"""JSON loading utilities for SoT files.

Uses msgspec for ~5-10x faster JSON parsing compared to stdlib json.
"""

import re
from pathlib import Path
from typing import Any, Optional

import msgspec

_RE_VISIBILITY = re.compile(
    r'^(?:public\s+|protected\s+|private\s+|static\s+|final\s+|abstract\s+)*function\s+'
)
_RE_ATTRIBUTES = re.compile(r'#\[[^\]]*\]\s*')
_RE_WHITESPACE = re.compile(r'\s+')


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
    """Node specification in SoT JSON.

    Includes computed properties (start_line, signature, display_name, location_str)
    so it can be used directly without conversion to NodeData.
    """

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

    # NodeData compatibility: enclosing_range (not in SoT JSON, but kept for API compat)
    enclosing_range: Optional[dict] = None

    @property
    def start_line(self) -> Optional[int]:
        if self.range:
            return self.range.get("start_line")
        return None

    @property
    def location_str(self) -> str:
        """Return file:line string."""
        if self.file and self.start_line is not None:
            return f"{self.file}:{self.start_line + 1}"  # 1-based
        elif self.file:
            return self.file
        return "<unknown>"

    @property
    def signature(self) -> Optional[str]:
        """Extract method/function signature from documentation."""
        if not self.documentation or self.kind not in ("Method", "Function"):
            return None

        for doc in self.documentation:
            clean = doc.replace("```php", "").replace("```", "").strip()
            if "function " in clean:
                sig_lines = []
                capturing = False
                for line in clean.split("\n"):
                    line = line.strip()
                    if "function " in line:
                        capturing = True
                    if capturing:
                        sig_lines.append(line)
                        if ")" in line:
                            break

                if not sig_lines:
                    continue

                full_sig = " ".join(sig_lines)
                full_sig = _RE_VISIBILITY.sub('', full_sig)
                full_sig = _RE_ATTRIBUTES.sub('', full_sig)
                full_sig = _RE_WHITESPACE.sub(' ', full_sig).strip()

                if "(" in full_sig and ")" in full_sig:
                    return full_sig
                if "(" in full_sig:
                    method_name = full_sig.split("(")[0]
                    return f"{method_name}(...)"
                return full_sig
        return None

    @property
    def display_name(self) -> str:
        """Return display name - signature for methods, FQN otherwise."""
        if self.kind in ("Method", "Function") and self.signature:
            if "::" in self.fqn:
                class_part = self.fqn.rsplit("::", 1)[0]
                return f"{class_part}::{self.signature}"
            return self.signature
        return self.fqn


class EdgeSpec(msgspec.Struct, omit_defaults=True):
    """Edge specification in SoT JSON."""

    type: str
    source: str
    target: str
    location: Optional[dict] = None  # Keep as dict for compatibility
    position: Optional[int] = None   # For argument edges: 0-based argument index
    expression: Optional[str] = None  # For argument edges: source expression text
    parameter: Optional[str] = None  # For argument edges: formal parameter FQN

    @property
    def location_str(self) -> Optional[str]:
        """Return file:line string if location exists."""
        if self.location:
            file = self.location.get("file", "")
            line = self.location.get("line", 0)
            return f"{file}:{line + 1}"  # 1-based
        return None


class SoTSpec(msgspec.Struct, omit_defaults=True):
    """Full SoT JSON specification."""

    version: str = "1.0"
    metadata: dict = {}
    nodes: list[NodeSpec] = []
    edges: list[EdgeSpec] = []


# Create reusable decoder for performance
_decoder = msgspec.json.Decoder(SoTSpec)


def load_sot(path: str | Path) -> SoTSpec:
    """Load SoT JSON from file.

    Uses msgspec for fast parsing (~5-10x faster than stdlib json).
    Returns the typed SoTSpec struct directly to avoid intermediate dict creation.

    Args:
        path: Path to the SoT JSON file.

    Returns:
        Parsed SoTSpec struct.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        msgspec.DecodeError: If the file is not valid JSON.
    """
    with open(path, "rb") as f:
        return _decoder.decode(f.read())


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
