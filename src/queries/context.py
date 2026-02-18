"""Context query with BFS depth expansion and tree structure."""

from typing import Optional, TYPE_CHECKING

from ..models import ContextResult, ContextEntry, MemberRef, InheritEntry, OverrideEntry, NodeData, ArgumentInfo, DefinitionInfo
from ..models.edge import EdgeData
from .base import Query
from .definition import build_definition
from .graph_utils import (
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
    member_display_name,
    resolve_receiver_identity,
    resolve_containing_method,
    is_internal_reference,
    resolve_param_name,
    build_external_call_fqn,
    find_result_var,
    find_local_value_for_call,
    get_argument_info,
    resolve_param_fqn,
    resolve_promoted_property_fqn,
    get_promoted_params,
    trace_source_chain,
    get_single_argument_info,
    get_all_children,
)
from .value_context import (
    build_value_consumer_chain,
    cross_into_callee,
    get_type_of,
    cross_into_callers_via_return,
    build_value_source_chain,
    build_parameter_uses,
)
from .method_context import (
    get_type_references,
    build_execution_flow,
    filter_orphan_property_accesses,
)
from .polymorphic import (
    get_implementations_for_node,
    build_deps_subtree,
    get_interface_method_ids,
    get_concrete_implementors,
    build_implementations_tree,
    build_overrides_tree,
)
from .property_context import (
    build_property_uses,
    build_property_callers_filtered,
    build_property_used_by,
)
from .class_context import (
    build_class_used_by,
    build_caller_chain,
    build_caller_chain_for_method,
    build_class_uses,
    build_class_uses_recursive,
)
from .interface_context import (
    build_interface_used_by,
    build_interface_injection_point_calls,
    build_interface_uses,
)

