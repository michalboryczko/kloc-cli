"""Main CLI application."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .graph import SoTIndex
from .queries import (
    ResolveQuery,
    UsagesQuery,
    DepsQuery,
    ContextQuery,
    OwnersQuery,
    InheritQuery,
    OverridesQuery,
)
from .output import (
    print_json,
    print_node,
    print_candidates,
    print_deps_tree,
    print_usages_tree,
    deps_tree_to_dict,
    usages_tree_to_dict,
    print_context_tree,
    context_tree_to_dict,
    print_owners_tree,
    owners_tree_to_dict,
    print_inherit_tree,
    inherit_tree_to_dict,
    print_overrides_tree,
    overrides_tree_to_dict,
)

app = typer.Typer(
    name="kloc-cli",
    help="Query KLOC Source-of-Truth JSON",
    add_completion=False,
)
console = Console()

# Global state for the loaded index
_index: Optional[SoTIndex] = None


def get_index(sot: str) -> SoTIndex:
    """Load or return cached index."""
    global _index
    if _index is None:
        sot_path = Path(sot)
        if not sot_path.exists():
            console.print(f"[red]Error: SoT file not found: {sot}[/red]")
            raise typer.Exit(1)
        _index = SoTIndex(sot_path)
    return _index


# =============================================================================
# MCP Server Command
# =============================================================================


@app.command("mcp-server")
def mcp_server_cmd(
    sot: Optional[Path] = typer.Option(None, "--sot", "-s", help="Path to single SoT JSON file"),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config JSON with multiple projects"),
):
    """Start MCP server for AI assistant integration (stdio).

    Exposes kloc tools via Model Context Protocol for Claude and other AI assistants.

    Single project mode:
        kloc-cli mcp-server --sot /path/to/sot.json

    Multi-project mode (config file):
        kloc-cli mcp-server --config /path/to/kloc.json

    Config file format:
        {
            "projects": [
                {"name": "my-app", "sot": "/path/to/my-app-sot.json"},
                {"name": "payments", "sot": "/path/to/payments-sot.json"}
            ]
        }

    Tools provided:
    - kloc_projects: List available projects
    - kloc_resolve: Resolve symbol to definition
    - kloc_usages: Find usages with depth expansion
    - kloc_deps: Find dependencies with depth expansion
    - kloc_context: Bidirectional context (usages + deps)
    - kloc_owners: Show containment chain
    - kloc_inherit: Show inheritance tree
    - kloc_overrides: Show method override tree
    """
    if not sot and not config:
        console.print("[red]Error: Either --sot or --config is required[/red]", err=True)
        raise typer.Exit(1)

    if sot and config:
        console.print("[red]Error: Cannot use both --sot and --config[/red]", err=True)
        raise typer.Exit(1)

    if sot and not sot.exists():
        console.print(f"[red]Error: SoT file not found: {sot}[/red]", err=True)
        raise typer.Exit(1)

    if config and not config.exists():
        console.print(f"[red]Error: Config file not found: {config}[/red]", err=True)
        raise typer.Exit(1)

    from .server import run_mcp_server
    run_mcp_server(config_path=str(config) if config else None, sot_path=str(sot) if sot else None)


# =============================================================================
# Query Commands
# =============================================================================


@app.command()
def resolve(
    symbol: str = typer.Argument(..., help="Symbol to resolve"),
    sot: str = typer.Option(..., "--sot", "-s", help="Path to SoT JSON"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Resolve a symbol to its definition location."""
    index = get_index(sot)
    query = ResolveQuery(index)
    result = query.execute(symbol)

    if not result.found:
        if json_output:
            print_json({"error": "Symbol not found", "query": symbol})
        else:
            console.print(f"[red]Symbol not found: {symbol}[/red]")
        raise typer.Exit(1)

    if result.unique:
        print_node(result.candidates[0], as_json=json_output)
    else:
        print_candidates(result.candidates, as_json=json_output)


