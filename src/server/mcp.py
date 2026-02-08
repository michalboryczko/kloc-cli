"""MCP (Model Context Protocol) server for kloc-cli.

Implements JSON-RPC 2.0 based MCP protocol for Claude and other MCP clients.

Usage:
    kloc-cli mcp-server --sot /path/to/sot.json
    kloc-cli mcp-server --config /path/to/kloc.json

Config file format:
    {
        "projects": [
            {"name": "my-app", "sot": "/path/to/my-app-sot.json"},
            {"name": "payments", "sot": "/path/to/payments-sot.json"}
        ]
    }
"""

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
    """MCP server for kloc-cli with multi-project support."""

    def __init__(self, config_path: Optional[str] = None, sot_path: Optional[str] = None):
        """Initialize server with config file or single SoT path.

        Args:
            config_path: Path to JSON config file with multiple projects
            sot_path: Path to single SoT file (creates default project)
        """
        self._projects: dict[str, str] = {}  # name -> sot_path
        self._indexes: dict[str, SoTIndex] = {}  # name -> index (lazy loaded)

        if config_path:
            self._load_config(config_path)
        elif sot_path:
            self._projects["default"] = sot_path
        else:
            raise ValueError("Either config_path or sot_path must be provided")

    def _load_config(self, config_path: str):
        """Load projects from config file."""
        with open(config_path, "r") as f:
            config = json.load(f)

        projects = config.get("projects", [])
        if not projects:
            raise ValueError("Config file must contain at least one project")

        for proj in projects:
            name = proj.get("name")
            sot = proj.get("sot")
            if not name or not sot:
                raise ValueError("Each project must have 'name' and 'sot' fields")
            self._projects[name] = sot

    def _get_index(self, project: Optional[str] = None) -> SoTIndex:
        """Get index for a project (lazy-loaded).

        Args:
            project: Project name. If None, uses default (only if single project).
        """
        if project is None:
            if len(self._projects) == 1:
                project = list(self._projects.keys())[0]
            else:
                raise ValueError(f"Multiple projects configured. Specify 'project' parameter. Available: {list(self._projects.keys())}")

        if project not in self._projects:
            raise ValueError(f"Unknown project: {project}. Available: {list(self._projects.keys())}")

        if project not in self._indexes:
            self._indexes[project] = SoTIndex(self._projects[project])

        return self._indexes[project]

    def get_projects(self) -> list[dict]:
        """Return list of configured projects."""
        return [{"name": name, "sot": path} for name, path in self._projects.items()]

    def get_tools(self) -> list[dict]:
        """Return list of available MCP tools."""
        # Common project property for multi-project support
        project_prop = {"type": "string", "description": "Project name (required if multiple projects configured)"}

        return [
            {
                "name": "kloc_projects",
                "description": "List all configured projects. Use this to discover available projects before querying.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "kloc_resolve",
                "description": "Resolve a symbol to its definition location. Supports FQN, partial match, or method syntax.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Symbol to resolve"},
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_usages",
                "description": "Find all usages of a symbol with depth expansion.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Symbol to find usages for"},
                        "depth": {"type": "integer", "description": "BFS depth (default: 1)", "default": 1},
                        "limit": {"type": "integer", "description": "Max results (default: 50)", "default": 50},
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_deps",
                "description": "Find all dependencies of a symbol with depth expansion.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Symbol to find dependencies for"},
                        "depth": {"type": "integer", "description": "BFS depth (default: 1)", "default": 1},
                        "limit": {"type": "integer", "description": "Max results (default: 50)", "default": 50},
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_context",
                "description": "Get bidirectional context: what uses it and what it uses. With include_impl, shows implementations/overrides.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Symbol to get context for"},
                        "depth": {"type": "integer", "description": "BFS depth (default: 1)", "default": 1},
                        "limit": {"type": "integer", "description": "Max results per direction (default: 50)", "default": 50},
                        "include_impl": {"type": "boolean", "description": "Include implementations/overrides", "default": False},
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_owners",
                "description": "Show structural containment chain (Method -> Class -> File).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Symbol to find ownership for"},
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_inherit",
                "description": "Show inheritance tree for a class.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Class to show inheritance for"},
                        "direction": {"type": "string", "enum": ["up", "down"], "description": "up=ancestors, down=descendants", "default": "up"},
                        "depth": {"type": "integer", "description": "BFS depth (default: 1)", "default": 1},
                        "limit": {"type": "integer", "description": "Max results (default: 100)", "default": 100},
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_overrides",
                "description": "Show override tree for a method.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Method to show overrides for"},
                        "direction": {"type": "string", "enum": ["up", "down"], "description": "up=overridden, down=overriding", "default": "up"},
                        "depth": {"type": "integer", "description": "BFS depth (default: 1)", "default": 1},
                        "limit": {"type": "integer", "description": "Max results (default: 100)", "default": 100},
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict) -> Any:
        """Call a tool by name."""
        handlers = {
            "kloc_projects": self._handle_projects,
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
            raise ValueError(f"Unknown tool: {name}")
        return handler(arguments)

    def _resolve_symbol(self, symbol: str, project: Optional[str] = None):
        """Resolve symbol and return node or raise error."""
        index = self._get_index(project)
        query = ResolveQuery(index)
        result = query.execute(symbol)

        if not result.found:
            raise ValueError(f"Symbol not found: {symbol}")

        if not result.unique:
            candidates = [{"id": c.id, "kind": c.kind, "fqn": c.fqn} for c in result.candidates]
            raise ValueError(f"Ambiguous: {len(result.candidates)} candidates: {candidates}")

        return result.candidates[0]

    def _handle_projects(self, args: dict) -> dict:
        """List all configured projects."""
        return {"projects": self.get_projects()}

    def _handle_resolve(self, args: dict) -> dict:
        project = args.get("project")
        node = self._resolve_symbol(args["symbol"], project)
        result = {"id": node.id, "kind": node.kind, "name": node.name, "fqn": node.fqn, "file": node.file, "line": node.start_line + 1 if node.start_line is not None else None}
        if node.signature:
            result["signature"] = node.signature
        return result

    def _handle_usages(self, args: dict) -> dict:
        project = args.get("project")
        node = self._resolve_symbol(args["symbol"], project)
        index = self._get_index(project)
        query = UsagesQuery(index)
        result = query.execute(node.id, depth=args.get("depth", 1), limit=args.get("limit", 50))

        def entry_to_dict(e):
            return {"depth": e.depth, "fqn": e.fqn, "file": e.file, "line": e.line + 1 if e.line else None, "children": [entry_to_dict(c) for c in e.children]}

        return {"target": {"fqn": node.fqn, "file": node.file}, "total": _count_tree_nodes(result.tree), "tree": [entry_to_dict(e) for e in result.tree]}

    def _handle_deps(self, args: dict) -> dict:
        project = args.get("project")
        node = self._resolve_symbol(args["symbol"], project)
        index = self._get_index(project)
        query = DepsQuery(index)
        result = query.execute(node.id, depth=args.get("depth", 1), limit=args.get("limit", 50))

        def entry_to_dict(e):
            return {"depth": e.depth, "fqn": e.fqn, "file": e.file, "line": e.line + 1 if e.line else None, "children": [entry_to_dict(c) for c in e.children]}

        return {"target": {"fqn": node.fqn, "file": node.file}, "total": _count_tree_nodes(result.tree), "tree": [entry_to_dict(e) for e in result.tree]}

    def _handle_context(self, args: dict) -> dict:
        project = args.get("project")
        node = self._resolve_symbol(args["symbol"], project)
        index = self._get_index(project)
        query = ContextQuery(index)
        result = query.execute(node.id, depth=args.get("depth", 1), limit=args.get("limit", 50), include_impl=args.get("include_impl", False))

        def entry_to_dict(e):
            d = {"depth": e.depth, "fqn": e.fqn, "kind": e.kind, "file": e.file, "line": e.line + 1 if e.line else None, "children": [entry_to_dict(c) for c in e.children]}
            if e.signature:
                d["signature"] = e.signature
            if e.implementations:
                d["implementations"] = [entry_to_dict(i) for i in e.implementations]
            if e.via_interface:
                d["via_interface"] = True
            if e.member_ref:
                mr = {
                    "target_name": e.member_ref.target_name,
                    "target_fqn": e.member_ref.target_fqn,
                    "target_kind": e.member_ref.target_kind,
                    "file": e.member_ref.file,
                    "line": e.member_ref.line + 1 if e.member_ref.line is not None else None,
                }
                if e.member_ref.reference_type:
                    mr["reference_type"] = e.member_ref.reference_type
                if e.member_ref.access_chain:
                    mr["access_chain"] = e.member_ref.access_chain
                if e.member_ref.access_chain_symbol:
                    mr["access_chain_symbol"] = e.member_ref.access_chain_symbol
                d["member_ref"] = mr
            if e.arguments:
                d["arguments"] = [
                    {"position": a.position, "param_name": a.param_name, "value_expr": a.value_expr, "value_source": a.value_source}
                    for a in e.arguments
                ]
            if e.result_var:
                d["result_var"] = e.result_var
            return d

        return {"target": {"fqn": result.target.fqn, "file": result.target.file}, "used_by": [entry_to_dict(e) for e in result.used_by], "uses": [entry_to_dict(e) for e in result.uses]}

    def _handle_owners(self, args: dict) -> dict:
        project = args.get("project")
        node = self._resolve_symbol(args["symbol"], project)
        index = self._get_index(project)
        query = OwnersQuery(index)
        result = query.execute(node.id)
        return {"chain": [{"kind": n.kind, "fqn": n.fqn, "file": n.file} for n in result.chain]}

    def _handle_inherit(self, args: dict) -> dict:
        project = args.get("project")
        node = self._resolve_symbol(args["symbol"], project)
        if node.kind not in ("Class", "Interface", "Trait", "Enum"):
            raise ValueError(f"Symbol must be a class/interface, got: {node.kind}")
        index = self._get_index(project)
        query = InheritQuery(index)
        result = query.execute(node.id, direction=args.get("direction", "up"), depth=args.get("depth", 1), limit=args.get("limit", 100))

        def entry_to_dict(e):
            return {"depth": e.depth, "kind": e.kind, "fqn": e.fqn, "file": e.file, "line": e.line + 1 if e.line else None, "children": [entry_to_dict(c) for c in e.children]}

        return {"root": {"fqn": node.fqn}, "direction": result.direction, "tree": [entry_to_dict(e) for e in result.tree]}

    def _handle_overrides(self, args: dict) -> dict:
        project = args.get("project")
        node = self._resolve_symbol(args["symbol"], project)
        if node.kind != "Method":
            raise ValueError(f"Symbol must be a method, got: {node.kind}")
        index = self._get_index(project)
        query = OverridesQuery(index)
        result = query.execute(node.id, direction=args.get("direction", "up"), depth=args.get("depth", 1), limit=args.get("limit", 100))

        def entry_to_dict(e):
            return {"depth": e.depth, "fqn": e.fqn, "file": e.file, "line": e.line + 1 if e.line else None, "children": [entry_to_dict(c) for c in e.children]}

        return {"root": {"fqn": node.fqn}, "direction": result.direction, "tree": [entry_to_dict(e) for e in result.tree]}


def run_mcp_server(config_path: Optional[str] = None, sot_path: Optional[str] = None):
    """Run the MCP server using stdio with JSON-RPC 2.0 protocol.

    Args:
        config_path: Path to JSON config file with multiple projects
        sot_path: Path to single SoT file (creates default project)
    """
    server = MCPServer(config_path=config_path, sot_path=sot_path)

    def send_response(id: Any, result: Any = None, error: Any = None):
        response = {"jsonrpc": "2.0", "id": id}
        if error is not None:
            response["error"] = {"code": -32000, "message": str(error)}
        else:
            response["result"] = result
        print(json.dumps(response), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            send_response(None, error=f"Parse error: {e}")
            continue

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        try:
            if method == "initialize":
                send_response(req_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "kloc-cli", "version": "0.3.0"}
                })
            elif method == "notifications/initialized":
                pass  # No response needed for notifications
            elif method == "tools/list":
                send_response(req_id, {"tools": server.get_tools()})
            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = server.call_tool(tool_name, arguments)
                send_response(req_id, {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]})
            elif method == "ping":
                send_response(req_id, {})
            else:
                send_response(req_id, error=f"Method not found: {method}")
        except Exception as e:
            send_response(req_id, error=str(e))


def main():
    """Main entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="KLOC MCP Server")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sot", "-s", help="Path to single SoT JSON file")
    group.add_argument("--config", "-c", help="Path to config JSON file with multiple projects")
    args = parser.parse_args()
    run_mcp_server(config_path=args.config, sot_path=args.sot)


if __name__ == "__main__":
    main()
