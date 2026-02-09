"""Tests for reference type inference."""

import pytest
from src.models.edge import EdgeData
from src.models.node import NodeData
from src.queries.context import _infer_reference_type


def make_edge(edge_type: str, source: str = "source", target: str = "target") -> EdgeData:
    """Helper to create an edge with specified type."""
    return EdgeData(type=edge_type, source=source, target=target)


def make_node(kind: str, name: str = "test", fqn: str = "Test\\test") -> NodeData:
    """Helper to create a node with specified kind."""
    return NodeData(
        id=f"node:{name}",
        kind=kind,
        name=name,
        fqn=fqn,
        symbol=f"scip-php ... {fqn}#",
        file="test.php",
        range={"start_line": 10, "start_col": 0, "end_line": 20, "end_col": 0},
    )


class TestInferReferenceType:
    """Tests for _infer_reference_type() function."""

    def test_extends_edge_returns_extends(self):
        """extends edge type maps directly to extends reference type."""
        edge = make_edge("extends")
        node = make_node("Class", "ParentClass")
        assert _infer_reference_type(edge, node) == "extends"

    def test_implements_edge_returns_implements(self):
        """implements edge type maps directly to implements reference type."""
        edge = make_edge("implements")
        node = make_node("Interface", "SomeInterface")
        assert _infer_reference_type(edge, node) == "implements"

    def test_uses_trait_edge_returns_uses_trait(self):
        """uses_trait edge type maps directly to uses_trait reference type."""
        edge = make_edge("uses_trait")
        node = make_node("Trait", "SomeTrait")
        assert _infer_reference_type(edge, node) == "uses_trait"

    def test_uses_method_returns_method_call(self):
        """uses edge targeting a Method returns method_call."""
        edge = make_edge("uses")
        node = make_node("Method", "doSomething")
        assert _infer_reference_type(edge, node) == "method_call"

    def test_uses_property_returns_property_access(self):
        """uses edge targeting a Property returns property_access."""
        edge = make_edge("uses")
        node = make_node("Property", "$name")
        assert _infer_reference_type(edge, node) == "property_access"

    def test_uses_class_returns_type_hint(self):
        """uses edge targeting a Class returns type_hint."""
        edge = make_edge("uses")
        node = make_node("Class", "MyClass")
        assert _infer_reference_type(edge, node) == "type_hint"

    def test_uses_interface_returns_type_hint(self):
        """uses edge targeting an Interface returns type_hint."""
        edge = make_edge("uses")
        node = make_node("Interface", "MyInterface")
        assert _infer_reference_type(edge, node) == "type_hint"

    def test_uses_enum_returns_type_hint(self):
        """uses edge targeting an Enum returns type_hint."""
        edge = make_edge("uses")
        node = make_node("Enum", "Status")
        assert _infer_reference_type(edge, node) == "type_hint"

    def test_uses_constant_returns_constant_access(self):
        """uses edge targeting a Constant returns constant_access."""
        edge = make_edge("uses")
        node = make_node("Constant", "MAX_SIZE")
        assert _infer_reference_type(edge, node) == "constant_access"

    def test_uses_function_returns_function_call(self):
        """uses edge targeting a Function returns function_call."""
        edge = make_edge("uses")
        node = make_node("Function", "myFunction")
        assert _infer_reference_type(edge, node) == "function_call"

    def test_uses_argument_returns_argument_ref(self):
        """uses edge targeting an Argument returns argument_ref."""
        edge = make_edge("uses")
        node = make_node("Argument", "$param")
        assert _infer_reference_type(edge, node) == "argument_ref"

    def test_uses_variable_returns_variable_ref(self):
        """uses edge targeting a Variable returns variable_ref."""
        edge = make_edge("uses")
        node = make_node("Variable", "$localVar")
        assert _infer_reference_type(edge, node) == "variable_ref"

    def test_uses_without_target_node_returns_uses(self):
        """uses edge without target node returns generic uses type."""
        edge = make_edge("uses")
        assert _infer_reference_type(edge, None) == "uses"

    def test_unknown_edge_type_returns_uses(self):
        """Unknown edge type returns generic uses type."""
        edge = make_edge("some_unknown_type")
        node = make_node("Class")
        assert _infer_reference_type(edge, node) == "uses"

    def test_extends_edge_ignores_target_kind(self):
        """extends edge type is used regardless of target node kind."""
        edge = make_edge("extends")
        # Even if target is wrong kind, extends edge means extends
        node = make_node("Method")  # Unusual but possible with malformed data
        assert _infer_reference_type(edge, node) == "extends"


