"""Strategy pattern handlers for class USED BY edge classification.

Each handler processes one reference type (or group of related types) and
appends ContextEntry objects into the appropriate bucket in EntryBucket.

EdgeContext carries all pre-resolved information about a single edge so
handlers don't need to re-resolve nodes or reference types.
"""

from dataclasses import dataclass, field
from typing import Protocol, TYPE_CHECKING

from ..models import ContextEntry, NodeData
from ..models.edge import EdgeData
from .graph_utils import (
    resolve_receiver_identity,
    resolve_containing_method,
    get_argument_info,
)
from .reference_types import _infer_reference_type

if TYPE_CHECKING:
    from ..graph import SoTIndex


@dataclass(frozen=True)
class EdgeContext:
    """Immutable context for a single edge being classified."""

    index: "SoTIndex"
    start_id: str
    source_id: str
    source_node: NodeData
    edge: EdgeData
    target_node: NodeData
    ref_type: str
    file: str | None
    line: int | None
    call_node_id: str | None
    classes_with_injection: set[str]


@dataclass
class EntryBucket:
    """Mutable collector for classified USED BY entries with dedup tracking."""

    instantiation: list[ContextEntry] = field(default_factory=list)
    extends: list[ContextEntry] = field(default_factory=list)
    property_type: list[ContextEntry] = field(default_factory=list)
    method_call: list[ContextEntry] = field(default_factory=list)
    property_access_groups: dict[str, list[dict]] = field(default_factory=dict)
    param_return: list[ContextEntry] = field(default_factory=list)

    # Dedup tracking
    seen_instantiation_methods: set[str] = field(default_factory=set)
    seen_property_type_props: set[str] = field(default_factory=set)


class UsedByHandler(Protocol):
    """Protocol for USED BY edge handlers."""

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        """Process an edge and append entries to the appropriate bucket."""
        ...


class InstantiationHandler:
    """Handle [instantiation] edges — new ClassName() calls."""

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        containing_method_id = resolve_containing_method(ctx.index, ctx.source_id)
        containing_method = ctx.index.nodes.get(containing_method_id) if containing_method_id else None
        method_key = containing_method_id or ctx.source_id
        if method_key in bucket.seen_instantiation_methods:
            return
        bucket.seen_instantiation_methods.add(method_key)

        entry_fqn = containing_method.fqn if containing_method else ctx.source_node.fqn
        if containing_method and containing_method.kind == "Method" and not entry_fqn.endswith("()"):
            entry_fqn += "()"

        arguments = []
        if ctx.call_node_id:
            arguments = get_argument_info(ctx.index, ctx.call_node_id)

        entry = ContextEntry(
            depth=1,
            node_id=method_key,
            fqn=entry_fqn,
            kind=containing_method.kind if containing_method else ctx.source_node.kind,
            file=ctx.file,
            line=ctx.line,
            ref_type="instantiation",
            children=[],
            arguments=arguments,
        )
        bucket.instantiation.append(entry)


class ExtendsHandler:
    """Handle [extends] edges — class inheritance."""

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        entry = ContextEntry(
            depth=1,
            node_id=ctx.source_id,
            fqn=ctx.source_node.fqn,
            kind=ctx.source_node.kind,
            file=ctx.source_node.file,
            line=ctx.source_node.start_line,
            ref_type="extends",
            children=[],
        )
        bucket.extends.append(entry)


class ImplementsHandler:
    """Handle [implements] edges — interface implementation."""

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        entry = ContextEntry(
            depth=1,
            node_id=ctx.source_id,
            fqn=ctx.source_node.fqn,
            kind=ctx.source_node.kind,
            file=ctx.source_node.file,
            line=ctx.source_node.start_line,
            ref_type="implements",
            children=[],
        )
        bucket.extends.append(entry)


class PropertyTypeHandler:
    """Handle [property_type] edges — typed property declarations."""

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        prop_fqn = None
        prop_node = None
        if ctx.source_node.kind == "Property":
            prop_fqn = ctx.source_node.fqn
            prop_node = ctx.source_node
        elif ctx.source_node.kind in ("Method", "Function"):
            containing_class_id = ctx.index.get_contains_parent(ctx.source_id)
            if containing_class_id:
                for child_id in ctx.index.get_contains_children(containing_class_id):
                    child = ctx.index.nodes.get(child_id)
                    if child and child.kind == "Property":
                        for th_edge in ctx.index.outgoing[child_id].get("type_hint", []):
                            if th_edge.target == ctx.start_id:
                                prop_fqn = child.fqn
                                prop_node = child
                                break
                        if prop_fqn:
                            break

        if prop_fqn and prop_node and prop_fqn not in bucket.seen_property_type_props:
            bucket.seen_property_type_props.add(prop_fqn)
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
            bucket.property_type.append(entry)


