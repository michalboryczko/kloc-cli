"""Tests for the SoT index module."""

import pytest
import json
import tempfile
from pathlib import Path

from src.graph import SoTIndex


@pytest.fixture
def sample_sot():
    """Create a minimal SoT JSON for testing."""
    data = {
        "version": "1.0",
        "metadata": {},
        "nodes": [
            {
                "id": "node:file1",
                "kind": "File",
                "name": "Foo.php",
                "fqn": "src/Foo.php",
                "symbol": "file:src/Foo.php",
                "file": "src/Foo.php",
                "range": None,
                "documentation": [],
            },
            {
                "id": "node:class1",
                "kind": "Class",
                "name": "Foo",
                "fqn": "App\\Entity\\Foo",
                "symbol": "scip-php ... App/Entity/Foo#",
                "file": "src/Foo.php",
                "range": {"start_line": 10, "start_col": 0, "end_line": 50, "end_col": 0},
                "documentation": ["class Foo"],
            },
            {
                "id": "node:method1",
                "kind": "Method",
                "name": "bar",
                "fqn": "App\\Entity\\Foo::bar()",
                "symbol": "scip-php ... App/Entity/Foo#bar().",
                "file": "src/Foo.php",
                "range": {"start_line": 20, "start_col": 4, "end_line": 30, "end_col": 4},
                "documentation": [],
            },
            {
                "id": "node:class2",
                "kind": "Class",
                "name": "Bar",
                "fqn": "App\\Entity\\Bar",
                "symbol": "scip-php ... App/Entity/Bar#",
                "file": "src/Bar.php",
                "range": {"start_line": 5, "start_col": 0, "end_line": 40, "end_col": 0},
                "documentation": [],
            },
        ],
        "edges": [
            {"type": "contains", "source": "node:file1", "target": "node:class1"},
            {"type": "contains", "source": "node:class1", "target": "node:method1"},
            {"type": "extends", "source": "node:class2", "target": "node:class1"},
            {
                "type": "uses",
                "source": "node:method1",
                "target": "node:class2",
                "location": {"file": "src/Foo.php", "line": 25, "col": 8},
            },
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        return f.name


@pytest.fixture
def index(sample_sot):
    """Create an index from the sample SoT."""
    return SoTIndex(sample_sot)


class TestSoTIndex:
    def test_load_nodes(self, index):
        assert len(index.nodes) == 4
        assert "node:class1" in index.nodes

    def test_load_edges(self, index):
        assert len(index.edges) == 4

    def test_resolve_exact_fqn(self, index):
        candidates = index.resolve_symbol("App\\Entity\\Foo")
        assert len(candidates) == 1
        assert candidates[0].name == "Foo"

    def test_resolve_method_fqn(self, index):
        candidates = index.resolve_symbol("App\\Entity\\Foo::bar()")
        assert len(candidates) == 1
        assert candidates[0].kind == "Method"

    def test_resolve_short_name(self, index):
        candidates = index.resolve_symbol("Foo")
        assert len(candidates) >= 1
        assert any(c.name == "Foo" for c in candidates)

    def test_resolve_case_insensitive(self, index):
        candidates = index.resolve_symbol("app\\entity\\foo")
        assert len(candidates) >= 1

    def test_get_usages(self, index):
        usages = index.get_usages("node:class2")
        assert len(usages) == 1
        assert usages[0].source == "node:method1"

    def test_get_deps(self, index):
        deps = index.get_deps("node:method1")
        assert len(deps) == 1
        assert deps[0].target == "node:class2"

    def test_get_contains_parent(self, index):
        parent_id = index.get_contains_parent("node:method1")
        assert parent_id == "node:class1"

    def test_get_contains_children(self, index):
        children = index.get_contains_children("node:class1")
        assert "node:method1" in children

    def test_get_extends_parent(self, index):
        parent_id = index.get_extends_parent("node:class2")
        assert parent_id == "node:class1"

    def test_get_extends_children(self, index):
        children = index.get_extends_children("node:class1")
        assert "node:class2" in children


@pytest.fixture
def v2_sot_with_expression():
    """Create a v2.0 SoT JSON with Call/Value nodes and argument edges with expression."""
    data = {
        "version": "2.0",
        "metadata": {},
        "nodes": [
            {
                "id": "node:method1",
                "kind": "Method",
                "name": "process",
                "fqn": "App\\Service::process()",
                "symbol": "scip-php ... Service#process().",
                "file": "src/Service.php",
                "range": {"start_line": 10, "start_col": 0, "end_line": 30, "end_col": 0},
                "documentation": [],
            },
            {
                "id": "node:call:abc",
                "kind": "Call",
                "name": "save()",
                "fqn": "App\\Service::process()@15:8",
                "symbol": "",
                "file": "src/Service.php",
                "range": {"start_line": 15, "start_col": 8, "end_line": 15, "end_col": 12},
                "documentation": [],
                "call_kind": "method",
            },
            {
                "id": "node:val:arg0",
                "kind": "Value",
                "name": "$order",
                "fqn": "App\\Service::process().$order",
                "symbol": "scip-php ... #process().local$order@10",
                "file": "src/Service.php",
                "range": {"start_line": 15, "start_col": 13, "end_line": 15, "end_col": 19},
                "documentation": [],
                "value_kind": "local",
            },
            {
                "id": "node:val:arg1",
                "kind": "Value",
                "name": "(result)",
                "fqn": "src/Service.php:15:(result)",
                "symbol": "",
                "file": "src/Service.php",
                "range": {"start_line": 15, "start_col": 21, "end_line": 15, "end_col": 35},
                "documentation": [],
                "value_kind": "result",
            },
        ],
        "edges": [
            {
                "type": "argument",
                "source": "node:call:abc",
                "target": "node:val:arg0",
                "position": 0,
                "expression": "$input->productId",
            },
            {
                "type": "argument",
                "source": "node:call:abc",
                "target": "node:val:arg1",
                "position": 1,
            },
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        return f.name


class TestGetArgumentsWithExpression:
    def test_returns_expression(self, v2_sot_with_expression):
        """get_arguments() should return 3-tuple with expression."""
        idx = SoTIndex(v2_sot_with_expression)
        args = idx.get_arguments("node:call:abc")

        assert len(args) == 2
        # First arg has expression
        assert args[0] == ("node:val:arg0", 0, "$input->productId")
        # Second arg has no expression
        assert args[1] == ("node:val:arg1", 1, None)

    def test_sorted_by_position(self, v2_sot_with_expression):
        """Arguments should be sorted by position."""
        idx = SoTIndex(v2_sot_with_expression)
        args = idx.get_arguments("node:call:abc")

        positions = [a[1] for a in args]
        assert positions == [0, 1]

    def test_no_arguments(self, v2_sot_with_expression):
        """Call with no argument edges returns empty list."""
        idx = SoTIndex(v2_sot_with_expression)
        args = idx.get_arguments("node:method1")
        assert args == []


@pytest.fixture
def v2_sot_with_type_of():
    """Create a v2.0 SoT JSON with Value nodes and type_of edges (including union types)."""
    data = {
        "version": "2.0",
        "metadata": {},
        "nodes": [
            {
                "id": "node:val:param",
                "kind": "Value",
                "name": "$input",
                "fqn": "App\\Service::process().$input",
                "symbol": "scip-php ... #process().local$input@10",
                "file": "src/Service.php",
                "documentation": [],
                "value_kind": "parameter",
            },
            {
                "id": "node:val:union",
                "kind": "Value",
                "name": "$result",
                "fqn": "App\\Service::process().$result",
                "symbol": "scip-php ... #process().local$result@15",
                "file": "src/Service.php",
                "documentation": [],
                "value_kind": "local",
            },
            {
                "id": "node:val:notype",
                "kind": "Value",
                "name": "$temp",
                "fqn": "App\\Service::process().$temp",
                "symbol": "scip-php ... #process().local$temp@20",
                "file": "src/Service.php",
                "documentation": [],
                "value_kind": "local",
            },
            {
                "id": "node:class:order",
                "kind": "Class",
                "name": "Order",
                "fqn": "App\\Entity\\Order",
                "symbol": "scip-php ... App/Entity/Order#",
                "file": "src/Order.php",
                "documentation": [],
            },
            {
                "id": "node:iface:serializable",
                "kind": "Interface",
                "name": "Serializable",
                "fqn": "App\\Contract\\Serializable",
                "symbol": "scip-php ... App/Contract/Serializable#",
                "file": "src/Serializable.php",
                "documentation": [],
            },
        ],
        "edges": [
            {
                "type": "type_of",
                "source": "node:val:param",
                "target": "node:class:order",
            },
            {
                "type": "type_of",
                "source": "node:val:union",
                "target": "node:class:order",
            },
            {
                "type": "type_of",
                "source": "node:val:union",
                "target": "node:iface:serializable",
            },
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        return f.name


class TestGetTypeOfAll:
    def test_single_type(self, v2_sot_with_type_of):
        """Value with single type_of edge returns one target."""
        idx = SoTIndex(v2_sot_with_type_of)
        types = idx.get_type_of_all("node:val:param")
        assert types == ["node:class:order"]

    def test_union_type(self, v2_sot_with_type_of):
        """Value with multiple type_of edges (union type) returns all targets."""
        idx = SoTIndex(v2_sot_with_type_of)
        types = idx.get_type_of_all("node:val:union")
        assert len(types) == 2
        assert "node:class:order" in types
        assert "node:iface:serializable" in types

    def test_no_type(self, v2_sot_with_type_of):
        """Value with no type_of edges returns empty list."""
        idx = SoTIndex(v2_sot_with_type_of)
        types = idx.get_type_of_all("node:val:notype")
        assert types == []
