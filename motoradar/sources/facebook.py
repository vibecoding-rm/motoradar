"""Facebook: NO hay API.

La Graph API nunca expuso Marketplace, y el acceso al feed de grupos para
apps se cerro en 2020. La unica via es manejar tu propia sesion con un
navegador real (Playwright + perfil persistente). Automatizar la navegacion
va contra los Terminos de Meta: el riesgo es un checkpoint o bloqueo de la
cuenta. Por eso el scroll es lento y el volumen chico por defecto.

En un pueblo como Jaguarao los grupos de compra/venta rinden mucho mas que
Marketplace: configuralos en `groups`.
"""
from __future__ import annotations

import time
import hashlib
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

from ..config import Config
from ..models import Listing, parse_price, parse_price_text, strip_accents
from ..money import detect_currency
from .base import SourceError

PROFILE_DIR = Path.home() / ".motoradar" / "fb-profile"
ITEM_JS = """
() => Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]'))
  .map(a => ({
    href: a.href.split('?')[0],
    text: (a.innerText || '').trim(),
    img: (a.querySelector('img') || {}).src || ''
  }))
  .filter(x => x.text)
"""
POST_JS = r"""
() => {
  const feed = document.querySelector('[role="feed"]');
  if (!feed) return [];
  return Array.from(feed.children).map(k => {
    // El texto del post vive en div[dir="auto"]; innerText del contenedor
    // devuelve "Facebook" repetido (el alt de los iconos), no sirve.
    // El cuerpo del post puede venir partido en varios div[dir=auto] (y tras
    // expandir "Ver mas" aparecen mas). Se devuelven todos: el precio suele
    // estar en un bloque distinto al del texto largo.
    const vistos = new Set();
    const blocks = Array.from(k.querySelectorAll('div[dir="auto"]'))
      .map(d => (d.textContent || '').trim())
      .filter(t => t.length > 8 && !vistos.has(t) && vistos.add(t));
    blocks.sort((a, b) => b.length - a.length);
    const link = Array.from(k.querySelectorAll('a[href]'))
      .map(a => a.href)
      .find(h => /\/posts\/|multi_permalinks|permalink/.test(h)) || '';
    const img = (k.querySelector('img[src*="scontent"]') || {}).src || '';
    return {text: blocks[0] || '', blocks: blocks, href: link, img: img};
  }).filter(x => x.text);
}
"""


def _launch(headless: bool):
    # patchright es un fork stealth de Playwright; Facebook detecta al vanilla
    # (fingerprint de CDP) y te manda a checkpoint. Si esta instalado, gana.
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise SourceError(
                "Falta el navegador: pip install patchright && "
                "patchright install chromium"
            ) from exc
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        locale="pt-BR",
    )
    return pw, ctx


def login(timeout_min: int = 10) -> None:
    """Abre el navegador para que inicies sesion VOS, a mano.

    El proyecto nunca toca ni guarda tu contraseña: lo unico que queda en
    disco es la cookie de sesion, dentro del perfil de Chromium.

    Aprovecha y deja fijada la ubicacion de Marketplace (Jaguarao + radio):
    el perfil persistente la recuerda en las corridas siguientes.
    """
    pw, ctx = _launch(headless=False)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.facebook.com/", timeout=60000)
        print("\n  1. Inicia sesion en la ventana que se abrio.")
        print("  2. Entra a Marketplace y fija la ubicacion en Jaguarao.")
        print("  3. Esta terminal avisa sola cuando la sesion quedo guardada.\n")

        deadline = time.time() + timeout_min * 60
        while time.time() < deadline:
            if _logged_in(page):
                print("  Sesion iniciada y guardada.")
                time.sleep(2)  # que termine de escribir las cookies
                return
            time.sleep(3)
        print("  Se acabo el tiempo sin detectar la sesion.")
    finally:
        ctx.close()
        pw.stop()
        print(f"  Perfil: {PROFILE_DIR}")


def _logged_in(page) -> bool:
    """c_user es la cookie que Facebook pone recien cuando hay sesion real."""
    try:
        return any(c.get("name") == "c_user" for c in page.context.cookies())
    except Exception:
        return False


