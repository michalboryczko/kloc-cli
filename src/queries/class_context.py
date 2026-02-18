"""Class-specific context handlers for USED BY and USES sections.

Handles class USED BY grouping (instantiation, extends, property_type,
method_call, property_access, parameter_type), class USES dedup and
behavioral depth-2 expansion, and caller chain traversal.

All functions are standalone with an explicit `index` parameter.

CROSS-MODULE NOTE:
interface_context.py imports build_caller_chain_for_method,
build_class_used_by_depth_callers, build_class_uses_recursive,
build_implements_depth2, and offset_entry_depths from this module.
"""

from typing import Optional, Callable, TYPE_CHECKING

from ..models import ContextEntry, NodeData
from ..models.edge import EdgeData
from .graph_utils import (
    member_display_name,
    resolve_receiver_identity,
    resolve_containing_method,
    is_internal_reference,
    get_argument_info,
    resolve_access_chain_symbol,
)
from .reference_types import (
    CHAINABLE_REFERENCE_TYPES,
    get_reference_type_from_call,
    find_call_for_usage,
    _infer_reference_type,
)
from .used_by_handlers import EdgeContext, EntryBucket, USED_BY_HANDLERS

if TYPE_CHECKING:
    from ..graph import SoTIndex


# Reference type priority for sorting USED BY entries
REF_TYPE_PRIORITY = {
    "instantiation": 0,
    "extends": 1,
    "implements": 1,
    "property_type": 2,
    "method_call": 3,
    "static_call": 3,
    "property_access": 4,
    "parameter_type": 5,
    "return_type": 5,
    "type_hint": 6,
}


