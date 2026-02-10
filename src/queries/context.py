"""Context query with BFS depth expansion and tree structure."""

from typing import Optional, TYPE_CHECKING

from ..models import ContextResult, ContextEntry, MemberRef, InheritEntry, OverrideEntry, NodeData, ArgumentInfo, DefinitionInfo
from ..models.edge import EdgeData
from .base import Query

if TYPE_CHECKING:
    from ..graph import SoTIndex


# =============================================================================
# Depth Chaining Rules (R8)
# =============================================================================
# Only these reference types represent actual call/data flow relationships
# and should be followed when expanding USED BY depth N -> N+1.
# Structural/declarative references (type_hint, extends, implements, use_trait)
# are leaf nodes -- they do not imply that callers of the source are callers
# of the target.
CHAINABLE_REFERENCE_TYPES = {"method_call", "property_access", "instantiation", "static_call"}


# =============================================================================
# Graph-based Access Chain Building
# =============================================================================

def build_access_chain(index: "SoTIndex", call_node_id: str, max_depth: int = 10) -> Optional[str]:
    """Build access chain string by traversing receiver edges in the graph.

    For a call like `$this->orderRepository->save()`, traverses receiver edges
    to build the chain "$this->orderRepository".

    Args:
        index: The SoT index with graph data.
        call_node_id: ID of the Call node.
        max_depth: Maximum traversal depth to prevent infinite loops.

    Returns:
        Access chain string like "$this->orderRepository" or None if no receiver.
    """
    call_node = index.nodes.get(call_node_id)
    if not call_node or call_node.kind != "Call":
        return None

    receiver_id = index.get_receiver(call_node_id)
    if not receiver_id:
        return None  # Static call or constructor

    return _build_chain_from_value(index, receiver_id, max_depth)


def _build_chain_from_value(index: "SoTIndex", value_id: str, max_depth: int) -> str:
    """Build chain by following value references.

    Args:
        index: The SoT index.
        value_id: Starting value node ID.
        max_depth: Maximum recursion depth.

    Returns:
        Chain string like "$this->repo" or "$param" or "?".
    """
    if max_depth <= 0:
        return "?"

    value_node = index.nodes.get(value_id)
    if not value_node or value_node.kind != "Value":
        return "?"

    value_kind = value_node.value_kind

    if value_kind == "parameter":
        # Return the parameter name
        return value_node.name

    if value_kind == "local":
        # Return the local variable name
        return value_node.name

    if value_kind == "result":
        # Result of a call - follow to source call
        source_call_id = index.get_source_call(value_id)
        if source_call_id:
            source_call = index.nodes.get(source_call_id)
            if source_call and source_call.kind == "Call":
                # Get the method/property name being accessed
                target_id = index.get_call_target(source_call_id)
                target_node = index.nodes.get(target_id) if target_id else None
                member_name = target_node.name if target_node else "?"
                # Strip $ from property names if present
                if member_name.startswith("$"):
                    member_name = member_name[1:]

                # For property access, format as chain
                if source_call.call_kind == "access":
                    # Recurse to get receiver chain
                    receiver_id = index.get_receiver(source_call_id)
                    if receiver_id:
                        receiver_chain = _build_chain_from_value(index, receiver_id, max_depth - 1)
                        return f"{receiver_chain}->{member_name}"
                    # No receiver - this is $this-> access (implicit receiver in PHP)
                    return f"$this->{member_name}"

                # For method calls, show as method()
                if source_call.call_kind in ("method", "method_static"):
                    receiver_id = index.get_receiver(source_call_id)
                    if receiver_id:
                        receiver_chain = _build_chain_from_value(index, receiver_id, max_depth - 1)
                        return f"{receiver_chain}->{member_name}()"
                    # No receiver - this is $this-> method call
                    return f"$this->{member_name}()"

        return "?"

    if value_kind == "literal":
        return "(literal)"

    if value_kind == "constant":
        return value_node.name

    return "?"


def get_reference_type_from_call(index: "SoTIndex", call_node_id: str) -> str:
    """Get reference type from a Call node's call_kind.

    Maps call_kind to human-readable reference types.
    """
    call_node = index.nodes.get(call_node_id)
    if not call_node or call_node.kind != "Call":
        return "unknown"

    kind_map = {
        "method": "method_call",
        "method_static": "static_call",
        "constructor": "instantiation",
        "access": "property_access",
        "access_static": "static_property",
        "function": "function_call",
    }
    return kind_map.get(call_node.call_kind or "", "unknown")


def _call_matches_target(index: "SoTIndex", call_id: str, target_id: str) -> bool:
    """Check if a Call node's callee matches the expected usage target.

    Handles the special case of constructor calls: when `new Foo()` produces
    a Call node whose callee is `Foo::__construct()`, and the uses edge target
    is the `Foo` class itself, the match is accepted because the constructor
    call represents an instantiation of that class.

    Args:
        index: The SoT index.
        call_id: ID of the Call node to check.
        target_id: Expected target node ID from the uses edge.

    Returns:
        True if the Call node's callee matches the target.
    """
    call_target = index.get_call_target(call_id)
    if call_target == target_id:
        return True

    # Constructor special case: Call targets __construct() but uses edge
    # targets the Class node itself. Accept if the constructor's containing
    # class matches the target.
    call_node = index.nodes.get(call_id)
    if call_node and call_node.call_kind == "constructor" and call_target:
        constructor_class = index.get_contains_parent(call_target)
        if constructor_class == target_id:
            return True

    return False


def find_call_for_usage(index: "SoTIndex", source_id: str, target_id: str, file: Optional[str], line: Optional[int]) -> Optional[str]:
    """Find a Call node that matches a usage edge's location.

    Args:
        index: The SoT index.
        source_id: Source node ID of the usage.
        target_id: Target node ID of the usage.
        file: File path from the edge location.
        line: Line number from the edge location (0-based).

    Returns:
        Call node ID if found, None otherwise.
    """
    # Get all calls that target this node
    calls = index.get_calls_to(target_id)

    # Also check if there are Call nodes contained by the source
    source_children = index.get_contains_children(source_id)
    call_children = [c for c in source_children if index.nodes.get(c) and index.nodes[c].kind == "Call"]

    # Filter by location if provided
    if file and line is not None:
        for call_id in calls + call_children:
            call_node = index.nodes.get(call_id)
            if call_node and call_node.file == file:
                if call_node.range:
                    call_line = call_node.range.get("start_line", -1)
                    if call_line == line:
                        # Verify the Call node's callee matches the usage target
                        # to prevent returning a wrong Call (e.g., constructor
                        # matched for a static property access on the same line)
                        if _call_matches_target(index, call_id, target_id):
                            return call_id

        # Constructor fallback: search call_children for constructor Call nodes
        # whose callee's containing class matches the target_id. This handles
        # the case where get_calls_to(target_id) returns nothing because
        # constructor Calls target __construct(), not the Class itself.
        # Allow +/- 1 line tolerance because `uses` edge location may refer
        # to the class name token while the Call node range covers `new X(...)`.
        for call_id in call_children:
            call_node = index.nodes.get(call_id)
            if (call_node and call_node.call_kind == "constructor"
                    and call_node.file == file):
                if call_node.range:
                    call_line = call_node.range.get("start_line", -1)
                    if abs(call_line - line) <= 1:
                        if _call_matches_target(index, call_id, target_id):
                            return call_id

    # If no location match, try to find any call from source to target
    for call_id in calls:
        call_node = index.nodes.get(call_id)
        if call_node:
            # Check if this call is contained in the source
            container_id = index.get_contains_parent(call_id)
            if container_id == source_id:
                # Verify the Call node's callee matches the usage target
                if _call_matches_target(index, call_id, target_id):
                    return call_id

    return None


def get_containing_scope(index: "SoTIndex", call_node_id: str) -> Optional[str]:
    """Get the containing method/function for a Call node.

    Traverses the containment hierarchy to find the Method or Function
    that contains this call.

    Args:
        index: The SoT index.
        call_node_id: ID of the Call node.

    Returns:
        Node ID of the containing Method/Function, or None if not found.
    """
    current_id = call_node_id
    max_depth = 10  # Prevent infinite loops

    for _ in range(max_depth):
        parent_id = index.get_contains_parent(current_id)
        if not parent_id:
            return None

        parent_node = index.nodes.get(parent_id)
        if not parent_node:
            return None

        if parent_node.kind in ("Method", "Function"):
            return parent_id

        # Continue up the hierarchy
        current_id = parent_id

    return None


