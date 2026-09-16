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
    if listing.currency == base or listing.price is None:
        return
    converted = fx.convert(listing.price, listing.currency, base)
    if converted is None:
        listing.raw["fx_error"] = "moneda sin conversion disponible"
        return
    listing.raw["original_price"] = listing.price
    listing.raw["original_currency"] = listing.currency
    listing.raw["fx_source"] = fx.source
    listing.price = round(converted, 2)
    listing.currency = base


def prepare(listings: list[Listing], cfg: Config, fx: FX,
            enrich_details: bool = True) -> SearchResult:
    base = cfg.fx.get("base", "BRL")
    observed = list({l.uid: l for l in listings}.values())
    zone = replace(cfg.filters, max_price=float("inf"), min_price=0,
                   allow_no_price=True)
    intent_words = {"procuro", "busco", "compro", "a pedido"}
    zone = replace(zone, exclude_keywords=[k for k in zone.exclude_keywords
                                           if k.lower() not in intent_words])
    candidates = []
    for l in observed:
        normalize(l, fx, base)
        ok, _ = matches(l, zone)
        if ok and not l.raw.get("fx_error") and (l.price is None or l.price <= cfg.budget):
            candidates.append(l)
    if enrich_details and candidates:
        try:
            enrich(candidates)
        except Exception as exc:
            print(f"  ! detalle incompleto: {type(exc).__name__}")
    fix_bait_prices(candidates)
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
        elif l.price is not None and l.price > cfg.budget:
            reason = "fuera de presupuesto"
        else:
            status = "confirmed" if l.price is not None and not a.bait_price else "unconfirmed"
            l.raw["alert_status"] = status
            l.raw["condition"] = a.condition
            l.raw["doc_risk"] = a.doc_risk
            if status == "confirmed":
                opportunities.append(l)
            else:
                uncertain.append(l)
            continue
        l.raw["alert_status"] = "excluded"
        discarded[reason] = discarded.get(reason, 0) + 1
    opportunities.sort(key=lambda l: l.price)
    return SearchResult(observed, opportunities, uncertain, discarded)
