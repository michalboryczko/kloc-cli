"""Query result types."""

from dataclasses import dataclass, field
from typing import Optional

from .node import NodeData


@dataclass
class ResolveResult:
    """Result of symbol resolution."""

    query: str
    candidates: list[NodeData]

    @property
    def found(self) -> bool:
        return len(self.candidates) > 0

    @property
    def unique(self) -> bool:
        return len(self.candidates) == 1


@dataclass
class UsageEntry:
    """Single usage entry with tree support."""

    depth: int
    node_id: str
    fqn: str
    file: Optional[str]
    line: Optional[int]
    children: list["UsageEntry"] = field(default_factory=list)


@dataclass
class UsagesTreeResult:
    """Result of usages query with tree structure."""

    target: NodeData
    max_depth: int
    tree: list[UsageEntry] = field(default_factory=list)


@dataclass
class DepsEntry:
    """Single dependency entry with tree support."""

    depth: int
    node_id: str
    fqn: str
    file: Optional[str]
    line: Optional[int]
    children: list["DepsEntry"] = field(default_factory=list)


@dataclass
class DepsTreeResult:
    """Result of deps query with tree structure."""

    target: NodeData
    max_depth: int
    tree: list[DepsEntry] = field(default_factory=list)


# Legacy result types for backward compatibility
@dataclass
class UsageResult:
    """Single usage of a symbol (legacy flat format)."""

    file: Optional[str]
    line: Optional[int]
    referrer_fqn: str
    referrer_id: str


@dataclass
class DepsResult:
    """Single dependency of a symbol (legacy flat format)."""

    file: Optional[str]
    line: Optional[int]
    target_fqn: str
    target_id: str


@dataclass
class ContextEntry:
    """Single entry in context tree (used_by or uses)."""

    depth: int
    node_id: str
    fqn: str
    kind: Optional[str] = None
    file: Optional[str] = None
    line: Optional[int] = None
    signature: Optional[str] = None  # Method/function signature if available
    children: list["ContextEntry"] = field(default_factory=list)
    # For interfaces/methods: their implementations (USES direction)
    implementations: list["ContextEntry"] = field(default_factory=list)
    # For concrete methods: marks this as an interface method grouping (USED BY direction)
    # When True, this entry represents an interface method and children are usages via that interface
    via_interface: bool = False


@dataclass
class ContextResult:
    """Result of context query with tree structure."""

    target: NodeData
    max_depth: int
    used_by: list[ContextEntry] = field(default_factory=list)
    uses: list[ContextEntry] = field(default_factory=list)


@dataclass
class OwnersResult:
    """Result of ownership chain query."""

    chain: list[NodeData]


@dataclass
class InheritEntry:
    """Single entry in inheritance tree with depth support."""

    depth: int
    node_id: str
    fqn: str
    kind: str
    file: Optional[str]
    line: Optional[int]
    children: list["InheritEntry"] = field(default_factory=list)


@dataclass
class InheritTreeResult:
    """Result of inheritance query with tree structure."""

    root: NodeData
    direction: str
    max_depth: int
    tree: list[InheritEntry] = field(default_factory=list)


# Legacy result type for backward compatibility
@dataclass
class InheritResult:
    """Result of inheritance query (legacy flat format)."""

    root: NodeData
    direction: str
    chain: list[NodeData]


@dataclass
class OverrideEntry:
    """Single entry in override tree with depth support."""

    depth: int
    node_id: str
    fqn: str
    file: Optional[str]
    line: Optional[int]
    children: list["OverrideEntry"] = field(default_factory=list)


@dataclass
class OverridesTreeResult:
    """Result of overrides query with tree structure."""

    root: NodeData
    direction: str
    max_depth: int
    tree: list[OverrideEntry] = field(default_factory=list)


# Legacy result type for backward compatibility
@dataclass
class OverridesResult:
    """Result of overrides query (legacy flat format)."""

    root: NodeData
    direction: str
    chain: list[NodeData]