class _MockIndex:
    """Minimal mock index for _infer_reference_type tests that need source node lookup.

    Supports the cross-referencing logic in _infer_reference_type that checks
    type_hint edges from Argument children to distinguish parameter_type vs return_type.
    """

    def __init__(self, nodes: dict[str, NodeData], contains: dict[str, list[str]] = None,
                 type_hints: dict[str, list] = None):
        from collections import defaultdict
        self.nodes = nodes
        self._contains = contains or {}
        # outgoing[node_id]["type_hint"] -> list of EdgeData-like objects
        self.outgoing = defaultdict(lambda: defaultdict(list))
        if type_hints:
            for source_id, edges in type_hints.items():
                self.outgoing[source_id]["type_hint"] = edges

    def get_contains_children(self, node_id: str) -> list[str]:
        return self._contains.get(node_id, [])


class TestInferReferenceTypeWithIndex:
    """Tests for _infer_reference_type() with index parameter (Phase 1, Issue 3).

    When the index is provided, _infer_reference_type can look up the source node
    of a type_hint edge to distinguish parameter_type, return_type, and property_type.
    """

    def test_t1_6_argument_source_returns_parameter_type(self):
        """T1.6: uses edge to Class with Argument source returns parameter_type."""
        edge = make_edge("uses", source="arg:$input", target="class:CreateOrderInput")
        target = make_node("Class", "CreateOrderInput", "App\\Dto\\CreateOrderInput")
        source = make_node("Argument", "$input", "App\\Service\\OrderService::createOrder.$input")
        source.id = "arg:$input"

        index = _MockIndex({"arg:$input": source})
        assert _infer_reference_type(edge, target, index) == "parameter_type"

    def test_t1_7_method_source_returns_return_type(self):
        """T1.7: uses edge to Class with Method source returns return_type.

        The method has a direct type_hint edge to the target (return type),
        and no Argument child has a type_hint to the target.
        """
        edge = make_edge("uses", source="method:createOrder", target="class:OrderOutput")
        target = make_node("Class", "OrderOutput", "App\\Dto\\OrderOutput")
        source = make_node("Method", "createOrder", "App\\Service\\OrderService::createOrder")
        source.id = "method:createOrder"

        # Method has a type_hint edge to OrderOutput (return type)
        return_type_hint = EdgeData(type="type_hint", source="method:createOrder", target="class:OrderOutput")
        index = _MockIndex(
            nodes={"method:createOrder": source},
            contains={"method:createOrder": []},  # no argument children
            type_hints={"method:createOrder": [return_type_hint]},
        )
        assert _infer_reference_type(edge, target, index) == "return_type"

    def test_property_source_returns_property_type(self):
        """uses edge to Class with Property source returns property_type."""
        edge = make_edge("uses", source="prop:$logger", target="class:LoggerInterface")
        target = make_node("Interface", "LoggerInterface", "Psr\\Log\\LoggerInterface")
        source = make_node("Property", "$logger", "App\\Service\\OrderService::$logger")
        source.id = "prop:$logger"

        index = _MockIndex({"prop:$logger": source})
        assert _infer_reference_type(edge, target, index) == "property_type"

    def test_function_source_returns_return_type(self):
        """uses edge to Class with Function source returns return_type."""
        edge = make_edge("uses", source="func:getUser", target="class:User")
        target = make_node("Class", "User", "App\\Entity\\User")
        source = make_node("Function", "getUser", "getUser")
        source.id = "func:getUser"

        # Function has a type_hint edge to User (return type)
        return_type_hint = EdgeData(type="type_hint", source="func:getUser", target="class:User")
        index = _MockIndex(
            nodes={"func:getUser": source},
            contains={"func:getUser": []},
            type_hints={"func:getUser": [return_type_hint]},
        )
        assert _infer_reference_type(edge, target, index) == "return_type"

    def test_method_source_with_param_type_hint_returns_parameter_type(self):
        """Method source with Argument child having type_hint to target returns parameter_type.

        This tests the cross-referencing logic: the uses edge source is the Method,
        but the method has an Argument child with a type_hint to the target class,
        indicating this is a parameter type, not a return type.
        """
        edge = make_edge("uses", source="method:createOrder", target="class:CreateOrderInput")
        target = make_node("Class", "CreateOrderInput", "App\\Dto\\CreateOrderInput")
        source = make_node("Method", "createOrder", "App\\Service\\OrderService::createOrder")
        source.id = "method:createOrder"
        arg = make_node("Argument", "$input", "App\\Service\\OrderService::createOrder.$input")
        arg.id = "arg:$input"

        # Argument has a type_hint edge to CreateOrderInput (parameter type)
        param_type_hint = EdgeData(type="type_hint", source="arg:$input", target="class:CreateOrderInput")
        index = _MockIndex(
            nodes={"method:createOrder": source, "arg:$input": arg},
            contains={"method:createOrder": ["arg:$input"]},
            type_hints={"arg:$input": [param_type_hint]},
        )
        assert _infer_reference_type(edge, target, index) == "parameter_type"

    def test_method_source_no_type_hints_returns_type_hint(self):
        """Method source with no type_hint edges falls back to type_hint."""
        edge = make_edge("uses", source="method:foo", target="class:Bar")
        target = make_node("Class", "Bar", "App\\Bar")
        source = make_node("Method", "foo", "App\\Foo::foo")
        source.id = "method:foo"

        # No type_hint edges at all
        index = _MockIndex(
            nodes={"method:foo": source},
            contains={"method:foo": []},
            type_hints={},
        )
        assert _infer_reference_type(edge, target, index) == "type_hint"

    def test_t1_8_no_index_backward_compat(self):
        """T1.8: uses edge to Class without index returns type_hint (backward compat)."""
        edge = make_edge("uses")
        target = make_node("Class", "Order", "App\\Entity\\Order")
        # No index passed -- should fall back to "type_hint"
        assert _infer_reference_type(edge, target) == "type_hint"

    def test_no_index_explicit_none_backward_compat(self):
        """uses edge to Class with index=None returns type_hint (backward compat)."""
        edge = make_edge("uses")
        target = make_node("Interface", "OrderRepositoryInterface")
        assert _infer_reference_type(edge, target, index=None) == "type_hint"

    def test_source_not_in_index_returns_type_hint(self):
        """uses edge to Class where source node not found in index returns type_hint."""
        edge = make_edge("uses", source="unknown:node", target="class:Foo")
        target = make_node("Class", "Foo", "App\\Foo")
        # Index exists but doesn't contain the source node
        index = _MockIndex({})
        assert _infer_reference_type(edge, target, index) == "type_hint"

    def test_file_source_returns_type_hint(self):
        """uses edge to Class with File source returns type_hint (import statement)."""
        edge = make_edge("uses", source="file:OrderService.php", target="class:Order")
        target = make_node("Class", "Order", "App\\Entity\\Order")
        source = make_node("File", "OrderService.php", "src/Service/OrderService.php")
        source.id = "file:OrderService.php"
        source.kind = "File"

        index = _MockIndex({"file:OrderService.php": source})
        # File sources are imports, not param/return/property types
        assert _infer_reference_type(edge, target, index) == "type_hint"

    def test_class_source_returns_type_hint(self):
        """uses edge to Class with Class source returns type_hint."""
        edge = make_edge("uses", source="class:OrderService", target="class:Order")
        target = make_node("Class", "Order", "App\\Entity\\Order")
        source = make_node("Class", "OrderService", "App\\Service\\OrderService")
        source.id = "class:OrderService"

        index = _MockIndex({"class:OrderService": source})
        assert _infer_reference_type(edge, target, index) == "type_hint"

    def test_index_only_used_for_class_interface_targets(self):
        """Index is only consulted for Class/Interface/Trait/Enum targets."""
        # Method target: returns method_call regardless of index
        edge = make_edge("uses", source="method:foo", target="method:bar")
        target = make_node("Method", "bar", "App\\Foo::bar")
        source = make_node("Argument", "$x", "App\\Foo::bar.$x")
        source.id = "method:foo"

        index = _MockIndex({"method:foo": source})
        assert _infer_reference_type(edge, target, index) == "method_call"

    def test_extends_edge_ignores_index(self):
        """extends edge returns extends regardless of index."""
        edge = make_edge("extends", source="class:Child", target="class:Parent")
        target = make_node("Class", "Parent", "App\\Parent")
        source = make_node("Class", "Child", "App\\Child")
        source.id = "class:Child"

        index = _MockIndex({"class:Child": source})
        assert _infer_reference_type(edge, target, index) == "extends"


