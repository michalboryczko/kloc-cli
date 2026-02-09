"""SoT Index for fast lookups."""

from pathlib import Path
from collections import defaultdict
from typing import Optional

from .loader import load_sot
from .precompute import PrecomputedGraph
from .trie import SymbolTrie, build_symbol_trie
from ..models import NodeData, EdgeData


class SoTIndex:
    """In-memory index over SoT JSON for fast lookups.

    Includes precomputed transitive closures and symbol trie for O(1) queries.
    """

    def __init__(self, sot_path: str | Path, precompute: bool = True):
        """Initialize the index.

        Args:
            sot_path: Path to the SoT JSON file.
            precompute: Whether to build precomputed closures (default True).
        """
        self.sot_path = Path(sot_path)
        self._precompute_enabled = precompute
        self._load()
        self._build_indexes()

    def _load(self):
        """Load SoT JSON from file."""
        data = load_sot(self.sot_path)

        self.version = data.get("version", "1.0")
        self.metadata = data.get("metadata", {})

        self.nodes: dict[str, NodeData] = {}
        for n in data.get("nodes", []):
            node = NodeData(
                id=n["id"],
                kind=n["kind"],
                name=n["name"],
                fqn=n["fqn"],
                symbol=n["symbol"],
                file=n.get("file"),
                range=n.get("range"),
                enclosing_range=n.get("enclosing_range"),
                documentation=n.get("documentation", []),
                # v2.0 fields
                value_kind=n.get("value_kind"),
                type_symbol=n.get("type_symbol"),
                call_kind=n.get("call_kind"),
            )
            self.nodes[node.id] = node

        self.edges: list[EdgeData] = []
        for e in data.get("edges", []):
            edge = EdgeData(
                type=e["type"],
                source=e["source"],
                target=e["target"],
                location=e.get("location"),
                position=e.get("position"),
                expression=e.get("expression"),
            )
            self.edges.append(edge)

    def _build_indexes(self):
        """Build lookup indexes."""
        # Symbol string to node ID
        self.symbol_to_id: dict[str, str] = {}
        # FQN to node ID (may have collisions, store list)
        self.fqn_to_ids: dict[str, list[str]] = defaultdict(list)
        # Short name to node IDs
        self.name_to_ids: dict[str, list[str]] = defaultdict(list)

        for node_id, node in self.nodes.items():
            self.symbol_to_id[node.symbol] = node_id
            self.fqn_to_ids[node.fqn].append(node_id)
            self.fqn_to_ids[node.fqn.lower()].append(node_id)  # case-insensitive
            self.name_to_ids[node.name].append(node_id)
            self.name_to_ids[node.name.lower()].append(node_id)

        # Edge indexes by node ID
        self.outgoing: dict[str, dict[str, list[EdgeData]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.incoming: dict[str, dict[str, list[EdgeData]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for edge in self.edges:
            self.outgoing[edge.source][edge.type].append(edge)
            self.incoming[edge.target][edge.type].append(edge)

        # Build precomputed graph (transitive closures)
        if self._precompute_enabled:
            self.precomputed = PrecomputedGraph.build(self.nodes, self.edges)
            self.trie = build_symbol_trie(self.nodes)
        else:
            self.precomputed = None
            self.trie = None

    def resolve_symbol(self, query: str) -> list[NodeData]:
        """Resolve a symbol query to matching nodes.

        Supports formats:
        - App\\Foo\\Bar (class)
        - App\\Foo\\Bar::method() (method)
        - App\\Foo\\Bar::CONST (const)
        - App\\Foo\\Bar::$prop (property)
        - Short names like Bar or method()
        """
        candidates = []
        seen_ids = set()

        def add_candidate(node):
            if node.id not in seen_ids:
                seen_ids.add(node.id)
                candidates.append(node)

        # Normalize query
        query_normalized = self._normalize_query(query)

        # Try exact FQN match
        if query_normalized in self.fqn_to_ids:
            for node_id in self.fqn_to_ids[query_normalized]:
                add_candidate(self.nodes[node_id])
            return candidates

        # Try case-insensitive FQN
        query_lower = query_normalized.lower()
        if query_lower in self.fqn_to_ids:
            for node_id in self.fqn_to_ids[query_lower]:
                add_candidate(self.nodes[node_id])
            return candidates

        # Use trie for faster partial matching if available
        if self.trie is not None:
            # Try suffix search first (most common use case)
            suffix_ids = self.trie.search_suffix(query_normalized, limit=50)
            for node_id in suffix_ids:
                if node_id in self.nodes:
                    add_candidate(self.nodes[node_id])
            if candidates:
                return candidates

            # Try contains search
            contains_ids = self.trie.search_contains(query_normalized, limit=50)
            for node_id in contains_ids:
                if node_id in self.nodes:
                    add_candidate(self.nodes[node_id])
            if candidates:
                return candidates

        # Fall back to linear search for partial FQN match (suffix)
        for fqn, node_ids in self.fqn_to_ids.items():
            if fqn.endswith(query_normalized) or fqn.endswith("::" + query_normalized):
                for node_id in node_ids:
                    add_candidate(self.nodes[node_id])

        if candidates:
            return candidates

        # Try short name match
        short_name = (
            query_normalized.split("::")[-1]
            if "::" in query_normalized
            else query_normalized
        )
        if short_name in self.name_to_ids:
            for node_id in self.name_to_ids[short_name]:
                add_candidate(self.nodes[node_id])
            return candidates

        # Try name with () stripped
        short_name_no_parens = short_name.rstrip("()")
        if short_name_no_parens in self.name_to_ids:
            for node_id in self.name_to_ids[short_name_no_parens]:
                add_candidate(self.nodes[node_id])

        return candidates

    def _normalize_query(self, query: str) -> str:
        """Normalize a query string to match FQN format."""
        normalized = query.strip()

        # Remove leading backslash
        if normalized.startswith("\\"):
            normalized = normalized[1:]

        return normalized

    def get_usages(self, node_id: str, include_members: bool = True) -> list[EdgeData]:
        """Get all incoming 'uses' edges for a node.

        Args:
            node_id: The node to get usages for.
            include_members: If True and node is a container (class/file),
                            also include usages of contained members.
        """
        direct_usages = self.incoming[node_id].get("uses", [])

        if not include_members:
            return direct_usages

        # For container nodes, also get usages of contained members
        node = self.nodes.get(node_id)
        if node and node.kind in ("Class", "Interface", "Trait", "Enum", "File"):
            all_usages = list(direct_usages)
            seen_sources = {e.source for e in direct_usages}

            # Recursively collect usages of contained members
            def collect_member_usages(parent_id: str):
                for child_id in self.get_contains_children(parent_id):
                    for edge in self.incoming[child_id].get("uses", []):
                        if edge.source not in seen_sources:
                            seen_sources.add(edge.source)
                            all_usages.append(edge)
                    # Recurse into nested containers
                    collect_member_usages(child_id)

            collect_member_usages(node_id)
            return all_usages

        return direct_usages

    def get_usages_grouped(self, node_id: str) -> dict[str, list[EdgeData]]:
        """Get all incoming uses edges for a node and its members, grouped by source.

        Unlike get_usages(), does NOT deduplicate by source - returns all edges
        so callers can see every member reference from each source.

        Returns:
            Dict mapping source_id -> list of EdgeData (may target the node
            itself or any of its contained members).
        """
        grouped: dict[str, list[EdgeData]] = defaultdict(list)

        # Direct usages of the node itself
        for edge in self.incoming[node_id].get("uses", []):
            grouped[edge.source].append(edge)

        # Member usages (for container types)
        node = self.nodes.get(node_id)
        if node and node.kind in ("Class", "Interface", "Trait", "Enum", "File"):
            def collect_member_edges(parent_id: str):
                for child_id in self.get_contains_children(parent_id):
                    for edge in self.incoming[child_id].get("uses", []):
                        grouped[edge.source].append(edge)
                    collect_member_edges(child_id)

            collect_member_edges(node_id)

        return dict(grouped)

    def get_deps(self, node_id: str, include_members: bool = True) -> list[EdgeData]:
        """Get all outgoing 'uses' edges from a node.

        Args:
            node_id: The node to get dependencies for.
            include_members: If True and node is a container (class/file),
                            also include uses from contained members.
        """
        direct_uses = self.outgoing[node_id].get("uses", [])

        if not include_members:
            return direct_uses

        # For container nodes, also get uses from contained members
        node = self.nodes.get(node_id)
        if node and node.kind in ("Class", "Interface", "Trait", "Enum", "File"):
            all_uses = list(direct_uses)
            seen_targets = {e.target for e in direct_uses}

            # Recursively collect from contained members
            def collect_member_uses(parent_id: str):
                for child_id in self.get_contains_children(parent_id):
                    for edge in self.outgoing[child_id].get("uses", []):
                        if edge.target not in seen_targets:
                            seen_targets.add(edge.target)
                            all_uses.append(edge)
                    # Recurse into nested containers (e.g., methods in class)
                    collect_member_uses(child_id)

            collect_member_uses(node_id)
            return all_uses

        return direct_uses

    def get_contains_children(self, node_id: str) -> list[str]:
        """Get IDs of nodes contained by this node."""
        return [e.target for e in self.outgoing[node_id].get("contains", [])]

    def get_contains_parent(self, node_id: str) -> Optional[str]:
        """Get ID of the containing node."""
        parents = self.incoming[node_id].get("contains", [])
        if parents:
            return parents[0].source
        return None

    def get_extends_parent(self, node_id: str) -> Optional[str]:
        """Get ID of the extended class/interface."""
        parents = self.outgoing[node_id].get("extends", [])
        if parents:
            return parents[0].target
        return None

    def get_extends_children(self, node_id: str) -> list[str]:
        """Get IDs of classes that extend this one."""
        return [e.source for e in self.incoming[node_id].get("extends", [])]

    def get_implements(self, node_id: str) -> list[str]:
        """Get IDs of interfaces implemented by this class."""
        return [e.target for e in self.outgoing[node_id].get("implements", [])]

    def get_implementors(self, node_id: str) -> list[str]:
        """Get IDs of classes that implement this interface."""
        return [e.source for e in self.incoming[node_id].get("implements", [])]

    def get_overrides_parent(self, node_id: str) -> Optional[str]:
        """Get ID of the method this one overrides."""
        overrides = self.outgoing[node_id].get("overrides", [])
        if overrides:
            return overrides[0].target
        return None

    def get_overridden_by(self, node_id: str) -> list[str]:
        """Get IDs of methods that override this one."""
        return [e.source for e in self.incoming[node_id].get("overrides", [])]

    # Precomputed query methods (O(1) lookups)

    def get_all_ancestors(self, node_id: str) -> list[str]:
        """Get all ancestors of a class (O(1) if precomputed)."""
        if self.precomputed:
            return self.precomputed.get_ancestors(node_id)
        # Fall back to iterative lookup
        ancestors = []
        current = node_id
        while True:
            parent = self.get_extends_parent(current)
            if not parent:
                break
            ancestors.append(parent)
            current = parent
        return ancestors

    def get_all_descendants(self, node_id: str) -> list[str]:
        """Get all descendants of a class (O(1) if precomputed)."""
        if self.precomputed:
            return self.precomputed.get_descendants(node_id)
        # Fall back to BFS
        descendants = []
        to_visit = [node_id]
        visited = {node_id}
        while to_visit:
            current = to_visit.pop(0)
            for child in self.get_extends_children(current):
                if child not in visited:
                    visited.add(child)
                    descendants.append(child)
                    to_visit.append(child)
        return descendants

    def get_all_interfaces(self, node_id: str) -> set[str]:
        """Get all interfaces (including inherited) for a class."""
        if self.precomputed:
            return self.precomputed.get_all_interfaces(node_id)
        # Fall back to iterative lookup
        interfaces = set(self.get_implements(node_id))
        for ancestor in self.get_all_ancestors(node_id):
            interfaces.update(self.get_implements(ancestor))
        return interfaces

    def get_override_root(self, method_id: str) -> str:
        """Get the original definition of an overridden method."""
        if self.precomputed:
            return self.precomputed.get_override_root(method_id)
        # Fall back to iterative lookup
        current = method_id
        while True:
            parent = self.get_overrides_parent(current)
            if not parent:
                return current
            current = parent

    def get_containment_path(self, node_id: str) -> list[str]:
        """Get the full containment path from root to node."""
        if self.precomputed:
            return self.precomputed.get_containment_path(node_id)
        # Fall back to iterative lookup
        path = [node_id]
        current = node_id
        while True:
            parent = self.get_contains_parent(current)
            if not parent:
                break
            path.append(parent)
            current = parent
        return list(reversed(path))

    # =========================================================================
    # v2.0 Edge Query Methods (Value/Call graph traversal)
    # =========================================================================

    def get_receiver(self, call_node_id: str) -> Optional[str]:
        """Get the receiver Value node ID for a Call node."""
        edges = self.outgoing[call_node_id].get("receiver", [])
        if edges:
            return edges[0].target
        return None

    def get_call_target(self, call_node_id: str) -> Optional[str]:
        """Get the target (callee) node ID for a Call node."""
        edges = self.outgoing[call_node_id].get("calls", [])
        if edges:
            return edges[0].target
        return None

    def get_produces(self, call_node_id: str) -> Optional[str]:
        """Get the result Value node ID produced by a Call node."""
        edges = self.outgoing[call_node_id].get("produces", [])
        if edges:
            return edges[0].target
        return None

    def get_source_call(self, value_node_id: str) -> Optional[str]:
        """Get the Call node ID that produced this Value node (via produces edge)."""
        edges = self.incoming[value_node_id].get("produces", [])
        if edges:
            return edges[0].source
        return None

    def get_assigned_from(self, value_node_id: str) -> Optional[str]:
        """Get the source Value node ID for a value assignment."""
        edges = self.outgoing[value_node_id].get("assigned_from", [])
        if edges:
            return edges[0].target
        return None

    def get_type_of(self, value_node_id: str) -> Optional[str]:
        """Get the type (Class/Interface) node ID for a Value node."""
        edges = self.outgoing[value_node_id].get("type_of", [])
        if edges:
            return edges[0].target
        return None

    def get_type_of_all(self, value_node_id: str) -> list[str]:
        """Get all type (Class/Interface) node IDs for a Value node (supports union types)."""
        return [e.target for e in self.outgoing[value_node_id].get("type_of", [])]

    def get_calls_to(self, target_node_id: str) -> list[str]:
        """Get all Call node IDs that call a given Method/Property/Class."""
        return [e.source for e in self.incoming[target_node_id].get("calls", [])]

    def resolve_file_to_class(self, file_node_id: str) -> Optional[str]:
        """Resolve a File node to its primary contained Class/Interface/Trait/Enum (R6).

        Resolution rules:
        - Single class -> use that class
        - Multiple classes -> use the one whose name matches the filename (PSR-4)
        - No class (script file) -> return None (caller keeps file path)

        Args:
            file_node_id: ID of the File node.

        Returns:
            Node ID of the primary class, or None if no class found.
        """
        file_node = self.nodes.get(file_node_id)
        if not file_node or file_node.kind != "File":
            return None

        children = self.get_contains_children(file_node_id)
        class_children = []
        for child_id in children:
            child_node = self.nodes.get(child_id)
            if child_node and child_node.kind in ("Class", "Interface", "Trait", "Enum"):
                class_children.append(child_id)

        if not class_children:
            return None

        if len(class_children) == 1:
            return class_children[0]

        # Multiple classes: find the one matching the filename (PSR-4 convention)
        if file_node.file:
            import os
            filename = os.path.splitext(os.path.basename(file_node.file))[0]
            for child_id in class_children:
                child_node = self.nodes.get(child_id)
                if child_node and child_node.name == filename:
                    return child_id

        # Fallback: return the first class
        return class_children[0]

    def get_arguments(self, call_node_id: str) -> list[tuple[str, int, Optional[str]]]:
        """Get argument Value node IDs with their positions for a Call node.

        Returns:
            List of (value_node_id, position, expression) tuples sorted by position.
        """
        edges = self.outgoing[call_node_id].get("argument", [])
        args = [(e.target, e.position or 0, e.expression) for e in edges]
        return sorted(args, key=lambda x: x[1])
