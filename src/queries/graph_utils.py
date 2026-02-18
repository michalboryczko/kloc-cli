"""Pure graph-traversal helpers extracted from context.py.

All functions are standalone (no class dependency). Functions that were
previously ContextQuery methods now take an explicit `index` parameter.

Reference type inference functions have been moved to reference_types.py.
They are re-exported here for backward compatibility.
"""

from typing import Optional, TYPE_CHECKING

from ..models import NodeData, ArgumentInfo

# Re-export reference_types symbols for backward compatibility
from .reference_types import (
    CHAINABLE_REFERENCE_TYPES,
    build_access_chain,
    _build_chain_from_value,
    get_reference_type_from_call,
    _call_matches_target,
    find_call_for_usage,
    get_containing_scope,
    _is_import_reference,
    resolve_access_chain_symbol,
    _infer_reference_type,
)

if TYPE_CHECKING:
    from ..graph import SoTIndex


# =============================================================================
# Former ContextQuery methods — now standalone functions with explicit index
# =============================================================================

def member_display_name(node: NodeData) -> str:
    """Format a short member display name: '$prop', 'method()', 'CONST'."""
    if node.kind == "Method" or node.kind == "Function":
        return f"{node.name}()"
    if node.kind == "Property":
        name = node.name
        return name if name.startswith("$") else f"${name}"
    return node.name


def resolve_receiver_identity(index: "SoTIndex", call_node_id: str) -> tuple[
    Optional[str], Optional[str], Optional[str], Optional[str], Optional[int]
]:
    """Resolve access chain and receiver identity for a Call node.

    Returns:
        (access_chain, access_chain_symbol, on_kind, on_file, on_line)
    """
    access_chain = build_access_chain(index, call_node_id)
    access_chain_symbol_val = resolve_access_chain_symbol(index, call_node_id)
    on_kind = None
    on_file = None
    on_line = None
    recv_id = index.get_receiver(call_node_id)
    if recv_id:
        recv_node = index.nodes.get(recv_id)
        if recv_node and recv_node.kind == "Value" and recv_node.value_kind in ("local", "parameter"):
            on_kind = "local" if recv_node.value_kind == "local" else "param"
            if recv_node.file:
                on_file = recv_node.file
            if recv_node.range and recv_node.range.get("start_line") is not None:
                on_line = recv_node.range["start_line"]
        elif recv_node and recv_node.kind == "Value" and recv_node.value_kind == "result":
            # ISSUE-C: Chain access — receiver is result of a property/method access.
            # e.g., $customer->address->street: the receiver of ->street is the
            # result Value of ->address. Mark as "property" chain access.
            on_kind = "property"
    else:
        # No explicit receiver: check if this is a $this-> access (implicit self)
        call_node = index.nodes.get(call_node_id)
        if call_node and call_node.kind == "Call" and call_node.call_kind in ("access", "method", "method_static"):
            on_kind = "self"
            if not access_chain:
                access_chain = "$this"
    return access_chain, access_chain_symbol_val, on_kind, on_file, on_line


def resolve_containing_method(index: "SoTIndex", node_id: str) -> Optional[str]:
    """Resolve the containing Method/Function for a given node.

    For USED BY depth chaining (R7), we need to find the method that contains
    a reference so we can find callers of that method at the next depth level.

    If the node IS a Method/Function, return it directly.
    If the node is a File, return None (file-level references don't chain).
    Otherwise, traverse containment upward to find the Method/Function.

    Args:
        index: The SoT index.
        node_id: Node ID to resolve.

    Returns:
        Node ID of the containing Method/Function, or None if not found.
    """
    node = index.nodes.get(node_id)
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
        parent_id = index.get_contains_parent(current_id)
        if not parent_id:
            return None

        parent_node = index.nodes.get(parent_id)
        if not parent_node:
            return None

        if parent_node.kind in ("Method", "Function"):
            return parent_id

        # File level reached without finding a method
        if parent_node.kind == "File":
            return None

        current_id = parent_id

    return None