def session_ready() -> bool:
    """Chequeo rapido y sin ruido, para `motoradar status`."""
    if not PROFILE_DIR.exists():
        return False
    pw, ctx = _launch(headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.facebook.com/", timeout=45000,
                  wait_until="domcontentloaded")
        return _logged_in(page)
    finally:
        ctx.close()
        pw.stop()


def _scroll(page, rounds: int, pause: float) -> None:
    for _ in range(rounds):
        page.mouse.wheel(0, 2200)
        time.sleep(pause)
    _settle(page)


def _render_feed(page, max_items: int = 14, budget_s: float = 35.0,
                 pause: float = 0.6) -> int:
    """Materializa los posts del feed, con techo de tiempo.

    Los posts se renderizan recien cuando entran al viewport, y mouse.wheel no
    dispara ese observer en headless: el feed queda con los hijos vacios. Hay
    que traerlos a la vista uno por uno.

    Eso es caro, asi que va acotado: sin techo, 8 grupos x 2 queries se comian
    mas de 15 minutos y la corrida moria por timeout sin devolver nada.
    """
    deadline = time.time() + budget_s
    done = 0
    while done < max_items and time.time() < deadline:
        try:
            total = page.evaluate(
                "() => { const f = document.querySelector('[role=\"feed\"]');"
                " return f ? f.children.length : 0; }")
        except Exception:
            return done
        target = min(total, max_items)
        if target <= done:
            page.mouse.wheel(0, 2500)
            time.sleep(1.5)
            try:
                nuevo = page.evaluate(
                    "() => { const f = document.querySelector('[role=\"feed\"]');"
                    " return f ? f.children.length : 0; }")
            except Exception:
                return done
            if nuevo <= total:
                break          # el feed no crece mas
            continue
        for i in range(done, target):
            if time.time() > deadline:
                break
            try:
                page.evaluate(
                    "(i) => { const f = document.querySelector('[role=\"feed\"]');"
                    " if (f && f.children[i]) f.children[i]"
                    ".scrollIntoView({block: 'center'}); }", i)
            except Exception:
                return done
            time.sleep(pause)
        done = target
    return done


EXPAND_JS = """
() => {
  // Los posts largos vienen cortados con "Ver mas": el precio suele quedar
  // justo en la parte oculta, asi que sin expandir casi todo sale sin precio.
  const etiquetas = ['ver mas', 'ver mais', 'see more', 'ver más'];
  let clics = 0;
  document.querySelectorAll('div[role="button"], span[role="button"]').forEach(b => {
    const t = (b.textContent || '').trim().toLowerCase();
    if (etiquetas.includes(t)) { b.click(); clics++; }
  });
  return clics;
}
"""


def _expand_posts(page, rounds: int = 2, pause: float = 1.2) -> int:
    total = 0
    for _ in range(rounds):
        try:
            n = page.evaluate(EXPAND_JS)
        except Exception:
            break
        if not n:
            break
        total += n
        time.sleep(pause)
    return total


def _settle(page, extra: float = 2.0) -> None:
    """Esperar a que las tarjetas terminen de renderizar ANTES de leerlas.

    Sin esto se leen a medio pintar y los precios salen truncados: una corrida
    devolvio "R$5" para una Honda CG160 2022 y "R$55" para otra. Con la lectura
    asentada, esos mismos avisos dan el precio real. Un precio mal leido no es
    ruido: rompe el calculo de margen entero.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass  # networkidle no siempre llega en un feed infinito
    time.sleep(extra)


def _first_price(text: str) -> float | None:
    for line in text.splitlines():
        if any(s in line.upper() for s in ("R$", "US$", "U$S", "$U", "USD", "UYU")) or strip_accents(line).startswith("gratis"):
            price = parse_price(line)
            if price is not None:
                return price
    return None


def _parse_card(text: str) -> tuple[str, str]:
    """Las tarjetas de Marketplace vienen siempre con la misma forma:

        ['R$1.800', 'Moto trilha', 'Arroio Grande, RS']
        ['R$3.000', 'R$3.700', '2014 Yamaha outro', 'Jaguarao, RS']   <- rebaja

    O sea: precio(s), titulo, ubicacion. La ULTIMA linea es la ubicacion y la
    anteultima el titulo. Antes tomaba la linea mas larga que no fuera precio,
    y en los avisos de titulo corto se quedaba con la ciudad.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 3:
        return lines[-2][:200], lines[-1]
    if len(lines) == 2:
        return lines[1][:200], ""
    return (lines[0][:200] if lines else ""), ""