def _is_import_reference(entry, index: "SoTIndex") -> bool:
    """Identify whether a context entry represents a PHP import/use statement (R1).

    An import reference is identified by:
    - The source node is a File node (kind == "File")
    - The reference type is "type_hint"
    - This indicates a file-level `use App\\Foo\\Bar;` declaration

    Non-import type hints (constructor parameters, method return types) come from
    Method/Function sources, not File sources, so they are NOT filtered.

    Args:
        entry: A ContextEntry to check.
        index: The SoT index for node lookups.

    Returns:
        True if the entry represents an import/use statement.
    """
    if not entry.member_ref:
        return False

    if entry.member_ref.reference_type != "type_hint":
        return False

    # Check if the source node is a File node
    source_node = index.nodes.get(entry.node_id)
    if source_node and source_node.kind == "File":
        return True

    return False


def resolve_access_chain_symbol(index: "SoTIndex", call_node_id: str) -> Optional[str]:
    """Resolve the property FQN from a Call node's receiver chain (R4).

    For a call like `$this->orderService->createOrder()`, the receiver is a
    property access to `$orderService`. This function finds the FQN of that
    intermediate property (e.g., `App\\Controller\\OrderController::$orderService`).

    Args:
        index: The SoT index.
        call_node_id: ID of the Call node.

    Returns:
        Property FQN string if resolved, None otherwise.
    """
    call_node = index.nodes.get(call_node_id)
    if not call_node or call_node.kind != "Call":
        return None

    receiver_id = index.get_receiver(call_node_id)
    if not receiver_id:
        return None

    receiver_node = index.nodes.get(receiver_id)
    if not receiver_node or receiver_node.kind != "Value":
        return None

    # If the receiver is the result of a property access call, follow to the property
    if receiver_node.value_kind == "result":
        source_call_id = index.get_source_call(receiver_id)
        if source_call_id:
            source_call = index.nodes.get(source_call_id)
            if source_call and source_call.kind == "Call" and source_call.call_kind == "access":
                # This is a property access -- get the target property
                target_id = index.get_call_target(source_call_id)
                if target_id:
                    target_node = index.nodes.get(target_id)
                    if target_node and target_node.kind == "Property":
                        return target_node.fqn

    return None


def _infer_reference_type(edge: EdgeData, target_node: Optional[NodeData], index: Optional["SoTIndex"] = None) -> str:
    """Infer reference type from edge type and target node kind.

    Without calls.json, we can only infer based on sot.json edge metadata.
    This provides a best-effort classification that may be ambiguous in some cases.

    When an index is provided, type_hint references to Class/Interface/Trait/Enum
    targets are further distinguished by checking the source node kind:
    - Argument source -> parameter_type
    - Method/Function source -> return_type
    - Property source -> property_type
    - Other/unknown -> type_hint (fallback)

    Reference Type Inference Rules:
    | Edge Type | Target Kind | Inferred Reference Type |
    |-----------|-------------|------------------------|
    | extends   | Class       | extends                |
    | implements| Interface   | implements             |
    | uses_trait| Trait       | uses_trait             |
    | uses      | Method      | method_call (could be static) |
    | uses      | Property    | property_access or type_hint |
    | uses      | Class       | parameter_type / return_type / property_type / type_hint |

    Args:
        edge: The edge data from sot.json
        target_node: The target node of the edge (if resolved)
        index: Optional SoT index for source node lookup (enables param/return type distinction)

    Returns:
        A reference type string (e.g., "method_call", "parameter_type", "extends")
    """
    # Direct edge type mappings
    if edge.type == "extends":
        return "extends"
    if edge.type == "implements":
        return "implements"
    if edge.type == "uses_trait":
        return "uses_trait"

    # For 'uses' edges, infer from target node kind
    if edge.type == "uses" and target_node:
        kind = target_node.kind
        if kind == "Method":
            # Could be method_call or static_call - can't distinguish without calls.json
            return "method_call"
        if kind == "Property":
            # Could be property_access or type_hint for property declarations
            return "property_access"
        if kind in ("Class", "Interface", "Trait", "Enum"):
            # Distinguish parameter_type / return_type / property_type when index available.
            # For 'uses' edges, the source is typically the containing Method/Class/File.
            # To determine if a Class reference is a param type vs return type, we check
            # the type_hint edges: if an Argument of the source method has a type_hint
            # to this target, it's parameter_type; if the Method itself has a type_hint
            # to the target, it's return_type; if a Property has a type_hint, it's property_type.
            if index is not None:
                source_node = index.nodes.get(edge.source)
                if source_node:
                    if source_node.kind == "Argument":
                        return "parameter_type"
                    if source_node.kind == "Property":
                        return "property_type"
                    if source_node.kind in ("Method", "Function"):
                        # Check type_hint edges to distinguish param vs return type.
                        # First check if any Argument child of this method has a
                        # type_hint edge to the target (parameter_type).
                        target_id = edge.target
                        method_id = edge.source
                        has_param_type_hint = False
                        has_return_type_hint = False
                        for child_id in index.get_contains_children(method_id):
                            child = index.nodes.get(child_id)
                            if child and child.kind == "Argument":
                                for th_edge in index.outgoing[child_id].get("type_hint", []):
                                    if th_edge.target == target_id:
                                        has_param_type_hint = True
                                        break
                            if has_param_type_hint:
                                break
                        # Check if the method itself has a type_hint to the target (return_type)
                        for th_edge in index.outgoing[method_id].get("type_hint", []):
                            if th_edge.target == target_id:
                                has_return_type_hint = True
                                break
                        if has_param_type_hint:
                            return "parameter_type"
                        if has_return_type_hint:
                            return "return_type"
                    if source_node.kind in ("Class", "Interface", "Trait", "Enum"):
                        # Class-level query: check if any Property child has a
                        # type_hint edge to the target (property_type).
                        target_id = edge.target
                        class_id = edge.source
                        for child_id in index.get_contains_children(class_id):
                            child = index.nodes.get(child_id)
                            if child and child.kind == "Property":
                                for th_edge in index.outgoing[child_id].get("type_hint", []):
                                    if th_edge.target == target_id:
                                        return "property_type"
            return "type_hint"
        if kind == "Constant":
            return "constant_access"
        if kind == "Function":
            return "function_call"
        if kind == "Argument":
            # Usage of a method/function argument (parameter reference)
            return "argument_ref"
        if kind == "Variable":
            # Local variable usage
            return "variable_ref"

    # Fallback for unknown edge types or missing target node
    return "uses"