if TYPE_CHECKING:
    from ..graph import SoTIndex


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

        definition = build_definition(self.index, node_id)
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

    # === Deferred builder callbacks (break circular deps between modules) ===

    def _wired_execution_flow(
        self, method_id, depth, max_depth, limit, cycle_guard, count,
        include_impl=False, shown_impl_for=None,
    ):
        """Wired callback: build_execution_flow with implementations_fn bound."""
        return build_execution_flow(
            self.index, method_id, depth, max_depth, limit, cycle_guard, count,
            include_impl=include_impl, shown_impl_for=shown_impl_for,
            implementations_fn=self._wired_implementations,
        )

    def _wired_implementations(
        self, node, depth, max_depth, limit, visited, count, shown_impl_for,
    ):
        """Wired callback: get_implementations_for_node with execution_flow_fn bound."""
        return get_implementations_for_node(
            self.index, node, depth, max_depth, limit, visited, count, shown_impl_for,
            execution_flow_fn=self._wired_execution_flow,
        )

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

        # Value nodes: use dedicated consumer chain traversal instead of
        # generic uses-edge traversal. Value nodes have no incoming 'uses'
        # edges — their consumers are tracked through receiver and argument edges.
        if start_node and start_node.kind == "Value":
            return build_value_consumer_chain(self.index, start_id, 1, max_depth, limit, visited=set())

        # ISSUE-F: Property nodes — trace who reads this property across methods
        if start_node and start_node.kind == "Property":
            return build_property_used_by(self.index, start_id, 1, max_depth, limit,
                caller_chain_fn=lambda mid, d, md: build_caller_chain_for_method(self.index, mid, d, md))

        # ISSUE-B: Class nodes — grouped, sorted, deduped USED BY
        if start_node and start_node.kind == "Class":
            return build_class_used_by(
                self.index, start_id, max_depth, limit, include_impl,
                caller_chain_for_method_fn=lambda idx, mid, d, md: build_caller_chain_for_method(idx, mid, d, md),
                injection_point_calls_fn=None,
                interface_injection_point_calls_fn=lambda nid, iid, d, md: build_interface_injection_point_calls(self.index, nid, iid, d, md),
            )

        # ISSUE-D: Interface nodes — implementors + injection points USED BY
        if start_node and start_node.kind == "Interface":
            return build_interface_used_by(
                self.index, start_id, max_depth, limit, include_impl,
                interface_injection_point_calls_fn=lambda idx, pid, iid, d, md: build_interface_injection_point_calls(idx, pid, iid, d, md),
            )

        # ISSUE-A: Constructor special case — redirect usedBy to containing Class node.
        # `new ClassName(...)` creates a uses edge targeting the Class, not __construct().
        # So querying __construct() directly finds no usages. Redirect to Class-level lookup.
        if start_node and start_node.kind == "Method" and start_node.name == "__construct":
            containing_class_id = self.index.get_contains_parent(start_id)
            if containing_class_id:
                class_node = self.index.nodes.get(containing_class_id)
                if class_node and class_node.kind in ("Class", "Enum"):
                    return build_class_used_by(
                        self.index, containing_class_id, max_depth, limit, include_impl,
                        interface_injection_point_calls_fn=lambda nid, iid, d, md: build_interface_injection_point_calls(self.index, nid, iid, d, md),
                    )

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
                    if is_internal_reference(self.index,source_id, start_id):
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
                    call_node_id = None

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
                                target_name=member_display_name(target_node),
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
                            target_name=member_display_name(target_node) if target_node else "",
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

                    # ISSUE-I: Add argument info for method_call entries
                    arguments = []
                    if call_node_id:
                        arguments = get_argument_info(self.index,call_node_id)

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
                        arguments=arguments,
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
                        containing_method_id = resolve_containing_method(self.index,entry.node_id)
                        if containing_method_id and containing_method_id not in branch_visited:
                            # Create a new branch_visited set for this branch to prevent cycles
                            child_branch_visited = branch_visited | {containing_method_id}
                            entry.children = build_tree(
                                containing_method_id, current_depth + 1, child_branch_visited
                            )

            return entries

        # Build direct usages first
        direct_entries = build_tree(start_id, 1)

        # If include_impl, also build interface/implementation usages
        interface_entries = []
        if include_impl:
            # Existing: concrete -> interface direction
            # When querying a concrete method, also show callers of the interface method
            interface_method_ids = get_interface_method_ids(self.index,start_id)
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
            type_entries = get_type_references(self.index,
                start_id, 1, cycle_guard, count, limit
            )
            # Get execution flow from Call children
            call_entries = self._wired_execution_flow(
                start_id, 1, max_depth, limit, cycle_guard, count,
                include_impl=include_impl, shown_impl_for=shown_impl_for,
            )

            # ISSUE-B: interface -> concrete direction for USES section.
            # When querying an interface method with --impl, the interface method
            # itself is abstract (no execution flow). Show the execution flows
            # of each concrete implementation grouped by implementor.
            impl_entries: list[ContextEntry] = []
            if include_impl and not call_entries:
                concrete_method_ids = get_concrete_implementors(self.index,start_id)
                for concrete_id in concrete_method_ids:
                    concrete_node = self.index.nodes.get(concrete_id)
                    if not concrete_node:
                        continue
                    # Build execution flow for this concrete method
                    impl_cycle_guard = {concrete_id}
                    impl_count = [0]
                    impl_shown = set()
                    impl_type_entries = get_type_references(self.index,
                        concrete_id, 1, impl_cycle_guard, impl_count, limit
                    )
                    impl_call_entries = self._wired_execution_flow(
                        concrete_id, 1, max_depth, limit, impl_cycle_guard, impl_count,
                        include_impl=False, shown_impl_for=impl_shown,
                    )
                    impl_children = impl_type_entries + impl_call_entries
                    concrete_fqn = concrete_node.fqn
                    if concrete_node.kind == "Method" and not concrete_fqn.endswith("()"):
                        concrete_fqn += "()"
                    impl_entry = ContextEntry(
                        depth=0,
                        node_id=concrete_id,
                        fqn=concrete_fqn,
                        kind=concrete_node.kind,
                        file=concrete_node.file,
                        line=concrete_node.start_line,
                        signature=concrete_node.signature,
                        children=impl_children,
                        via_interface=True,
                    )
                    impl_entries.append(impl_entry)

            # Combine: type references first, then call entries, then impl entries
            return type_entries + call_entries + impl_entries

        # For Value nodes, use source chain traversal
        if start_node and start_node.kind == "Value":
            return build_value_source_chain(self.index,start_id, 1, max_depth, limit, visited=set())

        # ISSUE-F: Property nodes — trace who sets this property (assigned_from -> parameter -> callers)
        if start_node and start_node.kind == "Property":
            return build_property_uses(self.index, start_id, 1, max_depth, limit)

        # ISSUE-C: Class nodes — grouped, deduped USES with behavioral depth 2
        if start_node and start_node.kind == "Class":
            return build_class_uses(
                self.index, start_id, max_depth, limit, include_impl,
                execution_flow_fn=self._wired_execution_flow,
            )

        # ISSUE-D: Interface nodes — signature types + extends USES
        if start_node and start_node.kind == "Interface":
            return build_interface_uses(self.index, start_id, max_depth, limit, include_impl)

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
                        arguments = get_argument_info(self.index,call_node_id)
                        result_var = find_result_var(self.index,call_node_id)
                    else:
                        # Fall back to inference from edge/node types
                        reference_type = _infer_reference_type(edge, target_node, self.index)

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
                    entry.implementations = self._wired_implementations(
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
