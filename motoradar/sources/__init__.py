from __future__ import annotations

from . import facebook, mercadolivre, olx

REGISTRY = {
    "mercadolivre": mercadolivre.MercadoLivreSource(),
    "olx": olx.OlxSource(),
    "facebook": facebook.FacebookSource(),
}

__all__ = ["REGISTRY"]
