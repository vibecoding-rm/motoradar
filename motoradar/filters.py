from __future__ import annotations

import re

from .config import FilterConfig
from .models import Listing, strip_accents

# Negative city exclusion & Rio Branco disambiguation (FEAT-FB-GEO-07, BUG-FB-GEO-02, BUG-FB-GEO-03)
_NEGATIVE_LOCATIONS_RE = re.compile(
    r"\b(?:"
    r"pelotas|"
    r"bage|"
    r"melo|"
    r"treinta\s+y\s+tres|"
    r"porto\s+alegre|"
    r"poa|"
    r"arroio\s+grande|"
    r"rio\s+grande(?!\s+do\s+sul\b)|"
    r"herval|"
    r"erval|"
    r"pedras\s+altas|"
    r"candiota|"
    r"pinheiro\s+machado|"
    r"novo\s+hamburgo|"
    r"canoas|"
    r"sao\s+leopoldo|"
    r"alvorada|"
    r"gravatai|"
    r"viamao|"
    r"caxias\s+do\s+sul|"
    r"santa\s+maria|"
    r"passo\s+fundo|"
    r"uruguaiana|"
    r"santana\s+do\s+livramento|"
    r"dom\s+pedrito|"
    r"chui|"
    r"chuy|"
    r"santa\s+vitoria\s+do\s+palmar|"
    r"sao\s+jose\s+do\s+norte|"
    r"piratini|"
    r"cangucu|"
    r"morro\s+redondo|"
    r"capao\s+do\s+leao|"
    r"acegua|"
    r"acre|"
    r"bairro\s+rio\s+branco|"
    r"rio\s+branco\s*[/, -]*\(?\s*(?:porto\s+alegre|novo\s+hamburgo|canoas|acre|ac|rs|brasil|brazil)(?:\b|\))|"
    r"(?:porto\s+alegre|novo\s+hamburgo|canoas|acre|ac)\s*[/, -]+\s*rio\s+branco|"
    r"(?<!jaguarao,\s)(?<!jaguarao\s)(?<!jaguarao/\s)(?<!jaguarao/)(?<!jaguarao\s-\s)rs\s*[/, -]+\s*rio\s+branco"
    r")\b",
    re.IGNORECASE,
)


def is_non_target_location(text: str) -> bool:
    """Devuelve True si el texto menciona explícitamente una ciudad no objetivo."""
    norm = strip_accents(text or "").lower()
    return bool(_NEGATIVE_LOCATIONS_RE.search(norm))


def detect_location_cue(text: str) -> tuple[str | None, bool | None]:
    """Detecta mención explícita de ubicación en texto libre.
    Devuelve (nombre_ciudad, es_objetivo):
      - (ciudad_no_objetivo, False) si menciona Pelotas, Bagé, Melo, etc.
      - (ciudad_objetivo, True) si menciona Jaguarão o Río Branco.
      - (None, None) si no menciona ninguna ciudad conocida.
    """
    if not text:
        return None, None
    norm = strip_accents(text).lower()
    neg_m = _NEGATIVE_LOCATIONS_RE.search(norm)
    if neg_m:
        return neg_m.group(0).strip(), False
    if re.search(r"\bjaguarao\b", norm):
        return "jaguarao", True
    if re.search(r"\brio\s+branco\b", norm):
        return "rio branco", True
    return None, None


def matches(listing: Listing, f: FilterConfig) -> tuple[bool, str]:
    """(pasa, motivo_del_descarte). El motivo ayuda a afinar la config."""
    # Only seller-written text: raw also holds category/query metadata such as
    # "autos-e-pecas/motos", which made exclude "pecas" drop every motorcycle.
    description = listing.raw.get("description", "")
    card_text = listing.raw.get("card_text", "")
    haystack = strip_accents(f"{listing.title} {listing.location} {description} {card_text}")

    if f.cities:
        # 1. Negative city exclusion & Rio Branco disambiguation (FEAT-FB-GEO-07, BUG-FB-GEO-02, BUG-FB-GEO-03)
        loc_haystack = strip_accents(f"{listing.location} {listing.title} {description} {card_text}").lower()
        if _NEGATIVE_LOCATIONS_RE.search(loc_haystack):
            return False, "ciudad no coincide"

        # 2. Strict positive geographic matching
        wanted = [strip_accents(c).lower() for c in f.cities]
        search_region = strip_accents(listing.raw.get("search_region", "")).lower()
        location_field = strip_accents(listing.location).lower()

        matched_city = False
        is_group = listing.raw.get("kind") == "group" or location_field.strip().startswith("grupo ")

        if not is_group and location_field.strip():
            # Listing with explicit location: city in title cannot override it.
            for c in wanted:
                c_re = re.compile(rf"\b{re.escape(c)}\b", re.IGNORECASE)
                if c_re.search(location_field):
                    matched_city = True
                    break
        else:
            # Group post or listing relying on search_region / text:
            for c in wanted:
                c_re = re.compile(rf"\b{re.escape(c)}\b", re.IGNORECASE)
                if c_re.search(search_region) or c_re.search(loc_haystack):
                    matched_city = True
                    break

        if not matched_city:
            # BUG-FB-GEO-05: Handle Marketplace cards missing a location line safely without dropping
            # them prematurely if they can be verified or enriched.
            if listing.raw.get("kind") == "marketplace" and not listing.location:
                pass
            else:
                return False, "ciudad no coincide"

    if listing.price is None:
        if not f.allow_no_price:
            return False, "sin precio"
    elif not (f.min_price <= listing.price <= f.max_price):
        return False, f"precio {listing.price:.0f} fuera de rango"

    if f.include_keywords:
        wanted = [strip_accents(k) for k in f.include_keywords]
        if not any(k in haystack for k in wanted):
            return False, "sin keyword obligatoria"

    for kw in f.exclude_keywords:
        if strip_accents(kw) in haystack:
            return False, f"excluido por '{kw}'"

    return True, ""
