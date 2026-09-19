"""OLX Brasil: sin API publica.

OLX ya NO es Next.js (el viejo truco del `__NEXT_DATA__` murio), asi que se
parsea el markup de las tarjetas, que es estable y semantico:

    <section class="olx-adcard">
      <a data-testid="adcard-link" title="..." href="...-1533801209">
      <span class="olx-adcard__price">R$ 950</span>
      <p class="olx-adcard__location">Porto Alegre, Santa Tereza</p>
      <p class="olx-adcard__date">27 de ago, 18:26</p>

Ademas filtra por fingerprint TLS/HTTP2, no solo por User-Agent: `requests`
se come un 403. curl_cffi impersona a Chrome de verdad.

Sobre la ubicacion (todo comprobado contra el sitio real):
  - El slug de REGION si funciona:
    `estado-rs/regioes-de-pelotas-rio-grande-e-bage` -> 150 avisos, todos del
    sur, 4 de Jaguarao en 3 paginas.
  - El slug de MUNICIPIO no: agregar `/jaguarao` al final hace que OLX lo
    ignore en silencio y devuelva el estado entero (Porto Alegre copando todo).
  - Sin region, Jaguarao aparecia 1 vez cada 400 avisos.
Asi que: region correcta + paginado `&o=N` (50 por pagina) + filtro de ciudad
nuestro sobre el resultado.
"""
from __future__ import annotations

import random
import re
import time
from typing import Iterable
from urllib.parse import urlencode

try:
    from curl_cffi import requests as http
    IMPERSONATE = {"impersonate": "chrome"}
except ImportError:  # pragma: no cover
    import requests as http
    IMPERSONATE = {}

from ..config import Config
from ..models import Listing, parse_price
from .base import BaseSource, SourceError

BASE = "https://www.olx.com.br"

# Para un flipper las dos categorias importan y son MUY distintas:
#   motos              -> motos completas (proyectos y andando)
#   pecas-e-acessorios -> sucatas y doadores. Aca esta la mercaderia barata:
#                         "Sucata Honda CB 300R 2012 em pecas", "Sucata Biz 125".
CATEGORIES = ["autos-e-pecas/motos", "autos-e-pecas/pecas-e-acessorios"]

CARD_RE = re.compile(r'<section[^>]*class="[^"]*olx-adcard[^"]*"[^>]*>(.*?)</section>', re.S)
LINK_RE = re.compile(r'<a[^>]*data-testid="adcard-link"[^>]*href="([^"]+)"', re.S)
TITLE_RE = re.compile(r'class="[^"]*olx-adcard__title[^"]*"[^>]*>(.*?)</h2>', re.S)
IMG_RE = re.compile(r'<img[^>]*src="(https://img\.olx\.com\.br/[^"]+)"', re.S)
ID_RE = re.compile(r'-(\d{6,})(?:$|[?#])')
TAG_RE = re.compile(r"<[^>]+>")
BLOCK_RE = re.compile(
    r"verify you are human|captcha|access denied|acesso negado|sou humano|"
    r"atividade suspeita|challenge-platform",
    re.IGNORECASE,
)
EMPTY_RE = re.compile(
    r"nenhum an[uú]ncio encontrado|n[aã]o encontramos resultados|"
    r"sem resultados|data-testid=[\"'][^\"']*(?:empty|no-results)",
    re.IGNORECASE,
)


def _field(card: str, name: str) -> str:
    m = re.search(
        rf'class="[^"]*olx-adcard__{name}[^"]*"[^>]*>(.*?)</(?:span|p|h2|h3|div)>',
        card, re.S,
    )
    return _text(m.group(1)) if m else ""


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub("", fragment)).strip()


