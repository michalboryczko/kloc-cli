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
class MemberRef:
    """A specific member usage reference within a USES relationship.

    When a source uses members of a class (properties, methods), each reference
    is captured here so the output shows the execution flow - what specific
    members are accessed and where.
    """

    target_name: str  # Short display name: "$prop", "method()"
    target_fqn: str  # Full FQN: "App\\Foo::method()"
    target_kind: Optional[str] = None  # "Method", "Property", etc.
    file: Optional[str] = None  # Where the reference occurs
    line: Optional[int] = None  # Line of the reference (0-indexed)
    # Reference type classification
    reference_type: Optional[str] = None  # "method_call", "type_hint", "instantiation", etc.
    # Access chain showing receiver expression
    access_chain: Optional[str] = None  # "$this->orderRepository" or None
    # R4: FQN of the intermediate property in the access chain
    access_chain_symbol: Optional[str] = None  # "App\\Foo::$orderRepository" or None


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
    # When this entry represents a member usage (not direct class usage),
    # member_ref identifies which specific member is being referenced
    member_ref: Optional["MemberRef"] = None


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
