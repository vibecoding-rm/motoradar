"""Facebook: traduccion de lo scrapeado, salud del adaptador y vigilancia.

El JS (ITEM_JS / POST_JS) corre dentro de Chromium y no se puede ejecutar en
una prueba offline. Lo que si se puede probar —y es donde estan los errores
caros— es la traduccion de esa salida a Listing: un precio truncado, un titulo
que en realidad era la ciudad, una alerta sin enlace. Las fixtures de
`tests/fixtures/` guardan la forma real que devuelve cada script, sanitizada.

Ninguna prueba abre un navegador, usa cuentas reales ni envia mensajes.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC
from pathlib import Path
from unittest.mock import Mock, patch

from motoradar.cli import (
    check_health,
    collect,
    cycle_config,
    observation_origins,
    run_once,
    watch_loop,
)
from motoradar.config import Config, FilterConfig
from motoradar.enrich import extract_description
from motoradar.filters import matches
from motoradar.models import Listing
from motoradar.money import FX
from motoradar.notify import DeliveryResult
from motoradar.pipeline import prepare
from motoradar.sources.base import BaseSource, SessionExpired, SourceError
from motoradar.sources.facebook import (
    FacebookSource,
    _group_region,
    _validate_navigation,
    card_to_listing,
    feed_dom_broken,
    market_dom_broken,
    post_to_listing,
)
from motoradar.store import Store

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Scraping(unittest.TestCase):
    def test_no_price_card_survives_the_full_selection_pipeline(self):
        case = fixture("facebook_marketplace_cards.json")["casos"][-1]
        listing = card_to_listing(case["card"])
        result = prepare([listing], Config(), FX(offline=True), enrich_details=False)
        self.assertEqual(result.unconfirmed, [listing])
        self.assertEqual(result.discarded, {})

    def test_marketplace_cards_from_fixtures(self):
        for caso in fixture("facebook_marketplace_cards.json")["casos"]:
            with self.subTest(caso=caso["nombre"]):
                listing = card_to_listing(caso["card"])
                got = {"external_id": listing.external_id, "title": listing.title,
                       "price": listing.price, "currency": listing.currency,
                       "location": listing.location, "image": listing.image}
                for key, value in caso["espera"].items():
                    self.assertEqual(got[key], value, key)

    def test_group_posts_from_fixtures_always_carry_a_usable_link(self):
        data = fixture("facebook_group_posts.json")
        for caso in data["casos"]:
            with self.subTest(caso=caso["nombre"]):
                listing = post_to_listing(data["grupo"], caso["post"], "", True)
                got = {"external_id": listing.external_id, "title": listing.title,
                       "price": listing.price, "currency": listing.currency,
                       "url": listing.url,
                       "url_confidence": listing.raw["url_confidence"],
                       "identity_confidence": listing.raw["identity_confidence"]}
                for key, value in caso["espera"].items():
                    self.assertEqual(got[key], value, key)
                # Una alerta sin enlace no sirve para comprar: nunca vacio.
                self.assertTrue(listing.url.startswith("https://www.facebook.com/"))

    def test_zone_without_configured_cities_is_declared_not_assumed(self):
        self.assertEqual(_group_region({"group_cities": {"999": ["jaguarao"]}}, "999"),
                         ("jaguarao", False))
        self.assertEqual(_group_region({}, "999"), ("", True))

        f = FilterConfig(cities=["jaguarao"])
        post = {"id": "1", "text": "Vendo moto 800 reais", "blocks": [],
                "href": "", "img": ""}
        desconocido = post_to_listing("999", post, "", True)
        ok, reason = matches(desconocido, f)
        self.assertFalse(ok, "un grupo sin ciudades configuradas ni mencion local debe descartarse")
        self.assertEqual(reason, "ciudad no coincide")
        self.assertIn("sin ciudades configuradas", desconocido.raw["location_confidence"])

        # Si el post en un grupo no configurado declara explicitamente Jaguarao, si pasa
        post_local = {"id": "2", "text": "Vendo moto 800 reais em Jaguarao", "blocks": [],
                      "href": "", "img": ""}
        local = post_to_listing("999", post_local, "", True)
        ok_local, _ = matches(local, f)
        self.assertTrue(ok_local, "un post que menciona la ciudad objetivo pasa aunque el grupo no este mapeado")

        declarado = post_to_listing("999", post, "pelotas", False)
        ok, reason = matches(declarado, f)
        self.assertFalse(ok)
        self.assertEqual(reason, "ciudad no coincide")

    def test_expired_session_is_its_own_failure(self):
        self.assertTrue(issubclass(SessionExpired, SourceError))
        for url in ("https://facebook.com/login/", "https://facebook.com/checkpoint/1"):
            with self.subTest(url=url), self.assertRaises(SessionExpired):
                _validate_navigation(Mock(url=url))


class DetailPipeline(unittest.TestCase):
    def test_vehicle_detail_headers_in_three_languages(self):
        for header, description in (("Detalhes do veículo", "Descrição"),
                                    ("Detalles del vehículo", "Descripción"),
                                    ("Vehicle details", "Description")):
            with self.subTest(header=header):
                body = f"{header}\n{description}\nVendo moto por 900 reais\nSeller information\n99999"
                self.assertIn("Vendo moto por 900 reais", extract_description(body))
                self.assertNotIn("99999", extract_description(body))

    def test_description_after_many_vehicle_attributes_is_not_lost(self):
        body = "Detalhes do veículo\n" + "Estado usado\n" * 20
        body += "Descrição\nQuero 6500 reais\nInformações do vendedor"
        self.assertEqual(extract_description(body), "Quero 6500 reais")

    def test_card_detail_selection_and_deliveries_without_network(self):
        for amount, expected in ((6500, "excluded"), (800, "confirmed")):
            with self.subTest(amount=amount), tempfile.TemporaryDirectory() as td:
                cfg = Config(db_path=str(Path(td) / "radar.db"),
                             csv_path=str(Path(td) / "hits.csv"), fx={"offline": True},
                             sources={"facebook": {"detail_pause": 0}},
                             telegram={"token": "fake", "chat_id": ["yo", "socio"]})
                listing = card_to_listing({"href": "https://www.facebook.com/marketplace/item/900001/",
                                           "text": "R$6\nYBR 125\nJaguarão, RS"})
                page, ctx, pw, profile = Mock(), Mock(), Mock(), Mock()
                ctx.pages = [page]
                profile.exists.return_value = True
                page.evaluate.return_value = f"Detalhes do veículo\nDescrição\nVendo YBR 125, quero {amount} reais\nInformações do vendedor"
                with patch("motoradar.cli.collect", return_value=(
                        [listing], {"facebook": {"status": "ok", "observed": 1}})), \
                        patch("motoradar.sources.facebook.PROFILE_DIR", profile), \
                        patch("motoradar.sources.facebook._launch", return_value=(pw, ctx)), \
                        patch("motoradar.sources.facebook._settle"), \
                        patch("motoradar.enrich.time.sleep"), \
                        patch("motoradar.notify.send_telegram", return_value=DeliveryResult(True)) as sender, \
                        patch("motoradar.notify.requests.post", side_effect=AssertionError("network forbidden")), \
                        redirect_stdout(io.StringIO()):
                    run_once(cfg)
                self.assertEqual(listing.raw["alert_status"], expected)
                self.assertEqual(listing.price, amount)
                self.assertEqual(listing.raw["detail_status"], "leido")
                self.assertEqual(sender.call_count, 2 if expected == "confirmed" else 0)
                ctx.close.assert_called_once()
                pw.stop.assert_called_once()

    def test_unrecognized_description_leaves_explicit_status(self):
        from motoradar.enrich import enrich_facebook
        listing = card_to_listing({"href": "https://www.facebook.com/marketplace/item/900001/",
                                   "text": "R$6\nYBR 125\nJaguarão, RS"})
        page, ctx, pw, profile = Mock(), Mock(), Mock(), Mock()
        ctx.pages = [page]
        profile.exists.return_value = True
        page.evaluate.return_value = "Pantalla desconocida"
        with patch("motoradar.sources.facebook.PROFILE_DIR", profile), \
                patch("motoradar.sources.facebook._launch", return_value=(pw, ctx)), \
                patch("motoradar.sources.facebook._settle"), \
                patch("motoradar.enrich.time.sleep"), redirect_stdout(io.StringIO()):
            self.assertEqual(enrich_facebook([listing]), 0)
        self.assertEqual(listing.raw["detail_status"],
                         "pagina no reconocida: sin descripcion ni precio")

    def test_the_detail_price_field_rescues_a_bait_card(self):
        """El precio del detalle es un campo, no una frase: no depende de que
        el vendedor repita el numero en la descripcion."""
        from motoradar.enrich import enrich_facebook, fix_bait_prices
        listing = card_to_listing({"href": "https://www.facebook.com/marketplace/item/900002/",
                                   "text": "R$5\n2025 Honda cg160\nJaguarão, RS"})
        self.assertEqual(listing.price, 5.0)          # la tarjeta miente
        page, ctx, pw, profile = Mock(), Mock(), Mock(), Mock()
        ctx.pages = [page]
        profile.exists.return_value = True
        # Cuerpo sin ningun marcador de descripcion: antes esto devolvia "" y
        # la correccion de precio no tenia con que trabajar.
        page.evaluate.return_value = (
            "Marketplace\nR$ 15.500\n2025 Honda cg160\nJaguarão, RS\n"
            "Publicado hace 3 horas\nA localização é aproximada")
        with patch("motoradar.sources.facebook.PROFILE_DIR", profile), \
                patch("motoradar.sources.facebook._launch", return_value=(pw, ctx)), \
                patch("motoradar.sources.facebook._settle"), \
                patch("motoradar.enrich.time.sleep"), redirect_stdout(io.StringIO()):
            self.assertEqual(enrich_facebook([listing]), 1)
        self.assertEqual(listing.raw["detail_price_text"], "R$ 15.500")
        self.assertEqual(listing.raw["detail_status"],
                         "sin descripcion; precio leido del detalle")
        self.assertEqual(fix_bait_prices([listing]), 1)
        self.assertEqual(listing.price, 15500.0)
        self.assertEqual(listing.raw["card_price"], 5.0)
        self.assertEqual(listing.raw["price_evidence"], "detail_price")

    def test_enrich_ignores_an_item_url_from_another_host(self):
        from motoradar.enrich import _is_facebook_item
        self.assertTrue(_is_facebook_item(
            "https://www.facebook.com/marketplace/item/1/"))
        for url in ("https://evil.example/marketplace/item/1",
                    "http://www.facebook.com/marketplace/item/1/",
                    "https://facebook.com.evil.example/marketplace/item/1",
                    "https://www.facebook.com/groups/999/posts/1/", ""):
            with self.subTest(url=url):
                self.assertFalse(_is_facebook_item(url))


class DomHealth(unittest.TestCase):
    """Cero resultados y "Facebook cambio el HTML" se ven igual desde afuera."""

    def test_empty_feed_requires_an_explicit_empty_marker(self):
        self.assertTrue(feed_dom_broken({"feed": True, "children": 0, "empty": False}))
        self.assertFalse(feed_dom_broken({"feed": True, "children": 0, "empty": True}))

    def test_children_without_extractable_posts_are_reported_as_blind(self):
        for children in (0, 5):
            with self.subTest(children=children):
                source, page = FacebookSource(), Mock()
                page.evaluate.return_value = []
                with patch("motoradar.sources.facebook._settle"), \
                        patch("motoradar.sources.facebook._render_feed"), \
                        patch("motoradar.sources.facebook._expand_posts"), \
                        patch("motoradar.sources.facebook._health", return_value={
                            "feed": True, "children": children, "empty": False}), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(source._read_feed(page, {}, "grupo de prueba"), [])
                self.assertEqual(source.dom_failures, 1)

    def test_broken_dom_is_not_the_same_as_no_results(self):
        # Cero avisos CON cartel de vacio: la zona no tenia nada, todo bien.
        self.assertFalse(feed_dom_broken({"feed": False, "empty": True, "main": True}))
        self.assertFalse(market_dom_broken({"anchors": 0, "empty": True, "main": True}))
        # Cero avisos SIN cartel ni feed: quedamos ciegos.
        self.assertTrue(feed_dom_broken({"feed": False, "empty": False, "main": True}))
        self.assertTrue(market_dom_broken({"anchors": 0, "empty": False, "main": True}))
        self.assertTrue(feed_dom_broken({}))
        self.assertTrue(market_dom_broken({"error": "TimeoutError"}))
        # Con contenido real no hay nada que denunciar.
        self.assertFalse(feed_dom_broken({"feed": True, "children": 5, "empty": False}))
        self.assertFalse(market_dom_broken({"anchors": 8, "empty": False}))

    def test_group_feed_reads_chronological_order_not_the_search_box(self):
        caso = fixture("facebook_group_posts.json")["casos"][0]
        page = Mock()
        page.url = "https://www.facebook.com/groups/999"
        page.evaluate.return_value = [caso["post"]]
        source = FacebookSource()
        with patch("motoradar.sources.facebook._settle"), \
                patch("motoradar.sources.facebook._render_feed", return_value=1), \
                patch("motoradar.sources.facebook._expand_posts", return_value=0):
            listings = list(source._group_feed(page, "999", "jaguarao", False, {}))
        url = page.goto.call_args.args[0]
        self.assertIn("sorting_setting=CHRONOLOGICAL", url)
        self.assertNotIn("/search/", url)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].raw["group_origin"], "feed")
        self.assertEqual(source.dom_failures, 0)

    def test_group_search_is_opt_in_and_marked(self):
        caso = fixture("facebook_group_posts.json")["casos"][0]
        page = Mock()
        page.url = "https://www.facebook.com/groups/999/search/"
        page.evaluate.return_value = [caso["post"]]
        source = FacebookSource()
        with patch("motoradar.sources.facebook._settle"), \
                patch("motoradar.sources.facebook._render_feed", return_value=1), \
                patch("motoradar.sources.facebook._expand_posts", return_value=0), \
                redirect_stdout(io.StringIO()):
            listings = list(source._group_search(page, "999", "jaguarao", False,
                                                 ["moto"], {}))
        self.assertIn("/search/", page.goto.call_args.args[0])
        self.assertEqual(listings[0].raw["group_origin"], "search")

    def test_blind_run_reports_error_not_an_empty_afternoon(self):
        page = Mock()
        page.url = "https://www.facebook.com/groups/999"
        # Ni posts ni cartel de vacio: asi se ve un cambio de HTML.
        page.evaluate.side_effect = lambda script, *a: (
            {"feed": False, "children": 0, "main": True, "empty": False,
             "text_len": 4000} if "empty:" in script else [])
        ctx, pw = Mock(), Mock()
        ctx.pages = [page]
        profile = Mock()
        profile.exists.return_value = True
        opts = {"marketplace": False, "groups": ["999"], "group_mode": "feed"}
        out = io.StringIO()
        source = FacebookSource()
        with patch("motoradar.sources.facebook.PROFILE_DIR", profile), \
                patch("motoradar.sources.facebook._launch", return_value=(pw, ctx)), \
                patch("motoradar.sources.facebook._settle"), \
                patch("motoradar.sources.facebook._render_feed", return_value=0), \
                patch("motoradar.sources.facebook._expand_posts", return_value=0), \
                redirect_stdout(out), self.assertRaises(SourceError):
            list(source.fetch(Config(), opts))
        self.assertEqual(source.dom_failures, 1)
        self.assertIn("DOM no reconocido", out.getvalue())
        ctx.close.assert_called_once()

    def test_collect_marks_a_blind_source_as_error(self):
        class Ciega(BaseSource):
            name = "facebook"

            def fetch(self, cfg, opts):
                # Dos paginas cargadas sin nada reconocible ni cartel de vacio.
                self.note_blind("grupo 999 (cronologico)", "children=0")
                self.note_blind("grupo 999 (cronologico)", "children=0")
                return iter(())

        cfg = Config(sources={"facebook": {"enabled": True}})
        with patch.dict("motoradar.sources.REGISTRY", {"facebook": Ciega()}, clear=True), \
                redirect_stdout(io.StringIO()):
            _, stats = collect(cfg, apply_filters=False, fx=FX(None, offline=True))
        self.assertEqual(stats["facebook"]["status"], "error")
        self.assertEqual(stats["facebook"]["dom_failures"], 2)
        self.assertIn("DOM no reconocido en grupo 999 (cronologico)",
                      stats["facebook"]["errors"])

    def test_collect_flags_an_expired_session_separately(self):
        class Caida(BaseSource):
            name = "facebook"

            def fetch(self, cfg, opts):
                raise SessionExpired("La sesion de Facebook expiro")

        cfg = Config(sources={"facebook": {"enabled": True}})
        with patch.dict("motoradar.sources.REGISTRY", {"facebook": Caida()}, clear=True), \
                redirect_stdout(io.StringIO()):
            _, stats = collect(cfg, apply_filters=False, fx=FX(None, offline=True))
        self.assertTrue(stats["facebook"]["session_expired"])
        self.assertEqual(stats["facebook"]["status"], "error")

    def test_collect_skips_a_paused_source(self):
        class NoDeberia(BaseSource):
            name = "facebook"

            def fetch(self, cfg, opts):  # pragma: no cover - no debe llamarse
                raise AssertionError("la fuente pausada no debe consultarse")

        cfg = Config(sources={"facebook": {"enabled": True}})
        with patch.dict("motoradar.sources.REGISTRY", {"facebook": NoDeberia()}, clear=True), \
                redirect_stdout(io.StringIO()):
            found, stats = collect(cfg, apply_filters=False, fx=FX(None, offline=True),
                                   skip={"facebook"})
        self.assertEqual(found, [])
        self.assertEqual(stats, {})


class Health(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "salud.sqlite3"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _run(self, observed):
        self.store.record_run("t0", {"facebook": {"status": "ok", "observed": observed}}, 0, 0)

    def test_zero_streak_counts_only_consecutive_blind_runs(self):
        self.assertEqual(self.store.source_zero_streak("facebook"), 0)
        for _ in range(3):
            self._run(0)
        self.assertEqual(self.store.source_zero_streak("facebook"), 3)
        self._run(5)
        self.assertEqual(self.store.source_zero_streak("facebook"), 0)
        self._run(0)
        self.assertEqual(self.store.source_zero_streak("facebook"), 1)
        # Una corrida donde la fuente NO participo (un `run --source olx`, o la
        # fuente pausada por sesion caida) no dice nada de ella y no puede
        # cortar la racha: cortandola, un uso normal desactivaba el unico
        # detector de "el radar se apago y no lo sabes".
        self.store.record_run("t0", {"olx": {"status": "ok", "observed": 1}}, 0, 0)
        self.assertEqual(self.store.source_zero_streak("facebook"), 1)
        self._run(0)
        self.assertEqual(self.store.source_zero_streak("facebook"), 2)
        # Un `stats` ilegible tampoco demuestra que la fuente viera algo.
        self.store.conn.execute(
            "INSERT INTO runs(started_at,finished_at,stats,opportunities,unconfirmed)"
            " VALUES('t','t','{roto',0,0)")
        self.store.conn.commit()
        self.assertEqual(self.store.source_zero_streak("facebook"), 2)

    def test_zero_streak_per_surface_sees_a_blind_group(self):
        """Con Marketplace observando, la fuente entera nunca llega a cero: la
        ceguera de los 8 grupos quedaba invisible."""
        for _ in range(3):
            self.store.record_run("t0", {"facebook": {
                "status": "partial", "observed": 120,
                "observed_by_origin": {"facebook/marketplace": 120,
                                       "facebook/grupo-999": 0}}}, 0, 0)
        self.assertEqual(self.store.source_zero_streak("facebook"), 0)
        self.assertEqual(self.store.source_zero_streak("facebook/grupo-999"), 3)
        self.assertEqual(self.store.source_zero_streak("facebook/marketplace"), 0)

    def test_health_notice_respects_cooldown_and_clears_on_recovery(self):
        # Consultar no marca: el cooldown arranca cuando el aviso se ENTREGA.
        self.assertTrue(self.store.health_notice_due("dom:facebook", cooldown_s=3600))
        self.assertTrue(self.store.health_notice_due("dom:facebook", cooldown_s=3600))
        self.store.mark_health_notice("dom:facebook")
        self.assertFalse(self.store.health_notice_due("dom:facebook", cooldown_s=3600))
        self.assertTrue(self.store.health_notice_due("dom:facebook", cooldown_s=0))
        self.store.clear_health_notice("dom:facebook")
        self.assertTrue(self.store.health_notice_due("dom:facebook", cooldown_s=3600))

    def test_partial_success_does_not_reset_active_dom_notice_cooldown(self):
        cfg = Config(telegram={"token": "t", "chat_id": "c"})
        stats = {"facebook": {"status": "partial", "observed": 151, "dom_failures": 8}}
        with patch("motoradar.cli.send_health", return_value=DeliveryResult(True)) as sender, \
                redirect_stdout(io.StringIO()):
            for _ in range(4):
                check_health(self.store, cfg, stats)
            self.assertEqual(sender.call_count, 1)
            check_health(self.store, cfg, {"facebook": {"status": "ok", "observed": 1}})
            check_health(self.store, cfg, stats)
            self.assertEqual(sender.call_count, 2)

    def test_blind_source_alerts_once_and_recovery_resets_it(self):
        cfg = Config(telegram={"token": "t", "chat_id": "c"},
                     monitoring={"health_zero_runs": 2})
        stats = {"facebook": {"status": "error", "observed": 0, "dom_failures": 3,
                              "session_expired": False}}
        with patch("motoradar.cli.send_health", return_value=DeliveryResult(True)) as sender, \
                redirect_stdout(io.StringIO()):
            avisos = check_health(self.store, cfg, stats)
        self.assertEqual(len(avisos), 1)
        self.assertIn("ciego", avisos[0])
        sender.assert_called_once()

        with patch("motoradar.cli.send_health", return_value=DeliveryResult(True)) as sender, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(check_health(self.store, cfg, stats), [])
        sender.assert_not_called()

        with patch("motoradar.cli.send_health", return_value=DeliveryResult(True)), \
                redirect_stdout(io.StringIO()):
            check_health(self.store, cfg, {"facebook": {"status": "ok", "observed": 7}})
        self.assertTrue(self.store.health_notice_due("dom:facebook", cooldown_s=3600))

    def test_sustained_silence_is_alerted_even_without_dom_failure(self):
        cfg = Config(telegram={"token": "t", "chat_id": "c"},
                     monitoring={"health_zero_runs": 2})
        stats = {"facebook": {"status": "ok", "observed": 0}}
        self._run(0)
        with patch("motoradar.cli.send_health", return_value=DeliveryResult(True)), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(check_health(self.store, cfg, stats), [])
        self._run(0)
        with patch("motoradar.cli.send_health", return_value=DeliveryResult(True)), \
                redirect_stdout(io.StringIO()):
            avisos = check_health(self.store, cfg, stats)
        self.assertEqual(len(avisos), 1)
        self.assertIn("2 pasadas seguidas", avisos[0])

    def test_expired_session_is_announced_by_telegram(self):
        cfg = Config(telegram={"token": "t", "chat_id": "c"})
        stats = {"facebook": {"status": "error", "observed": 0, "session_expired": True}}
        with patch("motoradar.cli.send_health", return_value=DeliveryResult(True)), \
                redirect_stdout(io.StringIO()):
            avisos = check_health(self.store, cfg, stats)
        self.assertEqual(len(avisos), 1)
        self.assertIn("motoradar login", avisos[0])


class Watching(unittest.TestCase):
    def test_marketplace_runs_on_its_own_cadence(self):
        cfg = Config(sources={"facebook": {"enabled": True, "marketplace": True,
                                           "marketplace_every_cycles": 3}})
        with redirect_stdout(io.StringIO()):
            self.assertTrue(cycle_config(cfg, 0).source_opts("facebook")["marketplace"])
            self.assertFalse(cycle_config(cfg, 1).source_opts("facebook")["marketplace"])
            self.assertFalse(cycle_config(cfg, 2).source_opts("facebook")["marketplace"])
            self.assertTrue(cycle_config(cfg, 3).source_opts("facebook")["marketplace"])
        # La config original no se toca: los grupos siguen corriendo siempre.
        self.assertTrue(cfg.source_opts("facebook")["marketplace"])
        sin_cadencia = Config(sources={"facebook": {"enabled": True, "marketplace": True}})
        self.assertIs(cycle_config(sin_cadencia, 7), sin_cadencia)

    def test_watch_pauses_a_source_whose_session_died(self):
        cfg = Config(sources={"facebook": {"enabled": True}, "olx": {"enabled": True}},
                     monitoring={"session_pause_cycles": 3})
        pasadas = []

        def fake_run(config, only=None, dry_run=False, enrich_details=True,
                     cycle=0, skip=None, stats_out=None):
            pasadas.append(set(skip or ()))
            stats_out.clear()
            stats_out["olx"] = {"status": "ok", "observed": 3}
            stats_out["facebook"] = ({"status": "error", "observed": 0,
                                      "session_expired": True} if cycle == 0
                                     else {"status": "ok", "observed": 1})
            return 0

        out, err = io.StringIO(), io.StringIO()
        with patch("motoradar.cli.run_once", side_effect=fake_run), \
                redirect_stdout(out), redirect_stderr(err):
            watch_loop(cfg, None, True, 15, max_cycles=4,
                       sleeper=lambda _s: None, start_cycle=0)
        self.assertEqual(pasadas, [set(), {"facebook"}, {"facebook"}, set()])
        self.assertIn("sesion caida", err.getvalue())
        self.assertIn("se reintenta en esta pasada", out.getvalue())

    def test_watch_does_not_hammer_a_provider_with_every_source_paused(self):
        cfg = Config(sources={"facebook": {"enabled": True}},
                     monitoring={"session_pause_cycles": 5})
        llamadas = []

        def fake_run(config, only=None, dry_run=False, enrich_details=True,
                     cycle=0, skip=None, stats_out=None):
            llamadas.append(cycle)
            stats_out.clear()
            stats_out["facebook"] = {"status": "error", "observed": 0,
                                     "session_expired": True}
            return 0

        out = io.StringIO()
        with patch("motoradar.cli.run_once", side_effect=fake_run), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            watch_loop(cfg, None, True, 15, max_cycles=3,
                       sleeper=lambda _s: None, start_cycle=0)
        self.assertEqual(llamadas, [0], "la unica fuente quedo pausada")
        self.assertIn("Todas las fuentes estan pausadas", out.getvalue())

    def test_watch_survives_a_failed_pass_and_keeps_going(self):
        cfg = Config(sources={"olx": {"enabled": True}})
        llamadas = []

        def boom(config, only=None, dry_run=False, enrich_details=True,
                 cycle=0, skip=None, stats_out=None):
            llamadas.append(cycle)
            if cycle == 0:
                raise SourceError("proveedor caido")
            return 0

        with patch("motoradar.cli.run_once", side_effect=boom), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            watch_loop(cfg, None, True, 15, max_cycles=3,
                       sleeper=lambda _s: None, start_cycle=0)
        self.assertEqual(llamadas, [0, 1, 2])


class Diagnostico(unittest.TestCase):
    """De 151 observaciones a 13 seleccionadas sin explicacion no se afina nada."""

    def test_origins_separate_the_group_feed_from_marketplace(self):
        data = fixture("facebook_group_posts.json")
        observado = [
            Listing("facebook", "m1", "2025 Honda cg160",
                    "https://www.facebook.com/marketplace/item/1/", price=5.0,
                    raw={"kind": "marketplace"}),
            Listing("facebook", "m2", "Biz",
                    "https://www.facebook.com/marketplace/item/2/", price=400.0,
                    raw={"kind": "marketplace"}),
            post_to_listing(data["grupo"], data["casos"][0]["post"], "jaguarao", False),
            Listing("olx", "o1", "CG 125", "https://olx.test/1", price=900.0),
        ]
        self.assertEqual(observation_origins(observado),
                         {"facebook/marketplace": 2, "facebook/grupo-feed": 1, "olx": 1})

    def test_run_once_reports_origin_and_why_observations_fell(self):
        data = fixture("facebook_group_posts.json")
        mercado = Listing("facebook", "m1", "2025 Honda cg160",
                          "https://www.facebook.com/marketplace/item/1/",
                          price=5.0, location="Jaguarão, RS",
                          raw={"kind": "marketplace"})
        # Un post del feed que NO es una moto: tiene que aparecer descartado y
        # con motivo, no desaparecer en silencio.
        heladera = post_to_listing(data["grupo"],
                                   {"id": "5", "text": "Vendo geladeira 200 reais",
                                    "blocks": [], "href": "", "img": ""},
                                   "jaguarao", False)
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(db_path=str(Path(td) / "radar.db"),
                         csv_path=str(Path(td) / "hits.csv"), fx={"offline": True})
            out = io.StringIO()
            with patch("motoradar.cli.collect",
                       return_value=([mercado, heladera],
                                     {"facebook": {"status": "ok", "observed": 2}})), \
                    redirect_stdout(out):
                run_once(cfg, enrich_details=False)
        texto = out.getvalue()
        self.assertIn("facebook/marketplace 1", texto)
        self.assertIn("facebook/grupo-feed 1", texto)
        self.assertIn("descartados:", texto)


class StrictGeographicDelimitation(unittest.TestCase):
    def setUp(self):
        self.f = FilterConfig(cities=["jaguarao", "rio branco"])

    def test_jaguarao_passes(self):
        # Card with Jaguarao, RS
        c1 = Listing("facebook", "1", "CG 125", "https://facebook.com/1", price=800.0,
                     location="Jaguarão, RS", raw={"kind": "marketplace"})
        ok, _ = matches(c1, self.f)
        self.assertTrue(ok, "Jaguarao con tilde debe pasar")

        # Card with Jaguarao without tilde
        c2 = Listing("facebook", "2", "CG 125", "https://facebook.com/2", price=800.0,
                     location="Jaguarao", raw={"kind": "marketplace"})
        ok, _ = matches(c2, self.f)
        self.assertTrue(ok, "Jaguarao sin tilde debe pasar")

        # Card with Jaguarao - RS
        c3 = Listing("facebook", "3", "CG 125", "https://facebook.com/3", price=800.0,
                     location="Jaguarão - RS", raw={"kind": "marketplace"})
        ok, _ = matches(c3, self.f)
        self.assertTrue(ok, "Jaguarao con guion debe pasar")

        # Group post in mapped group for jaguarao
        post = {"id": "10", "text": "Vendo moto 800 reais", "blocks": [], "href": "", "img": ""}
        g1 = post_to_listing("100", post, "jaguarao", False)
        ok, _ = matches(g1, self.f)
        self.assertTrue(ok, "Grupo mapeado a Jaguarao debe pasar")

        # Group post in unmapped group mentioning Jaguarao in text
        post_text = {"id": "11", "text": "Vendo moto 800 reais em Jaguarao", "blocks": [], "href": "", "img": ""}
        g2 = post_to_listing("101", post_text, "", True)
        ok, _ = matches(g2, self.f)
        self.assertTrue(ok, "Post que menciona Jaguarao en texto debe pasar")

    def test_rio_branco_passes(self):
        # Card with Rio Branco, Cerro Largo, Uruguay
        c1 = Listing("facebook", "4", "Scooter Yumbo", "https://facebook.com/4", price=800.0,
                     location="Rio Branco, Cerro Largo, Uruguay", raw={"kind": "marketplace"})
        ok, _ = matches(c1, self.f)
        self.assertTrue(ok, "Rio Branco, Cerro Largo, Uruguay debe pasar")

        # Card with Rio Branco alone
        c2 = Listing("facebook", "5", "Scooter Yumbo", "https://facebook.com/5", price=800.0,
                     location="Rio Branco", raw={"kind": "marketplace"})
        ok, _ = matches(c2, self.f)
        self.assertTrue(ok, "Rio Branco solo debe pasar")

        # Card with Río Branco accented
        c3 = Listing("facebook", "6", "Scooter Yumbo", "https://facebook.com/6", price=800.0,
                     location="Río Branco", raw={"kind": "marketplace"})
        ok, _ = matches(c3, self.f)
        self.assertTrue(ok, "Rio Branco acentuado debe pasar")

        # Group post with Rio Branco in search_region
        post = {"id": "12", "text": "Vendo moto 800", "blocks": [], "href": "", "img": ""}
        g1 = post_to_listing("102", post, "rio branco", False)
        ok, _ = matches(g1, self.f)
        self.assertTrue(ok, "Grupo mapeado a Rio Branco debe pasar")

        # Group post with Rio Branco in text
        post_text = {"id": "13", "text": "Se vende moto 110cc en Rio Branco, 600 pesos",
                     "blocks": [], "href": "", "img": ""}
        g2 = post_to_listing("103", post_text, "", True)
        ok, _ = matches(g2, self.f)
        self.assertTrue(ok, "Post que menciona Rio Branco en texto debe pasar")

        # Valid border combinations
        border_combinations = [
            "Jaguarão, RS - Rio Branco, Uruguay",
            "Jaguarão, RS / Rio Branco, UY",
            "Jaguarão, RS - Rio Branco",
            "Jaguarão (RS) / Rio Branco (UY)",
            "Jaguarão/RS - Rio Branco",
            "Jaguarão - RS - Rio Branco",
            "Rio Branco, Uruguay",
            "Rio Branco (UY)",
            "Rio Branco/UY",
            "Rio Branco / Cerro Largo",
            "Rio Branco - Uruguay",
        ]
        for loc in border_combinations:
            with self.subTest(loc=loc):
                c = Listing("facebook", "border_ok", "Moto CG 125", "https://facebook.com/border_ok",
                            price=800.0, location=loc, raw={"kind": "marketplace"})
                ok, reason = matches(c, self.f)
                self.assertTrue(ok, f"Ubicacion fronteriza valida '{loc}' debe pasar, fallo con {reason}")
                self.assertEqual(reason, "")

    def test_distant_cities_rejected(self):
        distant = [
            "Pelotas, RS",
            "Pelotas",
            "Bagé, RS",
            "Bage",
            "Melo",
            "Melo, Cerro Largo",
            "Porto Alegre, RS",
            "Porto Alegre",
            "Arroio Grande, RS",
            "Arroio Grande",
            "Rio Grande, RS",
            "Herval, RS",
            "Santa Vitória do Palmar",
            "Candiota",
            "Pinheiro Machado",
            "Pedras Altas",
            "Canoas, RS",
            "Novo Hamburgo, RS",
            "Treinta y Tres",
            "Treinta y Tres, Uruguay",
            "Acre",
        ]
        for loc in distant:
            with self.subTest(loc=loc):
                c = Listing("facebook", "d1", "Moto CG 125", "https://facebook.com/d1", price=800.0,
                            location=loc, raw={"kind": "marketplace"})
                ok, reason = matches(c, self.f)
                self.assertFalse(ok, f"{loc} debe ser rechazada")
                self.assertEqual(reason, "ciudad no coincide")

    def test_rio_branco_disambiguation(self):
        homonyms = [
            "Bairro Rio Branco, Porto Alegre, RS",
            "Bairro Rio Branco",
            "Rio Branco, Porto Alegre",
            "Rio Branco, Novo Hamburgo",
            "Rio Branco, Canoas",
            "Rio Branco, Acre",
            "Rio Branco, AC",
            "Rio Branco - RS",
            "Rio Branco, RS",
            "Rio Branco / RS",
            "Rio Branco (RS)",
            "Rio Branco/RS",
            "Rio Branco / AC",
            "Rio Branco (AC)",
            "Rio Branco (Acre)",
            "AC, Rio Branco",
            "RS, Rio Branco",
            "Rio Branco, Brasil",
            "Rio Branco / Acre",
            "Rio Branco/AC",
            "Rio Branco(RS)",
            "Rio Branco(AC)",
            "Rio Branco(Acre)",
            "Rio Branco/Acre",
            "AC - Rio Branco",
            "RS - Rio Branco",
            "AC / Rio Branco",
            "RS / Rio Branco",
            "AC/Rio Branco",
            "RS/Rio Branco",
            "Rio Branco, Brazil",
            "Rio Branco (Brasil)",
            "Rio Branco / Brasil",
            "Rio Branco (RS) - Bairro Nobre",
            "Rio Branco / AC - Centro",
        ]
        for loc in homonyms:
            with self.subTest(loc=loc):
                c = Listing("facebook", "h1", "Moto CG 125", "https://facebook.com/h1", price=800.0,
                            location=loc, raw={"kind": "marketplace"})
                ok, reason = matches(c, self.f)
                self.assertFalse(ok, f"Homonimo {loc} no debe confundirse con Rio Branco (UY)")
                self.assertEqual(reason, "ciudad no coincide")

    def test_treinta_y_tres_rejections(self):
        """Verify Treinta y Tres (UY) is rejected in Marketplace cards and mapped group posts."""
        card_locations = [
            "Treinta y Tres",
            "Treinta y Tres, Uruguay",
            "Treinta y Tres - UY",
            "Treinta y Tres / Uruguay",
            "Treinta y Tres (UY)",
        ]
        for loc in card_locations:
            with self.subTest(loc=loc):
                c = Listing("facebook", "tyt_card", "Moto CG 125", "https://facebook.com/tyt_card", price=800.0,
                            location=loc, raw={"kind": "marketplace"})
                ok, reason = matches(c, self.f)
                self.assertFalse(ok, f"Ubicacion '{loc}' debe ser rechazada")
                self.assertEqual(reason, "ciudad no coincide")

        group_texts = [
            "Vendo moto 800 pesos em Treinta y Tres",
            "Vendo moto en Treinta y Tres, Uruguay por 500 pesos",
            "Retirar en Treinta y Tres",
            "Moto en Treinta y Tres lista para transferir",
        ]
        for text in group_texts:
            with self.subTest(text=text):
                post = {"id": "mg_tyt", "text": text, "blocks": [text], "href": "", "img": ""}
                l = post_to_listing("396849520366647", post, "jaguarao rio branco", False)
                ok, reason = matches(l, self.f)
                self.assertFalse(ok, f"Post con texto '{text}' en grupo mapeado debe descartarse")
                self.assertEqual(reason, "ciudad no coincide")

    def test_mapped_groups_reject_distant_city_mentions(self):
        cases = [
            "Vendo moto 800 reais em Pelotas",
            "Vendo CG 125, sou de Bagé, valor 900",
            "Vendo moto en Melo, 4000 pesos",
            "Moto em Porto Alegre, Barbada",
            "Retirar em Arroio Grande",
            "Moto localizada em Rio Grande",
            "Vendo moto em Herval",
            "Vendo moto em Novo Hamburgo",
            "Vendo moto 800 pesos em Treinta y Tres",
        ]
        for text in cases:
            with self.subTest(text=text):
                post = {"id": "mg1", "text": text, "blocks": [text], "href": "", "img": ""}
                l = post_to_listing("396849520366647", post, "jaguarao rio branco", False)
                ok, reason = matches(l, self.f)
                self.assertFalse(ok, f"Post con texto '{text}' en grupo mapeado debe descartarse")
                self.assertEqual(reason, "ciudad no coincide")


class ScraperExceptionIsolation(unittest.TestCase):
    def test_marketplace_generator_isolates_malformed_card(self):
        source = FacebookSource()
        page = Mock()
        page.goto.return_value = None
        good_card_1 = {"href": "https://www.facebook.com/marketplace/item/101/",
                       "text": "R$800\nBiz\nJaguarão, RS", "img": ""}
        bad_card = {"href": "https://www.facebook.com/marketplace/item/102/",
                    "text": None, "img": ""}
        good_card_2 = {"href": "https://www.facebook.com/marketplace/item/103/",
                       "text": "R$900\nCG 125\nJaguarão, RS", "img": ""}
        page.evaluate.return_value = [good_card_1, bad_card, good_card_2]

        def flaky_card_to_listing(card):
            if card["href"].endswith("102/"):
                raise ValueError("DOM corrupto en tarjeta 102")
            return card_to_listing(card)

        with patch("motoradar.sources.facebook._validate_navigation"), \
                patch("motoradar.sources.facebook._scroll"), \
                patch("motoradar.sources.facebook.time.sleep"), \
                patch("motoradar.sources.facebook.card_to_listing", side_effect=flaky_card_to_listing), \
                redirect_stdout(io.StringIO()):
            cfg = Config(filters=FilterConfig(queries=["moto"]))
            results = list(source._marketplace(page, cfg, {"marketplace_queries": ["moto"]}, 1, 0.1))

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].external_id, "101")
        self.assertEqual(results[1].external_id, "103")
        self.assertTrue(any("tarjeta malformada" in e for e in source.errors))
        self.assertEqual(len(source.errors), 1, "source.errors no debe duplicar mensajes")
        self.assertEqual(len(source.errors), len(set(source.errors)), "source.errors contiene duplicados")

    def test_group_feed_generator_isolates_malformed_post(self):
        source = FacebookSource()
        page = Mock()
        page.goto.return_value = None
        good_post_1 = {"id": "201", "text": "Vendo Biz 800", "blocks": [], "href": "", "img": ""}
        bad_post = {"id": "202", "text": "bad", "blocks": [], "href": "", "img": ""}
        good_post_2 = {"id": "203", "text": "Vendo CG 900", "blocks": [], "href": "", "img": ""}

        def flaky_post_to_listing(group_id, post, region, region_unknown, origin="feed"):
            if post.get("id") == "202":
                raise KeyError("Campo faltante en post 202")
            return post_to_listing(group_id, post, region, region_unknown, origin)

        with patch("motoradar.sources.facebook._validate_navigation"), \
                patch.object(source, "_read_feed", return_value=[good_post_1, bad_post, good_post_2]), \
                patch("motoradar.sources.facebook.post_to_listing", side_effect=flaky_post_to_listing), \
                redirect_stdout(io.StringIO()):
            results = list(source._group_feed(page, "999", "jaguarao", False, {}))

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].external_id, "g999-201")
        self.assertEqual(results[1].external_id, "g999-203")
        self.assertTrue(any("post malformado" in e for e in source.errors))
        self.assertEqual(len(source.errors), 1, "source.errors no debe duplicar mensajes")
        self.assertEqual(len(source.errors), len(set(source.errors)), "source.errors contiene duplicados")

    def test_group_search_generator_isolates_malformed_post(self):
        source = FacebookSource()
        page = Mock()
        page.goto.return_value = None
        good_post_1 = {"id": "301", "text": "Vendo Biz 800", "blocks": [], "href": "", "img": ""}
        bad_post = {"id": "302", "text": "bad", "blocks": [], "href": "", "img": ""}
        good_post_2 = {"id": "303", "text": "Vendo CG 900", "blocks": [], "href": "", "img": ""}

        def flaky_post_to_listing(group_id, post, region, region_unknown, origin="search"):
            if post.get("id") == "302":
                raise KeyError("Campo faltante en post 302")
            return post_to_listing(group_id, post, region, region_unknown, origin)

        with patch("motoradar.sources.facebook._validate_navigation"), \
                patch.object(source, "_read_feed", return_value=[good_post_1, bad_post, good_post_2]), \
                patch("motoradar.sources.facebook.post_to_listing", side_effect=flaky_post_to_listing), \
                redirect_stdout(io.StringIO()):
            results = list(source._group_search(page, "999", "jaguarao", False, ["moto"], {}))

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].external_id, "g999-301")
        self.assertEqual(results[1].external_id, "g999-303")
        self.assertTrue(any("post malformado" in e for e in source.errors))
        self.assertEqual(len(source.errors), 1, "source.errors no debe duplicar mensajes")
        self.assertEqual(len(source.errors), len(set(source.errors)), "source.errors contiene duplicados")

    def test_source_note_deduplicates_repeated_messages(self):
        source = FacebookSource()
        aviso = "Marketplace/moto: tarjeta malformada omitida (ValueError: prueba)"
        source.note(aviso)
        source.note(aviso)
        self.assertEqual(len(source.errors), 1)
        self.assertEqual(source.errors, [aviso])


class TestFacebookDateParsing(unittest.TestCase):
    """Pruebas exhaustivas para `parse_post_age` en espanol y portugues."""

    def test_spanish_relative_date_expressions(self):
        """Expresiones relativas comunes en publicaciones en espanol."""
        from motoradar.sources.facebook import parse_post_age

        # Horas
        self.assertEqual(parse_post_age("hace 2 horas"), 7200.0)
        self.assertEqual(parse_post_age("2 h"), 7200.0)
        self.assertEqual(parse_post_age("hace 1 hora"), 3600.0)

        # Minutos
        self.assertEqual(parse_post_age("hace 15 minutos"), 900.0)
        self.assertEqual(parse_post_age("15 min"), 900.0)
        self.assertEqual(parse_post_age("hace 45 min"), 2700.0)

        # Dias
        self.assertEqual(parse_post_age("hace 3 dias"), 259200.0)
        self.assertEqual(parse_post_age("hace 3 días"), 259200.0)
        self.assertEqual(parse_post_age("3 d"), 259200.0)

        # Semanas, meses y anos
        self.assertEqual(parse_post_age("hace 1 semana"), 604800.0)
        self.assertEqual(parse_post_age("1 sem"), 604800.0)
        self.assertEqual(parse_post_age("hace 2 meses"), 5184000.0)
        self.assertEqual(parse_post_age("hace 1 año"), 31536000.0)
        self.assertEqual(parse_post_age("1 a"), 31536000.0)

        # Marcadores inmediatos y de dia
        self.assertEqual(parse_post_age("hoy a las 14:30"), 0.0)
        self.assertEqual(parse_post_age("ayer a las 20:00"), 86400.0)
        self.assertEqual(parse_post_age("ahora"), 0.0)

    def test_portuguese_relative_date_expressions(self):
        """Expresiones relativas comunes en publicaciones en portugues."""
        from motoradar.sources.facebook import parse_post_age

        # Horas
        self.assertEqual(parse_post_age("há 2 horas"), 7200.0)
        self.assertEqual(parse_post_age("ha 2 horas"), 7200.0)
        self.assertEqual(parse_post_age("faz 2 horas"), 7200.0)
        self.assertEqual(parse_post_age("2 horas atrás"), 7200.0)
        self.assertEqual(parse_post_age("2 horas atras"), 7200.0)
        self.assertEqual(parse_post_age("2h"), 7200.0)

        # Minutos
        self.assertEqual(parse_post_age("há 15 minutos"), 900.0)
        self.assertEqual(parse_post_age("15 min atrás"), 900.0)
        self.assertEqual(parse_post_age("15m"), 900.0)

        # Dias
        self.assertEqual(parse_post_age("há 3 dias"), 259200.0)
        self.assertEqual(parse_post_age("3 dias atrás"), 259200.0)
        self.assertEqual(parse_post_age("3d"), 259200.0)

        # Semanas, meses y anos
        self.assertEqual(parse_post_age("há 1 semana"), 604800.0)
        self.assertEqual(parse_post_age("1 semana atrás"), 604800.0)
        self.assertEqual(parse_post_age("há 2 meses"), 5184000.0)
        self.assertEqual(parse_post_age("há 1 ano"), 31536000.0)

        # Marcadores inmediatos y de dia
        self.assertEqual(parse_post_age("hoje às 14:30"), 0.0)
        self.assertEqual(parse_post_age("ontem às 18:00"), 86400.0)
        self.assertEqual(parse_post_age("agora"), 0.0)
        self.assertEqual(parse_post_age("agora mesmo"), 0.0)

    def test_absolute_date_expressions_spanish_and_portuguese(self):
        """Fechas absolutas en espanol y portugues calculadas con timestamp de referencia fijo."""
        from datetime import datetime

        from motoradar.sources.facebook import parse_post_age

        # Referencia fija: 2026-09-19 12:00:00 UTC
        ref_now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

        # Portugues: "12 de setembro" (hace 7 dias y 12 horas = 648000s)
        age_pt = parse_post_age("12 de setembro", now=ref_now)
        self.assertIsNotNone(age_pt)
        self.assertEqual(age_pt, 648000.0)

        # Espanol: "12 de septiembre"
        age_es = parse_post_age("12 de septiembre", now=ref_now)
        self.assertIsNotNone(age_es)
        self.assertEqual(age_es, 648000.0)

        # Con ano explicito: "15 de enero de 2024"
        expected_seconds = (ref_now - datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)).total_seconds()
        self.assertEqual(parse_post_age("15 de enero de 2024", now=ref_now), expected_seconds)
        self.assertEqual(parse_post_age("15 de janeiro de 2024", now=ref_now), expected_seconds)

        # Abreviaciones de mes comunes
        self.assertIsNotNone(parse_post_age("28 de fev", now=ref_now))
        self.assertIsNotNone(parse_post_age("5 de mai", now=ref_now))
        self.assertIsNotNone(parse_post_age("14 de oct", now=ref_now))

    def test_unparseable_date_strings_return_none(self):
        """Cadenas invalidas o no temporales retornan None de forma segura."""
        from motoradar.sources.facebook import parse_post_age

        self.assertIsNone(parse_post_age(""))
        self.assertIsNone(parse_post_age(None))
        self.assertIsNone(parse_post_age("precio negociable"))
        self.assertIsNone(parse_post_age("vendo moto 125"))
        self.assertIsNone(parse_post_age("1234"))


if __name__ == "__main__":
    unittest.main()
