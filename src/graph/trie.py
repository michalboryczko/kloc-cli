"""Symbol trie for fast prefix/suffix/substring search."""

from typing import Optional
from collections import defaultdict


class TrieNode:
    """Node in a trie data structure."""

    __slots__ = ("children", "node_ids", "is_end")

    def __init__(self):
        self.children: dict[str, "TrieNode"] = {}
        self.node_ids: list[str] = []
        self.is_end: bool = False


class SymbolTrie:
    """Trie for fast symbol search by prefix, suffix, or substring.

    Maintains both forward and reverse tries for efficient lookups.
    """

    def __init__(self):
        self.forward_trie = TrieNode()
        self.reverse_trie = TrieNode()
        # For substring search, we also maintain a token index
        self.token_to_ids: dict[str, set[str]] = defaultdict(set)

    def add(self, fqn: str, node_id: str):
        """Add a symbol to the trie.

        Args:
            fqn: Fully qualified name (e.g., "App\\Entity\\User")
            node_id: Node ID to associate with this symbol
        """
        # Normalize: lowercase for case-insensitive search
        fqn_lower = fqn.lower()

        # Add to forward trie
        self._insert(self.forward_trie, fqn_lower, node_id)

        # Add to reverse trie (reversed string)
        self._insert(self.reverse_trie, fqn_lower[::-1], node_id)

        # Extract tokens for substring search
        tokens = self._tokenize(fqn_lower)
        for token in tokens:
            self.token_to_ids[token].add(node_id)

    def _insert(self, root: TrieNode, key: str, node_id: str):
        """Insert a key into a trie."""
        node = root
        for char in key:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end = True
        if node_id not in node.node_ids:
            node.node_ids.append(node_id)

    def _tokenize(self, fqn: str) -> list[str]:
        """Extract searchable tokens from an FQN.

        Splits on namespace separators and extracts meaningful parts.
        """
        tokens = []

        # Split by common separators
        parts = fqn.replace("::", "\\").split("\\")

        for part in parts:
            if part:
                tokens.append(part)
                # Also add camelCase/snake_case splits
                sub_tokens = self._split_identifier(part)
                tokens.extend(sub_tokens)

        return list(set(tokens))

    def _split_identifier(self, identifier: str) -> list[str]:
        """Split an identifier by camelCase or snake_case."""
        tokens = []

        # Split by underscore first
        parts = identifier.split("_")
        for part in parts:
            if not part:
                continue

            # Split camelCase
            current = ""
            for i, char in enumerate(part):
                if char.isupper() and current:
                    tokens.append(current.lower())
                    current = char
                else:
                    current += char
            if current:
                tokens.append(current.lower())

        return [t for t in tokens if len(t) > 1]  # Skip single chars

    def search_prefix(self, prefix: str, limit: int = 100) -> list[str]:
        """Find all symbols starting with the given prefix.

        Args:
            prefix: Prefix to search for
            limit: Maximum number of results

        Returns:
            List of matching node IDs
        """
        prefix_lower = prefix.lower()
        return self._search_trie(self.forward_trie, prefix_lower, limit)

    def search_suffix(self, suffix: str, limit: int = 100) -> list[str]:
        """Find all symbols ending with the given suffix.

        Args:
            suffix: Suffix to search for
            limit: Maximum number of results

        Returns:
            List of matching node IDs
        """
        suffix_lower = suffix.lower()
        # Search reverse trie with reversed suffix
        return self._search_trie(self.reverse_trie, suffix_lower[::-1], limit)

    def search_contains(self, substring: str, limit: int = 100) -> list[str]:
        """Find all symbols containing the given substring.

        Args:
            substring: Substring to search for
            limit: Maximum number of results

        Returns:
            List of matching node IDs
        """
        substring_lower = substring.lower()

        # First try exact token match
        if substring_lower in self.token_to_ids:
            results = list(self.token_to_ids[substring_lower])[:limit]
            if results:
                return results

        # Fall back to prefix search on tokens
        matching_ids: set[str] = set()
        for token, ids in self.token_to_ids.items():
            if substring_lower in token:
                matching_ids.update(ids)
                if len(matching_ids) >= limit:
                    break

        return list(matching_ids)[:limit]

    def _search_trie(self, root: TrieNode, prefix: str, limit: int) -> list[str]:
        """Search a trie for all nodes with the given prefix."""
        # Navigate to prefix node
        node = root
        for char in prefix:
            if char not in node.children:
                return []
            node = node.children[char]

        # Collect all node IDs under this prefix
        results: list[str] = []
        self._collect_ids(node, results, limit)
        return results

    def _collect_ids(self, node: TrieNode, results: list[str], limit: int):
        """Recursively collect all node IDs under a trie node."""
        if len(results) >= limit:
            return

        for node_id in node.node_ids:
            if len(results) >= limit:
                return
            if node_id not in results:
                results.append(node_id)

        for child in node.children.values():
            if len(results) >= limit:
                return
            self._collect_ids(child, results, limit)


def build_symbol_trie(
    nodes: dict[str, "NodeData"],
    skip_kinds: frozenset[str] | None = None,
) -> SymbolTrie:
    """Build a symbol trie from a dictionary of nodes.

    Args:
        nodes: Dictionary mapping node IDs to NodeData
        skip_kinds: Node kinds to exclude (e.g. internal Call/Value/Argument nodes)

    Returns:
        Populated SymbolTrie
    """
    trie = SymbolTrie()

    for node_id, node in nodes.items():
        if skip_kinds and node.kind in skip_kinds:
            continue
        # Add FQN
        trie.add(node.fqn, node_id)
        # Also add short name for quick lookups
        trie.add(node.name, node_id)

    return trie
