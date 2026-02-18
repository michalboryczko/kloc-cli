"""Definition builders extracted from ContextQuery.

All functions are standalone with an explicit `index` parameter.
"""

import re
from typing import Optional, TYPE_CHECKING

from ..models import NodeData, DefinitionInfo
from .reference_types import get_containing_scope

if TYPE_CHECKING:
    from ..graph import SoTIndex


def build_definition(index: "SoTIndex", node_id: str) -> DefinitionInfo:
    """Build definition metadata for a symbol.

    Gathers structural information about the symbol: signature, typed
    arguments, return type, containing class, properties, methods,
    and inheritance relationships.

    Args:
        index: The SoT index.
        node_id: The node to build definition for.

    Returns:
        DefinitionInfo with symbol metadata.
    """
    node = index.nodes.get(node_id)
    if not node:
        return DefinitionInfo(fqn="unknown", kind="unknown")

    info = DefinitionInfo(
        fqn=node.fqn,
        kind=node.kind,
        file=node.file,
        line=node.start_line,
        signature=node.signature,
    )

    # Resolve containing class/method
    parent_id = index.get_contains_parent(node_id)
    if parent_id:
        parent_node = index.nodes.get(parent_id)
        if parent_node:
            info.declared_in = {
                "fqn": parent_node.fqn,
                "kind": parent_node.kind,
                "file": parent_node.file,
                "line": parent_node.start_line,
            }

    if node.kind in ("Method", "Function"):
        build_method_definition(index, node_id, node, info)
    elif node.kind in ("Class", "Interface", "Trait", "Enum"):
        build_class_definition(index, node_id, node, info)
    elif node.kind == "Property":
        build_property_definition(index, node_id, node, info)
    elif node.kind == "Argument":
        build_argument_definition(index, node_id, node, info)
    elif node.kind == "Value":
        build_value_definition(index, node_id, node, info)

    return info


def build_method_definition(index: "SoTIndex", node_id: str, node: NodeData, info: DefinitionInfo):
    """Populate definition for Method/Function nodes."""
    children = index.get_contains_children(node_id)

    # Collect typed arguments
    for child_id in children:
        child = index.nodes.get(child_id)
        if child and child.kind == "Argument":
            arg_dict: dict = {"name": child.name, "position": None}
            # Resolve type from type_hint edges
            type_edges = index.outgoing[child_id].get("type_hint", [])
            if type_edges:
                type_node = index.nodes.get(type_edges[0].target)
                if type_node:
                    arg_dict["type"] = type_node.name
            info.arguments.append(arg_dict)

    # Resolve return type from type_hint edges on the method itself
    type_edges = index.outgoing[node_id].get("type_hint", [])
    if type_edges:
        type_node = index.nodes.get(type_edges[0].target)
        if type_node:
            info.return_type = {"fqn": type_node.fqn, "name": type_node.name}


