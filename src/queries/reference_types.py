"""Reference type inference and access chain building.

Extracted from graph_utils.py. Handles Call/Value node traversal for
reference type classification, access chain construction, and
call-to-usage matching.
"""

from typing import Optional, TYPE_CHECKING

from ..models import NodeData
from ..models.edge import EdgeData

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

    # Constructor fallback without location: if target is a Class, search the
    # source's Call children for constructor calls whose callee's containing
    # class matches the target. This handles cases where get_calls_to(Class)
    # misses constructor calls (which target __construct, not the Class node).
    target_node = index.nodes.get(target_id)
    if target_node and target_node.kind in ("Class", "Interface", "Trait", "Enum"):
        for call_id in call_children:
            call_node = index.nodes.get(call_id)
            if call_node and call_node.call_kind == "constructor":
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
                        # Constructor promotion fix: when source is __construct()
                        # and no Argument child matched, check the parent class's
                        # Property children for a type_hint edge to the target.
                        # Promoted constructor params create Property nodes with
                        # type_hint edges but no Argument nodes.
                        if source_node.name == "__construct":
                            containing_class_id = index.get_contains_parent(method_id)
                            if containing_class_id:
                                for child_id in index.get_contains_children(containing_class_id):
                                    child = index.nodes.get(child_id)
                                    if child and child.kind == "Property":
                                        for th_edge in index.outgoing[child_id].get("type_hint", []):
                                            if th_edge.target == target_id:
                                                return "property_type"
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
