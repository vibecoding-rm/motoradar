from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

from .models import Listing

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def status_label(l: Listing) -> str:
    return "PRECIO POR CONFIRMAR" if l.raw.get("alert_status") == "unconfirmed" else "MOTO EN PRESUPUESTO"


def is_unconfirmed(l: Listing) -> bool:
    return l.raw.get("alert_status") == "unconfirmed"


def price_line(l: Listing) -> str:
    """Un importe sin confirmar no se muestra como si fuera el precio.

    La tarjeta de Marketplace trae carnada ("R$5" para una CG160 2025) o no
    trae precio. Mostrarlo como `R$ 5` a secas era afirmar un precio que nadie
    publicó: el aviso llega igual —una doadora sin precio es justo lo que se
    busca— pero diciendo qué se sabe y qué no.
    """
    if not is_unconfirmed(l):
        return l.pretty_price()
    if l.price is None:
        motivo = l.raw.get("card_price_evidence") or "el aviso no publica precio"
        return f"SIN PRECIO CONFIRMADO - {motivo}"
    return f"SIN CONFIRMAR - la tarjeta decia {l.pretty_price()}"


def detail_note(l: Listing) -> str:
    """Si nadie abrió el aviso, el mensaje lo dice."""
    estado = str(l.raw.get("detail_status") or "")
    if not estado or estado == "leido":
        return ""
    return f"detalle: {estado}"


def to_console(listings: list[Listing]) -> None:
    if not listings:
        print("  (sin anuncios nuevos)")
        return
    for l in listings:
        marca = "?" if is_unconfirmed(l) else "OK"
        print(f"  [{marca:>2}] {price_line(l):<38}  {l.title[:56]}")
        print(f"       {l.location[:34]:<34}  {l.url}")
        nota = detail_note(l)
        if nota:
            print(f"       {nota}")


