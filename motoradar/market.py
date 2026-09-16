"""Precio de referencia y margen estimado, para decidir compras de reventa.

La referencia no viene de una tabla externa (la FIPE no cubre sucata ni el
precio real de la calle): se calcula con la mediana de lo que piden HOY por el
mismo modelo andando, en la misma region. Es autocalibrante.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from .appraise import Appraisal, appraise
from .models import Listing


@dataclass
class Economics:
    """Tus numeros. Ajustalos con lo que realmente te cuesta y te pagan."""
    repair_runner: float = 300.0    # revision, fluidos, detalles
    repair_project: float = 1200.0  # motor / electrica
    repair_parts: float = 2500.0    # armar entera desde un doador
    resale_factor: float = 0.85     # vendes por debajo del aviso promedio
    # Los papeles NO se penalizan por riesgo: eso lo juzga el comprador.
    # Lo que si cambia es el precio de reventa, porque la referencia se calcula
    # con motos documentadas y una sin papeles no se vende a ese valor.
    # 1.0 = sin ajuste. Bajalo si queres que lo descuente.
    no_docs_resale_factor: float = 1.0
    min_margin: float = 800.0
    min_samples: int = 3            # menos muestras que esto = referencia floja
    min_real_price: float = 1500.0  # una moto entera andando no cuesta menos
    survey_max_price: float = 60000.0  # tope al muestrear para la referencia

    def repair_cost(self, condition: str) -> float:
        return {"runner": self.repair_runner,
                "project": self.repair_project,
                "parts": self.repair_parts}.get(condition, self.repair_project)

    @classmethod
    def from_config(cls, data: dict) -> "Economics":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class Reference:
    model: str
    n: int
    median: float
    low: float
    high: float

    @property
    def solid(self) -> bool:
        return self.n >= 3


@dataclass
class Deal:
    listing: Listing
    appraisal: Appraisal
    reference: Reference | None
    repair: float
    margin: float | None
    warnings: list[str]

    @property
    def confidence(self) -> str:
        if self.reference is None:
            return "sin referencia"
        return "buena" if self.reference.solid else f"floja (n={self.reference.n})"


def build_references(listings: list[Listing],
                     floor: float = 1500.0) -> dict[str, Reference]:
    """Mediana de precio por modelo, SOLO de motos andando y con precio real.

    El piso descarta repuestos que se colaron con nombre de modelo: un aviso
    de R$190 que dice "gsx" es una lente de pisca, no una Suzuki.
    """
    buckets: dict[str, list[float]] = {}
    seen: set[str] = set()
    for l in listings:
        if l.uid in seen:
            continue
        seen.add(l.uid)
        a = appraise(l)
        if (a.kind == "moto" and a.condition == "runner" and a.model
                and not (a.wanted or a.shop_ad or a.doc_risk)
                and l.price and not a.bait_price and l.price >= floor):
            buckets.setdefault(a.model, []).append(l.price)

    refs: dict[str, Reference] = {}
    for model, prices in buckets.items():
        prices.sort()
        refs[model] = Reference(
            model=model,
            n=len(prices),
            median=statistics.median(prices),
            low=prices[len(prices) // 4],
            high=prices[(3 * len(prices)) // 4],
        )
    return refs


def evaluate(listing: Listing, refs: dict[str, Reference], econ: Economics) -> Deal:
    a = appraise(listing)
    ref = refs.get(a.model) if a.model else None

    # Un repuesto puede nombrar el modelo ("Escape Inox CBR 600F") y no es una
    # compra: sin esta guarda el margen sale disparatado.
    if a.kind != "moto":
        return Deal(listing=listing, appraisal=a, reference=ref,
                    repair=0.0, margin=None,
                    warnings=[f"no es una moto entera ({a.kind})"])

    # Guarda contra repuestos que nombran el modelo ("Escape Inox CBR 600F").
    # SOLO en la categoria de repuestos de OLX, que es de donde salian.
    #
    # Antes se aplicaba a todo por precio, y eso mataba justo las compras que
    # importan: una Biz a R$450 contra una referencia de R$12.500 quedaba
    # descartada como "repuesto" cuando es exactamente el negocio. Comprar al
    # 5% del valor de mercado no es una anomalia aca, es el objetivo.
    en_categoria_repuestos = "pecas-e-acessorios" in str(
        listing.raw.get("category", ""))
    if (en_categoria_repuestos and ref and a.condition == "runner"
            and listing.price is not None
            and listing.price < max(econ.min_real_price, 0.25 * ref.median)):
        return Deal(listing=listing, appraisal=a, reference=ref,
                    repair=0.0, margin=None,
                    warnings=["precio demasiado bajo para una moto andando: "
                              "es casi seguro un repuesto"])
    repair = econ.repair_cost(a.condition)

    warnings: list[str] = []
    if a.bait_price:
        warnings.append("precio señuelo: el desmanche publica R$1-10 para que llames")
    if a.doc_risk:
        warnings.append("sin papeles / sinistrada / deuda — vos decidís")
    if a.stolen_flag:
        warnings.append("PROCEDENCIA DUDOSA: no la toques")
    if a.year is None:
        warnings.append("sin año en el título")
    if ref and not ref.solid:
        warnings.append(f"referencia con pocas muestras (n={ref.n})")

    margin = None
    if ref and ref.n >= econ.min_samples and listing.price is not None and not a.bait_price:
        factor = econ.resale_factor
        if a.doc_risk:
            factor *= econ.no_docs_resale_factor
        margin = (ref.median * factor) - listing.price - repair

    return Deal(listing=listing, appraisal=a, reference=ref,
                repair=repair, margin=margin, warnings=warnings)


def assembly_clusters(deals: list[Deal], min_units: int = 2) -> dict[str, list[Deal]]:
    """Dos o mas doadores del mismo modelo = podes armar una entera.

    Es el caso que mas margen deja: dos motos muertas iguales suelen costar
    menos que una viva, y entre las dos sale una completa.
    """
    by_model: dict[str, list[Deal]] = {}
    for d in deals:
        if d.appraisal.model and d.appraisal.condition in ("parts", "project"):
            by_model.setdefault(d.appraisal.model, []).append(d)
    return {m: ds for m, ds in by_model.items() if len(ds) >= min_units}
