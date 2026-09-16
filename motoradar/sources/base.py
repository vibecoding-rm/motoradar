from __future__ import annotations

from typing import Iterable, Protocol

from ..config import Config
from ..models import Listing


class Source(Protocol):
    name: str

    def fetch(self, cfg: Config, opts: dict) -> Iterable[Listing]:
        ...


class SourceError(RuntimeError):
    """Fallo recuperable de una fuente: se loguea y se sigue con las demas."""
