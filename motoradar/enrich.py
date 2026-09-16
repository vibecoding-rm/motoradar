"""Lee la descripcion de cada aviso candidato.

El titulo miente por omision. Dos casos reales de Jaguarao que lo prueban:

  "A+pedido+"    R$500 -> "Vendo moto pra retirada de pecas ou pra alguem
                          que queira arrumar"   <- justo lo que buscas
  "Biz+Uruguai+" R$450 -> "A pedido alguma biz a venda"
                          <- NO vende: esta BUSCANDO comprar

Sin la descripcion, el segundo te manda a un callejon sin salida.

Solo se enriquecen las candidatas (las que pasaron precio y zona), asi que son
pocas requests y se puede ir despacio.
"""
from __future__ import annotations

import json
import re
import time

from .models import Listing, parse_price_text

# --- OLX ---------------------------------------------------------------------
LD_JSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def enrich_olx(listings: list[Listing], pause: float = 1.0) -> int:
    """La descripcion de OLX vive en el ld+json de la pagina de detalle."""
    try:
        from curl_cffi import requests as http
        kw = {"impersonate": "chrome"}
    except ImportError:
        import requests as http
        kw = {}

    session = http.Session()
    done = 0
    for listing in listings:
        if not listing.url or listing.raw.get("description"):
            continue
        try:
            r = session.get(listing.url, timeout=30, **kw)
            if r.status_code != 200:
                continue
            html = r.content.decode("utf-8", "replace")
            m = LD_JSON_RE.search(html)
            if not m:
                continue
            desc = json.loads(m.group(1)).get("description") or ""
            if desc:
                listing.raw["description"] = desc.strip()
                done += 1
        except Exception:
            continue  # un aviso ilegible no frena a los demas
        time.sleep(pause)
    return done


# --- Facebook ----------------------------------------------------------------
# La pagina cambia de idioma segun la cuenta, asi que se buscan los marcadores
# en los tres que pueden salir.
START_MARKERS = ("detalles", "detalhes", "details")
STOP_MARKERS = ("ubicacion es aproximada", "localizacao e aproximada",
                "location is approximate", "informacion del vendedor",
                "informacoes do vendedor", "seller information",
                "busquedas relacionadas", "buscas relacionadas")


def _norm(text: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _clean_plus(text: str) -> str:
    """Algunos avisos vienen con '+' en lugar de espacios:
    "Vendo+moto+pra+retirada+de+pecas". Solo se toca si los '+' son claramente
    separadores, para no romper un "CG 125+bau" legitimo."""
    if text.count("+") >= 3 and text.count(" ") < text.count("+"):
        return re.sub(r"\++", " ", text).strip()
    return text


def extract_description(body_text: str) -> str:
    lines = [l.strip() for l in body_text.splitlines() if l.strip()]
    start = None
    for i, line in enumerate(lines):
        if _norm(line) in START_MARKERS:
            start = i + 1
            break
    if start is None:
        return ""
    out: list[str] = []
    for line in lines[start:start + 12]:
        low = _norm(line)
        if any(mark in low for mark in STOP_MARKERS):
            break
        out.append(line)
    return _clean_plus(" ".join(out)).strip()


def enrich_facebook(listings: list[Listing], pause: float = 2.5) -> int:
    """Una sola sesion de navegador para todas las candidatas de Facebook."""
    from .sources.facebook import PROFILE_DIR, _launch, _settle

    pending = [l for l in listings
               if l.url and "/marketplace/item/" in l.url
               and not l.raw.get("description")]
    if not pending or not PROFILE_DIR.exists():
        return 0

    pw, ctx = _launch(headless=True)
    done = 0
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for listing in pending:
            try:
                page.goto(listing.url, timeout=45000, wait_until="domcontentloaded")
                _settle(page, 1.5)
                body = page.evaluate("() => document.body.innerText")
                desc = extract_description(body)
                if desc:
                    listing.raw["description"] = desc
                    done += 1
            except Exception:
                continue
            time.sleep(pause)
    finally:
        ctx.close()
        pw.stop()
    return done


def enrich(listings: list[Listing]) -> dict[str, int]:
    olx = [l for l in listings if l.source == "olx"]
    fb = [l for l in listings if l.source == "facebook"]
    return {"olx": enrich_olx(olx), "facebook": enrich_facebook(fb)}


# Un precio de tarjeta de R$1-100 casi nunca es el precio: o es carnada de
# desmanche, o Facebook lo muestra cortado. Caso real: la tarjeta decia "R$6"
# y la descripcion "YBR 125 em dia transfere na hora, quero 6.500". Con R$6
# entraba a una busqueda de hasta R$1000 siendo una moto de R$6.500.
BAIT_MAX = 100.0


def fix_bait_prices(listings: list[Listing]) -> int:
    corregidos = 0
    for l in listings:
        desc = l.raw.get("description")
        original = l.raw.get("original_price", l.price)
        if not desc or (original is not None and original > BAIT_MAX):
            continue
        real, currency = parse_price_text(desc, l.raw.get("original_currency", l.currency))
        if real is not None and (original is None or real > original or currency != l.currency):
            l.raw["card_price"] = l.price
            l.raw.pop("original_price", None)
            l.raw.pop("original_currency", None)
            l.price = real
            l.currency = currency or l.currency
            l.raw["price_evidence"] = "description"
            corregidos += 1
    return corregidos
