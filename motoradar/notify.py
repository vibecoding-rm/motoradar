from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import requests

from .models import Listing

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def to_console(listings: list[Listing]) -> None:
    if not listings:
        print("  (sin anuncios nuevos)")
        return
    for l in listings:
        print(f"  [{l.source:<13}] {l.pretty_price():>10}  {l.title[:60]}")
        print(f"                  {l.location[:40]}  {l.url}")


def to_csv(listings: list[Listing], path: str) -> None:
    if not listings:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new_file = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(listings[0].to_row().keys()))
        if new_file:
            writer.writeheader()
        for l in listings:
            writer.writerow(l.to_row())


@dataclass
class DeliveryResult:
    ok: bool
    error: str = ""
    retry_after: int = 0


def send_telegram(l: Listing, token: str, chat_id: str, reason: str = "") -> DeliveryResult:
    if not (token and chat_id):
        return DeliveryResult(False, "Telegram sin configurar")
    status = "PRECIO POR CONFIRMAR" if l.raw.get("alert_status") == "unconfirmed" else "MOTO EN PRESUPUESTO"
    papers = "sin papeles (no excluye)" if l.raw.get("doc_risk") else "papeles por verificar"
    text = (
            f"<b>{status}</b> · {_esc(reason)}\n"
            f"🏍️ <b>{_esc(l.title[:120])}</b>\n"
            f"💰 {l.pretty_price()}\n"
            f"📍 {_esc(l.location[:60]) or 'sin ubicacion'}\n"
            f"🔧 {_esc(l.raw.get('condition', 'por verificar'))} · {papers}\n"
            f"{_esc(l.raw.get('location_confidence', ''))}\n"
            f"🔗 {_esc(l.url)}\n"
            f"<i>via {_esc(l.source)}</i>"
        )
    try:
        r = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        body = r.json()
        if r.ok and body.get("ok") is True:
            return DeliveryResult(True)
        retry = int((body.get("parameters") or {}).get("retry_after", 0))
        return DeliveryResult(False, f"Telegram HTTP {r.status_code}", retry)
    except (requests.RequestException, ValueError, TypeError):
        # Exception URLs may contain bot tokens. Never store/print them.
        return DeliveryResult(False, "Telegram: error de red/respuesta")


def flush_pending(store, token: str, chat_id: str) -> int:
    if not token or not chat_id:
        return 0
    sent = 0
    for delivery in store.pending(chat_id):
        listing = Listing(**json.loads(delivery["payload"]))
        result = send_telegram(listing, token, chat_id, delivery["reason"])
        if result.ok:
            store.delivered(delivery["id"])
            sent += 1
        else:
            delay = max(result.retry_after, min(3600, 30 * 2 ** min(delivery["attempts"], 7)))
            store.failed(delivery["id"], delay, result.error)
            print(f"  ! {result.error}; pendiente, reintento en >= {delay}s")
            if result.retry_after:
                store.defer_destination(chat_id, result.retry_after)
                break  # Respect destination rate limits for remaining messages.
    return sent


def to_telegram(listings: list[Listing], token: str, chat_id: str) -> int:
    return sum(send_telegram(l, token, chat_id).ok for l in listings)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
