"""MCP (Model Context Protocol) server adapter for kloc-cli.

This module provides an MCP server that exposes kloc-cli functionality
to AI assistants and other MCP clients.

Usage:
    python -m src.server.mcp --sot /path/to/sot.json

Or via the CLI:
    kloc-cli mcp-server --sot /path/to/sot.json
"""

import argparse
import json
import sys
from typing import Any, Optional

from ..graph import SoTIndex
from ..queries import (
    ResolveQuery,
    UsagesQuery,
    DepsQuery,
    ContextQuery,
    OwnersQuery,
    InheritQuery,
    OverridesQuery,
)


def _count_tree_nodes(entries: list) -> int:
    """Count total nodes in a tree structure."""
    total = 0
    for entry in entries:
        total += 1
        if entry.children:
            total += _count_tree_nodes(entry.children)
    return total


class MCPServer:
    """MCP server for kloc-cli.

    Provides tools for querying code structure via MCP protocol.
    """

    def __init__(self, sot_path: str):
        """Initialize the MCP server.

        Args:
            sot_path: Path to the SoT JSON file.
        """
        self.index = SoTIndex(sot_path)

    def get_tools(self) -> list[dict]:
        """Return list of available MCP tools."""
        return [
            {
                "name": "kloc_resolve",
                "description": "Resolve a symbol to its definition location. Supports FQN (App\\Entity\\User), partial match (User), or method syntax (User::getId()).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to resolve (FQN, partial, or short name)",
                        },
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_usages",
                "description": "Find all usages of a symbol with depth expansion. Returns tree of usages.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to find usages for",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth for expansion (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 50)",
                            "default": 50,
                        },
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_deps",
                "description": "Find all dependencies of a symbol with depth expansion. Returns tree of dependencies.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to find dependencies for",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth for expansion (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 50)",
                            "default": 50,
                        },
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_context",
                "description": "Get bidirectional context for a symbol: both what uses it and what it uses, with configurable depth. Optionally includes implementations (for classes/interfaces) or overrides (for methods).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to get context for",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth for expansion (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results per direction (default: 50)",
                            "default": 50,
                        },
                        "include_impl": {
                            "type": "boolean",
                            "description": "Include implementations (for classes/interfaces) or overrides (for methods)",
                            "default": False,
                        },
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_owners",
                "description": "Show structural containment chain (e.g., Method -> Class -> File).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to find ownership chain for",
                        },
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_inherit",
                "description": "Show inheritance tree for a class with depth expansion. Returns ancestors or descendants.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Class to show inheritance for",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down"],
                            "description": "Direction: 'up' for ancestors, 'down' for descendants",
                            "default": "up",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth for expansion (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 100)",
                            "default": 100,
                        },
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_overrides",
                "description": "Show override tree for a method with depth expansion.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Method to show override chain for",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down"],
                            "description": "Direction: 'up' for overridden, 'down' for overriding",
                            "default": "up",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth for expansion (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 100)",
                            "default": 100,
                        },
                    },
                    "required": ["symbol"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a tool by name with arguments.

        Args:
            name: Tool name (e.g., "kloc_resolve")
            arguments: Tool arguments

        Returns:
            Tool result as a dictionary
        """
        handlers = {
            "kloc_resolve": self._handle_resolve,
            "kloc_usages": self._handle_usages,
            "kloc_deps": self._handle_deps,
            "kloc_context": self._handle_context,
            "kloc_owners": self._handle_owners,
            "kloc_inherit": self._handle_inherit,
            "kloc_overrides": self._handle_overrides,
        }

        handler = handlers.get(name)
        if not handler:
            return {"error": f"Unknown tool: {name}"}

        try:
            return handler(arguments)
        except Exception as e:
            return {"error": str(e)}

    def _resolve_symbol(self, symbol: str) -> tuple[Optional[Any], Optional[dict]]:
        """Resolve symbol and return (node, error_dict)."""
        query = ResolveQuery(self.index)
        result = query.execute(symbol)

        if not result.found:
            return None, {"error": "not_found", "message": f"Symbol not found: {symbol}"}

        if not result.unique:
            return None, {
                "error": "ambiguous",
                "message": f"Found {len(result.candidates)} candidates",
                "candidates": [
                    {"id": c.id, "kind": c.kind, "fqn": c.fqn}
                    for c in result.candidates
                ],
            }

        return result.candidates[0], None

    def _handle_resolve(self, args: dict) -> dict:
        """Handle kloc_resolve tool call."""
        symbol = args.get("symbol")
        if not symbol:
            return {"error": "Missing required parameter: symbol"}

        node, error = self._resolve_symbol(symbol)
        if error:
            return error

        result = {
            "id": node.id,
            "kind": node.kind,
            "name": node.name,
            "fqn": node.fqn,
            "file": node.file,
            "line": node.start_line + 1 if node.start_line is not None else None,
        }
        # Include signature for methods/functions
        if node.signature:
            result["signature"] = node.signature
        return result

    def _handle_usages(self, args: dict) -> dict:
        """Handle kloc_usages tool call."""
        symbol = args.get("symbol")
        depth = args.get("depth", 1)
        limit = args.get("limit", 50)

        if not symbol:
            return {"error": "Missing required parameter: symbol"}

        node, error = self._resolve_symbol(symbol)
        if error:
            return error

        query = UsagesQuery(self.index)
        result = query.execute(node.id, depth=depth, limit=limit)

        def entry_to_dict(entry):
            return {
                "depth": entry.depth,
                "fqn": entry.fqn,
                "file": entry.file,
                "line": entry.line + 1 if entry.line is not None else None,
                "children": [entry_to_dict(c) for c in entry.children],
            }

        return {
            "target": {"fqn": node.fqn, "file": node.file},
            "max_depth": result.max_depth,
            "total": _count_tree_nodes(result.tree),
            "tree": [entry_to_dict(e) for e in result.tree],
        }

    def _handle_deps(self, args: dict) -> dict:
        """Handle kloc_deps tool call."""
        symbol = args.get("symbol")
        depth = args.get("depth", 1)
        limit = args.get("limit", 50)

        if not symbol:
            return {"error": "Missing required parameter: symbol"}

        node, error = self._resolve_symbol(symbol)
        if error:
            return error

        query = DepsQuery(self.index)
        result = query.execute(node.id, depth=depth, limit=limit)

        def entry_to_dict(entry):
            return {
                "depth": entry.depth,
                "fqn": entry.fqn,
                "file": entry.file,
                "line": entry.line + 1 if entry.line is not None else None,
                "children": [entry_to_dict(c) for c in entry.children],
            }

        return {
            "target": {"fqn": node.fqn, "file": node.file},
            "max_depth": result.max_depth,
            "total": _count_tree_nodes(result.tree),
            "tree": [entry_to_dict(e) for e in result.tree],
        }

    def _handle_context(self, args: dict) -> dict:
        """Handle kloc_context tool call."""
        symbol = args.get("symbol")
        depth = args.get("depth", 1)
        limit = args.get("limit", 50)
        include_impl = args.get("include_impl", False)

        if not symbol:
            return {"error": "Missing required parameter: symbol"}

        node, error = self._resolve_symbol(symbol)
        if error:
            return error

        query = ContextQuery(self.index)
        result = query.execute(node.id, depth=depth, limit=limit, include_impl=include_impl)

        def context_entry_to_dict(entry):
            d = {
                "depth": entry.depth,
                "fqn": entry.fqn,
                "kind": entry.kind,
                "file": entry.file,
                "line": entry.line + 1 if entry.line is not None else None,
                "children": [context_entry_to_dict(c) for c in entry.children],
            }
            # Include signature if present (for methods/functions)
            if entry.signature:
                d["signature"] = entry.signature
            # Include implementations if present (for interfaces/methods)
            if entry.implementations:
                d["implementations"] = [context_entry_to_dict(impl) for impl in entry.implementations]
            return d

        target_dict = {
            "fqn": result.target.fqn,
            "file": result.target.file,
            "line": (
                result.target.start_line + 1
                if result.target.start_line is not None
                else None
            ),
        }
        # Include signature for methods/functions
        if result.target.signature:
            target_dict["signature"] = result.target.signature

        return {
            "target": target_dict,
            "max_depth": result.max_depth,
            "used_by": [context_entry_to_dict(e) for e in result.used_by],
            "uses": [context_entry_to_dict(e) for e in result.uses],
        }

    def _handle_owners(self, args: dict) -> dict:
        """Handle kloc_owners tool call."""
        symbol = args.get("symbol")

        if not symbol:
            return {"error": "Missing required parameter: symbol"}

        node, error = self._resolve_symbol(symbol)
        if error:
            return error

        query = OwnersQuery(self.index)
        result = query.execute(node.id)

        return {
            "chain": [
                {"kind": n.kind, "fqn": n.fqn, "file": n.file}
                for n in result.chain
            ],
        }

    def _handle_inherit(self, args: dict) -> dict:
        """Handle kloc_inherit tool call."""
        symbol = args.get("symbol")
        direction = args.get("direction", "up")
        depth = args.get("depth", 1)
        limit = args.get("limit", 100)

        if not symbol:
            return {"error": "Missing required parameter: symbol"}

        node, error = self._resolve_symbol(symbol)
        if error:
            return error

        if node.kind not in ("Class", "Interface", "Trait", "Enum"):
            return {"error": f"Symbol must be a class/interface, got: {node.kind}"}

        query = InheritQuery(self.index)
        result = query.execute(node.id, direction=direction, depth=depth, limit=limit)

        def entry_to_dict(entry):
            return {
                "depth": entry.depth,
                "kind": entry.kind,
                "fqn": entry.fqn,
                "file": entry.file,
                "line": entry.line + 1 if entry.line is not None else None,
                "children": [entry_to_dict(c) for c in entry.children],
            }

        return {
            "root": {"fqn": node.fqn, "file": node.file},
            "direction": result.direction,
            "max_depth": result.max_depth,
            "total": _count_tree_nodes(result.tree),
            "tree": [entry_to_dict(e) for e in result.tree],
        }

    def _handle_overrides(self, args: dict) -> dict:
        """Handle kloc_overrides tool call."""
        symbol = args.get("symbol")
        direction = args.get("direction", "up")
        depth = args.get("depth", 1)
        limit = args.get("limit", 100)

        if not symbol:
            return {"error": "Missing required parameter: symbol"}

        node, error = self._resolve_symbol(symbol)
        if error:
            return error

        if node.kind != "Method":
            return {"error": f"Symbol must be a method, got: {node.kind}"}

        query = OverridesQuery(self.index)
        result = query.execute(node.id, direction=direction, depth=depth, limit=limit)

        def entry_to_dict(entry):
            return {
                "depth": entry.depth,
                "fqn": entry.fqn,
                "file": entry.file,
                "line": entry.line + 1 if entry.line is not None else None,
                "children": [entry_to_dict(c) for c in entry.children],
            }

        return {
            "root": {"fqn": node.fqn, "file": node.file},
            "direction": result.direction,
            "max_depth": result.max_depth,
            "total": _count_tree_nodes(result.tree),
            "tree": [entry_to_dict(e) for e in result.tree],
        }


def run_mcp_server(sot_path: str):
    """Run the MCP server using stdio.

    This implements a simple JSON-based MCP protocol over stdin/stdout.
    """
    server = MCPServer(sot_path)

    # Print server info to stderr (for debugging)
    print(f"KLOC MCP Server started with {sot_path}", file=sys.stderr)
    print(f"Loaded {len(server.index.nodes)} nodes", file=sys.stderr)

    # Process requests from stdin
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            method = request.get("method", "")

            if method == "list_tools":
                response = {"tools": server.get_tools()}
            elif method == "call_tool":
                tool_name = request.get("name", "")
                arguments = request.get("arguments", {})
                response = server.call_tool(tool_name, arguments)
            else:
                response = {"error": f"Unknown method: {method}"}

            print(json.dumps(response), flush=True)

        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON: {e}"}), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)


def main():
    """Main entry point for MCP server."""
    parser = argparse.ArgumentParser(description="KLOC MCP Server")
    parser.add_argument(
        "--sot", "-s", required=True, help="Path to SoT JSON file"
    )
    args = parser.parse_args()

    run_mcp_server(args.sot)


if __name__ == "__main__":
    main()