class FacebookSource:
    name = "facebook"

    def fetch(self, cfg: Config, opts: dict) -> Iterable[Listing]:
        if not PROFILE_DIR.exists():
            raise SourceError("Sin sesion de Facebook. Corre: python -m motoradar login")

        headless = bool(opts.get("headless", True))
        rounds = int(opts.get("scroll_rounds", 6))
        pause = float(opts.get("scroll_pause", 1.6))
        groups = opts.get("groups") or []

        pw, ctx = _launch(headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if opts.get("marketplace", True):
                yield from self._marketplace(page, cfg, opts, rounds, pause)
            # Los grupos son chicos: no tiene sentido pasarles las 6 queries
            # de OLX. 8 grupos x 6 queries son 48 cargas de pagina y el riesgo
            # de checkpoint sube mucho. Con 2 alcanza.
            gq = opts.get("group_queries") or cfg.filters.queries[:2]
            for group_id in groups:
                yield from self._group(
                    page, cfg, str(group_id), rounds, pause, gq,
                    int(opts.get('group_items', 14)),
                    float(opts.get('group_budget_s', 35)))
        finally:
            ctx.close()
            pw.stop()

    def _marketplace(self, page, cfg: Config, opts: dict, rounds: int, pause: float):
        for query in cfg.filters.queries:
            params = {
                "query": query,
                "maxPrice": int(cfg.filters.max_price),
                "minPrice": int(cfg.filters.min_price),
                "exact": "false",
            }
            if opts.get("radius_km"):
                params["radius"] = int(opts["radius_km"]) * 1000
            url = f"https://www.facebook.com/marketplace/search/?{urlencode(params)}"
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            time.sleep(3)
            if "/login" in page.url:
                raise SourceError("La sesion de Facebook expiro. Corre: python -m motoradar login")
            _scroll(page, rounds, pause)

            for card in page.evaluate(ITEM_JS):
                item_id = card["href"].rstrip("/").split("/")[-1]
                price = _first_price(card["text"])
                title, location = _parse_card(card["text"])
                yield Listing(
                    source=self.name,
                    external_id=item_id,
                    title=title,
                    url=card["href"],
                    price=price,
                    currency=detect_currency(card["text"]),
                    location=location,
                    image=card["img"],
                    raw={"card_text": card["text"], "kind": "marketplace"},
                )

    def _group(self, page, cfg: Config, group_id: str, rounds: int,
               pause: float, queries: list[str] | None = None,
               opts_items: int = 14, opts_budget: float = 35.0):
        for query in (queries or cfg.filters.queries):
            url = f"https://www.facebook.com/groups/{group_id}/search/?{urlencode({'q': query})}"
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            _settle(page, 3.0)
            _render_feed(page,
                         max_items=int(opts_items),
                         budget_s=float(opts_budget))
            _expand_posts(page)

            for post in page.evaluate(POST_JS):
                post_id = (post["href"].rstrip("/").split("/")[-1]
                           if post["href"] else hashlib.sha256(post["text"].encode()).hexdigest()[:20])
                from ..enrich import _clean_plus
                text = _clean_plus(post["text"])
                # En los grupos el precio va escrito a mano dentro del post
                # ("valor 1.700 reais", "17 mil pezos"), no como campo aparte.
                # Buscar el precio en TODOS los bloques, no solo en el largo.
                price, currency = parse_price_text(text)
                if price is None:
                    for extra in post.get("blocks", [])[1:6]:
                        price, currency = parse_price_text(_clean_plus(extra))
                        if price is not None:
                            break
                if price is None:
                    price, currency = _first_price(text), "BRL"
                yield Listing(
                    source=self.name,
                    external_id=f"g{group_id}-{post_id}",
                    title=text[:200],
                    url=post["href"],
                    price=price,
                    currency=currency or "BRL",
                    location=f"grupo {group_id}",
                    image=post["img"],
                    raw={"description": text, "kind": "group",
                         "search_region": " ".join(cfg.source_opts(self.name).get("group_cities", {}).get(group_id, cfg.filters.cities)),
                         "location_confidence": "grupo configurado; confirmar con vendedor",
                         "group": group_id},
                )
