"""Privacidad de terceros, retencion, y salud medida por superficie.

El feed cronologico trae el post COMPLETO de todos los vecinos del grupo, no
solo de quien vende una moto. Eso se persistia entero en SQLite y se exportaba
al CSV —telefonos, nombres, direcciones— sin ningun plazo de borrado. Y la
salud se media por fuente: con Marketplace trayendo 150 avisos, los 8 grupos
podian estar ciegos semanas sin que la racha de silencio arrancara.
"""
from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from motoradar.cli import check_health, mask_destination
from motoradar.config import Config
from motoradar.models import Listing, parse_price_text, scrub_personal
from motoradar.notify import DeliveryResult, send_telegram, to_csv
from motoradar.sources.facebook import post_to_listing
from motoradar.store import Store

VECINO = ("Gente perdi minha carteira na rua Bento Goncalves, chama no zap "
          "53 99123-4567 ou mail maria@exemplo.com. Tambem vendo um sofa")


class Privacidad(unittest.TestCase):
    def test_scrub_redacta_contacto_y_deja_el_precio(self):
        limpio = scrub_personal(VECINO)
        for rastro in ("99123-4567", "53 99123-4567", "maria@exemplo.com"):
            self.assertNotIn(rastro, limpio)
        self.assertIn("[tel]", limpio)
        self.assertIn("[email]", limpio)
        # Lo que NO se puede romper: importes, años y kilometraje.
        for texto, esperado in (("vendo moto valor 1.700 reais", (1700.0, "BRL")),
                                ("quero 6.500 chama 53 99123-4567", (6500.0, "BRL")),
                                ("Se vende moto, 6.000 pesos", (6000.0, "UYU"))):
            with self.subTest(texto=texto):
                self.assertEqual(parse_price_text(scrub_personal(texto)), esperado)
        self.assertIn("2008 2009", scrub_personal("modelos 2008 2009 disponibles"))
        self.assertIn("12.000 km", scrub_personal("moto con 12.000 km"))

    def test_un_post_de_grupo_no_persiste_el_telefono_del_vecino(self):
        listing = post_to_listing(
            "999", {"id": "77", "text": VECINO, "blocks": [], "href": "", "img": ""},
            "jaguarao", False)
        listing.raw["alert_status"] = "excluded"
        with tempfile.TemporaryDirectory() as td:
            store = Store(str(Path(td) / "t.db"))
            try:
                store.upsert(listing, "", False)
                filas = store.conn.execute(
                    "SELECT title, raw FROM listings").fetchone()
                observaciones = store.conn.execute(
                    "SELECT payload FROM observations").fetchall()
            finally:
                store.close()
            csv_path = str(Path(td) / "h.csv")
            to_csv([listing], csv_path)
            exportado = Path(csv_path).read_text(encoding="utf-8")
        for campo in (filas["title"], filas["raw"], exportado,
                      *(fila["payload"] for fila in observaciones)):
            self.assertNotIn("99123-4567", campo)
            self.assertNotIn("maria@exemplo.com", campo)

    def test_una_url_en_el_texto_del_vendedor_no_genera_vista_previa(self):
        listing = post_to_listing(
            "999", {"id": "78", "text": "Vendo moto 800 reais http://evil.example/pix",
                    "blocks": [], "href": "", "img": ""}, "jaguarao", False)
        self.assertNotIn("evil.example", listing.title)
        respuesta = Mock(ok=True, status_code=200, json=lambda: {"ok": True})
        with patch("motoradar.notify.requests.post", return_value=respuesta) as post:
            self.assertTrue(send_telegram(listing, "tok", "chat", "prueba").ok)
        enviado = post.call_args.kwargs["json"]
        self.assertEqual(enviado["link_preview_options"], {"url": listing.url})
        self.assertNotIn("evil.example", enviado["text"])

    def test_doctor_no_expone_el_chat_id_completo(self):
        self.assertEqual(mask_destination("7659880259"), "...0259")
        self.assertEqual(mask_destination("12"), "...")
        self.assertEqual(mask_destination(""), "...")


