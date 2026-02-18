"""Value chain traversal for USED BY and USES sections.

Handles Value node consumer chains (USED BY) and source chains (USES),
including cross-method boundary tracing via parameter FQNs.

All functions are standalone with an explicit `index` parameter.
"""

from typing import Optional, Callable, TYPE_CHECKING

from ..models import ContextEntry, MemberRef, ArgumentInfo
from .graph_utils import (
    member_display_name,
    resolve_receiver_identity,
    get_argument_info,
    find_local_value_for_call,
    find_result_var,
)
from .reference_types import (
    get_reference_type_from_call,
    get_containing_scope,
)

if TYPE_CHECKING:
    from ..graph import SoTIndex


def build_value_consumer_chain(
    index: "SoTIndex", value_id: str, depth: int, max_depth: int, limit: int,
    visited: set | None = None,
    crossing_count: int = 0, max_crossings: int | None = None
) -> list[ContextEntry]:
    """Build consumer chain for a Value node (USED BY section).

    Finds all Calls that consume this Value — either as a receiver (property
    access like $savedOrder->id) or directly as an argument. Groups property
    accesses by the downstream Call that consumes the accessed property result.

    For receiver edges: each Call that uses this Value as receiver accesses a
    property/method on it. The result of that access may feed into another Call
    as an argument — that consuming Call becomes the USED BY entry, with the
    property access shown as argument mapping.

    For direct argument edges: the Value itself is passed as argument to a Call.

    At depth+1: traces forward into callee body (promoted properties, further usage).
    Cross-method: when Value is passed as argument, crosses into callee to trace
    the matching parameter's consumers recursively (ISSUE-E).

    Args:
        index: The SoT index.
        value_id: The Value node ID to find consumers for.
        depth: Current depth level.
        max_depth: Maximum depth to expand.
        limit: Maximum number of entries.
        visited: Set of visited Value IDs for cycle detection.

    Returns:
        List of ContextEntry representing consuming Calls, sorted by line number.
    """
    if max_crossings is None:
        max_crossings = min(max_depth, 10)

    if depth > max_depth:
        return []

    if visited is None:
        visited = set()
    if value_id in visited:
        return []
    visited.add(value_id)

    entries = []
    count = 0
    seen_calls = set()  # Prevent duplicate entries for same consuming Call

    # === Part 1: Receiver edges (property accesses on this Value) ===
    # Each receiver edge means a Call accesses a property/method on this Value.
    # Group by consuming Call: $savedOrder->id feeds into send() as $to.
    receiver_edges = index.incoming[value_id].get("receiver", [])

    # Collect property access info grouped by consuming Call
    # Structure: consumer_call_id -> list of access info dicts
    consumer_groups: dict[str, list[dict]] = {}
    # Track standalone receiver calls (property accesses not consumed as arguments)
    standalone_accesses: list[tuple] = []  # (access_call_id, access_call_node)

    for edge in receiver_edges:
        access_call_id = edge.source  # The Call that accesses property on this Value
        access_call_node = index.nodes.get(access_call_id)
        if not access_call_node:
            continue

        # What property/method does this call access?
        target_id = index.get_call_target(access_call_id)
        target_node = index.nodes.get(target_id) if target_id else None

        # Does this property access produce a result that is used as argument?
        result_id = index.get_produces(access_call_id)
        found_consumer = False

        if result_id:
            # Check if result is used as argument in another Call
            arg_edges = index.incoming[result_id].get("argument", [])
            for arg_edge in arg_edges:
                consumer_call_id = arg_edge.source
                if consumer_call_id not in consumer_groups:
                    consumer_groups[consumer_call_id] = []
                consumer_groups[consumer_call_id].append({
                    "prop_name": target_node.name if target_node else "?",
                    "prop_fqn": target_node.fqn if target_node else None,
                    "position": arg_edge.position or 0,
                    "expression": arg_edge.expression,
                    "access_call_id": access_call_id,
                    "access_call_line": (
                        access_call_node.range.get("start_line")
                        if access_call_node.range else None
                    ),
                })
                found_consumer = True

            # Check if result is assigned to another variable
            if not found_consumer:
                assigned_edges = index.incoming[result_id].get("assigned_from", [])
                if assigned_edges:
                    found_consumer = True
                    standalone_accesses.append((access_call_id, access_call_node))

        if not found_consumer:
            standalone_accesses.append((access_call_id, access_call_node))

    # Build entries for each consuming Call (grouped property accesses)
    for consumer_call_id, access_infos in consumer_groups.items():
        if count >= limit:
            break
        if consumer_call_id in seen_calls:
            continue
        seen_calls.add(consumer_call_id)

        consumer_call_node = index.nodes.get(consumer_call_id)
        if not consumer_call_node:
            continue

        # Find the method being called by the consumer
        consumer_target_id = index.get_call_target(consumer_call_id)
        consumer_target = (
            index.nodes.get(consumer_target_id) if consumer_target_id else None
        )

        consumer_fqn = consumer_target.fqn if consumer_target else consumer_call_node.fqn
        consumer_kind = consumer_target.kind if consumer_target else consumer_call_node.kind
        consumer_sig = consumer_target.signature if consumer_target else None
        call_line = (
            consumer_call_node.range.get("start_line")
            if consumer_call_node.range else None
        )

        # Build argument info for this consuming Call (reuse existing helper)
        arguments = get_argument_info(index, consumer_call_id)

        # Build member_ref showing the call target
        member_ref = None
        if consumer_target:
            reference_type = get_reference_type_from_call(index, consumer_call_id)
            ac, acs, ok, of, ol = resolve_receiver_identity(index, consumer_call_id)
            member_ref = MemberRef(
                target_name=member_display_name(consumer_target),
                target_fqn=consumer_target.fqn,
                target_kind=consumer_target.kind,
                file=consumer_call_node.file,
                line=call_line,
                reference_type=reference_type,
                access_chain=ac,
                access_chain_symbol=acs,
                on_kind=ok,
                on_file=of,
                on_line=ol,
            )

        # Build flat fields for class-level context compatibility
        flat_ref_type = None
        flat_callee = None
        flat_on = None
        flat_on_kind = None
        if consumer_target:
            flat_ref_type = get_reference_type_from_call(index, consumer_call_id)
            if consumer_target.kind == "Method":
                flat_callee = consumer_target.name + "()"
            elif consumer_target.kind == "Property":
                flat_callee = consumer_target.name if consumer_target.name.startswith("$") else "$" + consumer_target.name
            if member_ref:
                flat_on = member_ref.access_chain
                flat_on_kind = member_ref.on_kind
                # Detect property-based receiver (on_kind None but access_chain_symbol is a Property)
                if flat_on_kind is None and member_ref.access_chain_symbol:
                    sym_nodes = index.resolve_symbol(member_ref.access_chain_symbol)
                    if sym_nodes and sym_nodes[0].kind == "Property":
                        flat_on_kind = "property"

        entry = ContextEntry(
            depth=depth,
            node_id=consumer_target_id or consumer_call_id,
            fqn=consumer_fqn,
            kind=consumer_kind,
            file=consumer_call_node.file,
            line=call_line,
            signature=consumer_sig,
            children=[],
            member_ref=member_ref,
            arguments=arguments,
            ref_type=flat_ref_type,
            callee=flat_callee,
            on=flat_on,
            on_kind=flat_on_kind,
        )

        # Depth expansion: trace forward into callee body
        if depth < max_depth and consumer_target_id and consumer_target:
            if consumer_target.kind in ("Method", "Function"):
                # ISSUE-E: Cross-method USED BY — cross into callee via parameter FQN
                cross_into_callee(
                    index, consumer_call_id, consumer_target_id, consumer_target,
                    entry, depth, max_depth, limit, visited,
                    crossing_count=crossing_count, max_crossings=max_crossings
                )

        count += 1
        entries.append(entry)

    # === Part 2: Standalone property accesses (not consumed as arguments) ===
    for access_call_id, access_call_node in standalone_accesses:
        if count >= limit:
            break
        if access_call_id in seen_calls:
            continue
        seen_calls.add(access_call_id)

        target_id = index.get_call_target(access_call_id)
        target_node = index.nodes.get(target_id) if target_id else None
        call_line = (
            access_call_node.range.get("start_line")
            if access_call_node.range else None
        )

        reference_type = get_reference_type_from_call(index, access_call_id)
        ac, acs, ok, of, ol = resolve_receiver_identity(index, access_call_id)
        arguments = get_argument_info(index, access_call_id)

        member_ref = MemberRef(
            target_name=member_display_name(target_node) if target_node else "?",
            target_fqn=target_node.fqn if target_node else "?",
            target_kind=target_node.kind if target_node else None,
            file=access_call_node.file,
            line=call_line,
            reference_type=reference_type,
            access_chain=ac,
            access_chain_symbol=acs,
            on_kind=ok,
            on_file=of,
            on_line=ol,
        )

        # Build flat fields for class-level context compatibility
        flat_callee = None
        if target_node:
            if target_node.kind == "Method":
                flat_callee = target_node.name + "()"
            elif target_node.kind == "Property":
                flat_callee = target_node.name if target_node.name.startswith("$") else "$" + target_node.name
        # Detect property-based receiver
        flat_on_kind = ok
        if flat_on_kind is None and acs:
            sym_nodes = index.resolve_symbol(acs)
            if sym_nodes and sym_nodes[0].kind == "Property":
                flat_on_kind = "property"

        entry = ContextEntry(
            depth=depth,
            node_id=target_id or access_call_id,
            fqn=target_node.fqn if target_node else access_call_node.fqn,
            kind=target_node.kind if target_node else access_call_node.kind,
            file=access_call_node.file,
            line=call_line,
            children=[],
            member_ref=member_ref,
            arguments=arguments,
            ref_type=reference_type,
            callee=flat_callee,
            on=ac,
            on_kind=flat_on_kind,
        )
        count += 1
        entries.append(entry)

    # === Part 3: Direct argument edges (Value used directly as argument) ===
    argument_edges = index.incoming[value_id].get("argument", [])
    for edge in argument_edges:
        if count >= limit:
            break
        consumer_call_id = edge.source
        if consumer_call_id in seen_calls:
            continue
        seen_calls.add(consumer_call_id)

        consumer_call_node = index.nodes.get(consumer_call_id)
        if not consumer_call_node:
            continue

        consumer_target_id = index.get_call_target(consumer_call_id)
        consumer_target = (
            index.nodes.get(consumer_target_id) if consumer_target_id else None
        )

        consumer_fqn = consumer_target.fqn if consumer_target else consumer_call_node.fqn
        consumer_kind = consumer_target.kind if consumer_target else consumer_call_node.kind
        consumer_sig = consumer_target.signature if consumer_target else None
        call_line = (
            consumer_call_node.range.get("start_line")
            if consumer_call_node.range else None
        )

        arguments = get_argument_info(index, consumer_call_id)

        member_ref = None
        if consumer_target:
            reference_type = get_reference_type_from_call(index, consumer_call_id)
            ac, acs, ok, of, ol = resolve_receiver_identity(index, consumer_call_id)
            member_ref = MemberRef(
                target_name=member_display_name(consumer_target),
                target_fqn=consumer_target.fqn,
                target_kind=consumer_target.kind,
                file=consumer_call_node.file,
                line=call_line,
                reference_type=reference_type,
                access_chain=ac,
                access_chain_symbol=acs,
                on_kind=ok,
                on_file=of,
                on_line=ol,
            )

        # Build flat fields for class-level context compatibility
        flat_ref_type3 = None
        flat_callee3 = None
        flat_on3 = None
        flat_on_kind3 = None
        if consumer_target:
            flat_ref_type3 = get_reference_type_from_call(index, consumer_call_id)
            if consumer_target.kind == "Method":
                flat_callee3 = consumer_target.name + "()"
            elif consumer_target.kind == "Property":
                flat_callee3 = consumer_target.name if consumer_target.name.startswith("$") else "$" + consumer_target.name
            if member_ref:
                flat_on3 = member_ref.access_chain
                flat_on_kind3 = member_ref.on_kind
                # Detect property-based receiver
                if flat_on_kind3 is None and member_ref.access_chain_symbol:
                    sym_nodes = index.resolve_symbol(member_ref.access_chain_symbol)
                    if sym_nodes and sym_nodes[0].kind == "Property":
                        flat_on_kind3 = "property"

        entry = ContextEntry(
            depth=depth,
            node_id=consumer_target_id or consumer_call_id,
            fqn=consumer_fqn,
            kind=consumer_kind,
            file=consumer_call_node.file,
            line=call_line,
            signature=consumer_sig,
            children=[],
            member_ref=member_ref,
            arguments=arguments,
            ref_type=flat_ref_type3,
            callee=flat_callee3,
            on=flat_on3,
            on_kind=flat_on_kind3,
        )

        # ISSUE-E: Cross-method USED BY — cross into callee via parameter FQN
        if depth < max_depth and consumer_target_id and consumer_target:
            if consumer_target.kind in ("Method", "Function"):
                cross_into_callee(
                    index, consumer_call_id, consumer_target_id, consumer_target,
                    entry, depth, max_depth, limit, visited,
                    crossing_count=crossing_count, max_crossings=max_crossings
                )

        count += 1
        entries.append(entry)

    # Sort all entries by source line number (AC 12)
    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

    return entries


