from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PATH = Path("config.yaml")

# Una errata en el NOMBRE de una clave pasaba entera y en silencio: escribir
# `alert_unconfirmado: false` para frenar los avisos inciertos no hacia nada, y
# `chat_ids` en plural dejaba la lista de destinatarios vacia mientras `doctor`
# informaba "sin configurar". `filters` era el unico bloque que avisaba, porque
# construye un dataclass. Ahora todos los bloques declaran sus claves.
KNOWN_KEYS: dict[str, frozenset[str]] = {
    "": frozenset({"filters", "economics", "fx", "budget", "sources",
                   "telegram", "db_path", "csv_path", "monitoring"}),
    "monitoring": frozenset({"enrich_details", "alert_unconfirmed",
                             "health_zero_runs", "session_pause_cycles",
                             "retention_days"}),
    "telegram": frozenset({"token", "chat_id"}),
    "fx": frozenset({"base", "offline", "rates"}),
    "economics": frozenset({"repair_runner", "repair_project", "repair_parts",
                            "resale_factor", "no_docs_resale_factor",
                            "min_margin", "min_samples", "min_real_price",
                            "survey_max_price"}),
    "sources.olx": frozenset({"enabled", "state_path", "pages", "categories"}),
    "sources.facebook": frozenset({
        "enabled", "headless", "marketplace", "marketplace_queries",
        "marketplace_every_cycles", "radius_km", "scroll_rounds",
        "scroll_pause", "group_scroll_pause", "navigation_timeout_ms",
        "group_mode", "group_queries",
        "group_items", "group_budget_s", "group_settle_s", "detail_limit",
        "group_freshness_days",
        "detail_timeout_ms", "detail_pause", "groups", "group_cities"}),
    "sources.mercadolivre": frozenset({"enabled", "client_id", "client_secret",
                                       "state_id", "limit", "sites"}),
}


def _reject_unknown(block: str, data: dict) -> None:
    known = KNOWN_KEYS.get(block)
    if known is None or not isinstance(data, dict):
        return
    unknown = sorted(set(data) - known)
    if unknown:
        donde = block or "la raiz"
        parecidas = ", ".join(
            f"{clave} (quisiste decir {sugerida}?)" if (sugerida := _closest(clave, known))
            else clave for clave in unknown)
        raise ValueError(
            f"{donde} tiene claves desconocidas: {parecidas}. "
            f"Comparar con config.example.yaml")


# Fuera del repositorio, en el perfil del usuario: es el unico sitio que ni un
# `grep -r` del proyecto ni un indexador de editor alcanzan.
TOKEN_FILE = Path.home() / ".motoradar" / "telegram_token"


def _read_token_file() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _closest(word: str, options: frozenset[str]) -> str | None:
    import difflib
    matches = difflib.get_close_matches(word, sorted(options), n=1, cutoff=0.7)
    return matches[0] if matches else None


def _mapping(value, field_name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} debe ser un mapa YAML")
    return value


def _string_list(value, field_name: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} debe ser una lista de textos")


def _number(value, field_name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} debe ser numerico")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{field_name} debe ser finito y >= {minimum}")
    return result


@dataclass
class FilterConfig:
    queries: list[str] = field(default_factory=lambda: ["moto"])
    max_price: float = 1000.0
    min_price: float = 0.0
    allow_no_price: bool = True
    cities: list[str] = field(default_factory=lambda: ["jaguarao", "rio branco"])
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)


