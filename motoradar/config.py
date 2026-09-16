from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PATH = Path("config.yaml")


@dataclass
class FilterConfig:
    queries: list[str] = field(default_factory=lambda: ["moto"])
    max_price: float = 1000.0
    min_price: float = 0.0
    allow_no_price: bool = True
    cities: list[str] = field(default_factory=lambda: ["jaguarao"])
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
        filters = FilterConfig(**(data.get("filters") or {}))
        cfg = cls(
            filters=filters,
            economics=data.get("economics") or {},
            fx=data.get("fx") or {},
            budget=float(data.get("budget", 1000.0)),
            sources=data.get("sources") or {},
            telegram=data.get("telegram") or {},
            db_path=data.get("db_path") or "data/listings.sqlite3",
            csv_path=data.get("csv_path") or "data/hits.csv",
            monitoring=data.get("monitoring") or {},
        )
        # Secretos por variable de entorno ganan sobre el YAML.
        for key, env in (("token", "TELEGRAM_BOT_TOKEN"), ("chat_id", "TELEGRAM_CHAT_ID")):
            cfg.telegram[key] = os.environ.get(env, cfg.telegram.get(key, ""))
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not math.isfinite(self.budget) or self.budget < 0:
            raise ValueError("budget debe ser finito y >= 0")
        if self.fx.get("base", "BRL") not in ("BRL", "USD", "UYU"):
            raise ValueError("fx.base debe ser BRL, USD o UYU")
        for code, rate in self.fx.get("rates", {}).items():
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
                raise ValueError(f"Tasa FX invalida para {code}")
        for key in ("enrich_details", "alert_unconfirmed"):
            if key in self.monitoring and not isinstance(self.monitoring[key], bool):
                raise ValueError(f"monitoring.{key} debe ser true/false")

    def source_enabled(self, name: str) -> bool:
        return bool((self.sources.get(name) or {}).get("enabled"))

    def source_opts(self, name: str) -> dict:
        return dict(self.sources.get(name) or {})