@app.command()
def usages(
    symbol: str = typer.Argument(..., help="Symbol to find usages for"),
    sot: str = typer.Option(..., "--sot", "-s", help="Path to SoT JSON"),
    depth: int = typer.Option(1, "--depth", "-d", help="BFS depth for expansion"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum results"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Find all usages of a symbol with depth expansion."""
    index = get_index(sot)

    resolve_query = ResolveQuery(index)
    resolve_result = resolve_query.execute(symbol)

    if not resolve_result.found:
        if json_output:
            print_json({"error": "Symbol not found", "query": symbol})
        else:
            console.print(f"[red]Symbol not found: {symbol}[/red]")
        raise typer.Exit(1)

    if not resolve_result.unique:
        print_candidates(resolve_result.candidates, as_json=json_output)
        raise typer.Exit(1)

    node = resolve_result.candidates[0]
    usages_query = UsagesQuery(index)
    result = usages_query.execute(node.id, depth=depth, limit=limit)

    if json_output:
        print_json(usages_tree_to_dict(result))
    else:
        console.print(f"[bold]Usages of {node.fqn} (depth={depth}):[/bold]")
        print_usages_tree(result, console)


@app.command()
def deps(
    symbol: str = typer.Argument(..., help="Symbol to find dependencies for"),
    sot: str = typer.Option(..., "--sot", "-s", help="Path to SoT JSON"),
    depth: int = typer.Option(1, "--depth", "-d", help="BFS depth for expansion"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum results"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Find all dependencies of a symbol with depth expansion."""
    index = get_index(sot)

    resolve_query = ResolveQuery(index)
    resolve_result = resolve_query.execute(symbol)

    if not resolve_result.found:
        if json_output:
            print_json({"error": "Symbol not found", "query": symbol})
        else:
            console.print(f"[red]Symbol not found: {symbol}[/red]")
        raise typer.Exit(1)

    if not resolve_result.unique:
        print_candidates(resolve_result.candidates, as_json=json_output)
        raise typer.Exit(1)

    node = resolve_result.candidates[0]
    deps_query = DepsQuery(index)
    result = deps_query.execute(node.id, depth=depth, limit=limit)

    if json_output:
        print_json(deps_tree_to_dict(result))
    else:
        console.print(f"[bold]Dependencies of {node.fqn} (depth={depth}):[/bold]")
        print_deps_tree(result, console)


@app.command()
def context(
    symbol: str = typer.Argument(..., help="Symbol to get context for"),
    sot: str = typer.Option(..., "--sot", "-s", help="Path to SoT JSON"),
    calls: Optional[Path] = typer.Option(None, "--calls", "-c", help="Path to calls.json for access chain display"),
    depth: int = typer.Option(1, "--depth", "-d", help="BFS depth for expansion"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum results per direction"),
    impl: bool = typer.Option(False, "--impl", "-i", help="Include implementations/overrides"),
    direct: bool = typer.Option(False, "--direct", help="Show only direct symbol references (no member usages)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get combined usages and dependencies with depth expansion.

    With --calls flag:
    - Enables access chain display for method calls (e.g., "on: $this->repo")
    - Provides authoritative reference types from call records

    With --impl flag (polymorphic analysis):
    - USES direction: includes implementations of interfaces and overriding methods
    - USED BY direction: includes usages of interface methods that concrete methods implement

    With --direct flag:
    - USED BY direction: shows only direct references to the symbol itself (extends,
      implements, type hints), excluding usages that only reference its members

    Example: When querying a concrete method that implements an interface method,
    --impl will also show callers that use the interface method, not just direct callers.
    """
    index = get_index(sot)

    # Load calls data if provided
    calls_data = None
    if calls:
        if not calls.exists():
            console.print(f"[red]Error: calls.json not found: {calls}[/red]")
            raise typer.Exit(1)
        from .graph import CallsData
        calls_data = CallsData.load(calls)

    resolve_query = ResolveQuery(index)
    resolve_result = resolve_query.execute(symbol)

    if not resolve_result.found:
        if json_output:
            print_json({"error": "Symbol not found", "query": symbol})
        else:
            console.print(f"[red]Symbol not found: {symbol}[/red]")
        raise typer.Exit(1)

    if not resolve_result.unique:
        print_candidates(resolve_result.candidates, as_json=json_output)
        raise typer.Exit(1)

    node = resolve_result.candidates[0]
    context_query = ContextQuery(index)
    result = context_query.execute(
        node.id, depth=depth, limit=limit, include_impl=impl, direct_only=direct,
        calls_data=calls_data
    )

    if json_output:
        print_json(context_tree_to_dict(result))
    else:
        print_context_tree(result, console)


@app.command()
def owners(
    symbol: str = typer.Argument(..., help="Symbol to find ownership chain for"),
    sot: str = typer.Option(..., "--sot", "-s", help="Path to SoT JSON"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show structural containment chain (Method -> Class -> File)."""
    index = get_index(sot)

    resolve_query = ResolveQuery(index)
    resolve_result = resolve_query.execute(symbol)

    if not resolve_result.found:
        if json_output:
            print_json({"error": "Symbol not found", "query": symbol})
        else:
            console.print(f"[red]Symbol not found: {symbol}[/red]")
        raise typer.Exit(1)

    if not resolve_result.unique:
        print_candidates(resolve_result.candidates, as_json=json_output)
        raise typer.Exit(1)

    node = resolve_result.candidates[0]
    owners_query = OwnersQuery(index)
    result = owners_query.execute(node.id)

    if json_output:
        print_json(owners_tree_to_dict(result))
    else:
        console.print(f"[bold]Ownership chain for {node.fqn}:[/bold]")
        print_owners_tree(result, console)


@app.command("inherit")
def inherit_cmd(
    class_symbol: str = typer.Argument(..., help="Class to show inheritance for"),
    sot: str = typer.Option(..., "--sot", "-s", help="Path to SoT JSON"),
    direction: str = typer.Option("up", "--direction", help="Direction: up or down"),
    depth: int = typer.Option(1, "--depth", "-d", help="BFS depth for expansion"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum results"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show inheritance tree for a class with depth expansion."""
    index = get_index(sot)

    resolve_query = ResolveQuery(index)
    resolve_result = resolve_query.execute(class_symbol)

    if not resolve_result.found:
        if json_output:
            print_json({"error": "Symbol not found", "query": class_symbol})
        else:
            console.print(f"[red]Symbol not found: {class_symbol}[/red]")
        raise typer.Exit(1)

    # Filter to classes/interfaces only
    candidates = [
        c for c in resolve_result.candidates
        if c.kind in ("Class", "Interface", "Trait", "Enum")
    ]

    if not candidates:
        if json_output:
            print_json({"error": "No class/interface found", "query": class_symbol})
        else:
            console.print(f"[red]No class/interface found: {class_symbol}[/red]")
        raise typer.Exit(1)

    if len(candidates) > 1:
        print_candidates(candidates, as_json=json_output)
        raise typer.Exit(1)

    node = candidates[0]
    inherit_query = InheritQuery(index)
    result = inherit_query.execute(node.id, direction=direction, depth=depth, limit=limit)

    if json_output:
        print_json(inherit_tree_to_dict(result))
    else:
        console.print(f"[bold]Inheritance for {node.fqn} (direction={direction}, depth={depth}):[/bold]")
        print_inherit_tree(result, console)


@app.command("overrides")
def overrides_cmd(
    method_symbol: str = typer.Argument(..., help="Method to show override chain for"),
    sot: str = typer.Option(..., "--sot", "-s", help="Path to SoT JSON"),
    direction: str = typer.Option("up", "--direction", help="Direction: up or down"),
    depth: int = typer.Option(1, "--depth", "-d", help="BFS depth for expansion"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum results"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show override tree for a method with depth expansion."""
    index = get_index(sot)

    resolve_query = ResolveQuery(index)
    resolve_result = resolve_query.execute(method_symbol)

    if not resolve_result.found:
        if json_output:
            print_json({"error": "Symbol not found", "query": method_symbol})
        else:
            console.print(f"[red]Symbol not found: {method_symbol}[/red]")
        raise typer.Exit(1)

    # Filter to methods only
    candidates = [c for c in resolve_result.candidates if c.kind == "Method"]

    if not candidates:
        if json_output:
            print_json({"error": "No method found", "query": method_symbol})
        else:
            console.print(f"[red]No method found: {method_symbol}[/red]")
        raise typer.Exit(1)

    if len(candidates) > 1:
        print_candidates(candidates, as_json=json_output)
        raise typer.Exit(1)

    node = candidates[0]
    overrides_query = OverridesQuery(index)
    result = overrides_query.execute(node.id, direction=direction, depth=depth, limit=limit)

    if json_output:
        print_json(overrides_tree_to_dict(result))
    else:
        console.print(f"[bold]Overrides for {node.fqn} (direction={direction}, depth={depth}):[/bold]")
        print_overrides_tree(result, console)


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
