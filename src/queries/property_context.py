"""Property-specific context handlers for USED BY and USES sections.

Handles property access grouping, deduplication, and depth expansion
for property nodes.

All functions are standalone with an explicit `index` parameter.
"""

from typing import Optional, Callable, TYPE_CHECKING

from ..models import ContextEntry, MemberRef, NodeData
from .graph_utils import (
    member_display_name,
    resolve_receiver_identity,
    get_single_argument_info,
)
from .reference_types import (
    get_reference_type_from_call,
    get_containing_scope,
    build_access_chain,
)
from .value_context import (
    build_value_consumer_chain,
    build_value_source_chain,
)

if TYPE_CHECKING:
    from ..graph import SoTIndex


def build_property_uses(
    index: "SoTIndex", property_id: str, depth: int, max_depth: int, limit: int
) -> list[ContextEntry]:
    """Build USES chain for a Property node.

    For promoted constructor properties: follow assigned_from edge to
    Value(parameter), then trace callers via argument edges. Shows only
    the argument matching this property (filtered), not all constructor args.

    For other properties: follow assigned_from edges to source Values.
    """
    if depth > max_depth:
        return []

    property_node = index.nodes.get(property_id)
    if not property_node:
        return []

    visited: set = set()

    # Check for assigned_from edges (promoted property -> Value(parameter))
    assigned_edges = index.outgoing[property_id].get("assigned_from", [])
    for edge in assigned_edges:
        source_node = index.nodes.get(edge.target)
        if source_node and source_node.kind == "Value" and source_node.value_kind == "parameter":
            # Promoted property: trace callers but filter to relevant arg only
            return build_property_callers_filtered(
                index, source_node, property_node, depth, max_depth, limit, visited
            )

    # No assigned_from from parameter: property may be set by DI container or direct assignment
    return []


def build_property_callers_filtered(
    index: "SoTIndex", param_node: NodeData, property_node: NodeData,
    depth: int, max_depth: int, limit: int, visited: set
) -> list[ContextEntry]:
    """Find callers of a constructor parameter, showing only the matching argument.

    Instead of showing all N constructor arguments, filters to show only
    the argument that maps to the queried property's parameter.
    """
    entries = []
    param_fqn = param_node.fqn

    # Search argument edges where parameter field matches this FQN
    for edge_data in index.edges:
        if edge_data.type != "argument":
            continue
        if edge_data.parameter != param_fqn:
            continue

        # Found a Call that passes a value for this parameter
        call_id = edge_data.source
        call_node = index.nodes.get(call_id)
        if not call_node:
            continue

        # The argument Value being passed
        caller_value_id = edge_data.target
        caller_value_node = index.nodes.get(caller_value_id)

        # Find containing method
        scope_id = get_containing_scope(index, call_id)
        scope_node = index.nodes.get(scope_id) if scope_id else None

        call_line = call_node.range.get("start_line") if call_node.range else None

        # Build a single filtered ArgumentInfo for just this property's arg
        filtered_args = []
        arg_info = get_single_argument_info(index,
            call_id, param_fqn, caller_value_id
        )
        if arg_info:
            filtered_args.append(arg_info)

        entry = ContextEntry(
            depth=depth,
            node_id=scope_id or call_id,
            fqn=scope_node.fqn if scope_node else call_node.fqn,
            kind=scope_node.kind if scope_node else call_node.kind,
            file=call_node.file,
            line=call_line,
            signature=scope_node.signature if scope_node else None,
            children=[],
            arguments=filtered_args,
            crossed_from=param_fqn,
        )

        # Trace the caller's argument Value source at depth+1
        if depth < max_depth and caller_value_id and caller_value_id not in visited:
            child_entries = build_value_source_chain(index,
                caller_value_id, depth + 1, max_depth, limit, visited
            )
            entry.children.extend(child_entries)

        entries.append(entry)
        if len(entries) >= limit:
            break

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


