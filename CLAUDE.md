# kloc-cli Development Guide

## Overview

CLI and MCP server for querying KLOC Source-of-Truth (sot.json) graphs. Resolves PHP symbols, traces usages/dependencies, builds bidirectional context with data-flow awareness.

## Development Setup

Python >=3.11. Uses `uv` as the package manager. Virtual environment at `.venv/`.

```bash
# Install dependencies (including dev)
uv pip install -e ".[dev]"

# Run the CLI during development
uv run kloc-cli --help
```

Dependencies: typer, rich, msgspec.

## CLI Commands

```bash
# Resolve a symbol
uv run kloc-cli resolve "App\Entity\User" --sot artifacts/sot.json

# Find usages of a symbol (with depth)
uv run kloc-cli usages "App\Entity\User" --sot artifacts/sot.json --depth 2

# Find dependencies
uv run kloc-cli deps "App\Entity\User::getId()" --sot artifacts/sot.json

# Bidirectional context (usages + deps + data flow)
uv run kloc-cli context "App\Entity\User" --sot artifacts/sot.json --depth 2

# Containment chain (method -> class -> file)
uv run kloc-cli owners "App\Entity\User::getId()" --sot artifacts/sot.json

# Inheritance tree
uv run kloc-cli inherit "App\Entity\User" --sot artifacts/sot.json --direction up

# Method overrides
uv run kloc-cli overrides "App\Entity\User::getId()" --sot artifacts/sot.json

# Start MCP server (single project)
uv run kloc-cli mcp-server --sot artifacts/sot.json

# Start MCP server (multi-project)
uv run kloc-cli mcp-server --config kloc.json
```

All query commands accept `--json / -j` for JSON output instead of rich console formatting.

## Testing

```bash
# Run all tests (258 passed, 22 skipped as of last run)
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_index.py -v
```

### Test files

| File | Type | What it tests |
|------|------|---------------|
| `test_index.py` | Unit | SoTIndex class: node/edge loading, symbol resolution (exact, partial, case-insensitive), usage/dependency queries, containment, inheritance lookups |
| `test_reference_type.py` | Unit | Reference type inference logic |
| `test_callee_verification.py` | Unit | Callee resolution and verification |
| `test_output_model.py` | Unit | ContextOutput model: from_result conversion, JSON serialization, contract compliance |
| `test_usage_flow.py` | Integration | Unified graph format (v2.0) using kloc-reference-project-php fixtures: reference type inference, access chains, graph-based call tracking |
| `test_integration.py` | Integration | Full CLI queries against real SoT artifact (requires `artifacts/sot_fixed.json`, skipped if missing) |

## Architecture

### Source layout

```
src/
  cli.py              # Typer CLI entry point
  commands/            # CLI command registration
  graph/               # Graph data layer
    index.py           # SoTIndex: in-memory graph with lookup tables
    loader.py          # SoT JSON loading
    precompute.py      # Precomputed graph traversals
    trie.py            # Prefix trie for symbol search
  models/              # Data models
    node.py            # NodeData
    edge.py            # EdgeData
    results.py         # ContextResult, ContextEntry, etc.
    output.py          # ContextOutput hierarchy (contract-compliant JSON output)
  output/              # Output formatting
    console.py         # Rich console output
    json_formatter.py  # JSON output
    tree.py            # Tree rendering
  queries/             # Query engine (18 modules)
    context.py         # Orchestrator (695 lines) — dispatches to specialized modules
    graph_utils.py     # Shared graph traversal utilities
    reference_types.py # Reference type inference (method_call, type_hint, instantiation)
    definition.py      # Symbol definition extraction
    method_context.py  # Method-level context building
    polymorphic.py     # Polymorphic dispatch / interface resolution
    value_context.py   # Value/data-flow context (assigned_from, produces)
    property_context.py # Property access context
    class_context.py   # Class-level context aggregation (largest module: 1567 lines)
    interface_context.py # Interface-level context
    used_by_handlers.py # "Used by" relationship handlers
    base.py            # Base query class
    resolve.py         # Symbol resolution query
    usages.py          # Usage tracking query
    deps.py            # Dependency query
    inherit.py         # Inheritance tree query
    overrides.py       # Method override query
    owners.py          # Containment chain query
  server/
    mcp.py             # MCP server (Model Context Protocol)
```

### Key refactoring note

The `context.py` orchestrator was decomposed from 6,595 lines to 695 lines. The logic was extracted into 11 specialized modules: `graph_utils`, `reference_types`, `definition`, `method_context`, `polymorphic`, `value_context`, `property_context`, `class_context`, `interface_context`, `used_by_handlers`, and the orchestrator itself. Total query code is ~7,800 lines across all modules.

### Output model

`models/output.py` defines `ContextOutput`, the contract-compliant intermediate representation between internal query results and JSON output. It mirrors the JSON schema in `kloc-contracts/kloc-cli-context.json`. All line numbers are converted to 1-based at construction time.

### SoT format compatibility

The CLI works with both v1.0 and v2.0 sot.json formats:

- **v1.0**: Basic nodes (Class, Method, Property, etc.) with uses/contains/extends/implements edges
- **v2.0**: Adds Value and Call nodes with additional edges (calls, receiver, argument, produces, assigned_from, type_of)

With v2.0, the `context` command provides data-flow-aware results: accurate reference types, access chains, and value tracking.

## Building

Standalone binary via PyInstaller:

```bash
# Build for current platform (macOS native, Linux via Docker)
./build.sh

# Test the binary
./dist/kloc-cli --help
```

### Force Linux build via Docker

```bash
docker build -t kloc-cli-builder-linux -f - . <<'EOF'
FROM python:3.12-slim
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends binutils && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY pyproject.toml build_entry.py ./
COPY src/ ./src/
RUN uv pip install --system -e . && uv pip install --system pyinstaller
RUN pyinstaller --onefile --name kloc-cli --collect-all src --collect-all rich --clean build_entry.py
EOF

docker create --name kloc-cli-build kloc-cli-builder-linux
docker cp kloc-cli-build:/build/dist/kloc-cli ./dist/kloc-cli
docker rm kloc-cli-build
```
