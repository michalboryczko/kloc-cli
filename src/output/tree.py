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


def _format_argument_lines(arg, indent: str = "          ") -> str:
    """Format a single argument with rich ISSUE-D display.

    Format: param_fqn (type): `expression` ref_symbol
    With optional source_chain expansion below.
    """
    # Build param label: prefer param_fqn, fall back to param_name
    param = arg.param_fqn or arg.param_name or f"arg[{arg.position}]"
    type_suffix = f" ({arg.value_type})" if arg.value_type else ""
    value = arg.value_expr or "?"

    # Build reference suffix
    ref_suffix = ""
    if arg.value_ref_symbol:
        ref_suffix = f" {arg.value_ref_symbol}"
    elif arg.value_source == "literal":
        ref_suffix = " literal"

    line = f"\n{indent}{param}{type_suffix}: `{value}`{ref_suffix}"

    # Expand source chain if present
    if arg.source_chain:
        for step in arg.source_chain:
            step_fqn = step.get("fqn", "?")
            step_ref = f" [cyan]\\[{step['reference_type']}][/cyan]" if step.get("reference_type") else ""
            line += f"\n{indent}    [dim]source:[/dim] {step_fqn}{step_ref}"
            if step.get("on"):
                on_text = step["on"]
                if step.get("on_kind"):
                    on_text += f" [cyan]\\[{step['on_kind']}][/cyan]"
                if step.get("on_file") and step.get("on_line") is not None:
                    on_text += f" [dim]({step['on_file']}:{step['on_line'] + 1})[/dim]"
                line += f"\n{indent}        [dim]on:[/dim] [green]{on_text}[/green]"

    return line


