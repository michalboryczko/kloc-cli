"""Inheritance chain query."""

from collections import deque

from ..models import InheritEntry, InheritTreeResult, NodeData
from .base import Query


class InheritQuery(Query[InheritTreeResult]):
    """Find inheritance chain/tree for a class.

    Supports depth-limited BFS for exploring multi-level inheritance.
    Uses precomputed closures for O(1) lookups when available.
    """

    ALLOWED_KINDS = {"Class", "Interface", "Trait", "Enum"}

    def execute(
        self, node_id: str, direction: str = "up", depth: int = 1, limit: int = 100
    ) -> InheritTreeResult:
        """Execute inheritance query with depth expansion.

        Args:
            node_id: Node ID to find inheritance for.
            direction: "up" for ancestors, "down" for descendants.
            depth: Maximum BFS depth (default: 1).
            limit: Maximum total results (default: 100).

        Returns:
            InheritTreeResult with tree structure.
        """
        node = self.index.nodes.get(node_id)
        if not node:
            raise ValueError(f"Node not found: {node_id}")

        if node.kind not in self.ALLOWED_KINDS:
            raise ValueError(
                f"Node must be Class/Interface/Trait/Enum, got: {node.kind}"
            )

        if direction == "up":
            tree = self._bfs_ancestors(node, depth, limit)
        else:
            tree = self._bfs_descendants(node, depth, limit)

        return InheritTreeResult(
            root=node,
            direction=direction,
            max_depth=depth,
            tree=tree,
        )

    def _get_all_parents(self, node_id: str) -> list[str]:
        """Get all parent types (extends + implements)."""
        parents = []
        # Class/interface that this extends
        extends_parent = self.index.get_extends_parent(node_id)
        if extends_parent:
            parents.append(extends_parent)
        # Interfaces that this implements
        parents.extend(self.index.get_implements(node_id))
        return parents

    def _bfs_ancestors(
        self, start_node: NodeData, max_depth: int, limit: int
    ) -> list[InheritEntry]:
        """BFS traversal upward to ancestors.

        For "up" direction, includes both extended classes and implemented interfaces.
        """
        tree: list[InheritEntry] = []
        visited = {start_node.id}
        count = 0

        # Queue: (node_id, current_depth, parent_entry or None)
        queue: deque[tuple[str, int, InheritEntry | None]] = deque()

        # Get direct parents (extends/implements)
        parent_ids = self._get_all_parents(start_node.id)
        for pid in parent_ids:
            if pid not in visited:
                queue.append((pid, 1, None))

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
                grandparent_ids = self._get_all_parents(current_id)
                for gp_id in grandparent_ids:
                    if gp_id not in visited:
                        queue.append((gp_id, current_depth + 1, entry))

        return tree

    def _get_all_children(self, node_id: str) -> list[str]:
        """Get all classes that extend or implement this class/interface."""
        children = []
        # Classes that extend this
        children.extend(self.index.get_extends_children(node_id))
        # Classes that implement this (for interfaces)
        children.extend(self.index.get_implementors(node_id))
        return children

    def _bfs_descendants(
        self, start_node: NodeData, max_depth: int, limit: int
    ) -> list[InheritEntry]:
        """BFS traversal downward to descendants.

        For "down" direction, a class can have multiple children.
        Includes both classes that extend and classes that implement.
        """
        tree: list[InheritEntry] = []
        visited = {start_node.id}
        count = 0

        # Queue: (node_id, current_depth, parent_entry or None)
        queue: deque[tuple[str, int, InheritEntry | None]] = deque()

        # Get direct children (classes that extend/implement this)
        child_ids = self._get_all_children(start_node.id)
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
