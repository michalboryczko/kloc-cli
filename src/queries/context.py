"""Context query with BFS depth expansion and tree structure."""

from typing import Optional, TYPE_CHECKING

from ..models import ContextResult, ContextEntry, MemberRef, InheritEntry, OverrideEntry, NodeData, ArgumentInfo, DefinitionInfo
from ..models.edge import EdgeData
from .base import Query

if TYPE_CHECKING:
    from ..graph import SoTIndex


# =============================================================================
# Depth Chaining Rules (R8)
# =============================================================================
# Only these reference types represent actual call/data flow relationships
# and should be followed when expanding USED BY depth N -> N+1.
# Structural/declarative references (type_hint, extends, implements, use_trait)
# are leaf nodes -- they do not imply that callers of the source are callers
# of the target.
CHAINABLE_REFERENCE_TYPES = {"method_call", "property_access", "instantiation", "static_call"}


# =============================================================================
# Graph-based Access Chain Building
# =============================================================================

def build_access_chain(index: "SoTIndex", call_node_id: str, max_depth: int = 10) -> Optional[str]:
    """Build access chain string by traversing receiver edges in the graph.

    For a call like `$this->orderRepository->save()`, traverses receiver edges
    to build the chain "$this->orderRepository".

    Args:
        index: The SoT index with graph data.
        call_node_id: ID of the Call node.
        max_depth: Maximum traversal depth to prevent infinite loops.

    Returns:
        Access chain string like "$this->orderRepository" or None if no receiver.
    """
    call_node = index.nodes.get(call_node_id)
    if not call_node or call_node.kind != "Call":
        return None

    receiver_id = index.get_receiver(call_node_id)
    if not receiver_id:
        return None  # Static call or constructor

    return _build_chain_from_value(index, receiver_id, max_depth)


def _build_chain_from_value(index: "SoTIndex", value_id: str, max_depth: int) -> str:
    """Build chain by following value references.

    Args:
        index: The SoT index.
        value_id: Starting value node ID.
        max_depth: Maximum recursion depth.

    Returns:
        Chain string like "$this->repo" or "$param" or "?".
    """
    if max_depth <= 0:
        return "?"

    value_node = index.nodes.get(value_id)
    if not value_node or value_node.kind != "Value":
        return "?"

    value_kind = value_node.value_kind

    if value_kind == "parameter":
        # Return the parameter name
        return value_node.name

    if value_kind == "local":
        # Return the local variable name
        return value_node.name

    if value_kind == "result":
        # Result of a call - follow to source call
        source_call_id = index.get_source_call(value_id)
        if source_call_id:
            source_call = index.nodes.get(source_call_id)
            if source_call and source_call.kind == "Call":
                # Get the method/property name being accessed
                target_id = index.get_call_target(source_call_id)
                target_node = index.nodes.get(target_id) if target_id else None
                member_name = target_node.name if target_node else "?"
                # Strip $ from property names if present
                if member_name.startswith("$"):
                    member_name = member_name[1:]

                # For property access, format as chain
                if source_call.call_kind == "access":
                    # Recurse to get receiver chain
                    receiver_id = index.get_receiver(source_call_id)
                    if receiver_id:
                        receiver_chain = _build_chain_from_value(index, receiver_id, max_depth - 1)
                        return f"{receiver_chain}->{member_name}"
                    # No receiver - this is $this-> access (implicit receiver in PHP)
                    return f"$this->{member_name}"

                # For method calls, show as method()
                if source_call.call_kind in ("method", "method_static"):
                    receiver_id = index.get_receiver(source_call_id)
                    if receiver_id:
                        receiver_chain = _build_chain_from_value(index, receiver_id, max_depth - 1)
                        return f"{receiver_chain}->{member_name}()"
                    # No receiver - this is $this-> method call
                    return f"$this->{member_name}()"

        return "?"

    if value_kind == "literal":
        return "(literal)"

    if value_kind == "constant":
        return value_node.name

    return "?"


def get_reference_type_from_call(index: "SoTIndex", call_node_id: str) -> str:
    """Get reference type from a Call node's call_kind.

    Maps call_kind to human-readable reference types.
    """
    call_node = index.nodes.get(call_node_id)
    if not call_node or call_node.kind != "Call":
        return "unknown"

    kind_map = {
        "method": "method_call",
        "method_static": "static_call",
        "constructor": "instantiation",
        "access": "property_access",
        "access_static": "static_property",
        "function": "function_call",
    }
    return kind_map.get(call_node.call_kind or "", "unknown")


def _call_matches_target(index: "SoTIndex", call_id: str, target_id: str) -> bool:
    """Check if a Call node's callee matches the expected usage target.

    Handles the special case of constructor calls: when `new Foo()` produces
    a Call node whose callee is `Foo::__construct()`, and the uses edge target
    is the `Foo` class itself, the match is accepted because the constructor
    call represents an instantiation of that class.

    Args:
        index: The SoT index.
        call_id: ID of the Call node to check.
        target_id: Expected target node ID from the uses edge.

    Returns:
        True if the Call node's callee matches the target.
    """
    call_target = index.get_call_target(call_id)
    if call_target == target_id:
        return True

    # Constructor special case: Call targets __construct() but uses edge
    # targets the Class node itself. Accept if the constructor's containing
    # class matches the target.
    call_node = index.nodes.get(call_id)
    if call_node and call_node.call_kind == "constructor" and call_target:
        constructor_class = index.get_contains_parent(call_target)
        if constructor_class == target_id:
            return True

    return False


def find_call_for_usage(index: "SoTIndex", source_id: str, target_id: str, file: Optional[str], line: Optional[int]) -> Optional[str]:
    """Find a Call node that matches a usage edge's location.

    Args:
        index: The SoT index.
        source_id: Source node ID of the usage.
        target_id: Target node ID of the usage.
        file: File path from the edge location.
        line: Line number from the edge location (0-based).

    Returns:
        Call node ID if found, None otherwise.
    """
    # Get all calls that target this node
    calls = index.get_calls_to(target_id)

    # Also check if there are Call nodes contained by the source
    source_children = index.get_contains_children(source_id)
    call_children = [c for c in source_children if index.nodes.get(c) and index.nodes[c].kind == "Call"]

    # Filter by location if provided
    if file and line is not None:
        for call_id in calls + call_children:
            call_node = index.nodes.get(call_id)
            if call_node and call_node.file == file:
                if call_node.range:
                    call_line = call_node.range.get("start_line", -1)
                    if call_line == line:
                        # Verify the Call node's callee matches the usage target
                        # to prevent returning a wrong Call (e.g., constructor
                        # matched for a static property access on the same line)
                        if _call_matches_target(index, call_id, target_id):
                            return call_id

        # Constructor fallback: search call_children for constructor Call nodes
        # whose callee's containing class matches the target_id. This handles
        # the case where get_calls_to(target_id) returns nothing because
        # constructor Calls target __construct(), not the Class itself.
        # Allow +/- 1 line tolerance because `uses` edge location may refer
        # to the class name token while the Call node range covers `new X(...)`.
        for call_id in call_children:
            call_node = index.nodes.get(call_id)
            if (call_node and call_node.call_kind == "constructor"
                    and call_node.file == file):
                if call_node.range:
                    call_line = call_node.range.get("start_line", -1)
                    if abs(call_line - line) <= 1:
                        if _call_matches_target(index, call_id, target_id):
                            return call_id

    # If no location match, try to find any call from source to target
    for call_id in calls:
        call_node = index.nodes.get(call_id)
        if call_node:
            # Check if this call is contained in the source
            container_id = index.get_contains_parent(call_id)
            if container_id == source_id:
                # Verify the Call node's callee matches the usage target
                if _call_matches_target(index, call_id, target_id):
                    return call_id

    # Constructor fallback without location: if target is a Class, search the
    # source's Call children for constructor calls whose callee's containing
    # class matches the target. This handles cases where get_calls_to(Class)
    # misses constructor calls (which target __construct, not the Class node).
    target_node = index.nodes.get(target_id)
    if target_node and target_node.kind in ("Class", "Interface", "Trait", "Enum"):
        for call_id in call_children:
            call_node = index.nodes.get(call_id)
            if call_node and call_node.call_kind == "constructor":
                if _call_matches_target(index, call_id, target_id):
                    return call_id

    return None


def get_containing_scope(index: "SoTIndex", call_node_id: str) -> Optional[str]:
    """Get the containing method/function for a Call node.

    Traverses the containment hierarchy to find the Method or Function
    that contains this call.

    Args:
        index: The SoT index.
        call_node_id: ID of the Call node.

    Returns:
        Node ID of the containing Method/Function, or None if not found.
    """
    current_id = call_node_id
    max_depth = 10  # Prevent infinite loops

    for _ in range(max_depth):
        parent_id = index.get_contains_parent(current_id)
        if not parent_id:
            return None

        parent_node = index.nodes.get(parent_id)
        if not parent_node:
            return None

        if parent_node.kind in ("Method", "Function"):
            return parent_id

        # Continue up the hierarchy
        current_id = parent_id

    return None


def _is_import_reference(entry, index: "SoTIndex") -> bool:
    """Identify whether a context entry represents a PHP import/use statement (R1).

    An import reference is identified by:
    - The source node is a File node (kind == "File")
    - The reference type is "type_hint"
    - This indicates a file-level `use App\\Foo\\Bar;` declaration

    Non-import type hints (constructor parameters, method return types) come from
    Method/Function sources, not File sources, so they are NOT filtered.

    Args:
        entry: A ContextEntry to check.
        index: The SoT index for node lookups.

    Returns:
        True if the entry represents an import/use statement.
    """
    if not entry.member_ref:
        return False

    if entry.member_ref.reference_type != "type_hint":
        return False

    # Check if the source node is a File node
    source_node = index.nodes.get(entry.node_id)
    if source_node and source_node.kind == "File":
        return True

    return False


def resolve_access_chain_symbol(index: "SoTIndex", call_node_id: str) -> Optional[str]:
    """Resolve the property FQN from a Call node's receiver chain (R4).

    For a call like `$this->orderService->createOrder()`, the receiver is a
    property access to `$orderService`. This function finds the FQN of that
    intermediate property (e.g., `App\\Controller\\OrderController::$orderService`).

    Args:
        index: The SoT index.
        call_node_id: ID of the Call node.

    Returns:
        Property FQN string if resolved, None otherwise.
    """
    call_node = index.nodes.get(call_node_id)
    if not call_node or call_node.kind != "Call":
        return None

    receiver_id = index.get_receiver(call_node_id)
    if not receiver_id:
        return None

    receiver_node = index.nodes.get(receiver_id)
    if not receiver_node or receiver_node.kind != "Value":
        return None

    # If the receiver is the result of a property access call, follow to the property
    if receiver_node.value_kind == "result":
        source_call_id = index.get_source_call(receiver_id)
        if source_call_id:
            source_call = index.nodes.get(source_call_id)
            if source_call and source_call.kind == "Call" and source_call.call_kind == "access":
                # This is a property access -- get the target property
                target_id = index.get_call_target(source_call_id)
                if target_id:
                    target_node = index.nodes.get(target_id)
                    if target_node and target_node.kind == "Property":
                        return target_node.fqn

    return None


def _infer_reference_type(edge: EdgeData, target_node: Optional[NodeData], index: Optional["SoTIndex"] = None) -> str:
    """Infer reference type from edge type and target node kind.

    Without calls.json, we can only infer based on sot.json edge metadata.
    This provides a best-effort classification that may be ambiguous in some cases.

    When an index is provided, type_hint references to Class/Interface/Trait/Enum
    targets are further distinguished by checking the source node kind:
    - Argument source -> parameter_type
    - Method/Function source -> return_type
    - Property source -> property_type
    - Other/unknown -> type_hint (fallback)

    Reference Type Inference Rules:
    | Edge Type | Target Kind | Inferred Reference Type |
    |-----------|-------------|------------------------|
    | extends   | Class       | extends                |
    | implements| Interface   | implements             |
    | uses_trait| Trait       | uses_trait             |
    | uses      | Method      | method_call (could be static) |
    | uses      | Property    | property_access or type_hint |
    | uses      | Class       | parameter_type / return_type / property_type / type_hint |

    Args:
        edge: The edge data from sot.json
        target_node: The target node of the edge (if resolved)
        index: Optional SoT index for source node lookup (enables param/return type distinction)

    Returns:
        A reference type string (e.g., "method_call", "parameter_type", "extends")
    """
    # Direct edge type mappings
    if edge.type == "extends":
        return "extends"
    if edge.type == "implements":
        return "implements"
    if edge.type == "uses_trait":
        return "uses_trait"

    # For 'uses' edges, infer from target node kind
    if edge.type == "uses" and target_node:
        kind = target_node.kind
        if kind == "Method":
            # Could be method_call or static_call - can't distinguish without calls.json
            return "method_call"
        if kind == "Property":
            # Could be property_access or type_hint for property declarations
            return "property_access"
        if kind in ("Class", "Interface", "Trait", "Enum"):
            # Distinguish parameter_type / return_type / property_type when index available.
            # For 'uses' edges, the source is typically the containing Method/Class/File.
            # To determine if a Class reference is a param type vs return type, we check
            # the type_hint edges: if an Argument of the source method has a type_hint
            # to this target, it's parameter_type; if the Method itself has a type_hint
            # to the target, it's return_type; if a Property has a type_hint, it's property_type.
            if index is not None:
                source_node = index.nodes.get(edge.source)
                if source_node:
                    if source_node.kind == "Argument":
                        return "parameter_type"
                    if source_node.kind == "Property":
                        return "property_type"
                    if source_node.kind in ("Method", "Function"):
                        # Check type_hint edges to distinguish param vs return type.
                        # First check if any Argument child of this method has a
                        # type_hint edge to the target (parameter_type).
                        target_id = edge.target
                        method_id = edge.source
                        has_param_type_hint = False
                        has_return_type_hint = False
                        for child_id in index.get_contains_children(method_id):
                            child = index.nodes.get(child_id)
                            if child and child.kind == "Argument":
                                for th_edge in index.outgoing[child_id].get("type_hint", []):
                                    if th_edge.target == target_id:
                                        has_param_type_hint = True
                                        break
                            if has_param_type_hint:
                                break
                        # Check if the method itself has a type_hint to the target (return_type)
                        for th_edge in index.outgoing[method_id].get("type_hint", []):
                            if th_edge.target == target_id:
                                has_return_type_hint = True
                                break
                        if has_param_type_hint:
                            return "parameter_type"
                        if has_return_type_hint:
                            return "return_type"
                        # Constructor promotion fix: when source is __construct()
                        # and no Argument child matched, check the parent class's
                        # Property children for a type_hint edge to the target.
                        # Promoted constructor params create Property nodes with
                        # type_hint edges but no Argument nodes.
                        if source_node.name == "__construct":
                            containing_class_id = index.get_contains_parent(method_id)
                            if containing_class_id:
                                for child_id in index.get_contains_children(containing_class_id):
                                    child = index.nodes.get(child_id)
                                    if child and child.kind == "Property":
                                        for th_edge in index.outgoing[child_id].get("type_hint", []):
                                            if th_edge.target == target_id:
                                                return "property_type"
                    if source_node.kind in ("Class", "Interface", "Trait", "Enum"):
                        # Class-level query: check if any Property child has a
                        # type_hint edge to the target (property_type).
                        target_id = edge.target
                        class_id = edge.source
                        for child_id in index.get_contains_children(class_id):
                            child = index.nodes.get(child_id)
                            if child and child.kind == "Property":
                                for th_edge in index.outgoing[child_id].get("type_hint", []):
                                    if th_edge.target == target_id:
                                        return "property_type"
            return "type_hint"
        if kind == "Constant":
            return "constant_access"
        if kind == "Function":
            return "function_call"
        if kind == "Argument":
            # Usage of a method/function argument (parameter reference)
            return "argument_ref"
        if kind == "Variable":
            # Local variable usage
            return "variable_ref"

    # Fallback for unknown edge types or missing target node
    return "uses"


