import copy
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from motoradar.cli import main, run_once
from motoradar.config import Config
from motoradar.locking import exclusive
from motoradar.models import Listing, parse_price, parse_price_text
from motoradar.money import FX
from motoradar.notify import DeliveryResult, flush_pending, send_telegram
from motoradar.pipeline import prepare
from motoradar.sources.base import SourceError
from motoradar.sources.olx import OlxSource
from motoradar.store import Store


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

    def test_intent_is_resolved_after_detail(self):
        self.cfg.filters.exclude_keywords = ["procuro"]
        l = moto(raw={"description": "vendo Honda Biz procuro carro para troca"})
        self.assertEqual(len(self.select([l]).opportunities), 1)


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

    def test_legacy_database_migrates_without_replaying_old_alerts(self):
        # Simulate the v0 schema/metadata and the old notified flag.
        a = moto(raw={"description": "antigua"})
        self.store.upsert(a)
        self.store.conn.execute("UPDATE listings SET notified=1")
        self.store.conn.execute("PRAGMA user_version=0")
        self.store.conn.commit()
        self.store.close(); self.store = Store(self.path)
        a.raw["alert_status"] = "confirmed"
        self.assertFalse(self.store.upsert(a, "chat", True))
        self.assertFalse(self.store.pending("chat"))
        self.assertEqual(self.store.count(), 1)

    def test_lock_prevents_overlapping_runs(self):
        with exclusive(self.path):
            with self.assertRaises(RuntimeError):
                with exclusive(self.path):
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
            store.conn.commit(); store.close()
            with patch("motoradar.cli.collect", return_value=([], {"olx": "ERROR: offline"})), patch("motoradar.notify.send_telegram", return_value=DeliveryResult(True)) as sender, redirect_stdout(io.StringIO()):
                with self.assertRaises(SourceError):
                    run_once(cfg, enrich_details=False)
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
            p=Path(td)/"config.yaml";p.write_text('telegram:\n  token: "yaml"\n  chat_id: ""\n')
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "env", "TELEGRAM_CHAT_ID": "chat"}):
                cfg=Config.load(p)
                self.assertEqual(cfg.telegram, {"token": "env", "chat_id": "chat"})

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
        cfg=Config();cfg.filters.queries=["a", "b"]
        self.assertEqual([l.external_id for l in FakeOlx().fetch(cfg, {"categories": ["motos"], "pages": 2})], ["1", "2", "3"])

    def test_olx_all_pages_failed_is_not_a_successful_empty_search(self):
        class FailedOlx(OlxSource):
            def _page(self, *args):
                raise SourceError("offline")
        with redirect_stdout(io.StringIO()), self.assertRaises(SourceError):
            list(FailedOlx().fetch(Config(), {"categories": ["motos"], "pages": 1}))


if __name__ == "__main__":
    unittest.main()
