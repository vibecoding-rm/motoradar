import copy
import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from motoradar.cli import collect, main, run_deals, run_once
from motoradar.config import Config, FilterConfig
from motoradar.enrich import enrich_facebook
from motoradar.filters import matches
from motoradar.locking import exclusive
from motoradar.market import Economics, Reference, evaluate
from motoradar.models import Listing, parse_price, parse_price_text
from motoradar.money import FX
from motoradar.notify import DeliveryResult, flush_pending, send_telegram, to_csv
from motoradar.pipeline import prepare
from motoradar.sources.base import BaseSource, SourceError
from motoradar.sources.facebook import (
    FacebookSource,
    _close_runtime,
    _post_identity,
    _validate_navigation,
)
from motoradar.sources.olx import OlxSource
from motoradar.store import SCHEMA_VERSION, Store


def moto(identifier="1", price=500, title="Honda Biz", **kwargs):
    return Listing("olx", identifier, title, f"https://example.test/{identifier}",
                   price=price, location="Jaguarão, RS", **kwargs)


class Prices(unittest.TestCase):
    def test_numeric_formats_do_not_truncate(self):
        for text, expected in [("1250", 1250), ("6500", 6500), ("R$ 1.250,00", 1250),
                               ("USD 1,250.00", 1250), ("1000,50", 1000.5),
                               ("1 250", 1250), ("1.5", 1.5), ("1.000.000", 1000000),
                               ("R$ 999 mil", 999000)]:
            with self.subTest(text=text):
                self.assertEqual(parse_price(text), expected)

    def test_invalid_numbers(self):
        for x in [True, -1, float("nan"), float("inf"), "-500", "1,2345", "consultar"]:
            with self.subTest(x=x):
                self.assertIsNone(parse_price(x))

    def test_post_prices_and_non_price_numbers(self):
        for text, expected in [("quero 6500 reais", (6500, "BRL")),
                               ("peço 5 mil", (5000, "BRL")),
                               ("vendo por US$ 200", (200, "USD")),
                               ("17 mil pezos", (17000, "UYU")),
                               ("vendo R$ 1,5 mil", (1500, "BRL")),
                               ("ano 2018 20.000 km contato 999123456", (None, None))]:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)

    def test_phone_and_year_are_never_prices(self):
        for text, expected in [("Vendo Biz 125 chamar por 53991234567 valor 900", (900, "BRL")),
                               ("CG ano 2012 por R$ 700", (700, "BRL"))]:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)

    def test_down_payments_and_installments_are_not_total_prices(self):
        cases = [
            ("Vendo moto valor 6500, entrada 800 reais", (6500, "BRL")),
            ("Entrada 800 reais, preco total 6500", (6500, "BRL")),
            ("Vendo moto em 10x 100 reais", (None, None)),
            ("Vendo moto valor 800 reais", (800, "BRL")),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_price_text(text), expected)

    def test_source_files_have_no_control_bytes(self):
        # A "\b" typed as a real backspace silently disabled the phone/year guards.
        root = Path(__file__).resolve().parent.parent
        for path in [*root.glob("motoradar/**/*.py"), *root.glob("tests/*.py")]:
            with self.subTest(path=path.name):
                self.assertNotRegex(path.read_bytes(), rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class Money(unittest.TestCase):
    def test_invalid_cache_shapes_and_rates_fall_back_safely(self):
        invalid = [[], None, {"USD": 1, "BRL": "5.15", "UYU": 40.2},
                   {"USD": 1, "BRL": True, "UYU": 40.2},
                   {"USD": 1, "BRL": -5.15, "UYU": 40.2}]
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "fx.json"
            with patch("motoradar.money.CACHE", cache), \
                    patch("motoradar.money.requests.get", side_effect=requests.Timeout("offline")):
                for payload in invalid:
                    with self.subTest(payload=payload):
                        cache.write_text(json.dumps(payload), encoding="utf-8")
                        fx = FX()
                        self.assertEqual(fx.source, "fallback")
                        self.assertEqual(fx.convert(200, "USD", "BRL"), 1030)
                cache.write_text(json.dumps({"USD": 1, "BRL": 5.2, "UYU": 41}), encoding="utf-8")
                fx = FX()
                self.assertEqual(fx.source, "cache")
                self.assertEqual(fx.convert(200, "USD", "BRL"), 1040)


class Selection(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(fx={"offline": True})
        self.fx = FX(offline=True)

    def select(self, listings):
        return prepare(listings, self.cfg, self.fx, enrich_details=False)

    def test_budget_inclusive_papers_and_broken_bikes_do_not_filter(self):
        result = self.select([moto("1", 1000, "Honda Biz sem documento motor fundido"),
                              moto("2", 700, "Sucata moto Honda CG 125 em pecas"),
                              moto("3", 1000.01)])
        self.assertEqual([l.external_id for l in result.opportunities], ["2", "1"])

    def test_accessories_requests_ads_and_non_vehicles_are_excluded(self):
        for title in ["Escape Honda Biz", "Compro Honda Biz", "Honda Biz loja promocao",
                      "Vendo projeto de panificadora", "Sucata Fusca", "Moto G 22"]:
            with self.subTest(title=title):
                self.assertFalse(self.select([moto(title=title)]).opportunities)

    def test_unknown_and_bait_prices_are_separate(self):
        result = self.select([moto("1", None), moto("2", 10)])
        self.assertEqual(len(result.unconfirmed), 2)
        self.assertFalse(result.opportunities)

    def test_description_corrects_bait_and_currency_before_budget(self):
        result = self.select([moto("1", 6, raw={"description": "vendo quero 6500 reais"}),
                              moto("2", 6, raw={"description": "vendo valor US$ 200"})])
        self.assertFalse(result.opportunities)
        self.assertEqual(result.observed[1].currency, "BRL")
        self.assertEqual(result.observed[1].price, 1030)

    def test_missing_price_can_be_resolved_from_description(self):
        result = self.select([moto(price=None, raw={"description": "vendo valor 800 reais"})])
        self.assertEqual(result.opportunities[0].price, 800)

    def test_no_comparison_for_unknown_currency(self):
        self.assertFalse(self.select([moto(currency="EUR")]).opportunities)

    def test_city_in_title_does_not_override_actual_location(self):
        l = moto(title="Honda Biz entrega em Jaguarao")
        l.location = "Porto Alegre"
        self.assertFalse(self.select([l]).opportunities)

    def test_duplicate_queries_do_not_duplicate_opportunities(self):
        l = moto()
        self.assertEqual(len(self.select([l, copy.deepcopy(l)]).opportunities), 1)

    def test_keyword_filters_ignore_internal_metadata(self):
        sucata = moto(title="Sucata Honda Biz 125 2007",
                      raw={"category": "autos-e-pecas/motos", "query": "moto"})
        self.assertEqual(matches(sucata, FilterConfig(exclude_keywords=["pecas"])), (True, ""))
        self.assertFalse(matches(sucata, FilterConfig(include_keywords=["moto"]))[0])
        described = moto(raw={"description": "Honda Biz, aceito troca"})
        self.assertFalse(matches(described, FilterConfig(exclude_keywords=["troca"]))[0])

    def test_intent_is_resolved_after_detail(self):
        self.cfg.filters.exclude_keywords = ["procuro"]
        l = moto(raw={"description": "vendo Honda Biz procuro carro para troca"})
        self.assertEqual(len(self.select([l]).opportunities), 1)

    def test_total_price_wins_over_down_payment(self):
        result = self.select([moto(price=6, raw={
            "description": "Vendo moto valor 6500, entrada 800 reais"})])
        self.assertFalse(result.opportunities)
        self.assertEqual(result.observed[0].price, 6500)
        self.assertEqual(result.observed[0].raw["alert_status"], "excluded")

    def test_object_condition_and_intent_are_classified_in_context(self):
        excluded = [
            moto("motor", 500, "Motor Honda Biz fundido", raw={"description": "pecas da Honda Biz"}),
            moto("motor2", 500, "Vendo motor Honda Biz fundido"),
            moto("escape", 500, "Escape Honda Biz", raw={"description": "pecas da Honda Biz"}),
            moto("wanted", 800, "Compro Honda Biz", raw={"description": "Vendo meu carro para comprar a moto"}),
            moto("fit", 800, "Honda Fit 2008"),
            moto("bmw", 800, "BMW 320i"),
            moto("phone", 800, "Celular Samsung Moto G 22"),
            moto("restock", 800, "Honda Biz", raw={"description": "Chegou reposicao na loja"}),
            moto("pieces", 400, "Peças de moto"),
            moto("piece", 50, "Vendo peça moto"),
            moto("baskets", 30, "Vendo 2 cestos de biz 100"),
            moto("plastic", 20, "Vendo plástico de moto"),
            moto("foot", 50, "Pé de moto"),
            moto("brushcutter", 600, "Roçadeira a gasolina 2 tempos CG-520N"),
            moto("house", 550, "Tiene 1 dormitorio cocina y baño",
                 raw={"description": "aceito moto na troca"}),
        ]
        for listing in excluded:
            with self.subTest(title=listing.title):
                result = self.select([listing])
                self.assertFalse(result.opportunities)
                self.assertFalse(result.unconfirmed)
        positives = [
            moto("project", 500, "Moto Honda Biz motor fundido"),
            moto("shopword", 800, "Vendo Honda Biz", raw={"description": "Moto chegou da oficina e funciona"}),
            moto("trade", 800, "Vendo Honda Biz", raw={"description": "procuro carro para troca"}),
            moto("winner", 500, "Vendo moto Winner 200cc", raw={"description": "Aceito celular como parte do pagamento"}),
            moto("unknown-with-accessory", 700, "Vendo moto Winner 200cc com baú"),
            moto("donor", 600, "Moto para retirada de peças"),
        ]
        for listing in positives:
            with self.subTest(title=listing.title, identifier=listing.external_id):
                self.assertEqual(len(self.select([listing]).opportunities), 1)

    def test_uyu_bait_is_corrected_then_normalized_in_base_currency(self):
        listing = moto(price=500, currency="UYU", title="Vendo moto Winner 200cc",
                       raw={"description": "Vendo moto valor 5000 pesos"})
        expensive = moto("expensive", 500, "Vendo moto Winner 200cc", currency="UYU",
                         raw={"description": "Vendo moto valor 10000 pesos"})
        result = self.select([listing, expensive])
        self.assertEqual(len(result.opportunities), 1)
        self.assertEqual((listing.price, listing.currency), (640.55, "BRL"))
        self.assertEqual(listing.raw["original_price"], 5000)
        self.assertEqual(expensive.raw["alert_status"], "excluded")

    def test_reusing_listing_with_new_fx_matches_fresh_evaluation(self):
        reused = moto(price=190, currency="USD", title="Vendo moto Winner 200cc")
        first = prepare([reused], self.cfg, FX({"USD": 1, "BRL": 5.15, "UYU": 40.2}, offline=True), False)
        self.assertEqual(first.opportunities[0].price, 978.5)
        second = prepare([reused], self.cfg, FX({"USD": 1, "BRL": 6, "UYU": 40.2}, offline=True), False)
        fresh = prepare([moto(price=190, currency="USD", title="Vendo moto Winner 200cc")],
                        self.cfg, FX({"USD": 1, "BRL": 6, "UYU": 40.2}, offline=True), False)
        self.assertEqual(second.observed[0].price, fresh.observed[0].price)
        self.assertEqual(second.observed[0].raw["alert_status"], "excluded")

    def test_price_presentation_and_reference_sufficiency_are_consistent(self):
        self.assertEqual(moto(price=999.99).pretty_price(), "R$ 999,99")
        listing = moto(price=500)
        deal = evaluate(listing, {"honda-biz": Reference("honda-biz", 3, 5000, 5000, 5000)},
                        Economics(min_samples=5))
        self.assertIsNone(deal.margin)
        self.assertIn("minimo=5", deal.confidence)
        self.assertTrue(any("pocas muestras" in warning for warning in deal.warnings))


class Persistence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "radar.sqlite3")
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def opportunity(self, **kwargs):
        return moto(raw={"alert_status": "confirmed"}, **kwargs)

    def test_distinct_ids_survive_and_price_updates_preserve_first_seen(self):
        a = self.opportunity(price=900)
        self.store.upsert(a, "chat", True)
        first = self.store.conn.execute("SELECT first_seen FROM listings").fetchone()[0]
        self.store.upsert(self.opportunity(identifier="2", price=900), "chat", True)
        a.price = 500
        self.store.upsert(a, "chat", True)
        row = self.store.conn.execute("SELECT price,first_seen FROM listings WHERE uid=?", (a.uid,)).fetchone()
        self.assertEqual(tuple(row), (500, first))
        self.assertEqual(self.store.count(), 2)

    def test_repeat_does_not_duplicate_delivery_and_drop_creates_event(self):
        a = self.opportunity(price=900)
        self.assertTrue(self.store.upsert(a, "chat", True))
        self.assertFalse(self.store.upsert(a, "chat", True))
        self.assertEqual(len(self.store.pending("chat")), 1)
        self.store.delivered(self.store.pending("chat")[0]["id"])
        a.price = 500
        self.assertTrue(self.store.upsert(a, "chat", True))
        self.assertEqual(self.store.pending("chat")[0]["reason"], "bajada de precio")

    def test_drop_into_budget_and_confirmed_price_transition(self):
        a = moto(price=2000, raw={"alert_status": "excluded"})
        self.store.upsert(a)
        a.price = 1000
        a.raw["alert_status"] = "confirmed"
        self.assertTrue(self.store.upsert(a, "chat", True))

    def test_json_is_valid_and_untruncated(self):
        a = moto(raw={"description": "x" * 25000})
        self.store.upsert(a)
        raw = self.store.conn.execute("SELECT raw FROM listings").fetchone()[0]
        self.assertEqual(len(json.loads(raw)["description"]), 25000)

    def test_pending_survives_restart_and_is_cancelled_when_out_of_budget(self):
        a = self.opportunity()
        self.store.upsert(a, "chat", True)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(len(self.store.pending("chat")), 1)
        a.price = 2000
        a.raw["alert_status"] = "excluded"
        self.store.upsert(a)
        self.assertFalse(self.store.pending("chat"))

    def test_pending_is_cancelled_when_listing_stops_being_eligible(self):
        # alert_unconfirmed: false -> an unconfirmed observation is not eligible.
        a = self.opportunity(price=900)
        self.store.upsert(a, "chat", True)
        a.price = None
        a.raw["alert_status"] = "unconfirmed"
        self.store.upsert(a, "chat", False)
        self.assertFalse(self.store.pending("chat"))
        a.price = 900
        a.raw["alert_status"] = "confirmed"
        self.assertTrue(self.store.upsert(a, "chat", True))
        self.assertEqual(json.loads(self.store.pending("chat")[0]["payload"])["price"], 900)

    def test_can_configure_telegram_after_console_only_run(self):
        a = self.opportunity()
        self.store.upsert(a, alert=True)
        self.assertTrue(self.store.upsert(a, "chat", True))
        self.assertEqual(len(self.store.pending("chat")), 1)

    def test_pending_payload_updates_without_resetting_retry(self):
        a = self.opportunity(price=700)
        self.store.upsert(a, "chat", True)
        item = self.store.pending("chat")[0]
        self.store.failed(item["id"], 30, "offline")
        a.price = 900
        self.store.upsert(a, "chat", True)
        row = self.store.conn.execute("SELECT * FROM deliveries").fetchone()
        self.assertEqual(json.loads(row["payload"])["price"], 900)
        self.assertEqual(row["attempts"], 1)

    def test_partial_delivery_failure_retries_individually(self):
        self.store.upsert(self.opportunity(), "chat", True)
        self.store.upsert(self.opportunity(identifier="2"), "chat", True)
        with patch("motoradar.notify.send_telegram", side_effect=[DeliveryResult(True), DeliveryResult(False, "offline")]), redirect_stdout(io.StringIO()):
            self.assertEqual(flush_pending(self.store, "fake-token", "chat"), 1)
        self.assertEqual(self.store.conn.execute("SELECT count(*) FROM deliveries WHERE sent_at IS NULL").fetchone()[0], 1)
        self.store.conn.execute("UPDATE deliveries SET retry_at=0")
        self.store.conn.commit()
        with patch("motoradar.notify.send_telegram", return_value=DeliveryResult(True)):
            self.assertEqual(flush_pending(self.store, "fake-token", "chat"), 1)

    def test_http_429_stops_batch(self):
        for i in ["1", "2"]:
            self.store.upsert(self.opportunity(identifier=i), "chat", True)
        with patch("motoradar.notify.send_telegram", return_value=DeliveryResult(False, "429", 60)) as sender, redirect_stdout(io.StringIO()):
            flush_pending(self.store, "fake-token", "chat")
            self.assertEqual(sender.call_count, 1)
        self.assertFalse(self.store.pending("chat"))

    def test_destination_cooldown_survives_new_events_and_restart(self):
        with patch("motoradar.store.time.time", return_value=1000):
            a = self.opportunity(price=900)
            self.store.upsert(a, "chat", True)
            row = self.store.pending("chat")[0]
            self.store.failed(row["id"], 120, "429")
            self.store.defer_destination("chat", 120)
            a.price = 800
            self.store.upsert(a, "chat", True)
            self.store.upsert(self.opportunity(identifier="2"), "chat", True)
            stored = self.store.conn.execute(
                "SELECT attempts,retry_at,reason FROM deliveries WHERE uid=?", (a.uid,)).fetchone()
            self.assertEqual((stored["attempts"], stored["retry_at"], stored["reason"]),
                             (1, 1120, "bajada de precio"))
        self.store.close()
        self.store = Store(self.path)
        with patch("motoradar.store.time.time", return_value=1100):
            self.assertFalse(self.store.pending("chat"))
        with patch("motoradar.store.time.time", return_value=1121):
            self.assertEqual(len(self.store.pending("chat")), 2)

    def test_incompatible_delivery_is_isolated_without_blocking_next(self):
        self.store.upsert(self.opportunity(identifier="bad"), "chat", True)
        self.store.upsert(self.opportunity(identifier="good"), "chat", True)
        first = self.store.conn.execute("SELECT id FROM deliveries ORDER BY id LIMIT 1").fetchone()[0]
        self.store.conn.execute("UPDATE deliveries SET payload='{' WHERE id=?", (first,))
        self.store.conn.commit()
        with patch("motoradar.notify.send_telegram", return_value=DeliveryResult(True)) as sender, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(flush_pending(self.store, "fake-token", "chat"), 1)
        self.assertEqual(sender.call_count, 1)
        bad = self.store.conn.execute("SELECT attempts,sent_at,last_error FROM deliveries WHERE id=?", (first,)).fetchone()
        self.assertEqual((bad["attempts"], bad["sent_at"], bad["last_error"]),
                         (1, None, "payload incompatible"))

    def test_legacy_database_migrates_without_replaying_old_alerts(self):
        # Simulate the v0 schema/metadata and the old notified flag.
        a = moto(raw={"description": "antigua"})
        self.store.upsert(a)
        self.store.conn.execute("UPDATE listings SET notified=1")
        self.store.conn.execute("PRAGMA user_version=0")
        self.store.conn.commit()
        self.store.close()
        self.store = Store(self.path)
        a.raw["alert_status"] = "confirmed"
        self.assertFalse(self.store.upsert(a, "chat", True))
        self.assertFalse(self.store.pending("chat"))
        self.assertEqual(self.store.count(), 1)

    def test_real_v0_schema_migrates_to_current_and_preserves_rows(self):
        path = str(Path(self.temp.name) / "legacy.sqlite3")
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE listings (
            uid TEXT PRIMARY KEY, fingerprint TEXT, source TEXT, title TEXT,
            price REAL, url TEXT, location TEXT, image TEXT, posted_at TEXT,
            first_seen TEXT, last_seen TEXT, notified INTEGER DEFAULT 0, raw TEXT)""")
        conn.execute("""INSERT INTO listings
            (uid,source,title,price,url,first_seen,last_seen,notified,raw)
            VALUES ('olx:old','olx','Honda Biz',800,'https://example.test/old',
                    '2024-01-01','2024-01-02',1,'{}')""")
        conn.execute("PRAGMA user_version=0")
        conn.commit()
        conn.close()
        migrated = Store(path)
        try:
            self.assertEqual(migrated.conn.execute("PRAGMA user_version").fetchone()[0],
                             SCHEMA_VERSION)
            self.assertEqual(tuple(migrated.conn.execute(
                "SELECT uid,first_seen FROM listings").fetchone()), ("olx:old", "2024-01-01"))
            tables = {row[0] for row in migrated.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("destination_state", tables)
            self.assertIn("health_notices", tables)
        finally:
            migrated.close()

    def test_future_database_version_is_rejected_without_modification(self):
        path = Path(self.temp.name) / "future.sqlite3"
        conn = sqlite3.connect(path)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        conn.close()
        before = hashlib.sha256(path.read_bytes()).digest()
        with self.assertRaises(ValueError):
            Store(str(path))
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_lock_prevents_overlapping_runs(self):
        with exclusive(self.path), self.assertRaises(RuntimeError), exclusive(self.path):
            pass


class Telegram(unittest.TestCase):
    def test_http_success_requires_api_ok(self):
        with patch("motoradar.notify.requests.post", return_value=Mock(ok=True, status_code=200, json=lambda: {"ok": False})):
            self.assertFalse(send_telegram(moto(), "fake-token", "chat").ok)

    def test_network_error_does_not_expose_token(self):
        with patch("motoradar.notify.requests.post", side_effect=requests.Timeout("url/fake-secret-token")):
            result = send_telegram(moto(), "fake-secret-token", "chat")
            self.assertFalse(result.ok)
            self.assertNotIn("fake-secret-token", result.error)

    def test_malformed_json_is_a_row_failure_not_a_batch_abort(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(str(Path(td) / "radar.db"))
            try:
                for identifier in ("1", "2"):
                    store.upsert(moto(identifier, raw={"alert_status": "confirmed"}), "chat", True)
                responses = [
                    Mock(ok=False, status_code=502, json=lambda: []),
                    Mock(ok=True, status_code=200, json=lambda: {"ok": True}),
                ]
                with patch("motoradar.notify.requests.post", side_effect=responses), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(flush_pending(store, "fake-token", "chat"), 1)
                rows = store.conn.execute("SELECT attempts,sent_at FROM deliveries ORDER BY id").fetchall()
                self.assertEqual(rows[0]["attempts"], 1)
                self.assertIsNone(rows[0]["sent_at"])
                self.assertIsNotNone(rows[1]["sent_at"])
            finally:
                store.close()

    def test_unconfirmed_message_is_explicit(self):
        response = Mock(ok=True, status_code=200, json=lambda: {"ok": True})
        listing = moto(raw={"alert_status": "unconfirmed"})
        with patch("motoradar.notify.requests.post", return_value=response) as post:
            self.assertTrue(send_telegram(listing, "fake-token", "chat").ok)
        self.assertIn("PRECIO POR CONFIRMAR", post.call_args.kwargs["json"]["text"])


class EndToEnd(unittest.TestCase):
    def test_watcher_pipeline_alerts_broken_bikes_and_retries_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(db_path=str(Path(td)/"radar.db"), csv_path=str(Path(td)/"hits.csv"),
                         fx={"offline": True}, telegram={"token": "fake", "chat_id": "chat"})
            listings = [moto("1", 800, "Honda Biz sem documento motor fundido"),
                        moto("2", 600, "Escape Honda Biz"), moto("3", 2000)]
            with patch("motoradar.cli.collect", side_effect=lambda *a, **kw: (copy.deepcopy(listings), {"olx": "OK"})), patch("motoradar.notify.send_telegram", return_value=DeliveryResult(False, "offline")), redirect_stdout(io.StringIO()):
                self.assertEqual(run_once(cfg, enrich_details=False), 1)
            store = Store(cfg.db_path)
            self.assertEqual(store.conn.execute("SELECT notified FROM listings WHERE uid='olx:1'").fetchone()[0], 0)
            store.conn.execute("UPDATE deliveries SET retry_at=0")
            store.conn.commit()
            store.close()
            with patch("motoradar.cli.collect", return_value=([], {"olx": "ERROR: offline"})), patch("motoradar.notify.send_telegram", return_value=DeliveryResult(True)) as sender, redirect_stdout(io.StringIO()):
                with self.assertRaises(SourceError):
                    run_once(cfg, enrich_details=False)
                self.assertEqual(sender.call_count, 1)

    def test_unconfirmed_is_labelled_in_console_and_csv(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(db_path=str(Path(td)/"radar.db"), csv_path=str(Path(td)/"hits.csv"),
                         fx={"offline": True})
            out = io.StringIO()
            with patch("motoradar.cli.collect", return_value=([moto("1", 10), moto("2", 900)], {"olx": "OK"})), redirect_stdout(out):
                run_once(cfg, enrich_details=False)
            lineas = out.getvalue().splitlines()
            # El importe sin confirmar no se presenta como si fuera el precio:
            # se dice que sale de la tarjeta y que falta confirmarlo.
            carnada = next(l for l in lineas if "R$ 10" in l)
            self.assertIn("SIN CONFIRMAR", carnada)
            self.assertIn("la tarjeta decia", carnada)
            confirmada = next(l for l in lineas if "R$ 900" in l)
            self.assertIn("[OK]", confirmada)
            self.assertNotIn("SIN CONFIRMAR", confirmada)
            with open(cfg.csv_path, encoding="utf-8") as fh:
                rows = {row["uid"]: row["alert_status"] for row in csv.DictReader(fh)}
            self.assertEqual(rows, {"olx:1": "unconfirmed", "olx:2": "confirmed"})

    def test_csv_with_old_columns_is_moved_aside(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"hits.csv"
            path.write_text("source,external_id\nolx,9\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                to_csv([moto(raw={"alert_status": "confirmed"})], str(path))
            with path.open(encoding="utf-8") as fh:
                self.assertIn("alert_status", next(csv.reader(fh)))
            self.assertEqual(len(list(Path(td).glob("hits*.csv"))), 2)

    def test_locked_csv_does_not_block_delivery(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(db_path=str(Path(td)/"radar.db"), csv_path=str(Path(td)/"hits.csv"),
                         fx={"offline": True}, telegram={"token": "fake", "chat_id": "chat"})
            with patch("motoradar.cli.collect", return_value=([moto("1", 800)], {"olx": "OK"})), \
                    patch("motoradar.cli.to_csv", side_effect=PermissionError("abierto en Excel")), \
                    patch("motoradar.notify.send_telegram", return_value=DeliveryResult(True)) as sender, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(run_once(cfg, enrich_details=False), 1)
            self.assertEqual(sender.call_count, 1)

    def test_dry_run_does_not_create_database_export_or_send(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(db_path=str(Path(td)/"radar.db"),csv_path=str(Path(td)/"hits.csv"),fx={"offline": True})
            with patch("motoradar.cli.collect", return_value=([moto()], {"olx": "OK"})), patch("motoradar.cli.flush_pending") as sender, redirect_stdout(io.StringIO()):
                run_once(cfg, dry_run=True, enrich_details=False)
            self.assertFalse(Path(cfg.db_path).exists())
            self.assertFalse(Path(cfg.csv_path).exists())
            sender.assert_not_called()

    def test_environment_overrides_yaml_values(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.yaml"
            p.write_text('telegram:\n  token: ""\n  chat_id: "del-yaml"\n', encoding="utf-8")
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "env", "TELEGRAM_CHAT_ID": "chat"}):
                cfg = Config.load(p)
                self.assertEqual(cfg.telegram, {"token": "env", "chat_id": "chat"})

    def test_a_token_in_the_yaml_is_rejected_with_instructions(self):
        """El token no puede vivir en el arbol del proyecto: un `grep -r` sobre
        *.yaml lo volca sin abrir el archivo. Paso de verdad en una auditoria."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.yaml"
            p.write_text('telegram:\n  token: "123:secreto"\n', encoding="utf-8")
            with (
                patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}),
                self.assertRaises(ValueError) as caso,
            ):
                Config.load(p)
            mensaje = str(caso.exception)
            self.assertIn("TELEGRAM_BOT_TOKEN", mensaje)
            self.assertNotIn("123:secreto", mensaje, "el error no puede repetir el token")

    def test_the_token_comes_from_a_file_outside_the_project(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.yaml"
            p.write_text('telegram:\n  token: ""\n  chat_id: "chat"\n', encoding="utf-8")
            archivo = Path(td) / "telegram_token"
            archivo.write_text("  123:del-archivo\n", encoding="utf-8")
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}), \
                    patch("motoradar.config.TOKEN_FILE", archivo):
                self.assertEqual(Config.load(p).telegram["token"], "123:del-archivo")

    def test_a_token_with_trailing_newline_is_normalised(self):
        """Pegado con Get-Content arrastra un salto: Telegram responde 404, el
        fallo se trata como transitorio y doctor sigue diciendo "configurado"."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.yaml"
            p.write_text('telegram:\n  chat_id: "chat"\n', encoding="utf-8")
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "  123:abc\n"}):
                self.assertEqual(Config.load(p).telegram["token"], "123:abc")

    def test_mistyped_yaml_is_rejected_before_use(self):
        bad_configs = [
            'sources:\n  olx:\n    enabled: "false"\n',
            'filters:\n  exclude_keywords: roubada\n',
            'monitoring:\n  alert_unconfirmed: "false"\n',
        ]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.yaml"
            for content in bad_configs:
                with self.subTest(content=content):
                    p.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        Config.load(p)

    def test_invalid_cli_options_fail_before_config_or_network(self):
        for args in [["run", "--source", "unknown"], ["watch", "--interval", "0"],
                     ["run", "--budget", "nan"], ["deals", "--top", "-1"]]:
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args)

    def test_olx_duplicate_page_does_not_stop_pagination(self):
        class FakeOlx(OlxSource):
            def _page(self, session, cfg, category, state, query, page, seen):
                ids = {"a": {1: ["1"], 2: ["2"]}, "b": {1: ["1"], 2: ["3"]}}
                for identifier in ids[query].get(page, []):
                    yield moto(identifier)
        cfg = Config()
        cfg.filters.queries = ["a", "b"]
        self.assertEqual([l.external_id for l in FakeOlx().fetch(cfg, {"categories": ["motos"], "pages": 2})], ["1", "2", "3"])

    def test_olx_all_pages_failed_is_not_a_successful_empty_search(self):
        class FailedOlx(OlxSource):
            def _page(self, *args):
                raise SourceError("offline")
        with redirect_stdout(io.StringIO()), self.assertRaises(SourceError):
            list(FailedOlx().fetch(Config(), {"categories": ["motos"], "pages": 1}))

    def test_fx_incompatible_observation_cancels_previous_delivery(self):
        class FakeSource(BaseSource):
            def __init__(self, listing):
                super().__init__()          # contadores del contrato
                self.listing = listing

            def fetch(self, cfg, opts):
                yield copy.deepcopy(self.listing)

        with tempfile.TemporaryDirectory() as td:
            cfg = Config(db_path=str(Path(td) / "radar.db"), csv_path=str(Path(td) / "hits.csv"),
                         fx={"offline": True}, telegram={"token": "fake", "chat_id": "chat"})
            first = moto(price=800)
            with patch("motoradar.cli.REGISTRY", {"fake": FakeSource(first)}), \
                    patch("motoradar.notify.send_telegram", return_value=DeliveryResult(False, "offline")), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(run_once(cfg, ["fake"], enrich_details=False), 1)
            second = moto(price=800, currency="EUR")
            with patch("motoradar.cli.REGISTRY", {"fake": FakeSource(second)}), \
                    patch("motoradar.notify.send_telegram") as sender, redirect_stdout(io.StringIO()):
                self.assertEqual(run_once(cfg, ["fake"], enrich_details=False), 0)
                sender.assert_not_called()
            store = Store(cfg.db_path)
            try:
                row = store.conn.execute("SELECT raw FROM listings WHERE uid=?", (second.uid,)).fetchone()
                self.assertEqual(json.loads(row["raw"])["alert_status"], "excluded")
                self.assertEqual(store.conn.execute(
                    "SELECT count(*) FROM deliveries WHERE sent_at IS NULL").fetchone()[0], 0)
            finally:
                store.close()

    def test_partial_source_keeps_results_and_structured_diagnostic(self):
        class PartialSource(BaseSource):
            def fetch(self, cfg, opts):
                yield moto()
                raise SourceError("pagina 2 bloqueada")

        with patch("motoradar.cli.REGISTRY", {"fake": PartialSource()}), redirect_stdout(io.StringIO()):
            listings, stats = collect(Config(fx={"offline": True}), ["fake"], apply_filters=False)
        self.assertEqual(len(listings), 1)
        self.assertEqual(stats["fake"]["status"], "partial")
        self.assertIn("pagina 2 bloqueada", stats["fake"]["errors"])

    def test_deals_fails_when_every_source_failed(self):
        stats = {"olx": {"status": "error", "observed": 0, "discarded": 0,
                         "failed_pages": 0, "errors": ["offline"]}}
        with patch("motoradar.cli.collect", return_value=([], stats)), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(run_deals(Config(fx={"offline": True}), enrich_details=False), 1)

    def test_csv_neutralizes_spreadsheet_formulas(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hits.csv"
            listings = [moto(str(i), title=value) for i, value in enumerate(
                ['=HYPERLINK("https://example.test","Moto")', '+SUM(1,1)', '-1+2', '@cmd', '  =1+1'])]
            to_csv(listings, str(path))
            with path.open(encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertTrue(all(row["title"].startswith("'") for row in rows))
            self.assertTrue(all(row["price"] == "500" for row in rows))

    def test_olx_distinguishes_empty_blocked_and_unknown_pages(self):
        class Response:
            status_code = 200

            def __init__(self, html):
                self.content = html.encode()

        class Session:
            def __init__(self, html):
                self.html = html

            def get(self, *args, **kwargs):
                return Response(self.html)

        source = OlxSource()
        args = (Config(), "motos", "estado-rs", "moto", 1, set())
        with self.assertRaises(SourceError):
            list(source._page(Session("Verify you are human captcha"), *args))
        with self.assertRaises(SourceError):
            list(source._page(Session("<html>layout nuevo</html>"), *args))
        self.assertEqual(list(source._page(Session("Nenhum anúncio encontrado"), *args)), [])

    def test_facebook_navigation_identity_and_cleanup_are_explicit(self):
        for url in ("https://facebook.com/login/", "https://facebook.com/checkpoint/123"):
            with self.subTest(url=url), self.assertRaises(SourceError):
                _validate_navigation(Mock(url=url))
        _validate_navigation(Mock(url="https://facebook.com/groups/1/search/"))

        first, confidence = _post_identity({"id": "123456", "text": "valor 900"})
        edited, _ = _post_identity({"id": "123456", "text": "valor 800"})
        other, _ = _post_identity({"id": "654321", "text": "valor 900"})
        fallback, fallback_confidence = _post_identity({"text": "sem link"})
        self.assertEqual(first, edited)
        self.assertNotEqual(first, other)
        self.assertEqual(confidence, "estable")
        self.assertTrue(fallback)
        self.assertIn("inestable", fallback_confidence)

        ctx = Mock()
        ctx.pages = []
        ctx.new_page.side_effect = RuntimeError("new page fallo")
        ctx.close.side_effect = RuntimeError("close fallo")
        pw = Mock()
        profile = Mock()
        profile.exists.return_value = True
        with patch("motoradar.sources.facebook.PROFILE_DIR", profile), \
                patch("motoradar.sources.facebook._launch", return_value=(pw, ctx)), \
                self.assertRaises(RuntimeError):
            list(FacebookSource().fetch(Config(), {}))
        ctx.close.assert_called_once()
        pw.stop.assert_called_once()

        ctx2, pw2 = Mock(), Mock()
        ctx2.close.side_effect = RuntimeError("close")
        _close_runtime(pw2, ctx2)
        pw2.stop.assert_called_once()

    def test_facebook_uses_dedicated_marketplace_queries_with_progress(self):
        page = Mock()
        page.url = "https://www.facebook.com/marketplace/search/"
        page.evaluate.return_value = []
        out = io.StringIO()
        opts = {"marketplace_queries": ["moto", "biz"],
                "navigation_timeout_ms": 12345}
        with patch("motoradar.sources.facebook.time.sleep"), \
                patch("motoradar.sources.facebook._scroll"), redirect_stdout(out):
            self.assertEqual(list(FacebookSource()._marketplace(
                page, Config(), opts, rounds=1, pause=0)), [])
        self.assertEqual(page.goto.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] == 12345
                            for call in page.goto.call_args_list))
        self.assertIn("consulta 1/2: moto", out.getvalue())
        self.assertIn("consulta 2/2: biz", out.getvalue())

    def test_facebook_detail_limit_reads_the_prices_it_can_correct(self):
        """El cupo de detalle va a lo que la lectura PUEDE arreglar.

        Antes se leian primero los precios ya plausibles y el limite dejaba sin
        leer justo las carnadas, que son las unicas cuyo precio esta mal: en una
        corrida real salieron 12 avisos "PRECIO POR CONFIRMAR" a R$0/R$5/R$55
        por una sola candidata legitima.
        """
        bait = Listing("facebook", "bait", "Moto", "https://www.facebook.com/marketplace/item/bait/",
                       price=10, location="Jaguarão")
        confirmed = Listing("facebook", "confirmed", "Vendo moto",
                            "https://www.facebook.com/marketplace/item/confirmed/",
                            price=500, location="Jaguarão")
        unknown = Listing("facebook", "unknown", "Moto",
                          "https://www.facebook.com/marketplace/item/unknown/",
                          price=None, location="Jaguarão")
        page = Mock()
        page.evaluate.return_value = "Detalles\nEstado Usado - Aceptable\nInformacion del vendedor"
        ctx, pw = Mock(), Mock()
        ctx.pages = [page]
        profile = Mock()
        profile.exists.return_value = True
        out = io.StringIO()
        with patch("motoradar.sources.facebook.PROFILE_DIR", profile), \
                patch("motoradar.sources.facebook._launch", return_value=(pw, ctx)), \
                patch("motoradar.sources.facebook._settle"), \
                patch("motoradar.enrich.time.sleep"), redirect_stdout(out):
            self.assertEqual(enrich_facebook(
                [bait, unknown, confirmed], pause=0, max_items=2, timeout_ms=12345), 2)
        # Carnada primero, despues el aviso sin precio; el que ya tiene un
        # precio plausible es el que se queda sin cupo.
        self.assertEqual([call.args[0] for call in page.goto.call_args_list],
                         [bait.url, unknown.url])
        self.assertEqual(confirmed.raw["detail_status"], "omitido por limite operativo")
        self.assertEqual(bait.raw["detail_status"], "leido")
        self.assertIn("detalle 1/2", out.getvalue())
        ctx.close.assert_called_once()
        pw.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
