"""Context query with BFS depth expansion and tree structure."""

from typing import Optional

from ..models import ContextResult, ContextEntry, MemberRef, InheritEntry, OverrideEntry, NodeData
from ..models.edge import EdgeData
from .base import Query


def _infer_reference_type(edge: EdgeData, target_node: Optional[NodeData]) -> str:
    """Infer reference type from edge type and target node kind.

    Without calls.json, we can only infer based on sot.json edge metadata.
    This provides a best-effort classification that may be ambiguous in some cases.

    Reference Type Inference Rules:
    | Edge Type | Target Kind | Inferred Reference Type |
    |-----------|-------------|------------------------|
    | extends   | Class       | extends                |
    | implements| Interface   | implements             |
    | uses_trait| Trait       | uses_trait             |
    | uses      | Method      | method_call (could be static) |
    | uses      | Property    | property_access or type_hint |
    | uses      | Class       | type_hint (could be instantiation) |

    Args:
        edge: The edge data from sot.json
        target_node: The target node of the edge (if resolved)

    Returns:
        A reference type string (e.g., "method_call", "type_hint", "extends")
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
            # Most common case is type_hint (parameter types, return types, property types)
            # Could also be instantiation, but we can't distinguish without calls.json
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
        direct_only: bool = False, calls_data=None
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
            calls_data: Optional CallsData instance for access chain resolution.

        Returns:
            ContextResult with tree structures for used_by and uses.
        """
        target_node = self.index.nodes.get(node_id)
        if not target_node:
            raise ValueError(f"Node not found: {node_id}")

        used_by = self._build_incoming_tree(node_id, depth, limit, include_impl, direct_only, calls_data)
        uses = self._build_outgoing_tree(node_id, depth, limit, include_impl)

        return ContextResult(
            target=target_node,
            max_depth=depth,
            used_by=used_by,
            uses=uses,
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

    def _build_incoming_tree(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False,
        direct_only: bool = False, calls_data=None
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

        If calls_data is provided, uses calls.json for authoritative reference types
        and access chain building.
        """
        visited = {start_id}
        count = [0]  # Tracks unique sources for limit

        def build_tree(current_id: str, current_depth: int) -> list[ContextEntry]:
            if current_depth > max_depth or count[0] >= limit:
                return []

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

                    if is_member:
                        target_node = self.index.nodes.get(edge.target)
                        if target_node:
                            # Try to get call record from calls_data for authoritative info
                            call_record = None
                            if calls_data and file and line is not None:
                                # calls.json uses 1-based lines, sot.json uses 0-based
                                # Use target_node.symbol to disambiguate multiple calls on same line
                                call_record = calls_data.get_call_at(
                                    file, line + 1, callee=target_node.symbol
                                )

                            # Get reference type - prefer calls_data if available
                            if call_record:
                                reference_type = calls_data.get_reference_type(call_record)
                                access_chain = calls_data.build_chain_for_callee(call_record)
                            else:
                                reference_type = _infer_reference_type(edge, target_node)

                            member_ref = MemberRef(
                                target_name=self._member_display_name(target_node),
                                target_fqn=target_node.fqn,
                                target_kind=target_node.kind,
                                file=file,
                                line=line,
                                reference_type=reference_type,
                                access_chain=access_chain,
                            )
                    else:
                        # Direct reference to the target node itself
                        # Infer reference type for direct edges (extends, implements, type_hint)
                        target_node = self.index.nodes.get(edge.target)
                        reference_type = _infer_reference_type(edge, target_node)
                        # For direct edges, create a member_ref to hold the reference_type
                        # even though it's not a member reference
                        member_ref = MemberRef(
                            target_name="",  # No specific member
                            target_fqn=target_node.fqn if target_node else edge.target,
                            target_kind=target_node.kind if target_node else None,
                            file=file,
                            line=line,
                            reference_type=reference_type,
                        )

                    entry = ContextEntry(
                        depth=current_depth,
                        node_id=source_id,
                        fqn=source_node.fqn if source_node else source_id,
                        kind=source_node.kind if source_node else None,
                        file=file,
                        line=line,
                        signature=source_node.signature if source_node else None,
                        children=[],
                        member_ref=member_ref,
                    )
                    entries.append(entry)

            # --- Pass 2: expand children for first entry per source ---
            # Done after all current-depth entries are claimed, so child
            # expansion can't steal siblings.
            if current_depth < max_depth:
                expanded = set()
                for entry in entries:
                    if entry.node_id not in expanded:
                        expanded.add(entry.node_id)
                        entry.children = build_tree(entry.node_id, current_depth + 1)

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

    def _build_outgoing_tree(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False
    ) -> list[ContextEntry]:
        """Build nested tree for outgoing dependencies (uses).

        If include_impl is True, attach implementations for interfaces/methods
        with their dependencies expanded.
        """
        visited = {start_id}
        count = [0]  # Use list to allow mutation in nested function
        # Track nodes we've already shown implementations for to prevent infinite loops
        shown_impl_for: set[str] = set()

        def build_tree(current_id: str, current_depth: int) -> list[ContextEntry]:
            if current_depth > max_depth or count[0] >= limit:
                return []

            entries = []
            edges = self.index.get_deps(current_id)

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

                entry = ContextEntry(
                    depth=current_depth,
                    node_id=target_id,
                    fqn=target_node.fqn if target_node else target_id,
                    kind=target_node.kind if target_node else None,
                    file=file,
                    line=line,
                    signature=target_node.signature if target_node else None,
                    children=[],
                    implementations=[],
                )

                # Attach implementations for interfaces/methods with their deps expanded
                # Skip if we've already shown implementations for this node
                if include_impl and target_node and target_id not in shown_impl_for:
                    shown_impl_for.add(target_id)
                    entry.implementations = self._get_implementations_for_node(
                        target_node, current_depth, max_depth, limit, visited, count, shown_impl_for
                    )

                # Recurse for children
                if current_depth < max_depth:
                    entry.children = build_tree(target_id, current_depth + 1)

                entries.append(entry)

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

            entry = ContextEntry(
                depth=depth,
                node_id=target_id,
                fqn=target_node.fqn if target_node else target_id,
                kind=target_node.kind if target_node else None,
                file=file,
                line=line,
                signature=target_node.signature if target_node else None,
                children=[],
                implementations=[],
            )

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
