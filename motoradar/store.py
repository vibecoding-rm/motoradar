from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
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
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
    stats TEXT NOT NULL, opportunities INTEGER NOT NULL,
    unconfirmed INTEGER NOT NULL
);
PRAGMA user_version = 1;
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            self.conn.close()
            raise ValueError("Base de datos de una version mas nueva")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def upsert(self, listing: Listing, destination: str = "", alert: bool = False) -> bool:
        """Update by identity and enqueue each opportunity atomically."""
        listing.raw["currency"] = listing.currency
        payload = json.dumps(asdict(listing), ensure_ascii=False, allow_nan=False)
        raw = json.dumps(listing.raw, ensure_ascii=False, allow_nan=False)
        ts = now_iso()
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
            if seen and "alert_status" not in previous and seen["notified"] and not dropped:
                event = False
            elif alert and destination and not self.conn.execute(
                    "SELECT 1 FROM deliveries WHERE uid=? AND destination=? LIMIT 1",
                    (listing.uid, destination)).fetchone():
                event = True  # Telegram configured after earlier console-only runs.
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
            if new_status == "excluded":
                self.conn.execute("DELETE FROM deliveries WHERE uid=? AND sent_at IS NULL", (listing.uid,))
            elif alert and destination and event:
                self.conn.execute("DELETE FROM deliveries WHERE uid=? AND destination=? AND sent_at IS NULL",
                                  (listing.uid, destination))
                self.conn.execute("INSERT INTO deliveries(uid,destination,payload,reason,created_at) VALUES(?,?,?,?,?)",
                                  (listing.uid, destination, payload, reason, ts))
            elif alert and destination:
                self.conn.execute("UPDATE deliveries SET payload=? WHERE uid=? AND destination=? AND sent_at IS NULL",
                                  (payload, listing.uid, destination))
            return event if alert else seen is None

    def pending(self, destination: str, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM deliveries WHERE destination=? AND sent_at IS NULL AND retry_at<=? ORDER BY id LIMIT ?",
            (destination, time.time(), limit)).fetchall()

    def delivered(self, delivery_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE deliveries SET sent_at=?,last_error=NULL WHERE id=?", (now_iso(), delivery_id))
            self.conn.execute("UPDATE listings SET notified=1 WHERE uid=(SELECT uid FROM deliveries WHERE id=?)", (delivery_id,))

    def failed(self, delivery_id: int, retry_after: int, error: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE deliveries SET attempts=attempts+1,retry_at=?,last_error=? WHERE id=?",
                              (time.time() + retry_after, error[:200], delivery_id))

    def defer_destination(self, destination: str, seconds: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE deliveries SET retry_at=max(retry_at,?) WHERE destination=? AND sent_at IS NULL",
                              (time.time() + seconds, destination))

    def record_run(self, started: str, stats: dict, opportunities: int, unconfirmed: int) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO runs(started_at,finished_at,stats,opportunities,unconfirmed) VALUES(?,?,?,?,?)",
                              (started, now_iso(), json.dumps(stats, ensure_ascii=False), opportunities, unconfirmed))

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
