from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Listing, now_iso

# Additive migration: existing listing rows and first_seen are retained.
SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    uid TEXT PRIMARY KEY, fingerprint TEXT, source TEXT, title TEXT,
    price REAL, url TEXT, location TEXT, image TEXT, posted_at TEXT,
    first_seen TEXT, last_seen TEXT, notified INTEGER DEFAULT 0, raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_fingerprint ON listings(fingerprint);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY, uid TEXT NOT NULL, observed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_uid ON observations(uid, id);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY, uid TEXT NOT NULL, destination TEXT NOT NULL,
    payload TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
    sent_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0, last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_deliveries_pending ON deliveries(destination, sent_at, retry_at);
CREATE TABLE IF NOT EXISTS destination_state (
    destination TEXT PRIMARY KEY, blocked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
    stats TEXT NOT NULL, opportunities INTEGER NOT NULL,
    unconfirmed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS health_notices (
    key TEXT PRIMARY KEY, notified_at REAL NOT NULL, detail TEXT
);
"""
SCHEMA_VERSION = 4
# Tope de reintentos antes de abandonar una entrega. Con 30s..1h de backoff son
# unas 12 horas: pasado eso, el fallo no es transitorio.
MAX_DELIVERY_ATTEMPTS = 12


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self.conn.close()
            raise ValueError("Base de datos de una version mas nueva")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate(version)

    def _migrate(self, version: int) -> None:
        """Migracion por version, no solo "crear tablas que falten".

        `SCHEMA` son `CREATE IF NOT EXISTS`: una columna nueva no se aplicaba a
        una base existente y el `PRAGMA user_version` se escribia igual, con lo
        cual la base quedaba marcada como migrada sin estarlo. La version se
        escribe recien cuando los pasos terminan.
        """
        with self.conn:
            if version < 4:
                columnas = {row["name"] for row in
                            self.conn.execute("PRAGMA table_info(deliveries)")}
                if "state" not in columnas:
                    self.conn.execute(
                        "ALTER TABLE deliveries ADD COLUMN state TEXT NOT NULL "
                        "DEFAULT 'pending'")
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def upsert(self, listing: Listing, destination: str = "", alert: bool = False) -> bool:
        """Compatibility wrapper for a single recipient."""
        return self.upsert_many(listing, [destination], alert)

    def upsert_many(self, listing: Listing, destinations: list[str], alert: bool = False) -> bool:
        """Observe once and enqueue all recipients in the same transaction.

        Event decisions use the previous observation, never the state already
        overwritten while enqueueing an earlier recipient.
        """
        listing.raw["currency"] = listing.currency
        payload = json.dumps(asdict(listing), ensure_ascii=False, allow_nan=False)
        raw = json.dumps(listing.raw, ensure_ascii=False, allow_nan=False)
        ts = now_iso()
        new_recipient = False
        with self.conn:
            seen = self.conn.execute("SELECT * FROM listings WHERE uid=?", (listing.uid,)).fetchone()
            try:
                previous = json.loads(seen["raw"] or "{}") if seen else {}
            except json.JSONDecodeError:
                previous = {}
            old_status = previous.get("alert_status", "excluded")
            new_status = listing.raw.get("alert_status", "excluded")
            old_price = previous.get("original_price", seen["price"] if seen else None)
            new_price = listing.raw.get("original_price", listing.price)
            same_currency = previous.get("original_currency", previous.get("currency", "BRL")) == listing.raw.get("original_currency", listing.currency)
            dropped = (same_currency and new_price is not None and old_price is not None
                       and new_price < old_price and new_status == "confirmed")
            event = (seen is None or old_status == "excluded" or
                     (new_status == "confirmed" and old_status != "confirmed") or dropped)
            reason = "bajada de precio" if dropped else "nueva oportunidad"
            legacy_notified = bool(seen and "alert_status" not in previous
                                   and seen["notified"] and not dropped)
            if legacy_notified:
                event = False
            changed = not seen or any(seen[k] != getattr(listing, k) for k in
                                     ("title", "price", "url", "location", "image", "posted_at")) or seen["raw"] != raw
            self.conn.execute(
                """INSERT INTO listings
                (uid,fingerprint,source,title,price,url,location,image,posted_at,first_seen,last_seen,raw)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET fingerprint=excluded.fingerprint,
                title=excluded.title,price=excluded.price,url=excluded.url,
                location=excluded.location,image=excluded.image,posted_at=excluded.posted_at,
                last_seen=excluded.last_seen,raw=excluded.raw""",
                (listing.uid, listing.fingerprint, listing.source, listing.title,
                 listing.price, listing.url, listing.location, listing.image,
                 listing.posted_at, ts, ts, raw))
            if changed:
                self.conn.execute("INSERT INTO observations(uid,observed_at,payload) VALUES(?,?,?)",
                                  (listing.uid, ts, payload))
            if new_status == "excluded" or not alert:
                # A pending snapshot must not announce a state the listing no longer has.
                self.conn.execute("DELETE FROM deliveries WHERE uid=? AND sent_at IS NULL", (listing.uid,))
            else:
                for destination in dict.fromkeys(destinations):
                    if not destination:
                        continue
                    first_delivery = not legacy_notified and not self.conn.execute(
                        "SELECT 1 FROM deliveries WHERE uid=? AND destination=? LIMIT 1",
                        (listing.uid, destination)).fetchone()
                    # Telegram can be configured after console-only runs, or
                    # a new recipient added later. This does not change the
                    # shared event decision for the other recipients.
                    recipient_event = event or first_delivery
                    if recipient_event:
                        pending = self.conn.execute(
                            "SELECT id FROM deliveries WHERE uid=? AND destination=? AND sent_at IS NULL ORDER BY id DESC LIMIT 1",
                            (listing.uid, destination)).fetchone()
                        if pending:
                            self.conn.execute("UPDATE deliveries SET payload=?,reason=? WHERE id=?",
                                              (payload, reason, pending["id"]))
                        else:
                            self.conn.execute("INSERT INTO deliveries(uid,destination,payload,reason,created_at) VALUES(?,?,?,?,?)",
                                              (listing.uid, destination, payload, reason, ts))
                    else:
                        self.conn.execute("UPDATE deliveries SET payload=? WHERE uid=? AND destination=? AND sent_at IS NULL",
                                          (payload, listing.uid, destination))
                    if first_delivery:
                        new_recipient = True
            return (event or new_recipient) if alert else seen is None

    def pending(self, destination: str, limit: int = 50) -> list[sqlite3.Row]:
        state = self.conn.execute(
            "SELECT blocked_until FROM destination_state WHERE destination=?",
            (destination,)).fetchone()
        if state and state["blocked_until"] > time.time():
            return []
        return self.conn.execute(
            "SELECT * FROM deliveries WHERE destination=? AND sent_at IS NULL"
            " AND state='pending' AND retry_at<=? ORDER BY id LIMIT ?",
            (destination, time.time(), limit)).fetchall()

    def delivered(self, delivery_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE deliveries SET sent_at=?,last_error=NULL WHERE id=?", (now_iso(), delivery_id))
            self.conn.execute("UPDATE listings SET notified=1 WHERE uid=(SELECT uid FROM deliveries WHERE id=?)", (delivery_id,))

    def failed(self, delivery_id: int, retry_after: int, error: str,
               permanent: bool = False) -> None:
        """Un fallo permanente se abandona en vez de reintentarse para siempre.

        Sin esto, un socio que bloquea el bot (HTTP 403) o un chat_id mal
        tipeado dejaban una fila reintentandose cada hora indefinidamente, y el
        contador de pendientes de `doctor` dejaba de significar nada.
        """
        with self.conn:
            self.conn.execute(
                "UPDATE deliveries SET attempts=attempts+1,retry_at=?,last_error=?,"
                " state=CASE WHEN ? OR attempts+1 >= ? THEN 'dead' ELSE state END"
                " WHERE id=?",
                (time.time() + retry_after, error[:200], 1 if permanent else 0,
                 MAX_DELIVERY_ATTEMPTS, delivery_id))

    def abandoned(self) -> list[sqlite3.Row]:
        """Entregas que no se van a reintentar mas, para que `doctor` no las
        cuente como pendientes vivos."""
        return self.conn.execute(
            "SELECT destination, count(*) AS total, max(last_error) AS motivo"
            " FROM deliveries WHERE state='dead' AND sent_at IS NULL"
            " GROUP BY destination ORDER BY destination").fetchall()

    def defer_destination(self, destination: str, seconds: int) -> None:
        with self.conn:
            blocked_until = time.time() + seconds
            self.conn.execute(
                """INSERT INTO destination_state(destination,blocked_until) VALUES(?,?)
                ON CONFLICT(destination) DO UPDATE SET
                blocked_until=max(destination_state.blocked_until,excluded.blocked_until)""",
                (destination, blocked_until))
            self.conn.execute("UPDATE deliveries SET retry_at=max(retry_at,?) WHERE destination=? AND sent_at IS NULL",
                              (blocked_until, destination))

    def record_run(self, started: str, stats: dict, opportunities: int, unconfirmed: int) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO runs(started_at,finished_at,stats,opportunities,unconfirmed) VALUES(?,?,?,?,?)",
                              (started, now_iso(), json.dumps(stats, ensure_ascii=False), opportunities, unconfirmed))

    def source_zero_streak(self, source: str, limit: int = 20) -> int:
        """Corridas seguidas (de la mas reciente hacia atras) en las que la
        fuente no observo NADA.

        Una sola pasada vacia es normal un domingo; cinco seguidas con la
        sesion viva es el radar apagado y nadie avisando.
        """
        # "facebook/grupo-999": la fuente ubica el stat, la clave COMPLETA es la
        # que aparece en observed_by_origin.
        fuente, _, resto = source.partition("/")
        superficie = source if resto else ""
        streak = 0
        for row in self.conn.execute(
                "SELECT stats FROM runs ORDER BY id DESC LIMIT ?", (limit,)):
            try:
                stats = json.loads(row["stats"] or "{}")
            except json.JSONDecodeError:
                continue       # una corrida ilegible no demuestra nada
            stat = stats.get(fuente) if isinstance(stats, dict) else None
            if not isinstance(stat, dict):
                # La fuente no participo en esa corrida (un `run --source olx`,
                # o quedo pausada por sesion caida). Antes esto CORTABA la racha
                # y desactivaba el unico detector de radar apagado.
                continue
            if superficie:
                origenes = stat.get("observed_by_origin")
                if not isinstance(origenes, dict) or superficie not in origenes:
                    continue
                observed = origenes[superficie]
            else:
                if "observed" not in stat:
                    continue
                observed = stat["observed"]
            if observed:
                break
            streak += 1
        return streak

    def health_notice_due(self, key: str, cooldown_s: float = 6 * 3600) -> bool:
        """¿Toca volver a avisar por este problema? Solo LEE.

        Antes marcaba el cooldown aqui, antes de intentar el envio: si Telegram
        fallaba una vez, el aviso de sesion caida quedaba silenciado 6 horas y
        no se reintentaba nunca. Justo en el momento en que el radar tiene mas
        probabilidad de estar roto. Ahora el cooldown se marca despues, con
        `mark_health_notice`, y solo si alguien lo recibio.
        """
        row = self.conn.execute(
            "SELECT notified_at FROM health_notices WHERE key=?", (key,)).fetchone()
        if row and time.time() - float(row["notified_at"]) < cooldown_s:
            return False
        return True

    def mark_health_notice(self, key: str) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO health_notices(key,notified_at,detail) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET notified_at=excluded.notified_at,
                detail=excluded.detail""",
                (key, time.time(), now_iso()))

    def clear_health_notice(self, key: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM health_notices WHERE key=?", (key,))

    def prune(self, days: int) -> dict[str, int]:
        """Borra historico mas viejo que `days`.

        El feed cronologico de grupos guarda el texto completo de los posts de
        TODOS los vecinos, no solo de quien vende una moto. Sin plazo de borrado
        la base crece para siempre como un archivo de datos personales de gente
        que nunca vendio nada. No se toca ningun anuncio con entrega registrada:
        ese historico es lo unico que evita volver a alertar lo mismo.
        """
        vacio = {"observations": 0, "listings": 0, "runs": 0}
        if days <= 0:
            return vacio
        limite = (datetime.now(timezone.utc) - timedelta(days=days)
                  ).isoformat(timespec="seconds")
        with self.conn:
            borrado = {
                "observations": self.conn.execute(
                    "DELETE FROM observations WHERE observed_at < ?",
                    (limite,)).rowcount,
                "listings": self.conn.execute(
                    """DELETE FROM listings WHERE last_seen < ?
                    AND uid NOT IN (SELECT uid FROM deliveries)""",
                    (limite,)).rowcount,
                "runs": self.conn.execute(
                    "DELETE FROM runs WHERE finished_at < ?", (limite,)).rowcount,
            }
        return {key: max(0, value) for key, value in borrado.items()}

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
