"""Lo que pasa cuando algo sale mal: cobertura del feed, grupos que revientan,
bloqueos sin redireccion, entregas que no se van a arreglar nunca.

Cada caso de aqui salio de un hallazgo con su sintoma real, no de imaginar
fallos. Ninguna prueba abre un navegador ni toca la red.
"""
from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from motoradar.cli import watch_loop
from motoradar.config import Config
from motoradar.enrich import fix_bait_prices
from motoradar.models import Listing
from motoradar.notify import DeliveryResult, flush_pending, send_telegram, to_csv
from motoradar.sources.base import SessionExpired
from motoradar.sources.facebook import FacebookSource, _grow_feed, _session_blocked, card_to_listing
from motoradar.store import MAX_DELIVERY_ATTEMPTS, Store


def _pagina(children_por_llamada):
    """Pagina falsa cuyo feed va creciendo segun la secuencia dada."""
    page = Mock()
    secuencia = list(children_por_llamada)
    def evaluate(script, *a):
        if "children.length" in script:
            return secuencia.pop(0) if len(secuencia) > 1 else secuencia[0]
        return []
    page.evaluate.side_effect = evaluate
    return page


class CoberturaDelFeed(unittest.TestCase):
    def test_grow_feed_reintenta_mientras_quede_presupuesto(self):
        """Antes era un solo wheel y 1,5 s: Facebook tarda mas en traer el lote
        siguiente, asi que el bucle se rendia con 3-8 posts de los 20 pedidos."""
        page = _pagina([4, 4, 8])       # crece recien en el tercer chequeo
        with patch("motoradar.sources.facebook.time.sleep"):
            self.assertTrue(_grow_feed(page, total=4, deadline=1e18, espera=0))
        self.assertEqual(page.mouse.wheel.call_count, 3)

    def test_grow_feed_se_rinde_si_no_crece(self):
        page = _pagina([4])
        with patch("motoradar.sources.facebook.time.sleep"):
            self.assertFalse(_grow_feed(page, total=4, deadline=1e18, espera=0))
        self.assertEqual(page.mouse.wheel.call_count, 3)

    def test_grow_feed_respeta_el_presupuesto_de_tiempo(self):
        page = _pagina([4])
        with patch("motoradar.sources.facebook.time.sleep"):
            self.assertFalse(_grow_feed(page, total=4, deadline=0, espera=0))
        page.mouse.wheel.assert_not_called()


class GruposIndependientes(unittest.TestCase):
    def _contexto(self, page):
        ctx, pw, profile = Mock(), Mock(), Mock()
        ctx.pages = [page]
        profile.exists.return_value = True
        return (patch("motoradar.sources.facebook.PROFILE_DIR", profile),
                patch("motoradar.sources.facebook._launch", return_value=(pw, ctx)),
                patch("motoradar.sources.facebook._settle"),
                patch("motoradar.sources.facebook._render_feed", return_value=3),
                patch("motoradar.sources.facebook._expand_posts", return_value=0))

    def test_un_grupo_que_revienta_no_se_lleva_los_demas(self):
        bueno = {"id": "900001", "text": "Vendo moto CG 125 andando, valor 900 reais",
                 "blocks": [], "href": "", "img": ""}
        page = Mock()
        page.url = ""
        def goto(url, **kw):
            page.url = url
        page.goto.side_effect = goto
        def evaluate(script, *a):
            if "/groups/1?" in page.url:
                raise RuntimeError("Execution context was destroyed")
            return [bueno]
        page.evaluate.side_effect = evaluate
        source = FacebookSource()
        opts = {"marketplace": False, "groups": ["1", "2"], "group_mode": "feed"}
        parches = self._contexto(page)
        with parches[0], parches[1], parches[2], parches[3], parches[4], \
                redirect_stdout(io.StringIO()):
            listings = list(source.fetch(Config(), opts))
        self.assertEqual([l.uid for l in listings], ["facebook:g2-900001"])
        self.assertEqual(source.failed_pages, 1)
        self.assertTrue(any("grupo 1/2" in e for e in source.errors))
        # El id del grupo privado no puede acabar en runs.stats ni en doctor.
        self.assertFalse(any("groups/1" in e for e in source.errors))