def cross_into_callee(
    index: "SoTIndex", call_node_id: str, callee_id: str, callee_node,
    entry: "ContextEntry", depth: int, max_depth: int, limit: int,
    visited: set, crossing_count: int = 0, max_crossings: int | None = None
) -> None:
    """Cross method boundary from caller into callee for USED BY tracing.

    For each argument edge with a parameter FQN, finds the matching
    Value(parameter) node in the callee and recursively traces its consumers.

    Also follows the return value path: if the call produces a result
    assigned to a local variable, traces that local's consumers.

    Args:
        index: The SoT index.
        call_node_id: The Call node in the caller.
        callee_id: The callee Method/Function node ID.
        callee_node: The callee Method/Function NodeData.
        entry: The ContextEntry to attach children to.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        visited: Visited Value IDs for cycle detection.
        crossing_count: Number of return crossings so far in this chain.
        max_crossings: Maximum return crossings allowed per chain.
    """
    if max_crossings is None:
        max_crossings = min(max_depth, 10)
    # Cross into callee via argument parameter FQNs
    arg_edges = index.get_arguments(call_node_id)
    for _, _, _, parameter_fqn in arg_edges:
        if not parameter_fqn:
            continue
        # Find the matching Value(parameter) node by FQN
        param_matches = index.resolve_symbol(parameter_fqn)
        for pm in param_matches:
            if pm.kind == "Value" and pm.value_kind == "parameter":
                if pm.id not in visited:
                    child_entries = build_value_consumer_chain(
                        index, pm.id, depth + 1, max_depth, limit, visited,
                        crossing_count=crossing_count, max_crossings=max_crossings
                    )
                    for ce in child_entries:
                        if not ce.crossed_from:
                            ce.crossed_from = parameter_fqn
                    entry.children.extend(child_entries)
                break

    # Return value path: if callee return flows back to a local in caller
    local_value = find_local_value_for_call(index, call_node_id)
    if local_value and local_value.id not in visited:
        return_entries = build_value_consumer_chain(
            index, local_value.id, depth + 1, max_depth, limit, visited,
            crossing_count=crossing_count, max_crossings=max_crossings
        )
        entry.children.extend(return_entries)
    else:
        # No local assignment — check if return expression, cross into callers
        cross_into_callers_via_return(
            index, call_node_id, entry, depth, max_depth, limit, visited,
            crossing_count=crossing_count, max_crossings=max_crossings
        )


