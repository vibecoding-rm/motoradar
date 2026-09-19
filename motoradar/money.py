"""Conversion de monedas. Jaguarao cotiza en reales, Rio Branco en pesos
uruguayos y en dolares, y el mismo presupuesto se ve distinto en cada lado:

    US$ 200  ~=  R$ 1.029  ~=  $U 8.039

Sin esto, comparar un aviso de Rio Branco con uno de Jaguarao no significa nada.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import requests

CACHE = Path.home() / ".motoradar" / "fx.json"
API = "https://open.er-api.com/v6/latest/USD"
MAX_AGE = 12 * 3600  # medio dia: las cotizaciones no se mueven tanto

# Red de seguridad si la API no responde. Editables en config -> fx.rates.
FALLBACK = {"USD": 1.0, "BRL": 5.15, "UYU": 40.2}

# Como escriben los precios de cada lado del puente.
SYMBOLS = [
    (re.compile(r"U\$S|USD|u\$s|US\$", re.IGNORECASE), "USD"),
    (re.compile(r"R\$|BRL", re.IGNORECASE), "BRL"),
    (re.compile(r"\$U|UYU|\$\s*U\b|(?<![A-Za-z])\$(?![A-Za-z])|\bpesos?\b|\bpezos\b", re.IGNORECASE), "UYU"),
]


def _validated_rates(data) -> dict[str, float] | None:
    if not isinstance(data, dict):
        return None
    keep: dict[str, float] = {}
    for code, value in data.items():
        if code not in ("USD", "BRL", "UYU"):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            return None
        keep[code] = value
    return keep or None


def detect_currency(text: str, default: str = "BRL") -> str:
    """"U$S 200" -> USD. Ojo con el orden: "U$S" tiene que ganarle a "$U"."""
    for pattern, code in SYMBOLS:
        if pattern.search(text or ""):
            return code
    return default


class FX:
    def __init__(self, rates: dict[str, float] | None = None, offline: bool = False):
        self.rates = dict(FALLBACK)
        self.source = "fallback"
        if not offline:
            fetched = self._load()
            if fetched:
                self.rates.update(fetched)
        if rates:  # lo que pongas en la config manda sobre todo
            checked = _validated_rates(rates)
            if checked is None:
                raise ValueError("Tasas FX invalidas")
            self.rates.update(checked)
            self.source = "config"

    def _load(self) -> dict[str, float] | None:
        try:
            if CACHE.exists() and time.time() - CACHE.stat().st_mtime < MAX_AGE:
                cached = _validated_rates(json.loads(CACHE.read_text(encoding="utf-8")))
                if cached:
                    self.source = "cache"
                    return cached
        except (OSError, json.JSONDecodeError):
            pass
        try:
            r = requests.get(API, timeout=15)
            if r.ok:
                body = r.json()
                rates = body.get("rates", {}) if isinstance(body, dict) else None
                keep = _validated_rates(rates)
                if not keep:
                    return None
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(keep), encoding="utf-8")
                self.source = "api"
                return keep
        except (requests.RequestException, ValueError, OSError):
            pass
        return None

    def convert(self, amount: float | None, frm: str, to: str) -> float | None:
        """Todo pasa por USD, que es la base que devuelve la API."""
        if amount is None:
            return None
        if frm == to:
            return amount
        r_from, r_to = self.rates.get(frm), self.rates.get(to)
        if not r_from or not r_to or r_from <= 0 or r_to <= 0 or not math.isfinite(r_from) or not math.isfinite(r_to):
            return None
        return amount / r_from * r_to

    def describe(self) -> str:
        pairs = " | ".join(f"1 USD = {self.rates[c]:.2f} {c}"
                           for c in ("BRL", "UYU") if c in self.rates)
        return f"cotizacion [{self.source}]: {pairs}"
