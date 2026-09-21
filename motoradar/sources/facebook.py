"""Facebook: NO hay API.

La Graph API nunca expuso Marketplace, y el acceso al feed de grupos para
apps se cerro en 2020. La unica via es manejar tu propia sesion con un
navegador real (Playwright + perfil persistente). Automatizar la navegacion
va contra los Terminos de Meta: el riesgo es un checkpoint o bloqueo de la
cuenta. Por eso el scroll es lento y el volumen chico por defecto.

En un pueblo como Jaguarao los grupos de compra/venta rinden mucho mas que
Marketplace: configuralos en `groups`.

Los grupos se leen por FEED CRONOLOGICO, no por buscador. El buscador de
Facebook ordena por relevancia (un post de 2021 le gana a uno de hace diez
minutos) y solo devuelve lo que contiene la palabra buscada: "vendo essa 125
andando, 800" nunca dice "moto" y se perdia entera. El feed cronologico da
frescura y recall; la clasificacion la hace appraise.py, que para eso esta.
"""
from __future__ import annotations

import time
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

from ..config import Config
from ..models import (Listing, parse_price, parse_price_text,
                      scrub_personal, strip_accents)
from ..money import detect_currency
from .base import BaseSource, SessionExpired, SourceError

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
  let items = Array.from(feed.querySelectorAll('[role="article"]')).filter(art => {
    const parentArt = art.parentElement ? art.parentElement.closest('[role="article"]') : null;
    if (parentArt) return false;
    const txt = (art.textContent || '').trim();
    return txt.length >= 15;
  });
  if (items.length === 0) {
    items = Array.from(feed.children).filter(c => {
      const txt = (c.textContent || '').trim();
      return txt.length >= 15;
    });
  }
  return items.map(k => {
    // El texto del post vive en div[dir="auto"], span[dir="auto"], o bloques preview de mensaje.
    // El cuerpo del post puede venir partido en varios bloques (y tras expandir "Ver mas" aparecen mas).
    // Se devuelven todos: el precio suele estar en un bloque distinto al del texto largo.
    const vistos = new Set();
    const UI_WORDS = new Set(['facebook', 'curtir', 'comentar', 'compartilhar', 'me gusta', 'compartir', 'ver mas', 'ver mais', 'see more', 'ver más', 'ver traducción', 'ver tradução']);
    const blocks = Array.from(k.querySelectorAll('div[dir="auto"], span[dir="auto"], [data-ad-preview="message"], [data-ad-comet-preview="message"]'))
      .map(d => (d.textContent || '').trim())
      .filter(t => t.length >= 4 && !UI_WORDS.has(t.toLowerCase()) && !vistos.has(t) && vistos.add(t));
    if (blocks.length === 0) {
      const lines = (k.innerText || '').split('\n').map(l => l.trim()).filter(l => l.length >= 4 && !UI_WORDS.has(l.toLowerCase()) && !vistos.has(l) && vistos.add(l));
      if (lines.length > 0) blocks.push(...lines);
    }
    blocks.sort((a, b) => b.length - a.length);
    const link = Array.from(k.querySelectorAll('a[href]'))
      .map(a => a.href)
      .find(h => /\/posts\/|multi_permalinks|permalink|\/story\.php|\/commerce\/listing\//.test(h)) || '';
    const ftNode = k.matches('[data-ft]') ? k : k.querySelector('[data-ft]');
    let stableId = '';
    if (ftNode) {
      try {
        const ft = JSON.parse(ftNode.getAttribute('data-ft') || '{}');
        stableId = String(ft.top_level_post_id || ft.mf_story_key || ft.story_fbid || '');
      } catch (_) {}
    }
    if (!stableId && link) {
      const linkMatch = link.match(/(?:posts|permalink|multi_permalinks|commerce\/listing)[/=](\d+)/i) ||
                        link.match(/story_fbid=(\d+)/i);
      if (linkMatch) stableId = linkMatch[1];
    }
    if (!stableId) {
      const nodeId = k.id || '';
      const match = nodeId.match(/(?:post|story)[^0-9]*(\d{5,})/i);
      stableId = match ? match[1] : '';
    }
    // La fecha del post: el <a> del permalink la lleva como texto ("2 h",
    // "12 de setembro") o en aria-label. Sin esto `posted_at` quedaba vacio y
    // la frescura que justifica el feed cronologico no se podia medir.
    let dateText = '';
    const fechas = Array.from(k.querySelectorAll('a[href], abbr'));
    for (const a of fechas) {
      const etiqueta = (a.getAttribute('aria-label') || a.textContent || '').trim();
      if (etiqueta && etiqueta.length < 40 &&
          /^\d+\s*(s|m|min|h|d|sem|a)$|hace|faz|h[áa]|publicad[oa]|atras|atrás|ayer|hoje|hoy|ontem|\d{1,2}\s+de\s+\w+/i.test(etiqueta)) {
        dateText = etiqueta;
        break;
      }
    }
    const img = (k.querySelector('img[src*="scontent"]') || {}).src || '';
    return {text: blocks[0] || '', blocks: blocks, href: link, img: img,
            id: stableId, date_text: dateText};
  }).filter(x => x.text);
}
"""

# --- Salud del DOM ------------------------------------------------------------
# Cero resultados y "Facebook cambio el HTML" se ven IGUAL desde afuera: una
# lista vacia. Sin distinguirlos, el radar se apaga y sigue informando "OK: 0
# observados" como un domingo tranquilo. Facebook siempre pinta un cartel de
# vacio cuando no hay nada; si no hay ni tarjetas ni cartel, el DOM cambio.
EMPTY_MARKERS_JS = (
    r"/nenhum resultado|nenhuma publica|no se encontraron|no hay resultados|"
    r"no results|sin resultados|sem resultados|tente outra|intenta otra|"
    r"no encontramos|nao encontramos/i"
)
FEED_HEALTH_JS = """
() => {
  const feed = document.querySelector('[role="feed"]');
  const main = document.querySelector('[role="main"]');
  const text = (main && main.innerText || '');
  return {
    feed: !!feed,
    children: feed ? feed.children.length : 0,
    main: !!main,
    empty: %s.test(text.slice(0, 4000)),
    text_len: text.length,
  };
}
""" % EMPTY_MARKERS_JS
MARKET_HEALTH_JS = """
() => {
  const main = document.querySelector('[role="main"]');
  const text = (main && main.innerText || '');
  return {
    anchors: document.querySelectorAll('a[href*="/marketplace/item/"]').length,
    main: !!main,
    empty: %s.test(text.slice(0, 4000)),
    text_len: text.length,
  };
}
""" % EMPTY_MARKERS_JS


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
    try:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            locale="pt-BR",
        )
    except Exception:
        try:
            pw.stop()
        except Exception:
            pass
        raise
    return pw, ctx


def _close_runtime(pw, ctx) -> None:
    """Intenta liberar cada recurso; un close fallido no omite stop."""
    try:
        if ctx is not None:
            ctx.close()
    except Exception:
        pass
    try:
        if pw is not None:
            pw.stop()
    except Exception:
        pass


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
        _close_runtime(pw, ctx)
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
        _close_runtime(pw, ctx)


def _validate_navigation(page) -> None:
    url = str(getattr(page, "url", "")).lower()
    if any(marker in url for marker in ("/login", "/checkpoint", "/recover")):
        raise SessionExpired("La sesion de Facebook expiro o requiere verificacion. Corre: python -m motoradar login")


# Todas las formas conocidas en que un post lleva su id numerico estable. Antes
# _post_identity solo miraba /posts|/permalink|/commerce/listing y story_fbid|fbid,
# menos que el propio POST_JS: un permalink de grupo ("?multi_permalinks=<id>",
# "?post_id=<id>", "/story.php?story_fbid=<id>", "/videos/<id>") no daba id
# estable y el post caia al hash del texto, que cambia con cada edicion y
# dispara una re-alerta del mismo aviso. Con esto Python queda a la par de POST_JS.
_POST_ID_PATTERNS = (
    re.compile(r"/(?:posts|permalink|commerce/listing|videos|photos)/(?:[^/?]+/)?(\d{5,})"),
    re.compile(r"[?&](?:story_fbid|fbid|multi_permalinks|post_id)=(\d{5,})"),
)


def _stable_id_from_href(href: str) -> str:
    for patron in _POST_ID_PATTERNS:
        match = patron.search(href or "")
        if match:
            return match.group(1)
    return ""


def _post_identity(post: dict) -> tuple[str, str]:
    stable = str(post.get("id") or "").strip()
    if not stable:
        stable = _stable_id_from_href(str(post.get("href") or ""))
    if stable:
        return stable, "estable"
    # Fallback DELIBERADAMENTE sin normalizar: hash del texto crudo. Normalizarlo
    # (minusculas, sin acentos) evitaria re-alertas al reeditar el formato, pero
    # haria colisionar dos avisos distintos con el mismo texto -> una moto barata
    # perdida, el peor error del sistema. Entre una re-alerta y una moto perdida,
    # se elige la re-alerta.
    fallback = hashlib.sha256(str(post.get("text") or "").encode()).hexdigest()[:20]
    return fallback, "inestable: hash del texto; una edicion cambia la identidad"


def _post_url(group_id: str, post: dict, post_id: str, confidence: str) -> tuple[str, str]:
    """Una alerta sin enlace no sirve para comprar.

    POST_JS solo encuentra el permalink cuando el post trae el `<a>` con la
    fecha renderizado; en el feed cronologico a veces no esta todavia. Antes
    eso mandaba a Telegram una moto a R$800 con el campo del link vacio. Con
    id estable el permalink se reconstruye; si ni eso, al menos el grupo.
    """
    href = str(post.get("href") or "").strip()
    if href:
        return href, "permalink del post"
    if post_id and confidence == "estable":
        return (f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/",
                "permalink reconstruido desde el id del post")
    return (f"https://www.facebook.com/groups/{group_id}",
            "sin permalink: enlace al grupo, buscar el aviso a mano")


# "2 h", "hace 3 horas", "ontem", "12 de setembro". Se resuelve lo relativo, que
# es lo unico que importa para decidir si el feed viene cronologico de verdad.
_REL_RE = re.compile(
    r"(?:hace\s+|faz\s+|ha\s+|publicad[oa]\s+(?:hace\s+|ha\s+)?)?"
    r"(\d+)\s*"
    r"(s|seg|segs|segundos?|m|min|mins|minutos?|h|hr|hrs|hs|horas?|"
    r"d|dias?|sem|sems|semanas?|mes|meses|a|ano|anos?)\b",
    re.IGNORECASE,
)
_UNIDADES = {
    "s": 1, "seg": 1, "segs": 1, "segundo": 1, "segundos": 1,
    "m": 60, "min": 60, "mins": 60, "minuto": 60, "minutos": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hs": 3600, "hora": 3600, "horas": 3600,
    "d": 86400, "dia": 86400, "dias": 86400,
    "sem": 604800, "sems": 604800, "semana": 604800, "semanas": 604800,
    "mes": 2592000, "meses": 2592000,
    "a": 31536000, "ano": 31536000, "anos": 31536000,
}
_MONTHS = {
    "enero": 1, "ene": 1, "janeiro": 1, "jan": 1, "january": 1,
    "febrero": 2, "feb": 2, "fevereiro": 2, "fev": 2, "february": 2,
    "marzo": 3, "mar": 3, "marco": 3, "march": 3,
    "abril": 4, "abr": 4, "april": 4, "apr": 4,
    "mayo": 5, "may": 5, "maio": 5, "mai": 5,
    "junio": 6, "jun": 6, "junho": 6, "june": 6,
    "julio": 7, "jul": 7, "julho": 7, "july": 7,
    "agosto": 8, "ago": 8, "august": 8, "aug": 8,
    "septiembre": 9, "sep": 9, "sept": 9, "setiembre": 9, "set": 9, "setembro": 9, "september": 9,
    "octubre": 10, "oct": 10, "outubro": 10, "out": 10, "october": 10,
    "noviembre": 11, "nov": 11, "novembro": 11, "november": 11,
    "diciembre": 12, "dic": 12, "dezembro": 12, "dez": 12, "december": 12, "dec": 12,
}
_ABS_RE = re.compile(
    r"\b(\d{1,2})\s+(?:de\s+)?([a-z]+)\.?(?:\s+(?:de\s+)?(\d{4}))?"
    r"(?:(?:\s+as|\s+a\s+las)?\s+(\d{1,2}):(\d{2}))?",
    re.IGNORECASE,
)


def parse_post_age(date_text: str, now: datetime | None = None) -> float | None:
    """Antiguedad del post en segundos, o None si no se pudo interpretar.

    Soporta formatos relativos y absolutos en espanol y portugues (comunes
    en grupos de frontera y marketplace). Nunca lanza excepciones no controladas.
    """
    try:
        if date_text is None:
            return None
        texto = strip_accents(str(date_text)).lower().strip()
        if not texto:
            return None
        if now is None:
            now = datetime.now(timezone.utc)

        # Marcadores de inmediatez
        if re.search(r"\b(hoy|hoje|ahora|agora|hace\s+un\s+momento|hace\s+poco|agora\s+mesmo)\b", texto):
            return 0.0

        # Anteayer / anteontem (2 dias)
        if re.search(r"\b(anteayer|anteontem)\b", texto):
            return 172800.0

        # Ayer / ontem (1 dia)
        if re.search(r"\b(ayer|ontem)\b", texto):
            return 86400.0

        # Unidades relativas ("2 h", "3 d", "5 min", "hace X horas", "ha X dias", "faz X min")
        m_rel = _REL_RE.search(texto)
        if m_rel:
            val, unit = m_rel.group(1), m_rel.group(2).lower()
            mult = _UNIDADES.get(unit)
            if mult:
                return float(val) * mult

        # Fechas absolutas con mes ("12 de setembro", "12 de septiembre de 2024")
        m_abs = _ABS_RE.search(texto)
        if m_abs:
            day, mon_str, yr, hr, mn = m_abs.groups()
            mon_clean = mon_str.lower().rstrip(".")
            if mon_clean in _MONTHS:
                month = _MONTHS[mon_clean]
                year = int(yr) if yr else now.year
                hour = int(hr) if hr else 0
                minute = int(mn) if mn else 0
                try:
                    dt = datetime(year, month, int(day), hour, minute, tzinfo=timezone.utc)
                    if not yr and dt > now:
                        dt = dt.replace(year=year - 1)
                    return max(0.0, (now - dt).total_seconds())
                except ValueError:
                    pass

        return None
    except Exception:
        return None


def _group_region(opts: dict, group_id: str) -> tuple[str, bool]:
    """(region_para_filtrar, region_desconocida).

    Antes, un grupo sin `group_cities` heredaba TODAS las ciudades buscadas
    como si fueran su ubicacion, con lo cual el filtro de ciudad pasaba
    siempre sin que nada lo dijera. Ahora la falta de dato se declara: el
    aviso sigue pasando (no se pierden motos baratas) pero queda marcado.
    """
    cities = (opts.get("group_cities") or {}).get(group_id)
    if cities:
        return " ".join(cities), False
    return "", True


def _scroll(page, rounds: int, pause: float) -> None:
    for _ in range(rounds):
        page.mouse.wheel(0, 2200)
        time.sleep(pause)
    _settle(page)


FEED_CHILDREN_JS = ('() => { const f = document.querySelector(\'[role="feed"]\');'
                    ' return f ? f.children.length : 0; }')
# Facebook decide si esta bloqueandote sin cambiar la URL: un dialogo de
# verificacion o un muro de contenido dejan la pagina sin posts y sin cartel de
# vacio, o sea indistinguible de un rediseño. Contarlo como "DOM roto" hacia que
# `watch` siguiera entrando 11 veces por pasada a una cuenta ya marcada.
SESSION_BLOCK_JS = r"""
() => {
  if (document.querySelector('input[name="pass"], input[type="password"]')) return true;
  const main = document.querySelector('[role="main"]') || document.body;
  const t = ((main && main.innerText) || '').slice(0, 4000);
  return /confirme sua identidade|confirm your identity|confirma tu identidad|voce foi temporariamente|has sido bloquead|temporarily blocked|atividade incomum|unusual activity|sesion ha caducado|sessao expirou/i.test(t);
}
"""


def _session_blocked(page) -> bool:
    try:
        return page.evaluate(SESSION_BLOCK_JS) is True
    except Exception:
        return False


def _grow_feed(page, total: int, deadline: float, intentos: int = 3,
               espera: float = 2.5) -> bool:
    """Pide mas posts y espera DE VERDAD a que lleguen.

    Antes era un solo `wheel` y 1,5 s: Facebook tarda mas que eso en traer y
    pintar el lote siguiente, asi que el bucle se rendia con lo ya renderizado
    —3 a 8 posts por grupo— mientras `group_items: 20` daba a entender que se
    leian 20, y quedaban 20 s del presupuesto sin usar.
    """
    for _ in range(intentos):
        restante = deadline - time.time()
        if restante <= 0:
            return False
        page.mouse.wheel(0, 2500)
        time.sleep(min(espera, restante))
        try:
            if int(page.evaluate(FEED_CHILDREN_JS) or 0) > total:
                return True
        except Exception:
            return False
    return False


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
            if not _grow_feed(page, total, deadline):
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


def _settle(page, extra: float = 2.0, wait_for: str | None = None) -> None:
    """Esperar a que las tarjetas terminen de renderizar ANTES de leerlas.

    Sin esto se leen a medio pintar y los precios salen truncados: una corrida
    devolvio "R$5" para una Honda CG160 2022 y "R$55" para otra. Con la lectura
    asentada, esos mismos avisos dan el precio real. Un precio mal leido no es
    ruido: rompe el calculo de margen entero.
    """
    if wait_for:
        # En un feed infinito `networkidle` NO llega nunca: esperarlo era pagar
        # 8 s por grupo (76 s por pasada) a cambio de nada. Se espera al primer
        # post, que es la señal de que hay algo que leer.
        try:
            page.wait_for_selector(wait_for, timeout=4000)
        except Exception:
            pass
    else:
        try:
            page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass  # networkidle no siempre llega
    time.sleep(extra)


def _health(page, script: str) -> dict:
    try:
        value = page.evaluate(script)
    except Exception as exc:
        return {"error": type(exc).__name__}
    return value if isinstance(value, dict) else {}


def feed_dom_broken(health: dict) -> bool:
    """True cuando la pagina cargo pero no se reconoce el feed de posts."""
    if not health or health.get("error"):
        return True
    if health.get("feed") and health.get("children", 0) > 0:
        return False
    return not health.get("empty")


def market_dom_broken(health: dict) -> bool:
    """True cuando no hay tarjetas NI cartel de vacio: el HTML cambio."""
    if not health or health.get("error"):
        return True
    if health.get("anchors"):
        return False
    return not health.get("empty")


# El importe PEGADO al simbolo. Antes se tomaba el primer numero de la linea y
# "CG 125 2008 R$ 900" daba R$1.252.008: la cilindrada y el año pegados.
# Y al reves: la clase "[\d.,  ]*\d" tragaba el espacio, asi "R$ 800 2015"
# (precio + año en el mismo renglon) capturaba "800 2015" y card_to_listing
# devolvia 8.002.015: la moto de R$800 quedaba fuera de presupuesto. La captura
# ya no cruza un espacio.
_CARD_PRICE_RE = re.compile(
    r"(?:R\$|U\$S|US\$|\$U|USD|UYU)\s*(\d[\d., ]*\d|\d)", re.IGNORECASE)
_FREE_WORDS = ("gratis", "free", "de graca", "de gracas")


def _is_price_line(line: str) -> bool:
    return bool(_CARD_PRICE_RE.search(line)) or strip_accents(line) in _FREE_WORDS


# "Jaguarao, RS", "Rio Branco, Cerro Largo, Uruguay", "Melo". Corta y sin verbo
# de venta ni importe: lo que Facebook pone en la ultima linea de la tarjeta.
_UF_RE = re.compile(r",\s*(?:[A-Z]{2}|uruguay|brasil|brazil)\s*$", re.IGNORECASE)
# Marcas Y modelos comunes de la frontera: un titulo de 2 palabras como "Titan
# preta" no debe leerse como ciudad solo por ser corto.
_SELL_HINT_RE = re.compile(
    r"\b(?:vendo|vende|troco|permut|passo|moto|scooter|"
    r"honda|yamaha|suzuki|dafra|shineray|haojue|"
    r"cg|biz|pop|fan|titan|bros|xre|cb|start|fazer|factor|ybr|xtz|crypton|"
    r"nmax|fz|lander|tenere|tornado|twister|cbx|dk|gsr|intruder)\b",
    re.IGNORECASE)

# Ciudades de la frontera, para el caso de UNA sola linea (ver _parse_card):
# ahi un titulo de moto es mas probable que una ubicacion, asi que solo se
# acepta como ubicacion una senal fuerte (', RS'/pais o ciudad conocida).
_KNOWN_LOC = {
    "jaguarao", "rio branco", "melo", "pelotas", "bage", "acegua",
    "arroio grande", "yaguaron", "rivera", "santana do livramento",
    "cerro largo", "treinta y tres", "rio grande", "herval", "candiota",
    "pedras altas", "pinheiro machado", "hulha negra", "aceguá",
}


def _looks_like_location(line: str) -> bool:
    if _SELL_HINT_RE.search(strip_accents(line)):
        return False
    if _UF_RE.search(line):
        return True
    # Un nombre de ciudad suelto: pocas palabras, sin numeros.
    return len(line.split()) <= 3 and not re.search(r"\d", line)


def _is_strong_location(line: str) -> bool:
    """Senal FUERTE de ubicacion, para lineas unicas: sufijo ', RS'/pais o una
    ciudad conocida. Evita que "Titan preta" (titulo) se lea como ciudad."""
    if _SELL_HINT_RE.search(strip_accents(line)):
        return False
    if _UF_RE.search(line):
        return True
    return strip_accents(line) in _KNOWN_LOC


def _first_price(text: str) -> tuple[float | None, str]:
    """(importe, evidencia). La evidencia distingue tres cosas que antes se
    confundian en un solo 0.0: un precio real, un "Gratis" y una tarjeta que
    no publica precio."""
    for line in text.splitlines():
        match = _CARD_PRICE_RE.search(line)
        if match:
            price = parse_price(match.group(1))
            if price is not None:
                return price, "simbolo de moneda en la tarjeta"
    for line in text.splitlines():
        if strip_accents(line) in _FREE_WORDS:
            return 0.0, "la tarjeta dice gratis"
    return None, ""


def _parse_card(text: str) -> tuple[str, str]:
    """Las tarjetas de Marketplace vienen siempre con la misma forma:

        ['R$1.800', 'Moto trilha', 'Arroio Grande, RS']
        ['R$3.000', 'R$3.700', '2014 Yamaha outro', 'Jaguarao, RS']   <- rebaja

    O sea: precio(s), titulo, ubicacion. Lo que decide no es el NUMERO de
    lineas sino cuales son precio: contando lineas, una tarjeta sin precio
    publicado ("Moto CG 125 para retirar pecas" + "Jaguarao, RS") se quedaba
    con la ciudad como titulo y sin ubicacion, y despues se descartaba por
    "ciudad no coincide" — una moto perdida con un motivo que miente.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cuerpo = [ln for ln in lines if not _is_price_line(ln)]
    if len(cuerpo) >= 2:
        if _looks_like_location(cuerpo[-1]):
            loc = cuerpo[-1]
        elif len(cuerpo) > 2 and _looks_like_location(cuerpo[-2]):
            loc = cuerpo[-2]
        else:
            loc = cuerpo[-1]
        return cuerpo[0][:200], loc
    if len(cuerpo) == 1:
        # Una sola linea que parece ubicacion ES la ubicacion, no el titulo: la
        # tarjeta no trajo titulo. Tomarla como titulo hacia que appraise
        # clasificara "Jaguarao, RS" y el aviso se descartara como desconocido.
        # Pero con UNA sola linea el default seguro es titulo: un modelo corto
        # ("Titan preta") no es una ciudad. Solo se descarta como titulo ante
        # una senal fuerte de ubicacion (', RS'/pais o ciudad conocida).
        if _is_strong_location(cuerpo[0]):
            return "", cuerpo[0]
        return cuerpo[0][:200], ""
    return (lines[0][:200] if lines else ""), ""


