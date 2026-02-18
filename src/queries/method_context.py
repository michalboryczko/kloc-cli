"""Execution flow builder for method/function USES section.

Handles variable-centric execution flow traversal, type reference
collection, and orphan property access filtering.

All functions are standalone with an explicit `index` parameter.

CIRCULAR DEPENDENCY NOTE:
build_execution_flow() calls get_implementations_for_node() (in polymorphic.py)
and get_implementations_for_node() calls build_execution_flow(). This is broken
by deferred builder callbacks: both functions accept an optional callback parameter
that the orchestrator (context.py) wires at runtime.
"""

from typing import Optional, Callable, TYPE_CHECKING

from ..models import ContextEntry, MemberRef, ArgumentInfo
from .graph_utils import (
    member_display_name,
    resolve_receiver_identity,
    get_argument_info,
    find_result_var,
    find_local_value_for_call,
    build_external_call_fqn,
)
from .reference_types import (
    get_reference_type_from_call,
    find_call_for_usage,
    _infer_reference_type,
)

if TYPE_CHECKING:
    from ..graph import SoTIndex


def get_type_references(
    index: "SoTIndex", method_id: str, depth: int, cycle_guard: set, count: list, limit: int
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

    edges = index.get_deps(method_id)
    for edge in edges:
        target_id = edge.target
        if target_id in cycle_guard or target_id in local_visited:
            continue

        target_node = index.nodes.get(target_id)
        if not target_node:
            continue

        # Only include Class/Interface/Trait/Enum targets (type references)
        if target_node.kind not in ("Class", "Interface", "Trait", "Enum"):
            continue

        # Infer reference type — only keep type-related ones
        ref_type = _infer_reference_type(edge, target_node, index)
        if ref_type not in TYPE_KINDS:
            continue

        # Check if there's a Call node (constructor) for this target
        file = edge.location.get("file") if edge.location else target_node.file
        line = edge.location.get("line") if edge.location else target_node.start_line
        call_node_id = find_call_for_usage(index, method_id, target_id, file, line)
        if call_node_id:
            # This is a constructor call — it will be picked up by execution flow
            continue

        local_visited.add(target_id)
        if count[0] >= limit:
            break
        count[0] += 1

        member_ref = MemberRef(
            target_name=member_display_name(target_node),
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


def build_execution_flow(
    index: "SoTIndex", method_id: str, depth: int, max_depth: int,
    limit: int, cycle_guard: set, count: list,
    include_impl: bool = False, shown_impl_for: set | None = None,
    implementations_fn: Callable | None = None,
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
        index: The SoT index.
        method_id: The Method/Function node ID to build flow for.
        depth: Current depth level in the tree.
        max_depth: Maximum depth to expand.
        limit: Maximum number of entries.
        cycle_guard: Set of node IDs to prevent infinite recursion.
        count: Mutable list[int] tracking total entries created.
        include_impl: Whether to attach implementations for interface methods.
        shown_impl_for: Set tracking nodes with implementations already shown.
        implementations_fn: Callback to get implementations (breaks circular dep with polymorphic).
            Signature: (node, depth, max_depth, limit, visited, count, shown_impl_for) -> list[ContextEntry]
    """
    if depth > max_depth or count[0] >= limit:
        return []

    if shown_impl_for is None:
        shown_impl_for = set()

    children = index.get_contains_children(method_id)

    # Step 1: Collect all Call children
    call_children = []
    for child_id in children:
        child = index.nodes.get(child_id)
        if child and child.kind == "Call":
            call_children.append((child_id, child))

    # Step 2: Identify consumed calls — calls whose result Value is used
    # as a receiver or argument source by another call in the same method.
    consumed: set[str] = set()
    for call_id, call_node in call_children:
        # Check receiver: if the receiver Value is a result of another call
        recv_id = index.get_receiver(call_id)
        if recv_id:
            recv_node = index.nodes.get(recv_id)
            if recv_node and recv_node.kind == "Value" and recv_node.value_kind == "result":
                source_call_id = index.get_source_call(recv_id)
                if source_call_id:
                    consumed.add(source_call_id)
        # Check arguments: if any arg Value is a result of another call
        arg_edges = index.get_arguments(call_id)
        for arg_id, _, _, _ in arg_edges:
            arg_node = index.nodes.get(arg_id)
            if arg_node and arg_node.value_kind == "result":
                src = index.get_source_call(arg_id)
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

        target_id = index.get_call_target(child_id)

        # External call (callee has no node in graph, e.g., vendor method)
        if not target_id:
            # Use the Call node's own data to build the entry
            count[0] += 1
            call_line = child.range.get("start_line") if child.range else None
            ac, acs, ok, of, ol = resolve_receiver_identity(index, child_id)
            arguments = get_argument_info(index, child_id)
            # Derive FQN from receiver type + call name
            ext_fqn = build_external_call_fqn(index, child_id, child)
            reference_type = get_reference_type_from_call(index, child_id)

            # ISSUE-G: Build display name from call node for external calls
            ext_display_name = ""
            if child.name:
                ck = child.call_kind or ""
                if ck in ("method", "method_static", "function", ""):
                    ext_display_name = child.name if child.name.endswith("()") else f"{child.name}()"
                elif ck == "access":
                    ext_display_name = f"${child.name}" if not child.name.startswith("$") else child.name
                else:
                    ext_display_name = child.name

            member_ref = MemberRef(
                target_name=ext_display_name,
                target_fqn=ext_fqn,
                target_kind=child.call_kind or "method",
                file=child.file,
                line=call_line,
                reference_type=reference_type,
                access_chain=ac,
                access_chain_symbol=acs,
                on_kind=ok,
                on_file=of,
                on_line=ol,
            )

            local_value = find_local_value_for_call(index, child_id)
            if local_value:
                var_type = None
                type_of_edges = index.outgoing[local_value.id].get("type_of", [])
                if type_of_edges:
                    type_node = index.nodes.get(type_of_edges[0].target)
                    if type_node:
                        var_type = type_node.name

                source_call_entry = ContextEntry(
                    depth=depth,
                    node_id=child_id,
                    fqn=ext_fqn,
                    kind=child.call_kind or "Method",
                    file=child.file,
                    line=call_line,
                    signature=None,
                    children=[],
                    implementations=[],
                    member_ref=member_ref,
                    arguments=arguments,
                    result_var=None,
                    entry_type="call",
                )
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
                result_var = find_result_var(index, child_id)
                entry = ContextEntry(
                    depth=depth,
                    node_id=child_id,
                    fqn=ext_fqn,
                    kind=child.call_kind or "Method",
                    file=child.file,
                    line=call_line,
                    signature=None,
                    children=[],
                    implementations=[],
                    member_ref=member_ref,
                    arguments=arguments,
                    result_var=result_var,
                    entry_type="call",
                )
            # External calls cannot recurse (no target method to expand)
            entries.append(entry)
            continue

        if target_id in local_visited:
            continue
        local_visited.add(target_id)

        target_node = index.nodes.get(target_id)
        if not target_node:
            continue

        if target_id in cycle_guard:
            continue

        count[0] += 1
        reference_type = get_reference_type_from_call(index, child_id)
        ac, acs, ok, of, ol = resolve_receiver_identity(index, child_id)
        arguments = get_argument_info(index, child_id)
        call_line = child.range.get("start_line") if child.range else None

        member_ref = MemberRef(
            target_name=member_display_name(target_node),
            target_fqn=target_node.fqn,
            target_kind=target_node.kind,
            file=child.file,
            line=call_line,
            reference_type=reference_type,
            access_chain=ac,
            access_chain_symbol=acs,
            on_kind=ok,
            on_file=of,
            on_line=ol,
        )

        # Check if this call's result is assigned to a local variable
        local_value = find_local_value_for_call(index, child_id)

        if local_value:
            # Kind 1: Variable entry with nested source_call
            # Resolve variable type from type_of edge
            var_type = None
            type_of_edges = index.outgoing[local_value.id].get("type_of", [])
            if type_of_edges:
                type_node = index.nodes.get(type_of_edges[0].target)
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
                if implementations_fn:
                    source_call_entry.implementations = implementations_fn(
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
            result_var = find_result_var(index, child_id)

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
                if implementations_fn:
                    entry.implementations = implementations_fn(
                        target_node, depth, max_depth, limit, cycle_guard, count, shown_impl_for
                    )

        # Depth expansion: recurse into callee's execution flow
        if depth < max_depth and target_node.kind in ("Method", "Function"):
            entry.children = build_execution_flow(
                index, target_id, depth + 1, max_depth, limit,
                cycle_guard | {target_id}, count,
                include_impl=include_impl, shown_impl_for=shown_impl_for,
                implementations_fn=implementations_fn,
            )

        entries.append(entry)

    # Filter orphan property accesses consumed by non-Call expressions
    entries = filter_orphan_property_accesses(entries)

    # Sort by line number for execution order
    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


def filter_orphan_property_accesses(entries: list[ContextEntry]) -> list[ContextEntry]:
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
