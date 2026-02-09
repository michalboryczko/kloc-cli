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
                line += f"\n{indent}        [dim]on:[/dim] [green]{step['on']}[/green]"

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
        console.print(f"[bold]{defn.kind}[/bold]: {defn.fqn}")

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

    # Show return type for methods/functions
    if defn.return_type:
        type_name = defn.return_type.get("name", defn.return_type.get("fqn", "?"))
        console.print(f"  [dim]Return type:[/dim] {type_name}")

    # Show type for properties/arguments (reuses return_type field)
    if defn.kind in ("Property", "Argument") and defn.return_type:
        type_name = defn.return_type.get("name", defn.return_type.get("fqn", "?"))
        console.print(f"  [dim]Type:[/dim] {type_name}")

    # Show properties for classes
    if defn.properties:
        console.print("  [dim]Properties:[/dim]")
        for prop in defn.properties:
            prop_name = prop.get("name", "?")
            prop_type = prop.get("type")
            if prop_type:
                console.print(f"    {prop_name}: {prop_type}")
            else:
                console.print(f"    {prop_name}")

    # Show methods for classes
    if defn.methods:
        console.print("  [dim]Methods:[/dim]")
        for method in defn.methods:
            sig = method.get("signature")
            if sig:
                console.print(f"    {sig}")
            else:
                console.print(f"    {method.get('name', '?')}()")

    # Show inheritance
    if defn.extends:
        console.print(f"  [dim]Extends:[/dim] {defn.extends}")
    if defn.implements:
        console.print(f"  [dim]Implements:[/dim] {', '.join(defn.implements)}")
    if defn.uses_traits:
        console.print(f"  [dim]Uses traits:[/dim] {', '.join(defn.uses_traits)}")

    # Show declared-in
    if defn.declared_in:
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
        console.print(f"  [dim]Defined at:[/dim] {location}")

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
                if entry.file and entry.line is not None:
                    label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
                elif entry.file:
                    label += f" [dim]({entry.file})[/dim]"
                # Add access chain on a new line if present
                if entry.member_ref and entry.member_ref.access_chain:
                    chain_text = entry.member_ref.access_chain
                    # R4: Include property FQN in parentheses after access chain if available
                    if entry.member_ref.access_chain_symbol:
                        chain_text += f" ({entry.member_ref.access_chain_symbol})"
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
    """Convert context result to JSON-serializable dict with nested tree structure."""
    from ..models import ContextEntry

    def _argument_to_dict(a) -> dict:
        d = {
            "position": a.position,
            "param_name": a.param_name,
            "value_expr": a.value_expr,
            "value_source": a.value_source,
        }
        if a.value_type is not None:
            d["value_type"] = a.value_type
        if a.param_fqn is not None:
            d["param_fqn"] = a.param_fqn
        if a.value_ref_symbol is not None:
            d["value_ref_symbol"] = a.value_ref_symbol
        if a.source_chain is not None:
            d["source_chain"] = a.source_chain
        return d

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
        # Include member reference (what specific member is used)
        if entry.member_ref:
            member_ref_dict = {
                "target_name": entry.member_ref.target_name,
                "target_fqn": entry.member_ref.target_fqn,
                "target_kind": entry.member_ref.target_kind,
                "file": entry.member_ref.file,
                "line": entry.member_ref.line + 1 if entry.member_ref.line is not None else None,
            }
            # Include reference_type if present
            if entry.member_ref.reference_type:
                member_ref_dict["reference_type"] = entry.member_ref.reference_type
            # Include access_chain if present
            if entry.member_ref.access_chain:
                member_ref_dict["access_chain"] = entry.member_ref.access_chain
            # R4: Include access_chain_symbol if present (property FQN)
            if entry.member_ref.access_chain_symbol:
                member_ref_dict["access_chain_symbol"] = entry.member_ref.access_chain_symbol
            d["member_ref"] = member_ref_dict
        # Include arguments if present
        if entry.arguments:
            d["arguments"] = [
                _argument_to_dict(a)
                for a in entry.arguments
            ]
        # Include result_var if present
        if entry.result_var:
            d["result_var"] = entry.result_var
        # ISSUE-C: Include entry_type and variable fields
        if entry.entry_type:
            d["entry_type"] = entry.entry_type
        if entry.variable_name:
            d["variable_name"] = entry.variable_name
        if entry.variable_symbol:
            d["variable_symbol"] = entry.variable_symbol
        if entry.variable_type:
            d["variable_type"] = entry.variable_type
        if entry.source_call:
            d["source_call"] = context_entry_to_dict(entry.source_call)
        return d

    target_dict = {
        "fqn": result.target.fqn,
        "file": result.target.file,
        "line": result.target.start_line + 1 if result.target.start_line is not None else None,
    }
    # Include signature for methods/functions
    if result.target.signature:
        target_dict["signature"] = result.target.signature

    d = {
        "target": target_dict,
        "max_depth": result.max_depth,
        "used_by": [context_entry_to_dict(e) for e in result.used_by],
        "uses": [context_entry_to_dict(e) for e in result.uses],
    }

    if result.definition:
        defn = result.definition
        defn_dict = {
            "fqn": defn.fqn,
            "kind": defn.kind,
            "file": defn.file,
            "line": defn.line + 1 if defn.line is not None else None,
        }
        if defn.signature:
            defn_dict["signature"] = defn.signature
        if defn.arguments:
            defn_dict["arguments"] = defn.arguments
        if defn.return_type:
            defn_dict["return_type"] = defn.return_type
        if defn.declared_in:
            defn_dict["declared_in"] = defn.declared_in
        if defn.properties:
            defn_dict["properties"] = defn.properties
        if defn.methods:
            defn_dict["methods"] = defn.methods
        if defn.extends:
            defn_dict["extends"] = defn.extends
        if defn.implements:
            defn_dict["implements"] = defn.implements
        if defn.uses_traits:
            defn_dict["uses_traits"] = defn.uses_traits
        d["definition"] = defn_dict

    return d


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
