"""Entrega a varios destinatarios: los dos socios, cada uno en su chat.

La base ya guardaba las entregas por destinatario; lo que faltaba era que la
configuracion y la corrida admitieran mas de uno. Lo que estas pruebas fijan es
la parte que importa cuando algo sale mal: que un 429 o un fallo de red de una
persona NO retrase la alerta de la otra, porque la moto se vende igual.

Ninguna prueba envia mensajes reales: `requests.post` esta simulado.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from motoradar.cli import check_health, run_once
from motoradar.config import Config
from motoradar.models import Listing
from motoradar.notify import DeliveryResult, flush_pending, send_telegram
from motoradar.store import Store


def moto(identifier="1", price=800, title="Honda Biz motor fundido"):
    return Listing("olx", identifier, title, f"https://example.test/{identifier}",
                   price=price, location="Jaguarão, RS",
                   raw={"alert_status": "confirmed"})


class Destinatarios(unittest.TestCase):
    def test_chat_id_admits_one_id_or_a_list(self):
        casos = [
            ("7659880259", ["7659880259"]),
            (["7659880259", "8765417677"], ["7659880259", "8765417677"]),
            (7659880259, ["7659880259"]),
            # Repetidos y espacios no duplican entregas ni crean colas fantasma.
            (["7659880259", " 7659880259 ", ""], ["7659880259"]),
            ("", []),
        ]
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                cfg = Config(telegram={"token": "t", "chat_id": valor})
                cfg.validate()
                self.assertEqual(cfg.telegram_destinations(), esperado)

    def test_invalid_chat_id_is_rejected_before_any_run(self):
        for valor in (True, {"a": 1}, [None], [True]):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                Config(telegram={"token": "t", "chat_id": valor}).validate()

    def test_environment_can_carry_several_recipients(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.yaml"
            path.write_text("budget: 1000\ntelegram:\n  chat_id: \"del-yaml\"\n",
                            encoding="utf-8")
            with patch.dict(os.environ,
                            {"TELEGRAM_CHAT_ID": "111, 222 ,333"}, clear=False):
                cfg = Config.load(path)
            self.assertEqual(cfg.telegram_destinations(), ["111", "222", "333"])


class ColasSeparadas(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "radar.db"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_each_recipient_gets_an_independent_delivery(self):
        listing = moto()
        self.assertTrue(self.store.upsert(listing, "yo", True))
        self.assertTrue(self.store.upsert(listing, "socio", True))
        filas = self.store.conn.execute(
            "SELECT destination FROM deliveries ORDER BY destination").fetchall()
        self.assertEqual([f[0] for f in filas], ["socio", "yo"])
        # El anuncio se guarda una sola vez, con una sola observacion.
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.conn.execute(
            "SELECT count(*) FROM observations").fetchone()[0], 1)

    def test_a_429_for_one_recipient_does_not_hold_back_the_other(self):
        listing = moto()
        for destino in ("yo", "socio"):
            self.store.upsert(listing, destino, True)

        def post(url, json, timeout):
            respuesta = Mock()
            if json["chat_id"] == "yo":
                respuesta.ok = False
                respuesta.status_code = 429
                respuesta.json.return_value = {"ok": False,
                                               "parameters": {"retry_after": 600}}
            else:
                respuesta.ok = True
                respuesta.status_code = 200
                respuesta.json.return_value = {"ok": True}
            return respuesta

        with patch("motoradar.notify.requests.post", side_effect=post), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(flush_pending(self.store, "token", "yo"), 0)
            self.assertEqual(flush_pending(self.store, "token", "socio"), 1)
        # El aplazado queda solo para quien recibio el 429.
        self.assertEqual(self.store.pending("yo"), [])
        entregas = dict(self.store.conn.execute(
            "SELECT destination, sent_at IS NOT NULL FROM deliveries").fetchall())
        self.assertEqual(entregas, {"yo": 0, "socio": 1})

    def test_batch_rolls_back_observation_if_any_recipient_fails(self):
        self.store.conn.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON deliveries
            WHEN NEW.destination = 'socio' BEGIN
            SELECT RAISE(ABORT, 'simulated failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.upsert_many(moto(), ["yo", "socio"], True)
        self.assertEqual(self.store.count(), 0)
        for table in ("observations", "deliveries"):
            self.assertEqual(self.store.conn.execute(
                f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_price_drop_keeps_each_recipients_retry_state_after_restart(self):
        self.store.upsert_many(moto(price=800), ["yo", "socio"], True)
        for destination in ("yo", "socio"):
            row = self.store.pending(destination)[0]
            self.store.failed(row["id"], 600, "simulated network failure")
        before = list(self.store.conn.execute(
            "SELECT destination, attempts, retry_at FROM deliveries ORDER BY destination"))
        self.store.close()
        self.store = Store(str(Path(self.temp.name) / "radar.db"))
        self.store.upsert_many(moto(price=500), ["socio", "yo"], True)
        rows = self.store.conn.execute(
            "SELECT destination, attempts, retry_at, reason, payload FROM deliveries ORDER BY destination").fetchall()
        for old, row in zip(before, rows):
            self.assertEqual(tuple(row)[:3], tuple(old))
            self.assertEqual(row["reason"], "bajada de precio")
            self.assertEqual(json.loads(row["payload"])["price"], 500)

    def test_health_alerts_reach_every_recipient(self):
        cfg = Config(telegram={"token": "t", "chat_id": ["yo", "socio"]})
        stats = {"facebook": {"status": "error", "observed": 0, "dom_failures": 2}}
        with patch("motoradar.cli.send_health",
                   return_value=DeliveryResult(True)) as sender, \
                redirect_stdout(io.StringIO()):
            avisos = check_health(self.store, cfg, stats)
        self.assertEqual(len(avisos), 1, "un motivo, un aviso")
        self.assertEqual([call.args[1] for call in sender.call_args_list],
                         ["yo", "socio"])


class CorridaCompleta(unittest.TestCase):
    def test_both_partners_receive_drops_and_price_confirmation_in_any_order(self):
        for destinations in (["yo", "socio"], ["socio", "yo"]):
            with self.subTest(destinations=destinations), tempfile.TemporaryDirectory() as td:
                cfg = Config(db_path=str(Path(td) / "radar.db"),
                             csv_path=str(Path(td) / "hits.csv"),
                             fx={"offline": True},
                             telegram={"token": "fake", "chat_id": destinations})
                with patch("motoradar.cli.collect") as collector, \
                        patch("motoradar.notify.send_telegram", return_value=DeliveryResult(True)) as sender, \
                        redirect_stdout(io.StringIO()):
                    for price, expected_fresh in ((None, 1), (800, 1), (500, 1), (500, 0), (1500, 0), (700, 1)):
                        collector.return_value = ([moto(price=price)], {"facebook": {"status": "ok", "observed": 1}})
                        sender.reset_mock()
                        self.assertEqual(run_once(cfg, enrich_details=False), expected_fresh)
                        self.assertEqual(sender.call_count, 2 * expected_fresh)
                        if expected_fresh:
                            self.assertEqual([call.args[2] for call in sender.call_args_list], destinations)
                        if price == 500 and expected_fresh:
                            self.assertTrue(all(call.args[3] == "bajada de precio"
                                                for call in sender.call_args_list))

    def test_run_once_alerts_both_partners_for_the_same_bike(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(db_path=str(Path(td) / "radar.db"),
                         csv_path=str(Path(td) / "hits.csv"),
                         fx={"offline": True},
                         telegram={"token": "fake", "chat_id": ["yo", "socio"]})
            with patch("motoradar.cli.collect",
                       return_value=([moto("1", 800)], {"olx": {"status": "ok", "observed": 1}})), \
                    patch("motoradar.notify.send_telegram",
                          return_value=DeliveryResult(True)) as sender, \
                    redirect_stdout(io.StringIO()):
                # Una sola moto nueva, aunque se avise a dos personas.
                self.assertEqual(run_once(cfg, enrich_details=False), 1)
            self.assertEqual(sender.call_count, 2)
            self.assertEqual(sorted(call.args[2] for call in sender.call_args_list),
                             ["socio", "yo"])

    def test_without_telegram_the_run_still_persists(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(db_path=str(Path(td) / "radar.db"),
                         csv_path=str(Path(td) / "hits.csv"),
                         fx={"offline": True})
            with patch("motoradar.cli.collect",
                       return_value=([moto("1", 800)], {"olx": {"status": "ok", "observed": 1}})), \
                    redirect_stdout(io.StringIO()):
                run_once(cfg, enrich_details=False)
            store = Store(cfg.db_path)
            try:
                self.assertEqual(store.count(), 1)
                self.assertEqual(store.conn.execute(
                    "SELECT count(*) FROM deliveries").fetchone()[0], 0)
            finally:
                store.close()


class AlertasDiferenciadas(unittest.TestCase):
    """Los avisos sin precio confirmado LLEGAN igual (una doadora sin precio es
    justo lo que se busca), pero no se confunden con una compra confirmada ni
    afirman un importe que nadie publico."""

    def _enviado(self, listing):
        respuesta = Mock(ok=True, status_code=200, json=lambda: {"ok": True})
        with patch("motoradar.notify.requests.post", return_value=respuesta) as post:
            self.assertTrue(send_telegram(listing, "tok", "chat", "nueva oportunidad").ok)
        return post.call_args.kwargs["json"]["text"]

    def test_una_compra_confirmada_muestra_el_precio(self):
        listing = moto(price=800)
        texto = self._enviado(listing)
        self.assertIn("MOTO EN PRESUPUESTO", texto)
        self.assertIn("R$ 800", texto)
        self.assertNotIn("SIN CONFIRMAR", texto)

    def test_una_tarjeta_sin_precio_no_inventa_un_importe(self):
        listing = moto(price=None)
        listing.price = None
        listing.raw.update({"alert_status": "unconfirmed",
                            "card_price_evidence": 'la tarjeta mostraba "Gratis": '
                                                   "el aviso no publica precio",
                            "detail_status": "omitido por limite operativo"})
        texto = self._enviado(listing)
        self.assertIn("PRECIO POR CONFIRMAR", texto)
        self.assertIn("SIN PRECIO CONFIRMADO", texto)
        self.assertIn("no publica precio", texto)
        # Y dice que nadie abrio el aviso, que es la otra mitad del dato.
        self.assertIn("detalle: omitido por limite operativo", texto)
        self.assertNotIn("R$ 0", texto)

    def test_una_carnada_dice_de_donde_sale_el_numero(self):
        listing = moto(price=5)
        listing.raw.update({"alert_status": "unconfirmed",
                            "detail_status": "omitido por limite operativo"})
        texto = self._enviado(listing)
        self.assertIn("SIN CONFIRMAR - la tarjeta decia R$ 5", texto)


if __name__ == "__main__":
    unittest.main()
