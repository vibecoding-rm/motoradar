"""Mercado Livre: la unica fuente de las tres con API oficial documentada.

Desde 2024 los endpoints de busqueda piden Bearer token. Se obtiene con
client_credentials desde una app creada en developers.mercadolivre.com.br
(gratis). Sin credenciales se intenta anonimo y se avisa si devuelve 401.
"""
from __future__ import annotations

import os
from collections.abc import Iterable

import requests

from ..config import Config
from ..models import Listing, parse_price
from .base import BaseSource, SourceError

API = "https://api.mercadolibre.com"
TOKEN_URL = f"{API}/oauth/token"
# MLB = Brasil, MLU = Uruguay. Rio Branco esta cruzando el puente, asi que
# para este negocio las dos puntas cuentan. Misma API, mismas credenciales.
SITES = {"MLB": "BRL", "MLU": "UYU"}


def _app_token(opts: dict) -> str | None:
    client_id = os.getenv("MELI_CLIENT_ID") or opts.get("client_id")
    client_secret = os.getenv("MELI_CLIENT_SECRET") or opts.get("client_secret")
    if not (client_id and client_secret):
        return None
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Accept": "application/json"},
        timeout=20,
    )
    if r.status_code != 200:
        raise SourceError(f"MELI token {r.status_code}: {r.text[:200]}")
    return r.json().get("access_token")


class MercadoLivreSource(BaseSource):
    name = "mercadolivre"

    def fetch(self, cfg: Config, opts: dict) -> Iterable[Listing]:
        token = _app_token(opts)
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        limit = int(opts.get("limit", 50))
        state = opts.get("state_id", "TUxCUFJTZTM4ZA")  # Rio Grande do Sul
        sites = opts.get("sites") or ["MLB"]

        self.reset()
        for site in sites:
            self.expected_origins.append(f"{self.name}/{site}")
        for site in sites:
            yield from self._search(site, headers, limit, state, cfg)

    def _search(self, site: str, headers: dict, limit: int, state: str,
                cfg: Config) -> Iterable[Listing]:
        currency = SITES.get(site, "BRL")
        for query in cfg.filters.queries:
            params = {"q": query, "limit": limit}
            if site == "MLB":  # el id de estado es especifico de Brasil
                params["state"] = state
                params["price"] = (f"{cfg.filters.min_price:.0f}-"
                                   f"{cfg.filters.max_price:.0f}")
            r = requests.get(f"{API}/sites/{site}/search", params=params,
                             headers=headers, timeout=25)
            if r.status_code in (401, 403):
                raise SourceError(
                    "Mercado Livre exige token. Crea una app en "
                    "developers.mercadolivre.com.br y pone client_id/client_secret "
                    "en config.yaml (o MELI_CLIENT_ID / MELI_CLIENT_SECRET)."
                )
            if r.status_code != 200:
                # Un no-200 en UNA consulta no puede cancelar las demas ni el
                # otro sitio: OLX ya saltea la pagina y sigue.
                self.note_failed_page(f"MELI {site}/{query} HTTP {r.status_code}")
                continue

            cuerpo = r.json() if isinstance(r.json(), dict) else {}
            resultados = cuerpo.get("results")
            # Cero resultados exige evidencia: `paging.total == 0` es la prueba
            # de que el sitio no tiene nada. Sin `paging`, la API cambio y esto
            # NO es una tarde tranquila. Antes devolvia "ok, 0 observados", o
            # sea "hoy no hay motos baratas", ante cualquier cambio de contrato.
            paging = cuerpo.get("paging")
            self.require_empty_evidence(
                len(resultados or []),
                isinstance(paging, dict) and paging.get("total") == 0,
                f"MELI {site}/{query}",
                f"claves={sorted(cuerpo)[:6]}")

            for item in (resultados or []):
                addr = item.get("address") or {}
                city = addr.get("city_name") or ""
                yield Listing(
                    source=self.name,
                    external_id=f"{site}-{item.get('id')}",
                    title=item.get("title", ""),
                    url=item.get("permalink", ""),
                    price=parse_price(item.get("price")),
                    currency=item.get("currency_id") or currency,
                    location=", ".join(x for x in [city, addr.get("state_name", "")] if x),
                    image=item.get("thumbnail", ""),
                    # start_time es el alta; stop_time es el VENCIMIENTO del
                    # anuncio, que no dice nada sobre lo nuevo que es.
                    posted_at=item.get("start_time") or "",
                    # `condition` de la API usa otro vocabulario ("new"/"used")
                    # que el de appraise ("runner"/"project"/"parts"), y pipeline
                    # escribe esa misma clave: se guarda aparte para no pisarse.
                    raw={"api_condition": item.get("condition"), "city": city,
                         "expires_at": item.get("stop_time") or ""},
                )
