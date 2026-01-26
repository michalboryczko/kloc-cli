"""Ownership chain query."""

from ..models import OwnersResult, NodeData
from .base import Query


class OwnersQuery(Query[OwnersResult]):
    """Find structural containment chain (Method -> Class -> File)."""

    def execute(self, node_id: str) -> OwnersResult:
        """Execute ownership chain query.

        Args:
            node_id: Node ID to find ownership chain for.

        Returns:
            OwnersResult with chain of containing nodes.
        """
        node = self.index.nodes.get(node_id)
        if not node:
            raise ValueError(f"Node not found: {node_id}")

        chain = [node]
        current_id = node_id

        while True:
            parent_id = self.index.get_contains_parent(current_id)
            if not parent_id:
                break
            parent_node = self.index.nodes.get(parent_id)
            if parent_node:
                chain.append(parent_node)
            current_id = parent_id

        return OwnersResult(chain=chain)
