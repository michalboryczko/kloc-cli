"""Polymorphic analysis: implementation/override discovery.

Handles interface method implementations, class hierarchy traversal,
and override trees for polymorphic analysis in USES direction.

All functions are standalone with an explicit `index` parameter.

CIRCULAR DEPENDENCY NOTE:
get_implementations_for_node() calls build_execution_flow() (in method_context.py)
and build_execution_flow() calls get_implementations_for_node(). This is broken
by deferred builder callbacks: both functions accept an optional callback parameter
that the orchestrator (context.py) wires at runtime.
"""

from collections import deque
from typing import Optional, Callable, TYPE_CHECKING

from ..models import ContextEntry, MemberRef, InheritEntry, OverrideEntry, NodeData
from .graph_utils import get_all_children
from .reference_types import (
    find_call_for_usage,
    build_access_chain,
    get_reference_type_from_call,
    resolve_access_chain_symbol,
    _infer_reference_type,
)
from .graph_utils import (
    member_display_name,
    get_argument_info,
    find_result_var,
)

if TYPE_CHECKING:
    from ..graph import SoTIndex


def get_implementations_for_node(
    index: "SoTIndex", node: NodeData, depth: int, max_depth: int, limit: int,
    visited: set, count: list, shown_impl_for: set,
    execution_flow_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Get implementations for an interface/class or implementing methods for interface methods.

    For interfaces/classes: returns implementing classes with their dependencies expanded.
    For methods: returns implementing methods (via overrides or interface implementation).

    Note: Implementation subtrees use their own visited set to show full dependency tree
    even if some nodes were already shown in the main tree. The shown_impl_for set prevents
    infinite loops by tracking which nodes we've already shown implementations for.

    Args:
        index: The SoT index.
        node: The node to find implementations for.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        visited: Visited set for cycle detection.
        count: Mutable list[int] tracking total entries.
        shown_impl_for: Set tracking nodes with implementations already shown.
        execution_flow_fn: Callback to build execution flow (breaks circular dep with method_context).
            Signature: (method_id, depth, max_depth, limit, cycle_guard, count,
                        include_impl, shown_impl_for) -> list[ContextEntry]
    """
    INHERITABLE_KINDS = {"Class", "Interface", "Trait", "Enum"}
    implementations = []

    if node.kind in INHERITABLE_KINDS:
        # Get all classes that implement/extend this
        impl_ids = get_all_children(index, node.id)
        for impl_id in impl_ids:
            impl_node = index.nodes.get(impl_id)
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

                # Recurse into implementation's execution flow with FRESH cycle guard
                # so we show the full tree even if nodes appeared elsewhere
                if depth < max_depth and execution_flow_fn:
                    impl_cycle_guard = {impl_id}
                    impl_count = [0]
                    entry.children = execution_flow_fn(
                        impl_id, depth + 1, max_depth, limit,
                        impl_cycle_guard, impl_count,
                        True, shown_impl_for,
                    )

                implementations.append(entry)

    elif node.kind == "Method":
        # First try direct overrides
        override_ids = list(index.get_overridden_by(node.id))

        # Also find interface method implementations
        # Get the containing class/interface of this method
        containing_id = index.get_contains_parent(node.id)
        if containing_id:
            containing_node = index.nodes.get(containing_id)
            if containing_node and containing_node.kind in ("Interface", "Trait"):
                # Find implementing classes
                impl_class_ids = get_all_children(index, containing_id)
                method_name = node.name

                for impl_class_id in impl_class_ids:
                    # Find method with same name in implementing class
                    for child_id in index.get_contains_children(impl_class_id):
                        child_node = index.nodes.get(child_id)
                        if child_node and child_node.kind == "Method" and child_node.name == method_name:
                            if child_id not in override_ids:
                                override_ids.append(child_id)

        for override_id in override_ids:
            override_node = index.nodes.get(override_id)
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

                # Recurse into method's execution flow with FRESH cycle guard
                if depth < max_depth and execution_flow_fn:
                    impl_cycle_guard = {override_id}
                    impl_count = [0]
                    entry.children = execution_flow_fn(
                        override_id, depth + 1, max_depth, limit,
                        impl_cycle_guard, impl_count,
                        True, shown_impl_for,
                    )

                implementations.append(entry)

    return implementations


def build_deps_subtree(
    index: "SoTIndex", start_id: str, depth: int, max_depth: int, limit: int,
    visited: set, count: list, include_impl: bool, shown_impl_for: set,
    implementations_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Build dependency subtree for a node.

    Args:
        index: The SoT index.
        start_id: Node ID to build deps for.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        visited: Visited set for cycle detection.
        count: Mutable list[int] tracking total entries.
        include_impl: Whether to include implementations.
        shown_impl_for: Set of node IDs we've already shown implementations for,
                       to prevent infinite loops when implementations depend on their interfaces.
        implementations_fn: Callback for getting implementations (breaks circular dep).
            Signature: (node, depth, max_depth, limit, visited, count, shown_impl_for) -> list[ContextEntry]
    """
    if depth > max_depth or count[0] >= limit:
        return []

    entries = []
    edges = index.get_deps(start_id)

    for edge in edges:
        target_id = edge.target
        if target_id in visited:
            continue
        visited.add(target_id)

        if count[0] >= limit:
            break
        count[0] += 1

        target_node = index.nodes.get(target_id)

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
                index, start_id, target_id, file, line
            )

            reference_type = None
            access_chain = None
            access_chain_symbol = None

            if call_node_id:
                reference_type = get_reference_type_from_call(index, call_node_id)
                access_chain = build_access_chain(index, call_node_id)
                # R4: Resolve access chain property FQN
                access_chain_symbol = resolve_access_chain_symbol(index, call_node_id)
                # Phase 2: Argument tracking
                arguments = get_argument_info(index, call_node_id)
                result_var = find_result_var(index, call_node_id)
            else:
                # Fall back to inference from edge/node types
                reference_type = _infer_reference_type(edge, target_node, index)

            # For USES, populate target_name for consistency (ISSUE-G)
            member_ref = MemberRef(
                target_name=member_display_name(target_node),
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
            if implementations_fn:
                entry.implementations = implementations_fn(
                    target_node, depth, max_depth, limit, visited, count, shown_impl_for
                )

        # Recurse for children
        if depth < max_depth:
            entry.children = build_deps_subtree(
                index, target_id, depth + 1, max_depth, limit, visited, count,
                include_impl, shown_impl_for, implementations_fn=implementations_fn,
            )

        entries.append(entry)

    # R2: Sort entries by (file path, line number) for consistent ordering
    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

    return entries


def get_interface_method_ids(index: "SoTIndex", method_id: str) -> list[str]:
    """Get IDs of interface methods that this method implements.

    For a concrete method like SynxisConfigurationService::getHotelIdForItem(),
    returns the interface method SynxisConfigurationServiceInterface::getHotelIdForItem()
    if the class implements that interface.
    """
    method_node = index.nodes.get(method_id)
    if not method_node or method_node.kind != "Method":
        return []

    interface_methods = []

    # Check direct override relationship first
    override_parent = index.get_overrides_parent(method_id)
    if override_parent:
        parent_node = index.nodes.get(override_parent)
        if parent_node:
            # Add the parent, and recursively check its parents
            interface_methods.append(override_parent)
            interface_methods.extend(get_interface_method_ids(index, override_parent))

    # Find via class->interface->method relationship
    containing_id = index.get_contains_parent(method_id)
    if containing_id:
        containing_node = index.nodes.get(containing_id)
        if containing_node and containing_node.kind in ("Class", "Enum"):
            # Get all interfaces this class implements (including inherited)
            interface_ids = index.get_all_interfaces(containing_id)
            method_name = method_node.name

            for iface_id in interface_ids:
                # Find method with same name in interface
                for child_id in index.get_contains_children(iface_id):
                    child_node = index.nodes.get(child_id)
                    if (child_node and child_node.kind == "Method"
                            and child_node.name == method_name
                            and child_id not in interface_methods):
                        interface_methods.append(child_id)

    return interface_methods


def get_concrete_implementors(index: "SoTIndex", interface_method_id: str) -> list[str]:
    """Get IDs of concrete methods that implement this interface method.

    Reverse lookup of get_interface_method_ids(): given an interface method
    like OrderRepositoryInterface::save(), returns concrete methods like
    [InMemoryOrderRepository::save(), AuditableOrderRepository::save(), ...].
    """
    method_node = index.nodes.get(interface_method_id)
    if not method_node or method_node.kind != "Method":
        return []

    # Find containing interface
    containing_id = index.get_contains_parent(interface_method_id)
    if not containing_id:
        return []
    containing_node = index.nodes.get(containing_id)
    if not containing_node or containing_node.kind != "Interface":
        return []

    method_name = method_node.name
    concrete_methods = []

    # Find all classes that implement this interface
    implementor_ids = index.get_implementors(containing_id)
    # Also check classes that extend interfaces that extend this one
    extends_children = index.get_extends_children(containing_id)
    for ext_child_id in extends_children:
        ext_child = index.nodes.get(ext_child_id)
        if ext_child and ext_child.kind == "Interface":
            implementor_ids.extend(index.get_implementors(ext_child_id))

    for impl_id in implementor_ids:
        # Find method with same name in implementor
        for child_id in index.get_contains_children(impl_id):
            child_node = index.nodes.get(child_id)
            if (child_node and child_node.kind == "Method"
                    and child_node.name == method_name):
                concrete_methods.append(child_id)

    return concrete_methods


def build_implementations_tree(
    index: "SoTIndex", start_id: str, max_depth: int, limit: int
) -> list[InheritEntry]:
    """Build tree of classes that extend/implement this class/interface."""
    tree: list[InheritEntry] = []
    visited = {start_id}
    count = 0

    # Queue: (node_id, current_depth, parent_entry or None)
    queue: deque[tuple[str, int, InheritEntry | None]] = deque()

    # Get direct children (classes that extend/implement this)
    child_ids = get_all_children(index, start_id)
    for cid in child_ids:
        if cid not in visited:
            queue.append((cid, 1, None))

    while queue and count < limit:
        current_id, current_depth, parent_entry = queue.popleft()

        if current_id in visited:
            continue
        visited.add(current_id)

        node = index.nodes.get(current_id)
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
            grandchild_ids = get_all_children(index, current_id)
            for gc_id in grandchild_ids:
                if gc_id not in visited:
                    queue.append((gc_id, current_depth + 1, entry))

    return tree


def build_overrides_tree(
    index: "SoTIndex", start_id: str, max_depth: int, limit: int
) -> list[OverrideEntry]:
    """Build tree of methods that override this method."""
    tree: list[OverrideEntry] = []
    visited = {start_id}
    count = 0

    # Queue: (node_id, current_depth, parent_entry or None)
    queue: deque[tuple[str, int, OverrideEntry | None]] = deque()

    # Get direct children (methods that override this)
    child_ids = index.get_overridden_by(start_id)
    for cid in child_ids:
        if cid not in visited:
            queue.append((cid, 1, None))

    while queue and count < limit:
        current_id, current_depth, parent_entry = queue.popleft()

        if current_id in visited:
            continue
        visited.add(current_id)

        node = index.nodes.get(current_id)
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
            grandchild_ids = index.get_overridden_by(current_id)
            for gc_id in grandchild_ids:
                if gc_id not in visited:
                    queue.append((gc_id, current_depth + 1, entry))

    return tree