class ContextQuery(Query[ContextResult]):
    """Get combined usages and dependencies with depth expansion.

    Returns proper nested tree structures for both directions.
    Optionally includes polymorphic analysis:
    - USES direction: Shows implementations of interfaces and overriding methods
    - USED BY direction: Includes usages of interface methods that concrete methods implement
    """

    # Kinds that can have implementations (descendants)
    INHERITABLE_KINDS = {"Class", "Interface", "Trait", "Enum"}

    def execute(
        self, node_id: str, depth: int = 1, limit: int = 100, include_impl: bool = False,
        direct_only: bool = False, with_imports: bool = False
    ) -> ContextResult:
        """Execute context query with BFS tree building.

        Args:
            node_id: Node ID to get context for.
            depth: BFS depth for expansion.
            limit: Maximum results per direction.
            include_impl: If True, enables polymorphic analysis:
                         - USES: attaches implementations/overrides for interfaces/methods
                         - USED BY: includes usages of interface methods that concrete methods implement
            direct_only: If True, USED BY shows only direct references to the symbol itself,
                        excluding usages that only reference its members.
            with_imports: If True, include PHP import/use statements in USED BY output.
                         By default (False), import statements are hidden.

        Returns:
            ContextResult with tree structures for used_by and uses.
        """
        target_node = self.index.nodes.get(node_id)
        if not target_node:
            raise ValueError(f"Node not found: {node_id}")

        definition = self._build_definition(node_id)
        used_by = self._build_incoming_tree(
            node_id, depth, limit, include_impl, direct_only, with_imports
        )
        uses = self._build_outgoing_tree(node_id, depth, limit, include_impl)

        return ContextResult(
            target=target_node,
            max_depth=depth,
            used_by=used_by,
            uses=uses,
            definition=definition,
        )

    @staticmethod
    def _member_display_name(node: NodeData) -> str:
        """Format a short member display name: '$prop', 'method()', 'CONST'."""
        if node.kind == "Method" or node.kind == "Function":
            return f"{node.name}()"
        if node.kind == "Property":
            name = node.name
            return name if name.startswith("$") else f"${name}"
        return node.name

    def _build_definition(self, node_id: str) -> DefinitionInfo:
        """Build definition metadata for a symbol.

        Gathers structural information about the symbol: signature, typed
        arguments, return type, containing class, properties, methods,
        and inheritance relationships.

        Args:
            node_id: The node to build definition for.

        Returns:
            DefinitionInfo with symbol metadata.
        """
        node = self.index.nodes.get(node_id)
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
        parent_id = self.index.get_contains_parent(node_id)
        if parent_id:
            parent_node = self.index.nodes.get(parent_id)
            if parent_node:
                info.declared_in = {
                    "fqn": parent_node.fqn,
                    "kind": parent_node.kind,
                    "file": parent_node.file,
                    "line": parent_node.start_line,
                }

        if node.kind in ("Method", "Function"):
            self._build_method_definition(node_id, node, info)
        elif node.kind in ("Class", "Interface", "Trait", "Enum"):
            self._build_class_definition(node_id, node, info)
        elif node.kind == "Property":
            self._build_property_definition(node_id, node, info)
        elif node.kind == "Argument":
            self._build_argument_definition(node_id, node, info)
        elif node.kind == "Value":
            self._build_value_definition(node_id, node, info)

        return info

    def _build_method_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Method/Function nodes."""
        children = self.index.get_contains_children(node_id)

        # Collect typed arguments
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Argument":
                arg_dict: dict = {"name": child.name, "position": None}
                # Resolve type from type_hint edges
                type_edges = self.index.outgoing[child_id].get("type_hint", [])
                if type_edges:
                    type_node = self.index.nodes.get(type_edges[0].target)
                    if type_node:
                        arg_dict["type"] = type_node.name
                info.arguments.append(arg_dict)

        # Resolve return type from type_hint edges on the method itself
        type_edges = self.index.outgoing[node_id].get("type_hint", [])
        if type_edges:
            type_node = self.index.nodes.get(type_edges[0].target)
            if type_node:
                info.return_type = {"fqn": type_node.fqn, "name": type_node.name}

    def _build_class_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Class/Interface/Trait/Enum nodes.

        For Classes: adds properties with metadata (type, visibility, promoted,
        readonly, static), methods with tags ([override], [abstract], [inherited]),
        constructor_deps for promoted constructor parameters, extends, implements.

        For Interfaces: delegates to _build_interface_definition.
        """
        if node.kind == "Interface":
            self._build_interface_definition(node_id, node, info)
            return

        children = self.index.get_contains_children(node_id)

        for child_id in children:
            child = self.index.nodes.get(child_id)
            if not child:
                continue

            if child.kind == "Property":
                prop_dict: dict = {"name": child.name}
                # Type from type_hint edges
                type_edges = self.index.outgoing[child_id].get("type_hint", [])
                if type_edges:
                    type_node = self.index.nodes.get(type_edges[0].target)
                    if type_node:
                        prop_dict["type"] = type_node.name

                # Parse property metadata from documentation
                vis, readonly, static, doc_type = self._parse_property_doc(child)
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
                assigned_edges = self.index.outgoing[child_id].get("assigned_from", [])
                for edge in assigned_edges:
                    source_node = self.index.nodes.get(edge.target)
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
                override_parent = self.index.get_overrides_parent(child_id)
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
            child = self.index.nodes.get(child_id)
            if not child or child.kind != "Property":
                continue
            # Only promoted properties
            assigned_edges = self.index.outgoing[child_id].get("assigned_from", [])
            for edge in assigned_edges:
                source_node = self.index.nodes.get(edge.target)
                if source_node and source_node.kind == "Value" and source_node.value_kind == "parameter":
                    if "__construct()" in source_node.fqn:
                        dep = {"name": child.name}
                        # Get type from type_hint edges on the property
                        type_edges = self.index.outgoing[child_id].get("type_hint", [])
                        if type_edges:
                            type_node = self.index.nodes.get(type_edges[0].target)
                            if type_node:
                                dep["type"] = type_node.name
                        else:
                            # Try scalar type from docs
                            _, _, _, doc_type = self._parse_property_doc(child)
                            if doc_type:
                                dep["type"] = doc_type
                        info.constructor_deps.append(dep)
                        break

        # Inheritance: extends
        extends_id = self.index.get_extends_parent(node_id)
        if extends_id:
            extends_node = self.index.nodes.get(extends_id)
            if extends_node:
                info.extends = extends_node.fqn

        # Inheritance: implements
        impl_ids = self.index.get_implements(node_id)
        for impl_id in impl_ids:
            impl_node = self.index.nodes.get(impl_id)
            if impl_node:
                info.implements.append(impl_node.fqn)

        # Traits: uses_trait
        trait_edges = self.index.outgoing[node_id].get("uses_trait", [])
        for edge in trait_edges:
            trait_node = self.index.nodes.get(edge.target)
            if trait_node:
                info.uses_traits.append(trait_node.fqn)

    def _build_interface_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Interface nodes.

        Shows method signatures only (no properties, no implements).
        Shows extends if interface extends another interface.
        """
        children = self.index.get_contains_children(node_id)

        for child_id in children:
            child = self.index.nodes.get(child_id)
            if not child:
                continue

            if child.kind == "Method":
                method_dict: dict = {"name": child.name}
                if child.signature:
                    method_dict["signature"] = child.signature
                info.methods.append(method_dict)

        # Interface extends (interface extending interface)
        extends_id = self.index.get_extends_parent(node_id)
        if extends_id:
            extends_node = self.index.nodes.get(extends_id)
            if extends_node:
                info.extends = extends_node.fqn

    def _build_property_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Property nodes.

        Extracts: type (from type_hint edges or documentation), visibility,
        promoted (detected via assigned_from -> Value(parameter) in __construct),
        readonly, static — all parsed from SCIP documentation strings.
        """
        # Type from type_hint edges (class types)
        type_edges = self.index.outgoing[node_id].get("type_hint", [])
        if type_edges:
            type_node = self.index.nodes.get(type_edges[0].target)
            if type_node:
                info.return_type = {"fqn": type_node.fqn, "name": type_node.name}

        # Parse visibility, readonly, static, and scalar type from documentation
        vis, readonly, static, doc_type = self._parse_property_doc(node)
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
            parent_id = self.index.get_contains_parent(node_id)
            if parent_id:
                parent_node = self.index.nodes.get(parent_id)
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
        assigned_edges = self.index.outgoing[node_id].get("assigned_from", [])
        for edge in assigned_edges:
            source_node = self.index.nodes.get(edge.target)
            if source_node and source_node.kind == "Value" and source_node.value_kind == "parameter":
                if "__construct()" in source_node.fqn:
                    if not info.return_type:
                        info.return_type = {}
                    info.return_type["promoted"] = True
                    break

    @staticmethod
    def _parse_property_doc(node: NodeData) -> tuple[Optional[str], bool, bool, Optional[str]]:
        """Parse property documentation for visibility, readonly, static, type.

        SCIP documentation for properties looks like:
            ```php\\npublic string $customerEmail\\n```
            ```php\\nprivate static array $sentEmails = []\\n```
            ```php\\nprivate readonly \\App\\Service\\CustomerService $customerService\\n```

        Returns:
            (visibility, readonly, static, scalar_type)
        """
        import re
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

    def _build_argument_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Argument nodes."""
        type_edges = self.index.outgoing[node_id].get("type_hint", [])
        if type_edges:
            type_node = self.index.nodes.get(type_edges[0].target)
            if type_node:
                info.return_type = {"fqn": type_node.fqn, "name": type_node.name}

    def _build_value_definition(self, node_id: str, node: NodeData, info: DefinitionInfo):
        """Populate definition for Value nodes with data flow metadata.

        Adds value_kind (local/parameter/result/literal/constant), type
        resolution via type_of edges, and source resolution via
        assigned_from -> produces -> Call target chain.
        """
        # value_kind: local, parameter, result, literal, constant
        info.value_kind = node.value_kind

        # Type resolution via type_of edges (supports union types)
        type_ids = self.index.get_type_of_all(node_id)
        if type_ids:
            type_names = []
            first_type_node = None
            for tid in type_ids:
                tnode = self.index.nodes.get(tid)
                if tnode:
                    type_names.append(tnode.name)
                    if first_type_node is None:
                        first_type_node = tnode
            if type_names and first_type_node:
                info.type_info = {
                    "fqn": first_type_node.fqn if len(type_ids) == 1 else "|".join(
                        self.index.nodes[tid].fqn for tid in type_ids if tid in self.index.nodes
                    ),
                    "name": "|".join(type_names),
                }

        # Source resolution: assigned_from -> produces chain
        assigned_from_id = self.index.get_assigned_from(node_id)
        if assigned_from_id:
            assigned_from_node = self.index.nodes.get(assigned_from_id)

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
                source_call_id = self.index.get_source_call(assigned_from_id)
                if source_call_id:
                    call_node = self.index.nodes.get(source_call_id)
                    if call_node:
                        # Find the method being called
                        call_target_id = self.index.get_call_target(source_call_id)
                        if call_target_id:
                            target = self.index.nodes.get(call_target_id)
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
            source_call_id = self.index.get_source_call(node_id)
            if source_call_id:
                call_node = self.index.nodes.get(source_call_id)
                if call_node:
                    call_target_id = self.index.get_call_target(source_call_id)
                    if call_target_id:
                        target = self.index.nodes.get(call_target_id)
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
        scope_id = get_containing_scope(self.index, node_id)
        if scope_id:
            scope_node = self.index.nodes.get(scope_id)
            if scope_node and not info.declared_in:
                info.declared_in = {
                    "fqn": scope_node.fqn,
                    "kind": scope_node.kind,
                    "file": scope_node.file,
                    "line": scope_node.start_line,
                }

    def _build_incoming_tree(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False,
        direct_only: bool = False, with_imports: bool = False
    ) -> list[ContextEntry]:
        """Build nested tree for incoming usages (used_by).

        Each usage edge becomes its own branch. When a source references multiple
        members, each member reference is a separate entry showing the specific
        member and reference location. Depth expansion (children) is only added
        to the first entry per source to avoid duplication.

        If include_impl is True and the target is a method that implements an interface
        method, also include usages of that interface method grouped under the interface.

        If direct_only is True, only show usages that directly reference the current node,
        excluding usages that only reference its members.

        Reference types and access chains are resolved from the unified graph's
        Call/Value nodes when available.

        Depth chaining rules (R8):
        - Only chainable reference types (method_call, property_access, instantiation,
          static_call) expand to depth N+1. Non-chainable types (type_hint, extends,
          implements, use_trait) are always leaf nodes.

        Recursive USED BY depth (R7):
        - For each chainable entry at depth N, we resolve the containing method of
          the source reference, then find callers of that method at depth N+1.
          This correctly chains through the call graph: if Controller calls Service
          which calls Repository, querying Repository shows Service at depth 1 and
          Controller at depth 2.

        External-only filtering (R3):
        - For class-level queries, internal self-references (own methods accessing
          own properties) are filtered out.

        Import filtering (R1):
        - By default, PHP import/use statements are hidden. The with_imports
          parameter controls this.

        FQN resolution (R6):
        - File node sources are resolved to their containing class FQN.

        Sort order (R2):
        - Entries at each depth level are sorted by (file path, line number).
        """
        # Determine if the start node is a class-level query (for R3 filtering)
        start_node = self.index.nodes.get(start_id)

        # Value nodes: use dedicated consumer chain traversal instead of
        # generic uses-edge traversal. Value nodes have no incoming 'uses'
        # edges — their consumers are tracked through receiver and argument edges.
        if start_node and start_node.kind == "Value":
            return self._build_value_consumer_chain(start_id, 1, max_depth, limit, visited=set())

        # ISSUE-F: Property nodes — trace who reads this property across methods
        if start_node and start_node.kind == "Property":
            return self._build_property_used_by(start_id, 1, max_depth, limit)

        # ISSUE-B: Class nodes — grouped, sorted, deduped USED BY
        if start_node and start_node.kind == "Class":
            return self._build_class_used_by(start_id, max_depth, limit, include_impl)

        # ISSUE-D: Interface nodes — implementors + injection points USED BY
        if start_node and start_node.kind == "Interface":
            return self._build_interface_used_by(start_id, max_depth, limit, include_impl)

        is_class_query = start_node and start_node.kind in ("Class", "Interface", "Trait", "Enum")

        # Global visited set prevents the same source from appearing at multiple depths
        visited = {start_id}
        count = [0]  # Tracks unique sources for limit

        def build_tree(current_id: str, current_depth: int,
                       branch_visited: set[str] | None = None) -> list[ContextEntry]:
            if current_depth > max_depth or count[0] >= limit:
                return []

            # Per-branch visited set for cycle prevention in recursive depth (R7)
            if branch_visited is None:
                branch_visited = set()

            # --- Pass 1: collect all entries, claim sources in visited ---
            # This prevents deeper expansions from "stealing" sources that
            # belong at the current depth.
            entries = []
            source_groups = self.index.get_usages_grouped(current_id)

            for source_id, edges in source_groups.items():
                if source_id in visited:
                    continue

                # Separate direct edges (target = current node) from member edges
                direct_edges = [e for e in edges if e.target == current_id]
                member_edges = [e for e in edges if e.target != current_id]

                # In direct_only mode, skip sources that only have member edges
                if direct_only and not direct_edges:
                    continue

                # R3: For class queries, filter out internal self-references
                if is_class_query and current_depth == 1:
                    if self._is_internal_reference(source_id, start_id):
                        continue

                if count[0] >= limit:
                    break
                count[0] += 1
                visited.add(source_id)

                source_node = self.index.nodes.get(source_id)

                # Sort member edges by line for execution flow order
                member_edges.sort(
                    key=lambda e: e.location.get("line", 0) if e.location else 0
                )

                # Collect all edges to emit: direct edges first, then member edges
                # In direct_only mode, skip member edges entirely
                all_edges = direct_edges + ([] if direct_only else member_edges)

                for edge in all_edges:
                    is_member = edge.target != current_id

                    # Location from the edge itself
                    if edge.location:
                        file = edge.location.get("file")
                        line = edge.location.get("line")
                    elif source_node:
                        file = source_node.file
                        line = source_node.start_line
                    else:
                        file = None
                        line = None

                    # Build member_ref for member edges
                    member_ref = None
                    access_chain = None
                    reference_type = None
                    access_chain_symbol = None

                    if is_member:
                        target_node = self.index.nodes.get(edge.target)
                        if target_node:
                            # Try to find a Call node in the graph for authoritative info
                            call_node_id = find_call_for_usage(
                                self.index, source_id, edge.target, file, line
                            )

                            # Get reference type and access chain from Call node if found
                            if call_node_id:
                                reference_type = get_reference_type_from_call(self.index, call_node_id)
                                access_chain = build_access_chain(self.index, call_node_id)
                                # R4: Resolve access chain property FQN
                                access_chain_symbol = resolve_access_chain_symbol(self.index, call_node_id)
                            else:
                                # Fall back to inference from edge/node types
                                reference_type = _infer_reference_type(edge, target_node, self.index)

                            member_ref = MemberRef(
                                target_name=self._member_display_name(target_node),
                                target_fqn=target_node.fqn,
                                target_kind=target_node.kind,
                                file=file,
                                line=line,
                                reference_type=reference_type,
                                access_chain=access_chain,
                                access_chain_symbol=access_chain_symbol,
                            )
                    else:
                        # Direct reference to the target node itself
                        target_node = self.index.nodes.get(edge.target)

                        # Try to find a Call node in the graph
                        call_node_id = None
                        if file and line is not None and target_node:
                            call_node_id = find_call_for_usage(
                                self.index, source_id, edge.target, file, line
                            )

                        # Get reference type and access chain from Call node
                        access_chain = None
                        if call_node_id:
                            reference_type = get_reference_type_from_call(self.index, call_node_id)
                            access_chain = build_access_chain(self.index, call_node_id)
                            # R4: Resolve access chain property FQN
                            access_chain_symbol = resolve_access_chain_symbol(self.index, call_node_id)
                        else:
                            # Infer reference type for direct edges (extends, implements, type_hint)
                            reference_type = _infer_reference_type(edge, target_node, self.index)

                        # For direct edges, create a member_ref to hold the reference_type
                        # and access_chain
                        member_ref = MemberRef(
                            target_name="",  # No specific member
                            target_fqn=target_node.fqn if target_node else edge.target,
                            target_kind=target_node.kind if target_node else None,
                            file=file,
                            line=line,
                            reference_type=reference_type,
                            access_chain=access_chain,
                            access_chain_symbol=access_chain_symbol,
                        )

                    # R6: Resolve File node identifiers to their containing class FQN
                    entry_fqn = source_node.fqn if source_node else source_id
                    entry_kind = source_node.kind if source_node else None
                    if source_node and source_node.kind == "File":
                        resolved_class = self.index.resolve_file_to_class(source_id)
                        if resolved_class:
                            resolved_node = self.index.nodes.get(resolved_class)
                            if resolved_node:
                                entry_fqn = resolved_node.fqn
                                entry_kind = resolved_node.kind

                    entry = ContextEntry(
                        depth=current_depth,
                        node_id=source_id,
                        fqn=entry_fqn,
                        kind=entry_kind,
                        file=file,
                        line=line,
                        signature=source_node.signature if source_node else None,
                        children=[],
                        member_ref=member_ref,
                    )
                    entries.append(entry)

            # R1: Filter out import references unless with_imports is True
            if not with_imports:
                entries = [e for e in entries if not _is_import_reference(e, self.index)]

            # R2: Sort entries by (file path, line number) for consistent ordering
            entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

            # --- Pass 2: expand children using R7 recursive depth and R8 chaining rules ---
            # For each chainable entry at depth N, resolve the containing method
            # of the source reference, then find callers of that method at depth N+1.
            if current_depth < max_depth:
                expanded = set()
                for entry in entries:
                    if entry.node_id not in expanded:
                        expanded.add(entry.node_id)

                        # R8: Only expand children for chainable reference types
                        ref_type = entry.member_ref.reference_type if entry.member_ref else None
                        if ref_type not in CHAINABLE_REFERENCE_TYPES:
                            # Non-chainable types (type_hint, extends, implements, etc.)
                            # are leaf nodes -- no further depth expansion
                            continue

                        # R7: Resolve the containing method for recursive USED BY
                        # The source node (entry.node_id) references our target.
                        # To find depth N+1, we need to find callers of the METHOD
                        # that contains the source reference, not callers of the source node itself.
                        containing_method_id = self._resolve_containing_method(entry.node_id)
                        if containing_method_id and containing_method_id not in branch_visited:
                            # Create a new branch_visited set for this branch to prevent cycles
                            child_branch_visited = branch_visited | {containing_method_id}
                            entry.children = build_tree(
                                containing_method_id, current_depth + 1, child_branch_visited
                            )

            return entries

        # Build direct usages first
        direct_entries = build_tree(start_id, 1)

        # If include_impl, also build interface usages grouped under the interface method
        interface_entries = []
        if include_impl:
            interface_method_ids = self._get_interface_method_ids(start_id)
            for iface_id in interface_method_ids:
                if iface_id in visited:
                    continue
                visited.add(iface_id)

                iface_node = self.index.nodes.get(iface_id)
                if not iface_node:
                    continue

                # Build usages of the interface method
                iface_usages = build_tree(iface_id, 1)

                # Only add if there are actual usages
                if iface_usages:
                    # Create an entry for the interface method itself
                    iface_entry = ContextEntry(
                        depth=0,  # Special depth for grouping entry
                        node_id=iface_id,
                        fqn=iface_node.fqn,
                        kind=iface_node.kind,
                        file=iface_node.file,
                        line=iface_node.start_line,
                        signature=iface_node.signature,
                        children=iface_usages,
                        via_interface=True,  # Mark as interface grouping
                    )
                    interface_entries.append(iface_entry)

        return direct_entries + interface_entries

    def _build_value_consumer_chain(
        self, value_id: str, depth: int, max_depth: int, limit: int,
        visited: set | None = None
    ) -> list[ContextEntry]:
        """Build consumer chain for a Value node (USED BY section).

        Finds all Calls that consume this Value — either as a receiver (property
        access like $savedOrder->id) or directly as an argument. Groups property
        accesses by the downstream Call that consumes the accessed property result.

        For receiver edges: each Call that uses this Value as receiver accesses a
        property/method on it. The result of that access may feed into another Call
        as an argument — that consuming Call becomes the USED BY entry, with the
        property access shown as argument mapping.

        For direct argument edges: the Value itself is passed as argument to a Call.

        At depth+1: traces forward into callee body (promoted properties, further usage).
        Cross-method: when Value is passed as argument, crosses into callee to trace
        the matching parameter's consumers recursively (ISSUE-E).

        Args:
            value_id: The Value node ID to find consumers for.
            depth: Current depth level.
            max_depth: Maximum depth to expand.
            limit: Maximum number of entries.
            visited: Set of visited Value IDs for cycle detection.

        Returns:
            List of ContextEntry representing consuming Calls, sorted by line number.
        """
        if depth > max_depth:
            return []

        if visited is None:
            visited = set()
        if value_id in visited:
            return []
        visited.add(value_id)

        entries = []
        count = 0
        seen_calls = set()  # Prevent duplicate entries for same consuming Call

        # === Part 1: Receiver edges (property accesses on this Value) ===
        # Each receiver edge means a Call accesses a property/method on this Value.
        # Group by consuming Call: $savedOrder->id feeds into send() as $to.
        receiver_edges = self.index.incoming[value_id].get("receiver", [])

        # Collect property access info grouped by consuming Call
        # Structure: consumer_call_id -> list of access info dicts
        consumer_groups: dict[str, list[dict]] = {}
        # Track standalone receiver calls (property accesses not consumed as arguments)
        standalone_accesses: list[tuple] = []  # (access_call_id, access_call_node)

        for edge in receiver_edges:
            access_call_id = edge.source  # The Call that accesses property on this Value
            access_call_node = self.index.nodes.get(access_call_id)
            if not access_call_node:
                continue

            # What property/method does this call access?
            target_id = self.index.get_call_target(access_call_id)
            target_node = self.index.nodes.get(target_id) if target_id else None

            # Does this property access produce a result that is used as argument?
            result_id = self.index.get_produces(access_call_id)
            found_consumer = False

            if result_id:
                # Check if result is used as argument in another Call
                arg_edges = self.index.incoming[result_id].get("argument", [])
                for arg_edge in arg_edges:
                    consumer_call_id = arg_edge.source
                    if consumer_call_id not in consumer_groups:
                        consumer_groups[consumer_call_id] = []
                    consumer_groups[consumer_call_id].append({
                        "prop_name": target_node.name if target_node else "?",
                        "prop_fqn": target_node.fqn if target_node else None,
                        "position": arg_edge.position or 0,
                        "expression": arg_edge.expression,
                        "access_call_id": access_call_id,
                        "access_call_line": (
                            access_call_node.range.get("start_line")
                            if access_call_node.range else None
                        ),
                    })
                    found_consumer = True

                # Check if result is assigned to another variable
                if not found_consumer:
                    assigned_edges = self.index.incoming[result_id].get("assigned_from", [])
                    if assigned_edges:
                        found_consumer = True
                        standalone_accesses.append((access_call_id, access_call_node))

            if not found_consumer:
                standalone_accesses.append((access_call_id, access_call_node))

        # Build entries for each consuming Call (grouped property accesses)
        for consumer_call_id, access_infos in consumer_groups.items():
            if count >= limit:
                break
            if consumer_call_id in seen_calls:
                continue
            seen_calls.add(consumer_call_id)

            consumer_call_node = self.index.nodes.get(consumer_call_id)
            if not consumer_call_node:
                continue

            # Find the method being called by the consumer
            consumer_target_id = self.index.get_call_target(consumer_call_id)
            consumer_target = (
                self.index.nodes.get(consumer_target_id) if consumer_target_id else None
            )

            consumer_fqn = consumer_target.fqn if consumer_target else consumer_call_node.fqn
            consumer_kind = consumer_target.kind if consumer_target else consumer_call_node.kind
            consumer_sig = consumer_target.signature if consumer_target else None
            call_line = (
                consumer_call_node.range.get("start_line")
                if consumer_call_node.range else None
            )

            # Build argument info for this consuming Call (reuse existing helper)
            arguments = self._get_argument_info(consumer_call_id)

            # Build member_ref showing the call target
            member_ref = None
            if consumer_target:
                reference_type = get_reference_type_from_call(self.index, consumer_call_id)
                ac, acs, ok, of, ol = self._resolve_receiver_identity(consumer_call_id)
                member_ref = MemberRef(
                    target_name="",
                    target_fqn=consumer_target.fqn,
                    target_kind=consumer_target.kind,
                    file=consumer_call_node.file,
                    line=call_line,
                    reference_type=reference_type,
                    access_chain=ac,
                    access_chain_symbol=acs,
                    on_kind=ok,
                    on_file=of,
                    on_line=ol,
                )

            # Build flat fields for class-level context compatibility
            flat_ref_type = None
            flat_callee = None
            flat_on = None
            flat_on_kind = None
            if consumer_target:
                flat_ref_type = get_reference_type_from_call(self.index, consumer_call_id)
                if consumer_target.kind == "Method":
                    flat_callee = consumer_target.name + "()"
                elif consumer_target.kind == "Property":
                    flat_callee = consumer_target.name if consumer_target.name.startswith("$") else "$" + consumer_target.name
                if member_ref:
                    flat_on = member_ref.access_chain
                    flat_on_kind = member_ref.on_kind
                    # Detect property-based receiver (on_kind None but access_chain_symbol is a Property)
                    if flat_on_kind is None and member_ref.access_chain_symbol:
                        sym_nodes = self.index.resolve_symbol(member_ref.access_chain_symbol)
                        if sym_nodes and sym_nodes[0].kind == "Property":
                            flat_on_kind = "property"

            entry = ContextEntry(
                depth=depth,
                node_id=consumer_target_id or consumer_call_id,
                fqn=consumer_fqn,
                kind=consumer_kind,
                file=consumer_call_node.file,
                line=call_line,
                signature=consumer_sig,
                children=[],
                member_ref=member_ref,
                arguments=arguments,
                ref_type=flat_ref_type,
                callee=flat_callee,
                on=flat_on,
                on_kind=flat_on_kind,
            )

            # Depth expansion: trace forward into callee body
            if depth < max_depth and consumer_target_id and consumer_target:
                if consumer_target.kind in ("Method", "Function"):
                    children_ids = self.index.get_contains_children(consumer_target_id)
                    promoted = self._get_promoted_params(children_ids)
                    if promoted:
                        # Constructor with promoted params — trace property usages
                        for access_info in access_infos:
                            pos = access_info["position"]
                            if pos < len(promoted):
                                param_node = promoted[pos]
                                for af_edge in self.index.incoming[param_node.id].get(
                                    "assigned_from", []
                                ):
                                    prop_node = self.index.nodes.get(af_edge.source)
                                    if prop_node and prop_node.kind == "Property":
                                        prop_entry = ContextEntry(
                                            depth=depth + 1,
                                            node_id=prop_node.id,
                                            fqn=prop_node.fqn,
                                            kind=prop_node.kind,
                                            file=prop_node.file,
                                            line=prop_node.start_line,
                                            children=[],
                                        )
                                        entry.children.append(prop_entry)

                    # ISSUE-E: Cross-method USED BY — cross into callee via parameter FQN
                    self._cross_into_callee(
                        consumer_call_id, consumer_target_id, consumer_target,
                        entry, depth, max_depth, limit, visited
                    )

            count += 1
            entries.append(entry)

        # === Part 2: Standalone property accesses (not consumed as arguments) ===
        for access_call_id, access_call_node in standalone_accesses:
            if count >= limit:
                break
            if access_call_id in seen_calls:
                continue
            seen_calls.add(access_call_id)

            target_id = self.index.get_call_target(access_call_id)
            target_node = self.index.nodes.get(target_id) if target_id else None
            call_line = (
                access_call_node.range.get("start_line")
                if access_call_node.range else None
            )

            reference_type = get_reference_type_from_call(self.index, access_call_id)
            ac, acs, ok, of, ol = self._resolve_receiver_identity(access_call_id)
            arguments = self._get_argument_info(access_call_id)

            member_ref = MemberRef(
                target_name=self._member_display_name(target_node) if target_node else "?",
                target_fqn=target_node.fqn if target_node else "?",
                target_kind=target_node.kind if target_node else None,
                file=access_call_node.file,
                line=call_line,
                reference_type=reference_type,
                access_chain=ac,
                access_chain_symbol=acs,
                on_kind=ok,
                on_file=of,
                on_line=ol,
            )

            # Build flat fields for class-level context compatibility
            flat_callee = None
            if target_node:
                if target_node.kind == "Method":
                    flat_callee = target_node.name + "()"
                elif target_node.kind == "Property":
                    flat_callee = target_node.name if target_node.name.startswith("$") else "$" + target_node.name
            # Detect property-based receiver
            flat_on_kind = ok
            if flat_on_kind is None and acs:
                sym_nodes = self.index.resolve_symbol(acs)
                if sym_nodes and sym_nodes[0].kind == "Property":
                    flat_on_kind = "property"

            entry = ContextEntry(
                depth=depth,
                node_id=target_id or access_call_id,
                fqn=target_node.fqn if target_node else access_call_node.fqn,
                kind=target_node.kind if target_node else access_call_node.kind,
                file=access_call_node.file,
                line=call_line,
                children=[],
                member_ref=member_ref,
                arguments=arguments,
                ref_type=reference_type,
                callee=flat_callee,
                on=ac,
                on_kind=flat_on_kind,
            )
            count += 1
            entries.append(entry)

        # === Part 3: Direct argument edges (Value used directly as argument) ===
        argument_edges = self.index.incoming[value_id].get("argument", [])
        for edge in argument_edges:
            if count >= limit:
                break
            consumer_call_id = edge.source
            if consumer_call_id in seen_calls:
                continue
            seen_calls.add(consumer_call_id)

            consumer_call_node = self.index.nodes.get(consumer_call_id)
            if not consumer_call_node:
                continue

            consumer_target_id = self.index.get_call_target(consumer_call_id)
            consumer_target = (
                self.index.nodes.get(consumer_target_id) if consumer_target_id else None
            )

            consumer_fqn = consumer_target.fqn if consumer_target else consumer_call_node.fqn
            consumer_kind = consumer_target.kind if consumer_target else consumer_call_node.kind
            consumer_sig = consumer_target.signature if consumer_target else None
            call_line = (
                consumer_call_node.range.get("start_line")
                if consumer_call_node.range else None
            )

            arguments = self._get_argument_info(consumer_call_id)

            member_ref = None
            if consumer_target:
                reference_type = get_reference_type_from_call(self.index, consumer_call_id)
                ac, acs, ok, of, ol = self._resolve_receiver_identity(consumer_call_id)
                member_ref = MemberRef(
                    target_name="",
                    target_fqn=consumer_target.fqn,
                    target_kind=consumer_target.kind,
                    file=consumer_call_node.file,
                    line=call_line,
                    reference_type=reference_type,
                    access_chain=ac,
                    access_chain_symbol=acs,
                    on_kind=ok,
                    on_file=of,
                    on_line=ol,
                )

            # Build flat fields for class-level context compatibility
            flat_ref_type3 = None
            flat_callee3 = None
            flat_on3 = None
            flat_on_kind3 = None
            if consumer_target:
                flat_ref_type3 = get_reference_type_from_call(self.index, consumer_call_id)
                if consumer_target.kind == "Method":
                    flat_callee3 = consumer_target.name + "()"
                elif consumer_target.kind == "Property":
                    flat_callee3 = consumer_target.name if consumer_target.name.startswith("$") else "$" + consumer_target.name
                if member_ref:
                    flat_on3 = member_ref.access_chain
                    flat_on_kind3 = member_ref.on_kind
                    # Detect property-based receiver
                    if flat_on_kind3 is None and member_ref.access_chain_symbol:
                        sym_nodes = self.index.resolve_symbol(member_ref.access_chain_symbol)
                        if sym_nodes and sym_nodes[0].kind == "Property":
                            flat_on_kind3 = "property"

            entry = ContextEntry(
                depth=depth,
                node_id=consumer_target_id or consumer_call_id,
                fqn=consumer_fqn,
                kind=consumer_kind,
                file=consumer_call_node.file,
                line=call_line,
                signature=consumer_sig,
                children=[],
                member_ref=member_ref,
                arguments=arguments,
                ref_type=flat_ref_type3,
                callee=flat_callee3,
                on=flat_on3,
                on_kind=flat_on_kind3,
            )

            # ISSUE-E: Cross-method USED BY — cross into callee via parameter FQN
            if depth < max_depth and consumer_target_id and consumer_target:
                if consumer_target.kind in ("Method", "Function"):
                    self._cross_into_callee(
                        consumer_call_id, consumer_target_id, consumer_target,
                        entry, depth, max_depth, limit, visited
                    )

            count += 1
            entries.append(entry)

        # Sort all entries by source line number (AC 12)
        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

        return entries

    def _cross_into_callee(
        self, call_node_id: str, callee_id: str, callee_node,
        entry: "ContextEntry", depth: int, max_depth: int, limit: int,
        visited: set
    ) -> None:
        """Cross method boundary from caller into callee for USED BY tracing.

        For each argument edge with a parameter FQN, finds the matching
        Value(parameter) node in the callee and recursively traces its consumers.

        Also follows the return value path: if the call produces a result
        assigned to a local variable, traces that local's consumers.

        Args:
            call_node_id: The Call node in the caller.
            callee_id: The callee Method/Function node ID.
            callee_node: The callee Method/Function NodeData.
            entry: The ContextEntry to attach children to.
            depth: Current depth level.
            max_depth: Maximum depth.
            limit: Maximum entries.
            visited: Visited Value IDs for cycle detection.
        """
        # Cross into callee via argument parameter FQNs
        arg_edges = self.index.get_arguments(call_node_id)
        for _, _, _, parameter_fqn in arg_edges:
            if not parameter_fqn:
                continue
            # Find the matching Value(parameter) node by FQN
            param_matches = self.index.resolve_symbol(parameter_fqn)
            for pm in param_matches:
                if pm.kind == "Value" and pm.value_kind == "parameter":
                    if pm.id not in visited:
                        child_entries = self._build_value_consumer_chain(
                            pm.id, depth + 1, max_depth, limit, visited
                        )
                        for ce in child_entries:
                            if not ce.crossed_from:
                                ce.crossed_from = parameter_fqn
                        entry.children.extend(child_entries)
                    break

        # Return value path: if callee return flows back to a local in caller
        local_value = self._find_local_value_for_call(call_node_id)
        if local_value and local_value.id not in visited:
            return_entries = self._build_value_consumer_chain(
                local_value.id, depth + 1, max_depth, limit, visited
            )
            entry.children.extend(return_entries)

    def _resolve_receiver_identity(self, call_node_id: str) -> tuple[
        Optional[str], Optional[str], Optional[str], Optional[str], Optional[int]
    ]:
        """Resolve access chain and receiver identity for a Call node.

        Returns:
            (access_chain, access_chain_symbol, on_kind, on_file, on_line)
        """
        access_chain = build_access_chain(self.index, call_node_id)
        access_chain_symbol = resolve_access_chain_symbol(self.index, call_node_id)
        on_kind = None
        on_file = None
        on_line = None
        recv_id = self.index.get_receiver(call_node_id)
        if recv_id:
            recv_node = self.index.nodes.get(recv_id)
            if recv_node and recv_node.kind == "Value" and recv_node.value_kind in ("local", "parameter"):
                on_kind = "local" if recv_node.value_kind == "local" else "param"
                if recv_node.file:
                    on_file = recv_node.file
                if recv_node.range and recv_node.range.get("start_line") is not None:
                    on_line = recv_node.range["start_line"]
        else:
            # No explicit receiver: check if this is a $this-> access (implicit self)
            call_node = self.index.nodes.get(call_node_id)
            if call_node and call_node.kind == "Call" and call_node.call_kind == "access":
                on_kind = "self"
                if not access_chain:
                    access_chain = "$this"
        return access_chain, access_chain_symbol, on_kind, on_file, on_line

    def _resolve_containing_method(self, node_id: str) -> Optional[str]:
        """Resolve the containing Method/Function for a given node.

        For USED BY depth chaining (R7), we need to find the method that contains
        a reference so we can find callers of that method at the next depth level.

        If the node IS a Method/Function, return it directly.
        If the node is a File, return None (file-level references don't chain).
        Otherwise, traverse containment upward to find the Method/Function.

        Args:
            node_id: Node ID to resolve.

        Returns:
            Node ID of the containing Method/Function, or None if not found.
        """
        node = self.index.nodes.get(node_id)
        if not node:
            return None

        # If the node itself is a Method/Function, use it directly
        if node.kind in ("Method", "Function"):
            return node_id

        # If the node is a File, don't chain further (file-level references are leaf nodes)
        if node.kind == "File":
            return None

        # Traverse containment hierarchy upward to find the Method/Function
        current_id = node_id
        max_depth = 10  # Prevent infinite loops
        for _ in range(max_depth):
            parent_id = self.index.get_contains_parent(current_id)
            if not parent_id:
                return None

            parent_node = self.index.nodes.get(parent_id)
            if not parent_node:
                return None

            if parent_node.kind in ("Method", "Function"):
                return parent_id

            # File level reached without finding a method
            if parent_node.kind == "File":
                return None

            current_id = parent_id

        return None

    def _is_internal_reference(self, source_id: str, target_class_id: str) -> bool:
        """Check if a source node is internal to the target class (R3).

        A reference is internal if the source node is contained within the
        target class (e.g., the class's own methods accessing its own properties).

        Args:
            source_id: The source node of the reference.
            target_class_id: The class being queried.

        Returns:
            True if the source is internal to the target class.
        """
        current_id = source_id
        max_depth = 10  # Prevent infinite loops

        for _ in range(max_depth):
            parent_id = self.index.get_contains_parent(current_id)
            if not parent_id:
                return False

            # If we found the target class in the containment chain, it's internal
            if parent_id == target_class_id:
                return True

            parent_node = self.index.nodes.get(parent_id)
            if not parent_node:
                return False

            # Stop at File level -- we've traversed past any class
            if parent_node.kind == "File":
                return False

            current_id = parent_id

        return False

    def _resolve_param_name(self, call_node_id: str, position: int) -> Optional[str]:
        """Get the formal parameter name at the given position from the callee.

        Resolves the Call node's target (callee method/function), gets its
        Argument children in containment order, and returns the name at the
        requested position. Falls back to promoted property Value children
        when no Argument children exist (constructor promotion).

        Args:
            call_node_id: ID of the Call node.
            position: 0-based argument position.

        Returns:
            Parameter name string (e.g., "$productId") or None if not found.
        """
        target_id = self.index.get_call_target(call_node_id)
        if not target_id:
            return None
        children = self.index.get_contains_children(target_id)
        arg_nodes = []
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Argument":
                arg_nodes.append(child)
        if position < len(arg_nodes):
            return arg_nodes[position].name
        # Fallback: promoted constructor parameters (Value children, no Argument nodes)
        promoted = self._get_promoted_params(children)
        if position < len(promoted):
            return promoted[position].name
        return None

    def _build_external_call_fqn(self, call_node_id: str, call_node) -> str:
        """Build a display FQN for an external call (callee not in graph).

        Uses the receiver's type_of to get the class/interface name, then
        appends the call name. Falls back to just the call name.
        """
        call_name = call_node.name or "?"
        # Try to get receiver type for a qualified FQN
        recv_id = self.index.get_receiver(call_node_id)
        if recv_id:
            recv_node = self.index.nodes.get(recv_id)
            if recv_node:
                type_ids = self.index.get_type_of_all(recv_id)
                for tid in type_ids:
                    type_node = self.index.nodes.get(tid)
                    if type_node:
                        return f"{type_node.fqn}::{call_name}"
        return call_name

    def _find_result_var(self, call_node_id: str) -> Optional[str]:
        """Find the local variable name that receives this call's result.

        Follows: Call --produces--> Value (result) <--assigned_from-- Value (local)

        Args:
            call_node_id: ID of the Call node.

        Returns:
            Local variable name (e.g., "$order") or None if no assignment.
        """
        local_node = self._find_local_value_for_call(call_node_id)
        return local_node.name if local_node else None

    def _find_local_value_for_call(self, call_node_id: str):
        """Find the local Value node assigned from this call's result.

        Follows: Call --produces--> Value (result) <--assigned_from-- Value (local)

        Args:
            call_node_id: ID of the Call node.

        Returns:
            NodeData for the local Value node, or None if no assignment.
        """
        result_id = self.index.get_produces(call_node_id)
        if not result_id:
            return None
        for edge in self.index.incoming[result_id].get("assigned_from", []):
            source_node = self.index.nodes.get(edge.source)
            if source_node and source_node.kind == "Value" and source_node.value_kind == "local":
                return source_node
        return None

    def _get_argument_info(self, call_node_id: str) -> list:
        """Get argument-to-parameter mappings for a Call node.

        Returns a list of ArgumentInfo instances with position, param_name,
        value_expr, value_source, value_type, param_fqn, value_ref_symbol,
        and source_chain.

        Args:
            call_node_id: ID of the Call node.

        Returns:
            List of ArgumentInfo instances.
        """

        arg_edges = self.index.get_arguments(call_node_id)
        arguments = []
        for arg_node_id, position, expression, parameter in arg_edges:
            arg_node = self.index.nodes.get(arg_node_id)
            if arg_node:
                # Use parameter field from edge if available, fall back to position-based matching
                if parameter:
                    # Extract param_name from original parameter FQN (uses . separator)
                    param_name = parameter.rsplit(".", 1)[-1] if "." in parameter else parameter
                    # For promoted constructor params, resolve to Property FQN via assigned_from
                    param_fqn = self._resolve_promoted_property_fqn(parameter) or parameter
                else:
                    param_name = self._resolve_param_name(call_node_id, position)
                    param_fqn = self._resolve_param_fqn(call_node_id, position)

                # Resolve value type via type_of edges
                value_type = None
                type_ids = self.index.get_type_of_all(arg_node_id)
                if type_ids:
                    type_names = []
                    for tid in type_ids:
                        tnode = self.index.nodes.get(tid)
                        if tnode:
                            type_names.append(tnode.name)
                    if type_names:
                        value_type = "|".join(type_names)

                # ISSUE-D: Resolve value_ref_symbol and source_chain
                value_ref_symbol = None
                source_chain = None
                if arg_node.value_kind == "local":
                    # Local variable — reference by graph symbol
                    value_ref_symbol = arg_node.fqn
                elif arg_node.value_kind == "parameter":
                    # Method parameter — reference by graph symbol
                    value_ref_symbol = arg_node.fqn
                elif arg_node.value_kind == "result":
                    # Result of another call — trace source chain
                    source_chain = self._trace_source_chain(arg_node_id)

                arguments.append(ArgumentInfo(
                    position=position,
                    param_name=param_name,
                    value_expr=expression or arg_node.name,
                    value_source=arg_node.value_kind,
                    value_type=value_type,
                    param_fqn=param_fqn,
                    value_ref_symbol=value_ref_symbol,
                    source_chain=source_chain,
                ))
        return arguments

    def _resolve_param_fqn(self, call_node_id: str, position: int) -> Optional[str]:
        """Get the formal parameter FQN at the given position from the callee.

        Falls back to promoted property resolution via assigned_from edges
        when no Argument children exist (constructor promotion).

        Args:
            call_node_id: ID of the Call node.
            position: 0-based argument position.

        Returns:
            Parameter FQN string or None.
        """
        target_id = self.index.get_call_target(call_node_id)
        if not target_id:
            return None
        children = self.index.get_contains_children(target_id)
        arg_nodes = []
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Argument":
                arg_nodes.append(child)
        if position < len(arg_nodes):
            return arg_nodes[position].fqn
        # Fallback: promoted constructor parameters — resolve to Property FQN
        promoted = self._get_promoted_params(children)
        if position < len(promoted):
            param_node = promoted[position]
            # Check for assigned_from edge from a Property node
            for edge in self.index.incoming[param_node.id].get("assigned_from", []):
                source_node = self.index.nodes.get(edge.source)
                if source_node and source_node.kind == "Property":
                    return source_node.fqn
            return param_node.fqn
        return None

    def _resolve_promoted_property_fqn(self, param_fqn: str) -> Optional[str]:
        """Resolve a parameter FQN to its promoted Property FQN if applicable.

        For PHP constructor promotion, the parameter Value node has an
        assigned_from edge from a Property node. This returns the Property FQN.

        Args:
            param_fqn: The parameter FQN (e.g., Order::__construct().$id).

        Returns:
            Property FQN if promoted, None otherwise.
        """
        param_ids = self.index.fqn_to_ids.get(param_fqn, [])
        for param_id in param_ids:
            param_node = self.index.nodes.get(param_id)
            if param_node and param_node.kind == "Value" and param_node.value_kind == "parameter":
                for edge in self.index.incoming[param_id].get("assigned_from", []):
                    source_node = self.index.nodes.get(edge.source)
                    if source_node and source_node.kind == "Property":
                        return source_node.fqn
        return None

    def _get_promoted_params(self, children: list[str]) -> list:
        """Get promoted constructor parameter Value nodes sorted by declaration order.

        For PHP constructor promotion, the callee has Value(parameter) children
        instead of Argument children. These are sorted by source range to
        establish positional order matching the constructor signature.

        Args:
            children: List of child node IDs from get_contains_children().

        Returns:
            List of NodeData for promoted parameter Value nodes, sorted by position.
        """
        param_values = []
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Value" and child.value_kind == "parameter":
                param_values.append(child)
        if not param_values:
            return []
        param_values.sort(key=lambda n: (
            n.range.get("start_line", 0) if n.range else 0,
            n.range.get("start_col", 0) if n.range else 0,
        ))
        return param_values

    def _trace_source_chain(self, value_node_id: str) -> Optional[list]:
        """Trace the source chain for a result Value node.

        For property access results, follows the receiver chain to build
        a source chain showing what property is accessed on what object.

        Args:
            value_node_id: ID of the result Value node.

        Returns:
            List of chain step dicts, or None if chain cannot be traced.
        """
        # Find the Call that produces this result value
        # Result values have incoming 'produces' from their Call
        for edge in self.index.incoming[value_node_id].get("produces", []):
            call_id = edge.source
            call_node = self.index.nodes.get(call_id)
            if not call_node:
                continue

            target_id = self.index.get_call_target(call_id)
            if not target_id:
                continue
            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue

            # Build chain step
            step = {
                "fqn": target_node.fqn,
                "kind": target_node.kind,
            }
            ref_type = None
            if call_node.call_kind:
                ref_type = call_node.call_kind
            step["reference_type"] = ref_type

            # Add receiver info if available
            recv_id = self.index.get_receiver(call_id)
            if recv_id:
                recv_node = self.index.nodes.get(recv_id)
                if recv_node:
                    step["on"] = recv_node.fqn
                    # Add variable identity info for Value receivers
                    if recv_node.kind == "Value" and recv_node.value_kind:
                        if recv_node.value_kind == "local":
                            step["on_kind"] = "local"
                        elif recv_node.value_kind == "parameter":
                            step["on_kind"] = "param"
                        if recv_node.file:
                            step["on_file"] = recv_node.file
                        if recv_node.range and recv_node.range.get("start_line") is not None:
                            step["on_line"] = recv_node.range["start_line"]

            return [step]

        return None

    def _get_type_references(
        self, method_id: str, depth: int, cycle_guard: set, count: list, limit: int
    ) -> list[ContextEntry]:
        """Extract type-related references (param types, return types) from uses edges.

        When using execution flow for methods, Call nodes don't capture type hints
        for parameters and return types. This helper extracts those from the
        structural `uses` edges so they still appear in USES output.

        Only includes entries where the inferred reference type is a type-related
        value (property_type, type_hint). Excludes parameter_type and return_type
        since those are already shown in the DEFINITION section.
        """
        TYPE_KINDS = {"property_type", "type_hint"}
        entries = []
        local_visited: set[str] = set()

        edges = self.index.get_deps(method_id)
        for edge in edges:
            target_id = edge.target
            if target_id in cycle_guard or target_id in local_visited:
                continue

            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue

            # Only include Class/Interface/Trait/Enum targets (type references)
            if target_node.kind not in ("Class", "Interface", "Trait", "Enum"):
                continue

            # Infer reference type — only keep type-related ones
            ref_type = _infer_reference_type(edge, target_node, self.index)
            if ref_type not in TYPE_KINDS:
                continue

            # Check if there's a Call node (constructor) for this target
            file = edge.location.get("file") if edge.location else target_node.file
            line = edge.location.get("line") if edge.location else target_node.start_line
            call_node_id = find_call_for_usage(self.index, method_id, target_id, file, line)
            if call_node_id:
                # This is a constructor call — it will be picked up by execution flow
                continue

            local_visited.add(target_id)
            if count[0] >= limit:
                break
            count[0] += 1

            member_ref = MemberRef(
                target_name="",
                target_fqn=target_node.fqn,
                target_kind=target_node.kind,
                file=file,
                line=line,
                reference_type=ref_type,
                access_chain=None,
                access_chain_symbol=None,
            )

            entry_kwargs = dict(
                depth=depth,
                node_id=target_id,
                fqn=target_node.fqn,
                kind=target_node.kind,
                file=file,
                line=line,
                signature=target_node.signature,
                children=[],
                implementations=[],
                member_ref=member_ref,
                arguments=[],
                result_var=None,
            )
            entries.append(ContextEntry(**entry_kwargs))

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _build_execution_flow(
        self, method_id: str, depth: int, max_depth: int,
        limit: int, cycle_guard: set, count: list,
        include_impl: bool = False, shown_impl_for: set | None = None,
    ) -> list[ContextEntry]:
        """Build variable-centric execution flow for a method.

        Produces two kinds of entries:
        - Kind 1 (local_variable): When a call result is assigned to a local
          variable. The variable is the primary entry; the call is nested as
          source_call.
        - Kind 2 (call): When a call result is discarded (void/unused). The
          call is the primary entry, same as before.

        Calls consumed as receivers or argument sources by other calls in the
        same method are NOT top-level entries — they appear nested inside the
        consuming entry's access chain or argument source chain.

        Args:
            method_id: The Method/Function node ID to build flow for.
            depth: Current depth level in the tree.
            max_depth: Maximum depth to expand.
            limit: Maximum number of entries.
            cycle_guard: Set of node IDs to prevent infinite recursion.
            count: Mutable list[int] tracking total entries created.
            include_impl: Whether to attach implementations for interface methods.
            shown_impl_for: Set tracking nodes with implementations already shown.
        """
        if depth > max_depth or count[0] >= limit:
            return []

        if shown_impl_for is None:
            shown_impl_for = set()

        children = self.index.get_contains_children(method_id)

        # Step 1: Collect all Call children
        call_children = []
        for child_id in children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Call":
                call_children.append((child_id, child))

        # Step 2: Identify consumed calls — calls whose result Value is used
        # as a receiver or argument source by another call in the same method.
        consumed: set[str] = set()
        for call_id, call_node in call_children:
            # Check receiver: if the receiver Value is a result of another call
            recv_id = self.index.get_receiver(call_id)
            if recv_id:
                recv_node = self.index.nodes.get(recv_id)
                if recv_node and recv_node.kind == "Value" and recv_node.value_kind == "result":
                    source_call_id = self.index.get_source_call(recv_id)
                    if source_call_id:
                        consumed.add(source_call_id)
            # Check arguments: if any arg Value is a result of another call
            arg_edges = self.index.get_arguments(call_id)
            for arg_id, _, _, _ in arg_edges:
                arg_node = self.index.nodes.get(arg_id)
                if arg_node and arg_node.value_kind == "result":
                    src = self.index.get_source_call(arg_id)
                    if src:
                        consumed.add(src)

        # Step 3: Build entries for non-consumed calls
        entries = []
        local_visited: set[str] = set()

        for child_id, child in call_children:
            # Skip consumed calls (they appear nested inside consuming entries)
            if child_id in consumed:
                continue
            if count[0] >= limit:
                break

            target_id = self.index.get_call_target(child_id)

            # External call (callee has no node in graph, e.g., vendor method)
            if not target_id:
                # Use the Call node's own data to build the entry
                count[0] += 1
                call_line = child.range.get("start_line") if child.range else None
                ac, acs, ok, of, ol = self._resolve_receiver_identity(child_id)
                arguments = self._get_argument_info(child_id)
                # Derive FQN from receiver type + call name
                ext_fqn = self._build_external_call_fqn(child_id, child)
                reference_type = get_reference_type_from_call(self.index, child_id)

                member_ref = MemberRef(
                    target_name="",
                    target_fqn=ext_fqn,
                    target_kind=child.call_kind or "method",
                    file=child.file,
                    line=call_line,
                    reference_type=reference_type,
                    access_chain=ac,
                    access_chain_symbol=acs,
                    on_kind=ok,
                    on_file=of,
                    on_line=ol,
                )

                local_value = self._find_local_value_for_call(child_id)
                if local_value:
                    var_type = None
                    type_of_edges = self.index.outgoing[local_value.id].get("type_of", [])
                    if type_of_edges:
                        type_node = self.index.nodes.get(type_of_edges[0].target)
                        if type_node:
                            var_type = type_node.name

                    source_call_entry = ContextEntry(
                        depth=depth,
                        node_id=child_id,
                        fqn=ext_fqn,
                        kind=child.call_kind or "Method",
                        file=child.file,
                        line=call_line,
                        signature=None,
                        children=[],
                        implementations=[],
                        member_ref=member_ref,
                        arguments=arguments,
                        result_var=None,
                        entry_type="call",
                    )
                    var_symbol = local_value.fqn
                    var_line = local_value.range.get("start_line") if local_value.range else call_line

                    entry = ContextEntry(
                        depth=depth,
                        node_id=local_value.id,
                        fqn=local_value.fqn,
                        kind="Value",
                        file=child.file,
                        line=var_line,
                        signature=None,
                        children=[],
                        implementations=[],
                        member_ref=None,
                        arguments=[],
                        result_var=None,
                        entry_type="local_variable",
                        variable_name=local_value.name,
                        variable_symbol=var_symbol,
                        variable_type=var_type,
                        source_call=source_call_entry,
                    )
                else:
                    result_var = self._find_result_var(child_id)
                    entry = ContextEntry(
                        depth=depth,
                        node_id=child_id,
                        fqn=ext_fqn,
                        kind=child.call_kind or "Method",
                        file=child.file,
                        line=call_line,
                        signature=None,
                        children=[],
                        implementations=[],
                        member_ref=member_ref,
                        arguments=arguments,
                        result_var=result_var,
                        entry_type="call",
                    )
                # External calls cannot recurse (no target method to expand)
                entries.append(entry)
                continue

            if target_id in local_visited:
                continue
            local_visited.add(target_id)

            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue

            if target_id in cycle_guard:
                continue

            count[0] += 1
            reference_type = get_reference_type_from_call(self.index, child_id)
            ac, acs, ok, of, ol = self._resolve_receiver_identity(child_id)
            arguments = self._get_argument_info(child_id)
            call_line = child.range.get("start_line") if child.range else None

            member_ref = MemberRef(
                target_name="",
                target_fqn=target_node.fqn,
                target_kind=target_node.kind,
                file=child.file,
                line=call_line,
                reference_type=reference_type,
                access_chain=ac,
                access_chain_symbol=acs,
                on_kind=ok,
                on_file=of,
                on_line=ol,
            )

            # Check if this call's result is assigned to a local variable
            local_value = self._find_local_value_for_call(child_id)

            if local_value:
                # Kind 1: Variable entry with nested source_call
                # Resolve variable type from type_of edge
                var_type = None
                type_of_edges = self.index.outgoing[local_value.id].get("type_of", [])
                if type_of_edges:
                    type_node = self.index.nodes.get(type_of_edges[0].target)
                    if type_node:
                        var_type = type_node.name

                # Build the nested source_call entry (the call itself)
                source_call_entry = ContextEntry(
                    depth=depth,
                    node_id=target_id,
                    fqn=target_node.fqn,
                    kind=target_node.kind,
                    file=child.file,
                    line=call_line,
                    signature=target_node.signature,
                    children=[],
                    implementations=[],
                    member_ref=member_ref,
                    arguments=arguments,
                    result_var=None,
                    entry_type="call",
                )

                # Attach implementations to source_call
                if include_impl and target_node and target_id not in shown_impl_for:
                    shown_impl_for.add(target_id)
                    source_call_entry.implementations = self._get_implementations_for_node(
                        target_node, depth, max_depth, limit, cycle_guard, count, shown_impl_for
                    )

                # Variable symbol from local Value's FQN
                var_symbol = local_value.fqn

                var_line = local_value.range.get("start_line") if local_value.range else call_line

                entry = ContextEntry(
                    depth=depth,
                    node_id=local_value.id,
                    fqn=local_value.fqn,
                    kind="Value",
                    file=child.file,
                    line=var_line,
                    signature=None,
                    children=[],
                    implementations=[],
                    member_ref=None,
                    arguments=[],
                    result_var=None,
                    entry_type="local_variable",
                    variable_name=local_value.name,
                    variable_symbol=var_symbol,
                    variable_type=var_type,
                    source_call=source_call_entry,
                )
            else:
                # Kind 2: Call entry (result discarded)
                result_var = self._find_result_var(child_id)

                entry = ContextEntry(
                    depth=depth,
                    node_id=target_id,
                    fqn=target_node.fqn,
                    kind=target_node.kind,
                    file=child.file,
                    line=call_line,
                    signature=target_node.signature,
                    children=[],
                    implementations=[],
                    member_ref=member_ref,
                    arguments=arguments,
                    result_var=result_var,
                    entry_type="call",
                )

                # Attach implementations for interface methods
                if include_impl and target_node and target_id not in shown_impl_for:
                    shown_impl_for.add(target_id)
                    entry.implementations = self._get_implementations_for_node(
                        target_node, depth, max_depth, limit, cycle_guard, count, shown_impl_for
                    )

            # Depth expansion: recurse into callee's execution flow
            if depth < max_depth and target_node.kind in ("Method", "Function"):
                entry.children = self._build_execution_flow(
                    target_id, depth + 1, max_depth, limit,
                    cycle_guard | {target_id}, count,
                    include_impl=include_impl, shown_impl_for=shown_impl_for,
                )

            entries.append(entry)

        # Filter orphan property accesses consumed by non-Call expressions
        entries = self._filter_orphan_property_accesses(entries)

        # Sort by line number for execution order
        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _filter_orphan_property_accesses(self, entries: list[ContextEntry]) -> list[ContextEntry]:
        """Filter property access entries consumed by non-Call expressions.

        An orphan is a top-level property_access entry whose result is not consumed
        by any other Call (via receiver or argument edges) but whose access expression
        appears in another entry's argument value_expr. These are already visible in
        the consuming argument text (e.g., in sprintf or string concatenation).

        Only filters Kind 2 (call) entries with reference_type == "property_access".
        Kind 1 (local_variable) entries are never filtered.
        """
        # Collect all argument value_expr strings from all entries
        all_value_exprs: list[str] = []
        for entry in entries:
            for arg in entry.arguments:
                if arg.value_expr:
                    all_value_exprs.append(arg.value_expr)
            if entry.source_call:
                for arg in entry.source_call.arguments:
                    if arg.value_expr:
                        all_value_exprs.append(arg.value_expr)

        if not all_value_exprs:
            return entries

        # Identify orphan property accesses and check if their expression
        # appears in any other entry's argument value_expr
        filtered = []
        for entry in entries:
            # Only consider Kind 2 property_access entries as orphan candidates
            if (entry.entry_type == "call"
                    and entry.member_ref
                    and entry.member_ref.reference_type == "property_access"
                    and entry.member_ref.access_chain):
                # Build the expression: "$receiver->propertyName"
                # FQN is like "App\Entity\Order::$id", extract "id"
                prop_fqn = entry.fqn
                prop_name = prop_fqn.split("::$")[-1] if "::$" in prop_fqn else None
                if prop_name:
                    access_expr = f"{entry.member_ref.access_chain}->{prop_name}"
                    # Check if this expression appears in any value_expr
                    is_expression_consumed = any(
                        access_expr in expr for expr in all_value_exprs
                    )
                    if is_expression_consumed:
                        # Orphan: skip this entry
                        continue

            filtered.append(entry)

        return filtered

    def _build_value_source_chain(
        self, value_id: str, depth: int, max_depth: int, limit: int,
        visited: set | None = None
    ) -> list[ContextEntry]:
        """Build source chain for a Value node (USES section).

        Traces: $savedOrder <- save($processedOrder) <- process($order) <- new Order(...)
        Each depth level follows assigned_from -> produces -> Call, then recursively
        traces the Call's argument Values' source chains.

        For parameter Values: crosses method boundary to find callers via argument
        edges with matching parameter FQN (ISSUE-E).

        Args:
            value_id: ID of the Value node to trace from.
            depth: Current depth level.
            max_depth: Maximum depth for recursion.
            limit: Maximum number of entries.
            visited: Set of visited Value IDs for cycle detection.

        Returns:
            List of ContextEntry instances representing the source chain.
        """
        if depth > max_depth:
            return []

        if visited is None:
            visited = set()
        if value_id in visited:
            return []
        visited.add(value_id)

        value_node = self.index.nodes.get(value_id)
        if not value_node or value_node.kind != "Value":
            return []

        # ISSUE-E: Parameter Values have no local sources — find callers via argument edges
        if value_node.value_kind == "parameter":
            return self._build_parameter_uses(value_id, value_node, depth, max_depth, limit, visited)

        # Follow assigned_from to find source Value
        assigned_from_id = self.index.get_assigned_from(value_id)
        source_value_id = assigned_from_id

        # If no assigned_from, check if this is a result value with a source call
        if not source_value_id and value_node.value_kind == "result":
            source_value_id = value_id  # Result value IS the source

        if not source_value_id:
            return []

        # Find the Call that produced the source Value
        source_call_id = self.index.get_source_call(source_value_id)
        if not source_call_id:
            return []

        call_node = self.index.nodes.get(source_call_id)
        if not call_node:
            return []

        # Get the call target (callee method/constructor)
        target_id = self.index.get_call_target(source_call_id)
        target_node = self.index.nodes.get(target_id) if target_id else None

        if not target_node:
            return []

        # Build reference type and access chain
        reference_type = get_reference_type_from_call(self.index, source_call_id)
        ac, acs, ok, of, ol = self._resolve_receiver_identity(source_call_id)

        call_line = call_node.range.get("start_line") if call_node.range else None

        member_ref = MemberRef(
            target_name="",
            target_fqn=target_node.fqn,
            target_kind=target_node.kind,
            file=call_node.file,
            line=call_line,
            reference_type=reference_type,
            access_chain=ac,
            access_chain_symbol=acs,
            on_kind=ok,
            on_file=of,
            on_line=ol,
        )

        # Reuse _get_argument_info for argument tracking
        arguments = self._get_argument_info(source_call_id)

        entry = ContextEntry(
            depth=depth,
            node_id=target_id,
            fqn=target_node.fqn,
            kind=target_node.kind,
            file=call_node.file,
            line=call_line,
            signature=target_node.signature,
            children=[],
            implementations=[],
            member_ref=member_ref,
            arguments=arguments,
            result_var=None,
            entry_type="call",
        )

        # Recursively trace each argument's source chain at depth+1
        if depth < max_depth:
            for arg in arguments:
                if arg.value_ref_symbol:
                    # Find this Value node by FQN and trace its source
                    arg_value_matches = self.index.resolve_symbol(arg.value_ref_symbol)
                    if arg_value_matches:
                        arg_value_node = arg_value_matches[0]
                        if arg_value_node.kind == "Value":
                            children = self._build_value_source_chain(
                                arg_value_node.id, depth + 1, max_depth, limit, visited
                            )
                            entry.children.extend(children)
                elif arg.source_chain:
                    # Result argument (e.g., $input->customerEmail) — trace the
                    # receiver Value to follow data flow across method boundaries
                    for step in arg.source_chain:
                        on_fqn = step.get("on")
                        if on_fqn:
                            on_matches = self.index.resolve_symbol(on_fqn)
                            if on_matches:
                                on_node = on_matches[0]
                                if on_node.kind == "Value":
                                    children = self._build_value_source_chain(
                                        on_node.id, depth + 1, max_depth, limit, visited
                                    )
                                    entry.children.extend(children)

        return [entry]

    def _build_parameter_uses(
        self, param_value_id: str, param_node, depth: int, max_depth: int,
        limit: int, visited: set
    ) -> list[ContextEntry]:
        """Find callers of a parameter Value via argument edges with matching parameter FQN.

        Searches all argument edges in the graph where the `parameter` field matches
        this Value's FQN, then traces the source of each caller's argument Value.

        Args:
            param_value_id: ID of the parameter Value node.
            param_node: The parameter Value NodeData.
            depth: Current depth level.
            max_depth: Maximum depth.
            limit: Maximum entries.
            visited: Visited Value IDs for cycle detection.

        Returns:
            List of ContextEntry representing caller-provided sources.
        """
        entries = []
        param_fqn = param_node.fqn

        # Search argument edges where parameter field matches this FQN
        for edge in self.index.edges:
            if edge.type != "argument":
                continue
            if edge.parameter != param_fqn:
                continue

            # Found a caller's argument edge
            caller_call_id = edge.source  # Call node in the caller
            caller_value_id = edge.target  # Value passed by the caller

            call_node = self.index.nodes.get(caller_call_id)
            caller_value = self.index.nodes.get(caller_value_id)
            if not call_node or not caller_value:
                continue

            # Find the containing method of the caller
            scope_id = get_containing_scope(self.index, caller_call_id)
            scope_node = self.index.nodes.get(scope_id) if scope_id else None

            call_line = call_node.range.get("start_line") if call_node.range else None

            entry = ContextEntry(
                depth=depth,
                node_id=scope_id or caller_call_id,
                fqn=scope_node.fqn if scope_node else call_node.fqn,
                kind=scope_node.kind if scope_node else call_node.kind,
                file=call_node.file,
                line=call_line,
                signature=scope_node.signature if scope_node else None,
                children=[],
                crossed_from=param_fqn,
            )

            # Trace the caller's argument Value's source chain (recurse with depth+1)
            if depth < max_depth and caller_value_id not in visited:
                child_entries = self._build_value_source_chain(
                    caller_value_id, depth + 1, max_depth, limit, visited
                )
                entry.children.extend(child_entries)

            entries.append(entry)
            if len(entries) >= limit:
                break

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _build_property_uses(
        self, property_id: str, depth: int, max_depth: int, limit: int
    ) -> list[ContextEntry]:
        """Build USES chain for a Property node.

        For promoted constructor properties: follow assigned_from edge to
        Value(parameter), then trace callers via argument edges. Shows only
        the argument matching this property (filtered), not all constructor args.

        For other properties: follow assigned_from edges to source Values.
        """
        if depth > max_depth:
            return []

        property_node = self.index.nodes.get(property_id)
        if not property_node:
            return []

        visited: set = set()

        # Check for assigned_from edges (promoted property -> Value(parameter))
        assigned_edges = self.index.outgoing[property_id].get("assigned_from", [])
        for edge in assigned_edges:
            source_node = self.index.nodes.get(edge.target)
            if source_node and source_node.kind == "Value" and source_node.value_kind == "parameter":
                # Promoted property: trace callers but filter to relevant arg only
                return self._build_property_callers_filtered(
                    source_node, property_node, depth, max_depth, limit, visited
                )

        # No assigned_from from parameter: property may be set by DI container or direct assignment
        return []

    def _build_property_callers_filtered(
        self, param_node: NodeData, property_node: NodeData,
        depth: int, max_depth: int, limit: int, visited: set
    ) -> list[ContextEntry]:
        """Find callers of a constructor parameter, showing only the matching argument.

        Instead of showing all N constructor arguments, filters to show only
        the argument that maps to the queried property's parameter.
        """
        entries = []
        param_fqn = param_node.fqn

        # Search argument edges where parameter field matches this FQN
        for edge_data in self.index.edges:
            if edge_data.type != "argument":
                continue
            if edge_data.parameter != param_fqn:
                continue

            # Found a Call that passes a value for this parameter
            call_id = edge_data.source
            call_node = self.index.nodes.get(call_id)
            if not call_node:
                continue

            # The argument Value being passed
            caller_value_id = edge_data.target
            caller_value_node = self.index.nodes.get(caller_value_id)

            # Find containing method
            scope_id = get_containing_scope(self.index, call_id)
            scope_node = self.index.nodes.get(scope_id) if scope_id else None

            call_line = call_node.range.get("start_line") if call_node.range else None

            # Build a single filtered ArgumentInfo for just this property's arg
            filtered_args = []
            arg_info = self._get_single_argument_info(
                call_id, param_fqn, caller_value_id
            )
            if arg_info:
                filtered_args.append(arg_info)

            entry = ContextEntry(
                depth=depth,
                node_id=scope_id or call_id,
                fqn=scope_node.fqn if scope_node else call_node.fqn,
                kind=scope_node.kind if scope_node else call_node.kind,
                file=call_node.file,
                line=call_line,
                signature=scope_node.signature if scope_node else None,
                children=[],
                arguments=filtered_args,
                crossed_from=param_fqn,
            )

            # Trace the caller's argument Value source at depth+1
            if depth < max_depth and caller_value_id and caller_value_id not in visited:
                child_entries = self._build_value_source_chain(
                    caller_value_id, depth + 1, max_depth, limit, visited
                )
                entry.children.extend(child_entries)

            entries.append(entry)
            if len(entries) >= limit:
                break

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _get_single_argument_info(
        self, call_id: str, param_fqn: str, value_id: str
    ) -> Optional[ArgumentInfo]:
        """Build ArgumentInfo for a single argument matching param_fqn."""
        value_node = self.index.nodes.get(value_id)
        if not value_node:
            return None

        # Determine value expression and source
        value_expr = None
        value_source = None
        if value_node.value_kind == "parameter":
            value_source = "parameter"
            value_expr = value_node.name
        elif value_node.value_kind == "local":
            value_source = "local"
            value_expr = value_node.name
        elif value_node.value_kind == "literal":
            value_source = "literal"
            # Use expression from argument edge if available (has actual value)
            arg_expression = None
            for arg_vid, _pos, expr, _param in self.index.get_arguments(call_id):
                if arg_vid == value_id and expr:
                    arg_expression = expr
                    break
            value_expr = arg_expression if arg_expression else value_node.name
        elif value_node.value_kind == "result":
            value_source = "result"
            # Try to get the expression from source call
            source_call_id = self.index.get_source_call(value_id)
            if source_call_id:
                source_call = self.index.nodes.get(source_call_id)
                if source_call:
                    target_id = self.index.get_call_target(source_call_id)
                    target = self.index.nodes.get(target_id) if target_id else None
                    if target and source_call.call_kind == "access":
                        # Property access: show as $receiver->property
                        chain = build_access_chain(self.index, source_call_id)
                        prop_name = target.name.lstrip("$")
                        if chain:
                            value_expr = f"{chain}->{prop_name}"
                        else:
                            value_expr = f"$this->{prop_name}"
                    elif target:
                        value_expr = f"{target.name}()"

        # Resolve type
        value_type = None
        type_ids = self.index.get_type_of_all(value_id)
        if type_ids:
            type_names = []
            for tid in type_ids:
                tnode = self.index.nodes.get(tid)
                if tnode:
                    type_names.append(tnode.name)
            if type_names:
                value_type = "|".join(type_names)

        # Extract param name from FQN (e.g., "Order::__construct().$id" -> "$id")
        param_name = param_fqn.rsplit(".", 1)[-1] if "." in param_fqn else param_fqn

        return ArgumentInfo(
            position=0,
            param_name=param_name,
            value_expr=value_expr,
            value_source=value_source,
            value_type=value_type,
            param_fqn=param_fqn,
            value_ref_symbol=value_node.fqn,
        )

    def _build_property_used_by(
        self, property_id: str, depth: int, max_depth: int, limit: int
    ) -> list[ContextEntry]:
        """Build USED BY chain for a Property node.

        Groups property accesses by containing method with (xN) dedup.
        For service properties: shows access -> method_call chain at depth 2.
        For entity properties: groups by containing method with (xN) counts.
        """
        if depth > max_depth:
            return []

        property_node = self.index.nodes.get(property_id)
        if not property_node:
            return []

        # Find all Call nodes that target this Property (via 'calls' edges)
        call_ids = self.index.get_calls_to(property_id)

        # Group accesses by containing method
        # Key: scope_id (method), Value: list of (call_id, call_node, receiver_info)
        method_groups: dict[str, list] = {}
        for call_id in call_ids:
            call_node = self.index.nodes.get(call_id)
            if not call_node:
                continue
            scope_id = get_containing_scope(self.index, call_id)
            if not scope_id:
                continue
            key = scope_id
            if key not in method_groups:
                method_groups[key] = []
            method_groups[key].append((call_id, call_node))

        entries = []
        visited = set()

        for scope_id, calls in method_groups.items():
            if len(entries) >= limit:
                break

            scope_node = self.index.nodes.get(scope_id)
            if not scope_node:
                continue

            # Use first call for representative info
            first_call_id, first_call_node = calls[0]
            first_line = first_call_node.range.get("start_line") if first_call_node.range else None
            reference_type = get_reference_type_from_call(self.index, first_call_id)
            ac, acs, ok, of, ol = self._resolve_receiver_identity(first_call_id)

            # Collect unique receivers across all accesses in this method
            receiver_names = []
            for call_id, call_node in calls:
                _, _, c_ok, _, _ = self._resolve_receiver_identity(call_id)
                chain = build_access_chain(self.index, call_id)
                recv_id = self.index.get_receiver(call_id)
                if recv_id:
                    recv_node = self.index.nodes.get(recv_id)
                    if recv_node and recv_node.kind == "Value":
                        rname = recv_node.name
                        rkind = "local" if recv_node.value_kind == "local" else ("param" if recv_node.value_kind == "parameter" else None)
                        if rname and rkind and rname not in [r[0] for r in receiver_names]:
                            receiver_names.append((rname, rkind))
                elif c_ok == "self":
                    if ("$this", "self") not in receiver_names:
                        receiver_names.append(("$this", "self"))

            # Build sites for (xN) dedup
            access_count = len(calls)
            sites = None
            if access_count > 1:
                sites = []
                for call_id, call_node in calls:
                    site_line = call_node.range.get("start_line") if call_node.range else None
                    sites.append({"method": scope_node.fqn, "line": site_line})

            member_ref = MemberRef(
                target_name=self._member_display_name(property_node),
                target_fqn=property_node.fqn,
                target_kind="Property",
                file=first_call_node.file,
                line=first_line,
                reference_type=reference_type,
                access_chain=ac,
                access_chain_symbol=acs,
                on_kind=ok if not receiver_names else receiver_names[0][1],
                on_file=of,
                on_line=ol,
            )

            # Build on display string from receiver_names (no tags — onKind is separate)
            on_display = None
            if receiver_names:
                parts = []
                for rname, rkind in receiver_names:
                    if rkind == "self":
                        # Show full property access expression for self-property
                        prop_name = property_node.name
                        if not prop_name.startswith("$"):
                            prop_name = "$" + prop_name
                        parts.append(f"$this->{prop_name.lstrip('$')} ({property_node.fqn})")
                    else:
                        parts.append(rname)
                on_display = ", ".join(parts)

            entry = ContextEntry(
                depth=depth,
                node_id=scope_id,
                fqn=scope_node.fqn,
                kind=scope_node.kind,
                file=first_call_node.file,
                line=first_line,
                signature=scope_node.signature,
                children=[],
                member_ref=member_ref,
                ref_type=reference_type or "property_access",
                callee=self._member_display_name(property_node),
                on=on_display,
                on_kind="property" if (receiver_names and receiver_names[0][1] == "self") else (receiver_names[0][1] if receiver_names else ok),
                sites=sites,
            )

            # Depth 2: trace result Values of each access
            if depth < max_depth:
                # Collect result Value IDs produced by property access calls
                # so we can filter constructor args to only the relevant one
                property_result_value_ids: set[str] = set()
                for call_id, call_node in calls:
                    result_id = self.index.get_produces(call_id)
                    if result_id:
                        property_result_value_ids.add(result_id)
                    if result_id and result_id not in visited:
                        child_entries = self._build_value_consumer_chain(
                            result_id, depth + 1, max_depth, limit, visited
                        )
                        entry.children.extend(child_entries)

                # ISSUE-O: Filter constructor/method args to only the one
                # matching the queried property. For each depth-2 child entry,
                # keep only arguments whose value traces back to our property.
                prop_name_bare = property_node.name.lstrip("$")
                for child_entry in entry.children:
                    if child_entry.arguments:
                        filtered_args = []
                        for arg in child_entry.arguments:
                            # Check if value_expr references the queried property
                            # e.g. "$savedOrder->id" ends with the property name "id"
                            if arg.value_expr and arg.value_expr.endswith(
                                f"->{prop_name_bare}"
                            ):
                                filtered_args.append(arg)
                            elif arg.value_expr and arg.value_expr.endswith(
                                f"->{property_node.name}"
                            ):
                                filtered_args.append(arg)
                            # Also check source_chain for property FQN reference
                            elif arg.source_chain:
                                for step in arg.source_chain:
                                    if isinstance(step, dict) and step.get("fqn") == property_node.fqn:
                                        filtered_args.append(arg)
                                        break
                        # Only apply filter if we found matches; if none match,
                        # keep all args (better to show too much than nothing)
                        if filtered_args:
                            child_entry.arguments = filtered_args

                # ISSUE-S+J fix: add upstream callers instead of downstream reads
                # If depth-2 children exist, replace their depth-3 children with callers
                # If no depth-2 children, add callers directly as depth-2 entries
                if entry.children and depth + 1 < max_depth:
                    caller_entries = self._build_caller_chain_for_method(
                        scope_id, depth + 2, max_depth
                    )
                    if caller_entries:
                        for child in entry.children:
                            child.children = caller_entries
                elif not entry.children:
                    caller_entries = self._build_caller_chain_for_method(
                        scope_id, depth + 1, max_depth
                    )
                    if caller_entries:
                        entry.children = caller_entries

            entries.append(entry)

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    # =================================================================
    # ISSUE-B: Class USED BY — grouped, sorted, deduped
    # =================================================================

    # Reference type priority for sorting USED BY entries
    _REF_TYPE_PRIORITY = {
        "instantiation": 0,
        "extends": 1,
        "implements": 1,
        "property_type": 2,
        "method_call": 3,
        "static_call": 3,
        "property_access": 4,
        "parameter_type": 5,
        "return_type": 5,
        "type_hint": 6,
    }

    def _build_class_used_by(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False
    ) -> list[ContextEntry]:
        """Build USED BY tree for a Class node with grouping, sorting, and dedup.

        Collects all incoming edges, classifies by reference type, groups into
        buckets (instantiation, extends, property_type, method_call,
        property_access, parameter_type/return_type), sorts by priority,
        and deduplicates property accesses by FQN with (xN) per method.

        Self-property accesses (class's own methods accessing its own properties)
        are excluded. Method calls through injected properties are shown at depth 2
        under the property_type entry (not as separate depth-1 entries).
        """
        start_node = self.index.nodes.get(start_id)
        if not start_node:
            return []

        # Collect all incoming edges grouped by source
        source_groups = self.index.get_usages_grouped(start_id)

        # Pass 1: Identify which containing classes have property_type refs to this class.
        # Method calls from those classes through the property should NOT appear at depth 1.
        classes_with_injection: set[str] = set()
        for source_id, edges in source_groups.items():
            source_node = self.index.nodes.get(source_id)
            if not source_node:
                continue
            for edge in edges:
                target_node = self.index.nodes.get(edge.target)
                if not target_node:
                    continue
                file = edge.location.get("file") if edge.location else None
                line = edge.location.get("line") if edge.location else None
                ref_type = _infer_reference_type(edge, target_node, self.index)
                if ref_type == "property_type":
                    # Find the containing class of this source
                    cls_id = source_id
                    node = source_node
                    while node and node.kind not in ("Class", "Interface", "Trait", "Enum", "File"):
                        cls_id = self.index.get_contains_parent(cls_id)
                        node = self.index.nodes.get(cls_id) if cls_id else None
                    if node and node.kind in ("Class", "Interface", "Trait", "Enum"):
                        classes_with_injection.add(cls_id)

        # Pass 2: Classify each edge into buckets
        instantiation_entries: list[ContextEntry] = []
        extends_entries: list[ContextEntry] = []
        property_type_entries: list[ContextEntry] = []
        method_call_entries: list[ContextEntry] = []
        property_access_groups: dict[str, list[dict]] = {}
        param_return_entries: list[ContextEntry] = []
        seen_instantiation_methods: set[str] = set()
        seen_property_type_props: set[str] = set()

        visited_sources: set[str] = {start_id}

        # Pre-collect extends/implements relationships (not in uses edges)
        extends_children_ids = self.index.get_extends_children(start_id)
        for child_id in extends_children_ids:
            child_node = self.index.nodes.get(child_id)
            if child_node and child_id not in visited_sources:
                visited_sources.add(child_id)
                entry = ContextEntry(
                    depth=1,
                    node_id=child_id,
                    fqn=child_node.fqn,
                    kind=child_node.kind,
                    file=child_node.file,
                    line=child_node.start_line,
                    ref_type="extends",
                    children=[],
                )
                extends_entries.append(entry)

        implementor_ids = self.index.get_implementors(start_id)
        for impl_id in implementor_ids:
            impl_node = self.index.nodes.get(impl_id)
            if impl_node and impl_id not in visited_sources:
                visited_sources.add(impl_id)
                entry = ContextEntry(
                    depth=1,
                    node_id=impl_id,
                    fqn=impl_node.fqn,
                    kind=impl_node.kind,
                    file=impl_node.file,
                    line=impl_node.start_line,
                    ref_type="implements",
                    children=[],
                )
                extends_entries.append(entry)

        for source_id, edges in source_groups.items():
            if source_id in visited_sources:
                continue

            # R3: Filter out internal self-references
            if self._is_internal_reference(source_id, start_id):
                continue

            source_node = self.index.nodes.get(source_id)
            if not source_node:
                continue

            if source_node.kind == "File":
                continue

            visited_sources.add(source_id)

            for edge in edges:
                target_node = self.index.nodes.get(edge.target)
                if not target_node:
                    continue

                file = edge.location.get("file") if edge.location else source_node.file
                line = edge.location.get("line") if edge.location else source_node.start_line

                call_node_id = find_call_for_usage(self.index, source_id, edge.target, file, line)
                if call_node_id:
                    ref_type = get_reference_type_from_call(self.index, call_node_id)
                else:
                    ref_type = _infer_reference_type(edge, target_node, self.index)

                if ref_type == "instantiation":
                    containing_method_id = self._resolve_containing_method(source_id)
                    containing_method = self.index.nodes.get(containing_method_id) if containing_method_id else None
                    method_key = containing_method_id or source_id
                    if method_key in seen_instantiation_methods:
                        continue
                    seen_instantiation_methods.add(method_key)

                    entry_fqn = containing_method.fqn if containing_method else source_node.fqn
                    if containing_method and containing_method.kind == "Method" and not entry_fqn.endswith("()"):
                        entry_fqn += "()"

                    arguments = []
                    if call_node_id:
                        arguments = self._get_argument_info(call_node_id)

                    entry = ContextEntry(
                        depth=1,
                        node_id=method_key,
                        fqn=entry_fqn,
                        kind=containing_method.kind if containing_method else source_node.kind,
                        file=file,
                        line=line,
                        ref_type="instantiation",
                        children=[],
                        arguments=arguments,
                    )
                    instantiation_entries.append(entry)

                elif ref_type == "extends":
                    entry = ContextEntry(
                        depth=1,
                        node_id=source_id,
                        fqn=source_node.fqn,
                        kind=source_node.kind,
                        file=source_node.file,
                        line=source_node.start_line,
                        ref_type="extends",
                        children=[],
                    )
                    extends_entries.append(entry)

                elif ref_type == "implements":
                    entry = ContextEntry(
                        depth=1,
                        node_id=source_id,
                        fqn=source_node.fqn,
                        kind=source_node.kind,
                        file=source_node.file,
                        line=source_node.start_line,
                        ref_type="implements",
                        children=[],
                    )
                    extends_entries.append(entry)

                elif ref_type == "property_type":
                    prop_fqn = None
                    prop_node = None
                    if source_node.kind == "Property":
                        prop_fqn = source_node.fqn
                        prop_node = source_node
                    elif source_node.kind in ("Method", "Function"):
                        containing_class_id = self.index.get_contains_parent(source_id)
                        if containing_class_id:
                            for child_id in self.index.get_contains_children(containing_class_id):
                                child = self.index.nodes.get(child_id)
                                if child and child.kind == "Property":
                                    for th_edge in self.index.outgoing[child_id].get("type_hint", []):
                                        if th_edge.target == start_id:
                                            prop_fqn = child.fqn
                                            prop_node = child
                                            break
                                    if prop_fqn:
                                        break

                    if prop_fqn and prop_node and prop_fqn not in seen_property_type_props:
                        seen_property_type_props.add(prop_fqn)
                        entry = ContextEntry(
                            depth=1,
                            node_id=prop_node.id,
                            fqn=prop_fqn,
                            kind="Property",
                            file=prop_node.file,
                            line=prop_node.start_line,
                            ref_type="property_type",
                            children=[],
                        )
                        property_type_entries.append(entry)

                elif ref_type == "method_call":
                    # Suppress method_call if the containing class has a property_type
                    # injection for this target class (those calls show at depth 2)
                    containing_method_id = self._resolve_containing_method(source_id)
                    containing_class_id = None
                    if containing_method_id:
                        containing_class_id = self.index.get_contains_parent(containing_method_id)
                    if containing_class_id and containing_class_id in classes_with_injection:
                        continue

                    containing_method = self.index.nodes.get(containing_method_id) if containing_method_id else None

                    callee_name = target_node.name + "()" if target_node.kind == "Method" else None
                    on_expr = None
                    on_kind = None
                    if call_node_id:
                        ac, acs, ok, of, ol = self._resolve_receiver_identity(call_node_id)
                        on_expr = ac
                        on_kind = ok

                    method_fqn = containing_method.fqn if containing_method else source_node.fqn
                    if containing_method and containing_method.kind == "Method" and not method_fqn.endswith("()"):
                        method_fqn += "()"

                    entry = ContextEntry(
                        depth=1,
                        node_id=containing_method_id or source_id,
                        fqn=method_fqn,
                        kind=containing_method.kind if containing_method else source_node.kind,
                        file=file,
                        line=line,
                        ref_type="method_call",
                        callee=callee_name,
                        on=on_expr,
                        on_kind=on_kind,
                        children=[],
                    )
                    method_call_entries.append(entry)

                elif ref_type == "property_access":
                    prop_fqn = target_node.fqn
                    containing_method_id = self._resolve_containing_method(source_id)
                    containing_method = self.index.nodes.get(containing_method_id) if containing_method_id else None
                    method_fqn = containing_method.fqn if containing_method else source_node.fqn

                    on_expr = None
                    on_kind = None
                    if call_node_id:
                        ac, acs, ok, of, ol = self._resolve_receiver_identity(call_node_id)
                        on_expr = ac
                        on_kind = ok

                    if prop_fqn not in property_access_groups:
                        property_access_groups[prop_fqn] = []

                    found = False
                    for group_entry in property_access_groups[prop_fqn]:
                        if group_entry["method_fqn"] == method_fqn:
                            group_entry["lines"].append(line)
                            found = True
                            break
                    if not found:
                        property_access_groups[prop_fqn].append({
                            "method_fqn": method_fqn,
                            "method_id": containing_method_id or source_id,
                            "method_kind": containing_method.kind if containing_method else source_node.kind,
                            "lines": [line],
                            "on_expr": on_expr,
                            "on_kind": on_kind,
                            "file": file,
                        })

                elif ref_type in ("parameter_type", "return_type", "type_hint"):
                    # For return_type, show method-level FQN instead of class-level
                    if ref_type == "return_type" and source_node.kind in ("Method", "Function"):
                        method_fqn = source_node.fqn
                        if source_node.kind == "Method" and not method_fqn.endswith("()"):
                            method_fqn += "()"
                        already_exists = any(e.fqn == method_fqn for e in param_return_entries)
                        if not already_exists:
                            entry = ContextEntry(
                                depth=1,
                                node_id=source_id,
                                fqn=method_fqn,
                                kind=source_node.kind,
                                file=source_node.file,
                                line=source_node.start_line,
                                signature=source_node.signature,
                                ref_type=ref_type,
                                children=[],
                            )
                            param_return_entries.append(entry)
                        continue

                    # Group by containing class
                    cls_id = source_id
                    node = source_node
                    while node and node.kind not in ("Class", "Interface", "Trait", "Enum", "File"):
                        cls_id = self.index.get_contains_parent(cls_id)
                        node = self.index.nodes.get(cls_id) if cls_id else None
                    if not node or node.kind not in ("Class", "Interface", "Trait", "Enum"):
                        continue

                    already_exists = any(e.fqn == node.fqn for e in param_return_entries)
                    if not already_exists:
                        entry = ContextEntry(
                            depth=1,
                            node_id=cls_id,
                            fqn=node.fqn,
                            kind=node.kind,
                            file=node.file,
                            line=node.start_line,
                            ref_type=ref_type,
                            children=[],
                        )
                        param_return_entries.append(entry)

        # Build property access group entries
        property_access_entries: list[ContextEntry] = []
        for prop_fqn, method_groups in property_access_groups.items():
            total_accesses = sum(len(g["lines"]) for g in method_groups)
            total_methods = len(method_groups)

            # Build depth-2 children: per-method breakdown
            method_children: list[ContextEntry] = []
            if max_depth >= 2:
                for group in method_groups:
                    method_short = group["method_fqn"].split("::")[-1] if "::" in group["method_fqn"] else group["method_fqn"]
                    method_node = self.index.nodes.get(group["method_id"])
                    if method_node and method_node.kind == "Method" and not method_short.endswith("()"):
                        method_short = method_short + "()"
                    class_part = group["method_fqn"].split("::")[0].split("\\")[-1] if "::" in group["method_fqn"] else ""
                    child_display = f"{class_part}::{method_short}" if class_part else method_short

                    count = len(group["lines"])
                    lines_sorted = sorted(l for l in group["lines"] if l is not None)
                    first_line = lines_sorted[0] if lines_sorted else None

                    sites = None
                    if count > 1 and lines_sorted:
                        sites = [{"line": l} for l in lines_sorted]

                    child_entry = ContextEntry(
                        depth=2,
                        node_id=group["method_id"],
                        fqn=child_display,
                        kind=group["method_kind"],
                        file=group["file"],
                        line=first_line,
                        ref_type="property_access",
                        on=group["on_expr"],
                        on_kind=group["on_kind"],
                        sites=sites,
                        children=[],
                    )

                    if max_depth >= 3 and group["method_id"]:
                        child_entry.children = self._build_class_used_by_depth_callers(
                            group["method_id"], 3, max_depth, set(visited_sources)
                        )

                    method_children.append(child_entry)

            method_children.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

            # Use the full property FQN but with short class name for display
            prop_short = prop_fqn.split("::")[-1] if "::" in prop_fqn else prop_fqn
            class_short = prop_fqn.split("::")[0].split("\\")[-1] if "::" in prop_fqn else ""
            display_fqn = f"{class_short}::{prop_short}" if class_short else prop_short

            prop_entry = ContextEntry(
                depth=1,
                node_id=prop_fqn,
                fqn=display_fqn,
                kind="PropertyGroup",
                file=None,
                line=None,
                ref_type="property_access",
                children=method_children,
                access_count=total_accesses,
                method_count=total_methods,
            )
            property_access_entries.append(prop_entry)

        # Pass 3: Via-interface usedBy — collect injection points from interfaces
        # this class implements. If a property is typed to the interface, it
        # indirectly references this concrete class.
        via_interface_entries: list[ContextEntry] = []
        impl_ids = self.index.get_implements(start_id)
        # Also check extends chain for interfaces
        extends_parent_id = self.index.get_extends_parent(start_id)
        while extends_parent_id:
            impl_ids.extend(self.index.get_implements(extends_parent_id))
            extends_parent_id = self.index.get_extends_parent(extends_parent_id)

        for iface_id in impl_ids:
            iface_node = self.index.nodes.get(iface_id)
            if not iface_node:
                continue
            # Collect property_type injection points for this interface
            iface_source_groups = self.index.get_usages_grouped(iface_id)
            for source_id, edges in iface_source_groups.items():
                source_node = self.index.nodes.get(source_id)
                if not source_node:
                    continue
                for edge in edges:
                    target_node = self.index.nodes.get(edge.target)
                    if not target_node:
                        continue
                    ref_type = _infer_reference_type(edge, target_node, self.index)
                    if ref_type != "property_type":
                        continue
                    # Resolve the property node
                    prop_fqn = None
                    prop_node = None
                    if source_node.kind == "Property":
                        prop_fqn = source_node.fqn
                        prop_node = source_node
                    elif source_node.kind in ("Method", "Function"):
                        containing_class_id = self.index.get_contains_parent(source_id)
                        if containing_class_id:
                            for child_id in self.index.get_contains_children(containing_class_id):
                                child = self.index.nodes.get(child_id)
                                if child and child.kind == "Property":
                                    for th_edge in self.index.outgoing[child_id].get("type_hint", []):
                                        if th_edge.target == iface_id:
                                            prop_fqn = child.fqn
                                            prop_node = child
                                            break
                                    if prop_fqn:
                                        break
                    if not prop_fqn or not prop_node:
                        continue
                    if prop_fqn in seen_property_type_props:
                        continue
                    seen_property_type_props.add(prop_fqn)

                    entry = ContextEntry(
                        depth=1,
                        node_id=prop_node.id,
                        fqn=prop_fqn,
                        kind="Property",
                        file=prop_node.file,
                        line=prop_node.start_line,
                        ref_type="property_type",
                        via=iface_node.fqn,
                        children=[],
                    )
                    via_interface_entries.append(entry)

        via_interface_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

        # Sort within each group
        instantiation_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        extends_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        property_type_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        method_call_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        property_access_entries.sort(key=lambda e: e.fqn)
        param_return_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

        # Expand depth-2
        if max_depth >= 2:
            for entry in instantiation_entries:
                entry.children = self._build_class_used_by_depth_callers(
                    entry.node_id, 2, max_depth, set(visited_sources)
                )
            for entry in extends_entries:
                if entry.ref_type in ("extends", "implements"):
                    entry.children = self._build_override_methods_for_subclass(
                        entry.node_id, start_id, 2, max_depth
                    )
            for entry in property_type_entries:
                entry.children = self._build_injection_point_calls(
                    entry.node_id, start_id, 2, max_depth
                )
            for entry in via_interface_entries:
                # For via-interface entries, find the interface ID from the via FQN
                iface_nodes = self.index.resolve_symbol(entry.via) if entry.via else []
                iface_id = iface_nodes[0].id if iface_nodes else None
                if iface_id:
                    entry.children = self._build_interface_injection_point_calls(
                        entry.node_id, iface_id, 2, max_depth
                    )

        # Combine in priority order
        all_entries = (
            instantiation_entries
            + extends_entries
            + property_type_entries
            + via_interface_entries
            + method_call_entries
            + property_access_entries
            + param_return_entries
        )

        return all_entries[:limit]

    def _build_caller_chain(
        self, call_site_id: str, depth: int, max_depth: int, visited: set[str] | None = None
    ) -> list[ContextEntry]:
        """Build upstream caller chain from a call site node.

        Given a Call node (e.g., a method_call or property_access at depth 2),
        find the containing method, then find callers of that method using
        override root resolution to handle interface/concrete method lookups.

        This is the shared helper for depth-3 expansion in:
        - _build_property_used_by (property USED BY)
        - _build_injection_point_calls (class USED BY > property_type)
        - _build_interface_injection_point_calls (interface USED BY > property_type)
        """
        if depth > max_depth:
            return []

        if visited is None:
            visited = set()

        # Find the containing method of the call site
        scope_id = get_containing_scope(self.index, call_site_id)
        if not scope_id or scope_id in visited:
            return []

        return self._build_caller_chain_for_method(scope_id, depth, max_depth, visited)

    def _build_caller_chain_for_method(
        self, method_id: str, depth: int, max_depth: int, visited: set[str] | None = None
    ) -> list[ContextEntry]:
        """Build upstream caller chain starting from a known method ID.

        Finds callers of the given method using override root resolution:
        callers may reference the interface method rather than the concrete
        implementation, so we check both.
        """
        if depth > max_depth:
            return []

        if visited is None:
            visited = set()

        method_node = self.index.nodes.get(method_id)
        if not method_node or method_node.kind not in ("Method", "Function"):
            return []

        if method_id in visited:
            return []
        visited.add(method_id)

        # Find callers via override root resolution
        override_root = self.index.get_override_root(method_id)
        caller_method_ids: set[str] = set()

        # Collect callers of the method itself
        self._collect_callers_from_usages(method_id, visited, caller_method_ids)

        # Also collect callers of the override root (interface method)
        if override_root and override_root != method_id:
            self._collect_callers_from_usages(override_root, visited, caller_method_ids)

        # Build caller entries
        entries = []
        for caller_id in caller_method_ids:
            caller_node = self.index.nodes.get(caller_id)
            if not caller_node:
                continue

            display_fqn = caller_node.fqn
            if caller_node.kind == "Method" and not display_fqn.endswith("()"):
                display_fqn += "()"

            entry = ContextEntry(
                depth=depth,
                node_id=caller_id,
                fqn=display_fqn,
                kind=caller_node.kind,
                file=caller_node.file,
                line=caller_node.start_line,
                ref_type="caller",
                children=[],
            )

            # Recursive caller expansion
            if depth < max_depth:
                entry.children = self._build_caller_chain_for_method(
                    caller_id, depth + 1, max_depth, visited.copy()
                )

            entries.append(entry)

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _collect_callers_from_usages(
        self, target_method_id: str, visited: set[str], result: set[str]
    ) -> None:
        """Collect containing methods of all chainable usages of a target method."""
        for source_id, edges in self.index.get_usages_grouped(target_method_id).items():
            for edge in edges:
                call_node_id = find_call_for_usage(
                    self.index, source_id, edge.target,
                    edge.location.get("file") if edge.location else None,
                    edge.location.get("line") if edge.location else None,
                )
                if call_node_id:
                    ref_type = get_reference_type_from_call(self.index, call_node_id)
                else:
                    target_node = self.index.nodes.get(edge.target)
                    ref_type = _infer_reference_type(edge, target_node, self.index) if target_node else "uses"
                if ref_type in CHAINABLE_REFERENCE_TYPES:
                    containing = self._resolve_containing_method(source_id)
                    if containing and containing not in visited:
                        result.add(containing)

    def _build_class_used_by_depth_callers(
        self, method_id: str, depth: int, max_depth: int, visited: set[str]
    ) -> list[ContextEntry]:
        """Find callers of a method for depth expansion in class USED BY.

        For instantiation and property_access depth expansion: find who calls
        the containing method.
        """
        if depth > max_depth:
            return []

        method_node = self.index.nodes.get(method_id)
        if not method_node or method_node.kind not in ("Method", "Function"):
            return []

        entries = []
        source_groups = self.index.get_usages_grouped(method_id)

        for source_id, edges in source_groups.items():
            if source_id in visited:
                continue
            visited.add(source_id)

            source_node = self.index.nodes.get(source_id)
            if not source_node:
                continue
            if source_node.kind == "File":
                continue

            for edge in edges:
                file = edge.location.get("file") if edge.location else source_node.file
                line = edge.location.get("line") if edge.location else source_node.start_line

                call_node_id = find_call_for_usage(self.index, source_id, edge.target, file, line)
                if call_node_id:
                    ref_type = get_reference_type_from_call(self.index, call_node_id)
                else:
                    target_node = self.index.nodes.get(edge.target)
                    ref_type = _infer_reference_type(edge, target_node, self.index) if target_node else "uses"

                if ref_type not in CHAINABLE_REFERENCE_TYPES:
                    continue

                # Resolve containing method
                containing_method_id = self._resolve_containing_method(source_id)
                containing_method = self.index.nodes.get(containing_method_id) if containing_method_id else None

                callee_name = method_node.name + "()" if method_node.kind == "Method" else method_node.name
                on_expr = None
                on_kind = None
                if call_node_id:
                    ac, acs, ok, of, ol = self._resolve_receiver_identity(call_node_id)
                    on_expr = ac
                    on_kind = ok
                    # Detect "property" from access chain pattern ($this->prop)
                    if on_kind is None and on_expr and on_expr.startswith("$this->"):
                        on_kind = "property"

                display_fqn = containing_method.fqn if containing_method else source_node.fqn
                if containing_method and containing_method.kind == "Method":
                    if not display_fqn.endswith("()"):
                        display_fqn += "()"

                # Use "caller" refType for depth 3+ entries (upstream callers)
                entry_ref_type = "caller" if depth >= 3 else "method_call"

                entry = ContextEntry(
                    depth=depth,
                    node_id=containing_method_id or source_id,
                    fqn=display_fqn,
                    kind=containing_method.kind if containing_method else source_node.kind,
                    file=file,
                    line=line,
                    ref_type=entry_ref_type,
                    callee=callee_name if entry_ref_type != "caller" else None,
                    on=on_expr,
                    on_kind=on_kind,
                    children=[],
                )

                # Further depth expansion
                if depth < max_depth and containing_method_id:
                    entry.children = self._build_class_used_by_depth_callers(
                        containing_method_id, depth + 1, max_depth, visited
                    )

                entries.append(entry)
                break  # One entry per source

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _build_override_methods_for_subclass(
        self, subclass_id: str, parent_class_id: str, depth: int, max_depth: int
    ) -> list[ContextEntry]:
        """Build override method entries for a subclass under [extends] in USED BY.

        Shows which methods the subclass overrides from the parent class/interface.
        Uses get_overrides_parent() directly to detect overrides, which handles
        the full hierarchy chain including grandparent methods (ISSUE-E fix).
        """
        if depth > max_depth:
            return []

        entries = []

        # Find override methods in the subclass using direct overrides edge check.
        # This handles grandparent methods (e.g., findAll from BaseRepositoryInterface)
        # that the old name-matching approach missed.
        for child_id in self.index.get_contains_children(subclass_id):
            child = self.index.nodes.get(child_id)
            if not child or child.kind != "Method" or child.name == "__construct":
                continue

            # Check if this method overrides ANY ancestor method via the overrides edge
            override_parent_id = self.index.get_overrides_parent(child_id)
            if override_parent_id:
                entry = ContextEntry(
                    depth=depth,
                    node_id=child_id,
                    fqn=child.fqn,
                    kind="Method",
                    file=child.file,
                    line=child.start_line,
                    signature=child.signature,
                    ref_type="override",
                    children=[],
                )

                # At depth 3, show what the override method does internally
                if depth < max_depth:
                    entry.children = self._build_override_method_internals(
                        child_id, depth + 1, max_depth
                    )

                entries.append(entry)

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _build_override_method_internals(
        self, method_id: str, depth: int, max_depth: int
    ) -> list[ContextEntry]:
        """Show internal actions of an override method (property_access, method_call).

        Used at depth 3+ under [extends] > [override] entries.
        """
        if depth > max_depth:
            return []

        entries = []
        # Use execution flow to find what this method does
        call_children = []
        for child_id in self.index.get_contains_children(method_id):
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Call":
                call_children.append((child_id, child))

        for call_id, call_node in call_children:
            target_id = self.index.get_call_target(call_id)
            if not target_id:
                continue
            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue

            # Filter out property access noise at depth 3 — these are
            # implementation details, not class-level dependencies
            if target_node.kind in ("Property", "StaticProperty"):
                continue

            ref_type = get_reference_type_from_call(self.index, call_id)
            ac, acs, ok, of, ol = self._resolve_receiver_identity(call_id)
            call_line = call_node.range.get("start_line") if call_node.range else None

            entry = ContextEntry(
                depth=depth,
                node_id=target_id,
                fqn=target_node.fqn,
                kind=target_node.kind,
                file=call_node.file,
                line=call_line,
                ref_type=ref_type,
                on=ac,
                on_kind=ok,
                children=[],
            )
            entries.append(entry)

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _build_injection_point_calls(
        self, property_id: str, target_class_id: str, depth: int, max_depth: int
    ) -> list[ContextEntry]:
        """Build method call entries for an injection point [property_type].

        For a property like OrderService::$orderRepository that is typed to
        target_class_id, find all method calls made through that property.
        """
        if depth > max_depth:
            return []

        prop_node = self.index.nodes.get(property_id)
        if not prop_node or prop_node.kind != "Property":
            return []

        # Find the containing class of this property
        containing_class_id = self.index.get_contains_parent(property_id)
        if not containing_class_id:
            return []

        entries = []
        seen_callees: set[str] = set()

        # Find all Call nodes in the containing class's methods that use this property as receiver
        for method_child_id in self.index.get_contains_children(containing_class_id):
            method_node = self.index.nodes.get(method_child_id)
            if not method_node or method_node.kind != "Method":
                continue

            for call_child_id in self.index.get_contains_children(method_child_id):
                call_child = self.index.nodes.get(call_child_id)
                if not call_child or call_child.kind != "Call":
                    continue

                # Check if this call's receiver is our property
                recv_id = self.index.get_receiver(call_child_id)
                if not recv_id:
                    continue

                # Resolve receiver to check if it matches our property
                chain_symbol = resolve_access_chain_symbol(self.index, call_child_id)
                if chain_symbol != prop_node.fqn:
                    continue

                target_id = self.index.get_call_target(call_child_id)
                if not target_id:
                    continue
                target_node = self.index.nodes.get(target_id)
                if not target_node:
                    continue

                callee_name = target_node.name + "()" if target_node.kind == "Method" else target_node.name
                ref_type = get_reference_type_from_call(self.index, call_child_id)
                ac, acs, ok, of, ol = self._resolve_receiver_identity(call_child_id)
                arguments = self._get_argument_info(call_child_id)
                call_line = call_child.range.get("start_line") if call_child.range else None

                # Dedup: same callee method, collect as sites
                callee_key = target_node.fqn
                if callee_key in seen_callees:
                    # Find existing entry and add site
                    for existing in entries:
                        if existing.fqn == target_node.fqn:
                            if existing.sites is None:
                                existing.sites = [{"method": method_node.name, "line": existing.line}]
                                existing.line = None
                            existing.sites.append({"method": method_node.name, "line": call_line})
                            break
                    continue
                seen_callees.add(callee_key)

                entry = ContextEntry(
                    depth=depth,
                    node_id=target_id,
                    fqn=target_node.fqn,
                    kind=target_node.kind,
                    file=call_child.file,
                    line=call_line,
                    ref_type="method_call",
                    callee=callee_name,
                    on=ac,
                    on_kind="property",
                    arguments=arguments,
                    children=[],
                )

                # Depth 3: show callers of the containing method (ISSUE-S+J fix)
                # If no callers found, show the containing method itself as terminal
                if depth < max_depth and method_child_id:
                    callers = self._build_caller_chain_for_method(
                        method_child_id, depth + 1, max_depth
                    )
                    if callers:
                        entry.children = callers
                    else:
                        # Terminal: show the containing method itself as a caller node
                        method_n = self.index.nodes.get(method_child_id)
                        if method_n:
                            display = method_n.fqn
                            if method_n.kind == "Method" and not display.endswith("()"):
                                display += "()"
                            entry.children = [ContextEntry(
                                depth=depth + 1,
                                node_id=method_child_id,
                                fqn=display,
                                kind=method_n.kind,
                                file=method_n.file,
                                line=method_n.start_line,
                                ref_type="caller",
                                children=[],
                            )]

                entries.append(entry)

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    # =================================================================
    # ISSUE-C: Class USES — grouped, deduped, behavioral depth 2
    # =================================================================

    def _build_class_uses(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False
    ) -> list[ContextEntry]:
        """Build USES tree for a Class node with dedup and semantic grouping.

        Shows one entry per unique external dependency class/interface.
        Classifies each as [extends], [implements], [property_type],
        [parameter_type], [return_type], [instantiation].

        At depth 2:
        - property_type deps: behavioral (method calls on the dep)
        - extends/implements: override and inherited methods
        - non-property deps: recursive class-level expansion
        """
        start_node = self.index.nodes.get(start_id)
        if not start_node:
            return []

        # Collect all outgoing dependencies from the class and its members
        edges = self.index.get_deps(start_id, include_members=True)

        # Also collect extends and implements edges directly
        extends_edges = self.index.outgoing[start_id].get("extends", [])
        implements_edges = self.index.outgoing[start_id].get("implements", [])

        # Deduplicate by target class/interface — track best ref_type per target
        target_info: dict[str, dict] = {}  # target_id -> {ref_type, file, line, property_name, ...}

        # Process extends first (highest structural priority)
        for edge in extends_edges:
            target_id = edge.target
            if target_id == start_id:
                continue
            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue
            target_info[target_id] = {
                "ref_type": "extends",
                "file": start_node.file,
                "line": start_node.start_line,
                "property_name": None,
                "node": target_node,
            }

        # Process implements — file ref points to the class declaration
        for edge in implements_edges:
            target_id = edge.target
            if target_id == start_id or target_id in target_info:
                continue
            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue
            target_info[target_id] = {
                "ref_type": "implements",
                "file": start_node.file,
                "line": start_node.start_line,
                "property_name": None,
                "node": target_node,
            }

        # Pre-collect type_hint edges from class members to classify targets accurately
        # type_hint from Property -> target = property_type
        # type_hint from Argument -> target = parameter_type
        # type_hint from Method -> target = return_type
        type_hint_info: dict[str, dict] = {}  # target_class_id -> {ref_type, property_name, file, line}
        for child_id in self.index.get_contains_children(start_id):
            child = self.index.nodes.get(child_id)
            if not child:
                continue

            # Property type_hints -> property_type
            if child.kind == "Property":
                for th_edge in self.index.outgoing.get(child_id, {}).get("type_hint", []):
                    tid = th_edge.target
                    prop_name = child.name
                    if not prop_name.startswith("$"):
                        prop_name = "$" + prop_name
                    type_hint_info[tid] = {
                        "ref_type": "property_type",
                        "property_name": prop_name,
                        "file": child.file,
                        "line": child.start_line,
                    }

            # Method return type_hints -> return_type, Argument type_hints -> parameter_type
            if child.kind == "Method":
                for th_edge in self.index.outgoing.get(child_id, {}).get("type_hint", []):
                    tid = th_edge.target
                    if tid not in type_hint_info:
                        type_hint_info[tid] = {
                            "ref_type": "return_type",
                            "property_name": None,
                            "file": child.file,
                            "line": child.start_line,
                        }

                # Check sub-children (Arguments)
                for sub_id in self.index.get_contains_children(child_id):
                    sub = self.index.nodes.get(sub_id)
                    if not sub:
                        continue
                    if sub.kind == "Argument":
                        for th_edge in self.index.outgoing.get(sub_id, {}).get("type_hint", []):
                            tid = th_edge.target
                            existing = type_hint_info.get(tid)
                            # parameter_type wins over return_type but not over property_type
                            if not existing or existing["ref_type"] == "return_type":
                                type_hint_info[tid] = {
                                    "ref_type": "parameter_type",
                                    "property_name": None,
                                    "file": child.file,
                                    "line": child.start_line,
                                }

        # Pre-collect constructor calls to detect instantiation targets
        instantiation_targets: dict[str, dict] = {}  # target_class_id -> {file, line}
        for child_id in self.index.get_contains_children(start_id):
            child = self.index.nodes.get(child_id)
            if not child or child.kind != "Method":
                continue
            for call_child_id in self.index.get_contains_children(child_id):
                call_child = self.index.nodes.get(call_child_id)
                if not call_child or call_child.kind != "Call":
                    continue
                target_id = self.index.get_call_target(call_child_id)
                if not target_id:
                    continue
                target_node = self.index.nodes.get(target_id)
                if not target_node:
                    continue
                # Constructor call -> instantiation of the containing class
                if target_node.kind == "Method" and target_node.name == "__construct":
                    cls_id = self.index.get_contains_parent(target_id)
                    if cls_id and cls_id != start_id:
                        call_line = call_child.range.get("start_line") if call_child.range else None
                        if cls_id not in instantiation_targets:
                            instantiation_targets[cls_id] = {
                                "file": call_child.file,
                                "line": call_line,
                            }

        # Process uses edges — classify each and pick best ref_type per target
        for edge in edges:
            target_id = edge.target
            if target_id == start_id:
                continue

            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue

            # We only care about class/interface level deps
            # For member targets (Method, Property), resolve to containing class
            resolved_target_id = target_id
            resolved_target = target_node
            if target_node.kind in ("Method", "Property", "Argument", "Value", "Call", "Constant"):
                parent_id = self.index.get_contains_parent(target_id)
                if parent_id:
                    parent = self.index.nodes.get(parent_id)
                    if parent and parent.kind in ("Class", "Interface", "Trait", "Enum"):
                        resolved_target_id = parent_id
                        resolved_target = parent
                    else:
                        continue
                else:
                    continue

            # Skip self-references (class accessing its own members)
            if resolved_target_id == start_id:
                continue

            # Skip already-tracked extends/implements
            if resolved_target_id in target_info and target_info[resolved_target_id]["ref_type"] in ("extends", "implements"):
                continue

            file = edge.location.get("file") if edge.location else None
            line = edge.location.get("line") if edge.location else None

            # Classify this reference using pre-collected info
            # Priority: property_type > return_type > instantiation > parameter_type
            # property_type is the strongest signal (structural injection via property).
            # return_type wins over instantiation (constructor call serves the return).
            # instantiation wins over parameter_type (creating > receiving).
            ref_type = None
            property_name = None

            # Check type_hint-based classification
            if resolved_target_id in type_hint_info:
                th_info = type_hint_info[resolved_target_id]
                th_ref = th_info["ref_type"]
                # property_type and return_type always win
                if th_ref in ("property_type", "return_type"):
                    ref_type = th_ref
                    property_name = th_info.get("property_name")
                    file = th_info["file"] or file
                    line = th_info["line"] if th_info["line"] is not None else line

            # Check for instantiation (wins over parameter_type but not property_type/return_type)
            if ref_type is None and resolved_target_id in instantiation_targets:
                inst_info = instantiation_targets[resolved_target_id]
                ref_type = "instantiation"
                file = inst_info["file"] or file
                line = inst_info["line"] if inst_info["line"] is not None else line

            # Fall back to remaining type_hint classification (parameter_type)
            if ref_type is None and resolved_target_id in type_hint_info:
                th_info = type_hint_info[resolved_target_id]
                ref_type = th_info["ref_type"]
                property_name = th_info.get("property_name")
                file = th_info["file"] or file
                line = th_info["line"] if th_info["line"] is not None else line

            # Fall back to edge-level inference
            if ref_type is None:
                ref_type = _infer_reference_type(edge, target_node, self.index)
                # For property_type from edge inference, resolve property name
                if ref_type == "property_type":
                    source_node = self.index.nodes.get(edge.source)
                    if source_node and source_node.kind == "Property":
                        property_name = source_node.name
                        if not property_name.startswith("$"):
                            property_name = "$" + property_name
                        file = source_node.file
                        line = source_node.start_line

            # Priority for dedup: instantiation > property_type > parameter_type > return_type > type_hint
            priority_map = {
                "instantiation": 0,
                "property_type": 1,
                "method_call": 2,
                "property_access": 2,
                "parameter_type": 3,
                "return_type": 4,
                "type_hint": 5,
            }

            if resolved_target_id in target_info:
                existing = target_info[resolved_target_id]
                existing_priority = priority_map.get(existing["ref_type"], 10)
                new_priority = priority_map.get(ref_type, 10)
                if new_priority < existing_priority:
                    target_info[resolved_target_id] = {
                        "ref_type": ref_type,
                        "file": file or existing["file"],
                        "line": line if line is not None else existing["line"],
                        "property_name": property_name or existing.get("property_name"),
                        "node": resolved_target,
                    }
                elif property_name and not existing.get("property_name"):
                    existing["property_name"] = property_name
            else:
                target_info[resolved_target_id] = {
                    "ref_type": ref_type,
                    "file": file or resolved_target.file,
                    "line": line if line is not None else resolved_target.start_line,
                    "property_name": property_name,
                    "node": resolved_target,
                }

        # Ensure type_hint targets (property_type, parameter_type, return_type)
        # that were not reached via "uses" edges are still included.
        for tid, th_info in type_hint_info.items():
            if tid in target_info or tid == start_id:
                continue
            target_node = self.index.nodes.get(tid)
            if not target_node or target_node.kind not in ("Class", "Interface", "Trait", "Enum"):
                continue
            target_info[tid] = {
                "ref_type": th_info["ref_type"],
                "file": th_info["file"] or target_node.file,
                "line": th_info["line"] if th_info["line"] is not None else target_node.start_line,
                "property_name": th_info.get("property_name"),
                "node": target_node,
            }

        # Build entries
        entries: list[ContextEntry] = []
        for target_id, info in target_info.items():
            target_node = info["node"]
            ref_type = info["ref_type"]
            file = info["file"]
            line = info["line"]
            property_name = info.get("property_name")

            entry = ContextEntry(
                depth=1,
                node_id=target_id,
                fqn=target_node.fqn,
                kind=target_node.kind,
                file=file,
                line=line,
                ref_type=ref_type,
                property_name=property_name,
                children=[],
            )

            # Depth 2 expansion based on ref_type
            if max_depth >= 2:
                if ref_type == "extends":
                    entry.children = self._build_extends_depth2(
                        start_id, target_id, 2, max_depth
                    )
                elif ref_type == "implements":
                    entry.children = self._build_implements_depth2(
                        start_id, target_id, 2, max_depth
                    )
                elif ref_type == "property_type":
                    # Behavioral: show method calls on this dep through the property
                    entry.children = self._build_behavioral_depth2(
                        start_id, target_id, property_name, 2, max_depth
                    )
                else:
                    # Non-property deps: recursive class-level expansion
                    entry.children = self._build_class_uses_recursive(
                        target_id, 2, max_depth, limit, {start_id}
                    )

            entries.append(entry)

        # Sort by USES-specific priority
        # extends/implements first, then property_type, then param/return, then instantiation
        uses_priority = {
            "extends": 0,
            "implements": 0,
            "property_type": 1,
            "parameter_type": 2,
            "return_type": 2,
            "instantiation": 3,
            "type_hint": 4,
            "method_call": 5,
            "property_access": 5,
        }

        def sort_key(e):
            pri = uses_priority.get(e.ref_type, 10)
            return (pri, e.file or "", e.line if e.line is not None else 0)

        entries.sort(key=sort_key)
        return entries[:limit]

    def _build_extends_depth2(
        self, class_id: str, parent_id: str, depth: int, max_depth: int
    ) -> list[ContextEntry]:
        """Build depth-2 for [extends]: show override and inherited methods."""
        if depth > max_depth:
            return []

        parent_node = self.index.nodes.get(parent_id)
        if not parent_node:
            return []

        # Check if parent exists in graph (external parents have no methods)
        parent_children = self.index.get_contains_children(parent_id)
        if not parent_children:
            return []

        override_entries: list[ContextEntry] = []
        inherited_entries: list[ContextEntry] = []

        # Collect parent's methods
        parent_methods = {}
        for child_id in parent_children:
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Method" and child.name != "__construct":
                parent_methods[child.name] = (child_id, child)

        # Check which ones the class overrides
        for child_id in self.index.get_contains_children(class_id):
            child = self.index.nodes.get(child_id)
            if not child or child.kind != "Method" or child.name == "__construct":
                continue

            if child.name in parent_methods:
                # Check if it actually overrides (has overrides edge)
                override_parent = self.index.get_overrides_parent(child_id)
                if override_parent:
                    entry = ContextEntry(
                        depth=depth,
                        node_id=child_id,
                        fqn=child.fqn,
                        kind="Method",
                        file=child.file,
                        line=child.start_line,
                        signature=child.signature,
                        ref_type="override",
                        children=[],
                    )
                    if depth < max_depth:
                        entry.children = self._build_override_method_internals(
                            child_id, depth + 1, max_depth
                        )
                    override_entries.append(entry)

        # Inherited methods: parent methods not overridden by the class
        overridden_names = {self.index.nodes.get(e.node_id).name for e in override_entries if self.index.nodes.get(e.node_id)}
        for method_name, (method_id, method_node) in parent_methods.items():
            if method_name not in overridden_names:
                entry = ContextEntry(
                    depth=depth,
                    node_id=method_id,
                    fqn=method_node.fqn,
                    kind="Method",
                    file=method_node.file,
                    line=method_node.start_line,
                    signature=method_node.signature,
                    ref_type="inherited",
                    children=[],
                )
                # Expand inherited method internals at depth 3 (same as override)
                if depth < max_depth:
                    entry.children = self._build_override_method_internals(
                        method_id, depth + 1, max_depth
                    )
                inherited_entries.append(entry)

        # Overrides first, then inherited
        override_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        inherited_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return override_entries + inherited_entries

    def _build_implements_depth2(
        self, class_id: str, interface_id: str, depth: int, max_depth: int
    ) -> list[ContextEntry]:
        """Build depth-2 for [implements]: show override methods and extends subclasses.

        Uses get_overrides_parent() directly to detect overrides (ISSUE-E fix).
        Also adds [extends] entries for concrete subclasses (ISSUE-D fix).
        """
        if depth > max_depth:
            return []

        override_entries = []
        extends_entries = []

        # Find override methods in the implementing class using direct overrides edge check.
        for child_id in self.index.get_contains_children(class_id):
            child = self.index.nodes.get(child_id)
            if not child or child.kind != "Method" or child.name == "__construct":
                continue

            override_parent_id = self.index.get_overrides_parent(child_id)
            if override_parent_id:
                entry = ContextEntry(
                    depth=depth,
                    node_id=child_id,
                    fqn=child.fqn,
                    kind="Method",
                    file=child.file,
                    line=child.start_line,
                    signature=child.signature,
                    ref_type="override",
                    children=[],
                )
                # At depth 3, show what the override does internally
                if depth < max_depth:
                    entry.children = self._build_override_method_internals(
                        child_id, depth + 1, max_depth
                    )
                override_entries.append(entry)

        # ISSUE-D: Add [extends] entries for concrete subclasses
        # Recursively find all classes that extend the implementing class
        self._collect_extends_entries(class_id, depth, max_depth, extends_entries, set())

        override_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        extends_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return override_entries + extends_entries

    @staticmethod
    def _offset_entry_depths(entries: list, offset: int) -> list:
        """Recursively add an offset to depth values in context entries."""
        for entry in entries:
            entry.depth += offset
            if entry.children:
                ContextQuery._offset_entry_depths(entry.children, offset)
        return entries

    def _collect_extends_entries(
        self, class_id: str, depth: int, max_depth: int,
        result: list, visited: set[str]
    ) -> None:
        """Recursively collect [extends] entries for subclasses of a class."""
        if class_id in visited:
            return
        visited.add(class_id)

        for child_id in self.index.get_extends_children(class_id):
            child_node = self.index.nodes.get(child_id)
            if not child_node or child_id in visited:
                continue

            entry = ContextEntry(
                depth=depth,
                node_id=child_id,
                fqn=child_node.fqn,
                kind=child_node.kind,
                file=child_node.file,
                line=child_node.start_line,
                ref_type="extends",
                children=[],
            )

            # At depth 3: show USED BY of this subclass (instantiation sites, etc.)
            if depth < max_depth:
                raw_children = self._build_class_used_by(
                    child_id, max_depth - depth, limit=10, include_impl=False
                )
                # _build_class_used_by starts depth from 1 internally;
                # offset to match our actual depth context
                depth_offset = depth  # e.g., depth=2 -> children should be depth 3
                entry.children = self._offset_entry_depths(raw_children, depth_offset)

            result.append(entry)

            # Recursively find further subclasses
            self._collect_extends_entries(child_id, depth, max_depth, result, visited)

    def _build_behavioral_depth2(
        self, class_id: str, dep_class_id: str, property_name: str | None,
        depth: int, max_depth: int
    ) -> list[ContextEntry]:
        """Build behavioral depth-2 for property_type deps: method calls on the dep.

        Finds all method calls through the property that holds this dependency.
        """
        if depth > max_depth:
            return []

        entries = []
        seen_callees: set[str] = set()

        # Find the property in the class that references the dep class
        prop_id = None
        for child_id in self.index.get_contains_children(class_id):
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Property":
                # Check if this property has type_hint to dep_class_id
                for th_edge in self.index.outgoing[child_id].get("type_hint", []):
                    if th_edge.target == dep_class_id:
                        prop_id = child_id
                        break
                if prop_id:
                    break

        if not prop_id:
            return []

        prop_node = self.index.nodes.get(prop_id)
        if not prop_node:
            return []

        # Find all method calls through this property in the class
        for method_child_id in self.index.get_contains_children(class_id):
            method_node = self.index.nodes.get(method_child_id)
            if not method_node or method_node.kind != "Method":
                continue

            for call_child_id in self.index.get_contains_children(method_child_id):
                call_child = self.index.nodes.get(call_child_id)
                if not call_child or call_child.kind != "Call":
                    continue

                # Check if this call's receiver is through our property
                chain_symbol = resolve_access_chain_symbol(self.index, call_child_id)
                if chain_symbol != prop_node.fqn:
                    continue

                target_id = self.index.get_call_target(call_child_id)
                if not target_id:
                    continue
                target_node = self.index.nodes.get(target_id)
                if not target_node:
                    continue

                callee_name = target_node.name + "()" if target_node.kind == "Method" else target_node.name
                if target_node.fqn in seen_callees:
                    continue
                seen_callees.add(target_node.fqn)

                ref_type = get_reference_type_from_call(self.index, call_child_id)
                ac, acs, ok, of, ol = self._resolve_receiver_identity(call_child_id)
                arguments = self._get_argument_info(call_child_id)
                call_line = call_child.range.get("start_line") if call_child.range else None

                entry = ContextEntry(
                    depth=depth,
                    node_id=target_id,
                    fqn=target_node.fqn,
                    kind=target_node.kind,
                    file=call_child.file,
                    line=call_line,
                    ref_type="method_call",
                    callee=callee_name,
                    on=ac,
                    on_kind="property",
                    arguments=arguments,
                    children=[],
                )

                # Depth 3: expand into callee's execution flow
                if depth < max_depth and target_id:
                    target_in_graph = self.index.nodes.get(target_id)
                    if target_in_graph and target_in_graph.kind == "Method":
                        entry.children = self._build_execution_flow(
                            target_id, depth + 1, max_depth, 100, {target_id}, [0],
                        )

                entries.append(entry)

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _build_class_uses_recursive(
        self, target_id: str, depth: int, max_depth: int, limit: int, visited: set[str]
    ) -> list[ContextEntry]:
        """Recursive class-level expansion for non-property USES deps.

        For parameter_type, return_type, instantiation deps at depth 2+,
        show their own class-level dependencies.
        """
        if depth > max_depth or target_id in visited:
            return []
        visited.add(target_id)

        target_node = self.index.nodes.get(target_id)
        if not target_node or target_node.kind not in ("Class", "Interface", "Trait", "Enum"):
            return []

        entries = []
        # Collect uses edges + extends edges (get_deps only returns uses)
        edges = list(self.index.get_deps(target_id, include_members=True))
        for ext_edge in self.index.outgoing[target_id].get("extends", []):
            edges.append(ext_edge)
        local_visited: set[str] = set()

        for edge in edges:
            dep_id = edge.target
            dep_node = self.index.nodes.get(dep_id)
            if not dep_node:
                continue

            # Resolve to containing class
            resolved_id = dep_id
            resolved_node = dep_node
            if dep_node.kind in ("Method", "Property", "Argument", "Value", "Call"):
                parent_id = self.index.get_contains_parent(dep_id)
                if parent_id:
                    parent = self.index.nodes.get(parent_id)
                    if parent and parent.kind in ("Class", "Interface", "Trait", "Enum"):
                        resolved_id = parent_id
                        resolved_node = parent
                    else:
                        continue
                else:
                    continue

            if resolved_id == target_id or resolved_id in local_visited or resolved_id in visited:
                continue
            local_visited.add(resolved_id)

            ref_type = _infer_reference_type(edge, dep_node, self.index)

            # Resolve property name for property_type
            property_name = None
            if ref_type == "property_type":
                source_node = self.index.nodes.get(edge.source)
                if source_node and source_node.kind == "Property":
                    property_name = source_node.name
                    if not property_name.startswith("$"):
                        property_name = "$" + property_name

            entry = ContextEntry(
                depth=depth,
                node_id=resolved_id,
                fqn=resolved_node.fqn,
                kind=resolved_node.kind,
                file=resolved_node.file,
                line=resolved_node.start_line,
                ref_type=ref_type,
                property_name=property_name,
                children=[],
            )

            # Recursive expansion for class-level deps at depth 2+
            if depth < max_depth and resolved_node.kind in ("Class", "Interface", "Trait", "Enum"):
                entry.children = self._build_class_uses_recursive(
                    resolved_id, depth + 1, max_depth, limit, visited | local_visited
                )

            entries.append(entry)

        # Sort by priority
        def sort_key(e):
            pri = self._REF_TYPE_PRIORITY.get(e.ref_type, 10)
            return (pri, e.file or "", e.line if e.line is not None else 0)

        entries.sort(key=sort_key)
        return entries

    # =================================================================
    # ISSUE-D: Interface USED BY — implementors + injection points
    # =================================================================

    def _build_interface_used_by(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False
    ) -> list[ContextEntry]:
        """Build USED BY tree for an Interface node.

        Structure:
        - Depth 1: implementors [implements] + child interfaces [extends] +
                    injection points [property_type]
        - Depth 2: override methods under [implements], method calls under [property_type]
        - Depth 3: callers of the method call sites

        Sorting: [implements] first, then [extends], then [property_type],
        then other ref types.
        """
        start_node = self.index.nodes.get(start_id)
        if not start_node:
            return []

        implements_entries: list[ContextEntry] = []
        extends_entries: list[ContextEntry] = []
        property_type_entries: list[ContextEntry] = []
        visited_sources: set[str] = {start_id}
        seen_property_type_props: set[str] = set()

        # --- Collect all interfaces in the extends hierarchy (for transitive lookup) ---
        all_interface_ids = [start_id]
        queue = [start_id]
        while queue:
            current = queue.pop(0)
            for child_id in self.index.get_extends_children(current):
                if child_id not in all_interface_ids:
                    all_interface_ids.append(child_id)
                    queue.append(child_id)

        # --- Collect implementors (DIRECT only — ISSUE-C fix) ---
        # Only show classes that implement this interface directly,
        # not classes that implement a child interface transitively.
        direct_implementor_ids = self.index.get_implementors(start_id)
        for impl_id in direct_implementor_ids:
            impl_node = self.index.nodes.get(impl_id)
            if not impl_node or impl_id in visited_sources:
                continue
            visited_sources.add(impl_id)

            entry = ContextEntry(
                depth=1,
                node_id=impl_id,
                fqn=impl_node.fqn,
                kind=impl_node.kind,
                file=impl_node.file,
                line=impl_node.start_line,
                ref_type="implements",
                children=[],
            )

            # Depth 2: override methods + extends entries (ISSUE-D)
            if max_depth >= 2:
                entry.children = self._build_implements_depth2(
                    impl_id, start_id, 2, max_depth
                )

            implements_entries.append(entry)

        # --- Collect child interfaces (incoming extends edges — direct only) ---
        extends_child_ids = self.index.get_extends_children(start_id)
        for child_id in extends_child_ids:
            child_node = self.index.nodes.get(child_id)
            if not child_node or child_id in visited_sources:
                continue
            visited_sources.add(child_id)

            entry = ContextEntry(
                depth=1,
                node_id=child_id,
                fqn=child_node.fqn,
                kind=child_node.kind,
                file=child_node.file,
                line=child_node.start_line,
                ref_type="extends",
                children=[],
            )

            # ISSUE-I: Depth 2 — own methods + deeper extends for child interfaces
            if max_depth >= 2:
                entry.children = self._build_interface_extends_depth2(
                    child_id, 2, max_depth
                )

            extends_entries.append(entry)

        # --- Pass 1: Identify classes with property_type injection (for suppression) ---
        # Check usages of this interface AND all child interfaces
        classes_with_injection: set[str] = set()
        # Collect source groups from all interfaces in the hierarchy
        all_source_groups: list[tuple[str, dict[str, list]]] = []  # (iface_id, source_groups)
        for iface_id in all_interface_ids:
            sg = self.index.get_usages_grouped(iface_id)
            all_source_groups.append((iface_id, sg))
            for source_id, edges in sg.items():
                source_node = self.index.nodes.get(source_id)
                if not source_node:
                    continue
                for edge in edges:
                    target_node = self.index.nodes.get(edge.target)
                    if not target_node:
                        continue
                    ref_type = _infer_reference_type(edge, target_node, self.index)
                    if ref_type == "property_type":
                        cls_id = source_id
                        node = source_node
                        while node and node.kind not in ("Class", "Interface", "Trait", "Enum", "File"):
                            cls_id = self.index.get_contains_parent(cls_id)
                            node = self.index.nodes.get(cls_id) if cls_id else None
                        if node and node.kind in ("Class", "Interface", "Trait", "Enum"):
                            classes_with_injection.add(cls_id)

        # Also check usages of implementors for property_type injection (ISSUE-D)
        for impl_id in direct_implementor_ids:
            for source_id, edges in self.index.get_usages_grouped(impl_id).items():
                source_node = self.index.nodes.get(source_id)
                if not source_node:
                    continue
                for edge in edges:
                    target_node = self.index.nodes.get(edge.target)
                    if not target_node:
                        continue
                    ref_type = _infer_reference_type(edge, target_node, self.index)
                    if ref_type == "property_type":
                        cls_id = source_id
                        node = source_node
                        while node and node.kind not in ("Class", "Interface", "Trait", "Enum", "File"):
                            cls_id = self.index.get_contains_parent(cls_id)
                            node = self.index.nodes.get(cls_id) if cls_id else None
                        if node and node.kind in ("Class", "Interface", "Trait", "Enum"):
                            classes_with_injection.add(cls_id)

        # --- ISSUE-B: Collect the target interface's own contract method names ---
        # Only methods declared directly on the queried interface count as "contract"
        contract_method_names: set[str] = set()
        for child_id in self.index.get_contains_children(start_id):
            child = self.index.nodes.get(child_id)
            if child and child.kind == "Method":
                contract_method_names.add(child.name)

        # --- Pass 2: Process uses edges for injection points (from all interfaces) ---
        for iface_id, source_groups in all_source_groups:
            iface_node = self.index.nodes.get(iface_id)
            for source_id, edges in source_groups.items():
                if source_id in visited_sources:
                    continue

                source_node = self.index.nodes.get(source_id)
                if not source_node or source_node.kind == "File":
                    continue

                for edge in edges:
                    target_node = self.index.nodes.get(edge.target)
                    if not target_node:
                        continue

                    file = edge.location.get("file") if edge.location else source_node.file
                    line = edge.location.get("line") if edge.location else source_node.start_line

                    call_node_id = find_call_for_usage(self.index, source_id, edge.target, file, line)
                    if call_node_id:
                        ref_type = get_reference_type_from_call(self.index, call_node_id)
                    else:
                        ref_type = _infer_reference_type(edge, target_node, self.index)

                    if ref_type == "property_type":
                        # ISSUE-C: Skip indirect property_type entries (from child interfaces)
                        if iface_id != start_id:
                            continue

                        # Resolve property node
                        prop_fqn = None
                        prop_node = None
                        if source_node.kind == "Property":
                            prop_fqn = source_node.fqn
                            prop_node = source_node
                        elif source_node.kind in ("Method", "Function"):
                            containing_class_id = self.index.get_contains_parent(source_id)
                            if containing_class_id:
                                for child_id in self.index.get_contains_children(containing_class_id):
                                    child = self.index.nodes.get(child_id)
                                    if child and child.kind == "Property":
                                        for th_edge in self.index.outgoing[child_id].get("type_hint", []):
                                            if th_edge.target == iface_id:
                                                prop_fqn = child.fqn
                                                prop_node = child
                                                break
                                        if prop_fqn:
                                            break

                        if prop_fqn and prop_node and prop_fqn not in seen_property_type_props:
                            # ISSUE-B: Check contract relevance before including consumer.
                            # Verify that the consumer calls at least one method from the
                            # target interface's own contract through this property.
                            if contract_method_names:
                                calls_contract = self._consumer_calls_contract_methods(
                                    prop_node.id, contract_method_names
                                )
                                if not calls_contract:
                                    continue  # Skip irrelevant consumer

                            seen_property_type_props.add(prop_fqn)
                            visited_sources.add(source_id)

                            # Add via marker when the injection is through a child interface
                            via_fqn = iface_node.fqn if iface_id != start_id else None

                            entry = ContextEntry(
                                depth=1,
                                node_id=prop_node.id,
                                fqn=prop_fqn,
                                kind="Property",
                                file=prop_node.file,
                                line=prop_node.start_line,
                                ref_type="property_type",
                                via=via_fqn,
                                children=[],
                            )

                            # Depth 2: method calls through this property (with caller depth 3)
                            if max_depth >= 2:
                                entry.children = self._build_interface_injection_point_calls(
                                    prop_node.id, iface_id, 2, max_depth
                                )

                                # ISSUE-B: Filter depth-2 children to only contract methods
                                if contract_method_names:
                                    entry.children = [
                                        c for c in entry.children
                                        if self._entry_targets_contract_method(c, contract_method_names)
                                    ]

                            property_type_entries.append(entry)

                    elif ref_type == "method_call":
                        # Suppress method_call if containing class has property_type injection
                        containing_method_id = self._resolve_containing_method(source_id)
                        containing_class_id = None
                        if containing_method_id:
                            containing_class_id = self.index.get_contains_parent(containing_method_id)
                        if containing_class_id and containing_class_id in classes_with_injection:
                            continue
                        # Non-injected method calls remain suppressed for interfaces
                        # (they would be handled through injection points)
                        continue

                    elif ref_type in ("type_hint", "parameter_type", "return_type"):
                        # Skip type_hint/parameter_type/return_type — these are subsumed
                        # by property_type entries or are constructor signature refs
                        continue

        # --- Pass 3: Discover property_type consumers typed to implementing classes (ISSUE-D) ---
        # Properties typed as an abstract/concrete implementor (not the interface itself)
        # should also appear as [property_type] consumers of the interface.
        # Example: OrderService::$orderProcessor typed as AbstractOrderProcessor
        #          should appear under OrderProcessorInterface USED BY.
        for impl_id in direct_implementor_ids:
            impl_usages = self.index.get_usages_grouped(impl_id)
            for source_id, edges in impl_usages.items():
                if source_id in visited_sources:
                    continue
                source_node = self.index.nodes.get(source_id)
                if not source_node:
                    continue
                for edge in edges:
                    target_node = self.index.nodes.get(edge.target)
                    if not target_node:
                        continue
                    ref_type = _infer_reference_type(edge, target_node, self.index)
                    if ref_type != "property_type":
                        continue

                    # Resolve the actual property node
                    prop_fqn = None
                    prop_node = None
                    if source_node.kind == "Property":
                        prop_fqn = source_node.fqn
                        prop_node = source_node
                    elif source_node.kind in ("Method", "Function"):
                        # Constructor promotion: check class properties for type_hint to implementor
                        containing_class_id = self.index.get_contains_parent(source_id)
                        if containing_class_id:
                            for child_id in self.index.get_contains_children(containing_class_id):
                                child = self.index.nodes.get(child_id)
                                if child and child.kind == "Property":
                                    for th_edge in self.index.outgoing[child_id].get("type_hint", []):
                                        if th_edge.target == impl_id:
                                            prop_fqn = child.fqn
                                            prop_node = child
                                            break
                                    if prop_fqn:
                                        break

                    if not prop_fqn or not prop_node or prop_fqn in seen_property_type_props:
                        continue

                    # Ensure property is not in the implementor class itself
                    prop_class_id = self.index.get_contains_parent(prop_node.id)
                    if prop_class_id == impl_id:
                        continue

                    # ISSUE-B: Check contract relevance
                    if contract_method_names:
                        calls_contract = self._consumer_calls_contract_methods(
                            prop_node.id, contract_method_names
                        )
                        if not calls_contract:
                            continue

                    seen_property_type_props.add(prop_fqn)
                    visited_sources.add(source_id)

                    impl_node = self.index.nodes.get(impl_id)
                    via_fqn = impl_node.fqn if impl_node else None

                    entry = ContextEntry(
                        depth=1,
                        node_id=prop_node.id,
                        fqn=prop_fqn,
                        kind="Property",
                        file=prop_node.file,
                        line=prop_node.start_line,
                        ref_type="property_type",
                        via=via_fqn,
                        children=[],
                    )

                    # Depth 2: method calls through this property
                    if max_depth >= 2:
                        entry.children = self._build_interface_injection_point_calls(
                            prop_node.id, start_id, 2, max_depth
                        )

                        # ISSUE-B: Filter depth-2 children to only contract methods
                        if contract_method_names:
                            entry.children = [
                                c for c in entry.children
                                if self._entry_targets_contract_method(c, contract_method_names)
                            ]

                    property_type_entries.append(entry)

        # Sort within groups
        implements_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        extends_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        property_type_entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

        # Combine: implementors first, then extends children, then property_type
        all_entries = implements_entries + extends_entries + property_type_entries
        return all_entries[:limit]

    def _build_interface_injection_point_calls(
        self, property_id: str, interface_id: str, depth: int, max_depth: int
    ) -> list[ContextEntry]:
        """Build method call entries for an interface injection point.

        Like _build_injection_point_calls but at depth 3 shows callers of the
        containing method (who triggers the call chain) instead of the callee's
        execution flow (which is empty for interface method definitions).
        """
        if depth > max_depth:
            return []

        prop_node = self.index.nodes.get(property_id)
        if not prop_node or prop_node.kind != "Property":
            return []

        containing_class_id = self.index.get_contains_parent(property_id)
        if not containing_class_id:
            return []

        entries = []
        seen_callees: set[str] = set()

        for method_child_id in self.index.get_contains_children(containing_class_id):
            method_node = self.index.nodes.get(method_child_id)
            if not method_node or method_node.kind != "Method":
                continue

            for call_child_id in self.index.get_contains_children(method_child_id):
                call_child = self.index.nodes.get(call_child_id)
                if not call_child or call_child.kind != "Call":
                    continue

                recv_id = self.index.get_receiver(call_child_id)
                if not recv_id:
                    continue

                chain_symbol = resolve_access_chain_symbol(self.index, call_child_id)
                if chain_symbol != prop_node.fqn:
                    continue

                target_id = self.index.get_call_target(call_child_id)
                if not target_id:
                    continue
                target_node = self.index.nodes.get(target_id)
                if not target_node:
                    continue

                callee_name = target_node.name + "()" if target_node.kind == "Method" else target_node.name
                ref_type = get_reference_type_from_call(self.index, call_child_id)
                ac, acs, ok, of, ol = self._resolve_receiver_identity(call_child_id)
                arguments = self._get_argument_info(call_child_id)
                call_line = call_child.range.get("start_line") if call_child.range else None

                callee_key = target_node.fqn
                if callee_key in seen_callees:
                    for existing in entries:
                        if existing.fqn == target_node.fqn:
                            if existing.sites is None:
                                existing.sites = [{"method": method_node.name, "line": existing.line}]
                                existing.line = None
                            existing.sites.append({"method": method_node.name, "line": call_line})
                            break
                    continue
                seen_callees.add(callee_key)

                entry = ContextEntry(
                    depth=depth,
                    node_id=target_id,
                    fqn=target_node.fqn,
                    kind=target_node.kind,
                    file=call_child.file,
                    line=call_line,
                    ref_type="method_call",
                    callee=callee_name,
                    on=ac,
                    on_kind="property",
                    arguments=arguments,
                    children=[],
                )

                # Depth 3: show callers of the containing method (ISSUE-S+J fix)
                # If no callers found, show the containing method itself as terminal
                if depth < max_depth and method_child_id:
                    callers = self._build_caller_chain_for_method(
                        method_child_id, depth + 1, max_depth
                    )
                    if callers:
                        entry.children = callers
                    else:
                        # Terminal: show the containing method itself as a caller node
                        method_n = self.index.nodes.get(method_child_id)
                        if method_n:
                            display = method_n.fqn
                            if method_n.kind == "Method" and not display.endswith("()"):
                                display += "()"
                            entry.children = [ContextEntry(
                                depth=depth + 1,
                                node_id=method_child_id,
                                fqn=display,
                                kind=method_n.kind,
                                file=method_n.file,
                                line=method_n.start_line,
                                ref_type="caller",
                                children=[],
                            )]

                entries.append(entry)

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    def _consumer_calls_contract_methods(
        self, property_id: str, contract_method_names: set[str]
    ) -> bool:
        """Check if a property's containing class calls any contract method through it.

        Traverses the containing class's methods to find Call nodes whose
        receiver chain resolves to this property, and checks if the called
        method name matches any contract method name.
        """
        prop_node = self.index.nodes.get(property_id)
        if not prop_node or prop_node.kind != "Property":
            return False

        containing_class_id = self.index.get_contains_parent(property_id)
        if not containing_class_id:
            return False

        for method_child_id in self.index.get_contains_children(containing_class_id):
            method_node = self.index.nodes.get(method_child_id)
            if not method_node or method_node.kind != "Method":
                continue

            for call_child_id in self.index.get_contains_children(method_child_id):
                call_child = self.index.nodes.get(call_child_id)
                if not call_child or call_child.kind != "Call":
                    continue

                # Check if this call's receiver is our property
                chain_symbol = resolve_access_chain_symbol(self.index, call_child_id)
                if chain_symbol != prop_node.fqn:
                    continue

                # Check if the called method name matches a contract method
                target_id = self.index.get_call_target(call_child_id)
                if not target_id:
                    continue
                target_node = self.index.nodes.get(target_id)
                if target_node and target_node.name in contract_method_names:
                    return True

        return False

    @staticmethod
    def _entry_targets_contract_method(
        entry: "ContextEntry", contract_method_names: set[str]
    ) -> bool:
        """Check if a depth-2 method_call entry targets a contract method."""
        if entry.ref_type != "method_call":
            return True  # Non-method_call entries pass through
        # Extract method name from FQN
        fqn = entry.fqn
        if "::" in fqn:
            method_name = fqn.rsplit("::", 1)[-1]
        else:
            method_name = fqn
        method_name = method_name.rstrip("()")
        return method_name in contract_method_names

    def _build_interface_extends_depth2(
        self, interface_id: str, depth: int, max_depth: int
    ) -> list[ContextEntry]:
        """Build depth-2 children for an [extends] interface entry.

        Shows:
        1. Own methods declared by this interface (not inherited) as [own_method]
        2. Deeper extends relationships (interfaces extending this one)
        """
        if depth > max_depth:
            return []

        entries: list[ContextEntry] = []

        # 1. Own methods (methods declared directly on this interface)
        for child_id in self.index.get_contains_children(interface_id):
            child = self.index.nodes.get(child_id)
            if not child or child.kind != "Method":
                continue

            entry = ContextEntry(
                depth=depth,
                node_id=child_id,
                fqn=child.fqn,
                kind="Method",
                file=child.file,
                line=child.start_line,
                signature=child.signature,
                ref_type="own_method",
                children=[],
            )
            entries.append(entry)

        # 2. Deeper extends (interfaces that extend this one)
        extends_child_ids = self.index.get_extends_children(interface_id)
        for child_id in extends_child_ids:
            child_node = self.index.nodes.get(child_id)
            if not child_node:
                continue

            entry = ContextEntry(
                depth=depth,
                node_id=child_id,
                fqn=child_node.fqn,
                kind=child_node.kind,
                file=child_node.file,
                line=child_node.start_line,
                ref_type="extends",
                children=[],
            )

            # Recursive expansion for deeper chains
            if depth < max_depth:
                entry.children = self._build_interface_extends_depth2(
                    child_id, depth + 1, max_depth
                )

            entries.append(entry)

        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
        return entries

    # =================================================================
    # ISSUE-D: Interface USES — signature types + parent interface
    # =================================================================

    def _build_interface_uses(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False
    ) -> list[ContextEntry]:
        """Build USES tree for an Interface node.

        Interfaces have no method bodies, so USES only shows:
        - Parent interface (extends) at depth 1 with its type deps at depth 2
        - Non-primitive type references in method signatures (parameter_type, return_type)
        - With --impl: implementing classes with their class-level deps

        Sorting: extends first, then parameter_type/return_type, then implements (--impl).
        """
        start_node = self.index.nodes.get(start_id)
        if not start_node:
            return []

        target_info: dict[str, dict] = {}  # target_id -> {ref_type, file, line, node}

        # --- Collect extends (parent interface) ---
        extends_edges = self.index.outgoing[start_id].get("extends", [])
        for edge in extends_edges:
            target_id = edge.target
            if target_id == start_id:
                continue
            target_node = self.index.nodes.get(target_id)
            if not target_node:
                continue
            target_info[target_id] = {
                "ref_type": "extends",
                "file": target_node.file,
                "line": target_node.start_line,
                "node": target_node,
            }

        # --- Collect type references from method signatures ---
        # type_hint from Method -> return_type
        # type_hint from Argument -> parameter_type
        for child_id in self.index.get_contains_children(start_id):
            child = self.index.nodes.get(child_id)
            if not child or child.kind != "Method":
                continue

            # Return type
            for th_edge in self.index.outgoing.get(child_id, {}).get("type_hint", []):
                tid = th_edge.target
                if tid == start_id or tid in target_info:
                    continue
                t_node = self.index.nodes.get(tid)
                if not t_node:
                    continue
                target_info[tid] = {
                    "ref_type": "return_type",
                    "file": child.file,
                    "line": child.start_line,
                    "node": t_node,
                }

            # Parameter types (from Argument children)
            for sub_id in self.index.get_contains_children(child_id):
                sub = self.index.nodes.get(sub_id)
                if not sub or sub.kind != "Argument":
                    continue
                for th_edge in self.index.outgoing.get(sub_id, {}).get("type_hint", []):
                    tid = th_edge.target
                    if tid == start_id:
                        continue
                    t_node = self.index.nodes.get(tid)
                    if not t_node:
                        continue
                    # parameter_type wins over return_type (spec: "shown once" as parameter_type)
                    existing = target_info.get(tid)
                    if existing and existing["ref_type"] not in ("return_type",):
                        continue  # Don't overwrite extends
                    target_info[tid] = {
                        "ref_type": "parameter_type",
                        "file": child.file,
                        "line": child.start_line,
                        "node": t_node,
                    }

        # --- ISSUE-G: Collect inherited method signature types ---
        # Walk up the extends chain to find types in inherited method signatures
        parent_queue = list(extends_edges)
        visited_parents: set[str] = {start_id}
        for edge in parent_queue:
            parent_id = edge.target
            if parent_id in visited_parents:
                continue
            visited_parents.add(parent_id)

            for child_id in self.index.get_contains_children(parent_id):
                child = self.index.nodes.get(child_id)
                if not child or child.kind != "Method":
                    continue

                # Return type from inherited method
                for th_edge in self.index.outgoing.get(child_id, {}).get("type_hint", []):
                    tid = th_edge.target
                    if tid == start_id or tid in target_info:
                        continue
                    t_node = self.index.nodes.get(tid)
                    if not t_node:
                        continue
                    target_info[tid] = {
                        "ref_type": "return_type",
                        "file": start_node.file,
                        "line": start_node.start_line,
                        "node": t_node,
                    }

                # Parameter types from inherited method arguments
                for sub_id in self.index.get_contains_children(child_id):
                    sub = self.index.nodes.get(sub_id)
                    if not sub or sub.kind != "Argument":
                        continue
                    for th_edge in self.index.outgoing.get(sub_id, {}).get("type_hint", []):
                        tid = th_edge.target
                        if tid == start_id:
                            continue
                        t_node = self.index.nodes.get(tid)
                        if not t_node:
                            continue
                        existing = target_info.get(tid)
                        if existing and existing["ref_type"] not in ("return_type",):
                            continue
                        target_info[tid] = {
                            "ref_type": "parameter_type",
                            "file": start_node.file,
                            "line": start_node.start_line,
                            "node": t_node,
                        }

            # Continue up the chain: add grandparent extends edges
            for gp_edge in self.index.outgoing[parent_id].get("extends", []):
                if gp_edge.target not in visited_parents:
                    parent_queue.append(gp_edge)

        # --- Collect implementing classes (if --impl) ---
        if include_impl:
            implementor_ids = self.index.get_implementors(start_id)
            for impl_id in implementor_ids:
                impl_node = self.index.nodes.get(impl_id)
                if not impl_node or impl_id == start_id or impl_id in target_info:
                    continue
                target_info[impl_id] = {
                    "ref_type": "implements",
                    "file": impl_node.file,
                    "line": impl_node.start_line,
                    "node": impl_node,
                }

        # Build entries
        entries: list[ContextEntry] = []
        for target_id, info in target_info.items():
            target_node = info["node"]
            ref_type = info["ref_type"]
            file = info["file"]
            line = info["line"]

            entry = ContextEntry(
                depth=1,
                node_id=target_id,
                fqn=target_node.fqn,
                kind=target_node.kind,
                file=file,
                line=line,
                ref_type=ref_type,
                children=[],
            )

            # Depth 2 expansion
            if max_depth >= 2:
                if ref_type == "extends":
                    # Show parent interface's own type deps
                    entry.children = self._build_class_uses_recursive(
                        target_id, 2, max_depth, limit, {start_id}
                    )
                elif ref_type == "implements" and include_impl:
                    # Show implementing class's own class-level deps
                    entry.children = self._build_class_uses_recursive(
                        target_id, 2, max_depth, limit, {start_id}
                    )
                elif ref_type in ("parameter_type", "return_type"):
                    # Show the type's own class-level deps
                    entry.children = self._build_class_uses_recursive(
                        target_id, 2, max_depth, limit, {start_id}
                    )

            entries.append(entry)

        # Sort: extends first, then implements, then parameter_type/return_type
        uses_priority = {
            "extends": 0,
            "implements": 1,
            "property_type": 2,
            "parameter_type": 3,
            "return_type": 3,
            "instantiation": 4,
            "type_hint": 5,
        }

        def sort_key(e):
            pri = uses_priority.get(e.ref_type, 10)
            return (pri, e.file or "", e.line if e.line is not None else 0)

        entries.sort(key=sort_key)
        return entries[:limit]

    def _build_outgoing_tree(
        self, start_id: str, max_depth: int, limit: int, include_impl: bool = False
    ) -> list[ContextEntry]:
        """Build nested tree for outgoing dependencies (uses).

        If include_impl is True, attach implementations for interfaces/methods
        with their dependencies expanded.

        Reference types and access chains are resolved from the unified graph's
        Call/Value nodes when available.

        Uses per-parent deduplication: each parent's children are deduplicated
        independently, allowing the same target to appear under different parents
        at different depths. The start_id is always excluded to prevent infinite
        recursion (cycle prevention).
        """
        # Only the start node is globally excluded to prevent cycles.
        # Per-parent deduplication replaces the old global visited set so that
        # the same target can appear under different parents at different depths.
        cycle_guard = {start_id}
        count = [0]  # Use list to allow mutation in nested function
        # Track nodes we've already shown implementations for to prevent infinite loops
        shown_impl_for: set[str] = set()

        # Phase 3: For Method/Function nodes, use execution flow traversal
        # which iterates Call children in line-number order instead of
        # following structural `uses` edges. Also include structural type
        # references (parameter_type, return_type, property_type, type_hint)
        # since these provide important context about method signatures.
        start_node = self.index.nodes.get(start_id)
        if start_node and start_node.kind in ("Method", "Function"):
            # Get structural type references from uses edges
            type_entries = self._get_type_references(
                start_id, 1, cycle_guard, count, limit
            )
            # Get execution flow from Call children
            call_entries = self._build_execution_flow(
                start_id, 1, max_depth, limit, cycle_guard, count,
                include_impl=include_impl, shown_impl_for=shown_impl_for,
            )
            # Combine: type references first, then call entries in execution order
            return type_entries + call_entries

        # For Value nodes, use source chain traversal
        if start_node and start_node.kind == "Value":
            return self._build_value_source_chain(start_id, 1, max_depth, limit, visited=set())

        # ISSUE-F: Property nodes — trace who sets this property (assigned_from -> parameter -> callers)
        if start_node and start_node.kind == "Property":
            return self._build_property_uses(start_id, 1, max_depth, limit)

        # ISSUE-C: Class nodes — grouped, deduped USES with behavioral depth 2
        if start_node and start_node.kind == "Class":
            return self._build_class_uses(start_id, max_depth, limit, include_impl)

        # ISSUE-D: Interface nodes — signature types + extends USES
        if start_node and start_node.kind == "Interface":
            return self._build_interface_uses(start_id, max_depth, limit, include_impl)

        def build_tree(current_id: str, current_depth: int) -> list[ContextEntry]:
            if current_depth > max_depth or count[0] >= limit:
                return []

            entries = []
            edges = self.index.get_deps(current_id)

            # Per-parent visited set: prevents duplicate targets within
            # this parent's children, but allows the same target to appear
            # under a different parent at a different depth.
            local_visited: set[str] = set()

            for edge in edges:
                target_id = edge.target
                if target_id in cycle_guard or target_id in local_visited:
                    continue
                local_visited.add(target_id)

                if count[0] >= limit:
                    break
                count[0] += 1

                target_node = self.index.nodes.get(target_id)

                if edge.location:
                    file = edge.location.get("file")
                    line = edge.location.get("line")
                elif target_node:
                    file = target_node.file
                    line = target_node.start_line
                else:
                    file = None
                    line = None

                # Try to find a Call node for reference type and access chain
                member_ref = None
                arguments = []
                result_var = None
                if target_node:
                    call_node_id = find_call_for_usage(
                        self.index, current_id, target_id, file, line
                    )

                    reference_type = None
                    access_chain = None
                    access_chain_symbol = None
                    arguments = []
                    result_var = None

                    if call_node_id:
                        reference_type = get_reference_type_from_call(self.index, call_node_id)
                        access_chain = build_access_chain(self.index, call_node_id)
                        # R4: Resolve access chain property FQN
                        access_chain_symbol = resolve_access_chain_symbol(self.index, call_node_id)
                        # Phase 2: Argument tracking
                        arguments = self._get_argument_info(call_node_id)
                        result_var = self._find_result_var(call_node_id)
                    else:
                        # Fall back to inference from edge/node types
                        reference_type = _infer_reference_type(edge, target_node, self.index)

                    # For USES, target_name is empty since fqn already shows the target
                    # We only need reference_type and access_chain
                    member_ref = MemberRef(
                        target_name="",  # Empty - fqn already shows the target
                        target_fqn=target_node.fqn,
                        target_kind=target_node.kind,
                        file=file,
                        line=line,
                        reference_type=reference_type,
                        access_chain=access_chain,
                        access_chain_symbol=access_chain_symbol,
                    )

                entry_kwargs = dict(
                    depth=current_depth,
                    node_id=target_id,
                    fqn=target_node.fqn if target_node else target_id,
                    kind=target_node.kind if target_node else None,
                    file=file,
                    line=line,
                    signature=target_node.signature if target_node else None,
                    children=[],
                    implementations=[],
                    member_ref=member_ref,
                    arguments=arguments,
                    result_var=result_var,
                )
                entry = ContextEntry(**entry_kwargs)

                # Attach implementations for interfaces/methods with their deps expanded
                # Skip if we've already shown implementations for this node
                if include_impl and target_node and target_id not in shown_impl_for:
                    shown_impl_for.add(target_id)
                    entry.implementations = self._get_implementations_for_node(
                        target_node, current_depth, max_depth, limit, cycle_guard, count, shown_impl_for
                    )

                # Recurse for children
                if current_depth < max_depth:
                    entry.children = build_tree(target_id, current_depth + 1)

                entries.append(entry)

            # R2: Sort entries by (file path, line number) for consistent ordering
            entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

            return entries

        return build_tree(start_id, 1)

    def _get_implementations_for_node(
        self, node: NodeData, depth: int, max_depth: int, limit: int,
        visited: set, count: list, shown_impl_for: set
    ) -> list[ContextEntry]:
        """Get implementations for an interface/class or implementing methods for interface methods.

        For interfaces/classes: returns implementing classes with their dependencies expanded.
        For methods: returns implementing methods (via overrides or interface implementation).

        Note: Implementation subtrees use their own visited set to show full dependency tree
        even if some nodes were already shown in the main tree. The shown_impl_for set prevents
        infinite loops by tracking which nodes we've already shown implementations for.
        """
        implementations = []

        if node.kind in self.INHERITABLE_KINDS:
            # Get all classes that implement/extend this
            impl_ids = self._get_all_children(node.id)
            for impl_id in impl_ids:
                impl_node = self.index.nodes.get(impl_id)
                if impl_node:
                    entry = ContextEntry(
                        depth=depth,
                        node_id=impl_id,
                        fqn=impl_node.fqn,
                        kind=impl_node.kind,
                        file=impl_node.file,
                        line=impl_node.start_line,
                        signature=impl_node.signature,
                        children=[],
                        implementations=[],
                    )

                    # Recurse into implementation's execution flow with FRESH cycle guard
                    # so we show the full tree even if nodes appeared elsewhere
                    if depth < max_depth:
                        impl_cycle_guard = {impl_id}
                        impl_count = [0]
                        entry.children = self._build_execution_flow(
                            impl_id, depth + 1, max_depth, limit,
                            impl_cycle_guard, impl_count,
                            include_impl=True, shown_impl_for=shown_impl_for,
                        )

                    implementations.append(entry)

        elif node.kind == "Method":
            # First try direct overrides
            override_ids = list(self.index.get_overridden_by(node.id))

            # Also find interface method implementations
            # Get the containing class/interface of this method
            containing_id = self.index.get_contains_parent(node.id)
            if containing_id:
                containing_node = self.index.nodes.get(containing_id)
                if containing_node and containing_node.kind in ("Interface", "Trait"):
                    # Find implementing classes
                    impl_class_ids = self._get_all_children(containing_id)
                    method_name = node.name

                    for impl_class_id in impl_class_ids:
                        # Find method with same name in implementing class
                        for child_id in self.index.get_contains_children(impl_class_id):
                            child_node = self.index.nodes.get(child_id)
                            if child_node and child_node.kind == "Method" and child_node.name == method_name:
                                if child_id not in override_ids:
                                    override_ids.append(child_id)

            for override_id in override_ids:
                override_node = self.index.nodes.get(override_id)
                if override_node:
                    entry = ContextEntry(
                        depth=depth,
                        node_id=override_id,
                        fqn=override_node.fqn,
                        kind=override_node.kind,
                        file=override_node.file,
                        line=override_node.start_line,
                        signature=override_node.signature,
                        children=[],
                        implementations=[],
                    )

                    # Recurse into method's execution flow with FRESH cycle guard
                    if depth < max_depth:
                        impl_cycle_guard = {override_id}
                        impl_count = [0]
                        entry.children = self._build_execution_flow(
                            override_id, depth + 1, max_depth, limit,
                            impl_cycle_guard, impl_count,
                            include_impl=True, shown_impl_for=shown_impl_for,
                        )

                    implementations.append(entry)

        return implementations

    def _build_deps_subtree(
        self, start_id: str, depth: int, max_depth: int, limit: int,
        visited: set, count: list, include_impl: bool, shown_impl_for: set
    ) -> list[ContextEntry]:
        """Build dependency subtree for a node.

        Args:
            shown_impl_for: Set of node IDs we've already shown implementations for,
                           to prevent infinite loops when implementations depend on their interfaces.
        """
        if depth > max_depth or count[0] >= limit:
            return []

        entries = []
        edges = self.index.get_deps(start_id)

        for edge in edges:
            target_id = edge.target
            if target_id in visited:
                continue
            visited.add(target_id)

            if count[0] >= limit:
                break
            count[0] += 1

            target_node = self.index.nodes.get(target_id)

            if edge.location:
                file = edge.location.get("file")
                line = edge.location.get("line")
            elif target_node:
                file = target_node.file
                line = target_node.start_line
            else:
                file = None
                line = None

            # Try to find a Call node for reference type and access chain
            member_ref = None
            arguments = []
            result_var = None
            if target_node:
                call_node_id = find_call_for_usage(
                    self.index, start_id, target_id, file, line
                )

                reference_type = None
                access_chain = None
                access_chain_symbol = None

                if call_node_id:
                    reference_type = get_reference_type_from_call(self.index, call_node_id)
                    access_chain = build_access_chain(self.index, call_node_id)
                    # R4: Resolve access chain property FQN
                    access_chain_symbol = resolve_access_chain_symbol(self.index, call_node_id)
                    # Phase 2: Argument tracking
                    arguments = self._get_argument_info(call_node_id)
                    result_var = self._find_result_var(call_node_id)
                else:
                    # Fall back to inference from edge/node types
                    reference_type = _infer_reference_type(edge, target_node, self.index)

                # For USES, target_name is empty since fqn already shows the target
                member_ref = MemberRef(
                    target_name="",  # Empty - fqn already shows the target
                    target_fqn=target_node.fqn,
                    target_kind=target_node.kind,
                    file=file,
                    line=line,
                    reference_type=reference_type,
                    access_chain=access_chain,
                    access_chain_symbol=access_chain_symbol,
                )

            entry_kwargs = dict(
                depth=depth,
                node_id=target_id,
                fqn=target_node.fqn if target_node else target_id,
                kind=target_node.kind if target_node else None,
                file=file,
                line=line,
                signature=target_node.signature if target_node else None,
                children=[],
                implementations=[],
                member_ref=member_ref,
                arguments=arguments,
                result_var=result_var,
            )
            entry = ContextEntry(**entry_kwargs)

            # Attach implementations for interfaces/methods
            # Skip if we've already shown implementations for this node
            if include_impl and target_node and target_id not in shown_impl_for:
                shown_impl_for.add(target_id)
                entry.implementations = self._get_implementations_for_node(
                    target_node, depth, max_depth, limit, visited, count, shown_impl_for
                )

            # Recurse for children
            if depth < max_depth:
                entry.children = self._build_deps_subtree(
                    target_id, depth + 1, max_depth, limit, visited, count, include_impl, shown_impl_for
                )

            entries.append(entry)

        # R2: Sort entries by (file path, line number) for consistent ordering
        entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

        return entries

    def _get_all_children(self, node_id: str) -> list[str]:
        """Get all classes that extend or implement this class/interface."""
        children = []
        # Classes that extend this
        children.extend(self.index.get_extends_children(node_id))
        # Classes that implement this (for interfaces)
        children.extend(self.index.get_implementors(node_id))
        return children

    def _get_interface_method_ids(self, method_id: str) -> list[str]:
        """Get IDs of interface methods that this method implements.

        For a concrete method like SynxisConfigurationService::getHotelIdForItem(),
        returns the interface method SynxisConfigurationServiceInterface::getHotelIdForItem()
        if the class implements that interface.
        """
        method_node = self.index.nodes.get(method_id)
        if not method_node or method_node.kind != "Method":
            return []

        interface_methods = []

        # Check direct override relationship first
        override_parent = self.index.get_overrides_parent(method_id)
        if override_parent:
            parent_node = self.index.nodes.get(override_parent)
            if parent_node:
                # Add the parent, and recursively check its parents
                interface_methods.append(override_parent)
                interface_methods.extend(self._get_interface_method_ids(override_parent))

        # Find via class->interface->method relationship
        containing_id = self.index.get_contains_parent(method_id)
        if containing_id:
            containing_node = self.index.nodes.get(containing_id)
            if containing_node and containing_node.kind in ("Class", "Enum"):
                # Get all interfaces this class implements (including inherited)
                interface_ids = self.index.get_all_interfaces(containing_id)
                method_name = method_node.name

                for iface_id in interface_ids:
                    # Find method with same name in interface
                    for child_id in self.index.get_contains_children(iface_id):
                        child_node = self.index.nodes.get(child_id)
                        if (child_node and child_node.kind == "Method"
                                and child_node.name == method_name
                                and child_id not in interface_methods):
                            interface_methods.append(child_id)

        return interface_methods

    def _build_implementations_tree(
        self, start_id: str, max_depth: int, limit: int
    ) -> list[InheritEntry]:
        """Build tree of classes that extend/implement this class/interface."""
        from collections import deque

        tree: list[InheritEntry] = []
        visited = {start_id}
        count = 0

        # Queue: (node_id, current_depth, parent_entry or None)
        queue: deque[tuple[str, int, InheritEntry | None]] = deque()

        # Get direct children (classes that extend/implement this)
        child_ids = self._get_all_children(start_id)
        for cid in child_ids:
            if cid not in visited:
                queue.append((cid, 1, None))

        while queue and count < limit:
            current_id, current_depth, parent_entry = queue.popleft()

            if current_id in visited:
                continue
            visited.add(current_id)

            node = self.index.nodes.get(current_id)
            if not node:
                continue

            count += 1

            entry = InheritEntry(
                depth=current_depth,
                node_id=node.id,
                fqn=node.fqn,
                kind=node.kind,
                file=node.file,
                line=node.start_line,
                children=[],
            )

            if parent_entry is None:
                tree.append(entry)
            else:
                parent_entry.children.append(entry)

            # Continue BFS if within depth limit
            if current_depth < max_depth:
                grandchild_ids = self._get_all_children(current_id)
                for gc_id in grandchild_ids:
                    if gc_id not in visited:
                        queue.append((gc_id, current_depth + 1, entry))

        return tree

    def _build_overrides_tree(
        self, start_id: str, max_depth: int, limit: int
    ) -> list[OverrideEntry]:
        """Build tree of methods that override this method."""
        from collections import deque

        tree: list[OverrideEntry] = []
        visited = {start_id}
        count = 0

        # Queue: (node_id, current_depth, parent_entry or None)
        queue: deque[tuple[str, int, OverrideEntry | None]] = deque()

        # Get direct children (methods that override this)
        child_ids = self.index.get_overridden_by(start_id)
        for cid in child_ids:
            if cid not in visited:
                queue.append((cid, 1, None))

        while queue and count < limit:
            current_id, current_depth, parent_entry = queue.popleft()

            if current_id in visited:
                continue
            visited.add(current_id)

            node = self.index.nodes.get(current_id)
            if not node:
                continue

            count += 1

            entry = OverrideEntry(
                depth=current_depth,
                node_id=node.id,
                fqn=node.fqn,
                file=node.file,
                line=node.start_line,
                children=[],
            )

            if parent_entry is None:
                tree.append(entry)
            else:
                parent_entry.children.append(entry)

            # Continue BFS if within depth limit
            if current_depth < max_depth:
                grandchild_ids = self.index.get_overridden_by(current_id)
                for gc_id in grandchild_ids:
                    if gc_id not in visited:
                        queue.append((gc_id, current_depth + 1, entry))

        return tree
