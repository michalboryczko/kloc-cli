"""Find usages query with depth and tree support."""

from ..models import UsageResult, UsageEntry, UsagesTreeResult
from .base import Query


class UsagesQuery(Query[UsagesTreeResult]):
    """Find all usages of a symbol with depth expansion."""

    def execute(
        self, node_id: str, depth: int = 1, limit: int = 100
    ) -> UsagesTreeResult:
        """Execute usages query with BFS depth expansion.

        Args:
            node_id: Node ID to find usages for.
            depth: BFS depth for expansion (1 = direct only).
            limit: Maximum total results.

        Returns:
            UsagesTreeResult with tree structure.
        """
        target = self.index.nodes.get(node_id)
        if not target:
            raise ValueError(f"Node not found: {node_id}")

        visited = {node_id}
        count = [0]  # Use list to allow mutation in nested function

        def build_tree(current_id: str, current_depth: int) -> list[UsageEntry]:
            if current_depth > depth or count[0] >= limit:
                return []

            entries = []
            edges = self.index.get_usages(current_id)

            for edge in edges:
                source_id = edge.source
                if source_id in visited:
                    continue
                visited.add(source_id)

                if count[0] >= limit:
                    break
                count[0] += 1

                source_node = self.index.nodes.get(source_id)

                if edge.location:
                    file = edge.location.get("file")
                    line = edge.location.get("line")
                elif source_node:
                    file = source_node.file
                    line = source_node.start_line
                else:
                    file = None
                    line = None

                entry = UsageEntry(
                    depth=current_depth,
                    node_id=source_id,
                    fqn=source_node.fqn if source_node else source_id,
                    file=file,
                    line=line,
                    children=[],
                )

                # Recurse for children
                if current_depth < depth:
                    entry.children = build_tree(source_id, current_depth + 1)

                entries.append(entry)

            return entries

        tree = build_tree(node_id, 1)

        return UsagesTreeResult(
            target=target,
            max_depth=depth,
            tree=tree,
        )

    def execute_flat(self, node_id: str, limit: int = 100) -> list[UsageResult]:
        """Execute flat usages query (legacy format).

        Args:
            node_id: Node ID to find usages for.
            limit: Maximum number of results.

        Returns:
            List of UsageResult objects.
        """
        results = []
        edges = self.index.get_usages(node_id)[:limit]

        for edge in edges:
            source_node = self.index.nodes.get(edge.source)

            if edge.location:
                file = edge.location.get("file")
                line = edge.location.get("line")
            elif source_node:
                file = source_node.file
                line = source_node.start_line
            else:
                file = None
                line = None

            results.append(
                UsageResult(
                    file=file,
                    line=line,
                    referrer_fqn=source_node.fqn if source_node else edge.source,
                    referrer_id=edge.source,
                )
            )

        return results
