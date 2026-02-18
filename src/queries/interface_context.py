"""Interface-specific context handlers for USED BY and USES sections.

Handles interface USED BY (implementors, extends hierarchy, injection points
with contract relevance filtering) and interface USES (signature types,
parent interfaces, implementing classes).

All functions are standalone with an explicit `index` parameter.

CROSS-MODULE NOTE:
This module imports from class_context.py:
- build_caller_chain_for_method (shared caller chain traversal)
- build_class_used_by_depth_callers (depth expansion)
- build_class_uses_recursive (recursive USES expansion)
- build_implements_depth2 (override methods for implementors)
- offset_entry_depths (depth adjustment utility)
"""

from typing import Optional, Callable, TYPE_CHECKING

from ..models import ContextEntry, NodeData
from .graph_utils import (
    resolve_receiver_identity,
    resolve_containing_method,
    get_argument_info,
    resolve_access_chain_symbol,
)
from .reference_types import (
    get_reference_type_from_call,
    find_call_for_usage,
    _infer_reference_type,
)
from .class_context import (
    build_caller_chain_for_method,
    build_implements_depth2,
)

if TYPE_CHECKING:
    from ..graph import SoTIndex


def build_interface_used_by(
    index: "SoTIndex", start_id: str, max_depth: int, limit: int,
    include_impl: bool = False,
    interface_injection_point_calls_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Build USED BY tree for an Interface node.

    Structure:
    - Depth 1: implementors [implements] + child interfaces [extends] +
                injection points [property_type]
    - Depth 2: override methods under [implements], method calls under [property_type]
    - Depth 3: callers of the method call sites

    Sorting: [implements] first, then [extends], then [property_type],
    then other ref types.
    """
    start_node = index.nodes.get(start_id)
    if not start_node:
        return []

    implements_entries: list[ContextEntry] = []
    extends_entries: list[ContextEntry] = []
    property_type_entries: list[ContextEntry] = []
    visited_sources: set[str] = {start_id}
    seen_property_type_props: set[str] = set()

    # --- Collect all interfaces in the extends hierarchy (for transitive lookup) ---
    all_interface_ids = [start_id]
    queue = [start_id]
    while queue:
        current = queue.pop(0)
        for child_id in index.get_extends_children(current):
            if child_id not in all_interface_ids:
                all_interface_ids.append(child_id)
                queue.append(child_id)

    # --- Collect implementors (DIRECT only — ISSUE-C fix) ---
    direct_implementor_ids = index.get_implementors(start_id)
    for impl_id in direct_implementor_ids:
        impl_node = index.nodes.get(impl_id)
        if not impl_node or impl_id in visited_sources:
            continue
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

        # Depth 2: override methods + extends entries (ISSUE-D)
        if max_depth >= 2:
            entry.children = build_implements_depth2(
                index, impl_id, start_id, 2, max_depth
            )

        implements_entries.append(entry)

    # --- Collect child interfaces (incoming extends edges — direct only) ---
    extends_child_ids = index.get_extends_children(start_id)
    for child_id in extends_child_ids:
        child_node = index.nodes.get(child_id)
        if not child_node or child_id in visited_sources:
            continue
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

        # ISSUE-I: Depth 2 — own methods + deeper extends for child interfaces
        if max_depth >= 2:
            entry.children = build_interface_extends_depth2(
                index, child_id, 2, max_depth
            )

        extends_entries.append(entry)

    # --- Pass 1: Identify classes with property_type injection (for suppression) ---
    classes_with_injection: set[str] = set()
    all_source_groups: list[tuple[str, dict[str, list]]] = []
    for iface_id in all_interface_ids:
        sg = index.get_usages_grouped(iface_id)
        all_source_groups.append((iface_id, sg))
        for source_id, edges in sg.items():
            source_node = index.nodes.get(source_id)
            if not source_node:
                continue
            for edge in edges:
                target_node = index.nodes.get(edge.target)
                if not target_node:
                    continue
                ref_type = _infer_reference_type(edge, target_node, index)
                if ref_type == "property_type":
                    cls_id = source_id
                    node = source_node
                    while node and node.kind not in ("Class", "Interface", "Trait", "Enum", "File"):
                        cls_id = index.get_contains_parent(cls_id)
                        node = index.nodes.get(cls_id) if cls_id else None
                    if node and node.kind in ("Class", "Interface", "Trait", "Enum"):
                        classes_with_injection.add(cls_id)

    # Also check usages of implementors for property_type injection (ISSUE-D)
    for impl_id in direct_implementor_ids:
        for source_id, edges in index.get_usages_grouped(impl_id).items():
            source_node = index.nodes.get(source_id)
            if not source_node:
                continue
            for edge in edges:
                target_node = index.nodes.get(edge.target)
                if not target_node:
                    continue
                ref_type = _infer_reference_type(edge, target_node, index)
                if ref_type == "property_type":
                    cls_id = source_id
                    node = source_node
                    while node and node.kind not in ("Class", "Interface", "Trait", "Enum", "File"):
                        cls_id = index.get_contains_parent(cls_id)
                        node = index.nodes.get(cls_id) if cls_id else None
                    if node and node.kind in ("Class", "Interface", "Trait", "Enum"):
                        classes_with_injection.add(cls_id)

    # --- ISSUE-B: Collect the target interface's own contract method names ---
    contract_method_names: set[str] = set()
    for child_id in index.get_contains_children(start_id):
        child = index.nodes.get(child_id)
        if child and child.kind == "Method":
            contract_method_names.add(child.name)

    # Use provided injection point calls fn or default
    _injection_fn = interface_injection_point_calls_fn or build_interface_injection_point_calls

    # --- Pass 2: Process uses edges for injection points (from all interfaces) ---
    for iface_id, source_groups in all_source_groups:
        iface_node = index.nodes.get(iface_id)
        for source_id, edges in source_groups.items():
            if source_id in visited_sources:
                continue

            source_node = index.nodes.get(source_id)
            if not source_node or source_node.kind == "File":
                continue

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

                if ref_type == "property_type":
                    # ISSUE-C: Skip indirect property_type entries (from child interfaces)
                    if iface_id != start_id:
                        continue

                    # Resolve property node
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

                    if prop_fqn and prop_node and prop_fqn not in seen_property_type_props:
                        # ISSUE-B: Check contract relevance before including consumer.
                        if contract_method_names:
                            calls_contract = consumer_calls_contract_methods(
                                index, prop_node.id, contract_method_names
                            )
                            if not calls_contract:
                                continue  # Skip irrelevant consumer

                        seen_property_type_props.add(prop_fqn)
                        visited_sources.add(source_id)

                        # Add via marker when the injection is through a child interface
                        via_fqn = iface_node.fqn if iface_id != start_id else None

                        entry = ContextEntry(
                            depth=1,
                            node_id=prop_node.id,
                            fqn=prop_fqn,
                            kind="Property",
                            file=prop_node.file,
                            line=prop_node.start_line,
                            ref_type="property_type",
                            via=via_fqn,
                            children=[],
                        )

                        # Depth 2: method calls through this property (with caller depth 3)
                        if max_depth >= 2:
                            entry.children = _injection_fn(
                                index, prop_node.id, iface_id, 2, max_depth
                            )

                            # ISSUE-B: Filter depth-2 children to only contract methods
                            if contract_method_names:
                                entry.children = [
                                    c for c in entry.children
                                    if entry_targets_contract_method(c, contract_method_names)
                                ]

                        property_type_entries.append(entry)

                elif ref_type == "method_call":
                    # Suppress method_call if containing class has property_type injection
                    containing_method_id = resolve_containing_method(index, source_id)
                    containing_class_id = None
                    if containing_method_id:
                        containing_class_id = index.get_contains_parent(containing_method_id)
                    if containing_class_id and containing_class_id in classes_with_injection:
                        continue
                    # Non-injected method calls remain suppressed for interfaces
                    continue

                elif ref_type in ("type_hint", "parameter_type", "return_type"):
                    # Skip type_hint/parameter_type/return_type — subsumed by property_type
                    continue

    # --- Pass 2b: Transitive property_type discovery for parent interfaces (ISSUE-B) ---
    if not property_type_entries and contract_method_names:
        for iface_id, source_groups in all_source_groups:
            if iface_id == start_id:
                continue  # Already processed in Pass 2
            iface_node = index.nodes.get(iface_id)
            for source_id, edges in source_groups.items():
                if source_id in visited_sources:
                    continue
                source_node = index.nodes.get(source_id)
                if not source_node or source_node.kind == "File":
                    continue
                for edge in edges:
                    target_node = index.nodes.get(edge.target)
                    if not target_node:
                        continue
                    file = edge.location.get("file") if edge.location else source_node.file
                    line = edge.location.get("line") if edge.location else source_node.start_line
                    call_node_id = find_call_for_usage(index, source_id, edge.target, file, line)
                    if call_node_id:
                        rt = get_reference_type_from_call(index, call_node_id)
                    else:
                        rt = _infer_reference_type(edge, target_node, index)
                    if rt != "property_type":
                        continue

                    # Resolve property node
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

                    if not prop_fqn or not prop_node or prop_fqn in seen_property_type_props:
                        continue

                    # Contract relevance check
                    calls_contract = consumer_calls_contract_methods(
                        index, prop_node.id, contract_method_names
                    )
                    if not calls_contract:
                        continue

                    seen_property_type_props.add(prop_fqn)
                    visited_sources.add(source_id)

                    via_fqn = iface_node.fqn if iface_node else None
                    entry = ContextEntry(
                        depth=1,
                        node_id=prop_node.id,
                        fqn=prop_fqn,
                        kind="Property",
                        file=prop_node.file,
                        line=prop_node.start_line,
                        ref_type="property_type",
                        via=via_fqn,
                        children=[],
                    )

                    # Depth 2: method calls through this property
                    if max_depth >= 2:
                        entry.children = _injection_fn(
                            index, prop_node.id, iface_id, 2, max_depth
                        )
                        # Filter depth-2 children to only contract methods
                        entry.children = [
                            c for c in entry.children
                            if entry_targets_contract_method(c, contract_method_names)
                        ]

                    property_type_entries.append(entry)

    # --- Pass 3: Discover property_type consumers typed to implementing classes (ISSUE-D) ---
    for impl_id in direct_implementor_ids:
        impl_usages = index.get_usages_grouped(impl_id)
        for source_id, edges in impl_usages.items():
            if source_id in visited_sources:
                continue
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

                # Resolve the actual property node
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
                                    if th_edge.target == impl_id:
                                        prop_fqn = child.fqn
                                        prop_node = child
                                        break
                                if prop_fqn:
                                    break

                if not prop_fqn or not prop_node or prop_fqn in seen_property_type_props:
                    continue

                # Ensure property is not in the implementor class itself
                prop_class_id = index.get_contains_parent(prop_node.id)
                if prop_class_id == impl_id:
                    continue

                # ISSUE-B: Check contract relevance
                if contract_method_names:
                    calls_contract = consumer_calls_contract_methods(
                        index, prop_node.id, contract_method_names
                    )
                    if not calls_contract:
                        continue

                seen_property_type_props.add(prop_fqn)
                visited_sources.add(source_id)

                impl_node = index.nodes.get(impl_id)
                via_fqn = impl_node.fqn if impl_node else None

                entry = ContextEntry(
                    depth=1,
                    node_id=prop_node.id,
                    fqn=prop_fqn,
                    kind="Property",
                    file=prop_node.file,
                    line=prop_node.start_line,
                    ref_type="property_type",
                    via=via_fqn,
                    children=[],
                )

                # Depth 2: method calls through this property
                if max_depth >= 2:
                    entry.children = _injection_fn(
                        index, prop_node.id, start_id, 2, max_depth
                    )

                    # ISSUE-B: Filter depth-2 children to only contract methods
                    if contract_method_names:
                        entry.children = [
                            c for c in entry.children
                            if entry_targets_contract_method(c, contract_method_names)
                        ]

                property_type_entries.append(entry)

    # Sort within groups
    implements_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    extends_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    property_type_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

    # Combine: implementors first, then extends children, then property_type
    all_entries = implements_entries + extends_entries + property_type_entries
    return all_entries[:limit]


def build_interface_injection_point_calls(
    index: "SoTIndex", property_id: str, interface_id: str, depth: int, max_depth: int
) -> list[ContextEntry]:
    """Build method call entries for an interface injection point.

    Like build_injection_point_calls but at depth 3 shows callers of the
    containing method (who triggers the call chain) instead of the callee's
    execution flow (which is empty for interface method definitions).
    """
    if depth > max_depth:
        return []

    prop_node = index.nodes.get(property_id)
    if not prop_node or prop_node.kind != "Property":
        return []

    containing_class_id = index.get_contains_parent(property_id)
    if not containing_class_id:
        return []

    entries = []
    seen_callees: set[str] = set()

    for method_child_id in index.get_contains_children(containing_class_id):
        method_node = index.nodes.get(method_child_id)
        if not method_node or method_node.kind != "Method":
            continue

        for call_child_id in index.get_contains_children(method_child_id):
            call_child = index.nodes.get(call_child_id)
            if not call_child or call_child.kind != "Call":
                continue

            recv_id = index.get_receiver(call_child_id)
            if not recv_id:
                continue

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

            callee_key = target_node.fqn
            if callee_key in seen_callees:
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
            containing_cls_node_iface = index.nodes.get(containing_class_id)
            crossed_from_fqn_iface = containing_cls_node_iface.fqn if containing_cls_node_iface else None

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
                crossed_from=crossed_from_fqn_iface,
            )

            # Depth 3: show callers of the containing method (ISSUE-S+J fix)
            if depth < max_depth and method_child_id:
                callers = build_caller_chain_for_method(
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


def consumer_calls_contract_methods(
    index: "SoTIndex", property_id: str, contract_method_names: set[str]
) -> bool:
    """Check if a property's containing class calls any contract method through it.

    Traverses the containing class's methods to find Call nodes whose
    receiver chain resolves to this property, and checks if the called
    method name matches any contract method name.
    """
    prop_node = index.nodes.get(property_id)
    if not prop_node or prop_node.kind != "Property":
        return False

    containing_class_id = index.get_contains_parent(property_id)
    if not containing_class_id:
        return False

    for method_child_id in index.get_contains_children(containing_class_id):
        method_node = index.nodes.get(method_child_id)
        if not method_node or method_node.kind != "Method":
            continue

        for call_child_id in index.get_contains_children(method_child_id):
            call_child = index.nodes.get(call_child_id)
            if not call_child or call_child.kind != "Call":
                continue

            # Check if this call's receiver is our property
            chain_symbol = resolve_access_chain_symbol(index, call_child_id)
            if chain_symbol != prop_node.fqn:
                continue

            # Check if the called method name matches a contract method
            target_id = index.get_call_target(call_child_id)
            if not target_id:
                continue
            target_node = index.nodes.get(target_id)
            if target_node and target_node.name in contract_method_names:
                return True

    return False


def entry_targets_contract_method(
    entry: "ContextEntry", contract_method_names: set[str]
) -> bool:
    """Check if a depth-2 method_call entry targets a contract method."""
    if entry.ref_type != "method_call":
        return True  # Non-method_call entries pass through
    # Extract method name from FQN
    fqn = entry.fqn
    if "::" in fqn:
        method_name = fqn.rsplit("::", 1)[-1]
    else:
        method_name = fqn
    method_name = method_name.rstrip("()")
    return method_name in contract_method_names


def build_interface_extends_depth2(
    index: "SoTIndex", interface_id: str, depth: int, max_depth: int
) -> list[ContextEntry]:
    """Build depth-2 children for an [extends] interface entry.

    Shows:
    1. Own methods declared by this interface (not inherited) as [own_method]
    2. Deeper extends relationships (interfaces extending this one)
    """
    if depth > max_depth:
        return []

    entries: list[ContextEntry] = []

    # 1. Own methods (methods declared directly on this interface)
    for child_id in index.get_contains_children(interface_id):
        child = index.nodes.get(child_id)
        if not child or child.kind != "Method":
            continue

        entry = ContextEntry(
            depth=depth,
            node_id=child_id,
            fqn=child.fqn,
            kind="Method",
            file=child.file,
            line=child.start_line,
            signature=child.signature,
            ref_type="own_method",
            children=[],
        )
        entries.append(entry)

    # 2. Deeper extends (interfaces that extend this one)
    extends_child_ids = index.get_extends_children(interface_id)
    for child_id in extends_child_ids:
        child_node = index.nodes.get(child_id)
        if not child_node:
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

        # Recursive expansion for deeper chains
        if depth < max_depth:
            entry.children = build_interface_extends_depth2(
                index, child_id, depth + 1, max_depth
            )

        entries.append(entry)

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


def build_interface_uses(
    index: "SoTIndex", start_id: str, max_depth: int, limit: int,
    include_impl: bool = False,
    class_uses_recursive_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Build USES tree for an Interface node.

    Interfaces have no method bodies, so USES only shows:
    - Parent interface (extends) at depth 1 with its type deps at depth 2
    - Non-primitive type references in method signatures (parameter_type, return_type)
    - With --impl: implementing classes with their class-level deps

    Sorting: extends first, then parameter_type/return_type, then implements (--impl).
    """
    from .class_context import build_class_uses_recursive

    _recursive_fn = class_uses_recursive_fn or build_class_uses_recursive

    start_node = index.nodes.get(start_id)
    if not start_node:
        return []

    target_info: dict[str, dict] = {}  # target_id -> {ref_type, file, line, node}

    # --- Collect extends (parent interface) ---
    extends_edges = index.outgoing[start_id].get("extends", [])
    for edge in extends_edges:
        target_id = edge.target
        if target_id == start_id:
            continue
        target_node = index.nodes.get(target_id)
        if not target_node:
            continue
        ext_file = edge.location.get("file") if edge.location else start_node.file
        ext_line = edge.location.get("line") if edge.location else start_node.start_line
        target_info[target_id] = {
            "ref_type": "extends",
            "file": ext_file,
            "line": ext_line,
            "node": target_node,
        }

    # --- Collect type references from method signatures ---
    for child_id in index.get_contains_children(start_id):
        child = index.nodes.get(child_id)
        if not child or child.kind != "Method":
            continue

        # Return type
        for th_edge in index.outgoing.get(child_id, {}).get("type_hint", []):
            tid = th_edge.target
            if tid == start_id or tid in target_info:
                continue
            t_node = index.nodes.get(tid)
            if not t_node:
                continue
            target_info[tid] = {
                "ref_type": "return_type",
                "file": child.file,
                "line": child.start_line,
                "node": t_node,
            }

        # Parameter types (from Argument children)
        for sub_id in index.get_contains_children(child_id):
            sub = index.nodes.get(sub_id)
            if not sub or sub.kind != "Argument":
                continue
            for th_edge in index.outgoing.get(sub_id, {}).get("type_hint", []):
                tid = th_edge.target
                if tid == start_id:
                    continue
                t_node = index.nodes.get(tid)
                if not t_node:
                    continue
                # parameter_type wins over return_type
                existing = target_info.get(tid)
                if existing and existing["ref_type"] not in ("return_type",):
                    continue  # Don't overwrite extends
                target_info[tid] = {
                    "ref_type": "parameter_type",
                    "file": child.file,
                    "line": child.start_line,
                    "node": t_node,
                }

    # --- ISSUE-G: Collect inherited method signature types ---
    parent_queue = list(extends_edges)
    visited_parents: set[str] = {start_id}
    for edge in parent_queue:
        parent_id = edge.target
        if parent_id in visited_parents:
            continue
        visited_parents.add(parent_id)

        for child_id in index.get_contains_children(parent_id):
            child = index.nodes.get(child_id)
            if not child or child.kind != "Method":
                continue

            # Return type from inherited method
            for th_edge in index.outgoing.get(child_id, {}).get("type_hint", []):
                tid = th_edge.target
                if tid == start_id or tid in target_info:
                    continue
                t_node = index.nodes.get(tid)
                if not t_node:
                    continue
                target_info[tid] = {
                    "ref_type": "return_type",
                    "file": start_node.file,
                    "line": start_node.start_line,
                    "node": t_node,
                }

            # Parameter types from inherited method arguments
            for sub_id in index.get_contains_children(child_id):
                sub = index.nodes.get(sub_id)
                if not sub or sub.kind != "Argument":
                    continue
                for th_edge in index.outgoing.get(sub_id, {}).get("type_hint", []):
                    tid = th_edge.target
                    if tid == start_id:
                        continue
                    t_node = index.nodes.get(tid)
                    if not t_node:
                        continue
                    existing = target_info.get(tid)
                    if existing and existing["ref_type"] not in ("return_type",):
                        continue
                    target_info[tid] = {
                        "ref_type": "parameter_type",
                        "file": start_node.file,
                        "line": start_node.start_line,
                        "node": t_node,
                    }

        # Continue up the chain: add grandparent extends edges
        for gp_edge in index.outgoing[parent_id].get("extends", []):
            if gp_edge.target not in visited_parents:
                parent_queue.append(gp_edge)

    # --- Collect implementing classes (if --impl) ---
    if include_impl:
        implementor_ids = index.get_implementors(start_id)
        for impl_id in implementor_ids:
            impl_node = index.nodes.get(impl_id)
            if not impl_node or impl_id == start_id or impl_id in target_info:
                continue
            target_info[impl_id] = {
                "ref_type": "implements",
                "file": impl_node.file,
                "line": impl_node.start_line,
                "node": impl_node,
            }

    # Build entries
    entries: list[ContextEntry] = []
    for target_id, info in target_info.items():
        target_node = info["node"]
        ref_type = info["ref_type"]
        file = info["file"]
        line = info["line"]

        entry = ContextEntry(
            depth=1,
            node_id=target_id,
            fqn=target_node.fqn,
            kind=target_node.kind,
            file=file,
            line=line,
            ref_type=ref_type,
            children=[],
        )

        # Depth 2 expansion
        if max_depth >= 2:
            if ref_type == "extends":
                entry.children = _recursive_fn(
                    index, target_id, 2, max_depth, limit, {start_id}
                )
            elif ref_type == "implements" and include_impl:
                entry.children = _recursive_fn(
                    index, target_id, 2, max_depth, limit, {start_id}
                )
            elif ref_type in ("parameter_type", "return_type"):
                entry.children = _recursive_fn(
                    index, target_id, 2, max_depth, limit, {start_id}
                )

        entries.append(entry)

    # Sort: extends first, then implements, then parameter_type/return_type
    uses_priority = {
        "extends": 0,
        "implements": 1,
        "property_type": 2,
        "parameter_type": 3,
        "return_type": 3,
        "instantiation": 4,
        "type_hint": 5,
    }

    def sort_key(e):
        pri = uses_priority.get(e.ref_type, 10)
        return (pri, e.file or "", e.line if e.line is not None else 0)

    entries.sort(key=sort_key)
    return entries[:limit]
