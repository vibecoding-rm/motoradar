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
from urllib.parse import urlparse

from .models import Listing, parse_price_text, scrub_personal
# El umbral de señuelo vive en appraise: una sola definicion. Duplicado en dos
# modulos, subir uno y no el otro dejaba avisos "confirmados" que
# fix_bait_prices no habia intentado corregir nunca.
from .appraise import BAIT_PRICE as BAIT_MAX, is_bait_price, is_decoy_sequence

# --- OLX ---------------------------------------------------------------------
LD_JSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def enrich_olx(listings: list[Listing], pause: float = 1.0,
               max_items: int = 40, budget_s: float = 120.0) -> int:
    """La descripcion de OLX vive en el ld+json de la pagina de detalle.

    Con techo de items y de tiempo: sin limite, 200 candidatas lentas a 30 s de
    timeout mas 1 s de pausa estiraban la pasada mas de una hora, o sea mas que
    la vida util de la oferta que se busca.
    """
    try:
        from curl_cffi import requests as http
        kw = {"impersonate": "chrome"}
    except ImportError:
        import requests as http
        kw = {}

    session = http.Session()
    done = 0
    deadline = time.time() + budget_s
    leidos = 0
    for listing in listings:
        if not listing.url or listing.raw.get("description"):
            continue
        if leidos >= max_items or time.time() > deadline:
            listing.raw["detail_status"] = "omitido por limite operativo"
            continue
        leidos += 1
        try:
            r = session.get(listing.url, timeout=30, **kw)
            if r.status_code != 200:
                # Antes un 403 en el detalle no dejaba ningun rastro.
                listing.raw["detail_status"] = f"HTTP {r.status_code}"
                continue
            html = r.content.decode("utf-8", "replace")
            m = LD_JSON_RE.search(html)
            if not m:
                continue
            desc = json.loads(m.group(1)).get("description") or ""
            if desc:
                listing.raw["description"] = desc.strip()
                done += 1
        except Exception as exc:
            listing.raw["detail_status"] = f"fallo: {type(exc).__name__}"
            continue  # un aviso ilegible no frena a los demas
        time.sleep(pause)
    return done


# --- Facebook ----------------------------------------------------------------
# La pagina cambia de idioma segun la cuenta, asi que se buscan los marcadores
# en los tres que pueden salir.
START_MARKERS = ("detalles", "detalhes", "details", "descricao", "descripcion", "description")
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
    # Vehicle details can have a long list of attributes before Description.
    # Prefer the actual description header so those attributes cannot consume
    # the extraction limit and hide the seller's price/intent.
    description_markers = ("descricao", "descripcion", "description")
    for i, line in enumerate(lines):
        low = _norm(line)
        if low in description_markers:
            start = i + 1
            break
    for i, line in enumerate(lines):
        if start is not None:
            break
        low = _norm(line)
        if any(low == mark or low.startswith(mark + " ") or low.startswith(mark + ":")
               for mark in START_MARKERS):
            start = i + 1
            break
    if start is None:
        return ""
    out: list[str] = []
    for line in lines[start:start + 12]:
        low = _norm(line)
        if any(mark in low for mark in STOP_MARKERS):
            break
        if any(low == mark or low.startswith(mark + " ")
               for mark in START_MARKERS):
            continue
        out.append(line)
    return scrub_personal(_clean_plus(" ".join(out)).strip())


_DETAIL_PRICE_RE = re.compile(r"(?:R\$|U\$S|US\$|\$U)\s*[\d.,  ]*\d")


def detail_price_text(body: str) -> str:
    """El importe que la PAGINA DE DETALLE muestra como precio.

    Es el dato fiable y el que faltaba: la tarjeta del listado viene truncada o
    con carnada ("R$5" para una CG160 2025), y la descripcion muchas veces no
    repite el precio. Se toma el primero de la cabecera; los de "articulos
    similares" aparecen mucho mas abajo.
    """
    for line in body.splitlines()[:60]:
        match = _DETAIL_PRICE_RE.search(line)
        if match:
            return match.group(0).strip()
    return ""


def _is_facebook_item(url: str) -> bool:
    """Host de Facebook y ruta de articulo, no un `in url` cualquiera.

    Con la comprobacion por substring, un href ajeno con "/marketplace/item/"
    en la ruta se abria en el navegador con el perfil real del usuario.
    """
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    return (parts.scheme == "https"
            and (host == "facebook.com" or host.endswith(".facebook.com"))
            and "/marketplace/item/" in parts.path)