def build_property_used_by(
    index: "SoTIndex", property_id: str, depth: int, max_depth: int, limit: int,
    caller_chain_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Build USED BY chain for a Property node.

    Groups property accesses by containing method with (xN) dedup.
    For service properties: shows access -> method_call chain at depth 2.
    For entity properties: groups by containing method with (xN) counts.

    Args:
        index: The SoT index.
        property_id: Property node ID.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        caller_chain_fn: Optional callback for building caller chain at depth+2.
            Signature: (method_id, depth, max_depth) -> list[ContextEntry]
            Passed from orchestrator to avoid importing class_context.
    """
    if depth > max_depth:
        return []

    property_node = index.nodes.get(property_id)
    if not property_node:
        return []

    # Find all Call nodes that target this Property (via 'calls' edges)
    call_ids = index.get_calls_to(property_id)

    # Group accesses by containing method
    # Key: scope_id (method), Value: list of (call_id, call_node, receiver_info)
    method_groups: dict[str, list] = {}
    for call_id in call_ids:
        call_node = index.nodes.get(call_id)
        if not call_node:
            continue
        scope_id = get_containing_scope(index, call_id)
        if not scope_id:
            continue
        key = scope_id
        if key not in method_groups:
            method_groups[key] = []
        method_groups[key].append((call_id, call_node))

    entries = []
    visited = set()

    for scope_id, calls in method_groups.items():
        if len(entries) >= limit:
            break

        scope_node = index.nodes.get(scope_id)
        if not scope_node:
            continue

        # Use first call for representative info
        first_call_id, first_call_node = calls[0]
        first_line = first_call_node.range.get("start_line") if first_call_node.range else None
        reference_type = get_reference_type_from_call(index, first_call_id)
        ac, acs, ok, of, ol = resolve_receiver_identity(index, first_call_id)

        # Collect unique receivers across all accesses in this method
        receiver_names = []
        for call_id, call_node in calls:
            c_ac, _, c_ok, _, _ = resolve_receiver_identity(index, call_id)
            chain = build_access_chain(index, call_id)
            recv_id = index.get_receiver(call_id)
            if recv_id:
                recv_node = index.nodes.get(recv_id)
                if recv_node and recv_node.kind == "Value":
                    if recv_node.value_kind in ("local", "parameter"):
                        rname = recv_node.name
                        rkind = "local" if recv_node.value_kind == "local" else "param"
                        if rname and rname not in [r[0] for r in receiver_names]:
                            receiver_names.append((rname, rkind))
                    elif recv_node.value_kind == "result" and c_ac:
                        # ISSUE-C: Chain access — use access chain as display name
                        if c_ac not in [r[0] for r in receiver_names]:
                            receiver_names.append((c_ac, "property"))
            elif c_ok == "self":
                if ("$this", "self") not in receiver_names:
                    receiver_names.append(("$this", "self"))

        # Build sites for (xN) dedup
        access_count = len(calls)
        sites = None
        if access_count > 1:
            sites = []
            for call_id, call_node in calls:
                site_line = call_node.range.get("start_line") if call_node.range else None
                sites.append({"method": scope_node.fqn, "line": site_line})

        member_ref = MemberRef(
            target_name=member_display_name(property_node),
            target_fqn=property_node.fqn,
            target_kind="Property",
            file=first_call_node.file,
            line=first_line,
            reference_type=reference_type,
            access_chain=ac,
            access_chain_symbol=acs,
            on_kind=ok if not receiver_names else receiver_names[0][1],
            on_file=of,
            on_line=ol,
        )

        # Build on display string from receiver_names (no tags — onKind is separate)
        on_display = None
        if receiver_names:
            parts = []
            for rname, rkind in receiver_names:
                if rkind == "self":
                    # Show full property access expression for self-property
                    prop_name = property_node.name
                    if not prop_name.startswith("$"):
                        prop_name = "$" + prop_name
                    parts.append(f"$this->{prop_name.lstrip('$')} ({property_node.fqn})")
                else:
                    parts.append(rname)
            on_display = ", ".join(parts)

        entry = ContextEntry(
            depth=depth,
            node_id=scope_id,
            fqn=scope_node.fqn,
            kind=scope_node.kind,
            file=first_call_node.file,
            line=first_line,
            signature=scope_node.signature,
            children=[],
            member_ref=member_ref,
            ref_type=reference_type or "property_access",
            callee=member_display_name(property_node),
            on=on_display,
            on_kind="property" if (receiver_names and receiver_names[0][1] == "self") else (receiver_names[0][1] if receiver_names else ok),
            sites=sites,
        )

        # Depth 2: trace result Values of each access
        if depth < max_depth:
            # Collect result Value IDs produced by property access calls
            # so we can filter constructor args to only the relevant one
            property_result_value_ids: set[str] = set()
            for call_id, call_node in calls:
                result_id = index.get_produces(call_id)
                if result_id:
                    property_result_value_ids.add(result_id)
                if result_id and result_id not in visited:
                    child_entries = build_value_consumer_chain(index,
                        result_id, depth + 1, max_depth, limit, visited
                    )
                    entry.children.extend(child_entries)

            # ISSUE-D: Deduplicate children from multiple access sites.
            # When a method accesses the same property multiple times (e.g.,
            # $contact->email and $contact->phone), each produces a result
            # Value that may feed into the same downstream consumer (e.g.,
            # CustomerOutput::__construct()). Deduplicate by (fqn, file, line).
            seen_children: set[tuple] = set()
            deduped_children: list[ContextEntry] = []
            for child in entry.children:
                key = (child.fqn, child.file, child.line)
                if key not in seen_children:
                    seen_children.add(key)
                    deduped_children.append(child)
            entry.children = deduped_children

            # ISSUE-O: Filter constructor/method args to only the one
            # matching the queried property. For each depth-2 child entry,
            # keep only arguments whose value traces back to our property.
            prop_name_bare = property_node.name.lstrip("$")
            for child_entry in entry.children:
                if child_entry.arguments:
                    filtered_args = []
                    for arg in child_entry.arguments:
                        # Check if value_expr references the queried property
                        # e.g. "$savedOrder->id" ends with the property name "id"
                        if arg.value_expr and arg.value_expr.endswith(
                            f"->{prop_name_bare}"
                        ):
                            filtered_args.append(arg)
                        elif arg.value_expr and arg.value_expr.endswith(
                            f"->{property_node.name}"
                        ):
                            filtered_args.append(arg)
                        # Also check source_chain for property FQN reference
                        elif arg.source_chain:
                            for step in arg.source_chain:
                                if isinstance(step, dict) and step.get("fqn") == property_node.fqn:
                                    filtered_args.append(arg)
                                    break
                    # Only apply filter if we found matches; if none match,
                    # keep all args (better to show too much than nothing)
                    if filtered_args:
                        child_entry.arguments = filtered_args

            # ISSUE-S+J fix: add upstream callers instead of downstream reads
            # If depth-2 children exist, replace their depth-3 children with callers
            # If no depth-2 children, add callers directly as depth-2 entries
            if caller_chain_fn:
                if entry.children and depth + 1 < max_depth:
                    caller_entries = caller_chain_fn(
                        scope_id, depth + 2, max_depth
                    )
                    if caller_entries:
                        for child in entry.children:
                            child.children = caller_entries
                elif not entry.children:
                    caller_entries = caller_chain_fn(
                        scope_id, depth + 1, max_depth
                    )
                    if caller_entries:
                        entry.children = caller_entries

        entries.append(entry)

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries
