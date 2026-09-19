from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from typing import Iterable, Protocol

from ..config import Config
from ..models import Listing


class Source(Protocol):
    name: str

    def fetch(self, cfg: Config, opts: dict) -> Iterable[Listing]:
        ...


class BaseSource(abc.ABC):
    """Contrato de una fuente, obligatorio en runtime.

    Antes `collect` leia `failed_pages`, `dom_failures`, `errors` y
    `expected_origins` con `getattr` y valores por defecto: una fuente que se
    olvidara de declararlos informaba salud OPTIMISTA en silencio. Mercado Livre
    era ese caso: devolvia `{"status": "ok", "observed": 0}`, o sea "hoy no hay
    motos baratas", ante un cambio de API. Una ABC obliga de verdad; un Protocol
    no obliga a nada sin un type checker, y aqui no hay ninguno configurado.
    """

    name: str = ""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """REGISTRY guarda una sola instancia: los contadores son por corrida."""
        self.failed_pages = 0
        self.dom_failures = 0
        self.pages_read = 0
        self.errors: list[str] = []
        # Superficies que ESTA corrida va a leer, declaradas antes de leerlas
        # para que una que observe 0 aparezca con 0 en el diagnostico.
        self.expected_origins: list[str] = []

    def note(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def note_failed_page(self, where: str, exc: Exception | None = None) -> None:
        self.failed_pages += 1
        detalle = f": {type(exc).__name__}" if exc is not None else ""
        self.note(f"{where} fallo{detalle}")

    def note_blind(self, where: str, detail: object = "") -> None:
        """La pagina cargo pero no se reconocio nada de lo que deberia estar."""
        self.dom_failures += 1
        self.failed_pages += 1
        self.note(f"DOM no reconocido en {where}")
        print(f"   ! DOM no reconocido en {where}: sin contenido reconocible ni "
              f"cartel de vacio ({detail}). Cero resultados NO significa que no "
              f"haya ofertas.", flush=True)

    def require_empty_evidence(self, encontrados: int, evidencia_de_vacio: bool,
                               where: str, detail: object = "") -> None:
        """Cero resultados exige evidencia de que la pagina estaba vacia.

        Sin esta comprobacion, un cambio de HTML o de API es indistinguible de
        una tarde tranquila, y es el fallo mas caro del sistema: el radar parece
        sano y no ve nada.
        """
        if not encontrados and not evidencia_de_vacio:
            self.note_blind(where, detail)

    @abc.abstractmethod
    def fetch(self, cfg: Config, opts: dict) -> Iterable[Listing]:
        ...


class SourceError(RuntimeError):
    """Fallo recuperable de una fuente: se loguea y se sigue con las demas."""


class SessionExpired(SourceError):
    """La sesion del proveedor murio o pide verificacion.

    Se distingue del resto porque reintentar cada 30 minutos no arregla nada
    y ademas golpea la cuenta: el vigilante pausa la fuente y avisa.
    """


@dataclass
class SourceOutcome:
    status: str  # ok | partial | error
    observed: int = 0
    discarded: int = 0
    failed_pages: int = 0
    dom_failures: int = 0
    session_expired: bool = False
    # Observaciones por SUPERFICIE, no por fuente. Lo que se rompe es una mitad
    # del adaptador: con Marketplace trayendo 150 avisos, los 8 grupos podian
    # estar ciegos semanas sin que la racha de silencio arrancara nunca.
    observed_by_origin: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def outcome_status(value) -> str:
    if isinstance(value, dict):
        return str(value.get("status", "unknown"))
    text = str(value)
    return "error" if text.startswith("ERROR") else "ok"


def format_outcome(value) -> str:
    if not isinstance(value, dict):
        return str(value)
    status = value.get("status", "unknown").upper()
    counts = f"{value.get('observed', 0)} observados / {value.get('discarded', 0)} descartados"
    pages = f" / {value.get('failed_pages', 0)} paginas fallidas" if value.get("failed_pages") else ""
    dom = f" / {value.get('dom_failures', 0)} con DOM no reconocido" if value.get("dom_failures") else ""
    sesion = " / SESION CAIDA" if value.get("session_expired") else ""
    errors = "; ".join(str(e) for e in value.get("errors", []))
    return f"{status}: {counts}{pages}{dom}{sesion}" + (f" / {errors}" if errors else "")