def get_type_of(index: "SoTIndex", node_id: str) -> Optional[str]:
    """Get the type_of target node ID for a Value node."""
    for edge in index.outgoing[node_id].get("type_of", []):
        return edge.target
    return None


def cross_into_callers_via_return(
    index: "SoTIndex", call_node_id: str, entry: "ContextEntry",
    depth: int, max_depth: int, limit: int, visited: set,
    crossing_count: int = 0, max_crossings: int | None = None
) -> None:
    """Cross from callee return back into caller scope via return value.

    When a Value is consumed by a Call (e.g., new OrderOutput(...)) that is
    the return expression of a method, and there is no local variable assigned
    from the result in the current scope, this traces the return value into
    caller scopes where the result IS assigned to a local variable.

    Uses type matching as a guard: the consumer result's type_of must match
    the caller local's type_of to prevent false positives.

    Safety guards (ISSUE-C):
    - Depth budget: depth >= max_depth stops expansion
    - Crossing limit: crossing_count >= max_crossings stops further crossings
    - Method-level cycle prevention: visited set tracks method IDs to prevent loops
    - Fan-out cap: callers capped at limit per crossing point

    Args:
        index: The SoT index.
        call_node_id: The consumer Call node ID (e.g., the instantiation).
        entry: The ContextEntry to attach children to.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        visited: Visited Value IDs and method-crossing keys for cycle detection.
        crossing_count: Number of return crossings so far in this chain.
        max_crossings: Maximum number of return crossings allowed per chain.
    """
    if max_crossings is None:
        max_crossings = min(max_depth, 10)

    if depth >= max_depth:
        return

    # ISSUE-C Guard: Crossing limit
    if crossing_count >= max_crossings:
        return

    # Step 1: Get produced Value(result) from consumer Call
    result_id = index.get_produces(call_node_id)
    if not result_id:
        return

    # Step 2: Verify no Value(local) assigned from it (inline return check)
    for edge in index.incoming[result_id].get("assigned_from", []):
        source_node = index.nodes.get(edge.source)
        if source_node and source_node.kind == "Value" and source_node.value_kind == "local":
            return  # Has a local assignment, not an inline return

    # Step 3: TYPE GUARD — get consumer result type_of
    consumer_type_id = get_type_of(index, result_id)
    if not consumer_type_id:
        return  # Conservative: no type info, don't cross

    # Step 4: Find containing method
    containing_method_id = index.get_contains_parent(call_node_id)
    if not containing_method_id:
        return
    containing_method = index.nodes.get(containing_method_id)
    if not containing_method or containing_method.kind not in ("Method", "Function"):
        return

    # ISSUE-C Guard: Method-level cycle prevention
    method_key = f"return_crossing:{containing_method_id}"
    if method_key in visited:
        return
    # Note: Don't add method_key to visited yet — only after finding a type-matching caller.
    # Adding it eagerly would block the correct Call (e.g., new OrderOutput) if an earlier
    # Call in the same method (e.g., new OrderCreatedMessage) fails the type guard first.

    # Step 5: Find callers of the containing method
    caller_call_ids = index.get_calls_to(containing_method_id)

    # ISSUE-C Guard: Fan-out cap — limit callers processed per crossing point
    for caller_call_id in caller_call_ids[:limit]:
        caller_local = find_local_value_for_call(index, caller_call_id)
        if not caller_local or caller_local.id in visited:
            continue

        # Step 7: TYPE GUARD — caller local type must match consumer result type
        caller_type_id = get_type_of(index, caller_local.id)
        if caller_type_id != consumer_type_id:
            continue  # Type mismatch, skip

        # Mark method as visited NOW — we found a valid match, prevent re-entry
        visited.add(method_key)

        # Step 8: Continue tracing from caller's local (increment crossing_count)
        return_entries = build_value_consumer_chain(
            index, caller_local.id, depth + 1, max_depth, limit, visited,
            crossing_count=crossing_count + 1, max_crossings=max_crossings
        )

        # ISSUE-D: Set crossed_from on first child entries to show crossing notation
        # Find the caller's containing method to show "crosses into CallerMethod()"
        caller_method_id = index.get_contains_parent(caller_call_id)
        caller_method_node = index.nodes.get(caller_method_id) if caller_method_id else None
        if caller_method_node and caller_method_node.kind in ("Method", "Function"):
            caller_method_fqn = caller_method_node.fqn
            for child_entry in return_entries:
                if not child_entry.crossed_from:
                    child_entry.crossed_from = caller_method_fqn

        entry.children.extend(return_entries)


