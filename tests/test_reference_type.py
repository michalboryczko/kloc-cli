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
