# kloc-cli Development Guide

## Development Setup

This project uses `uv` as the package manager. The virtual environment is at `.venv/`.

```bash
# Install dependencies (including dev)
uv pip install -e ".[dev]"

# Run the CLI during development
uv run kloc-cli --help

# Or run directly via python module
.venv/bin/python -m src.cli --help
```

### CLI Usage

```bash
# Resolve a symbol
uv run kloc-cli resolve "App\Entity\User" --sot artifacts/sot.json

# Find usages of a symbol (with depth)
uv run kloc-cli usages "App\Entity\User" --sot artifacts/sot.json --depth 2

# Find dependencies
uv run kloc-cli deps "App\Entity\User::getId()" --sot artifacts/sot.json

# Bidirectional context (usages + deps)
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
# Run all tests
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_index.py -v
uv run pytest tests/test_integration.py -v
```

### Test structure

- `tests/test_index.py` — **Unit tests** for SoTIndex class. Uses in-memory fixtures. Tests node/edge loading, symbol resolution (exact, partial, case-insensitive), usage/dependency queries, containment, and inheritance lookups.
- `tests/test_integration.py` — **Integration tests** using a real SoT artifact. Requires `artifacts/sot_fixed.json` (skipped if missing). Tests resolve, usages, deps, containment, inheritance, overrides, range validation, deduplication, and parameter self-reference filtering.
- `tests/test_usage_flow.py` — **Integration tests** for unified graph format (v2.0). Uses `kloc-reference-project-php` test fixtures. Tests reference type inference (type_hint, method_call, instantiation), access chain building, and graph-based call tracking.
- `tests/test_reference_type.py` — **Unit tests** for reference type inference logic.

### Unified Graph Format (sot.json v2.0)

The CLI works with both v1.0 and v2.0 sot.json formats:

- **v1.0**: Basic nodes (Class, Method, Property, etc.) with uses/contains/extends/implements edges
- **v2.0**: Adds Value and Call nodes with additional edges (calls, receiver, argument, produces, assigned_from, type_of)

With v2.0 format, the `context` command provides enhanced information:
- Accurate reference types (method_call vs type_hint vs instantiation)
- Access chains showing how a method is accessed (e.g., `$this->repository->save()`)

## Building

The project builds a standalone binary using PyInstaller via `build.sh`.

```bash
# Build for current platform (macOS builds natively, Linux uses Docker)
./build.sh

# Test the binary
./dist/kloc-cli --help
```

### Force Linux build via Docker

On macOS, you can force a Linux binary build by running the Docker build directly:

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

The output binary is at `./dist/kloc-cli`.