def build_value_source_chain(
    index: "SoTIndex", value_id: str, depth: int, max_depth: int, limit: int,
    visited: set | None = None
) -> list[ContextEntry]:
    """Build source chain for a Value node (USES section).

    Traces: $savedOrder <- save($processedOrder) <- process($order) <- new Order(...)
    Each depth level follows assigned_from -> produces -> Call, then recursively
    traces the Call's argument Values' source chains.

    For parameter Values: crosses method boundary to find callers via argument
    edges with matching parameter FQN (ISSUE-E).

    Args:
        index: The SoT index.
        value_id: ID of the Value node to trace from.
        depth: Current depth level.
        max_depth: Maximum depth for recursion.
        limit: Maximum number of entries.
        visited: Set of visited Value IDs for cycle detection.

    Returns:
        List of ContextEntry instances representing the source chain.
    """
    if depth > max_depth:
        return []

    if visited is None:
        visited = set()
    if value_id in visited:
        return []
    visited.add(value_id)

    value_node = index.nodes.get(value_id)
    if not value_node or value_node.kind != "Value":
        return []

    # ISSUE-E: Parameter Values have no local sources — find callers via argument edges
    if value_node.value_kind == "parameter":
        return build_parameter_uses(index, value_id, value_node, depth, max_depth, limit, visited)

    # Follow assigned_from to find source Value
    assigned_from_id = index.get_assigned_from(value_id)
    source_value_id = assigned_from_id

    # If no assigned_from, check if this is a result value with a source call
    if not source_value_id and value_node.value_kind == "result":
        source_value_id = value_id  # Result value IS the source

    if not source_value_id:
        return []

    # Find the Call that produced the source Value
    source_call_id = index.get_source_call(source_value_id)
    if not source_call_id:
        return []

    call_node = index.nodes.get(source_call_id)
    if not call_node:
        return []

    # Get the call target (callee method/constructor)
    target_id = index.get_call_target(source_call_id)
    target_node = index.nodes.get(target_id) if target_id else None

    if not target_node:
        return []

    # Build reference type and access chain
    reference_type = get_reference_type_from_call(index, source_call_id)
    ac, acs, ok, of, ol = resolve_receiver_identity(index, source_call_id)

    call_line = call_node.range.get("start_line") if call_node.range else None

    member_ref = MemberRef(
        target_name=member_display_name(target_node),
        target_fqn=target_node.fqn,
        target_kind=target_node.kind,
        file=call_node.file,
        line=call_line,
        reference_type=reference_type,
        access_chain=ac,
        access_chain_symbol=acs,
        on_kind=ok,
        on_file=of,
        on_line=ol,
    )

    # Reuse _get_argument_info for argument tracking
    arguments = get_argument_info(index, source_call_id)

    entry = ContextEntry(
        depth=depth,
        node_id=target_id,
        fqn=target_node.fqn,
        kind=target_node.kind,
        file=call_node.file,
        line=call_line,
        signature=target_node.signature,
        children=[],
        implementations=[],
        member_ref=member_ref,
        arguments=arguments,
        result_var=None,
        entry_type="call",
    )

    # Recursively trace each argument's source chain at depth+1
    if depth < max_depth:
        for arg in arguments:
            if arg.value_ref_symbol:
                # Find this Value node by FQN and trace its source
                arg_value_matches = index.resolve_symbol(arg.value_ref_symbol)
                if arg_value_matches:
                    arg_value_node = arg_value_matches[0]
                    if arg_value_node.kind == "Value":
                        children = build_value_source_chain(
                            index, arg_value_node.id, depth + 1, max_depth, limit, visited
                        )
                        entry.children.extend(children)
            elif arg.source_chain:
                # Result argument (e.g., $input->customerEmail) — trace the
                # receiver Value to follow data flow across method boundaries
                for step in arg.source_chain:
                    on_fqn = step.get("on")
                    if on_fqn:
                        on_matches = index.resolve_symbol(on_fqn)
                        if on_matches:
                            on_node = on_matches[0]
                            if on_node.kind == "Value":
                                children = build_value_source_chain(
                                    index, on_node.id, depth + 1, max_depth, limit, visited
                                )
                                entry.children.extend(children)

    return [entry]


