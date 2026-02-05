"""Edge data model."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class EdgeData:
    """Edge from SoT JSON."""

    type: str
    source: str
    target: str
    location: Optional[dict] = None
    position: Optional[int] = None  # For argument edges: 0-based argument index

    @property
    def location_str(self) -> Optional[str]:
        """Return file:line string if location exists."""
        if self.location:
            file = self.location.get("file", "")
            line = self.location.get("line", 0)
            return f"{file}:{line + 1}"  # 1-based
        return None