@dataclass
class Config:
    filters: FilterConfig = field(default_factory=FilterConfig)
    economics: dict = field(default_factory=dict)
    fx: dict = field(default_factory=dict)
    budget: float = 1000.0
    sources: dict = field(default_factory=dict)
    telegram: dict = field(default_factory=dict)
    db_path: str = "data/listings.sqlite3"
    csv_path: str = "data/hits.csv"
    monitoring: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Config":
        p = Path(path)
        if not p.exists():
            raise SystemExit(
                f"No existe {p}. Copia config.example.yaml a config.yaml y editalo."
            )
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("la raiz de config.yaml debe ser un mapa")
        _reject_unknown("", data)
        for bloque in ("monitoring", "telegram", "fx", "economics"):
            _reject_unknown(bloque, _mapping(data.get(bloque), bloque))
        for nombre, opciones in _mapping(data.get("sources"), "sources").items():
            _reject_unknown(f"sources.{nombre}", _mapping(opciones, f"sources.{nombre}"))
        filters_data = _mapping(data.get("filters"), "filters")
        try:
            filters = FilterConfig(**filters_data)
        except TypeError as exc:
            raise ValueError(f"filters contiene una opcion desconocida: {exc}") from exc
        raw_budget = data.get("budget", 1000.0)
        if isinstance(raw_budget, bool):
            raise ValueError("budget debe ser numerico")
        try:
            budget = float(raw_budget)
        except (TypeError, ValueError) as exc:
            raise ValueError("budget debe ser numerico") from exc
        cfg = cls(
            filters=filters,
            economics=_mapping(data.get("economics"), "economics"),
            fx=_mapping(data.get("fx"), "fx"),
            budget=budget,
            sources=_mapping(data.get("sources"), "sources"),
            telegram=_mapping(data.get("telegram"), "telegram"),
            db_path=data.get("db_path") or "data/listings.sqlite3",
            csv_path=data.get("csv_path") or "data/hits.csv",
            monitoring=_mapping(data.get("monitoring"), "monitoring"),
        )
        # El token NO puede vivir en el arbol del proyecto: un `grep -r` sobre
        # *.yaml, un indexador de editor o cualquier herramienta con el
        # directorio como cwd lo vuelca sin abrir el archivo. Paso real.
        if data.get("telegram", {}).get("token"):
            raise ValueError(
                "telegram.token no puede estar en config.yaml: cualquier "
                "busqueda de texto en el proyecto lo expone. Movelo a "
                f"{TOKEN_FILE} (una linea, solo el token) o exportá "
                "TELEGRAM_BOT_TOKEN, y dejá el campo del YAML vacío.")
        # Secretos por variable de entorno ganan sobre el YAML.
        for key, env in (("token", "TELEGRAM_BOT_TOKEN"), ("chat_id", "TELEGRAM_CHAT_ID")):
            env_value = os.environ.get(env)
            # `.strip()`: un token pegado con `Get-Content` arrastra un salto de
            # linea, Telegram responde 404, el fallo se trata como transitorio y
            # `doctor` sigue diciendo "configurado". Media hora de diagnostico.
            env_value = env_value.strip() if env_value else env_value
            if not env_value and key == "token":
                env_value = _read_token_file()
            if env_value:
                # Varios destinatarios por entorno van separados por coma.
                cfg.telegram[key] = ([part.strip() for part in env_value.split(",")
                                      if part.strip()]
                                     if key == "chat_id" and "," in env_value
                                     else env_value)
            else:
                cfg.telegram.setdefault(key, "")
        cfg.validate()
        return cfg

    def validate(self) -> None:
        _number(self.budget, "budget")
        for key in ("queries", "cities", "include_keywords", "exclude_keywords"):
            _string_list(getattr(self.filters, key), f"filters.{key}")
        self.filters.max_price = _number(self.filters.max_price, "filters.max_price")
        self.filters.min_price = _number(self.filters.min_price, "filters.min_price")
        if self.filters.min_price > self.filters.max_price:
            raise ValueError("filters.min_price no puede superar filters.max_price")
        if not isinstance(self.filters.allow_no_price, bool):
            raise ValueError("filters.allow_no_price debe ser true/false")

        if not isinstance(self.fx, dict):
            raise ValueError("fx debe ser un mapa YAML")
        if self.fx.get("base", "BRL") not in ("BRL", "USD", "UYU"):
            raise ValueError("fx.base debe ser BRL, USD o UYU")
        if "offline" in self.fx and not isinstance(self.fx["offline"], bool):
            raise ValueError("fx.offline debe ser true/false")
        rates = _mapping(self.fx.get("rates"), "fx.rates")
        for code, rate in rates.items():
            if code not in ("BRL", "USD", "UYU"):
                raise ValueError(f"Moneda FX no soportada: {code}")
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
                raise ValueError(f"Tasa FX invalida para {code}")

        for key in ("enrich_details", "alert_unconfirmed"):
            if key in self.monitoring and not isinstance(self.monitoring[key], bool):
                raise ValueError(f"monitoring.{key} debe ser true/false")
        for key in ("health_zero_runs", "session_pause_cycles",
                    "retention_days"):
            value = self.monitoring.get(key)
            if key in self.monitoring and (isinstance(value, bool)
                                           or not isinstance(value, int) or value <= 0):
                raise ValueError(f"monitoring.{key} debe ser entero > 0")

        for name, opts in self.sources.items():
            if not isinstance(opts, dict):
                raise ValueError(f"sources.{name} debe ser un mapa YAML")
            if "enabled" in opts and not isinstance(opts["enabled"], bool):
                raise ValueError(f"sources.{name}.enabled debe ser true/false")
        for name, key in (("olx", "pages"), ("facebook", "scroll_rounds"),
                          ("facebook", "group_items"),
                          ("facebook", "navigation_timeout_ms"),
                          ("facebook", "detail_limit"),
                          ("facebook", "detail_timeout_ms"),
                          ("facebook", "marketplace_every_cycles"),
                          ("mercadolivre", "limit")):
            opts = self.sources.get(name, {})
            if key in opts and (isinstance(opts[key], bool) or not isinstance(opts[key], int) or opts[key] <= 0):
                raise ValueError(f"sources.{name}.{key} debe ser entero > 0")
        for key in ("scroll_pause", "group_scroll_pause", "group_budget_s",
                    "group_settle_s", "detail_pause", "radius_km",
                    "group_freshness_days"):
            opts = self.sources.get("facebook", {})
            if key in opts:
                _number(opts[key], f"sources.facebook.{key}", minimum=0)
        for key in ("headless", "marketplace"):
            opts = self.sources.get("facebook", {})
            if key in opts and not isinstance(opts[key], bool):
                raise ValueError(f"sources.facebook.{key} debe ser true/false")
        for name, keys in (("olx", ("categories",)),
                           ("facebook", ("group_queries", "marketplace_queries")),
                           ("mercadolivre", ("sites",))):
            opts = self.sources.get(name, {})
            for key in keys:
                if key in opts:
                    _string_list(opts[key], f"sources.{name}.{key}")
        fb = self.sources.get("facebook", {})
        if "group_mode" in fb and fb["group_mode"] not in ("feed", "search", "both"):
            raise ValueError("sources.facebook.group_mode debe ser feed, search o both")
        if "groups" in fb and (not isinstance(fb["groups"], list) or
                               any(not isinstance(x, (str, int)) or isinstance(x, bool) for x in fb["groups"])):
            raise ValueError("sources.facebook.groups debe ser una lista de ids")
        if "group_cities" in fb:
            cities = _mapping(fb["group_cities"], "sources.facebook.group_cities")
            for group_id, values in cities.items():
                _string_list(values, f"sources.facebook.group_cities.{group_id}")

        if not isinstance(self.telegram, dict):
            raise ValueError("telegram debe ser un mapa YAML")
        if "token" in self.telegram and not isinstance(self.telegram["token"], str):
            raise ValueError("telegram.token debe ser texto")
        chat = self.telegram.get("chat_id", "")
        for value in (chat if isinstance(chat, list) else [chat]):
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError(
                    "telegram.chat_id debe ser un id o una lista de ids")
        if not isinstance(self.economics, dict):
            raise ValueError("economics debe ser un mapa YAML")
        for key, value in self.economics.items():
            if key == "min_samples":
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError("economics.min_samples debe ser entero > 0")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"economics.{key} debe ser numerico finito")
        if not isinstance(self.db_path, str) or not self.db_path:
            raise ValueError("db_path debe ser texto no vacio")
        if not isinstance(self.csv_path, str) or not self.csv_path:
            raise ValueError("csv_path debe ser texto no vacio")

    def source_enabled(self, name: str) -> bool:
        return (self.sources.get(name) or {}).get("enabled", False) is True

    def source_opts(self, name: str) -> dict:
        return dict(self.sources.get(name) or {})

    def telegram_destinations(self) -> list[str]:
        """Destinatarios normalizados, sin repetidos y en orden de configuracion.

        `chat_id` acepta un id suelto o una lista. El radar lo usan dos socios:
        cada uno recibe en su chat privado y con su propia cola de entregas, asi
        que un 429 o un fallo de red de uno no retrasa las alertas del otro.
        """
        raw = self.telegram.get("chat_id", "")
        values = raw if isinstance(raw, list) else [raw]
        destinos: list[str] = []
        for value in values:
            text = str(value).strip()
            if text and text not in destinos:
                destinos.append(text)
        return destinos
