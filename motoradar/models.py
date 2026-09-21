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


# Un espacio (o \u00a0) solo separa MILLARES en grupos de 3 digitos ("1 800").
# Antes "[.,\u00a0 ]\d+" dejaba que "800 2015" (precio + a\u00f1o pegados en el mismo
# renglon) se uniera en "8002015": una moto de R$800 quedaba fuera de
# presupuesto y se descartaba en silencio. El "." y "," siguen siendo flexibles
# porque parse_price ya resuelve millares vs decimal mas abajo.
_PRICE_RE = re.compile(
    r"(?<!\d)\d{1,3}(?:[\u00a0 ]\d{3})+(?:[.,]\d{1,2})?(?!\d)"
    r"|(?<!\d)\d+(?:[.,]\d+)*(?!\d)")

# El feed cronologico trae el post COMPLETO de cada vecino, no solo de quien
# vende una moto, y eso se persistia entero en SQLite y se exportaba al CSV:
# telefonos, nombres y direcciones de gente que no vende nada. Tambien evita
# que un enlace escrito por un desconocido genere la vista previa de la alerta.
_PHONE_RE = re.compile(
    r"\b(?:\+?\d{1,3}[\s.\-]?)?\(?\d{2,3}\)?[\s.\-]?9\d{4}[\s.\-]?\d{4}\b"
    r"|\b\d{9,13}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+")


def scrub_personal(text: str) -> str:
    """Redacta telefonos, e-mails y enlaces del texto escrito por terceros.

    No afecta a la clasificacion (appraise no usa ninguno de los tres) ni al
    precio: _NOT_PRICE ya descartaba los numeros de 8+ digitos como importe.
    """
    if not text:
        return text
    out = _URL_RE.sub("[enlace]", text)
    out = _EMAIL_RE.sub("[email]", out)
    return _PHONE_RE.sub("[tel]", out)


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
    # OJO: aqui NO se decide "gratis". Un corto-circuito por esa palabra hacia
    # que "R$ 900 . Entrega gratis" valiera 0.0, y con eso una moto de R$900
    # dejaba de ser compra confirmada y pasaba a "precio por confirmar".
    # Una linea que SOLO dice "gratis" la resuelve quien lee la tarjeta.
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
    "$": "UYU", "$u": "UYU", "uyu": "UYU",
}
_AMOUNT = r"(\d+(?:[.]\d{3})+(?:,\d{1,2})?|\d+(?:[,]\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_WITH_CURRENCY = re.compile(
    rf"{_AMOUNT}\s*(mil\s*)?(reais|real|contos?|pesos|pezos|peso|dolares|dolar|usd|uyu)"
    r"|(?:r\$|u\$s|us\$|\$u|uyu|(?<![a-zA-Z])\$)\s*" + _AMOUNT + r"(\s*mil)?",
    re.IGNORECASE)
# En los avisos brasilenos el precio se pide asi: "quero 6.500", "peco 5 mil",
# "fica por 800". Sin estos verbos, un "quero 6.500" quedaba sin precio y la
# moto entraba a una busqueda de hasta R$1000 con el R$6 de la tarjeta.
_WITH_LABEL = re.compile(
    r"(?:valor(?:\s+total)?|pre[cç]o(?:\s+total)?|total|quero|pe[cç]o|"
    # "por" a secas leia "usei por 3 anos" como R$3, y en "comprei por 4 mil,
    # vendo por 900" se quedaba con lo que PAGO el vendedor: la moto de R$900
    # salia por presupuesto. Solo vale pegado a un verbo de venta.
    # El verbo no siempre esta pegado: "vendo minha CG 125 por 900". Se
    # admiten hasta 4 palabras en medio, pero ninguna puede ser "troca":
    # "aceito troca por 200" no es el precio de la moto.
    r"(?:vendo|vende-?se|sai|fica|deixo|repasso|passo|levo)"
    r"(?:\s+(?!troca)\S+){0,4}?\s+por)"
    r"\s*[:.\-]?\s*(?:r\$|u\$s|us\$|\$u|uyu|(?<![a-zA-Z])\$)?\s*" + _AMOUNT + r"(\s*mil)?"
    # y nunca si lo que sigue es una unidad de tiempo
    r"(?!\s*(?:anos?|mes(?:es)?|dias?|semanas?|horas?)\b)",
    re.IGNORECASE)
