"""JSON loading utilities for SoT files.

Uses msgspec for ~5-10x faster JSON parsing compared to stdlib json.
"""

from pathlib import Path
from typing import Any, Optional

import msgspec


class RangeSpec(msgspec.Struct, omit_defaults=True):
    """Range specification in SoT JSON."""

    start_line: int
    start_col: int
    end_line: int
    end_col: int


class LocationSpec(msgspec.Struct, omit_defaults=True):
    """Location specification in SoT JSON."""

    file: str
    line: int
    col: Optional[int] = None


class NodeSpec(msgspec.Struct, omit_defaults=True):
    """Node specification in SoT JSON."""

    id: str
    kind: str
    name: str
    fqn: str
    symbol: str
    file: Optional[str] = None
    range: Optional[dict] = None  # Keep as dict for compatibility
    documentation: list[str] = []

    # Value node fields (sot.json v2.0)
    value_kind: Optional[str] = None    # "parameter", "local", "result", "literal", "constant"
    type_symbol: Optional[str] = None   # SCIP symbol of the value's type

    # Call node fields (sot.json v2.0)
    call_kind: Optional[str] = None     # "method", "method_static", "constructor", etc.


class EdgeSpec(msgspec.Struct, omit_defaults=True):
    """Edge specification in SoT JSON."""

    type: str
    source: str
    target: str
    location: Optional[dict] = None  # Keep as dict for compatibility
    position: Optional[int] = None   # For argument edges: 0-based argument index


class SoTSpec(msgspec.Struct, omit_defaults=True):
    """Full SoT JSON specification."""

    version: str = "1.0"
    metadata: dict = {}
    nodes: list[NodeSpec] = []
    edges: list[EdgeSpec] = []


# Create reusable decoder for performance
_decoder = msgspec.json.Decoder(SoTSpec)


def load_sot(path: str | Path) -> dict[str, Any]:
    """Load SoT JSON from file.

    Uses msgspec for fast parsing (~5-10x faster than stdlib json).

    Args:
        path: Path to the SoT JSON file.

    Returns:
        Parsed JSON data as a dictionary.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        msgspec.DecodeError: If the file is not valid JSON.
    """
    with open(path, "rb") as f:
        data = _decoder.decode(f.read())

    # Convert to dict format expected by the rest of the codebase
    return {
        "version": data.version,
        "metadata": data.metadata,
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind,
                "name": n.name,
                "fqn": n.fqn,
                "symbol": n.symbol,
                "file": n.file,
                "range": n.range,
                "documentation": n.documentation,
                # v2.0 fields
                "value_kind": n.value_kind,
                "type_symbol": n.type_symbol,
                "call_kind": n.call_kind,
            }
            for n in data.nodes
        ],
        "edges": [
            {
                "type": e.type,
                "source": e.source,
                "target": e.target,
                "location": e.location,
                "position": e.position,
            }
            for e in data.edges
        ],
    }


def load_sot_raw(path: str | Path) -> SoTSpec:
    """Load SoT JSON and return typed msgspec struct.

    This is useful when you want to work with the typed structs directly
    instead of dictionaries.

    Args:
        path: Path to the SoT JSON file.

    Returns:
        SoTSpec typed struct.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        msgspec.DecodeError: If the file is not valid JSON.
    """
    with open(path, "rb") as f:
        return _decoder.decode(f.read())
