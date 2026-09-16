from __future__ import annotations

from .config import FilterConfig
from .models import Listing, strip_accents


def matches(listing: Listing, f: FilterConfig) -> tuple[bool, str]:
    """(pasa, motivo_del_descarte). El motivo ayuda a afinar la config."""
    haystack = strip_accents(f"{listing.title} {listing.location} {listing.raw}")

    if listing.price is None:
        if not f.allow_no_price:
            return False, "sin precio"
    elif not (f.min_price <= listing.price <= f.max_price):
        return False, f"precio {listing.price:.0f} fuera de rango"

    if f.cities:
        wanted = [strip_accents(c) for c in f.cities]
        location = strip_accents(f"{listing.location} {listing.raw.get('search_region', '')}")
        if not any(c in location for c in wanted):
            return False, "ciudad no coincide"

    if f.include_keywords:
        wanted = [strip_accents(k) for k in f.include_keywords]
        if not any(k in haystack for k in wanted):
            return False, "sin keyword obligatoria"

    for kw in f.exclude_keywords:
        if strip_accents(kw) in haystack:
            return False, f"excluido por '{kw}'"

    return True, ""