def card_to_listing(card: dict) -> Listing:
    """Tarjeta de Marketplace -> Listing. Pura: se prueba con fixtures."""
    text = str(card.get("text") or "")
    url = str(card.get("href") or "")
    item_id = url.rstrip("/").split("/")[-1]
    title, location = _parse_card(text)
    raw = {"card_text": text, "kind": "marketplace", "text_shape": "title"}
    if not title:
        # La tarjeta no trajo titulo. Se declara en vez de inventar uno con la
        # ciudad: la lectura de detalle traera la descripcion y appraise podra
        # clasificar sobre eso.
        raw["card_parse"] = "la tarjeta no traia titulo"
    if not item_id:
        # Sin id, DOS tarjetas distintas compartian el uid "facebook:" y el
        # dedupe de pipeline se quedaba con una sola: la otra moto desaparecia.
        item_id = "card-" + hashlib.sha256(text.encode()).hexdigest()[:16]
        raw["url_confidence"] = "sin enlace: la tarjeta no traia href"
    if not location:
        # BUG-FB-GEO-05: Se declara la falta de ubicacion en la tarjeta para no
        # descartarla prematuramente antes de que el enriquecido traiga el detalle.
        raw["location_missing"] = True
        raw["location_confidence"] = "tarjeta sin ubicacion: verificar en detalle o con vendedor"
    price, evidencia = _first_price(text)
    if price == 0.0:
        # Ni "R$ 0" ni "Gratis" son un precio: son el hueco que deja un aviso
        # publicado sin precio, y Facebook muestra las dos formas para lo mismo.
        # Afirmarlo como importe presentaba una CG 2014 con "R$ 0" y la metia al
        # presupuesto como si costara cero. El aviso llega igual, sin precio.
        raw["card_price_text"] = "Gratis" if "gratis" in evidencia else "R$ 0"
        evidencia = f"la tarjeta mostraba \"{raw['card_price_text']}\": el aviso no publica precio"
        price = None
    if evidencia:
        raw["card_price_evidence"] = evidencia
    return Listing(
        source=FacebookSource.name,
        external_id=item_id,
        title=title,
        url=url,
        price=price,
        currency=detect_currency(text),
        location=location,
        image=str(card.get("img") or ""),
        raw=raw,
    )