class OlxSource(BaseSource):
    name = "olx"

    def fetch(self, cfg: Config, opts: dict) -> Iterable[Listing]:
        state = opts.get("state_path", "estado-rs/regioes-de-pelotas-rio-grande-e-bage")
        pages = max(1, int(opts.get("pages", 8)))
        categories = opts.get("categories") or CATEGORIES
        session = http.Session()
        session.headers.update({"Accept-Language": "pt-BR,pt;q=0.9"})
        self.reset()
        successful_pages = 0

        try:
            seen: set[str] = set()
            for category in categories:
                for query in cfg.filters.queries:
                    for page in range(1, pages + 1):
                        try:
                            got = list(self._page(session, cfg, category, state,
                                                  query, page, seen))
                        except SourceError as exc:
                            self.note_failed_page(f"OLX pagina {page}")
                            # OLX tira 502/503 sueltos. Perder una pagina es
                            # aceptable; perder la corrida entera no.
                            print(f"   (salteo pagina {page}: {exc})")
                            continue
                        successful_pages += 1
                        for listing in got:
                            if listing.external_id not in seen:
                                seen.add(listing.external_id)
                                yield listing
                        if not got:
                            break  # sin resultados: no tiene sentido seguir paginando
            if self.failed_pages and not successful_pages:
                raise SourceError(f"OLX: fallaron las {self.failed_pages} paginas consultadas")
        finally:
            session.close()

    def _page(self, session, cfg: Config, category: str, state: str,
              query: str, page: int, seen: set[str]) -> Iterable[Listing]:
            params = {
                "q": query,
                "pe": int(cfg.filters.max_price),   # preco maximo
                "ps": int(cfg.filters.min_price),   # preco minimo
                "sf": 1,
                "o": page,
            }
            url = f"{BASE}/{category}/{state}?{urlencode(params)}"
            r = session.get(url, timeout=30, **IMPERSONATE)
            if r.status_code == 403:
                hint = ("" if IMPERSONATE else
                        " Instala curl_cffi (pip install curl_cffi) para impersonar "
                        "el fingerprint TLS de Chrome.")
                raise SourceError(f"OLX devolvio 403 (anti-bot).{hint}")
            if r.status_code in (429, 502, 503, 504):
                # Con backoff y jitter, no inmediato: un 429 reintentado en el
                # mismo milisegundo devuelve 429 otra vez y la pagina se saltea.
                # Se respeta Retry-After si viene.
                for espera in (1.0, 3.0, 8.0):
                    try:
                        pedido = float(r.headers.get("Retry-After", 0) or 0)
                    except (TypeError, ValueError):
                        pedido = 0.0
                    time.sleep(max(espera, min(pedido, 30.0))
                               + random.uniform(0, 0.5))
                    r = session.get(url, timeout=30, **IMPERSONATE)
                    if r.status_code not in (429, 502, 503, 504):
                        break
            if r.status_code != 200:
                raise SourceError(f"OLX {r.status_code} en {url}")

            # curl_cffi adivina mal el charset y rompe los acentos.
            html = r.content.decode("utf-8", "replace")
            cards = CARD_RE.findall(html)
            if not cards:
                if BLOCK_RE.search(html):
                    raise SourceError("OLX: pagina bloqueada/desafio anti-bot")
                if "olx-adcard" in html:
                    raise SourceError("OLX: cambio el markup de las tarjetas")
                if EMPTY_RE.search(html):
                    return  # vacio explicitamente reconocido por OLX
                raise SourceError("OLX: pagina 200 sin estructura de resultados reconocible")

            for card in cards:
                link = LINK_RE.search(card)
                if not link:
                    continue
                href = link.group(1)
                ad_id = ID_RE.search(href)
                ad_id = ad_id.group(1) if ad_id else href.rsplit("/", 1)[-1]
                title = TITLE_RE.search(card)
                img = IMG_RE.search(card)
                yield Listing(
                    source=self.name,
                    external_id=ad_id,
                    title=_text(title.group(1)) if title else "",
                    url=href,
                    price=parse_price(_field(card, "price")),
                    location=_field(card, "location"),
                    image=img.group(1) if img else "",
                    posted_at=_field(card, "date"),
                    raw={"query": query, "page": page, "category": category},
                )
