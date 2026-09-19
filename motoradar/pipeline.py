"""Selection shared by the CLI and watcher. Papers and margin never filter."""
from __future__ import annotations

from dataclasses import dataclass, replace

from .appraise import appraise
from .config import Config
from .enrich import enrich, fix_bait_prices
from .filters import matches
from .models import Listing
from .money import FX


@dataclass
class SearchResult:
    observed: list[Listing]
    opportunities: list[Listing]
    unconfirmed: list[Listing]
    discarded: dict[str, int]


def normalize(listing: Listing, fx: FX, base: str) -> None:
    listing.raw.pop("fx_error", None)
    original_price = listing.raw.get("original_price", listing.price)
    original_currency = listing.raw.get("original_currency", listing.currency)
    if original_price is None:
        return
    if original_currency == base:
        listing.price = original_price
        listing.currency = base
        listing.raw.pop("original_price", None)
        listing.raw.pop("original_currency", None)
        listing.raw.pop("fx_source", None)
        return
    converted = fx.convert(original_price, original_currency, base)
    if converted is None:
        listing.price = original_price
        listing.currency = original_currency
        listing.raw["fx_error"] = "moneda sin conversion disponible"
        return
    listing.raw["original_price"] = original_price
    listing.raw["original_currency"] = original_currency
    listing.raw["fx_source"] = fx.source
    listing.price = round(converted, 2)
    listing.currency = base


def prepare(listings: list[Listing], cfg: Config, fx: FX,
            enrich_details: bool = True) -> SearchResult:
    base = cfg.fx.get("base", "BRL")
    # setdefault y no dict-comprehension: conservar el PRIMER avistamiento.
    # Quedandose con el ultimo, en `group_mode: both` el buscador se atribuia
    # el hallazgo y el feed desaparecia de la linea de origenes, que es justo
    # el dato con el que se decide si el feed paga el riesgo de cuenta.
    por_uid: dict[str, Listing] = {}
    for l in listings:
        por_uid.setdefault(l.uid, l)
    observed = list(por_uid.values())
    zone = replace(cfg.filters, max_price=float("inf"), min_price=0,
                   allow_no_price=True)
    intent_words = {"procuro", "busco", "compro", "a pedido"}
    zone = replace(zone, exclude_keywords=[k for k in zone.exclude_keywords
                                           if k.lower() not in intent_words])
    candidates = []
    for l in observed:
        normalize(l, fx, base)
        ok, _ = matches(l, zone)
        a = appraise(l)
        if ok and not l.raw.get("fx_error") and (l.price is None or l.price <= cfg.budget or a.bait_price):
            candidates.append(l)
    if enrich_details and candidates:
        try:
            enrich(candidates, cfg.source_opts("facebook"))
        except Exception as exc:
            print(f"  ! detalle incompleto: {type(exc).__name__}")
    fix_bait_prices(candidates, fx, base)
    opportunities, uncertain = [], []
    discarded: dict[str, int] = {}
    for l in observed:
        normalize(l, fx, base)
        ok, reason = matches(l, zone)
        a = appraise(l)
        if not ok:
            pass
        elif l.raw.get("fx_error"):
            reason = "moneda sin conversion"
        elif not a.is_buyable:
            reason = "pedido/publicidad/repuesto/desconocido"
        elif not a.bait_price and l.price is not None and l.price > cfg.budget:
            reason = "fuera de presupuesto"
        else:
            status = "confirmed" if l.price is not None and not a.bait_price else "unconfirmed"
            l.raw.pop("excluded_reason", None)
            l.raw["alert_status"] = status
            l.raw["condition"] = a.condition
            l.raw["doc_risk"] = a.doc_risk
            if status == "confirmed":
                opportunities.append(l)
            else:
                uncertain.append(l)
            continue
        l.raw["alert_status"] = "excluded"
        l.raw["excluded_reason"] = reason
        discarded[reason] = discarded.get(reason, 0) + 1
    opportunities.sort(key=lambda l: l.price)
    return SearchResult(observed, opportunities, uncertain, discarded)