def print_definition_section(result: ContextResult, console: Console):
    """Print the DEFINITION section for a context query result.

    Shows structural information about the queried symbol: signature,
    typed arguments, return type, containing class, properties, methods,
    and inheritance relationships.

    Args:
        result: ContextResult with definition info.
        console: Rich console for output.
    """
    defn = result.definition
    if not defn:
        return

    console.print("[bold cyan]== DEFINITION ==[/bold cyan]")

    # Show signature for methods/functions
    if defn.signature:
        console.print(f"[bold]{defn.signature}[/bold]")
    else:
        # Show value_kind for Value nodes: "Value (local)", "Value (parameter)", etc.
        kind_display = defn.kind
        if defn.kind == "Value" and defn.value_kind:
            kind_display = f"{defn.kind} ({defn.value_kind})"
        console.print(f"[bold]{kind_display}[/bold]: {defn.fqn}")

    # ISSUE-B: Show type info for Value nodes
    if defn.type_info:
        type_display = defn.type_info.get("name", defn.type_info.get("fqn", "?"))
        console.print(f"  [dim]Type:[/dim] {type_display}")

    # ISSUE-B: Show source for Value nodes
    if defn.source:
        source_name = defn.source.get("method_name", "unknown")
        source_line = defn.source.get("line")
        if source_line is not None:
            console.print(f"  [dim]Source:[/dim] {source_name} result (line {source_line + 1})")
        else:
            console.print(f"  [dim]Source:[/dim] {source_name}")

    # ISSUE-B: Show scope for Value nodes (via declared_in, rendered later)
    # Scope is shown as "Scope:" instead of "Defined in:" for Value nodes
    if defn.kind == "Value" and defn.declared_in:
        scope_fqn = defn.declared_in.get("fqn", "?")
        console.print(f"  [dim]Scope:[/dim] {scope_fqn}")

    # Show typed arguments for methods/functions
    if defn.arguments:
        console.print("  [dim]Arguments:[/dim]")
        for arg in defn.arguments:
            arg_name = arg.get("name", "?")
            arg_type = arg.get("type")
            if arg_type:
                console.print(f"    {arg_name}: {arg_type}")
            else:
                console.print(f"    {arg_name}")

    # Show return type for methods/functions (not properties)
    if defn.return_type and defn.kind not in ("Property",):
        type_name = defn.return_type.get("name", defn.return_type.get("fqn", "?"))
        console.print(f"  [dim]Return type:[/dim] {type_name}")

    # Show property metadata (type, typeFqn, visibility, promoted, readonly, static)
    if defn.kind == "Property" and defn.return_type:
        rt = defn.return_type
        type_name = rt.get("name", rt.get("fqn", "?"))
        console.print(f"  [dim]Type:[/dim] {type_name}")
        type_fqn = rt.get("fqn")
        if type_fqn and type_fqn != type_name:
            console.print(f"  [dim]Type FQN:[/dim] {type_fqn}")
        vis = rt.get("visibility")
        if vis:
            console.print(f"  [dim]Visibility:[/dim] {vis}")
        console.print(f"  [dim]Promoted:[/dim] {'yes' if rt.get('promoted') else 'no'}")
        console.print(f"  [dim]Readonly:[/dim] {'yes' if rt.get('readonly') else 'no'}")
        if rt.get("static"):
            console.print(f"  [dim]Static:[/dim] yes")

    # Show type for arguments (reuses return_type field)
    if defn.kind == "Argument" and defn.return_type:
        type_name = defn.return_type.get("name", defn.return_type.get("fqn", "?"))
        console.print(f"  [dim]Type:[/dim] {type_name}")

    # context-final ISSUE-G: Show constructor dependencies
    if defn.constructor_deps:
        console.print("  [dim]Constructor deps:[/dim]")
        for dep in defn.constructor_deps:
            dep_name = dep.get("name", "?")
            dep_type = dep.get("type")
            if dep_type:
                console.print(f"    {dep_name}: {dep_type}")
            else:
                console.print(f"    {dep_name}")

    # Show properties for classes
    if defn.properties:
        console.print("  [dim]Properties:[/dim]")
        for prop in defn.properties:
            prop_name = prop.get("name", "?")
            prop_type = prop.get("type")
            parts = []
            # context-final ISSUE-G: Show property metadata
            visibility = prop.get("visibility")
            if visibility:
                parts.append(visibility)
            if prop.get("readonly"):
                parts.append("readonly")
            if prop.get("static"):
                parts.append("static")
            if prop.get("promoted"):
                parts.append("promoted")
            prefix = f"[dim]{' '.join(parts)}[/dim] " if parts else ""
            if prop_type:
                console.print(f"    {prefix}{prop_name}: {prop_type}")
            else:
                console.print(f"    {prefix}{prop_name}")

    # Show methods for classes
    if defn.methods:
        console.print("  [dim]Methods:[/dim]")
        for method in defn.methods:
            sig = method.get("signature")
            # context-final ISSUE-G: Show method tags
            tags = method.get("tags", [])
            tag_str = f" [cyan]{''.join(f'[{t}]' for t in tags)}[/cyan]" if tags else ""
            if sig:
                console.print(f"    {sig}{tag_str}")
            else:
                console.print(f"    {method.get('name', '?')}(){tag_str}")

    # Show inheritance
    if defn.extends:
        console.print(f"  [dim]Extends:[/dim] {defn.extends}")
    if defn.implements:
        console.print(f"  [dim]Implements:[/dim] {', '.join(defn.implements)}")
    if defn.uses_traits:
        console.print(f"  [dim]Uses traits:[/dim] {', '.join(defn.uses_traits)}")

    # Show declared-in (skip for Value nodes — they show "Scope:" instead)
    if defn.declared_in and defn.kind != "Value":
        declared_fqn = defn.declared_in.get("fqn", "?")
        declared_file = defn.declared_in.get("file")
        declared_line = defn.declared_in.get("line")
        location = ""
        if declared_file:
            location = f" ({declared_file}"
            if declared_line is not None:
                location += f":{declared_line + 1}"
            location += ")"
        console.print(f"  [dim]Defined in:[/dim] {declared_fqn}{location}")
    elif defn.file:
        location = defn.file
        if defn.line is not None:
            location += f":{defn.line + 1}"
        label = "File" if defn.kind == "Value" else "Defined at"
        console.print(f"  [dim]{label}:[/dim] {location}")

    console.print()


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

            # Kind 1: Variable entry (local_variable)
            if entry.entry_type == "local_variable":
                var_type_str = f" ({entry.variable_type})" if entry.variable_type else ""
                label = f"[dim]\\[{entry.depth}][/dim] [bold green]{entry.variable_name}[/bold green]{var_type_str} [cyan]\\[variable][/cyan]"
                if entry.file and entry.line is not None:
                    label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
                elif entry.file:
                    label += f" [dim]({entry.file})[/dim]"
                # Show nested source call
                if entry.source_call:
                    sc = entry.source_call
                    sc_ref_type = f" [cyan]\\[{sc.member_ref.reference_type}][/cyan]" if sc.member_ref and sc.member_ref.reference_type else ""
                    label += f"\n        [dim]source:[/dim] {sc.fqn}{sc_ref_type}"
                    if sc.member_ref and sc.member_ref.access_chain:
                        chain_text = sc.member_ref.access_chain
                        if sc.member_ref.access_chain_symbol:
                            chain_text += f" ({sc.member_ref.access_chain_symbol})"
                        if sc.member_ref.on_kind:
                            chain_text += f" [cyan]\\[{sc.member_ref.on_kind}][/cyan]"
                        if sc.member_ref.on_file and sc.member_ref.on_line is not None:
                            chain_text += f" [dim]({sc.member_ref.on_file}:{sc.member_ref.on_line + 1})[/dim]"
                        label += f"\n          [dim]on:[/dim] [green]{chain_text}[/green]"
                    if sc.arguments:
                        label += "\n          [dim]args:[/dim]"
                        for arg in sc.arguments:
                            label += _format_argument_lines(arg, indent="            ")
            else:
                # Kind 2: Call entry (or type reference without entry_type)
                display_name = _format_entry_name(entry)
                label = f"[dim]\\[{entry.depth}][/dim] {display_name}"
                # Append member ref inline: "source -> member [reference_type]"
                if entry.member_ref:
                    # For member references, show the member name
                    if entry.member_ref.target_name:
                        label += f" [bold yellow]->[/bold yellow] [yellow]{entry.member_ref.target_name}[/yellow]"
                    # Add reference type indicator (escape brackets for Rich)
                    if entry.member_ref.reference_type:
                        label += f" [cyan]\\[{entry.member_ref.reference_type}][/cyan]"
                # context-final ISSUE-G: Render new flat entry fields
                # Property arrow for property_type entries
                if entry.property_name and not (entry.member_ref and entry.member_ref.target_name):
                    label += f" [bold yellow]->[/bold yellow] [yellow]{entry.property_name}[/yellow]"
                # refType tag (when no member_ref reference_type already shown)
                if entry.ref_type and not (entry.member_ref and entry.member_ref.reference_type):
                    label += f" [cyan]\\[{entry.ref_type}][/cyan]"
                # callee display (only for method_call)
                if entry.callee and entry.ref_type == "method_call" and not (entry.member_ref and entry.member_ref.target_name):
                    label += f" [bold yellow]->[/bold yellow] [yellow]{entry.callee}[/yellow]"
                # via label
                if entry.via:
                    via_short = entry.via.rsplit("\\", 1)[-1] if "\\" in entry.via else entry.via
                    label += f" [magenta]<- via {via_short}[/magenta]"
                # sites display (multi-site replaces single line)
                if entry.sites:
                    count = len(entry.sites)
                    label += f" [dim](x{count})[/dim]"
                    if entry.file:
                        label += f" [dim]({entry.file})[/dim]"
                elif entry.file and entry.line is not None:
                    label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
                elif entry.file:
                    label += f" [dim]({entry.file})[/dim]"
                # on/onKind display for new flat fields
                if entry.on and not (entry.member_ref and entry.member_ref.access_chain):
                    on_text = entry.on
                    if entry.on_kind:
                        on_text += f" [cyan]\\[{entry.on_kind}][/cyan]"
                    label += f"\n        [dim]on:[/dim] [green]{on_text}[/green]"
                # Add access chain on a new line if present
                elif entry.member_ref and entry.member_ref.access_chain:
                    chain_text = entry.member_ref.access_chain
                    # R4: Include property FQN in parentheses after access chain if available
                    if entry.member_ref.access_chain_symbol:
                        chain_text += f" ({entry.member_ref.access_chain_symbol})"
                    if entry.member_ref.on_kind:
                        chain_text += f" [cyan]\\[{entry.member_ref.on_kind}][/cyan]"
                    if entry.member_ref.on_file and entry.member_ref.on_line is not None:
                        chain_text += f" [dim]({entry.member_ref.on_file}:{entry.member_ref.on_line + 1})[/dim]"
                    label += f"\n        [dim]on:[/dim] [green]{chain_text}[/green]"
                # Show argument-to-parameter mappings if present
                if entry.arguments:
                    label += "\n        [dim]args:[/dim]"
                    for arg in entry.arguments:
                        label += _format_argument_lines(arg, indent="          ")
                # Show result variable if present
                if entry.result_var:
                    label += f"\n        [dim]result ->[/dim] [green]{entry.result_var}[/green]"

            branch = parent.add(label)

            # ISSUE-E: Show boundary crossing indicator
            if entry.crossed_from:
                branch.add(f"[dim italic]crosses into {entry.crossed_from}[/dim italic]")

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

    # Print DEFINITION section (before USED BY)
    if result.definition:
        print_definition_section(result, console)

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
    """Convert context result to JSON-serializable dict with nested tree structure.

    Delegates to ContextOutput model for contract-compliant serialization.
    """
    from ..models.output import ContextOutput

    return ContextOutput.from_result(result).to_dict()


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
