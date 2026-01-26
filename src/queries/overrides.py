"""Override chain query."""

from collections import deque

from ..models import OverrideEntry, OverridesTreeResult, NodeData
from .base import Query


class OverridesQuery(Query[OverridesTreeResult]):
    """Find override chain/tree for a method.

    Supports depth-limited BFS for exploring multi-level overrides.
    """

    def execute(
        self, node_id: str, direction: str = "up", depth: int = 1, limit: int = 100
    ) -> OverridesTreeResult:
        """Execute override query with depth expansion.

        Args:
            node_id: Node ID to find override chain for.
            direction: "up" for overridden methods, "down" for overriding methods.
            depth: Maximum BFS depth (default: 1).
            limit: Maximum total results (default: 100).

        Returns:
            OverridesTreeResult with tree structure.
        """
        node = self.index.nodes.get(node_id)
        if not node:
            raise ValueError(f"Node not found: {node_id}")

        if node.kind != "Method":
            raise ValueError(f"Node must be Method, got: {node.kind}")

        if direction == "up":
            tree = self._bfs_overrides_up(node, depth, limit)
        else:
            tree = self._bfs_overrides_down(node, depth, limit)

        return OverridesTreeResult(
            root=node,
            direction=direction,
            max_depth=depth,
            tree=tree,
        )

    def _bfs_overrides_up(
        self, start_node: NodeData, max_depth: int, limit: int
    ) -> list[OverrideEntry]:
        """BFS traversal upward to overridden methods.

        For "up" direction, typically single chain (one parent method).
        """
        tree: list[OverrideEntry] = []
        visited = {start_node.id}
        count = 0

        # Queue: (node_id, current_depth, parent_entry or None)
        queue: deque[tuple[str, int, OverrideEntry | None]] = deque()

        # Get direct parent (method this overrides)
        parent_id = self.index.get_overrides_parent(start_node.id)
        if parent_id and parent_id not in visited:
            queue.append((parent_id, 1, None))

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
                grandparent_id = self.index.get_overrides_parent(current_id)
                if grandparent_id and grandparent_id not in visited:
                    queue.append((grandparent_id, current_depth + 1, entry))

        return tree

    def _bfs_overrides_down(
        self, start_node: NodeData, max_depth: int, limit: int
    ) -> list[OverrideEntry]:
        """BFS traversal downward to overriding methods.

        For "down" direction, a method can be overridden by multiple classes.
        """
        tree: list[OverrideEntry] = []
        visited = {start_node.id}
        count = 0

        # Queue: (node_id, current_depth, parent_entry or None)
        queue: deque[tuple[str, int, OverrideEntry | None]] = deque()

        # Get direct children (methods that override this)
        child_ids = self.index.get_overridden_by(start_node.id)
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