def enrich_facebook(listings: list[Listing], pause: float = 2.5,
                    max_items: int | None = None,
                    timeout_ms: int = 45000) -> int:
    """Una sola sesion de navegador para todas las candidatas de Facebook."""
    from .sources.facebook import (PROFILE_DIR, _close_runtime, _launch,
                                   _settle, _validate_navigation)

    pending = [l for l in listings
               if _is_facebook_item(l.url) and not l.raw.get("description")]
    if not pending or not PROFILE_DIR.exists():
        return 0

    # Primero lo que la lectura PUEDE corregir: carnada y avisos sin precio.
    # Al revés (precios plausibles primero) el cupo de `detail_limit` se gastaba
    # entero en avisos cuyo precio ya era correcto, y las tarjetas a R$0/R$5
    # —las únicas cuyo precio está mal— salían sin leer a Telegram.
    pending.sort(key=lambda l: (0 if l.price is not None and l.price <= BAIT_MAX
                                else 1 if l.price is None else 2))
    if max_items is not None and len(pending) > max_items:
        for listing in pending[max_items:]:
            listing.raw["detail_status"] = "omitido por limite operativo"
        pending = pending[:max_items]

    pw, ctx = _launch(headless=True)
    done = 0
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for index, listing in enumerate(pending, 1):
            print(f"   facebook: detalle {index}/{len(pending)}", flush=True)
            try:
                page.goto(listing.url, timeout=timeout_ms, wait_until="domcontentloaded")
                # Una sesion que muere a mitad del enriquecido era invisible:
                # la pagina de login devuelve "" igual que un detalle no
                # reconocido. Asi el vigilante puede pausar la fuente.
                _validate_navigation(page)
                _settle(page, 1.5)
                body = page.evaluate("() => document.body.innerText")
                desc = extract_description(body)
                precio = detail_price_text(body)
                if precio:
                    listing.raw["detail_price_text"] = precio
                listing.raw["detail_status"] = (
                    "leido" if desc else
                    "sin descripcion; precio leido del detalle" if precio else
                    "pagina no reconocida: sin descripcion ni precio")
                if desc:
                    listing.raw["description"] = desc
                if desc or precio:
                    done += 1
            except Exception as exc:
                listing.raw["detail_status"] = f"fallo: {type(exc).__name__}"
                continue
            time.sleep(pause)
    finally:
        _close_runtime(pw, ctx)
    return done


def enrich(listings: list[Listing], facebook_opts: dict | None = None) -> dict[str, int]:
    olx = [l for l in listings if l.source == "olx"]
    fb = [l for l in listings if l.source == "facebook"]
    opts = facebook_opts or {}
    return {
        "olx": enrich_olx(olx),
        "facebook": enrich_facebook(
            fb,
            pause=float(opts.get("detail_pause", 2.5)),
            max_items=int(opts["detail_limit"]) if opts.get("detail_limit") is not None else None,
            timeout_ms=int(opts.get("detail_timeout_ms", 45000)),
        ),
    }




def fix_bait_prices(listings: list[Listing], fx: FX | None = None, base: str = "BRL") -> int:
    if fx is None:
        from .money import FX
        fx = FX(offline=True)
    corregidos = 0
    for l in listings:
        card_base = l.price
        original = l.raw.get("original_price", l.price)
        original_currency = l.raw.get("original_currency", l.currency)
        # El umbral de magnitud (100 BRL) vive en moneda base (card_base).
        # Para original solo se comprueban secuencias señuelo (1234, 1111)
        # porque comparar 50 USD con 100 descartaria precios legitimos y
        # comparar 500 UYU con 100 impediria leer el detalle.
        if card_base is not None and not (is_bait_price(card_base) or is_decoy_sequence(original)):
            continue
        # El precio del detalle gana a la descripcion: es un campo, no una
        # frase, y no depende de que el vendedor repita el numero en el texto.
        evidencia = "detail_price" if l.raw.get("detail_price_text") else "description"
        texto = l.raw.get("detail_price_text") or l.raw.get("description") or ""
        if not texto:
            continue
        real, currency = parse_price_text(texto, original_currency)
        # Un importe corregido por debajo del umbral de señuelo tampoco es un
        # precio: si el detalle vuelve a decir "R$ 0", escribirlo reintroducia
        # el fantasma que la tarjeta ya habia descartado, y el aviso pasaba de
        # "sin precio" a "R$ 0" con toda la confianza de un dato leido.
        if real is None:
            continue
        cur = currency or original_currency or base
        real_base = fx.convert(real, cur, base) if cur != base else real
        if real_base is None:
            real_base = real
        if is_bait_price(real_base) or is_decoy_sequence(real):
            continue
        if (original is None or is_bait_price(card_base) or is_decoy_sequence(original)
                or real > original or currency != original_currency):
            l.raw["card_price"] = original
            l.raw["card_currency"] = original_currency
            l.raw.pop("original_price", None)
            l.raw.pop("original_currency", None)
            l.raw.pop("fx_source", None)
            l.raw.pop("fx_error", None)
            l.price = real
            l.currency = currency or original_currency
            l.raw["price_evidence"] = evidencia
            corregidos += 1
    return corregidos