def is_internal_reference(index: "SoTIndex", source_id: str, target_class_id: str) -> bool:
    """Check if a source node is internal to the target class (R3).

    A reference is internal if the source node is contained within the
    target class (e.g., the class's own methods accessing its own properties).

    Args:
        index: The SoT index.
        source_id: The source node of the reference.
        target_class_id: The class being queried.

    Returns:
        True if the source is internal to the target class.
    """
    current_id = source_id
    max_depth = 10  # Prevent infinite loops

    for _ in range(max_depth):
        parent_id = index.get_contains_parent(current_id)
        if not parent_id:
            return False

        # If we found the target class in the containment chain, it's internal
        if parent_id == target_class_id:
            return True

        parent_node = index.nodes.get(parent_id)
        if not parent_node:
            return False

        # Stop at File level -- we've traversed past any class
        if parent_node.kind == "File":
            return False

        current_id = parent_id

    return False


def resolve_param_name(index: "SoTIndex", call_node_id: str, position: int) -> Optional[str]:
    """Get the formal parameter name at the given position from the callee.

    Resolves the Call node's target (callee method/function), gets its
    Argument children in containment order, and returns the name at the
    requested position. Falls back to promoted property Value children
    when no Argument children exist (constructor promotion).

    Args:
        index: The SoT index.
        call_node_id: ID of the Call node.
        position: 0-based argument position.

    Returns:
        Parameter name string (e.g., "$productId") or None if not found.
    """
    target_id = index.get_call_target(call_node_id)
    if not target_id:
        return None
    children = index.get_contains_children(target_id)
    arg_nodes = []
    for child_id in children:
        child = index.nodes.get(child_id)
        if child and child.kind == "Argument":
            arg_nodes.append(child)
    if position < len(arg_nodes):
        return arg_nodes[position].name
    # Fallback: promoted constructor parameters (Value children, no Argument nodes)
    promoted = get_promoted_params(index, children)
    if position < len(promoted):
        return promoted[position].name
    return None


def build_external_call_fqn(index: "SoTIndex", call_node_id: str, call_node) -> str:
    """Build a display FQN for an external call (callee not in graph).

    Uses the receiver's type_of to get the class/interface name, then
    appends the call name. Falls back to just the call name.
    """
    call_name = call_node.name or "?"
    # Try to get receiver type for a qualified FQN
    recv_id = index.get_receiver(call_node_id)
    if recv_id:
        recv_node = index.nodes.get(recv_id)
        if recv_node:
            type_ids = index.get_type_of_all(recv_id)
            for tid in type_ids:
                type_node = index.nodes.get(tid)
                if type_node:
                    return f"{type_node.fqn}::{call_name}"
    return call_name


def find_result_var(index: "SoTIndex", call_node_id: str) -> Optional[str]:
    """Find the local variable name that receives this call's result.

    Follows: Call --produces--> Value (result) <--assigned_from-- Value (local)

    Args:
        index: The SoT index.
        call_node_id: ID of the Call node.

    Returns:
        Local variable name (e.g., "$order") or None if no assignment.
    """
    local_node = find_local_value_for_call(index, call_node_id)
    return local_node.name if local_node else None


def find_local_value_for_call(index: "SoTIndex", call_node_id: str):
    """Find the local Value node assigned from this call's result.

    Follows: Call --produces--> Value (result) <--assigned_from-- Value (local)

    Args:
        index: The SoT index.
        call_node_id: ID of the Call node.

    Returns:
        NodeData for the local Value node, or None if no assignment.
    """
    result_id = index.get_produces(call_node_id)
    if not result_id:
        return None
    for edge in index.incoming[result_id].get("assigned_from", []):
        source_node = index.nodes.get(edge.source)
        if source_node and source_node.kind == "Value" and source_node.value_kind == "local":
            return source_node
    return None