class BloqueoSinRedireccion(unittest.TestCase):
    def test_un_muro_de_verificacion_es_sesion_caida_no_dom_roto(self):
        """Facebook decide bloquearte sin cambiar la URL. Contarlo como DOM roto
        hacia que watch siguiera entrando a una cuenta ya marcada."""
        page = Mock()
        page.url = "https://www.facebook.com/groups/999"
        page.evaluate.side_effect = lambda script, *a: (
            True if "input[name=" in script else [])
        source = FacebookSource()
        with patch("motoradar.sources.facebook._settle"), \
                patch("motoradar.sources.facebook._render_feed", return_value=0), \
                patch("motoradar.sources.facebook._expand_posts", return_value=0), \
                redirect_stdout(io.StringIO()), self.assertRaises(SessionExpired):
            list(source._group_feed(page, "999", "", True, {}))
        self.assertEqual(source.dom_failures, 0, "no es un rediseño, es un bloqueo")

    def test_session_blocked_no_se_dispara_con_un_mock_cualquiera(self):
        """Solo `True` cuenta: un Mock es verdadero y habria dado falso positivo
        en cada prueba que use una pagina simulada."""
        self.assertFalse(_session_blocked(Mock()))
        page = Mock()
        page.evaluate.side_effect = RuntimeError("boom")
        self.assertFalse(_session_blocked(page))


class EntregasQueNoSeArreglan(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "t.db"))
        self.listing = Listing("olx", "1", "Honda Biz", "https://e.test/1",
                               price=800, raw={"alert_status": "confirmed"})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _respuesta(self, status, body):
        return Mock(ok=status == 200, status_code=status,
                    json=lambda: body, headers={})

    def test_un_socio_que_bloqueo_el_bot_no_se_reintenta_para_siempre(self):
        self.store.upsert_many(self.listing, ["socio"], True)
        respuesta = self._respuesta(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})
        with patch("motoradar.notify.requests.post", return_value=respuesta) as post, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(flush_pending(self.store, "tok", "socio"), 0)
            # Segunda pasada: la fila ya esta abandonada, no se vuelve a pedir.
            self.assertEqual(flush_pending(self.store, "tok", "socio"), 0)
        self.assertEqual(post.call_count, 1, "una sola peticion, no una por pasada")
        fila = self.store.conn.execute(
            "SELECT state, last_error FROM deliveries").fetchone()
        self.assertEqual(fila["state"], "dead")
        self.assertIn("blocked by the user", fila["last_error"])
        self.assertEqual([tuple(f)[:2] for f in self.store.abandoned()],
                         [("socio", 1)])

    def test_un_fallo_transitorio_si_se_reintenta(self):
        self.store.upsert_many(self.listing, ["socio"], True)
        respuesta = self._respuesta(500, {"ok": False, "description": "Internal Server Error"})
        with patch("motoradar.notify.requests.post", return_value=respuesta), \
                redirect_stdout(io.StringIO()):
            flush_pending(self.store, "tok", "socio")
        fila = self.store.conn.execute("SELECT state FROM deliveries").fetchone()
        self.assertEqual(fila["state"], "pending")

    def test_tras_demasiados_intentos_se_abandona(self):
        self.store.upsert_many(self.listing, ["socio"], True)
        fila = self.store.pending("socio")[0]["id"]
        for _ in range(MAX_DELIVERY_ATTEMPTS):
            self.store.failed(fila, 0, "error de red")
        self.assertEqual(self.store.pending("socio"), [])
        self.assertEqual(self.store.conn.execute(
            "SELECT state FROM deliveries").fetchone()["state"], "dead")

    def test_un_429_sin_retry_after_igual_aplaza_al_destinatario(self):
        for identifier in ("1", "2", "3"):
            self.store.upsert_many(
                Listing("olx", identifier, "Moto", f"https://e.test/{identifier}",
                        price=800, raw={"alert_status": "confirmed"}),
                ["socio"], True)
        respuesta = self._respuesta(429, {"ok": False})
        with patch("motoradar.notify.requests.post", return_value=respuesta) as post, \
                redirect_stdout(io.StringIO()):
            flush_pending(self.store, "tok", "socio")
        self.assertEqual(post.call_count, 1, "no se sigue golpeando el endpoint")
        self.assertEqual(self.store.pending("socio"), [], "destinatario aplazado")

    def test_la_descripcion_de_telegram_llega_al_error(self):
        respuesta = self._respuesta(400, {"ok": False, "description": "chat not found"})
        with patch("motoradar.notify.requests.post", return_value=respuesta):
            result = send_telegram(self.listing, "tok", "chat")
        self.assertIn("chat not found", result.error)
        self.assertTrue(result.permanent)


