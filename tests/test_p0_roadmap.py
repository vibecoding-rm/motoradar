"""Verificación de P0 de Roadmap:
1. Mejora del extractor POST_JS (role="article", fallback children, IDs desde links, textos multilínea).
2. Fixtures sanitizados de OLX y Mercado Libre con verificación integral de extracción y pipeline.
3. Recuperación tras reinicio/caída abrupta en base SQLite de prueba (sin doble envío, sin corrupción).

100% offline, sin navegadores vivos ni conexiones de red reales.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from motoradar.config import Config, FilterConfig
from motoradar.models import Listing
from motoradar.money import FX
from motoradar.notify import DeliveryResult, flush_pending, send_telegram
from motoradar.pipeline import prepare
from motoradar.sources.mercadolivre import MercadoLivreSource
from motoradar.sources.olx import OlxSource
from motoradar.store import Store

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class TestP0PostJsAndFeedExtraction(unittest.TestCase):
    """P0 Cobertura de grupos: verificación de las mejoras del extractor POST_JS."""

    def test_post_js_structure_handles_articles_and_links(self):
        """Verificar que POST_JS incluye selectores de role=article, fallback a children y regex de IDs en links."""
        from motoradar.sources.facebook import POST_JS

        self.assertIn('[role="article"]', POST_JS)
        self.assertIn('feed.children', POST_JS)
        self.assertIn('posts|permalink|multi_permalinks', POST_JS)
        self.assertIn('commerce', POST_JS)
        self.assertIn('story_fbid', POST_JS)
        self.assertIn('data-ad-preview', POST_JS)

    def test_post_identity_supports_commerce_listing(self):
        """Verificar que _post_identity extrae el ID estable de URLs de tipo commerce/listing."""
        from motoradar.sources.facebook import _post_identity
        post = {"href": "https://www.facebook.com/commerce/listing/2990935431076652/?ref=share_attachment"}
        pid, conf = _post_identity(post)
        self.assertEqual(pid, "2990935431076652")
        self.assertEqual(conf, "estable")


class TestP0FixturesOlxAndMercadoLivre(unittest.TestCase):
    """P0 Fixtures sanitizados de fuentes alternativas (OLX y Mercado Livre)."""

    def test_olx_html_fixture_extraction_and_pipeline(self):
        """OLX: parseo de tarjetas desde HTML sanitizado y clasificación en el pipeline fronterizo."""
        html_content = (FIXTURES_DIR / "olx_search_cards.html").read_text(encoding="utf-8")

        class FakeResponse:
            status_code = 200
            content = html_content.encode("utf-8")

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        source = OlxSource()
        cfg = Config(
            budget=1000,
            filters=FilterConfig(
                cities=["jaguarao", "rio branco"],
                max_price=1000,
                min_price=0,
                queries=["moto"],
            ),
        )

        seen: set[str] = set()
        listings = list(
            source._page(
                FakeSession(),
                cfg,
                "autos-e-pecas/motos",
                "estado-rs/regioes-de-pelotas-rio-grande-e-bage",
                "moto",
                1,
                seen,
            )
        )

        # 5 tarjetas definidas en la fixture
        self.assertEqual(len(listings), 5)

        # Validar extracción individual
        biz = listings[0]
        self.assertEqual(biz.external_id, "1533801201")
        self.assertEqual(biz.title, "Honda Biz 100 2005")
        self.assertEqual(biz.price, 900.0)
        self.assertEqual(biz.currency, "BRL")
        self.assertEqual(biz.location, "Jaguarão, Centro")
        self.assertIn("biz100.jpg", biz.image)

        sucata = listings[1]
        self.assertEqual(sucata.external_id, "1533801202")
        self.assertEqual(sucata.price, 750.0)
        self.assertIn("Sucata CG 125", sucata.title)

        # Ejecutar en el pipeline unificado
        fx = FX(offline=True)
        res = prepare(listings, cfg, fx, enrich_details=False)

        # Oportunidades confirmadas: Biz (900) y Sucata CG 125 (750) en Jaguarão
        opp_ids = [l.external_id for l in res.opportunities]
        self.assertIn("1533801201", opp_ids)
        self.assertIn("1533801202", opp_ids)

        # Descartados esperados:
        # Pelotas (1533801203) por ciudad
        pelotas = next(l for l in listings if l.external_id == "1533801203")
        self.assertEqual(pelotas.raw.get("alert_status"), "excluded")
        self.assertIn("ciudad no coincide", pelotas.raw.get("excluded_reason", ""))

        # Titan (1533801204, R$ 2500) por presupuesto
        titan = next(l for l in listings if l.external_id == "1533801204")
        self.assertEqual(titan.raw.get("alert_status"), "excluded")
        self.assertIn("presupuesto", titan.raw.get("excluded_reason", ""))

        # Capacete (1533801205) por accesorio/no-moto
        capacete = next(l for l in listings if l.external_id == "1533801205")
        self.assertEqual(capacete.raw.get("alert_status"), "excluded")
        self.assertIn("repuesto", capacete.raw.get("excluded_reason", ""))

    def test_mercadolivre_json_fixture_extraction_and_pipeline(self):
        """Mercado Livre: parseo de JSON sanitizado (MLB Brasil y MLU Uruguay) y normalización."""
        data = json.loads((FIXTURES_DIR / "mercadolivre_search.json").read_text(encoding="utf-8"))

        class FakeMeliResponse:
            def __init__(self, payload):
                self.status_code = 200
                self._payload = payload

            def json(self):
                return self._payload

        source = MercadoLivreSource()
        cfg = Config(
            budget=1000,
            filters=FilterConfig(
                cities=["jaguarao", "rio branco"],
                max_price=1000,
                min_price=0,
                queries=["moto"],
            ),
            fx={"rates": {"UYU": 40.0, "BRL": 5.0, "USD": 1.0}},
        )
        fx = FX(cfg.fx["rates"], offline=True)

        with patch("motoradar.sources.mercadolivre.requests.get") as mock_get:
            def meli_get(url, params=None, **kwargs):
                if "/MLB/" in url:
                    return FakeMeliResponse(data["MLB"])
                elif "/MLU/" in url:
                    return FakeMeliResponse(data["MLU"])
                return FakeMeliResponse({"results": [], "paging": {"total": 0}})

            mock_get.side_effect = meli_get

            listings_mlb = list(source._search("MLB", {}, 50, "TUxCUFJTZTM4ZA", cfg))
            listings_mlu = list(source._search("MLU", {}, 50, "", cfg))

        self.assertEqual(len(listings_mlb), 3)
        self.assertEqual(len(listings_mlu), 2)

        # Comprobar extracción MLB
        cg_mlb = listings_mlb[0]
        self.assertEqual(cg_mlb.external_id, "MLB-1456789001")
        self.assertEqual(cg_mlb.price, 850.0)
        self.assertEqual(cg_mlb.currency, "BRL")
        self.assertIn("Jaguarão", cg_mlb.location)

        # Comprobar extracción MLU (Uruguay)
        yumbo_mlu = listings_mlu[0]
        self.assertEqual(yumbo_mlu.external_id, "MLU-2345678901")
        self.assertEqual(yumbo_mlu.price, 6500.0)
        self.assertEqual(yumbo_mlu.currency, "UYU")
        self.assertIn("Río Branco", yumbo_mlu.location)

        # Ejecutar ambas en el pipeline
        res = prepare(listings_mlb + listings_mlu, cfg, fx, enrich_details=False)

        opp_ids = [l.external_id for l in res.opportunities]
        # MLB-1456789001 (CG 125 Fan Sucata R$ 850 en Jaguarão) -> Oportunidad
        self.assertIn("MLB-1456789001", opp_ids)

        # MLU-2345678901 (Yumbo C110 UYU 6500 -> ~R$ 812.50 <= R$ 1000 en Río Branco) -> Oportunidad
        self.assertIn("MLU-2345678901", opp_ids)

        # MLU-2345678902 (Baccio Classic UYU 42000 -> ~R$ 5250 > R$ 1000) -> Descartada por presupuesto
        baccio = next(l for l in listings_mlu if l.external_id == "MLU-2345678902")
        self.assertEqual(baccio.raw.get("alert_status"), "excluded")
        self.assertIn("presupuesto", baccio.raw.get("excluded_reason", ""))


class TestP0CrashAndRestartRecovery(unittest.TestCase):
    """P0 Recuperación ante caída / reinicio abrupto en transacciones de persistencia y cola de envíos."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_recovery.sqlite3")
        self.store = Store(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_abrupt_crash_during_multi_delivery_resumes_without_duplicate(self):
        """Si el proceso cae tras notificar al socio A pero antes de notificar al socio B:
        1. La base sobrevive íntegra.
        2. Al reiniciar, el socio A no recibe duplicado.
        3. El socio B recibe su mensaje pendiente pendiente de forma limpia.
        """
        listing = Listing(
            source="facebook",
            external_id="crash-test-01",
            title="Honda CG 125 em ótimo estado",
            url="https://facebook.com/groups/1/posts/101",
            price=850.0,
            location="Jaguarão, RS",
            raw={"alert_status": "confirmed"},
        )

        recipients = ["chat_socio_A", "chat_socio_B"]
        # Encolar para ambos socios en la misma transacción atómica
        self.assertTrue(self.store.upsert_many(listing, recipients, alert=True))

        # Verificar que ambos tienen entrega pendiente
        self.assertEqual(len(self.store.pending("chat_socio_A")), 1)
        self.assertEqual(len(self.store.pending("chat_socio_B")), 1)

        # Simular entrega exitosa al socio A
        with patch("motoradar.notify.requests.post") as mock_post:
            mock_post.return_value = Mock(
                ok=True,
                status_code=200,
                json=lambda: {"ok": True, "result": {"message_id": 1001}},
            )
            sent_a = flush_pending(self.store, "fake_token", "chat_socio_A")
            self.assertEqual(sent_a, 1)

        # SIMULAR CAÍDA ABRUPTA DEL PROCESO:
        # Se cierra la conexión abruptamente y se reabre una nueva instancia de Store
        self.store.close()
        restarted_store = Store(self.db_path)

        # Verificación de integridad SQLite tras caída/reapertura
        integrity = restarted_store.conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(integrity, "ok")

        # Socio A ya NO tiene entregas pendientes (sent_at está marcado)
        self.assertEqual(len(restarted_store.pending("chat_socio_A")), 0)

        # Socio B sigue teniendo exactamente 1 entrega pendiente
        pending_b = restarted_store.pending("chat_socio_B")
        self.assertEqual(len(pending_b), 1)
        self.assertEqual(pending_b[0]["reason"], "nueva oportunidad")

        # Flushed tras reinicio: Socio B se entrega correctamente
        with patch("motoradar.notify.requests.post") as mock_post:
            mock_post.return_value = Mock(
                ok=True,
                status_code=200,
                json=lambda: {"ok": True, "result": {"message_id": 1002}},
            )
            sent_b = flush_pending(restarted_store, "fake_token", "chat_socio_B")
            self.assertEqual(sent_b, 1)

        # Ninguno tiene ya pendientes
        self.assertEqual(len(restarted_store.pending("chat_socio_A")), 0)
        self.assertEqual(len(restarted_store.pending("chat_socio_B")), 0)
        restarted_store.close()

    def test_interrupted_transaction_rolls_back_cleanly(self):
        """Una transacción de upsert_many que encuentra un fallo interno hace rollback completo."""
        listing = Listing(
            source="facebook",
            external_id="rollback-test",
            title="Honda Biz 100",
            url="https://facebook.com/groups/1/posts/102",
            price=700.0,
            location="Jaguarão, RS",
            raw={"alert_status": "confirmed"},
        )

        # Forzar un abort de SQLite mediante un trigger temporal para simular fallo a mitad de transacción
        self.store.conn.execute(
            "CREATE TRIGGER fail_obs BEFORE INSERT ON observations "
            "BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END;"
        )

        with self.assertRaises(sqlite3.Error):
            self.store.upsert_many(listing, ["chat_1"], alert=True)

        # Tras el rollback, no quedó la fila en listings ni en deliveries
        row = self.store.conn.execute("SELECT * FROM listings WHERE uid=?", (listing.uid,)).fetchone()
        self.assertIsNone(row)
        deliv = self.store.conn.execute("SELECT * FROM deliveries WHERE uid=?", (listing.uid,)).fetchall()
        self.assertEqual(len(deliv), 0)


if __name__ == "__main__":
    unittest.main()