def build_parameter_uses(
    index: "SoTIndex", param_value_id: str, param_node, depth: int, max_depth: int,
    limit: int, visited: set
) -> list[ContextEntry]:
    """Find callers of a parameter Value via argument edges with matching parameter FQN.

    Searches all argument edges in the graph where the `parameter` field matches
    this Value's FQN, then traces the source of each caller's argument Value.

    Args:
        index: The SoT index.
        param_value_id: ID of the parameter Value node.
        param_node: The parameter Value NodeData.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        visited: Visited Value IDs for cycle detection.

    Returns:
        List of ContextEntry representing caller-provided sources.
    """
    entries = []
    param_fqn = param_node.fqn

    # Search argument edges where parameter field matches this FQN
    for edge in index.edges:
        if edge.type != "argument":
            continue
        if edge.parameter != param_fqn:
            continue

        # Found a caller's argument edge
        caller_call_id = edge.source  # Call node in the caller
        caller_value_id = edge.target  # Value passed by the caller

        call_node = index.nodes.get(caller_call_id)
        caller_value = index.nodes.get(caller_value_id)
        if not call_node or not caller_value:
            continue

        # Find the containing method of the caller
        scope_id = get_containing_scope(index, caller_call_id)
        scope_node = index.nodes.get(scope_id) if scope_id else None

        call_line = call_node.range.get("start_line") if call_node.range else None

        entry = ContextEntry(
            depth=depth,
            node_id=scope_id or caller_call_id,
            fqn=scope_node.fqn if scope_node else call_node.fqn,
            kind=scope_node.kind if scope_node else call_node.kind,
            file=call_node.file,
            line=call_line,
            signature=scope_node.signature if scope_node else None,
            children=[],
            crossed_from=param_fqn,
        )

        # Trace the caller's argument Value's source chain (recurse with depth+1)
        if depth < max_depth and caller_value_id not in visited:
            child_entries = build_value_source_chain(
                index, caller_value_id, depth + 1, max_depth, limit, visited
            )
            entry.children.extend(child_entries)

        entries.append(entry)
        if len(entries) >= limit:
            break

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries
