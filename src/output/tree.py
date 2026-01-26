"""Tree output formatters for deps, usages, context, owners, inherit, and overrides."""

from rich.console import Console
from rich.tree import Tree

from ..models import (
    DepsEntry, DepsTreeResult,
    UsageEntry, UsagesTreeResult,
    ContextResult,
    OwnersResult,
    InheritEntry, InheritTreeResult,
    OverrideEntry, OverridesTreeResult,
)


def _count_tree_nodes(entries: list) -> int:
    """Count total nodes in a tree structure."""
    total = 0
    for entry in entries:
        total += 1
        if entry.children:
            total += _count_tree_nodes(entry.children)
    return total


def print_deps_tree(result: DepsTreeResult, console: Console):
    """Print dependencies as a tree.

    Args:
        result: DepsTreeResult with tree structure.
        console: Rich console for output.
    """
    root = Tree(f"[bold]{result.target.fqn}[/bold]")

    def add_children(parent: Tree, entries: list[DepsEntry]):
        for entry in entries:
            # Format: [depth] FQN (file:line)
            label = f"[dim][{entry.depth}][/dim] {entry.fqn}"
            if entry.file and entry.line is not None:
                label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
            elif entry.file:
                label += f" [dim]({entry.file})[/dim]"

            branch = parent.add(label)
            if entry.children:
                add_children(branch, entry.children)

    add_children(root, result.tree)
    console.print(root)


def print_usages_tree(result: UsagesTreeResult, console: Console):
    """Print usages as a tree.

    Args:
        result: UsagesTreeResult with tree structure.
        console: Rich console for output.
    """
    root = Tree(f"[bold]{result.target.fqn}[/bold]")

    def add_children(parent: Tree, entries: list[UsageEntry]):
        for entry in entries:
            # Format: [depth] FQN (file:line)
            label = f"[dim][{entry.depth}][/dim] {entry.fqn}"
            if entry.file and entry.line is not None:
                label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
            elif entry.file:
                label += f" [dim]({entry.file})[/dim]"

            branch = parent.add(label)
            if entry.children:
                add_children(branch, entry.children)

    add_children(root, result.tree)
    console.print(root)


def deps_tree_to_dict(result: DepsTreeResult) -> dict:
    """Convert deps tree to JSON-serializable dict.

    Args:
        result: DepsTreeResult with tree structure.

    Returns:
        Dictionary suitable for JSON serialization.
    """
    def entry_to_dict(entry: DepsEntry) -> dict:
        return {
            "depth": entry.depth,
            "fqn": entry.fqn,
            "file": entry.file,
            "line": entry.line + 1 if entry.line is not None else None,
            "children": [entry_to_dict(c) for c in entry.children],
        }

    return {
        "target": {
            "fqn": result.target.fqn,
            "file": result.target.file,
        },
        "max_depth": result.max_depth,
        "total": _count_tree_nodes(result.tree),
        "tree": [entry_to_dict(e) for e in result.tree],
    }


def usages_tree_to_dict(result: UsagesTreeResult) -> dict:
    """Convert usages tree to JSON-serializable dict.

    Args:
        result: UsagesTreeResult with tree structure.

    Returns:
        Dictionary suitable for JSON serialization.
    """
    def entry_to_dict(entry: UsageEntry) -> dict:
        return {
            "depth": entry.depth,
            "fqn": entry.fqn,
            "file": entry.file,
            "line": entry.line + 1 if entry.line is not None else None,
            "children": [entry_to_dict(c) for c in entry.children],
        }

    return {
        "target": {
            "fqn": result.target.fqn,
            "file": result.target.file,
        },
        "max_depth": result.max_depth,
        "total": _count_tree_nodes(result.tree),
        "tree": [entry_to_dict(e) for e in result.tree],
    }


def _format_entry_name(entry) -> str:
    """Format entry name, using signature for methods if available."""
    if entry.signature and entry.kind in ("Method", "Function"):
        # For methods with signature, show class::signature
        if "::" in entry.fqn:
            class_part = entry.fqn.rsplit("::", 1)[0]
            return f"{class_part}::{entry.signature}"
        return entry.signature
    return entry.fqn


def print_context_tree(result: ContextResult, console: Console):
    """Print context (used_by and uses) as nested trees.

    For uses tree, shows implementations inline for interfaces/methods.
    For used_by tree, shows interface usages grouped under the interface method.

    Args:
        result: ContextResult with tree structures.
        console: Rich console for output.
    """
    from ..models import ContextEntry

    def add_context_children(parent: Tree, entries: list[ContextEntry], show_impl: bool = False):
        for entry in entries:
            # Handle via_interface entries (usages grouped under interface method)
            if entry.via_interface:
                display_name = _format_entry_name(entry)
                label = f"[bold magenta]← via interface:[/bold magenta] {display_name}"
                if entry.file and entry.line is not None:
                    label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
                elif entry.file:
                    label += f" [dim]({entry.file})[/dim]"
                branch = parent.add(label)
                # Children are the usages of the interface method
                if entry.children:
                    add_context_children(branch, entry.children, show_impl)
                continue

            display_name = _format_entry_name(entry)
            label = f"[dim][{entry.depth}][/dim] {display_name}"
            if entry.file and entry.line is not None:
                label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
            elif entry.file:
                label += f" [dim]({entry.file})[/dim]"

            branch = parent.add(label)

            # Show implementations for interfaces/methods (with their children)
            if show_impl and entry.implementations:
                for impl in entry.implementations:
                    impl_display = _format_entry_name(impl)
                    impl_label = f"[bold magenta]→ impl:[/bold magenta] {impl_display}"
                    if impl.file and impl.line is not None:
                        impl_label += f" [dim]({impl.file}:{impl.line + 1})[/dim]"
                    elif impl.file:
                        impl_label += f" [dim]({impl.file})[/dim]"
                    impl_branch = branch.add(impl_label)
                    # Recurse into implementation's children
                    if impl.children:
                        add_context_children(impl_branch, impl.children, show_impl)

            if entry.children:
                add_context_children(branch, entry.children, show_impl)

    # Use display_name for methods (shows signature), fqn for others
    target_display = result.target.display_name

    # Print target info
    console.print(f"[bold]Context for {target_display}[/bold]")
    console.print(f"[dim]defined at: {result.target.location_str}[/dim]")
    console.print()

    # Print USED BY tree
    console.print("[bold cyan]== USED BY ==[/bold cyan]")
    if not result.used_by:
        console.print("[dim]None[/dim]")
    else:
        used_by_root = Tree(f"[bold]{target_display}[/bold]")
        add_context_children(used_by_root, result.used_by, show_impl=False)
        console.print(used_by_root)

    console.print()

    # Print USES tree (with implementations if present)
    console.print("[bold cyan]== USES ==[/bold cyan]")
    if not result.uses:
        console.print("[dim]None[/dim]")
    else:
        uses_root = Tree(f"[bold]{target_display}[/bold]")
        add_context_children(uses_root, result.uses, show_impl=True)
        console.print(uses_root)