def get_argument_info(index: "SoTIndex", call_node_id: str) -> list:
    """Get argument-to-parameter mappings for a Call node.

    Returns a list of ArgumentInfo instances with position, param_name,
    value_expr, value_source, value_type, param_fqn, value_ref_symbol,
    and source_chain.

    Args:
        index: The SoT index.
        call_node_id: ID of the Call node.

    Returns:
        List of ArgumentInfo instances.
    """

    arg_edges = index.get_arguments(call_node_id)
    arguments = []
    for arg_node_id, position, expression, parameter in arg_edges:
        arg_node = index.nodes.get(arg_node_id)
        if arg_node:
            # Use parameter field from edge if available, fall back to position-based matching
            if parameter:
                # Extract param_name from original parameter FQN (uses . separator)
                param_name = parameter.rsplit(".", 1)[-1] if "." in parameter else parameter
                # For promoted constructor params, resolve to Property FQN via assigned_from
                param_fqn = resolve_promoted_property_fqn(index, parameter) or parameter
            else:
                param_name = resolve_param_name(index, call_node_id, position)
                param_fqn = resolve_param_fqn(index, call_node_id, position)

            # Resolve value type via type_of edges
            value_type = None
            type_ids = index.get_type_of_all(arg_node_id)
            if type_ids:
                type_names = []
                for tid in type_ids:
                    tnode = index.nodes.get(tid)
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
                source_chain = trace_source_chain(index, arg_node_id)

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


def resolve_param_fqn(index: "SoTIndex", call_node_id: str, position: int) -> Optional[str]:
    """Get the formal parameter FQN at the given position from the callee.

    Falls back to promoted property resolution via assigned_from edges
    when no Argument children exist (constructor promotion).

    Args:
        index: The SoT index.
        call_node_id: ID of the Call node.
        position: 0-based argument position.

    Returns:
        Parameter FQN string or None.
    """
    target_id = index.get_call_target(call_node_id)
    if not target_id:
        return None
    children = index.get_contains_children(target_id)
    arg_nodes = []
    for child_id in children:
        child = index.nodes.get(child_id)
        if child and child.kind == "Argument":
            arg_nodes.append(child)
    if position < len(arg_nodes):
        return arg_nodes[position].fqn
    # Fallback: promoted constructor parameters — resolve to Property FQN
    promoted = get_promoted_params(index, children)
    if position < len(promoted):
        param_node = promoted[position]
        # Check for assigned_from edge from a Property node
        for edge in index.incoming[param_node.id].get("assigned_from", []):
            source_node = index.nodes.get(edge.source)
            if source_node and source_node.kind == "Property":
                return source_node.fqn
        return param_node.fqn
    return None


def resolve_promoted_property_fqn(index: "SoTIndex", param_fqn: str) -> Optional[str]:
    """Resolve a parameter FQN to its promoted Property FQN if applicable.

    For PHP constructor promotion, the parameter Value node has an
    assigned_from edge from a Property node. This returns the Property FQN.

    Args:
        index: The SoT index.
        param_fqn: The parameter FQN (e.g., Order::__construct().$id).

    Returns:
        Property FQN if promoted, None otherwise.
    """
    param_ids = index.fqn_to_ids.get(param_fqn, [])
    for param_id in param_ids:
        param_node = index.nodes.get(param_id)
        if param_node and param_node.kind == "Value" and param_node.value_kind == "parameter":
            for edge in index.incoming[param_id].get("assigned_from", []):
                source_node = index.nodes.get(edge.source)
                if source_node and source_node.kind == "Property":
                    return source_node.fqn
    return None


def get_promoted_params(index: "SoTIndex", children: list[str]) -> list:
    """Get promoted constructor parameter Value nodes sorted by declaration order.

    For PHP constructor promotion, the callee has Value(parameter) children
    instead of Argument children. These are sorted by source range to
    establish positional order matching the constructor signature.

    Args:
        index: The SoT index.
        children: List of child node IDs from get_contains_children().

    Returns:
        List of NodeData for promoted parameter Value nodes, sorted by position.
    """
    param_values = []
    for child_id in children:
        child = index.nodes.get(child_id)
        if child and child.kind == "Value" and child.value_kind == "parameter":
            param_values.append(child)
    if not param_values:
        return []
    param_values.sort(key=lambda n: (
        n.range.get("start_line", 0) if n.range else 0,
        n.range.get("start_col", 0) if n.range else 0,
    ))
    return param_values