def build_class_definition(index: "SoTIndex", node_id: str, node: NodeData, info: DefinitionInfo):
    """Populate definition for Class/Interface/Trait/Enum nodes.

    For Classes: adds properties with metadata (type, visibility, promoted,
    readonly, static), methods with tags ([override], [abstract], [inherited]),
    constructor_deps for promoted constructor parameters, extends, implements.

    For Interfaces: delegates to build_interface_definition.
    """
    if node.kind == "Interface":
        build_interface_definition(index, node_id, node, info)
        return

    children = index.get_contains_children(node_id)

    for child_id in children:
        child = index.nodes.get(child_id)
        if not child:
            continue

        if child.kind == "Property":
            prop_dict: dict = {"name": child.name}
            # Type from type_hint edges
            type_edges = index.outgoing[child_id].get("type_hint", [])
            if type_edges:
                type_node = index.nodes.get(type_edges[0].target)
                if type_node:
                    prop_dict["type"] = type_node.name

            # Parse property metadata from documentation
            vis, readonly, static, doc_type = parse_property_doc(child)
            if vis:
                prop_dict["visibility"] = vis
            if readonly:
                prop_dict["readonly"] = True
            if static:
                prop_dict["static"] = True

            # If no class type from edges, use type from docs
            if "type" not in prop_dict and doc_type:
                prop_dict["type"] = doc_type

            # Detect promoted: assigned_from -> Value(parameter) in __construct
            assigned_edges = index.outgoing[child_id].get("assigned_from", [])
            for edge in assigned_edges:
                source_node = index.nodes.get(edge.target)
                if source_node and source_node.kind == "Value" and source_node.value_kind == "parameter":
                    if "__construct()" in source_node.fqn:
                        prop_dict["promoted"] = True
                        break

            info.properties.append(prop_dict)

        elif child.kind == "Method":
            # Skip __construct — implied by promoted properties
            if child.name == "__construct":
                continue

            method_dict: dict = {"name": child.name}
            if child.signature:
                method_dict["signature"] = child.signature

            # Method tags: [override], [abstract], [inherited]
            tags = []
            # Check if method overrides a parent method
            override_parent = index.get_overrides_parent(child_id)
            if override_parent:
                tags.append("override")
            # Check if method is abstract (from PHP signature in documentation)
            if child.documentation:
                for doc in child.documentation:
                    # Only check within ```php code blocks, not descriptions
                    clean = doc.replace("```php", "").replace("```", "").strip()
                    for line in clean.split("\n"):
                        line = line.strip()
                        if "function " in line and "abstract " in line:
                            tags.append("abstract")
                            break
                    if "abstract" in tags:
                        break

            if tags:
                method_dict["tags"] = tags
            info.methods.append(method_dict)

    # Sort methods: override first, then inherited, then regular
    def _method_sort_key(m):
        tags = m.get("tags", [])
        if "override" in tags:
            return 0
        if "inherited" in tags:
            return 1
        return 2
    info.methods.sort(key=_method_sort_key)

    # Constructor deps: promoted parameters with their types
    for child_id in children:
        child = index.nodes.get(child_id)
        if not child or child.kind != "Property":
            continue
        # Only promoted properties
        assigned_edges = index.outgoing[child_id].get("assigned_from", [])
        for edge in assigned_edges:
            source_node = index.nodes.get(edge.target)
            if source_node and source_node.kind == "Value" and source_node.value_kind == "parameter":
                if "__construct()" in source_node.fqn:
                    dep = {"name": child.name}
                    # Get type from type_hint edges on the property
                    type_edges = index.outgoing[child_id].get("type_hint", [])
                    if type_edges:
                        type_node = index.nodes.get(type_edges[0].target)
                        if type_node:
                            dep["type"] = type_node.name
                    else:
                        # Try scalar type from docs
                        _, _, _, doc_type = parse_property_doc(child)
                        if doc_type:
                            dep["type"] = doc_type
                    info.constructor_deps.append(dep)
                    break

    # Inheritance: extends
    extends_id = index.get_extends_parent(node_id)
    if extends_id:
        extends_node = index.nodes.get(extends_id)
        if extends_node:
            info.extends = extends_node.fqn

    # Inheritance: implements
    impl_ids = index.get_implements(node_id)
    for impl_id in impl_ids:
        impl_node = index.nodes.get(impl_id)
        if impl_node:
            info.implements.append(impl_node.fqn)

    # Traits: uses_trait
    trait_edges = index.outgoing[node_id].get("uses_trait", [])
    for edge in trait_edges:
        trait_node = index.nodes.get(edge.target)
        if trait_node:
            info.uses_traits.append(trait_node.fqn)


def build_interface_definition(index: "SoTIndex", node_id: str, node: NodeData, info: DefinitionInfo):
    """Populate definition for Interface nodes.

    Shows method signatures only (no properties, no implements).
    Shows extends if interface extends another interface.
    """
    children = index.get_contains_children(node_id)

    for child_id in children:
        child = index.nodes.get(child_id)
        if not child:
            continue

        if child.kind == "Method":
            method_dict: dict = {"name": child.name}
            if child.signature:
                method_dict["signature"] = child.signature
            info.methods.append(method_dict)

    # Interface extends (interface extending interface)
    extends_id = index.get_extends_parent(node_id)
    if extends_id:
        extends_node = index.nodes.get(extends_id)
        if extends_node:
            info.extends = extends_node.fqn


