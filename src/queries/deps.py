"""Find dependencies query with depth and tree support."""

from ..models import DepsResult, DepsEntry, DepsTreeResult
from .base import Query


class DepsQuery(Query[DepsTreeResult]):
    """Find all dependencies of a symbol with depth expansion."""

    def execute(
        self, node_id: str, depth: int = 1, limit: int = 100
    ) -> DepsTreeResult:
        """Execute dependencies query with BFS depth expansion.

        Args:
            node_id: Node ID to find dependencies for.
            depth: BFS depth for expansion (1 = direct only).
            limit: Maximum total results.

        Returns:
            DepsTreeResult with tree structure.
        """
        target = self.index.nodes.get(node_id)
        if not target:
            raise ValueError(f"Node not found: {node_id}")

        visited = {node_id}
        count = [0]  # Use list to allow mutation in nested function

        def build_tree(current_id: str, current_depth: int) -> list[DepsEntry]:
            if current_depth > depth or count[0] >= limit:
                return []

            entries = []
            edges = self.index.get_deps(current_id)

            for edge in edges:
                dep_id = edge.target
                if dep_id in visited:
                    continue
                visited.add(dep_id)

                if count[0] >= limit:
                    break
                count[0] += 1

                dep_node = self.index.nodes.get(dep_id)

                if edge.location:
                    file = edge.location.get("file")
                    line = edge.location.get("line")
                else:
                    file = dep_node.file if dep_node else None
                    line = None

                entry = DepsEntry(
                    depth=current_depth,
                    node_id=dep_id,
                    fqn=dep_node.fqn if dep_node else dep_id,
                    file=file,
                    line=line,
                    children=[],
                )

                # Recurse for children
                if current_depth < depth:
                    entry.children = build_tree(dep_id, current_depth + 1)

                entries.append(entry)

            return entries

        tree = build_tree(node_id, 1)

        return DepsTreeResult(
            target=target,
            max_depth=depth,
            tree=tree,
        )

    def execute_flat(self, node_id: str, limit: int = 100) -> list[DepsResult]:
        """Execute flat dependencies query (legacy format).

        Args:
            node_id: Node ID to find dependencies for.
            limit: Maximum number of results.

        Returns:
            List of DepsResult objects.
        """
        results = []
        edges = self.index.get_deps(node_id)[:limit]

        for edge in edges:
            target_node = self.index.nodes.get(edge.target)

            if edge.location:
                file = edge.location.get("file")
                line = edge.location.get("line")
            else:
                file = None
                line = None

            results.append(
                DepsResult(
                    file=file,
                    line=line,
                    target_fqn=target_node.fqn if target_node else edge.target,
                    target_id=edge.target,
                )
            )

        return results