def trace_source_chain(index: "SoTIndex", value_node_id: str) -> Optional[list]:
    """Trace the source chain for a result Value node.

    For property access results, follows the receiver chain to build
    a source chain showing what property is accessed on what object.

    Args:
        index: The SoT index.
        value_node_id: ID of the result Value node.

    Returns:
        List of chain step dicts, or None if chain cannot be traced.
    """
    # Find the Call that produces this result value
    # Result values have incoming 'produces' from their Call
    for edge in index.incoming[value_node_id].get("produces", []):
        call_id = edge.source
        call_node = index.nodes.get(call_id)
        if not call_node:
            continue

        target_id = index.get_call_target(call_id)
        if not target_id:
            continue
        target_node = index.nodes.get(target_id)
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
        recv_id = index.get_receiver(call_id)
        if recv_id:
            recv_node = index.nodes.get(recv_id)
            if recv_node:
                step["on"] = recv_node.fqn
                # Add variable identity info for Value receivers
                if recv_node.kind == "Value" and recv_node.value_kind:
                    if recv_node.value_kind == "local":
                        step["on_kind"] = "local"
                    elif recv_node.value_kind == "parameter":
                        step["on_kind"] = "param"
                    if recv_node.file:
                        step["on_file"] = recv_node.file
                    if recv_node.range and recv_node.range.get("start_line") is not None:
                        step["on_line"] = recv_node.range["start_line"]

        return [step]

    return None


def get_single_argument_info(
    index: "SoTIndex", call_id: str, param_fqn: str, value_id: str
) -> Optional[ArgumentInfo]:
    """Build ArgumentInfo for a single argument matching param_fqn."""
    value_node = index.nodes.get(value_id)
    if not value_node:
        return None

    # Determine value expression and source
    value_expr = None
    value_source = None
    if value_node.value_kind == "parameter":
        value_source = "parameter"
        value_expr = value_node.name
    elif value_node.value_kind == "local":
        value_source = "local"
        value_expr = value_node.name
    elif value_node.value_kind == "literal":
        value_source = "literal"
        # Use expression from argument edge if available (has actual value)
        arg_expression = None
        for arg_vid, _pos, expr, _param in index.get_arguments(call_id):
            if arg_vid == value_id and expr:
                arg_expression = expr
                break
        value_expr = arg_expression if arg_expression else value_node.name
    elif value_node.value_kind == "result":
        value_source = "result"
        # Try to get the expression from source call
        source_call_id = index.get_source_call(value_id)
        if source_call_id:
            source_call = index.nodes.get(source_call_id)
            if source_call:
                target_id = index.get_call_target(source_call_id)
                target = index.nodes.get(target_id) if target_id else None
                if target and source_call.call_kind == "access":
                    # Property access: show as $receiver->property
                    chain = build_access_chain(index, source_call_id)
                    prop_name = target.name.lstrip("$")
                    if chain:
                        value_expr = f"{chain}->{prop_name}"
                    else:
                        value_expr = f"$this->{prop_name}"
                elif target:
                    value_expr = f"{target.name}()"

    # Resolve type
    value_type = None
    type_ids = index.get_type_of_all(value_id)
    if type_ids:
        type_names = []
        for tid in type_ids:
            tnode = index.nodes.get(tid)
            if tnode:
                type_names.append(tnode.name)
        if type_names:
            value_type = "|".join(type_names)

    # Extract param name from FQN (e.g., "Order::__construct().$id" -> "$id")
    param_name_val = param_fqn.rsplit(".", 1)[-1] if "." in param_fqn else param_fqn

    return ArgumentInfo(
        position=0,
        param_name=param_name_val,
        value_expr=value_expr,
        value_source=value_source,
        value_type=value_type,
        param_fqn=param_fqn,
        value_ref_symbol=value_node.fqn,
    )


def get_all_children(index: "SoTIndex", node_id: str) -> list[str]:
    """Get all classes that extend or implement this class/interface."""
    children = []
    # Classes that extend this
    children.extend(index.get_extends_children(node_id))
    # Classes that implement this (for interfaces)
    children.extend(index.get_implementors(node_id))
    return children