class ContextQuery(Query[ContextResult]):
    """Get combined usages and dependencies with depth expansion.

    Returns proper nested tree structures for both directions.
    Optionally includes polymorphic analysis:
    - USES direction: Shows implementations of interfaces and overriding methods
    - USED BY direction: Includes usages of interface methods that concrete methods implement
    """

    # Kinds that can have implementations (descendants)
    INHERITABLE_KINDS = {"Class", "Interface", "Trait", "Enum"}

    def execute(
        self, node_id: str, depth: int = 1, limit: int = 100, include_impl: bool = False,
        direct_only: bool = False, with_imports: bool = False
    ) -> ContextResult:
        """Execute context query with BFS tree building.

        Args:
            node_id: Node ID to get context for.
            depth: BFS depth for expansion.
            limit: Maximum results per direction.
            include_impl: If True, enables polymorphic analysis:
                         - USES: attaches implementations/overrides for interfaces/methods
                         - USED BY: includes usages of interface methods that concrete methods implement
            direct_only: If True, USED BY shows only direct references to the symbol itself,
                        excluding usages that only reference its members.
            with_imports: If True, include PHP import/use statements in USED BY output.
                         By default (False), import statements are hidden.

        Returns:
            ContextResult with tree structures for used_by and uses.
        """
        target_node = self.index.nodes.get(node_id)
        if not target_node:
            raise ValueError(f"Node not found: {node_id}")

        definition = self._build_definition(node_id)
        used_by = self._build_incoming_tree(
            node_id, depth, limit, include_impl, direct_only, with_imports
        )
        uses = self._build_outgoing_tree(node_id, depth, limit, include_impl)

        return ContextResult(
            target=target_node,
            max_depth=depth,
            used_by=used_by,
            uses=uses,
            definition=definition,
        )

    @staticmethod
    def _member_display_name(node: NodeData) -> str:
        """Format a short member display name: '$prop', 'method()', 'CONST'."""
        if node.kind == "Method" or node.kind == "Function":
            return f"{node.name}()"
        if node.kind == "Property":
            name = node.name
            return name if name.startswith("$") else f"${name}"
        return node.name

    def _build_definition(self, node_id: str) -> DefinitionInfo:
        """Build definition metadata for a symbol.

        Gathers structural information about the symbol: signature, typed
        arguments, return type, containing class, properties, methods,
        and inheritance relationships.

        Args:
            node_id: The node to build definition for.

        Returns:
            DefinitionInfo with symbol metadata.
        """
        node = self.index.nodes.get(node_id)
        if not node:
            return DefinitionInfo(fqn="unknown", kind="unknown")

        info = DefinitionInfo(
            fqn=node.fqn,
            kind=node.kind,
            file=node.file,
            line=node.start_line,
            signature=node.signature,
        )

        # Resolve containing class/method
        parent_id = self.index.get_contains_parent(node_id)
        if parent_id:
            parent_node = self.index.nodes.get(parent_id)
            if parent_node:
                info.declared_in = {
                    "fqn": parent_node.fqn,
                    "kind": parent_node.kind,
                    "file": parent_node.file,
                    "line": parent_node.start_line,
                }

        if node.kind in ("Method", "Function"):
            self._build_method_definition(node_id, node, info)
        elif node.kind in ("Class", "Interface", "Trait", "Enum"):
            self._build_class_definition(node_id, node, info)
        elif node.kind == "Property":
            self._build_property_definition(node_id, node, info)
        elif node.kind == "Argument":
            self._build_argument_definition(node_id, node, info)

        return info

    def _build_method_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Method/Function nodes."""
        children = self.index.get_contains_children(node_id)

        # Collect typed arguments
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Argument":
                arg_dict: dict = {"name": child.name, "position": None}
                # Resolve type from type_hint edges
                type_edges = self.index.outgoing[child_id].get("type_hint", [])
                if type_edges:
                    type_node = self.index.nodes.get(type_edges[0].target)
                    if type_node:
                        arg_dict["type"] = type_node.name
                info.arguments.append(arg_dict)

        # Resolve return type from type_hint edges on the method itself
        type_edges = self.index.outgoing[node_id].get("type_hint", [])
        if type_edges:
            type_node = self.index.nodes.get(type_edges[0].target)
            if type_node:
                info.return_type = {"fqn": type_node.fqn, "name": type_node.name}

    def _build_class_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Class/Interface/Trait/Enum nodes."""
        children = self.index.get_contains_children(node_id)

        for child_id in children:
            child = self.index.nodes.get(child_id)
            if not child:
                continue

            if child.kind == "Property":
                prop_dict: dict = {"name": child.name}
                type_edges = self.index.outgoing[child_id].get("type_hint", [])
                if type_edges:
                    type_node = self.index.nodes.get(type_edges[0].target)
                    if type_node:
                        prop_dict["type"] = type_node.name
                info.properties.append(prop_dict)

            elif child.kind == "Method":
                method_dict: dict = {"name": child.name}
                if child.signature:
                    method_dict["signature"] = child.signature
                info.methods.append(method_dict)

        # Inheritance: extends
        extends_id = self.index.get_extends_parent(node_id)
        if extends_id:
            extends_node = self.index.nodes.get(extends_id)
            if extends_node:
                info.extends = extends_node.fqn

        # Inheritance: implements
        impl_ids = self.index.get_implements(node_id)
        for impl_id in impl_ids:
            impl_node = self.index.nodes.get(impl_id)
            if impl_node:
                info.implements.append(impl_node.fqn)

        # Traits: uses_trait
        trait_edges = self.index.outgoing[node_id].get("uses_trait", [])
        for edge in trait_edges:
            trait_node = self.index.nodes.get(edge.target)
            if trait_node:
                info.uses_traits.append(trait_node.fqn)

    def _build_property_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Property nodes."""
        type_edges = self.index.outgoing[node_id].get("type_hint", [])
        if type_edges:
            type_node = self.index.nodes.get(type_edges[0].target)
            if type_node:
                info.return_type = {"fqn": type_node.fqn, "name": type_node.name}

    def _build_argument_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Argument nodes."""
        type_edges = self.index.outgoing[node_id].get("type_hint", [])
        if type_edges:
            type_node = self.index.nodes.get(type_edges[0].target)
            if type_node:
                info.return_type = {"fqn": type_node.fqn, "name": type_node.name}

    def _build_incoming_tree(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False,
        direct_only: bool = False, with_imports: bool = False
    ) -> list[ContextEntry]:
        """Build nested tree for incoming usages (used_by).

        Each usage edge becomes its own branch. When a source references multiple
        members, each member reference is a separate entry showing the specific
        member and reference location. Depth expansion (children) is only added
        to the first entry per source to avoid duplication.

        If include_impl is True and the target is a method that implements an interface
        method, also include usages of that interface method grouped under the interface.

        If direct_only is True, only show usages that directly reference the current node,
        excluding usages that only reference its members.

        Reference types and access chains are resolved from the unified graph's
        Call/Value nodes when available.

        Depth chaining rules (R8):
        - Only chainable reference types (method_call, property_access, instantiation,
          static_call) expand to depth N+1. Non-chainable types (type_hint, extends,
          implements, use_trait) are always leaf nodes.

        Recursive USED BY depth (R7):
        - For each chainable entry at depth N, we resolve the containing method of
          the source reference, then find callers of that method at depth N+1.
          This correctly chains through the call graph: if Controller calls Service
          which calls Repository, querying Repository shows Service at depth 1 and
          Controller at depth 2.

        External-only filtering (R3):
        - For class-level queries, internal self-references (own methods accessing
          own properties) are filtered out.

        Import filtering (R1):
        - By default, PHP import/use statements are hidden. The with_imports
          parameter controls this.

        FQN resolution (R6):
        - File node sources are resolved to their containing class FQN.

        Sort order (R2):
        - Entries at each depth level are sorted by (file path, line number).
        """
        # Determine if the start node is a class-level query (for R3 filtering)
        start_node = self.index.nodes.get(start_id)
        is_class_query = start_node and start_node.kind in ("Class", "Interface", "Trait", "Enum")

        # Global visited set prevents the same source from appearing at multiple depths
        visited = {start_id}
        count = [0]  # Tracks unique sources for limit

        def build_tree(current_id: str, current_depth: int,
                       branch_visited: set[str] | None = None) -> list[ContextEntry]:
            if current_depth > max_depth or count[0] >= limit:
                return []

            # Per-branch visited set for cycle prevention in recursive depth (R7)
            if branch_visited is None:
                branch_visited = set()

            # --- Pass 1: collect all entries, claim sources in visited ---
            # This prevents deeper expansions from "stealing" sources that
            # belong at the current depth.
            entries = []
            source_groups = self.index.get_usages_grouped(current_id)

            for source_id, edges in source_groups.items():
                if source_id in visited:
                    continue

                # Separate direct edges (target = current node) from member edges
                direct_edges = [e for e in edges if e.target == current_id]
                member_edges = [e for e in edges if e.target != current_id]

                # In direct_only mode, skip sources that only have member edges
                if direct_only and not direct_edges:
                    continue

                # R3: For class queries, filter out internal self-references
                if is_class_query and current_depth == 1:
                    if self._is_internal_reference(source_id, start_id):
                        continue

                if count[0] >= limit:
                    break
                count[0] += 1
                visited.add(source_id)

                source_node = self.index.nodes.get(source_id)

                # Sort member edges by line for execution flow order
                member_edges.sort(
                    key=lambda e: e.location.get("line", 0) if e.location else 0
                )

                # Collect all edges to emit: direct edges first, then member edges
                # In direct_only mode, skip member edges entirely
                all_edges = direct_edges + ([] if direct_only else member_edges)

                for edge in all_edges:
                    is_member = edge.target != current_id

                    # Location from the edge itself
                    if edge.location:
                        file = edge.location.get("file")
                        line = edge.location.get("line")
                    elif source_node:
                        file = source_node.file
                        line = source_node.start_line
                    else:
                        file = None
                        line = None

                    # Build member_ref for member edges
                    member_ref = None
                    access_chain = None
                    reference_type = None
                    access_chain_symbol = None

                    if is_member:
                        target_node = self.index.nodes.get(edge.target)
                        if target_node:
                            # Try to find a Call node in the graph for authoritative info
                            call_node_id = find_call_for_usage(
                                self.index, source_id, edge.target, file, line
                            )

                            # Get reference type and access chain from Call node if found
                            if call_node_id:
                                reference_type = get_reference_type_from_call(self.index, call_node_id)
                                access_chain = build_access_chain(self.index, call_node_id)
                                # R4: Resolve access chain property FQN
                                access_chain_symbol = resolve_access_chain_symbol(self.index, call_node_id)
                            else:
                                # Fall back to inference from edge/node types
                                reference_type = _infer_reference_type(edge, target_node, self.index)

                            member_ref = MemberRef(
                                target_name=self._member_display_name(target_node),
                                target_fqn=target_node.fqn,
                                target_kind=target_node.kind,
                                file=file,
                                line=line,
                                reference_type=reference_type,
                                access_chain=access_chain,
                                access_chain_symbol=access_chain_symbol,
                            )
                    else:
                        # Direct reference to the target node itself
                        target_node = self.index.nodes.get(edge.target)

                        # Try to find a Call node in the graph
                        call_node_id = None
                        if file and line is not None and target_node:
                            call_node_id = find_call_for_usage(
                                self.index, source_id, edge.target, file, line
                            )

                        # Get reference type and access chain from Call node
                        access_chain = None
                        if call_node_id:
                            reference_type = get_reference_type_from_call(self.index, call_node_id)
                            access_chain = build_access_chain(self.index, call_node_id)
                            # R4: Resolve access chain property FQN
                            access_chain_symbol = resolve_access_chain_symbol(self.index, call_node_id)
                        else:
                            # Infer reference type for direct edges (extends, implements, type_hint)
                            reference_type = _infer_reference_type(edge, target_node, self.index)

                        # For direct edges, create a member_ref to hold the reference_type
                        # and access_chain
                        member_ref = MemberRef(
                            target_name="",  # No specific member
                            target_fqn=target_node.fqn if target_node else edge.target,
                            target_kind=target_node.kind if target_node else None,
                            file=file,
                            line=line,
                            reference_type=reference_type,
                            access_chain=access_chain,
                            access_chain_symbol=access_chain_symbol,
                        )

                    # R6: Resolve File node identifiers to their containing class FQN
                    entry_fqn = source_node.fqn if source_node else source_id
                    entry_kind = source_node.kind if source_node else None
                    if source_node and source_node.kind == "File":
                        resolved_class = self.index.resolve_file_to_class(source_id)
                        if resolved_class:
                            resolved_node = self.index.nodes.get(resolved_class)
                            if resolved_node:
                                entry_fqn = resolved_node.fqn
                                entry_kind = resolved_node.kind

                    entry = ContextEntry(
                        depth=current_depth,
                        node_id=source_id,
                        fqn=entry_fqn,
                        kind=entry_kind,
                        file=file,
                        line=line,
                        signature=source_node.signature if source_node else None,
                        children=[],
                        member_ref=member_ref,
                    )
                    entries.append(entry)

            # R1: Filter out import references unless with_imports is True
            if not with_imports:
                entries = [e for e in entries if not _is_import_reference(e, self.index)]

            # R2: Sort entries by (file path, line number) for consistent ordering
            entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

            # --- Pass 2: expand children using R7 recursive depth and R8 chaining rules ---
            # For each chainable entry at depth N, resolve the containing method
            # of the source reference, then find callers of that method at depth N+1.
            if current_depth < max_depth:
                expanded = set()
                for entry in entries:
                    if entry.node_id not in expanded:
                        expanded.add(entry.node_id)

                        # R8: Only expand children for chainable reference types
                        ref_type = entry.member_ref.reference_type if entry.member_ref else None
                        if ref_type not in CHAINABLE_REFERENCE_TYPES:
                            # Non-chainable types (type_hint, extends, implements, etc.)
                            # are leaf nodes -- no further depth expansion
                            continue

                        # R7: Resolve the containing method for recursive USED BY
                        # The source node (entry.node_id) references our target.
                        # To find depth N+1, we need to find callers of the METHOD
                        # that contains the source reference, not callers of the source node itself.
                        containing_method_id = self._resolve_containing_method(entry.node_id)
                        if containing_method_id and containing_method_id not in branch_visited:
                            # Create a new branch_visited set for this branch to prevent cycles
                            child_branch_visited = branch_visited | {containing_method_id}
                            entry.children = build_tree(
                                containing_method_id, current_depth + 1, child_branch_visited
                            )

            return entries

        # Build direct usages first
        direct_entries = build_tree(start_id, 1)

        # If include_impl, also build interface usages grouped under the interface method
        interface_entries = []
        if include_impl:
            interface_method_ids = self._get_interface_method_ids(start_id)
            for iface_id in interface_method_ids:
                if iface_id in visited:
                    continue
                visited.add(iface_id)

                iface_node = self.index.nodes.get(iface_id)
                if not iface_node:
                    continue

                # Build usages of the interface method
                iface_usages = build_tree(iface_id, 1)

                # Only add if there are actual usages
                if iface_usages:
                    # Create an entry for the interface method itself
                    iface_entry = ContextEntry(
                        depth=0,  # Special depth for grouping entry
                        node_id=iface_id,
                        fqn=iface_node.fqn,
                        kind=iface_node.kind,
                        file=iface_node.file,
                        line=iface_node.start_line,
                        signature=iface_node.signature,
                        children=iface_usages,
                        via_interface=True,  # Mark as interface grouping
                    )
                    interface_entries.append(iface_entry)

        return direct_entries + interface_entries

    def _resolve_containing_method(self, node_id: str) -> Optional[str]:
        """Resolve the containing Method/Function for a given node.

        For USED BY depth chaining (R7), we need to find the method that contains
        a reference so we can find callers of that method at the next depth level.

        If the node IS a Method/Function, return it directly.
        If the node is a File, return None (file-level references don't chain).
        Otherwise, traverse containment upward to find the Method/Function.

        Args:
            node_id: Node ID to resolve.

        Returns:
            Node ID of the containing Method/Function, or None if not found.
        """
        node = self.index.nodes.get(node_id)
        if not node:
            return None

        # If the node itself is a Method/Function, use it directly
        if node.kind in ("Method", "Function"):
            return node_id

        # If the node is a File, don't chain further (file-level references are leaf nodes)
        if node.kind == "File":
            return None

        # Traverse containment hierarchy upward to find the Method/Function
        current_id = node_id
        max_depth = 10  # Prevent infinite loops
        for _ in range(max_depth):
            parent_id = self.index.get_contains_parent(current_id)
            if not parent_id:
                return None

            parent_node = self.index.nodes.get(parent_id)
            if not parent_node:
                return None

            if parent_node.kind in ("Method", "Function"):
                return parent_id

            # File level reached without finding a method
            if parent_node.kind == "File":
                return None

            current_id = parent_id

        return None

    def _is_internal_reference(self, source_id: str, target_class_id: str) -> bool:
        """Check if a source node is internal to the target class (R3).

        A reference is internal if the source node is contained within the
        target class (e.g., the class's own methods accessing its own properties).

        Args:
            source_id: The source node of the reference.
            target_class_id: The class being queried.

        Returns:
            True if the source is internal to the target class.
        """
        current_id = source_id
        max_depth = 10  # Prevent infinite loops

        for _ in range(max_depth):
            parent_id = self.index.get_contains_parent(current_id)
            if not parent_id:
                return False

            # If we found the target class in the containment chain, it's internal
            if parent_id == target_class_id:
                return True

            parent_node = self.index.nodes.get(parent_id)
            if not parent_node:
                return False

            # Stop at File level -- we've traversed past any class
            if parent_node.kind == "File":
                return False

            current_id = parent_id

        return False

    def _resolve_param_name(self, call_node_id: str, position: int) -> Optional[str]:
        """Get the formal parameter name at the given position from the callee.

        Resolves the Call node's target (callee method/function), gets its
        Argument children in containment order, and returns the name at the
        requested position. Falls back to promoted property Value children
        when no Argument children exist (constructor promotion).

        Args:
            call_node_id: ID of the Call node.
            position: 0-based argument position.

        Returns:
            Parameter name string (e.g., "$productId") or None if not found.
        """
        target_id = self.index.get_call_target(call_node_id)
        if not target_id:
            return None
        children = self.index.get_contains_children(target_id)
        arg_nodes = []
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Argument":
                arg_nodes.append(child)
        if position < len(arg_nodes):
            return arg_nodes[position].name
        # Fallback: promoted constructor parameters (Value children, no Argument nodes)
        promoted = self._get_promoted_params(children)
        if position < len(promoted):
            return promoted[position].name
        return None

    def _find_result_var(self, call_node_id: str) -> Optional[str]:
        """Find the local variable name that receives this call's result.

        Follows: Call --produces--> Value (result) <--assigned_from-- Value (local)

        Args:
            call_node_id: ID of the Call node.

        Returns:
            Local variable name (e.g., "$order") or None if no assignment.
        """
        local_node = self._find_local_value_for_call(call_node_id)
        return local_node.name if local_node else None

    def _find_local_value_for_call(self, call_node_id: str):
        """Find the local Value node assigned from this call's result.

        Follows: Call --produces--> Value (result) <--assigned_from-- Value (local)

        Args:
            call_node_id: ID of the Call node.

        Returns:
            NodeData for the local Value node, or None if no assignment.
        """
        result_id = self.index.get_produces(call_node_id)
        if not result_id:
            return None
        for edge in self.index.incoming[result_id].get("assigned_from", []):
            source_node = self.index.nodes.get(edge.source)
            if source_node and source_node.kind == "Value" and source_node.value_kind == "local":
                return source_node
        return None

    def _get_argument_info(self, call_node_id: str) -> list:
        """Get argument-to-parameter mappings for a Call node.

        Returns a list of ArgumentInfo instances with position, param_name,
        value_expr, value_source, value_type, param_fqn, value_ref_symbol,
        and source_chain.

        Args:
            call_node_id: ID of the Call node.

        Returns:
            List of ArgumentInfo instances.
        """

        arg_edges = self.index.get_arguments(call_node_id)
        arguments = []
        for arg_node_id, position, expression in arg_edges:
            arg_node = self.index.nodes.get(arg_node_id)
            if arg_node:
                param_name = self._resolve_param_name(call_node_id, position)
                param_fqn = self._resolve_param_fqn(call_node_id, position)

                # Resolve value type via type_of edges
                value_type = None
                type_ids = self.index.get_type_of_all(arg_node_id)
                if type_ids:
                    type_names = []
                    for tid in type_ids:
                        tnode = self.index.nodes.get(tid)
                        if tnode:
                            type_names.append(tnode.name)
                    if type_names:
                        value_type = "|".join(type_names)

                # ISSUE-D: Resolve value_ref_symbol and source_chain
                value_ref_symbol = None
                source_chain = None
                if arg_node.value_kind == "local":
                    # Local variable — reference by graph symbol
                    value_ref_symbol = arg_node.fqn
                elif arg_node.value_kind == "parameter":
                    # Method parameter — reference by graph symbol
                    value_ref_symbol = arg_node.fqn
                elif arg_node.value_kind == "result":
                    # Result of another call — trace source chain
                    source_chain = self._trace_source_chain(arg_node_id)

                arguments.append(ArgumentInfo(
                    position=position,
                    param_name=param_name,
                    value_expr=expression or arg_node.name,
                    value_source=arg_node.value_kind,
                    value_type=value_type,
                    param_fqn=param_fqn,
                    value_ref_symbol=value_ref_symbol,
                    source_chain=source_chain,
                ))
        return arguments

    def _resolve_param_fqn(self, call_node_id: str, position: int) -> Optional[str]:
        """Get the formal parameter FQN at the given position from the callee.

        Falls back to promoted property resolution via assigned_from edges
        when no Argument children exist (constructor promotion).

        Args:
            call_node_id: ID of the Call node.
            position: 0-based argument position.

        Returns:
            Parameter FQN string or None.
        """
        target_id = self.index.get_call_target(call_node_id)
        if not target_id:
            return None
        children = self.index.get_contains_children(target_id)
        arg_nodes = []
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Argument":
                arg_nodes.append(child)
        if position < len(arg_nodes):
            return arg_nodes[position].fqn
        # Fallback: promoted constructor parameters — resolve to Property FQN
        promoted = self._get_promoted_params(children)
        if position < len(promoted):
            param_node = promoted[position]
            # Check for assigned_from edge from a Property node
            for edge in self.index.incoming[param_node.id].get("assigned_from", []):
                source_node = self.index.nodes.get(edge.source)
                if source_node and source_node.kind == "Property":
                    return source_node.fqn
            return param_node.fqn
        return None

    def _get_promoted_params(self, children: list[str]) -> list:
        """Get promoted constructor parameter Value nodes sorted by declaration order.

        For PHP constructor promotion, the callee has Value(parameter) children
        instead of Argument children. These are sorted by source range to
        establish positional order matching the constructor signature.

        Args:
            children: List of child node IDs from get_contains_children().

        Returns:
            List of NodeData for promoted parameter Value nodes, sorted by position.
        """
        param_values = []
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Value" and child.value_kind == "parameter":
                param_values.append(child)
        if not param_values:
            return []
        param_values.sort(key=lambda n: (
            n.range.get("start_line", 0) if n.range else 0,
            n.range.get("start_col", 0) if n.range else 0,
        ))
        return param_values

    def _trace_source_chain(self, value_node_id: str) -> Optional[list]:
        """Trace the source chain for a result Value node.

        For property access results, follows the receiver chain to build
        a source chain showing what property is accessed on what object.

        Args:
            value_node_id: ID of the result Value node.

        Returns:
            List of chain step dicts, or None if chain cannot be traced.
        """
        # Find the Call that produces this result value
        # Result values have incoming 'produces' from their Call
        for edge in self.index.incoming[value_node_id].get("produces", []):
            call_id = edge.source
            call_node = self.index.nodes.get(call_id)
            if not call_node:
                continue

            target_id = self.index.get_call_target(call_id)
            if not target_id:
                continue
            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue

            # Build chain step
            step = {
                "fqn": target_node.fqn,
                "kind": target_node.kind,
            }
            ref_type = None
            if call_node.call_kind:
                ref_type = call_node.call_kind
            step["reference_type"] = ref_type

            # Add receiver info if available
            recv_id = self.index.get_receiver(call_id)
            if recv_id:
                recv_node = self.index.nodes.get(recv_id)
                if recv_node:
                    step["on"] = recv_node.fqn

            return [step]

        return None

    def _get_type_references(
        self, method_id: str, depth: int, cycle_guard: set, count: list, limit: int
    ) -> list[ContextEntry]:
        """Extract type-related references (param types, return types) from uses edges.

        When using execution flow for methods, Call nodes don't capture type hints
        for parameters and return types. This helper extracts those from the
        structural `uses` edges so they still appear in USES output.

        Only includes entries where the inferred reference type is a type-related
        value (property_type, type_hint). Excludes parameter_type and return_type
        since those are already shown in the DEFINITION section.
        """
        TYPE_KINDS = {"property_type", "type_hint"}
        entries = []
        local_visited: set[str] = set()

        edges = self.index.get_deps(method_id)
        for edge in edges:
            target_id = edge.target
            if target_id in cycle_guard or target_id in local_visited:
                continue

            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue

            # Only include Class/Interface/Trait/Enum targets (type references)
            if target_node.kind not in ("Class", "Interface", "Trait", "Enum"):
                continue

            # Infer reference type — only keep type-related ones
            ref_type = _infer_reference_type(edge, target_node, self.index)
            if ref_type not in TYPE_KINDS:
                continue

            # Check if there's a Call node (constructor) for this target
            file = edge.location.get("file") if edge.location else target_node.file
            line = edge.location.get("line") if edge.location else target_node.start_line
            call_node_id = find_call_for_usage(self.index, method_id, target_id, file, line)
            if call_node_id:
                # This is a constructor call — it will be picked up by execution flow
                continue

            local_visited.add(target_id)
            if count[0] >= limit:
                break
            count[0] += 1

            member_ref = MemberRef(
                target_name="",
                target_fqn=target_node.fqn,
                target_kind=target_node.kind,
                file=file,
                line=line,
                reference_type=ref_type,
                access_chain=None,
                access_chain_symbol=None,
            )

            entry_kwargs = dict(
                depth=depth,
                node_id=target_id,
                fqn=target_node.fqn,
                kind=target_node.kind,
                file=file,
                line=line,
                signature=target_node.signature,
                children=[],
                implementations=[],
                member_ref=member_ref,
                arguments=[],
                result_var=None,
            )
            entries.append(ContextEntry(**entry_kwargs))

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _build_execution_flow(
        self, method_id: str, depth: int, max_depth: int,
        limit: int, cycle_guard: set, count: list,
        include_impl: bool = False, shown_impl_for: set | None = None,
    ) -> list[ContextEntry]:
        """Build variable-centric execution flow for a method.

        Produces two kinds of entries:
        - Kind 1 (local_variable): When a call result is assigned to a local
          variable. The variable is the primary entry; the call is nested as
          source_call.
        - Kind 2 (call): When a call result is discarded (void/unused). The
          call is the primary entry, same as before.

        Calls consumed as receivers or argument sources by other calls in the
        same method are NOT top-level entries — they appear nested inside the
        consuming entry's access chain or argument source chain.

        Args:
            method_id: The Method/Function node ID to build flow for.
            depth: Current depth level in the tree.
            max_depth: Maximum depth to expand.
            limit: Maximum number of entries.
            cycle_guard: Set of node IDs to prevent infinite recursion.
            count: Mutable list[int] tracking total entries created.
            include_impl: Whether to attach implementations for interface methods.
            shown_impl_for: Set tracking nodes with implementations already shown.
        """
        if depth > max_depth or count[0] >= limit:
            return []

        if shown_impl_for is None:
            shown_impl_for = set()

        children = self.index.get_contains_children(method_id)

        # Step 1: Collect all Call children
        call_children = []
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Call":
                call_children.append((child_id, child))

        # Step 2: Identify consumed calls — calls whose result Value is used
        # as a receiver or argument source by another call in the same method.
        consumed: set[str] = set()
        for call_id, call_node in call_children:
            # Check receiver: if the receiver Value is a result of another call
            recv_id = self.index.get_receiver(call_id)
            if recv_id:
                recv_node = self.index.nodes.get(recv_id)
                if recv_node and recv_node.kind == "Value" and recv_node.value_kind == "result":
                    source_call_id = self.index.get_source_call(recv_id)
                    if source_call_id:
                        consumed.add(source_call_id)
            # Check arguments: if any arg Value is a result of another call
            arg_edges = self.index.get_arguments(call_id)
            for arg_id, _, _ in arg_edges:
                arg_node = self.index.nodes.get(arg_id)
                if arg_node and arg_node.value_kind == "result":
                    src = self.index.get_source_call(arg_id)
                    if src:
                        consumed.add(src)

        # Step 3: Build entries for non-consumed calls
        entries = []
        local_visited: set[str] = set()

        for child_id, child in call_children:
            # Skip consumed calls (they appear nested inside consuming entries)
            if child_id in consumed:
                continue
            if count[0] >= limit:
                break

            target_id = self.index.get_call_target(child_id)
            if not target_id:
                continue
            if target_id in local_visited:
                continue
            local_visited.add(target_id)

            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue

            if target_id in cycle_guard:
                continue

            count[0] += 1
            reference_type = get_reference_type_from_call(self.index, child_id)
            access_chain = build_access_chain(self.index, child_id)
            access_chain_symbol = resolve_access_chain_symbol(self.index, child_id)
            arguments = self._get_argument_info(child_id)
            call_line = child.range.get("start_line") if child.range else None

            member_ref = MemberRef(
                target_name="",
                target_fqn=target_node.fqn,
                target_kind=target_node.kind,
                file=child.file,
                line=call_line,
                reference_type=reference_type,
                access_chain=access_chain,
                access_chain_symbol=access_chain_symbol,
            )

            # Check if this call's result is assigned to a local variable
            local_value = self._find_local_value_for_call(child_id)

            if local_value:
                # Kind 1: Variable entry with nested source_call
                # Resolve variable type from type_of edge
                var_type = None
                type_of_edges = self.index.outgoing[local_value.id].get("type_of", [])
                if type_of_edges:
                    type_node = self.index.nodes.get(type_of_edges[0].target)
                    if type_node:
                        var_type = type_node.name

                # Build the nested source_call entry (the call itself)
                source_call_entry = ContextEntry(
                    depth=depth,
                    node_id=target_id,
                    fqn=target_node.fqn,
                    kind=target_node.kind,
                    file=child.file,
                    line=call_line,
                    signature=target_node.signature,
                    children=[],
                    implementations=[],
                    member_ref=member_ref,
                    arguments=arguments,
                    result_var=None,
                    entry_type="call",
                )

                # Attach implementations to source_call
                if include_impl and target_node and target_id not in shown_impl_for:
                    shown_impl_for.add(target_id)
                    source_call_entry.implementations = self._get_implementations_for_node(
                        target_node, depth, max_depth, limit, cycle_guard, count, shown_impl_for
                    )

                # Variable symbol from local Value's FQN
                var_symbol = local_value.fqn

                var_line = local_value.range.get("start_line") if local_value.range else call_line

                entry = ContextEntry(
                    depth=depth,
                    node_id=local_value.id,
                    fqn=local_value.fqn,
                    kind="Value",
                    file=child.file,
                    line=var_line,
                    signature=None,
                    children=[],
                    implementations=[],
                    member_ref=None,
                    arguments=[],
                    result_var=None,
                    entry_type="local_variable",
                    variable_name=local_value.name,
                    variable_symbol=var_symbol,
                    variable_type=var_type,
                    source_call=source_call_entry,
                )
            else:
                # Kind 2: Call entry (result discarded)
                result_var = self._find_result_var(child_id)

                entry = ContextEntry(
                    depth=depth,
                    node_id=target_id,
                    fqn=target_node.fqn,
                    kind=target_node.kind,
                    file=child.file,
                    line=call_line,
                    signature=target_node.signature,
                    children=[],
                    implementations=[],
                    member_ref=member_ref,
                    arguments=arguments,
                    result_var=result_var,
                    entry_type="call",
                )

                # Attach implementations for interface methods
                if include_impl and target_node and target_id not in shown_impl_for:
                    shown_impl_for.add(target_id)
                    entry.implementations = self._get_implementations_for_node(
                        target_node, depth, max_depth, limit, cycle_guard, count, shown_impl_for
                    )

            # Depth expansion: recurse into callee's execution flow
            if depth < max_depth and target_node.kind in ("Method", "Function"):
                entry.children = self._build_execution_flow(
                    target_id, depth + 1, max_depth, limit,
                    cycle_guard | {target_id}, count,
                    include_impl=include_impl, shown_impl_for=shown_impl_for,
                )

            entries.append(entry)

        # Filter orphan property accesses consumed by non-Call expressions
        entries = self._filter_orphan_property_accesses(entries)

        # Sort by line number for execution order
        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _filter_orphan_property_accesses(self, entries: list[ContextEntry]) -> list[ContextEntry]:
        """Filter property access entries consumed by non-Call expressions.

        An orphan is a top-level property_access entry whose result is not consumed
        by any other Call (via receiver or argument edges) but whose access expression
        appears in another entry's argument value_expr. These are already visible in
        the consuming argument text (e.g., in sprintf or string concatenation).

        Only filters Kind 2 (call) entries with reference_type == "property_access".
        Kind 1 (local_variable) entries are never filtered.
        """
        # Collect all argument value_expr strings from all entries
        all_value_exprs: list[str] = []
        for entry in entries:
            for arg in entry.arguments:
                if arg.value_expr:
                    all_value_exprs.append(arg.value_expr)
            if entry.source_call:
                for arg in entry.source_call.arguments:
                    if arg.value_expr:
                        all_value_exprs.append(arg.value_expr)

        if not all_value_exprs:
            return entries

        # Identify orphan property accesses and check if their expression
        # appears in any other entry's argument value_expr
        filtered = []
        for entry in entries:
            # Only consider Kind 2 property_access entries as orphan candidates
            if (entry.entry_type == "call"
                    and entry.member_ref
                    and entry.member_ref.reference_type == "property_access"
                    and entry.member_ref.access_chain):
                # Build the expression: "$receiver->propertyName"
                # FQN is like "App\Entity\Order::$id", extract "id"
                prop_fqn = entry.fqn
                prop_name = prop_fqn.split("::$")[-1] if "::$" in prop_fqn else None
                if prop_name:
                    access_expr = f"{entry.member_ref.access_chain}->{prop_name}"
                    # Check if this expression appears in any value_expr
                    is_expression_consumed = any(
                        access_expr in expr for expr in all_value_exprs
                    )
                    if is_expression_consumed:
                        # Orphan: skip this entry
                        continue

            filtered.append(entry)

        return filtered

    def _build_outgoing_tree(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False
    ) -> list[ContextEntry]:
        """Build nested tree for outgoing dependencies (uses).

        If include_impl is True, attach implementations for interfaces/methods
        with their dependencies expanded.

        Reference types and access chains are resolved from the unified graph's
        Call/Value nodes when available.

        Uses per-parent deduplication: each parent's children are deduplicated
        independently, allowing the same target to appear under different parents
        at different depths. The start_id is always excluded to prevent infinite
        recursion (cycle prevention).
        """
        # Only the start node is globally excluded to prevent cycles.
        # Per-parent deduplication replaces the old global visited set so that
        # the same target can appear under different parents at different depths.
        cycle_guard = {start_id}
        count = [0]  # Use list to allow mutation in nested function
        # Track nodes we've already shown implementations for to prevent infinite loops
        shown_impl_for: set[str] = set()

        # Phase 3: For Method/Function nodes, use execution flow traversal
        # which iterates Call children in line-number order instead of
        # following structural `uses` edges. Also include structural type
        # references (parameter_type, return_type, property_type, type_hint)
        # since these provide important context about method signatures.
        start_node = self.index.nodes.get(start_id)
        if start_node and start_node.kind in ("Method", "Function"):
            # Get structural type references from uses edges
            type_entries = self._get_type_references(
                start_id, 1, cycle_guard, count, limit
            )
            # Get execution flow from Call children
            call_entries = self._build_execution_flow(
                start_id, 1, max_depth, limit, cycle_guard, count,
                include_impl=include_impl, shown_impl_for=shown_impl_for,
            )
            # Combine: type references first, then call entries in execution order
            return type_entries + call_entries

        def build_tree(current_id: str, current_depth: int) -> list[ContextEntry]:
            if current_depth > max_depth or count[0] >= limit:
                return []

            entries = []
            edges = self.index.get_deps(current_id)

            # Per-parent visited set: prevents duplicate targets within
            # this parent's children, but allows the same target to appear
            # under a different parent at a different depth.
            local_visited: set[str] = set()

            for edge in edges:
                target_id = edge.target
                if target_id in cycle_guard or target_id in local_visited:
                    continue
                local_visited.add(target_id)

                if count[0] >= limit:
                    break
                count[0] += 1

                target_node = self.index.nodes.get(target_id)

                if edge.location:
                    file = edge.location.get("file")
                    line = edge.location.get("line")
                elif target_node:
                    file = target_node.file
                    line = target_node.start_line
                else:
                    file = None
                    line = None

                # Try to find a Call node for reference type and access chain
                member_ref = None
                arguments = []
                result_var = None
                if target_node:
                    call_node_id = find_call_for_usage(
                        self.index, current_id, target_id, file, line
                    )

                    reference_type = None
                    access_chain = None
                    access_chain_symbol = None
                    arguments = []
                    result_var = None

                    if call_node_id:
                        reference_type = get_reference_type_from_call(self.index, call_node_id)
                        access_chain = build_access_chain(self.index, call_node_id)
                        # R4: Resolve access chain property FQN
                        access_chain_symbol = resolve_access_chain_symbol(self.index, call_node_id)
                        # Phase 2: Argument tracking
                        arguments = self._get_argument_info(call_node_id)
                        result_var = self._find_result_var(call_node_id)
                    else:
                        # Fall back to inference from edge/node types
                        reference_type = _infer_reference_type(edge, target_node, self.index)

                    # For USES, target_name is empty since fqn already shows the target
                    # We only need reference_type and access_chain
                    member_ref = MemberRef(
                        target_name="",  # Empty - fqn already shows the target
                        target_fqn=target_node.fqn,
                        target_kind=target_node.kind,
                        file=file,
                        line=line,
                        reference_type=reference_type,
                        access_chain=access_chain,
                        access_chain_symbol=access_chain_symbol,
                    )

                entry_kwargs = dict(
                    depth=current_depth,
                    node_id=target_id,
                    fqn=target_node.fqn if target_node else target_id,
                    kind=target_node.kind if target_node else None,
                    file=file,
                    line=line,
                    signature=target_node.signature if target_node else None,
                    children=[],
                    implementations=[],
                    member_ref=member_ref,
                    arguments=arguments,
                    result_var=result_var,
                )
                entry = ContextEntry(**entry_kwargs)

                # Attach implementations for interfaces/methods with their deps expanded
                # Skip if we've already shown implementations for this node
                if include_impl and target_node and target_id not in shown_impl_for:
                    shown_impl_for.add(target_id)
                    entry.implementations = self._get_implementations_for_node(
                        target_node, current_depth, max_depth, limit, cycle_guard, count, shown_impl_for
                    )

                # Recurse for children
                if current_depth < max_depth:
                    entry.children = build_tree(target_id, current_depth + 1)

                entries.append(entry)

            # R2: Sort entries by (file path, line number) for consistent ordering
            entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

            return entries

        return build_tree(start_id, 1)

    def _get_implementations_for_node(
        self, node: NodeData, depth: int, max_depth: int, limit: int,
        visited: set, count: list, shown_impl_for: set
    ) -> list[ContextEntry]:
        """Get implementations for an interface/class or implementing methods for interface methods.

        For interfaces/classes: returns implementing classes with their dependencies expanded.
        For methods: returns implementing methods (via overrides or interface implementation).

        Note: Implementation subtrees use their own visited set to show full dependency tree
        even if some nodes were already shown in the main tree. The shown_impl_for set prevents
        infinite loops by tracking which nodes we've already shown implementations for.
        """
        implementations = []

        if node.kind in self.INHERITABLE_KINDS:
            # Get all classes that implement/extend this
            impl_ids = self._get_all_children(node.id)
            for impl_id in impl_ids:
                impl_node = self.index.nodes.get(impl_id)
                if impl_node:
                    entry = ContextEntry(
                        depth=depth,
                        node_id=impl_id,
                        fqn=impl_node.fqn,
                        kind=impl_node.kind,
                        file=impl_node.file,
                        line=impl_node.start_line,
                        signature=impl_node.signature,
                        children=[],
                        implementations=[],
                    )

                    # Recurse into implementation's dependencies with FRESH visited set
                    # so we show the full tree even if nodes appeared elsewhere
                    if depth < max_depth:
                        impl_visited = {impl_id}
                        impl_count = [0]
                        entry.children = self._build_deps_subtree(
                            impl_id, depth + 1, max_depth, limit, impl_visited, impl_count,
                            include_impl=True, shown_impl_for=shown_impl_for
                        )

                    implementations.append(entry)

        elif node.kind == "Method":
            # First try direct overrides
            override_ids = list(self.index.get_overridden_by(node.id))

            # Also find interface method implementations
            # Get the containing class/interface of this method
            containing_id = self.index.get_contains_parent(node.id)
            if containing_id:
                containing_node = self.index.nodes.get(containing_id)
                if containing_node and containing_node.kind in ("Interface", "Trait"):
                    # Find implementing classes
                    impl_class_ids = self._get_all_children(containing_id)
                    method_name = node.name

                    for impl_class_id in impl_class_ids:
                        # Find method with same name in implementing class
                        for child_id in self.index.get_contains_children(impl_class_id):
                            child_node = self.index.nodes.get(child_id)
                            if child_node and child_node.kind == "Method" and child_node.name == method_name:
                                if child_id not in override_ids:
                                    override_ids.append(child_id)

            for override_id in override_ids:
                override_node = self.index.nodes.get(override_id)
                if override_node:
                    entry = ContextEntry(
                        depth=depth,
                        node_id=override_id,
                        fqn=override_node.fqn,
                        kind=override_node.kind,
                        file=override_node.file,
                        line=override_node.start_line,
                        signature=override_node.signature,
                        children=[],
                        implementations=[],
                    )

                    # Recurse into method's dependencies with FRESH visited set
                    if depth < max_depth:
                        impl_visited = {override_id}
                        impl_count = [0]
                        entry.children = self._build_deps_subtree(
                            override_id, depth + 1, max_depth, limit, impl_visited, impl_count,
                            include_impl=True, shown_impl_for=shown_impl_for
                        )

                    implementations.append(entry)

        return implementations

    def _build_deps_subtree(
        self, start_id: str, depth: int, max_depth: int, limit: int,
        visited: set, count: list, include_impl: bool, shown_impl_for: set
    ) -> list[ContextEntry]:
        """Build dependency subtree for a node.

        Args:
            shown_impl_for: Set of node IDs we've already shown implementations for,
                           to prevent infinite loops when implementations depend on their interfaces.
        """
        if depth > max_depth or count[0] >= limit:
            return []

        entries = []
        edges = self.index.get_deps(start_id)

        for edge in edges:
            target_id = edge.target
            if target_id in visited:
                continue
            visited.add(target_id)

            if count[0] >= limit:
                break
            count[0] += 1

            target_node = self.index.nodes.get(target_id)

            if edge.location:
                file = edge.location.get("file")
                line = edge.location.get("line")
            elif target_node:
                file = target_node.file
                line = target_node.start_line
            else:
                file = None
                line = None

            # Try to find a Call node for reference type and access chain
            member_ref = None
            arguments = []
            result_var = None
            if target_node:
                call_node_id = find_call_for_usage(
                    self.index, start_id, target_id, file, line
                )

                reference_type = None
                access_chain = None
                access_chain_symbol = None

                if call_node_id:
                    reference_type = get_reference_type_from_call(self.index, call_node_id)
                    access_chain = build_access_chain(self.index, call_node_id)
                    # R4: Resolve access chain property FQN
                    access_chain_symbol = resolve_access_chain_symbol(self.index, call_node_id)
                    # Phase 2: Argument tracking
                    arguments = self._get_argument_info(call_node_id)
                    result_var = self._find_result_var(call_node_id)
                else:
                    # Fall back to inference from edge/node types
                    reference_type = _infer_reference_type(edge, target_node, self.index)

                # For USES, target_name is empty since fqn already shows the target
                member_ref = MemberRef(
                    target_name="",  # Empty - fqn already shows the target
                    target_fqn=target_node.fqn,
                    target_kind=target_node.kind,
                    file=file,
                    line=line,
                    reference_type=reference_type,
                    access_chain=access_chain,
                    access_chain_symbol=access_chain_symbol,
                )

            entry_kwargs = dict(
                depth=depth,
                node_id=target_id,
                fqn=target_node.fqn if target_node else target_id,
                kind=target_node.kind if target_node else None,
                file=file,
                line=line,
                signature=target_node.signature if target_node else None,
                children=[],
                implementations=[],
                member_ref=member_ref,
                arguments=arguments,
                result_var=result_var,
            )
            entry = ContextEntry(**entry_kwargs)

            # Attach implementations for interfaces/methods
            # Skip if we've already shown implementations for this node
            if include_impl and target_node and target_id not in shown_impl_for:
                shown_impl_for.add(target_id)
                entry.implementations = self._get_implementations_for_node(
                    target_node, depth, max_depth, limit, visited, count, shown_impl_for
                )

            # Recurse for children
            if depth < max_depth:
                entry.children = self._build_deps_subtree(
                    target_id, depth + 1, max_depth, limit, visited, count, include_impl, shown_impl_for
                )

            entries.append(entry)

        # R2: Sort entries by (file path, line number) for consistent ordering
        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

        return entries

    def _get_all_children(self, node_id: str) -> list[str]:
        """Get all classes that extend or implement this class/interface."""
        children = []
        # Classes that extend this
        children.extend(self.index.get_extends_children(node_id))
        # Classes that implement this (for interfaces)
        children.extend(self.index.get_implementors(node_id))
        return children

    def _get_interface_method_ids(self, method_id: str) -> list[str]:
        """Get IDs of interface methods that this method implements.

        For a concrete method like SynxisConfigurationService::getHotelIdForItem(),
        returns the interface method SynxisConfigurationServiceInterface::getHotelIdForItem()
        if the class implements that interface.
        """
        method_node = self.index.nodes.get(method_id)
        if not method_node or method_node.kind != "Method":
            return []

        interface_methods = []

        # Check direct override relationship first
        override_parent = self.index.get_overrides_parent(method_id)
        if override_parent:
            parent_node = self.index.nodes.get(override_parent)
            if parent_node:
                # Add the parent, and recursively check its parents
                interface_methods.append(override_parent)
                interface_methods.extend(self._get_interface_method_ids(override_parent))

        # Find via class->interface->method relationship
        containing_id = self.index.get_contains_parent(method_id)
        if containing_id:
            containing_node = self.index.nodes.get(containing_id)
            if containing_node and containing_node.kind in ("Class", "Enum"):
                # Get all interfaces this class implements (including inherited)
                interface_ids = self.index.get_all_interfaces(containing_id)
                method_name = method_node.name

                for iface_id in interface_ids:
                    # Find method with same name in interface
                    for child_id in self.index.get_contains_children(iface_id):
                        child_node = self.index.nodes.get(child_id)
                        if (child_node and child_node.kind == "Method"
                                and child_node.name == method_name
                                and child_id not in interface_methods):
                            interface_methods.append(child_id)

        return interface_methods

    def _build_implementations_tree(
        self, start_id: str, max_depth: int, limit: int
    ) -> list[InheritEntry]:
        """Build tree of classes that extend/implement this class/interface."""
        from collections import deque

        tree: list[InheritEntry] = []
        visited = {start_id}
        count = 0

        # Queue: (node_id, current_depth, parent_entry or None)
        queue: deque[tuple[str, int, InheritEntry | None]] = deque()

        # Get direct children (classes that extend/implement this)
        child_ids = self._get_all_children(start_id)
        for cid in child_ids:
            if cid not in visited:
                queue.append((cid, 1, None))

        while queue and count < limit:
            current_id, current_depth, parent_entry = queue.popleft()

            if current_id in visited:
                continue
            visited.add(current_id)

            node = self.index.nodes.get(current_id)
            if not node:
                continue

            count += 1

            entry = InheritEntry(
                depth=current_depth,
                node_id=node.id,
                fqn=node.fqn,
                kind=node.kind,
                file=node.file,
                line=node.start_line,
                children=[],
            )

            if parent_entry is None:
                tree.append(entry)
            else:
                parent_entry.children.append(entry)

            # Continue BFS if within depth limit
            if current_depth < max_depth:
                grandchild_ids = self._get_all_children(current_id)
                for gc_id in grandchild_ids:
                    if gc_id not in visited:
                        queue.append((gc_id, current_depth + 1, entry))

        return tree

    def _build_overrides_tree(
        self, start_id: str, max_depth: int, limit: int
    ) -> list[OverrideEntry]:
        """Build tree of methods that override this method."""
        from collections import deque

        tree: list[OverrideEntry] = []
        visited = {start_id}
        count = 0

        # Queue: (node_id, current_depth, parent_entry or None)
        queue: deque[tuple[str, int, OverrideEntry | None]] = deque()

        # Get direct children (methods that override this)
        child_ids = self.index.get_overridden_by(start_id)
        for cid in child_ids:
            if cid not in visited:
                queue.append((cid, 1, None))

        while queue and count < limit:
            current_id, current_depth, parent_entry = queue.popleft()

            if current_id in visited:
                continue
            visited.add(current_id)

            node = self.index.nodes.get(current_id)
            if not node:
                continue

            count += 1

            entry = OverrideEntry(
                depth=current_depth,
                node_id=node.id,
                fqn=node.fqn,
                file=node.file,
                line=node.start_line,
                children=[],
            )

            if parent_entry is None:
                tree.append(entry)
            else:
                parent_entry.children.append(entry)

            # Continue BFS if within depth limit
            if current_depth < max_depth:
                grandchild_ids = self.index.get_overridden_by(current_id)
                for gc_id in grandchild_ids:
                    if gc_id not in visited:
                        queue.append((gc_id, current_depth + 1, entry))

        return tree