def post_to_listing(group_id: str, post: dict, region: str,
                    region_unknown: bool, origin: str = "feed") -> Listing:
    """Post de grupo -> Listing. Pura: se prueba con fixtures."""
    from ..enrich import _clean_plus
    from ..filters import detect_location_cue

    post_id, identity_confidence = _post_identity(post)
    # Se redacta ANTES de cualquier otra cosa: lo que no entra al pipeline no
    # termina en la base, en el CSV ni en un mensaje de Telegram.
    text = scrub_personal(_clean_plus(str(post.get("text") or "")))
    # En los grupos el precio va escrito a mano dentro del post
    # ("valor 1.700 reais", "17 mil pezos"), no como campo aparte.
    # Buscar el precio en TODOS los bloques, no solo en el largo.
    price, currency = parse_price_text(text)
    if price is None:
        for extra in list(post.get("blocks") or [])[1:6]:
            price, currency = parse_price_text(scrub_personal(_clean_plus(str(extra))))
            if price is not None:
                break
    if price is None:
        # detect_currency, no "BRL" a la fuerza: en los grupos de frontera
        # aparece "$U 15.000" y darlo por reales inventa un precio brasileno.
        importe, _evidencia = _first_price(text)
        price, currency = importe, detect_currency(text)
    url, url_confidence = _post_url(group_id, post, post_id, identity_confidence)
    date_text = str(post.get("date_text") or "")
    edad = parse_post_age(date_text)
    posted_at = ""
    if edad is not None:
        posted_at = (datetime.now(timezone.utc)
                     - timedelta(seconds=edad)).isoformat(timespec="seconds")

    # BUG-FB-GEO-02: Inspeccionar texto del post por pistas de ubicacion.
    # Si el vendedor indica una ciudad lejana (Pelotas, Bagé, Melo, etc.), no asumir
    # la region del grupo; si confirma una ciudad objetivo, registrarlo.
    cue_city, is_target = detect_location_cue(text)
    if cue_city and not is_target:
        location = f"{cue_city} (grupo {group_id})"
        search_region = ""
        location_confidence = f"vendedor indico otra ciudad en el post: {cue_city}"
    elif cue_city and is_target:
        location = f"grupo {group_id}"
        search_region = region or cue_city
        location_confidence = f"ciudad confirmada en el post: {cue_city}"
    else:
        location = f"grupo {group_id}"
        search_region = region
        location_confidence = (
            "grupo sin ciudades configuradas: zona sin verificar"
            if region_unknown else
            "grupo configurado; confirmar con vendedor")

    return Listing(
        source=FacebookSource.name,
        external_id=f"g{group_id}-{post_id}",
        title=text[:200],
        url=url,
        price=price,
        currency=currency or "BRL",
        location=location,
        image=str(post.get("img") or ""),
        posted_at=posted_at,
        raw={"description": text, "kind": "group",
             # El cuerpo de un post no es un titulo de anuncio: casi todos
             # empiezan con saludo o emoji. appraise necesita saberlo para no
             # clasificar por posicion.
             "text_shape": "post",
             "identity_confidence": identity_confidence,
             "url_confidence": url_confidence,
             "search_region": search_region,
             "region_unknown": region_unknown,
             "location_cue": cue_city or "",
             "location_confidence": location_confidence,
             "group": group_id,
             "group_origin": origin,
             "posted_text": date_text},
    )


