# kloc-cli

CLI and MCP server for querying PHP code structure from KLOC Source-of-Truth (SoT) JSON files. Resolve symbols, find usages, explore dependencies, trace inheritance, and get bidirectional context with access chains.

## Pipeline Position

```
PHP code -> scip-php -> index.json -> kloc-mapper -> sot.json -> kloc-cli -> output
```

kloc-cli is the query layer. It reads `sot.json` (produced by kloc-mapper) and answers structural questions about PHP codebases.

## Installation

```bash
cd kloc-cli
uv venv && source .venv/bin/activate
uv pip install -e .
```

Requires Python 3.11+. Uses [uv](https://github.com/astral-sh/uv) for package management.

## Commands

All commands require `--sot` pointing to a SoT JSON file. All support `--json` for machine-readable output.

### resolve

Resolve a symbol to its definition location. Supports FQN, partial match, and method syntax.

```bash
kloc-cli resolve "App\\Entity\\User" --sot sot.json
kloc-cli resolve User --sot sot.json            # partial match
kloc-cli resolve "User::getId" --sot sot.json    # method syntax
```

### usages

Find all usages of a symbol with BFS depth expansion.

```bash
kloc-cli usages "App\\Entity\\User" --sot sot.json
kloc-cli usages User --sot sot.json --depth 2 --limit 50
```

### deps

Find all dependencies of a symbol with BFS depth expansion.

```bash
kloc-cli deps "App\\Service\\UserService" --sot sot.json
kloc-cli deps UserService --sot sot.json --depth 2
```

### context

Bidirectional analysis: what uses the symbol AND what it uses. The most powerful query.

```bash
kloc-cli context UserService --sot sot.json
kloc-cli context UserService --sot sot.json --depth 2 --limit 100
kloc-cli context UserServiceInterface --sot sot.json --impl    # polymorphic analysis
kloc-cli context User --sot sot.json --direct                  # direct refs only
kloc-cli context User --sot sot.json --with-imports            # include PHP use statements
```

Flags:
- `--impl` - Polymorphic analysis: in USES direction, shows implementations/overrides; in USED BY direction, includes callers of interface methods that concrete methods implement.
- `--direct` - Show only direct references (extends, implements, type hints), excluding member usages.
- `--with-imports` - Include PHP import/use statements in USED BY output (hidden by default).

With sot.json v2.0, context output includes reference types (method_call, type_hint, instantiation, etc.) and access chains showing how symbols are accessed.

### owners

Show structural containment chain (method -> class -> file).

```bash
kloc-cli owners "App\\Entity\\User::getId" --sot sot.json
```

### inherit

Show inheritance chain for a class.

```bash
kloc-cli inherit User --sot sot.json --direction up     # ancestors
kloc-cli inherit User --sot sot.json --direction down   # descendants
```

### overrides

Show override chain for a method.

```bash
kloc-cli overrides "User::getName" --sot sot.json --direction up    # original definition
kloc-cli overrides "User::getName" --sot sot.json --direction down  # all overriding methods
```

### mcp-server

Start the MCP server for AI assistant integration (stdio transport).

```bash
# Single project
kloc-cli mcp-server --sot sot.json

# Multi-project
kloc-cli mcp-server --config kloc.json
```

## MCP Server

The MCP server exposes all query commands as tools via the Model Context Protocol.

### Claude MCP Configuration

```json
{
  "mcpServers": {
    "kloc": {
      "command": "kloc-cli",
      "args": ["mcp-server", "--sot", "/path/to/sot.json"]
    }
  }
}
```

### Multi-Project Config

Create a `kloc.json` file:

```json
{
  "projects": [
    {"name": "my-app", "sot": "/path/to/my-app-sot.json"},
    {"name": "payments", "sot": "/path/to/payments-sot.json"}
  ]
}
```

Then configure Claude:

```json
{
  "mcpServers": {
    "kloc": {
      "command": "kloc-cli",
      "args": ["mcp-server", "--config", "/path/to/kloc.json"]
    }
  }
}
```

### MCP Tools

| Tool | Description |
|------|-------------|
| `kloc_projects` | List available projects |
| `kloc_resolve` | Resolve symbol to definition location |
| `kloc_usages` | Find usages with depth expansion |
| `kloc_deps` | Find dependencies with depth expansion |
| `kloc_context` | Bidirectional context (usages + deps + access chains) |
| `kloc_owners` | Structural containment chain |
| `kloc_inherit` | Inheritance tree |
| `kloc_overrides` | Method override tree |

All tools accept an optional `project` parameter when multiple projects are configured.

## Architecture

```
src/
├── cli.py                  # Typer CLI entry point (7 commands + mcp-server)
├── models/
│   ├── node.py             # NodeData (structural + runtime nodes)
│   ├── edge.py             # EdgeData (with position data)
│   ├── results.py          # Query result types
│   └── output.py           # ContextOutput class hierarchy
├── graph/
│   ├── index.py            # SoTIndex (main data structure)
│   ├── loader.py           # JSON loading via msgspec
│   ├── precompute.py       # Transitive closures for inheritance/overrides
│   └── trie.py             # Symbol prefix trie for fast partial matching
├── queries/                # 18 modules (decomposed from monolithic context.py)
│   ├── base.py             # Query base class
│   ├── resolve.py          # Symbol resolution
│   ├── usages.py           # Find usages (BFS)
│   ├── deps.py             # Find dependencies (BFS)
│   ├── owners.py           # Containment chain
│   ├── inherit.py          # Inheritance chain
│   ├── overrides.py        # Override chain
│   ├── context.py          # Context orchestrator (695 lines)
│   ├── graph_utils.py      # Graph traversal utilities
│   ├── reference_types.py  # Reference type inference
│   ├── definition.py       # Definition detail extraction
│   ├── method_context.py   # Method-specific context building
│   ├── polymorphic.py      # Polymorphic (interface/override) analysis
│   ├── value_context.py    # Value node context (params, locals, results)
│   ├── property_context.py # Property-specific context building
│   ├── class_context.py    # Class-specific context building
│   ├── interface_context.py# Interface-specific context building
│   └── used_by_handlers.py # Strategy pattern for USED BY dispatch
├── output/
│   ├── json_formatter.py   # JSON output
│   ├── tree.py             # Tree output formatters
│   └── console.py          # Rich console output
└── server/
    └── mcp.py              # MCP server (JSON-RPC 2.0 over stdio)
```

The `context` command is the most complex query. It was decomposed from a 6,595-line monolith into 11 focused modules organized in layers:

- **Layer 0**: `graph_utils.py`, `reference_types.py` (shared utilities)
- **Layer 1**: `definition.py` (definition detail extraction)
- **Layer 2**: `method_context.py`, `polymorphic.py`, `value_context.py` (core builders)
- **Layer 3**: `property_context.py`, `class_context.py`, `interface_context.py` (type-specific)
- **Orchestrator**: `context.py` (routes to appropriate builder by node kind)
- **Strategy**: `used_by_handlers.py` (dispatches USED BY processing by reference type)
- **Output Model**: `models/output.py` (ContextOutput class hierarchy)

## Development

```bash
uv pip install -e ".[dev]"
uv run pytest tests/ -v
uv run ruff check src/
uv run ruff format src/
```

## License

MIT
