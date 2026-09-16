from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def strip_accents(text: str) -> str:
    """jaguarão -> jaguarao, para comparar sin depender de acentos."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


_PRICE_RE = re.compile(r"(?<!\d)\d+(?:[.,\u00a0 ]\d+)*(?!\d)")


def parse_price(raw: Any) -> float | None:
    """Acepta 'R$ 1.250,00', '1250', 1250.0, None. Devuelve reales como float."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if math.isfinite(value) and value >= 0 else None
    text = str(raw)
    if not text.strip():
        return None
    low = strip_accents(text)
    if "gratis" in low or "free" in low:
        return 0.0
    m = _PRICE_RE.search(text)
    if not m:
        return None
    number = re.sub(r"\s", "", m.group(0))
    if m.start() and text[m.start() - 1] == "-":
        return None
    # Both separators: the last one is decimal. One separator: three digits
    # after it means thousands; one/two digits means decimal.
    if "." in number and "," in number:
        decimal = "." if number.rfind(".") > number.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        number = number.replace(thousands, "").replace(decimal, ".")
    elif "." in number or "," in number:
        separator = "." if "." in number else ","
        chunks = number.split(separator)
        if all(len(c) == 3 for c in chunks[1:]):
            number = "".join(chunks)
        elif len(chunks) == 2 and len(chunks[1]) in (1, 2):
            number = ".".join(chunks)
        else:
            return None
    try:
        value = float(number)
        if re.match(r"\s*(mil\b|k\b)", text[m.end():], re.IGNORECASE):
            value *= 1000
        return value if math.isfinite(value) and value >= 0 else None
    except ValueError:
        return None



# --- precios escritos a mano, como se ven en los grupos -----------------------
# Ahi nadie usa el formato "R$ 1.700". Escriben:
#   "Tenho essa moto pra vender valor 1.700 reais contato 99700..."
#   "Vendo moto gs 17 mil pezos"          <- ademas en pesos uruguayos
#   "VENDO CORSA ANO.96 1.0 8V VALOR.8.500"
#
# La trampa es lo que NO es precio y aparece igual de seguido: "20.000
# quilometros rodados", "ano 2018", y telefonos de 9 digitos. Por eso solo se
# acepta un numero si trae moneda explicita o un "valor/preco" delante.
_CUR_WORDS = {
    "reais": "BRL", "real": "BRL", "conto": "BRL", "contos": "BRL",
    "pesos": "UYU", "pezos": "UYU", "peso": "UYU",
    "dolares": "USD", "dolar": "USD", "usd": "USD",
}
_AMOUNT = r"(\d+(?:[.]\d{3})+(?:,\d{1,2})?|\d+(?:[,]\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_WITH_CURRENCY = re.compile(
    rf"{_AMOUNT}\s*(mil\s*)?(reais|real|contos?|pesos|pezos|peso|dolares|dolar|usd)"
    r"|(?:r\$|u\$s|us\$|\$u)\s*" + _AMOUNT + r"(\s*mil)?",
    re.IGNORECASE)
# En los avisos brasilenos el precio se pide asi: "quero 6.500", "peco 5 mil",
# "fica por 800". Sin estos verbos, un "quero 6.500" quedaba sin precio y la
# moto entraba a una busqueda de hasta R$1000 con el R$6 de la tarjeta.
_WITH_LABEL = re.compile(
    r"(?:valor|pre[cç]o|quero|pe[cç]o|aceito|fica\s+por|sai\s+por|por)"
    r"\s*[:.\-]?\s*(?:r\$|u\$s)?\s*" + _AMOUNT + r"(\s*mil)?",
    re.IGNORECASE)
_NOT_PRICE = re.compile(
    r"\d[\d.\s]*\s*(km|quil[oô]metros?|kms)"      # kilometraje
    r"|an[oa]\s*\.?\s*\d{2,4}"                   # "ano 2018", "ANO.96"
    r"|\d{8,}",                                 # telefonos
    re.IGNORECASE)


def parse_price_text(text: str, default_currency: str = "BRL"):
    """Devuelve (monto, moneda) leyendo un post escrito a mano, o (None, None)."""
    if not text:
        return None, None
    clean = strip_accents(_NOT_PRICE.sub(" ", text))

    m = _WITH_CURRENCY.search(clean)
    if m:
        raw = m.group(1) or m.group(4)
        mil = bool(m.group(2) or m.group(5))
        word = (m.group(3) or "").lower()
        value = parse_price(raw)
        if value is not None:
            if mil:
                value *= 1000
            cur = _CUR_WORDS.get(word)
            if cur is None:
                low = m.group(0).lower()
                cur = ("UYU" if "$u" in low else
                       "USD" if ("u$s" in low or "us$" in low) else "BRL")
            return value, cur

    m = _WITH_LABEL.search(clean)
    if m:
        value = parse_price(m.group(1))
        if value is not None:
            if m.group(2):          # "peco 5 mil"
                value *= 1000
            return value, default_currency
    return None, None

@dataclass
class Listing:
    source: str
    external_id: str
    title: str
    url: str
    price: float | None = None
    currency: str = "BRL"
    location: str = ""
    image: str = ""
    posted_at: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def fingerprint(self) -> str:
        """Hash de titulo+precio para cazar el mismo anuncio republicado."""
        key = f"{strip_accents(self.title)}|{self.price}|{self.currency}|{strip_accents(self.location)}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        d["uid"] = self.uid
        return d

    SYMBOL = {"BRL": "R$", "UYU": "$U", "USD": "US$"}

    def pretty_price(self) -> str:
        if self.price is None:
            return "sin precio"
        sym = self.SYMBOL.get(self.currency, self.currency)
        shown = f"{sym} {self.price:,.0f}".replace(",", ".")
        orig = self.raw.get("original_price")
        if orig:  # convertido: mostrar tambien lo que pide el vendedor
            osym = self.SYMBOL.get(self.raw.get("original_currency", ""), "")
            shown += f" ({osym} {orig:,.0f})".replace(",", ".")
        return shown


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