def context_tree_to_dict(result: ContextResult) -> dict:
    """Convert context result to JSON-serializable dict with nested tree structure."""
    from ..models import ContextEntry

    def context_entry_to_dict(entry: ContextEntry) -> dict:
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
        # Include implementations if present (USES direction)
        if entry.implementations:
            d["implementations"] = [context_entry_to_dict(impl) for impl in entry.implementations]
        # Include via_interface flag if set (USED BY direction)
        if entry.via_interface:
            d["via_interface"] = True
        return d

    target_dict = {
        "fqn": result.target.fqn,
        "file": result.target.file,
        "line": result.target.start_line + 1 if result.target.start_line is not None else None,
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


def print_owners_tree(result: OwnersResult, console: Console):
    """Print ownership chain as a tree (innermost to outermost).

    Args:
        result: OwnersResult with chain of containing nodes.
        console: Rich console for output.
    """
    if not result.chain:
        console.print("[dim]No ownership chain[/dim]")
        return

    # Build tree from innermost (first) to outermost (last)
    # Reverse so outermost is root
    chain = list(reversed(result.chain))
    root = Tree(f"[bold]{chain[0].kind}[/bold]: {chain[0].fqn}")

    current = root
    for node in chain[1:]:
        current = current.add(f"[bold]{node.kind}[/bold]: {node.fqn}")

    console.print(root)


def owners_tree_to_dict(result: OwnersResult) -> dict:
    """Convert owners result to JSON-serializable dict."""
    return {
        "chain": [
            {
                "kind": node.kind,
                "fqn": node.fqn,
                "file": node.file,
                "line": node.start_line + 1 if node.start_line is not None else None,
            }
            for node in result.chain
        ],
    }


def print_inherit_tree(result: InheritTreeResult, console: Console):
    """Print inheritance as a tree with depth support.

    Args:
        result: InheritTreeResult with tree structure.
        console: Rich console for output.
    """
    if not result.tree:
        console.print("[dim]No inheritance found[/dim]")
        return

    root = Tree(f"[bold]{result.root.fqn}[/bold]")

    def add_children(parent: Tree, entries: list[InheritEntry]):
        for entry in entries:
            # Format: [depth] kind FQN (file:line)
            if result.direction == "up":
                label = f"[dim][{entry.depth}][/dim] extends {entry.fqn}"
            else:
                label = f"[dim][{entry.depth}][/dim] {entry.kind}: {entry.fqn}"
            if entry.file and entry.line is not None:
                label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
            elif entry.file:
                label += f" [dim]({entry.file})[/dim]"

            branch = parent.add(label)
            if entry.children:
                add_children(branch, entry.children)

    add_children(root, result.tree)
    console.print(root)


def inherit_tree_to_dict(result: InheritTreeResult) -> dict:
    """Convert inherit tree result to JSON-serializable dict."""
    def entry_to_dict(entry: InheritEntry) -> dict:
        return {
            "depth": entry.depth,
            "kind": entry.kind,
            "fqn": entry.fqn,
            "file": entry.file,
            "line": entry.line + 1 if entry.line is not None else None,
            "children": [entry_to_dict(c) for c in entry.children],
        }

    return {
        "root": {
            "fqn": result.root.fqn,
            "file": result.root.file,
        },
        "direction": result.direction,
        "max_depth": result.max_depth,
        "total": _count_tree_nodes(result.tree),
        "tree": [entry_to_dict(e) for e in result.tree],
    }


def print_overrides_tree(result: OverridesTreeResult, console: Console):
    """Print overrides as a tree with depth support.

    Args:
        result: OverridesTreeResult with tree structure.
        console: Rich console for output.
    """
    if not result.tree:
        console.print("[dim]No overrides found[/dim]")
        return

    root = Tree(f"[bold]{result.root.fqn}[/bold]")

    def add_children(parent: Tree, entries: list[OverrideEntry]):
        for entry in entries:
            # Format: [depth] FQN (file:line)
            if result.direction == "up":
                label = f"[dim][{entry.depth}][/dim] overrides {entry.fqn}"
            else:
                label = f"[dim][{entry.depth}][/dim] {entry.fqn}"
            if entry.file and entry.line is not None:
                label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
            elif entry.file:
                label += f" [dim]({entry.file})[/dim]"

            branch = parent.add(label)
            if entry.children:
                add_children(branch, entry.children)

    add_children(root, result.tree)
    console.print(root)


def overrides_tree_to_dict(result: OverridesTreeResult) -> dict:
    """Convert overrides tree result to JSON-serializable dict."""
    def entry_to_dict(entry: OverrideEntry) -> dict:
        return {
            "depth": entry.depth,
            "fqn": entry.fqn,
            "file": entry.file,
            "line": entry.line + 1 if entry.line is not None else None,
            "children": [entry_to_dict(c) for c in entry.children],
        }

    return {
        "root": {
            "fqn": result.root.fqn,
            "file": result.root.file,
        },
        "direction": result.direction,
        "max_depth": result.max_depth,
        "total": _count_tree_nodes(result.tree),
        "tree": [entry_to_dict(e) for e in result.tree],
    }