def build_property_definition(index: "SoTIndex", node_id: str, node: NodeData, info: DefinitionInfo):
    """Populate definition for Property nodes.

    Extracts: type (from type_hint edges or documentation), visibility,
    promoted (detected via assigned_from -> Value(parameter) in __construct),
    readonly, static -- all parsed from SCIP documentation strings.
    """
    # Type from type_hint edges (class types)
    type_edges = index.outgoing[node_id].get("type_hint", [])
    if type_edges:
        type_node = index.nodes.get(type_edges[0].target)
        if type_node:
            info.return_type = {"fqn": type_node.fqn, "name": type_node.name}

    # Parse visibility, readonly, static, and scalar type from documentation
    vis, readonly, static, doc_type = parse_property_doc(node)
    if vis:
        if not info.return_type:
            info.return_type = {}
        info.return_type["visibility"] = vis
    if readonly:
        if not info.return_type:
            info.return_type = {}
        info.return_type["readonly"] = True
    if static:
        if not info.return_type:
            info.return_type = {}
        info.return_type["static"] = True

    # If property itself isn't readonly, check if the containing class is readonly
    # (PHP readonly classes make all properties implicitly readonly)
    if not readonly:
        parent_id = index.get_contains_parent(node_id)
        if parent_id:
            parent_node = index.nodes.get(parent_id)
            if parent_node and parent_node.kind == "Class" and parent_node.documentation:
                for doc in parent_node.documentation:
                    if "readonly class" in doc or "readonly " in doc:
                        readonly = True
                        break
        if readonly:
            if not info.return_type:
                info.return_type = {}
            info.return_type["readonly"] = True

    # If no class type from edges, use type from documentation
    if not info.return_type or "name" not in info.return_type:
        if doc_type:
            if info.return_type is None:
                info.return_type = {}
            info.return_type["name"] = doc_type
            info.return_type["fqn"] = doc_type

    # Detect promoted: assigned_from -> Value(parameter) in __construct
    assigned_edges = index.outgoing[node_id].get("assigned_from", [])
    for edge in assigned_edges:
        source_node = index.nodes.get(edge.target)
        if source_node and source_node.kind == "Value" and source_node.value_kind == "parameter":
            if "__construct()" in source_node.fqn:
                if not info.return_type:
                    info.return_type = {}
                info.return_type["promoted"] = True
                break


def parse_property_doc(node: NodeData) -> tuple[Optional[str], bool, bool, Optional[str]]:
    """Parse property documentation for visibility, readonly, static, type.

    SCIP documentation for properties looks like:
        ```php\\npublic string $customerEmail\\n```
        ```php\\nprivate static array $sentEmails = []\\n```
        ```php\\nprivate readonly \\App\\Service\\CustomerService $customerService\\n```

    Returns:
        (visibility, readonly, static, scalar_type)
    """
    visibility = None
    readonly = False
    static = False
    doc_type = None

    if not node.documentation:
        return visibility, readonly, static, doc_type

    for doc in node.documentation:
        clean = doc.replace("```php", "").replace("```", "").strip()
        if not clean:
            continue
        # Only look at lines that contain the property name
        for line in clean.split("\n"):
            line = line.strip()
            if node.name not in line:
                continue
            # Extract visibility
            if line.startswith("public "):
                visibility = "public"
            elif line.startswith("protected "):
                visibility = "protected"
            elif line.startswith("private "):
                visibility = "private"
            # Check modifiers
            if " readonly " in line or line.startswith("readonly "):
                readonly = True
            if " static " in line or line.startswith("static "):
                static = True
            # Extract type: everything between modifiers and the property name
            # Pattern: [visibility] [static] [readonly] TYPE $name
            match = re.search(
                r'(?:public|protected|private)?\s*(?:static\s+)?(?:readonly\s+)?(\S+)\s+\$',
                line
            )
            if match:
                raw_type = match.group(1)
                # Skip if the "type" is just a modifier word
                if raw_type not in ("public", "protected", "private", "static", "readonly"):
                    # Clean up namespace prefix
                    if raw_type.startswith("\\"):
                        raw_type = raw_type.lstrip("\\")
                    # Use short name (last part)
                    doc_type = raw_type.rsplit("\\", 1)[-1] if "\\" in raw_type else raw_type
            break  # Only need first matching doc
        if visibility or doc_type:
            break

    return visibility, readonly, static, doc_type


