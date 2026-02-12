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
    # v4 ISSUE-B: Variable identity for Value receivers
    on_kind: Optional[str] = None  # "local" or "param" for Value receivers
    on_file: Optional[str] = None  # File where Value is defined
    on_line: Optional[int] = None  # Line where Value is defined (0-indexed)


@dataclass
class ArgumentInfo:
    """Argument-to-parameter mapping at a call site.

    Maps an actual argument value at a call site to the formal parameter
    of the callee method/constructor.
    """

    position: int  # 0-based argument position
    param_name: Optional[str] = None  # Formal parameter name from callee (e.g., "$productId")
    value_expr: Optional[str] = None  # Source expression text (e.g., "$input->productId")
    value_source: Optional[str] = None  # Value kind: "parameter", "local", "literal", "result"
    value_type: Optional[str] = None  # Resolved type name(s) from type_of edges (e.g., "Order", "int|string")
    # ISSUE-D: Rich argument display
    param_fqn: Optional[str] = None  # Full FQN of callee's Argument node (e.g., "Order::__construct().$id")
    value_ref_symbol: Optional[str] = None  # Graph symbol the value resolves to (e.g., "local#32$order")
    source_chain: Optional[list] = None  # Access chain steps when value has no top-level entry


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
    # Phase 2: Argument-to-parameter mappings for calls (empty if not a call or no args)
    arguments: list["ArgumentInfo"] = field(default_factory=list)
    # Phase 2: Name of local variable that receives this call's result
    result_var: Optional[str] = None
    # ISSUE-C: Variable-centric flow — entry type and variable metadata
    entry_type: Optional[str] = None  # "call" or "local_variable"
    variable_name: Optional[str] = None  # "$order" for Kind 1 entries
    variable_symbol: Optional[str] = None  # "local#32$order" for Kind 1 entries
    variable_type: Optional[str] = None  # "Order" for Kind 1 entries
    source_call: Optional["ContextEntry"] = None  # Nested call for Kind 1 entries
    # ISSUE-E: Cross-method boundary crossing indicator
    crossed_from: Optional[str] = None  # FQN of the parameter crossed from


@dataclass
class ContextResult:
    """Result of context query with tree structure."""

    target: NodeData
    max_depth: int
    used_by: list[ContextEntry] = field(default_factory=list)
    uses: list[ContextEntry] = field(default_factory=list)
    definition: Optional["DefinitionInfo"] = None


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


@dataclass
class DefinitionInfo:
    """Symbol definition metadata for the DEFINITION section.

    Provides structural information about a symbol: its signature, typed
    arguments, return type, containing class, properties, methods, and
    inheritance relationships.
    """

    fqn: str
    kind: str
    file: Optional[str] = None
    line: Optional[int] = None
    signature: Optional[str] = None
    arguments: list[dict] = field(default_factory=list)
    return_type: Optional[dict] = None
    declared_in: Optional[dict] = None
    properties: list[dict] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    extends: Optional[str] = None
    implements: list[str] = field(default_factory=list)
    uses_traits: list[str] = field(default_factory=list)
    # ISSUE-B: Value-specific fields
    value_kind: Optional[str] = None      # "local", "parameter", "result", "literal", "constant"
    type_info: Optional[dict] = None      # {"fqn": ..., "name": ...}
    source: Optional[dict] = None         # {"call_fqn": ..., "method_fqn": ..., "method_name": ..., ...}