def to_csv(listings: list[Listing], path: str) -> None:
    if not listings:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(listings[0].to_row().keys())
    if p.exists():
        # utf-8-sig al leer tambien: si el archivo ya trae BOM, sin esto la
        # primera cabecera no coincide y el CSV se renombraría en cada corrida.
        with p.open(newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh), [])
        if header != fieldnames:
            # Appending new columns under an old header would misalign every row.
            old = p.with_name(f"{p.stem}-columnas-anteriores-{int(time.time())}{p.suffix}")
            p.rename(old)
            print(f"  ! CSV con columnas anteriores movido a {old.name}")
    new_file = not p.exists()
    # BOM: Excel en Windows abre un utf-8 sin BOM como cp1252 y "Jaguarão"
    # sale "JaguarÃ£o". Es el formato con el que el operador mira los datos.
    with p.open("a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        for l in listings:
            writer.writerow({key: _csv_safe(value) for key, value in l.to_row().items()})


def _csv_safe(value):
    """Neutraliza formulas al abrir el CSV en hojas de calculo."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


# Fallos que no se arreglan reintentando: el socio bloqueo el bot, el chat no
# existe (id mal tipeado, o nunca le escribio al bot) o el token es invalido.
_PERMANENT_MARKERS = ("blocked", "bloqueado", "not found", "deactivated",
                      "kicked", "chat not found", "unauthorized")


def _is_permanent(status_code: int, description: str) -> bool:
    if status_code in (400, 401, 403, 404):
        return True
    low = description.lower()
    return any(marker in low for marker in _PERMANENT_MARKERS)


@dataclass
class DeliveryResult:
    ok: bool
    error: str = ""
    retry_after: int = 0
    permanent: bool = False


def _place(location: str) -> str:
    """Lugar buscable para el boton de Maps. Quita el sufijo '(grupo 123)' y
    descarta ubicaciones que no son un lugar real ('grupo 999')."""
    p = re.sub(r"\s*\(grupo[^)]*\)", "", location or "").strip()
    return "" if not p or p.lower().startswith("grupo") else p


def _link_caveat(l: Listing) -> str:
    """El boton 'Abrir aviso' no siempre cae en el post exacto: si el link se
    reconstruyo o va al grupo, el mensaje lo avisa en cristiano."""
    conf = str(l.raw.get("url_confidence", ""))
    if conf.startswith("sin permalink"):
        return "⚠️ el botón abre el grupo; buscá el aviso ahí"
    if conf.startswith("permalink reconstruido"):
        return "🔗 link reconstruido: puede no caer justo en el aviso"
    return ""


def _alert_buttons(l: Listing) -> list[list[dict]]:
    """Botones de URL (no necesitan que el bot escuche): 1 toque para abrir."""
    fila: list[dict] = []
    if l.url:
        fila.append({"text": "🔗 Abrir aviso", "url": l.url})
    lugar = _place(l.location)
    if lugar:
        fila.append({"text": "🗺️ Ver zona",
                     "url": "https://www.google.com/maps/search/?api=1&query=" + quote(lugar)})
    return [fila] if fila else []


def _alert_text(l: Listing) -> str:
    marca = "❓" if is_unconfirmed(l) else "✅"
    lineas = [f"{marca} <b>{status_label(l)}</b>"]
    # Bajada de precio, bien arriba y en grande: es lo mas vendedor del aviso.
    prev = l.raw.get("previous_price")
    if prev is not None and not is_unconfirmed(l) and l.price is not None:
        sym = Listing.SYMBOL.get(l.currency, l.currency)
        lineas.append(f"📉 <b>bajó {sym} {Listing._format_amount(prev)} → "
                      f"{sym} {Listing._format_amount(l.price)}</b>")
    lineas.append(f"🏍️ <b>{_esc(l.title[:120])}</b>")
    lineas.append(f"💰 {_esc(price_line(l))}")
    # Una sola linea compacta: zona · estado mecanico. Sin jerga ('via facebook',
    # url/location_confidence): eso al comprador no le suma.
    zona = _esc(l.location[:60]) or "sin ubicacion"
    lineas.append(f"📍 {zona} · 🔧 {_esc(l.raw.get('condition', 'por verificar'))}")
    if l.raw.get("doc_risk"):
        lineas.append("📄 sin papeles (no excluye)")
    nota_detalle = detail_note(l)
    if nota_detalle:
        lineas.append(f"🔎 {_esc(nota_detalle)}")
    caveat = _link_caveat(l)
    if caveat:
        lineas.append(_esc(caveat))
    return "\n".join(lineas)


def send_telegram(l: Listing, token: str, chat_id: str, reason: str = "") -> DeliveryResult:
    if not (token and chat_id):
        return DeliveryResult(False, "Telegram sin configurar")
    text = _alert_text(l)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        # Solo se previsualiza el enlace del aviso. Con la vista previa libre,
        # una URL escrita por un desconocido en el texto del post generaba una
        # tarjeta rica dentro de una alerta que parece del radar: el efecto
        # exacto que busca una estafa.
        "link_preview_options": {"url": l.url},
    }
    botones = _alert_buttons(l)
    if botones:
        payload["reply_markup"] = {"inline_keyboard": botones}
    try:
        r = requests.post(
            TELEGRAM_API.format(token=token),
            json=payload,
            timeout=20,
        )
        body = r.json()
        if not isinstance(body, dict):
            return DeliveryResult(False, "Telegram: respuesta incompatible")
        if r.ok and body.get("ok") is True:
            return DeliveryResult(True)
        parameters = body.get("parameters")
        retry_value = parameters.get("retry_after", 0) if isinstance(parameters, dict) else 0
        retry = (int(retry_value) if isinstance(retry_value, (int, float))
                 and not isinstance(retry_value, bool) and retry_value >= 0 else 0)
        # Un 429 "pelado" (sin parameters) tambien tiene que aplazar: sin esto el
        # bucle seguia disparando los pendientes restantes contra un endpoint
        # que ya estaba frenando al bot.
        if r.status_code == 429 and not retry:
            retry = int(r.headers.get("Retry-After", 0) or 60)
        # La `description` es la unica explicacion util. "Telegram HTTP 403" no
        # dice "te bloqueo" ni "ese chat no existe".
        detalle = str(body.get("description") or "").strip()
        error = f"Telegram HTTP {r.status_code}" + (f": {detalle}" if detalle else "")
        return DeliveryResult(False, error, retry, permanent=_is_permanent(r.status_code, detalle))
    except (requests.RequestException, ValueError, TypeError):
        # Exception URLs may contain bot tokens. Never store/print them.
        return DeliveryResult(False, "Telegram: error de red/respuesta")


def send_health(token: str, chat_id: str, text: str) -> DeliveryResult:
    """Aviso de salud del radar, no de un anuncio.

    Un radar que se apago en silencio es peor que uno que no existe: creés
    que no hay ofertas cuando en realidad no estás mirando. Estos mensajes
    no pasan por la cola de entregas: no hay un `uid` que reintentar.
    """
    if not (token and chat_id):
        return DeliveryResult(False, "Telegram sin configurar")
    try:
        r = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": f"🛑 <b>Motoradar</b>\n{_esc(text)}",
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        body = r.json()
        if r.ok and isinstance(body, dict) and body.get("ok") is True:
            return DeliveryResult(True)
        return DeliveryResult(False, f"Telegram HTTP {r.status_code}")
    except (requests.RequestException, ValueError, TypeError):
        return DeliveryResult(False, "Telegram: error de red/respuesta")


def flush_pending(store, token: str, chat_id: str) -> int:
    if not token or not chat_id:
        return 0
    sent = 0
    for delivery in store.pending(chat_id):
        try:
            payload = json.loads(delivery["payload"])
            if not isinstance(payload, dict):
                raise TypeError("payload no es objeto")
            listing = Listing(**payload)
            if not isinstance(listing.raw, dict):
                raise TypeError("raw no es objeto")
        except (json.JSONDecodeError, TypeError, ValueError):
            delay = min(86400, 300 * 2 ** min(delivery["attempts"], 7))
            store.failed(delivery["id"], delay, "payload incompatible")
            print(f"  ! entrega {delivery['id']} incompatible; queda aislada para reparar")
            continue
        result = send_telegram(listing, token, chat_id, delivery["reason"])
        if result.ok:
            store.delivered(delivery["id"])
            sent += 1
        else:
            delay = max(result.retry_after, min(3600, 30 * 2 ** min(delivery["attempts"], 7)))
            store.failed(delivery["id"], delay, result.error,
                         permanent=result.permanent)
            if result.permanent:
                print(f"  ! {result.error}; entrega abandonada para {chat_id[-4:]}: "
                      f"reintentar no la arregla")
            else:
                print(f"  ! {result.error}; pendiente, reintento en >= {delay}s")
            if result.retry_after:
                store.defer_destination(chat_id, result.retry_after)
                break  # Respect destination rate limits for remaining messages.
    return sent


def to_telegram(listings: list[Listing], token: str, chat_id: str) -> int:
    return sum(send_telegram(l, token, chat_id).ok for l in listings)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
