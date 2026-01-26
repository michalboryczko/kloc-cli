"""Console output formatters using Rich."""

from rich.console import Console

from .json_formatter import print_json
from ..models import (
    NodeData,
    ResolveResult,
    UsageResult,
    DepsResult,
    ContextResult,
    OwnersResult,
    InheritResult,
    OverridesResult,
)

console = Console()


def print_node(node: NodeData, as_json: bool = False):
    """Print a single node."""
    if as_json:
        print_json({
            "id": node.id,
            "kind": node.kind,
            "name": node.name,
            "fqn": node.fqn,
            "file": node.file,
            "line": node.start_line + 1 if node.start_line is not None else None,
        })
    else:
        console.print(f"[bold]{node.kind}[/bold]: {node.fqn}")
        console.print(f"  File: {node.location_str}")
        if node.documentation:
            doc = node.documentation[0][:100]
            console.print(f"  Doc: {doc}...")


def print_candidates(nodes: list[NodeData], as_json: bool = False):
    """Print multiple candidate nodes."""
    if as_json:
        print_json([
            {
                "id": n.id,
                "kind": n.kind,
                "fqn": n.fqn,
                "file": n.file,
                "line": n.start_line + 1 if n.start_line is not None else None,
            }
            for n in nodes
        ])
    else:
        console.print(f"[yellow]Found {len(nodes)} candidates:[/yellow]")
        for i, node in enumerate(nodes, 1):
            console.print(f"  [{i}] {node.kind}: {node.fqn}")
            console.print(f"      {node.location_str}")


def print_usages(usages: list[UsageResult], as_json: bool = False):
    """Print usage results."""
    if as_json:
        print_json([
            {
                "file": u.file,
                "line": u.line + 1 if u.line is not None else None,
                "referrer": u.referrer_fqn,
            }
            for u in usages
        ])
    else:
        if not usages:
            console.print("[dim]No usages found[/dim]")
            return

        # Group by file
        by_file: dict[str, list[tuple[int, str]]] = {}
        for u in usages:
            file = u.file or "<unknown>"
            line = u.line + 1 if u.line is not None else 0
            if file not in by_file:
                by_file[file] = []
            by_file[file].append((line, u.referrer_fqn))

        for file, items in sorted(by_file.items()):
            for line, referrer in sorted(items):
                console.print(f"{file}:{line} — {referrer}")


def print_deps(deps: list[DepsResult], as_json: bool = False):
    """Print dependency results."""
    if as_json:
        print_json([
            {
                "file": d.file,
                "line": d.line + 1 if d.line is not None else None,
                "target": d.target_fqn,
            }
            for d in deps
        ])
    else:
        if not deps:
            console.print("[dim]No dependencies found[/dim]")
            return

        for dep in deps:
            if dep.file and dep.line is not None:
                console.print(
                    f"{dep.file}:{dep.line + 1} — uses {dep.target_fqn}"
                )
            else:
                console.print(f"<unknown> — uses {dep.target_fqn}")


def print_context(result: ContextResult, as_json: bool = False):
    """Print context results (usages + deps with depth)."""
    if as_json:
        print_json({
            "query": result.target.fqn,
            "resolved": {
                "id": result.target.id,
                "kind": result.target.kind,
                "fqn": result.target.fqn,
                "defined_at": {
                    "file": result.target.file,
                    "line": (
                        result.target.start_line + 1
                        if result.target.start_line is not None
                        else None
                    ),
                },
            },
            "depth": result.depth,
            "used_by": [
                {
                    "hop": e.hop,
                    "file": e.file,
                    "line": e.line + 1 if e.line is not None else None,
                    "referrer": e.fqn,
                }
                for e in result.used_by
            ],
            "uses": [
                {
                    "hop": e.hop,
                    "file": e.file,
                    "line": e.line + 1 if e.line is not None else None,
                    "target": e.fqn,
                }
                for e in result.uses
            ],
        })
    else:
        console.print("[bold]== TARGET ==[/bold]")
        console.print(f"{result.target.fqn}")
        console.print(f"defined at: {result.target.location_str}")
        console.print()

        console.print(
            f"[bold]== USED BY (incoming uses, depth<={result.depth}) ==[/bold]"
        )
        if not result.used_by:
            console.print("[dim]None[/dim]")
        else:
            for e in result.used_by:
                if e.file and e.line is not None:
                    console.print(f"[{e.hop}] {e.file}:{e.line + 1} — {e.fqn}")
                else:
                    console.print(f"[{e.hop}] <unknown> — {e.fqn}")
        console.print()

        console.print(
            f"[bold]== USES (outgoing uses, depth<={result.depth}) ==[/bold]"
        )
        if not result.uses:
            console.print("[dim]None[/dim]")
        else:
            for e in result.uses:
                if e.file and e.line is not None:
                    console.print(
                        f"[{e.hop}] {e.file}:{e.line + 1} — uses {e.fqn}"
                    )
                else:
                    console.print(f"[{e.hop}] <unknown> — uses {e.fqn}")


def print_owners(result: OwnersResult, as_json: bool = False):
    """Print ownership chain."""
    if as_json:
        print_json([
            {"kind": node.kind, "fqn": node.fqn, "file": node.file}
            for node in result.chain
        ])
    else:
        for node in result.chain:
            console.print(f"{node.kind:12} {node.fqn}")
            if node.file:
                console.print(f"             {node.file}")


def print_inheritance(result: InheritResult, as_json: bool = False):
    """Print inheritance chain."""
    if as_json:
        print_json([
            {"fqn": node.fqn, "file": node.file}
            for node in result.chain
        ])
    else:
        if not result.chain:
            console.print("[dim]No inheritance found[/dim]")
            return

        if result.direction == "up":
            for i, node in enumerate(result.chain):
                indent = "  " * i
                prefix = "extends " if i > 0 else ""
                console.print(f"{indent}{prefix}{node.fqn}")
        else:
            for node in result.chain:
                console.print(f"{node.fqn}")


def print_overrides(result: OverridesResult, as_json: bool = False):
    """Print override chain."""
    if as_json:
        print_json([
            {"fqn": node.fqn, "file": node.file}
            for node in result.chain
        ])
    else:
        if not result.chain:
            console.print("[dim]No overrides found[/dim]")
            return

        if result.direction == "up":
            for i, node in enumerate(result.chain):
                prefix = "overrides " if i > 0 else ""
                console.print(f"{prefix}{node.fqn}")
        else:
            for node in result.chain:
                console.print(f"{node.fqn}")