def build_class_used_by(
    index: "SoTIndex", start_id: str, max_depth: int, limit: int,
    include_impl: bool = False,
    caller_chain_for_method_fn: Callable | None = None,
    injection_point_calls_fn: Callable | None = None,
    interface_injection_point_calls_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Build USED BY tree for a Class node with grouping, sorting, and dedup.

    Collects all incoming edges, classifies by reference type, groups into
    buckets (instantiation, extends, property_type, method_call,
    property_access, parameter_type/return_type), sorts by priority,
    and deduplicates property accesses by FQN with (xN) per method.

    Self-property accesses (class's own methods accessing its own properties)
    are excluded. Method calls through injected properties are shown at depth 2
    under the property_type entry (not as separate depth-1 entries).
    """
    start_node = index.nodes.get(start_id)
    if not start_node:
        return []

    # Collect all incoming edges grouped by source
    source_groups = index.get_usages_grouped(start_id)

    # Pass 1: Identify which containing classes have property_type refs to this class.
    # Method calls from those classes through the property should NOT appear at depth 1.
    classes_with_injection: set[str] = set()
    for source_id, edges in source_groups.items():
        source_node = index.nodes.get(source_id)
        if not source_node:
            continue
        for edge in edges:
            target_node = index.nodes.get(edge.target)
            if not target_node:
                continue
            ref_type = _infer_reference_type(edge, target_node, index)
            if ref_type == "property_type":
                # Find the containing class of this source
                cls_id = source_id
                node = source_node
                while node and node.kind not in ("Class", "Interface", "Trait", "Enum", "File"):
                    cls_id = index.get_contains_parent(cls_id)
                    node = index.nodes.get(cls_id) if cls_id else None
                if node and node.kind in ("Class", "Interface", "Trait", "Enum"):
                    classes_with_injection.add(cls_id)

    # Pass 2: Classify each edge into buckets using handler registry
    bucket = EntryBucket()
    visited_sources: set[str] = {start_id}

    # Pre-collect extends/implements relationships (not in uses edges)
    extends_children_ids = index.get_extends_children(start_id)
    for child_id in extends_children_ids:
        child_node = index.nodes.get(child_id)
        if child_node and child_id not in visited_sources:
            visited_sources.add(child_id)
            entry = ContextEntry(
                depth=1,
                node_id=child_id,
                fqn=child_node.fqn,
                kind=child_node.kind,
                file=child_node.file,
                line=child_node.start_line,
                ref_type="extends",
                children=[],
            )
            bucket.extends.append(entry)

    implementor_ids = index.get_implementors(start_id)
    for impl_id in implementor_ids:
        impl_node = index.nodes.get(impl_id)
        if impl_node and impl_id not in visited_sources:
            visited_sources.add(impl_id)
            entry = ContextEntry(
                depth=1,
                node_id=impl_id,
                fqn=impl_node.fqn,
                kind=impl_node.kind,
                file=impl_node.file,
                line=impl_node.start_line,
                ref_type="implements",
                children=[],
            )
            bucket.extends.append(entry)

    for source_id, edges in source_groups.items():
        if source_id in visited_sources:
            continue

        # R3: Filter out internal self-references
        if is_internal_reference(index, source_id, start_id):
            continue

        source_node = index.nodes.get(source_id)
        if not source_node:
            continue

        if source_node.kind == "File":
            continue

        visited_sources.add(source_id)

        for edge in edges:
            target_node = index.nodes.get(edge.target)
            if not target_node:
                continue

            file = edge.location.get("file") if edge.location else source_node.file
            line = edge.location.get("line") if edge.location else source_node.start_line

            call_node_id = find_call_for_usage(index, source_id, edge.target, file, line)
            if call_node_id:
                ref_type = get_reference_type_from_call(index, call_node_id)
            else:
                ref_type = _infer_reference_type(edge, target_node, index)

            handler = USED_BY_HANDLERS.get(ref_type)
            if handler:
                ctx = EdgeContext(
                    index=index,
                    start_id=start_id,
                    source_id=source_id,
                    source_node=source_node,
                    edge=edge,
                    target_node=target_node,
                    ref_type=ref_type,
                    file=file,
                    line=line,
                    call_node_id=call_node_id,
                    classes_with_injection=classes_with_injection,
                )
                handler.handle(ctx, bucket)

    # Build property access group entries
    property_access_entries: list[ContextEntry] = []
    for prop_fqn, method_groups in bucket.property_access_groups.items():
        total_accesses = sum(len(g["lines"]) for g in method_groups)
        total_methods = len(method_groups)

        # Build depth-2 children: per-method breakdown
        method_children: list[ContextEntry] = []
        if max_depth >= 2:
            for group in method_groups:
                method_short = group["method_fqn"].split("::")[-1] if "::" in group["method_fqn"] else group["method_fqn"]
                method_node = index.nodes.get(group["method_id"])
                if method_node and method_node.kind == "Method" and not method_short.endswith("()"):
                    method_short = method_short + "()"
                class_part = group["method_fqn"].split("::")[0].split("\\")[-1] if "::" in group["method_fqn"] else ""
                child_display = f"{class_part}::{method_short}" if class_part else method_short

                count = len(group["lines"])
                lines_sorted = sorted(l for l in group["lines"] if l is not None)
                first_line = lines_sorted[0] if lines_sorted else None

                sites = None
                if count > 1 and lines_sorted:
                    sites = [{"line": l} for l in lines_sorted]

                child_entry = ContextEntry(
                    depth=2,
                    node_id=group["method_id"],
                    fqn=child_display,
                    kind=group["method_kind"],
                    file=group["file"],
                    line=first_line,
                    ref_type="property_access",
                    on=group["on_expr"],
                    on_kind=group["on_kind"],
                    sites=sites,
                    children=[],
                )

                if max_depth >= 3 and group["method_id"]:
                    child_entry.children = build_class_used_by_depth_callers(
                        index, group["method_id"], 3, max_depth, set(visited_sources)
                    )

                method_children.append(child_entry)

        method_children.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

        # Use the full property FQN but with short class name for display
        prop_short = prop_fqn.split("::")[-1] if "::" in prop_fqn else prop_fqn
        class_short = prop_fqn.split("::")[0].split("\\")[-1] if "::" in prop_fqn else ""
        display_fqn = f"{class_short}::{prop_short}" if class_short else prop_short

        prop_entry = ContextEntry(
            depth=1,
            node_id=prop_fqn,
            fqn=display_fqn,
            kind="PropertyGroup",
            file=None,
            line=None,
            ref_type="property_access",
            children=method_children,
            access_count=total_accesses,
            method_count=total_methods,
        )
        property_access_entries.append(prop_entry)

    # Pass 3: Via-interface usedBy — collect injection points from interfaces
    # this class implements. If a property is typed to the interface, it
    # indirectly references this concrete class.
    via_interface_entries: list[ContextEntry] = []
    impl_ids = index.get_implements(start_id)
    # Also check extends chain for interfaces
    extends_parent_id = index.get_extends_parent(start_id)
    while extends_parent_id:
        impl_ids.extend(index.get_implements(extends_parent_id))
        extends_parent_id = index.get_extends_parent(extends_parent_id)

    for iface_id in impl_ids:
        iface_node = index.nodes.get(iface_id)
        if not iface_node:
            continue
        # Collect property_type injection points for this interface
        iface_source_groups = index.get_usages_grouped(iface_id)
        for source_id, edges in iface_source_groups.items():
            source_node = index.nodes.get(source_id)
            if not source_node:
                continue
            for edge in edges:
                target_node = index.nodes.get(edge.target)
                if not target_node:
                    continue
                ref_type = _infer_reference_type(edge, target_node, index)
                if ref_type != "property_type":
                    continue
                # Resolve the property node
                prop_fqn = None
                prop_node = None
                if source_node.kind == "Property":
                    prop_fqn = source_node.fqn
                    prop_node = source_node
                elif source_node.kind in ("Method", "Function"):
                    containing_class_id = index.get_contains_parent(source_id)
                    if containing_class_id:
                        for child_id in index.get_contains_children(containing_class_id):
                            child = index.nodes.get(child_id)
                            if child and child.kind == "Property":
                                for th_edge in index.outgoing[child_id].get("type_hint", []):
                                    if th_edge.target == iface_id:
                                        prop_fqn = child.fqn
                                        prop_node = child
                                        break
                                if prop_fqn:
                                    break
                if not prop_fqn or not prop_node:
                    continue
                if prop_fqn in bucket.seen_property_type_props:
                    continue
                bucket.seen_property_type_props.add(prop_fqn)

                entry = ContextEntry(
                    depth=1,
                    node_id=prop_node.id,
                    fqn=prop_fqn,
                    kind="Property",
                    file=prop_node.file,
                    line=prop_node.start_line,
                    ref_type="property_type",
                    via=iface_node.fqn,
                    children=[],
                )
                via_interface_entries.append(entry)

    via_interface_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

    # Sort within each group
    bucket.instantiation.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    bucket.extends.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    bucket.property_type.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    bucket.method_call.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    property_access_entries.sort(key=lambda e: e.fqn)
    bucket.param_return.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

    # Expand depth-2
    if max_depth >= 2:
        for entry in bucket.instantiation:
            entry.children = build_class_used_by_depth_callers(
                index, entry.node_id, 2, max_depth, set(visited_sources)
            )
        for entry in bucket.extends:
            if entry.ref_type in ("extends", "implements"):
                entry.children = build_override_methods_for_subclass(
                    index, entry.node_id, start_id, 2, max_depth
                )
        for entry in bucket.property_type:
            entry.children = build_injection_point_calls(
                index, entry.node_id, start_id, 2, max_depth,
                caller_chain_for_method_fn=caller_chain_for_method_fn,
            )
        for entry in via_interface_entries:
            # For via-interface entries, find the interface ID from the via FQN
            iface_nodes = index.resolve_symbol(entry.via) if entry.via else []
            iface_id = iface_nodes[0].id if iface_nodes else None
            if iface_id:
                if interface_injection_point_calls_fn:
                    entry.children = interface_injection_point_calls_fn(
                        entry.node_id, iface_id, 2, max_depth
                    )
                else:
                    # Fallback: import locally to avoid circular dependency
                    from .interface_context import build_interface_injection_point_calls
                    entry.children = build_interface_injection_point_calls(
                        index, entry.node_id, iface_id, 2, max_depth
                    )

    # Combine in priority order
    all_entries = (
        bucket.instantiation
        + bucket.extends
        + bucket.property_type
        + via_interface_entries
        + bucket.method_call
        + property_access_entries
        + bucket.param_return
    )

    return all_entries[:limit]


def build_caller_chain(
    index: "SoTIndex", call_site_id: str, depth: int, max_depth: int,
    visited: set[str] | None = None
) -> list[ContextEntry]:
    """Build upstream caller chain from a call site node.

    Given a Call node (e.g., a method_call or property_access at depth 2),
    find the containing method, then find callers of that method using
    override root resolution to handle interface/concrete method lookups.
    """
    if depth > max_depth:
        return []

    if visited is None:
        visited = set()

    from .reference_types import get_containing_scope
    # Find the containing method of the call site
    scope_id = get_containing_scope(index, call_site_id)
    if not scope_id or scope_id in visited:
        return []

    return build_caller_chain_for_method(index, scope_id, depth, max_depth, visited)


def build_caller_chain_for_method(
    index: "SoTIndex", method_id: str, depth: int, max_depth: int,
    visited: set[str] | None = None
) -> list[ContextEntry]:
    """Build upstream caller chain starting from a known method ID.

    Finds callers of the given method using override root resolution:
    callers may reference the interface method rather than the concrete
    implementation, so we check both.
    """
    if depth > max_depth:
        return []

    if visited is None:
        visited = set()

    method_node = index.nodes.get(method_id)
    if not method_node or method_node.kind not in ("Method", "Function"):
        return []

    if method_id in visited:
        return []
    visited.add(method_id)

    # Find callers via override root resolution
    override_root = index.get_override_root(method_id)
    caller_method_ids: set[str] = set()

    # Collect callers of the method itself
    collect_callers_from_usages(index, method_id, visited, caller_method_ids)

    # Also collect callers of the override root (interface method)
    if override_root and override_root != method_id:
        collect_callers_from_usages(index, override_root, visited, caller_method_ids)

    # Build caller entries
    entries = []
    for caller_id in caller_method_ids:
        caller_node = index.nodes.get(caller_id)
        if not caller_node:
            continue

        display_fqn = caller_node.fqn
        if caller_node.kind == "Method" and not display_fqn.endswith("()"):
            display_fqn += "()"

        entry = ContextEntry(
            depth=depth,
            node_id=caller_id,
            fqn=display_fqn,
            kind=caller_node.kind,
            file=caller_node.file,
            line=caller_node.start_line,
            ref_type="caller",
            children=[],
            crossed_from=method_node.fqn,  # ISSUE-H: crossed from the method being expanded
        )

        # Recursive caller expansion
        if depth < max_depth:
            entry.children = build_caller_chain_for_method(
                index, caller_id, depth + 1, max_depth, visited.copy()
            )

        entries.append(entry)

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


def collect_callers_from_usages(
    index: "SoTIndex", target_method_id: str, visited: set[str], result: set[str]
) -> None:
    """Collect containing methods of all chainable usages of a target method."""
    for source_id, edges in index.get_usages_grouped(target_method_id).items():
        for edge in edges:
            call_node_id = find_call_for_usage(
                index, source_id, edge.target,
                edge.location.get("file") if edge.location else None,
                edge.location.get("line") if edge.location else None,
            )
            if call_node_id:
                ref_type = get_reference_type_from_call(index, call_node_id)
            else:
                target_node = index.nodes.get(edge.target)
                ref_type = _infer_reference_type(edge, target_node, index) if target_node else "uses"
            if ref_type in CHAINABLE_REFERENCE_TYPES:
                containing = resolve_containing_method(index, source_id)
                if containing and containing not in visited:
                    result.add(containing)


def build_class_used_by_depth_callers(
    index: "SoTIndex", method_id: str, depth: int, max_depth: int, visited: set[str]
) -> list[ContextEntry]:
    """Find callers of a method for depth expansion in class USED BY.

    For instantiation and property_access depth expansion: find who calls
    the containing method.
    """
    if depth > max_depth:
        return []

    method_node = index.nodes.get(method_id)
    if not method_node or method_node.kind not in ("Method", "Function"):
        return []

    entries = []
    source_groups = index.get_usages_grouped(method_id)

    for source_id, edges in source_groups.items():
        if source_id in visited:
            continue
        visited.add(source_id)

        source_node = index.nodes.get(source_id)
        if not source_node:
            continue
        if source_node.kind == "File":
            continue

        for edge in edges:
            file = edge.location.get("file") if edge.location else source_node.file
            line = edge.location.get("line") if edge.location else source_node.start_line

            call_node_id = find_call_for_usage(index, source_id, edge.target, file, line)
            if call_node_id:
                ref_type = get_reference_type_from_call(index, call_node_id)
            else:
                target_node = index.nodes.get(edge.target)
                ref_type = _infer_reference_type(edge, target_node, index) if target_node else "uses"

            if ref_type not in CHAINABLE_REFERENCE_TYPES:
                continue

            # Resolve containing method
            containing_method_id = resolve_containing_method(index, source_id)
            containing_method = index.nodes.get(containing_method_id) if containing_method_id else None

            callee_name = method_node.name + "()" if method_node.kind == "Method" else method_node.name
            on_expr = None
            on_kind = None
            if call_node_id:
                ac, acs, ok, of, ol = resolve_receiver_identity(index, call_node_id)
                on_expr = ac
                on_kind = ok
                # Detect "property" from access chain pattern ($this->prop)
                if on_kind is None and on_expr and on_expr.startswith("$this->"):
                    on_kind = "property"

            display_fqn = containing_method.fqn if containing_method else source_node.fqn
            if containing_method and containing_method.kind == "Method":
                if not display_fqn.endswith("()"):
                    display_fqn += "()"

            # Use "caller" refType for depth 3+ entries (upstream callers)
            entry_ref_type = "caller" if depth >= 3 else "method_call"

            # ISSUE-I: Add argument info for method_call entries
            arguments = []
            if call_node_id:
                arguments = get_argument_info(index, call_node_id)

            entry = ContextEntry(
                depth=depth,
                node_id=containing_method_id or source_id,
                fqn=display_fqn,
                kind=containing_method.kind if containing_method else source_node.kind,
                file=file,
                line=line,
                ref_type=entry_ref_type,
                callee=callee_name if entry_ref_type != "caller" else None,
                on=on_expr,
                on_kind=on_kind,
                children=[],
                arguments=arguments,
                crossed_from=method_node.fqn,  # ISSUE-H: crossed from the method being expanded
            )

            # Further depth expansion
            if depth < max_depth and containing_method_id:
                entry.children = build_class_used_by_depth_callers(
                    index, containing_method_id, depth + 1, max_depth, visited
                )

            entries.append(entry)
            break  # One entry per source

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


def build_override_methods_for_subclass(
    index: "SoTIndex", subclass_id: str, parent_class_id: str, depth: int, max_depth: int
) -> list[ContextEntry]:
    """Build override method entries for a subclass under [extends] in USED BY.

    Shows which methods the subclass overrides from the parent class/interface.
    Uses get_overrides_parent() directly to detect overrides, which handles
    the full hierarchy chain including grandparent methods (ISSUE-E fix).
    """
    if depth > max_depth:
        return []

    entries = []

    # Find override methods in the subclass using direct overrides edge check.
    for child_id in index.get_contains_children(subclass_id):
        child = index.nodes.get(child_id)
        if not child or child.kind != "Method" or child.name == "__construct":
            continue

        # Check if this method overrides ANY ancestor method via the overrides edge
        override_parent_id = index.get_overrides_parent(child_id)
        if override_parent_id:
            entry = ContextEntry(
                depth=depth,
                node_id=child_id,
                fqn=child.fqn,
                kind="Method",
                file=child.file,
                line=child.start_line,
                signature=child.signature,
                ref_type="override",
                children=[],
            )

            # At depth 3, show what the override method does internally
            if depth < max_depth:
                entry.children = build_override_method_internals(
                    index, child_id, depth + 1, max_depth
                )

            entries.append(entry)

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


def build_override_method_internals(
    index: "SoTIndex", method_id: str, depth: int, max_depth: int
) -> list[ContextEntry]:
    """Show internal actions of an override method (property_access, method_call).

    Used at depth 3+ under [extends] > [override] entries.
    """
    if depth > max_depth:
        return []

    entries = []
    # Use execution flow to find what this method does
    call_children = []
    for child_id in index.get_contains_children(method_id):
        child = index.nodes.get(child_id)
        if child and child.kind == "Call":
            call_children.append((child_id, child))

    for call_id, call_node in call_children:
        target_id = index.get_call_target(call_id)
        if not target_id:
            continue
        target_node = index.nodes.get(target_id)
        if not target_node:
            continue

        # Filter out property access noise at depth 3
        if target_node.kind in ("Property", "StaticProperty"):
            continue

        ref_type = get_reference_type_from_call(index, call_id)
        ac, acs, ok, of, ol = resolve_receiver_identity(index, call_id)
        call_line = call_node.range.get("start_line") if call_node.range else None

        entry = ContextEntry(
            depth=depth,
            node_id=target_id,
            fqn=target_node.fqn,
            kind=target_node.kind,
            file=call_node.file,
            line=call_line,
            ref_type=ref_type,
            on=ac,
            on_kind=ok,
            children=[],
        )
        entries.append(entry)

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


def build_injection_point_calls(
    index: "SoTIndex", property_id: str, target_class_id: str, depth: int, max_depth: int,
    caller_chain_for_method_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Build method call entries for an injection point [property_type].

    For a property like OrderService::$orderRepository that is typed to
    target_class_id, find all method calls made through that property.
    """
    if depth > max_depth:
        return []

    prop_node = index.nodes.get(property_id)
    if not prop_node or prop_node.kind != "Property":
        return []

    # Find the containing class of this property
    containing_class_id = index.get_contains_parent(property_id)
    if not containing_class_id:
        return []

    entries = []
    seen_callees: set[str] = set()

    # Find all Call nodes in the containing class's methods that use this property as receiver
    for method_child_id in index.get_contains_children(containing_class_id):
        method_node = index.nodes.get(method_child_id)
        if not method_node or method_node.kind != "Method":
            continue

        for call_child_id in index.get_contains_children(method_child_id):
            call_child = index.nodes.get(call_child_id)
            if not call_child or call_child.kind != "Call":
                continue

            # Check if this call's receiver is our property
            recv_id = index.get_receiver(call_child_id)
            if not recv_id:
                continue

            # Resolve receiver to check if it matches our property
            chain_symbol = resolve_access_chain_symbol(index, call_child_id)
            if chain_symbol != prop_node.fqn:
                continue

            target_id = index.get_call_target(call_child_id)
            if not target_id:
                continue
            target_node = index.nodes.get(target_id)
            if not target_node:
                continue

            callee_name = target_node.name + "()" if target_node.kind == "Method" else target_node.name
            ref_type = get_reference_type_from_call(index, call_child_id)
            ac, acs, ok, of, ol = resolve_receiver_identity(index, call_child_id)
            arguments = get_argument_info(index, call_child_id)
            call_line = call_child.range.get("start_line") if call_child.range else None

            # Dedup: same callee method, collect as sites
            callee_key = target_node.fqn
            if callee_key in seen_callees:
                # Find existing entry and add site
                for existing in entries:
                    if existing.fqn == target_node.fqn:
                        if existing.sites is None:
                            existing.sites = [{"method": method_node.name, "line": existing.line}]
                            existing.line = None
                        existing.sites.append({"method": method_node.name, "line": call_line})
                        break
                continue
            seen_callees.add(callee_key)

            # ISSUE-H: crossed_from the containing class (depth-1 property_type entry)
            containing_cls_node = index.nodes.get(containing_class_id)
            crossed_from_fqn = containing_cls_node.fqn if containing_cls_node else None

            entry = ContextEntry(
                depth=depth,
                node_id=target_id,
                fqn=target_node.fqn,
                kind=target_node.kind,
                file=call_child.file,
                line=call_line,
                ref_type="method_call",
                callee=callee_name,
                on=ac,
                on_kind="property",
                arguments=arguments,
                children=[],
                crossed_from=crossed_from_fqn,
            )

            # Depth 3: show callers of the containing method (ISSUE-S+J fix)
            # If no callers found, show the containing method itself as terminal
            if depth < max_depth and method_child_id:
                _caller_fn = caller_chain_for_method_fn or build_caller_chain_for_method
                callers = _caller_fn(
                    index, method_child_id, depth + 1, max_depth
                )
                if callers:
                    entry.children = callers
                else:
                    # Terminal: show the containing method itself as a caller node
                    method_n = index.nodes.get(method_child_id)
                    if method_n:
                        display = method_n.fqn
                        if method_n.kind == "Method" and not display.endswith("()"):
                            display += "()"
                        entry.children = [ContextEntry(
                            depth=depth + 1,
                            node_id=method_child_id,
                            fqn=display,
                            kind=method_n.kind,
                            file=method_n.file,
                            line=method_n.start_line,
                            ref_type="caller",
                            children=[],
                        )]

            entries.append(entry)

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


# =================================================================
# Class USES — grouped, deduped, behavioral depth 2
# =================================================================

def build_class_uses(
    index: "SoTIndex", start_id: str, max_depth: int, limit: int,
    include_impl: bool = False,
    execution_flow_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Build USES tree for a Class node with dedup and semantic grouping.

    Shows one entry per unique external dependency class/interface.
    Classifies each as [extends], [implements], [property_type],
    [parameter_type], [return_type], [instantiation].

    At depth 2:
    - property_type deps: behavioral (method calls on the dep)
    - extends/implements: override and inherited methods
    - non-property deps: recursive class-level expansion
    """
    start_node = index.nodes.get(start_id)
    if not start_node:
        return []

    # Collect all outgoing dependencies from the class and its members
    edges = index.get_deps(start_id, include_members=True)

    # Also collect extends and implements edges directly
    extends_edges = index.outgoing[start_id].get("extends", [])
    implements_edges = index.outgoing[start_id].get("implements", [])

    # Deduplicate by target class/interface — track best ref_type per target
    target_info: dict[str, dict] = {}  # target_id -> {ref_type, file, line, property_name, ...}

    # Process extends first (highest structural priority)
    for edge in extends_edges:
        target_id = edge.target
        if target_id == start_id:
            continue
        target_node = index.nodes.get(target_id)
        if not target_node:
            continue
        target_info[target_id] = {
            "ref_type": "extends",
            "file": start_node.file,
            "line": start_node.start_line,
            "property_name": None,
            "node": target_node,
        }

    # Process implements — file ref points to the class declaration
    for edge in implements_edges:
        target_id = edge.target
        if target_id == start_id or target_id in target_info:
            continue
        target_node = index.nodes.get(target_id)
        if not target_node:
            continue
        target_info[target_id] = {
            "ref_type": "implements",
            "file": start_node.file,
            "line": start_node.start_line,
            "property_name": None,
            "node": target_node,
        }

    # Pre-collect type_hint edges from class members to classify targets accurately
    type_hint_info: dict[str, dict] = {}
    for child_id in index.get_contains_children(start_id):
        child = index.nodes.get(child_id)
        if not child:
            continue

        # Property type_hints -> property_type
        if child.kind == "Property":
            for th_edge in index.outgoing.get(child_id, {}).get("type_hint", []):
                tid = th_edge.target
                prop_name = child.name
                if not prop_name.startswith("$"):
                    prop_name = "$" + prop_name
                type_hint_info[tid] = {
                    "ref_type": "property_type",
                    "property_name": prop_name,
                    "file": child.file,
                    "line": child.start_line,
                }

        # Method return type_hints -> return_type, Argument type_hints -> parameter_type
        if child.kind == "Method":
            for th_edge in index.outgoing.get(child_id, {}).get("type_hint", []):
                tid = th_edge.target
                if tid not in type_hint_info:
                    type_hint_info[tid] = {
                        "ref_type": "return_type",
                        "property_name": None,
                        "file": child.file,
                        "line": child.start_line,
                    }

            # Check sub-children (Arguments)
            for sub_id in index.get_contains_children(child_id):
                sub = index.nodes.get(sub_id)
                if not sub:
                    continue
                if sub.kind == "Argument":
                    for th_edge in index.outgoing.get(sub_id, {}).get("type_hint", []):
                        tid = th_edge.target
                        existing = type_hint_info.get(tid)
                        # parameter_type wins over return_type but not over property_type
                        if not existing or existing["ref_type"] == "return_type":
                            type_hint_info[tid] = {
                                "ref_type": "parameter_type",
                                "property_name": None,
                                "file": child.file,
                                "line": child.start_line,
                            }

    # Pre-collect constructor calls to detect instantiation targets
    instantiation_targets: dict[str, dict] = {}
    for child_id in index.get_contains_children(start_id):
        child = index.nodes.get(child_id)
        if not child or child.kind != "Method":
            continue
        for call_child_id in index.get_contains_children(child_id):
            call_child = index.nodes.get(call_child_id)
            if not call_child or call_child.kind != "Call":
                continue
            target_id = index.get_call_target(call_child_id)
            if not target_id:
                continue
            target_node = index.nodes.get(target_id)
            if not target_node:
                continue
            # Constructor call -> instantiation of the containing class
            if target_node.kind == "Method" and target_node.name == "__construct":
                cls_id = index.get_contains_parent(target_id)
                if cls_id and cls_id != start_id:
                    call_line = call_child.range.get("start_line") if call_child.range else None
                    if cls_id not in instantiation_targets:
                        instantiation_targets[cls_id] = {
                            "file": call_child.file,
                            "line": call_line,
                        }

    # Process uses edges — classify each and pick best ref_type per target
    for edge in edges:
        target_id = edge.target
        if target_id == start_id:
            continue

        target_node = index.nodes.get(target_id)
        if not target_node:
            continue

        # We only care about class/interface level deps
        resolved_target_id = target_id
        resolved_target = target_node
        if target_node.kind in ("Method", "Property", "Argument", "Value", "Call", "Constant"):
            parent_id = index.get_contains_parent(target_id)
            if parent_id:
                parent = index.nodes.get(parent_id)
                if parent and parent.kind in ("Class", "Interface", "Trait", "Enum"):
                    resolved_target_id = parent_id
                    resolved_target = parent
                else:
                    continue
            else:
                continue

        # Skip self-references
        if resolved_target_id == start_id:
            continue

        # Skip already-tracked extends/implements
        if resolved_target_id in target_info and target_info[resolved_target_id]["ref_type"] in ("extends", "implements"):
            continue

        file = edge.location.get("file") if edge.location else None
        line = edge.location.get("line") if edge.location else None

        # Classify this reference using pre-collected info
        ref_type = None
        property_name = None

        # Check type_hint-based classification
        if resolved_target_id in type_hint_info:
            th_info = type_hint_info[resolved_target_id]
            th_ref = th_info["ref_type"]
            if th_ref in ("property_type", "return_type"):
                ref_type = th_ref
                property_name = th_info.get("property_name")
                file = th_info["file"] or file
                line = th_info["line"] if th_info["line"] is not None else line

        # Check for instantiation
        if ref_type is None and resolved_target_id in instantiation_targets:
            inst_info = instantiation_targets[resolved_target_id]
            ref_type = "instantiation"
            file = inst_info["file"] or file
            line = inst_info["line"] if inst_info["line"] is not None else line

        # Fall back to remaining type_hint classification (parameter_type)
        if ref_type is None and resolved_target_id in type_hint_info:
            th_info = type_hint_info[resolved_target_id]
            ref_type = th_info["ref_type"]
            property_name = th_info.get("property_name")
            file = th_info["file"] or file
            line = th_info["line"] if th_info["line"] is not None else line

        # Fall back to edge-level inference
        if ref_type is None:
            ref_type = _infer_reference_type(edge, target_node, index)
            if ref_type == "property_type":
                source_node = index.nodes.get(edge.source)
                if source_node and source_node.kind == "Property":
                    property_name = source_node.name
                    if not property_name.startswith("$"):
                        property_name = "$" + property_name
                    file = source_node.file
                    line = source_node.start_line

        # Priority for dedup
        priority_map = {
            "instantiation": 0,
            "property_type": 1,
            "method_call": 2,
            "property_access": 2,
            "parameter_type": 3,
            "return_type": 4,
            "type_hint": 5,
        }

        if resolved_target_id in target_info:
            existing = target_info[resolved_target_id]
            existing_priority = priority_map.get(existing["ref_type"], 10)
            new_priority = priority_map.get(ref_type, 10)
            if new_priority < existing_priority:
                target_info[resolved_target_id] = {
                    "ref_type": ref_type,
                    "file": file or existing["file"],
                    "line": line if line is not None else existing["line"],
                    "property_name": property_name or existing.get("property_name"),
                    "node": resolved_target,
                }
            elif property_name and not existing.get("property_name"):
                existing["property_name"] = property_name
        else:
            target_info[resolved_target_id] = {
                "ref_type": ref_type,
                "file": file or resolved_target.file,
                "line": line if line is not None else resolved_target.start_line,
                "property_name": property_name,
                "node": resolved_target,
            }

    # Ensure type_hint targets that were not reached via "uses" edges are still included.
    for tid, th_info in type_hint_info.items():
        if tid in target_info or tid == start_id:
            continue
        target_node = index.nodes.get(tid)
        if not target_node or target_node.kind not in ("Class", "Interface", "Trait", "Enum"):
            continue
        target_info[tid] = {
            "ref_type": th_info["ref_type"],
            "file": th_info["file"] or target_node.file,
            "line": th_info["line"] if th_info["line"] is not None else target_node.start_line,
            "property_name": th_info.get("property_name"),
            "node": target_node,
        }

    # Build entries
    entries: list[ContextEntry] = []
    for target_id, info in target_info.items():
        target_node = info["node"]
        ref_type = info["ref_type"]
        file = info["file"]
        line = info["line"]
        property_name = info.get("property_name")

        entry = ContextEntry(
            depth=1,
            node_id=target_id,
            fqn=target_node.fqn,
            kind=target_node.kind,
            file=file,
            line=line,
            ref_type=ref_type,
            property_name=property_name,
            children=[],
        )

        # Depth 2 expansion based on ref_type
        if max_depth >= 2:
            if ref_type == "extends":
                entry.children = build_extends_depth2(
                    index, start_id, target_id, 2, max_depth
                )
            elif ref_type == "implements":
                entry.children = build_implements_depth2(
                    index, start_id, target_id, 2, max_depth
                )
            elif ref_type == "property_type":
                # Behavioral: show method calls on this dep through the property
                entry.children = build_behavioral_depth2(
                    index, start_id, target_id, property_name, 2, max_depth,
                    execution_flow_fn=execution_flow_fn,
                )
            else:
                # Non-property deps: recursive class-level expansion
                entry.children = build_class_uses_recursive(
                    index, target_id, 2, max_depth, limit, {start_id}
                )

        entries.append(entry)

    # Sort by USES-specific priority
    uses_priority = {
        "extends": 0,
        "implements": 0,
        "property_type": 1,
        "parameter_type": 2,
        "return_type": 2,
        "instantiation": 3,
        "type_hint": 4,
        "method_call": 5,
        "property_access": 5,
    }

    def sort_key(e):
        pri = uses_priority.get(e.ref_type, 10)
        return (pri, e.file or "", e.line if e.line is not None else 0)

    entries.sort(key=sort_key)
    return entries[:limit]


def build_extends_depth2(
    index: "SoTIndex", class_id: str, parent_id: str, depth: int, max_depth: int
) -> list[ContextEntry]:
    """Build depth-2 for [extends]: show override and inherited methods."""
    if depth > max_depth:
        return []

    parent_node = index.nodes.get(parent_id)
    if not parent_node:
        return []

    # Check if parent exists in graph (external parents have no methods)
    parent_children = index.get_contains_children(parent_id)
    if not parent_children:
        return []

    override_entries: list[ContextEntry] = []
    inherited_entries: list[ContextEntry] = []

    # Collect parent's methods
    parent_methods = {}
    for child_id in parent_children:
        child = index.nodes.get(child_id)
        if child and child.kind == "Method" and child.name != "__construct":
            parent_methods[child.name] = (child_id, child)

    # Check which ones the class overrides
    for child_id in index.get_contains_children(class_id):
        child = index.nodes.get(child_id)
        if not child or child.kind != "Method" or child.name == "__construct":
            continue

        if child.name in parent_methods:
            # Check if it actually overrides (has overrides edge)
            override_parent = index.get_overrides_parent(child_id)
            if override_parent:
                entry = ContextEntry(
                    depth=depth,
                    node_id=child_id,
                    fqn=child.fqn,
                    kind="Method",
                    file=child.file,
                    line=child.start_line,
                    signature=child.signature,
                    ref_type="override",
                    children=[],
                )
                if depth < max_depth:
                    entry.children = build_override_method_internals(
                        index, child_id, depth + 1, max_depth
                    )
                override_entries.append(entry)

    # Inherited methods: parent methods not overridden by the class
    overridden_names = {index.nodes.get(e.node_id).name for e in override_entries if index.nodes.get(e.node_id)}
    for method_name, (method_id, method_node) in parent_methods.items():
        if method_name not in overridden_names:
            entry = ContextEntry(
                depth=depth,
                node_id=method_id,
                fqn=method_node.fqn,
                kind="Method",
                file=method_node.file,
                line=method_node.start_line,
                signature=method_node.signature,
                ref_type="inherited",
                children=[],
            )
            # Expand inherited method internals at depth 3 (same as override)
            if depth < max_depth:
                entry.children = build_override_method_internals(
                    index, method_id, depth + 1, max_depth
                )
            inherited_entries.append(entry)

    # Overrides first, then inherited
    override_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    inherited_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return override_entries + inherited_entries


def build_implements_depth2(
    index: "SoTIndex", class_id: str, interface_id: str, depth: int, max_depth: int
) -> list[ContextEntry]:
    """Build depth-2 for [implements]: show override methods and extends subclasses.

    Uses get_overrides_parent() directly to detect overrides (ISSUE-E fix).
    Also adds [extends] entries for concrete subclasses (ISSUE-D fix).
    """
    if depth > max_depth:
        return []

    override_entries = []
    extends_entries = []

    # Find override methods in the implementing class using direct overrides edge check.
    for child_id in index.get_contains_children(class_id):
        child = index.nodes.get(child_id)
        if not child or child.kind != "Method" or child.name == "__construct":
            continue

        override_parent_id = index.get_overrides_parent(child_id)
        if override_parent_id:
            entry = ContextEntry(
                depth=depth,
                node_id=child_id,
                fqn=child.fqn,
                kind="Method",
                file=child.file,
                line=child.start_line,
                signature=child.signature,
                ref_type="override",
                children=[],
            )
            # At depth 3, show what the override does internally
            if depth < max_depth:
                entry.children = build_override_method_internals(
                    index, child_id, depth + 1, max_depth
                )
            override_entries.append(entry)

    # ISSUE-D: Add [extends] entries for concrete subclasses
    collect_extends_entries(index, class_id, depth, max_depth, extends_entries, set())

    override_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    extends_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return override_entries + extends_entries


def offset_entry_depths(entries: list, offset: int) -> list:
    """Recursively add an offset to depth values in context entries."""
    for entry in entries:
        entry.depth += offset
        if entry.children:
            offset_entry_depths(entry.children, offset)
    return entries


def collect_extends_entries(
    index: "SoTIndex", class_id: str, depth: int, max_depth: int,
    result: list, visited: set[str]
) -> None:
    """Recursively collect [extends] entries for subclasses of a class."""
    if class_id in visited:
        return
    visited.add(class_id)

    for child_id in index.get_extends_children(class_id):
        child_node = index.nodes.get(child_id)
        if not child_node or child_id in visited:
            continue

        entry = ContextEntry(
            depth=depth,
            node_id=child_id,
            fqn=child_node.fqn,
            kind=child_node.kind,
            file=child_node.file,
            line=child_node.start_line,
            ref_type="extends",
            children=[],
        )

        # At depth 3: show USED BY of this subclass (instantiation sites, etc.)
        if depth < max_depth:
            raw_children = build_class_used_by(
                index, child_id, max_depth - depth, limit=10, include_impl=False
            )
            # _build_class_used_by starts depth from 1 internally;
            # offset to match our actual depth context
            depth_offset = depth  # e.g., depth=2 -> children should be depth 3
            entry.children = offset_entry_depths(raw_children, depth_offset)

        result.append(entry)

        # Recursively find further subclasses
        collect_extends_entries(index, child_id, depth, max_depth, result, visited)


def build_behavioral_depth2(
    index: "SoTIndex", class_id: str, dep_class_id: str, property_name: str | None,
    depth: int, max_depth: int,
    execution_flow_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Build behavioral depth-2 for property_type deps: method calls on the dep.

    Finds all method calls through the property that holds this dependency.
    """
    if depth > max_depth:
        return []

    entries = []
    seen_callees: set[str] = set()

    # Find the property in the class that references the dep class
    prop_id = None
    for child_id in index.get_contains_children(class_id):
        child = index.nodes.get(child_id)
        if child and child.kind == "Property":
            # Check if this property has type_hint to dep_class_id
            for th_edge in index.outgoing[child_id].get("type_hint", []):
                if th_edge.target == dep_class_id:
                    prop_id = child_id
                    break
            if prop_id:
                break

    if not prop_id:
        return []

    prop_node = index.nodes.get(prop_id)
    if not prop_node:
        return []

    # Find all method calls through this property in the class
    for method_child_id in index.get_contains_children(class_id):
        method_node = index.nodes.get(method_child_id)
        if not method_node or method_node.kind != "Method":
            continue

        for call_child_id in index.get_contains_children(method_child_id):
            call_child = index.nodes.get(call_child_id)
            if not call_child or call_child.kind != "Call":
                continue

            # Check if this call's receiver is through our property
            chain_symbol = resolve_access_chain_symbol(index, call_child_id)
            if chain_symbol != prop_node.fqn:
                continue

            target_id = index.get_call_target(call_child_id)
            if not target_id:
                continue
            target_node = index.nodes.get(target_id)
            if not target_node:
                continue

            callee_name = target_node.name + "()" if target_node.kind == "Method" else target_node.name
            if target_node.fqn in seen_callees:
                continue
            seen_callees.add(target_node.fqn)

            ref_type = get_reference_type_from_call(index, call_child_id)
            ac, acs, ok, of, ol = resolve_receiver_identity(index, call_child_id)
            arguments = get_argument_info(index, call_child_id)
            call_line = call_child.range.get("start_line") if call_child.range else None

            entry = ContextEntry(
                depth=depth,
                node_id=target_id,
                fqn=target_node.fqn,
                kind=target_node.kind,
                file=call_child.file,
                line=call_line,
                ref_type="method_call",
                callee=callee_name,
                on=ac,
                on_kind="property",
                arguments=arguments,
                children=[],
            )

            # Depth 3: expand into callee's execution flow
            if depth < max_depth and target_id and execution_flow_fn:
                target_in_graph = index.nodes.get(target_id)
                if target_in_graph and target_in_graph.kind == "Method":
                    entry.children = execution_flow_fn(
                        target_id, depth + 1, max_depth, 100, {target_id}, [0],
                    )

            entries.append(entry)

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


def build_class_uses_recursive(
    index: "SoTIndex", target_id: str, depth: int, max_depth: int,
    limit: int, visited: set[str]
) -> list[ContextEntry]:
    """Recursive class-level expansion for non-property USES deps.

    For parameter_type, return_type, instantiation deps at depth 2+,
    show their own class-level dependencies.
    """
    if depth > max_depth or target_id in visited:
        return []
    visited.add(target_id)

    target_node = index.nodes.get(target_id)
    if not target_node or target_node.kind not in ("Class", "Interface", "Trait", "Enum"):
        return []

    entries = []
    # Collect extends edges FIRST (highest priority), then uses edges.
    edges: list[EdgeData] = []
    for ext_edge in index.outgoing[target_id].get("extends", []):
        edges.append(ext_edge)
    edges.extend(index.get_deps(target_id, include_members=True))
    local_visited: set[str] = set()

    # Get the source node for file references on extends entries
    source_node_for_extends = index.nodes.get(target_id)

    for edge in edges:
        dep_id = edge.target
        dep_node = index.nodes.get(dep_id)
        if not dep_node:
            continue

        # Resolve to containing class
        resolved_id = dep_id
        resolved_node = dep_node
        if dep_node.kind in ("Method", "Property", "Argument", "Value", "Call"):
            parent_id = index.get_contains_parent(dep_id)
            if parent_id:
                parent = index.nodes.get(parent_id)
                if parent and parent.kind in ("Class", "Interface", "Trait", "Enum"):
                    resolved_id = parent_id
                    resolved_node = parent
                else:
                    continue
            else:
                continue

        if resolved_id == target_id or resolved_id in local_visited or resolved_id in visited:
            continue
        local_visited.add(resolved_id)

        ref_type = _infer_reference_type(edge, dep_node, index)

        # Resolve property name for property_type
        property_name = None
        if ref_type == "property_type":
            source_node = index.nodes.get(edge.source)
            if source_node and source_node.kind == "Property":
                property_name = source_node.name
                if not property_name.startswith("$"):
                    property_name = "$" + property_name

        # File reference: for extends edges, point to the source declaration
        if edge.type == "extends" and source_node_for_extends:
            file = edge.location.get("file") if edge.location else source_node_for_extends.file
            line = edge.location.get("line") if edge.location else source_node_for_extends.start_line
        else:
            file = edge.location.get("file") if edge.location else resolved_node.file
            line = edge.location.get("line") if edge.location else resolved_node.start_line

        entry = ContextEntry(
            depth=depth,
            node_id=resolved_id,
            fqn=resolved_node.fqn,
            kind=resolved_node.kind,
            file=file,
            line=line,
            ref_type=ref_type,
            property_name=property_name,
            children=[],
        )

        # Recursive expansion for class-level deps at depth 2+
        if depth < max_depth and resolved_node.kind in ("Class", "Interface", "Trait", "Enum"):
            entry.children = build_class_uses_recursive(
                index, resolved_id, depth + 1, max_depth, limit, visited | local_visited
            )

        entries.append(entry)

    # ISSUE-K: Add non-scalar property type entries from the target class.
    for child_id in index.get_contains_children(target_id):
        child = index.nodes.get(child_id)
        if not child or child.kind != "Property":
            continue
        for th_edge in index.outgoing.get(child_id, {}).get("type_hint", []):
            type_target_id = th_edge.target
            type_target = index.nodes.get(type_target_id)
            if not type_target or type_target.kind not in ("Class", "Interface", "Trait", "Enum"):
                continue
            # Skip if already included via uses/extends edges
            if type_target_id in local_visited or type_target_id in visited or type_target_id == target_id:
                continue
            local_visited.add(type_target_id)

            prop_name = child.name
            if not prop_name.startswith("$"):
                prop_name = "$" + prop_name

            prop_entry = ContextEntry(
                depth=depth,
                node_id=type_target_id,
                fqn=type_target.fqn,
                kind=type_target.kind,
                file=child.file,
                line=child.start_line,
                ref_type="property_type",
                property_name=prop_name,
                children=[],
            )

            # One level of recursive expansion
            if depth < max_depth:
                prop_entry.children = build_class_uses_recursive(
                    index, type_target_id, depth + 1, max_depth, limit, visited | local_visited
                )

            entries.append(prop_entry)

    # Sort by priority
    def sort_key(e):
        pri = REF_TYPE_PRIORITY.get(e.ref_type, 10)
        return (pri, e.file or "", e.line if e.line is not None else 0)

    entries.sort(key=sort_key)
    return entries