class PrecioFantasma(unittest.TestCase):
    def test_un_detalle_que_tambien_dice_cero_no_reintroduce_el_precio(self):
        listing = card_to_listing({
            "href": "https://www.facebook.com/marketplace/item/1/",
            "text": "Gratis\nBiz+125\nJaguarao, RS", "img": ""})
        self.assertIsNone(listing.price)
        listing.raw["detail_price_text"] = "R$ 0"
        self.assertEqual(fix_bait_prices([listing]), 0)
        self.assertIsNone(listing.price, "el fantasma volvia por el detalle")

    def test_un_detalle_con_el_precio_real_si_corrige(self):
        listing = card_to_listing({
            "href": "https://www.facebook.com/marketplace/item/2/",
            "text": "R$ 5\n2025 Honda cg160\nJaguarao, RS", "img": ""})
        listing.raw["detail_price_text"] = "R$ 15.500"
        self.assertEqual(fix_bait_prices([listing]), 1)
        self.assertEqual(listing.price, 15500.0)
        self.assertEqual(listing.raw["price_evidence"], "detail_price")


class VigilanteHonesto(unittest.TestCase):
    def test_sin_fuentes_habilitadas_no_entra_al_bucle(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            codigo = watch_loop(Config(sources={}), None, True, 15,
                                max_cycles=5, sleeper=lambda _s: None,
                                start_cycle=0)
        self.assertEqual(codigo, 2)
        self.assertIn("Ninguna fuente habilitada", err.getvalue())

    def test_si_ninguna_pasada_funciono_el_codigo_de_salida_no_es_cero(self):
        cfg = Config(sources={"olx": {"enabled": True}})
        with patch("motoradar.cli.run_once", side_effect=RuntimeError("base tomada")), \
                patch("motoradar.cli.send_health", return_value=DeliveryResult(True)), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            codigo = watch_loop(cfg, None, True, 15, max_cycles=2,
                                sleeper=lambda _s: None, start_cycle=0)
        self.assertEqual(codigo, 1)

    def test_tres_pasadas_fallidas_seguidas_avisan(self):
        cfg = Config(sources={"olx": {"enabled": True}},
                     telegram={"token": "t", "chat_id": ["yo"]})
        with patch("motoradar.cli.run_once", side_effect=RuntimeError("base tomada")), \
                patch("motoradar.cli.send_health",
                      return_value=DeliveryResult(True)) as aviso, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            watch_loop(cfg, None, True, 15, max_cycles=4,
                       sleeper=lambda _s: None, start_cycle=0)
        # Una vez, no en cada pasada.
        self.assertEqual(aviso.call_count, 1)
        self.assertIn("no esta mirando", aviso.call_args.args[2])


class ExportacionLegible(unittest.TestCase):
    def test_el_csv_lleva_bom_para_que_excel_no_rompa_los_acentos(self):
        with tempfile.TemporaryDirectory() as td:
            ruta = Path(td) / "hits.csv"
            listing = Listing("facebook", "1", "Moto de Jaguarão", "https://e.test/1",
                              price=800, location="Jaguarão, RS",
                              raw={"alert_status": "confirmed"})
            to_csv([listing], str(ruta))
            self.assertTrue(ruta.read_bytes().startswith(b"\xef\xbb\xbf"))
            # Y una segunda escritura no renombra el archivo por la cabecera.
            to_csv([listing], str(ruta))
            self.assertEqual(len(list(Path(td).glob("hits*.csv"))), 1)
            with ruta.open(encoding="utf-8-sig", newline="") as fh:
                filas = list(csv.DictReader(fh))
            self.assertEqual(filas[0]["title"], "Moto de Jaguarão")


class CadenciaPersistida(unittest.TestCase):
    def test_el_ciclo_arranca_donde_quedo_la_base(self):
        """Reiniciar watch a mano no puede devolver la cadencia a cero: con
        `cycle % N == 0`, cada arranque hacia correr Marketplace igual."""
        from motoradar.cli import ciclo_inicial
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(db_path=str(Path(td) / "radar.db"))
            self.assertEqual(ciclo_inicial(cfg), 0)   # sin base todavia
            store = Store(cfg.db_path)
            try:
                for _ in range(5):
                    store.record_run("t", {"olx": {"status": "ok", "observed": 1}}, 0, 0)
            finally:
                store.close()
            self.assertEqual(ciclo_inicial(cfg), 5)


class TarjetaSinTitulo(unittest.TestCase):
    def test_una_tarjeta_cuyo_unico_texto_es_la_ciudad_no_inventa_titulo(self):
        """explain lo encontro en datos reales: tres avisos con titulo
        "Jaguarão, RS", que appraise clasificaba como desconocido."""
        listing = card_to_listing({
            "href": "https://www.facebook.com/marketplace/item/9/",
            "text": "\n".join(["R$ 333", "Arroio Grande, RS"]), "img": ""})
        self.assertEqual(listing.title, "")
        self.assertEqual(listing.location, "Arroio Grande, RS")
        self.assertEqual(listing.raw["card_parse"], "la tarjeta no traia titulo")
        self.assertEqual(listing.price, 333.0)

    def test_un_titulo_de_verdad_no_se_confunde_con_ubicacion(self):
        casos = [(["R$ 500", "Vendo moto"], "Vendo moto"),
                 (["R$ 450", "Biz", "Jaguarao, RS"], "Biz"),
                 (["R$ 800", "CG 125"], "CG 125")]
        for lineas, titulo in casos:
            texto = "\n".join(lineas)
            with self.subTest(texto=texto):
                listing = card_to_listing({"href": "https://f.test/marketplace/item/1/",
                                           "text": texto, "img": ""})
                self.assertEqual(listing.title, titulo)
                self.assertNotIn("card_parse", listing.raw)


class ExtraccionBaja(unittest.TestCase):
    def test_materializar_veinte_y_extraer_tres_no_pasa_callado(self):
        """Lo que mostro la corrida real: 20 hijos materializados, 2-5 posts
        extraidos. No es DOM roto (hay posts) pero es el techo de cobertura."""
        post = {"id": "1", "text": "Vendo moto CG 125, 900 reais",
                "blocks": [], "href": "", "img": ""}
        page = Mock()
        page.url = "https://www.facebook.com/groups/999"
        page.evaluate.return_value = [post, dict(post, id="2"), dict(post, id="3")]
        source = FacebookSource()
        out = io.StringIO()
        with patch("motoradar.sources.facebook._settle"),                 patch("motoradar.sources.facebook._render_feed", return_value=20),                 patch("motoradar.sources.facebook._expand_posts", return_value=0),                 redirect_stdout(out):
            posts = source._read_feed(page, {}, "grupo 999")
        self.assertEqual(len(posts), 3)
        self.assertEqual(source.dom_failures, 0, "hay posts: no es ceguera")
        self.assertTrue(any("extraccion baja" in e for e in source.errors))
        self.assertIn("3 de 20", out.getvalue())

    def test_una_extraccion_razonable_no_avisa(self):
        post = {"id": "1", "text": "Vendo moto CG 125, 900 reais",
                "blocks": [], "href": "", "img": ""}
        page = Mock()
        page.evaluate.return_value = [dict(post, id=str(i)) for i in range(15)]
        source = FacebookSource()
        with patch("motoradar.sources.facebook._settle"),                 patch("motoradar.sources.facebook._render_feed", return_value=20),                 patch("motoradar.sources.facebook._expand_posts", return_value=0),                 redirect_stdout(io.StringIO()):
            source._read_feed(page, {}, "grupo 999")
        self.assertEqual(source.errors, [])


if __name__ == "__main__":
    unittest.main()
