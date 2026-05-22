from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from codemapy.models import ImportRef


class DependencyExtractor(ABC):
    language: str

    @abstractmethod
    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        """Return import references found in path."""