class _ArgumentMockIndex:
    """Mock index for testing _get_argument_info() expression preference.

    Supports get_arguments(), get_call_target(), get_contains_children(),
    and nodes dict.
    """

    def __init__(self, nodes: dict[str, NodeData], arguments: dict[str, list] = None,
                 call_targets: dict[str, str] = None, contains: dict[str, list[str]] = None):
        from collections import defaultdict
        self.nodes = nodes
        self._arguments = arguments or {}
        self._call_targets = call_targets or {}
        self._contains = contains or {}
        self.outgoing = defaultdict(lambda: defaultdict(list))
        self.incoming = defaultdict(lambda: defaultdict(list))

    def get_arguments(self, call_node_id: str):
        return self._arguments.get(call_node_id, [])

    def get_call_target(self, call_node_id: str):
        return self._call_targets.get(call_node_id)

    def get_contains_children(self, node_id: str):
        return self._contains.get(node_id, [])

    def get_type_of_all(self, value_node_id: str):
        return []

    def get_receiver(self, call_node_id: str):
        return None


class TestExpressionPreference:
    """Tests for _get_argument_info() expression preference (ISSUE-A, Phase 1).

    When an argument edge has an expression field, _get_argument_info() should
    use it for value_expr. When expression is None, it falls back to the
    Value node's name.
    """

    def test_expression_preferred_over_node_name(self):
        """Expression from edge is used when available, even if node has a name."""
        from src.queries.context import ContextQuery

        # Create a Value node with name "(result)" — the old fallback
        value_node = make_node("Value", "(result)", "result#1")
        value_node.id = "value:1"
        value_node.kind = "Value"
        value_node.value_kind = "result"

        index = _ArgumentMockIndex(
            nodes={"value:1": value_node},
            arguments={"call:1": [("value:1", 0, "$input->productId")]},
            call_targets={},
        )

        query = ContextQuery.__new__(ContextQuery)
        query.index = index

        args = query._get_argument_info("call:1")
        assert len(args) == 1
        assert args[0].value_expr == "$input->productId", (
            f"Should use expression '$input->productId', got '{args[0].value_expr}'"
        )
        assert args[0].value_source == "result"

    def test_fallback_to_node_name_when_no_expression(self):
        """Falls back to Value node name when expression is None."""
        from src.queries.context import ContextQuery

        value_node = make_node("Value", "$order", "local#32$order")
        value_node.id = "value:2"
        value_node.kind = "Value"
        value_node.value_kind = "local"

        index = _ArgumentMockIndex(
            nodes={"value:2": value_node},
            arguments={"call:2": [("value:2", 0, None)]},
            call_targets={},
        )

        query = ContextQuery.__new__(ContextQuery)
        query.index = index

        args = query._get_argument_info("call:2")
        assert len(args) == 1
        assert args[0].value_expr == "$order", (
            f"Should fall back to node name '$order', got '{args[0].value_expr}'"
        )
        assert args[0].value_source == "local"

    def test_empty_string_expression_falls_back_to_node_name(self):
        """Empty string expression is falsy, so falls back to node name."""
        from src.queries.context import ContextQuery

        value_node = make_node("Value", "(literal)", "literal#5")
        value_node.id = "value:3"
        value_node.kind = "Value"
        value_node.value_kind = "literal"

        index = _ArgumentMockIndex(
            nodes={"value:3": value_node},
            arguments={"call:3": [("value:3", 0, "")]},
            call_targets={},
        )

        query = ContextQuery.__new__(ContextQuery)
        query.index = index

        args = query._get_argument_info("call:3")
        assert len(args) == 1
        assert args[0].value_expr == "(literal)", (
            f"Empty expression should fall back to '(literal)', got '{args[0].value_expr}'"
        )