class Retencion(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "t.db"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _viejo(self):
        self.store.conn.execute(
            "UPDATE listings SET last_seen='2024-01-01T00:00:00+00:00'")
        self.store.conn.execute(
            "UPDATE observations SET observed_at='2024-01-01T00:00:00+00:00'")
        self.store.conn.commit()

    def test_prune_borra_lo_viejo_sin_entrega(self):
        self.store.upsert(Listing("facebook", "g1-77", "post viejo", "u",
                                  raw={"alert_status": "excluded"}), "", False)
        self._viejo()
        self.assertEqual(self.store.prune(90),
                         {"observations": 1, "listings": 1, "runs": 0})
        self.assertEqual(self.store.count(), 0)

    def test_prune_conserva_lo_que_tiene_entrega_registrada(self):
        """El historico de lo avisado es lo unico que evita re-alertar."""
        avisado = Listing("facebook", "1", "Honda Biz", "u", price=800,
                          raw={"alert_status": "confirmed"})
        self.store.upsert_many(avisado, ["yo"], True)
        self._viejo()
        self.store.prune(90)
        self.assertEqual(self.store.count(), 1)

    def test_prune_desactivado_no_borra_nada(self):
        self.store.upsert(Listing("olx", "1", "x", "u"), "", False)
        self._viejo()
        self.assertEqual(self.store.prune(0),
                         {"observations": 0, "listings": 0, "runs": 0})
        self.assertEqual(self.store.count(), 1)


class SaludPorSuperficie(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "t.db"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_un_grupo_ciego_avisa_aunque_marketplace_observe(self):
        cfg = Config(telegram={"token": "t", "chat_id": ["yo"]},
                     monitoring={"health_zero_runs": 2})
        stat = {"status": "partial", "observed": 120,
                "observed_by_origin": {"facebook/marketplace": 120,
                                       "facebook/grupo-999": 0}}
        for _ in range(2):
            self.store.record_run("t0", {"facebook": stat}, 0, 0)
        with patch("motoradar.cli.send_health", return_value=DeliveryResult(True)), \
                redirect_stdout(io.StringIO()):
            avisos = check_health(self.store, cfg, {"facebook": stat})
        self.assertEqual(len(avisos), 1)
        self.assertIn("facebook/grupo-999", avisos[0])
        # Y no vuelve a avisar en la pasada siguiente: hay cooldown.
        with patch("motoradar.cli.send_health", return_value=DeliveryResult(True)), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(check_health(self.store, cfg, {"facebook": stat}), [])

    def test_cuando_el_grupo_vuelve_a_observar_el_aviso_se_limpia(self):
        cfg = Config(monitoring={"health_zero_runs": 1})
        ciego = {"status": "partial", "observed": 10,
                 "observed_by_origin": {"facebook/grupo-999": 0}}
        self.store.record_run("t0", {"facebook": ciego}, 0, 0)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(len(check_health(self.store, cfg, {"facebook": ciego})), 1)
        sano = {"status": "ok", "observed": 12,
                "observed_by_origin": {"facebook/grupo-999": 2}}
        with redirect_stdout(io.StringIO()):
            check_health(self.store, cfg, {"facebook": sano})
        self.assertTrue(
            self.store.health_notice_due("cero:facebook/grupo-999", cooldown_s=3600))


class ClavesDeConfig(unittest.TestCase):
    def test_una_errata_en_una_clave_no_pasa_en_silencio(self):
        # (yaml, clave mal escrita, clave correcta que deberia sugerir o None
        #  cuando la errata es demasiado distinta para adivinarla)
        casos = [
            ("budjet: 5000\n", "budjet", "budget"),
            ("monitoring:\n  alert_unconfirmado: false\n",
             "alert_unconfirmado", "alert_unconfirmed"),
            ("telegram:\n  chat_ids: ['111']\n", "chat_ids", "chat_id"),
            ("sources:\n  facebook:\n    group_mod: search\n",
             "group_mod", "group_mode"),
            # Caso real de este repositorio: OLX corria con la region por
            # defecto porque la clave escrita no existia.
            ("sources:\n  olx:\n    region_path: x\n", "region_path", None),
        ]
        with tempfile.TemporaryDirectory() as td:
            ruta = Path(td) / "config.yaml"
            for yaml_txt, errata, sugerida in casos:
                with self.subTest(clave=errata):
                    ruta.write_text(yaml_txt, encoding="utf-8")
                    with self.assertRaises(ValueError) as caso:
                        Config.load(ruta)
                    mensaje = str(caso.exception)
                    self.assertIn(errata, mensaje, "el error debe nombrar la clave")
                    if sugerida:
                        self.assertIn(sugerida, mensaje)

    def test_una_config_correcta_sigue_cargando(self):
        with tempfile.TemporaryDirectory() as td:
            ruta = Path(td) / "config.yaml"
            ruta.write_text(
                "budget: 1000\nmonitoring:\n  alert_unconfirmed: false\n"
                "  retention_days: 30\nsources:\n  olx:\n    state_path: x\n",
                encoding="utf-8")
            cfg = Config.load(ruta)
            self.assertFalse(cfg.monitoring["alert_unconfirmed"])
            self.assertEqual(cfg.monitoring["retention_days"], 30)


if __name__ == "__main__":
    unittest.main()