def build_argument_definition(index: "SoTIndex", node_id: str, node: NodeData, info: DefinitionInfo):
    """Populate definition for Argument nodes."""
    type_edges = index.outgoing[node_id].get("type_hint", [])
    if type_edges:
        type_node = index.nodes.get(type_edges[0].target)
        if type_node:
            info.return_type = {"fqn": type_node.fqn, "name": type_node.name}


def build_value_definition(index: "SoTIndex", node_id: str, node: NodeData, info: DefinitionInfo):
    """Populate definition for Value nodes with data flow metadata.

    Adds value_kind (local/parameter/result/literal/constant), type
    resolution via type_of edges, and source resolution via
    assigned_from -> produces -> Call target chain.
    """
    # value_kind: local, parameter, result, literal, constant
    info.value_kind = node.value_kind

    # Type resolution via type_of edges (supports union types)
    type_ids = index.get_type_of_all(node_id)
    if type_ids:
        type_names = []
        first_type_node = None
        for tid in type_ids:
            tnode = index.nodes.get(tid)
            if tnode:
                type_names.append(tnode.name)
                if first_type_node is None:
                    first_type_node = tnode
        if type_names and first_type_node:
            info.type_info = {
                "fqn": first_type_node.fqn if len(type_ids) == 1 else "|".join(
                    index.nodes[tid].fqn for tid in type_ids if tid in index.nodes
                ),
                "name": "|".join(type_names),
            }

    # Source resolution: assigned_from -> produces chain
    assigned_from_id = index.get_assigned_from(node_id)
    if assigned_from_id:
        assigned_from_node = index.nodes.get(assigned_from_id)

        # Check if assigned_from points to a Property (promoted constructor param)
        if assigned_from_node and assigned_from_node.kind == "Property":
            info.source = {
                "call_fqn": None,
                "method_fqn": assigned_from_node.fqn,
                "method_name": f"promotes to {assigned_from_node.fqn}",
                "file": assigned_from_node.file,
                "line": assigned_from_node.start_line,
            }
        else:
            # Follow to the Call that produced the source Value
            source_call_id = index.get_source_call(assigned_from_id)
            if source_call_id:
                call_node = index.nodes.get(source_call_id)
                if call_node:
                    # Find the method being called
                    call_target_id = index.get_call_target(source_call_id)
                    if call_target_id:
                        target = index.nodes.get(call_target_id)
                        if target:
                            method_display = target.name
                            if target.kind in ("Method", "Function"):
                                method_display = f"{target.name}()"
                            info.source = {
                                "call_fqn": call_node.fqn,
                                "method_fqn": target.fqn,
                                "method_name": method_display,
                                "file": call_node.file,
                                "line": call_node.start_line,
                            }
    elif node.value_kind == "result":
        # For result values: source is the producing Call directly
        source_call_id = index.get_source_call(node_id)
        if source_call_id:
            call_node = index.nodes.get(source_call_id)
            if call_node:
                call_target_id = index.get_call_target(source_call_id)
                if call_target_id:
                    target = index.nodes.get(call_target_id)
                    if target:
                        method_display = target.name
                        if target.kind in ("Method", "Function"):
                            method_display = f"{target.name}()"
                        info.source = {
                            "call_fqn": call_node.fqn,
                            "method_fqn": target.fqn,
                            "method_name": method_display,
                            "file": call_node.file,
                            "line": call_node.start_line,
                        }

    # Scope: resolve containing method/function via containment hierarchy
    scope_id = get_containing_scope(index, node_id)
    if scope_id:
        scope_node = index.nodes.get(scope_id)
        if scope_node and not info.declared_in:
            info.declared_in = {
                "fqn": scope_node.fqn,
                "kind": scope_node.kind,
                "file": scope_node.file,
                "line": scope_node.start_line,
            }