_NON_TOTAL_PRICE = re.compile(
    rf"\b(?:entrada|sinal|parcela|prestacao|entrega|cuotas?|se[ñn]a)\s*[:.\-]?\s*(?:de\s*[:.\-]?)?\s*"
    rf"(?:r\$|u\$s|us\$|\$u|uyu|(?<![a-zA-Z])\$)?\s*{_AMOUNT}\s*(?:mil\s*)?"
    r"(?:reais|real|contos?|pesos|pezos|peso|dolares|dolar|usd|uyu)?"
    rf"|\b\d+\s*(?:x|cuotas?|parcelas?)\s*[:.\-]?\s*(?:de\s*[:.\-]?)?\s*(?:r\$|u\$s|us\$|\$u|uyu|(?<![a-zA-Z])\$)?\s*{_AMOUNT}\s*(?:mil\s*)?"
    r"(?:reais|real|contos?|pesos|pezos|peso|dolares|dolar|usd|uyu)?"
    rf"|(?<!\bvalor total )(?<!\bvalor )(?<!\bvalor: )(?<!\bvalor\. )(?<!\bvalor\.)"
    rf"(?<!\bpreco )(?<!\bpreco: )(?<!\bpreço )(?<!\bpreço: )(?<!\btotal )(?<!\btotal: )"
    rf"\b{_AMOUNT}\s*(?:mil\s*)?"
    r"(?:(?:reais|real|contos?|pesos|pezos|peso|dolares|dolar|usd|uyu)\s*(?:de\s+)?|de\s+)"
    r"(?:entrada|sinal|parcela|prestacao|entrega|cuotas?|se[ñn]a)\b"
    rf"(?!\s*[:.\-]?\s*(?:de\s*[:.\-]?)?\s*(?:r\$|u\$s|us\$|\$u|uyu|(?<![a-zA-Z])\$)?\s*{_AMOUNT})",
    re.IGNORECASE,
)
_NOT_PRICE = re.compile(
    r"\d[\d.\s]*\s*(km|quil[oô]metros?|kms)"      # kilometraje
    r"|\ban[oa]\s*\.?\s*\d{2,4}"                   # "ano 2018", "ANO.96"
    r"|\b\d{8,}\b",                                 # telefonos
    re.IGNORECASE)


def parse_price_text(text: str, default_currency: str = "BRL"):
    """Devuelve (monto, moneda) leyendo un post escrito a mano, o (None, None)."""
    if not text:
        return None, None
    clean = strip_accents(_NOT_PRICE.sub(" ", text))
    # Entrada, senal y cuotas no representan el precio total de la moto. Se
    # quitan antes de elegir un importe para que "valor 6500, entrada 800"
    # nunca confirme una compra a 800. Si solo hay una cuota, queda incierto.
    clean = _NON_TOTAL_PRICE.sub(" ", clean)

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
                if "u$s" in low or "us$" in low or "usd" in low:
                    cur = "USD"
                elif "r$" in low or "real" in low or "reais" in low:
                    cur = "BRL"
                elif "$u" in low or "peso" in low or "$" in low or "uyu" in low:
                    cur = "UYU"
                else:
                    cur = default_currency
            return value, cur

    m = _WITH_LABEL.search(clean)
    if m:
        value = parse_price(m.group(1))
        if value is not None:
            if m.group(2):          # "peco 5 mil"
                value *= 1000
            low = m.group(0).lower()
            if "u$s" in low or "us$" in low or "usd" in low:
                cur = "USD"
            elif "r$" in low or "real" in low or "reais" in low:
                cur = "BRL"
            elif "$u" in low or "peso" in low or "$" in low or "uyu" in low:
                cur = "UYU"
            else:
                cur = default_currency
            return value, cur
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
        d["alert_status"] = self.raw.get("alert_status", "")
        return d

    SYMBOL = {"BRL": "R$", "UYU": "$U", "USD": "US$"}

    @staticmethod
    def _format_amount(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            val = float(value)
        else:
            parsed = parse_price(value)
            if parsed is not None:
                val = parsed
            else:
                try:
                    val = float(value)
                except (ValueError, TypeError):
                    return str(value)
        if not math.isfinite(val):
            return str(value)
        decimals = 0 if math.isclose(val, round(val), abs_tol=1e-9) else 2
        rendered = f"{val:,.{decimals}f}"
        return rendered.replace(",", "\0").replace(".", ",").replace("\0", ".")

    def pretty_price(self) -> str:
        if self.price is None:
            return "sin precio"
        sym = self.SYMBOL.get(self.currency, self.currency)
        shown = f"{sym} {self._format_amount(self.price)}"
        orig = self.raw.get("original_price")
        if orig is not None:  # convertido: mostrar tambien lo que pide el vendedor
            osym = self.SYMBOL.get(self.raw.get("original_currency", ""), "")
            shown += f" ({osym} {self._format_amount(orig)})"
        return shown


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