class FacebookSource(BaseSource):
    name = "facebook"

    def fetch(self, cfg: Config, opts: dict) -> Iterable[Listing]:
        self.reset()
        if not PROFILE_DIR.exists():
            raise SessionExpired("Sin sesion de Facebook. Corre: python -m motoradar login")

        headless = bool(opts.get("headless", True))
        rounds = int(opts.get("scroll_rounds", 6))
        pause = float(opts.get("scroll_pause", 1.6))
        groups = opts.get("groups") or []
        mode = str(opts.get("group_mode", "feed")).lower()
        if mode not in ("feed", "search", "both"):
            raise SourceError("sources.facebook.group_mode debe ser feed, search o both")

        if opts.get("marketplace", True):
            self.expected_origins.append(f"{self.name}/marketplace")
        self.expected_origins.extend(
            f"{self.name}/grupo-{group_id}" for group_id in groups)

        observed = 0
        pw, ctx = _launch(headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if opts.get("marketplace", True):
                print("   facebook: Marketplace", flush=True)
                for listing in self._marketplace(page, cfg, opts, rounds, pause):
                    observed += 1
                    yield listing
            # Los grupos son chicos: no tiene sentido pasarles las 6 queries
            # de OLX. 8 grupos x 6 queries son 48 cargas de pagina y el riesgo
            # de checkpoint sube mucho. El feed cronologico necesita UNA sola.
            gq = opts.get("group_queries") or cfg.filters.queries[:2]
            for index, group_id in enumerate(groups, 1):
                group_id = str(group_id)
                region, region_unknown = _group_region(opts, group_id)
                if region_unknown:
                    print(f"   facebook: grupo {index}/{len(groups)} sin "
                          f"group_cities: zona sin verificar", flush=True)
                else:
                    print(f"   facebook: grupo {index}/{len(groups)}", flush=True)
                # Un grupo que revienta no se lleva los otros siete. Pasaba con
                # "Execution context was destroyed", que es comun en un feed
                # que navega solo: moria el primero y se perdian los demas.
                # SessionExpired si corta: es una condicion de parada, no un
                # fallo de una pagina.
                try:
                    if mode in ("feed", "both"):
                        for listing in self._group_feed(
                                page, group_id, region, region_unknown, opts):
                            observed += 1
                            yield listing
                    if mode in ("search", "both"):
                        for listing in self._group_search(
                                page, group_id, region, region_unknown, gq, opts):
                            observed += 1
                            yield listing
                except SessionExpired:
                    raise
                except Exception as exc:
                    self.failed_pages += 1
                    # Por indice, no por id: el id del grupo privado no tiene
                    # por que quedar en runs.stats ni en la salida de doctor.
                    aviso = f"grupo {index}/{len(groups)} fallo: {type(exc).__name__}"
                    self.note(aviso)
                    print(f"   ! {aviso}; sigo con los demas", flush=True)
        finally:
            _close_runtime(pw, ctx)
        if self.dom_failures and not observed:
            # Cero observaciones CON DOM roto no es "no habia nada": es que la
            # fuente dejo de leerse. Se declara error para que la corrida no
            # lo informe como una pasada normal y vacia.
            raise SourceError(
                f"Facebook: {self.dom_failures} paginas con DOM no reconocido "
                f"y ninguna observacion; el adaptador quedo ciego")

    # --- Marketplace ---------------------------------------------------------
    def _marketplace(self, page, cfg: Config, opts: dict, rounds: int, pause: float):
        queries = opts.get("marketplace_queries") or cfg.filters.queries
        timeout_ms = int(opts.get("navigation_timeout_ms", 60000))
        for index, query in enumerate(queries, 1):
            print(f"     consulta {index}/{len(queries)}: {query}", flush=True)
            params = {
                "query": query,
                "maxPrice": int(cfg.filters.max_price),
                "minPrice": int(cfg.filters.min_price),
                "exact": "false",
            }
            if opts.get("radius_km"):
                params["radius"] = int(opts["radius_km"]) * 1000
            url = f"https://www.facebook.com/marketplace/search/?{urlencode(params)}"
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            _validate_navigation(page)
            time.sleep(3)
            _scroll(page, rounds, pause)
            self.pages_read += 1

            cards = page.evaluate(ITEM_JS) or []
            if not cards:
                if _session_blocked(page):
                    raise SessionExpired(
                        "Facebook pide verificacion o bloqueo la cuenta sin "
                        "redirigir. Corre: python -m motoradar login")
                health = _health(page, MARKET_HEALTH_JS)
                if market_dom_broken(health):
                    self.note_blind(f"Marketplace/{query}", health)
                continue
            cities = opts.get("cities") or cfg.filters.cities
            search_region = " ".join(cities)
            for card in cards:
                try:
                    listing = card_to_listing(card)
                    if not listing.location:
                        listing.raw["search_region"] = search_region
                    yield listing
                except Exception as exc:
                    aviso = f"Marketplace/{query}: tarjeta malformada omitida ({type(exc).__name__}: {exc})"
                    self.note(aviso)
                    print(f"     ! {aviso}", flush=True)

    # --- Grupos --------------------------------------------------------------
    def _read_feed(self, page, opts: dict, where: str) -> list[dict]:
        _settle(page, float(opts.get("group_settle_s", 3.0)),
                wait_for='[role="feed"] > div')
        materializados = _render_feed(
            page,
            max_items=int(opts.get("group_items", 14)),
            budget_s=float(opts.get("group_budget_s", 35.0)),
            # `scroll_pause` solo lo usaba Marketplace: el ritmo de los grupos
            # era el 0.6 por defecto y el dial no estaba conectado a nada.
            pause=float(opts.get("group_scroll_pause", 0.6)))
        _expand_posts(page)
        self.pages_read += 1
        posts = page.evaluate(POST_JS) or []
        edades = []
        for p in posts:
            try:
                edad = parse_post_age(str((p or {}).get("date_text") or ""))
                if edad is not None:
                    edades.append(edad)
            except Exception:
                pass
        frescura = ""
        if edades:
            mediana = sorted(edades)[len(edades) // 2] / 86400
            frescura = f", mediana {mediana:.1f} dias"
            # Si Facebook ignora `sorting_setting` devuelve los destacados de
            # siempre y el feed deja de ser cronologico en silencio: se leerian
            # los mismos posts viejos cada pasada informando "OK".
            tope = float(opts.get("group_freshness_days", 7))
            if len(edades) >= 3 and mediana > tope:
                self.note(f"{where}: el feed no viene cronologico "
                          f"(mediana {mediana:.1f} dias > {tope:.0f})")
                print(f"     ! {where}: mediana de antiguedad {mediana:.1f} dias; "
                      f"el orden cronologico no se esta aplicando", flush=True)
        print(f"     {where}: {len(posts)} posts de {materializados} "
              f"materializados{frescura}", flush=True)
        # Materializar 20 hijos y extraer 3 no es un feed corto: es el extractor
        # perdiendo el 85% de lo que ya se pago traer. No se declara DOM roto
        # (hay posts, el selector sigue vivo) pero tampoco puede pasar callado:
        # es el techo real de cobertura de los grupos.
        try:
            materializados = int(materializados)
        except (TypeError, ValueError):
            materializados = 0      # sin dato no se puede juzgar la extraccion
        if materializados >= 8 and len(posts) * 4 < materializados:
            self.note(f"{where}: extraccion baja, {len(posts)}/{materializados} "
                      f"posts; revisar POST_JS contra el DOM actual")
            print(f"     ! {where}: solo se extrajo {len(posts)} de "
                  f"{materializados}; el techo de cobertura esta en el extractor",
                  flush=True)
        if not posts:
            if _session_blocked(page):
                raise SessionExpired(
                    "Facebook pide verificacion o bloqueo la cuenta sin "
                    "redirigir. Corre: python -m motoradar login")
            health = _health(page, FEED_HEALTH_JS)
            # Children without extractable posts are not evidence of a healthy
            # empty feed either: the extractor may no longer recognize them.
            if feed_dom_broken(health) or (health.get("children", 0) > 0
                                           and not health.get("empty")):
                self.note_blind(where, health)
        return posts

    def _group_feed(self, page, group_id: str, region: str,
                    region_unknown: bool, opts: dict):
        """Feed cronologico del grupo: lo nuevo primero, sin filtrar por palabra."""
        url = (f"https://www.facebook.com/groups/{group_id}"
               f"?{urlencode({'sorting_setting': 'CHRONOLOGICAL'})}")
        page.goto(url, timeout=int(opts.get("navigation_timeout_ms", 60000)),
                  wait_until="domcontentloaded")
        _validate_navigation(page)
        for post in self._read_feed(page, opts, f"grupo {group_id} (cronologico)"):
            try:
                yield post_to_listing(group_id, post, region, region_unknown, "feed")
            except Exception as exc:
                aviso = f"grupo {group_id} (cronologico): post malformado omitido ({type(exc).__name__}: {exc})"
                self.note(aviso)
                print(f"     ! {aviso}", flush=True)

    def _group_search(self, page, group_id: str, region: str,
                      region_unknown: bool, queries: list[str], opts: dict):
        """Buscador del grupo: complementa el feed para posts mas viejos."""
        selected = queries or ["moto"]
        for index, query in enumerate(selected, 1):
            if len(selected) > 1:
                print(f"     consulta de grupo {index}/{len(selected)}", flush=True)
            url = f"https://www.facebook.com/groups/{group_id}/search/?{urlencode({'q': query})}"
            page.goto(url, timeout=int(opts.get("navigation_timeout_ms", 60000)),
                  wait_until="domcontentloaded")
            _validate_navigation(page)
            for post in self._read_feed(page, opts, f"grupo {group_id}/{query}"):
                try:
                    yield post_to_listing(group_id, post, region, region_unknown, "search")
                except Exception as exc:
                    aviso = f"grupo {group_id}/{query}: post malformado omitido ({type(exc).__name__}: {exc})"
                    self.note(aviso)
                    print(f"     ! {aviso}", flush=True)
