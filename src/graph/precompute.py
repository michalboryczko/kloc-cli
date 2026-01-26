"""Precomputation module for transitive closures and derived data."""

from typing import Optional
from collections import defaultdict

from ..models import NodeData, EdgeData


class PrecomputedGraph:
    """Holds precomputed transitive closures and derived data.

    Built once on index load to make queries O(1) instead of O(n).
    """

    def __init__(self):
        # Direct edges (from SoT)
        self.extends: dict[str, str] = {}  # child → parent
        self.implements: dict[str, set[str]] = defaultdict(set)  # class → interfaces
        self.overrides: dict[str, str] = {}  # method → parent method
        self.contains: dict[str, str] = {}  # child → parent

        # Transitive closures (precomputed)
        self.ancestors: dict[str, list[str]] = {}  # class → [parent, grandparent, ...]
        self.descendants: dict[str, list[str]] = {}  # class → [child, grandchild, ...]
        self.all_interfaces: dict[str, set[str]] = {}  # class → all implemented (incl. inherited)
        self.override_root: dict[str, str] = {}  # method → original definition
        self.override_chain_up: dict[str, list[str]] = {}  # method → [parent_method, ...]
        self.override_chain_down: dict[str, list[str]] = {}  # method → [child_methods, ...]
        self.containment_path: dict[str, list[str]] = {}  # symbol → [file, class, method, ...]

    @classmethod
    def build(
        cls,
        nodes: dict[str, NodeData],
        edges: list[EdgeData],
    ) -> "PrecomputedGraph":
        """Build precomputed graph from nodes and edges."""
        graph = cls()

        # First pass: extract direct relationships
        for edge in edges:
            if edge.type == "extends":
                graph.extends[edge.source] = edge.target
            elif edge.type == "implements":
                graph.implements[edge.source].add(edge.target)
            elif edge.type == "overrides":
                graph.overrides[edge.source] = edge.target
            elif edge.type == "contains":
                graph.contains[edge.source] = edge.target

        # Second pass: compute transitive closures
        graph._compute_inheritance_closures(nodes)
        graph._compute_interface_closures(nodes)
        graph._compute_override_closures(nodes)
        graph._compute_containment_paths(nodes)

        return graph

    def _compute_inheritance_closures(self, nodes: dict[str, NodeData]):
        """Compute ancestor and descendant chains for all classes."""
        # Compute ancestors (upward traversal)
        for node_id in nodes:
            if node_id not in self.ancestors:
                self.ancestors[node_id] = self._get_ancestors(node_id)

        # Build descendants from ancestors (reverse mapping)
        descendants_sets: dict[str, set[str]] = defaultdict(set)
        for node_id, ancestor_list in self.ancestors.items():
            for ancestor_id in ancestor_list:
                descendants_sets[ancestor_id].add(node_id)

        # Convert to lists and sort for determinism
        for node_id, desc_set in descendants_sets.items():
            self.descendants[node_id] = sorted(desc_set)

        # Ensure all nodes have an entry (even if empty)
        for node_id in nodes:
            if node_id not in self.descendants:
                self.descendants[node_id] = []

    def _get_ancestors(self, node_id: str) -> list[str]:
        """Get all ancestors of a node (memoized)."""
        if node_id in self.ancestors:
            return self.ancestors[node_id]

        ancestors = []
        current = node_id
        visited = {node_id}

        while current in self.extends:
            parent = self.extends[current]
            if parent in visited:
                break  # Cycle detection
            visited.add(parent)
            ancestors.append(parent)
            current = parent

        return ancestors

    def _compute_interface_closures(self, nodes: dict[str, NodeData]):
        """Compute all interfaces (including inherited) for each class."""
        for node_id in nodes:
            all_interfaces: set[str] = set()

            # Direct implementations
            all_interfaces.update(self.implements.get(node_id, set()))

            # Inherited implementations from ancestors
            for ancestor_id in self.ancestors.get(node_id, []):
                all_interfaces.update(self.implements.get(ancestor_id, set()))

            self.all_interfaces[node_id] = all_interfaces

    def _compute_override_closures(self, nodes: dict[str, NodeData]):
        """Compute override chains and roots for all methods."""
        # Build reverse mapping for override_chain_down
        overridden_by: dict[str, list[str]] = defaultdict(list)
        for method_id, parent_id in self.overrides.items():
            overridden_by[parent_id].append(method_id)

        # Compute upward chains and roots
        for method_id in nodes:
            if nodes[method_id].kind == "Method":
                chain_up = []
                current = method_id
                root = method_id

                while current in self.overrides:
                    parent = self.overrides[current]
                    chain_up.append(parent)
                    root = parent
                    current = parent

                self.override_chain_up[method_id] = chain_up
                self.override_root[method_id] = root

        # Compute downward chains (BFS from each method)
        for method_id in nodes:
            if nodes[method_id].kind == "Method":
                chain_down = []
                to_visit = [method_id]
                visited = {method_id}

                while to_visit:
                    current = to_visit.pop(0)
                    for child in overridden_by.get(current, []):
                        if child not in visited:
                            visited.add(child)
                            chain_down.append(child)
                            to_visit.append(child)

                self.override_chain_down[method_id] = chain_down

    def _compute_containment_paths(self, nodes: dict[str, NodeData]):
        """Compute containment paths for all nodes."""
        for node_id in nodes:
            path = [node_id]
            current = node_id

            while current in self.contains:
                parent = self.contains[current]
                path.append(parent)
                current = parent

            # Reverse so file is first, then class, then method, etc.
            self.containment_path[node_id] = list(reversed(path))

    # Query methods
    def get_ancestors(self, node_id: str) -> list[str]:
        """Get all ancestors of a class (O(1) lookup)."""
        return self.ancestors.get(node_id, [])

    def get_descendants(self, node_id: str) -> list[str]:
        """Get all descendants of a class (O(1) lookup)."""
        return self.descendants.get(node_id, [])

    def get_all_interfaces(self, node_id: str) -> set[str]:
        """Get all interfaces implemented by a class (O(1) lookup)."""
        return self.all_interfaces.get(node_id, set())

    def get_override_root(self, method_id: str) -> str:
        """Get the original definition of an overridden method (O(1) lookup)."""
        return self.override_root.get(method_id, method_id)

    def get_override_chain_up(self, method_id: str) -> list[str]:
        """Get the upward override chain (O(1) lookup)."""
        return self.override_chain_up.get(method_id, [])

    def get_override_chain_down(self, method_id: str) -> list[str]:
        """Get the downward override chain (O(1) lookup)."""
        return self.override_chain_down.get(method_id, [])

    def get_containment_path(self, node_id: str) -> list[str]:
        """Get the containment path from root to node (O(1) lookup)."""
        return self.containment_path.get(node_id, [node_id])