class MethodCallHandler:
    """Handle [method_call] edges — method invocations on injected properties."""

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        # Suppress method_call if the containing class has a property_type
        # injection for this target class (those calls show at depth 2)
        containing_method_id = resolve_containing_method(ctx.index, ctx.source_id)
        containing_class_id = None
        if containing_method_id:
            containing_class_id = ctx.index.get_contains_parent(containing_method_id)
        if containing_class_id and containing_class_id in ctx.classes_with_injection:
            return

        containing_method = ctx.index.nodes.get(containing_method_id) if containing_method_id else None

        callee_name = ctx.target_node.name + "()" if ctx.target_node.kind == "Method" else None
        on_expr = None
        on_kind = None
        if ctx.call_node_id:
            ac, acs, ok, of, ol = resolve_receiver_identity(ctx.index, ctx.call_node_id)
            on_expr = ac
            on_kind = ok

        method_fqn = containing_method.fqn if containing_method else ctx.source_node.fqn
        if containing_method and containing_method.kind == "Method" and not method_fqn.endswith("()"):
            method_fqn += "()"

        arguments = []
        if ctx.call_node_id:
            arguments = get_argument_info(ctx.index, ctx.call_node_id)

        entry = ContextEntry(
            depth=1,
            node_id=containing_method_id or ctx.source_id,
            fqn=method_fqn,
            kind=containing_method.kind if containing_method else ctx.source_node.kind,
            file=ctx.file,
            line=ctx.line,
            ref_type="method_call",
            callee=callee_name,
            on=on_expr,
            on_kind=on_kind,
            children=[],
            arguments=arguments,
        )
        bucket.method_call.append(entry)


class PropertyAccessHandler:
    """Handle [property_access] edges — grouped by property FQN and method."""

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        prop_fqn = ctx.target_node.fqn
        containing_method_id = resolve_containing_method(ctx.index, ctx.source_id)
        containing_method = ctx.index.nodes.get(containing_method_id) if containing_method_id else None
        method_fqn = containing_method.fqn if containing_method else ctx.source_node.fqn

        on_expr = None
        on_kind = None
        if ctx.call_node_id:
            ac, acs, ok, of, ol = resolve_receiver_identity(ctx.index, ctx.call_node_id)
            on_expr = ac
            on_kind = ok

        if prop_fqn not in bucket.property_access_groups:
            bucket.property_access_groups[prop_fqn] = []

        found = False
        for group_entry in bucket.property_access_groups[prop_fqn]:
            if (group_entry["method_fqn"] == method_fqn
                    and group_entry["on_expr"] == on_expr
                    and group_entry["on_kind"] == on_kind):
                group_entry["lines"].append(ctx.line)
                found = True
                break
        if not found:
            bucket.property_access_groups[prop_fqn].append({
                "method_fqn": method_fqn,
                "method_id": containing_method_id or ctx.source_id,
                "method_kind": containing_method.kind if containing_method else ctx.source_node.kind,
                "lines": [ctx.line],
                "on_expr": on_expr,
                "on_kind": on_kind,
                "file": ctx.file,
            })


class ParamReturnHandler:
    """Handle [parameter_type], [return_type], [type_hint] edges."""

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        # For return_type, show method-level FQN instead of class-level
        if ctx.ref_type == "return_type" and ctx.source_node.kind in ("Method", "Function"):
            method_fqn = ctx.source_node.fqn
            if ctx.source_node.kind == "Method" and not method_fqn.endswith("()"):
                method_fqn += "()"
            already_exists = any(e.fqn == method_fqn for e in bucket.param_return)
            if not already_exists:
                entry = ContextEntry(
                    depth=1,
                    node_id=ctx.source_id,
                    fqn=method_fqn,
                    kind=ctx.source_node.kind,
                    file=ctx.source_node.file,
                    line=ctx.source_node.start_line,
                    signature=ctx.source_node.signature,
                    ref_type=ctx.ref_type,
                    children=[],
                )
                bucket.param_return.append(entry)
            return

        # Group by containing class
        cls_id = ctx.source_id
        node = ctx.source_node
        while node and node.kind not in ("Class", "Interface", "Trait", "Enum", "File"):
            cls_id = ctx.index.get_contains_parent(cls_id)
            node = ctx.index.nodes.get(cls_id) if cls_id else None
        if not node or node.kind not in ("Class", "Interface", "Trait", "Enum"):
            return

        already_exists = any(e.fqn == node.fqn for e in bucket.param_return)
        if not already_exists:
            entry = ContextEntry(
                depth=1,
                node_id=cls_id,
                fqn=node.fqn,
                kind=node.kind,
                file=node.file,
                line=node.start_line,
                ref_type=ctx.ref_type,
                children=[],
            )
            bucket.param_return.append(entry)


# Handler registry: maps ref_type to handler instance
USED_BY_HANDLERS: dict[str, UsedByHandler] = {
    "instantiation": InstantiationHandler(),
    "extends": ExtendsHandler(),
    "implements": ImplementsHandler(),
    "property_type": PropertyTypeHandler(),
    "method_call": MethodCallHandler(),
    "property_access": PropertyAccessHandler(),
    "parameter_type": ParamReturnHandler(),
    "return_type": ParamReturnHandler(),
    "type_hint": ParamReturnHandler(),
}
