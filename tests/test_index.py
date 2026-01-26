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
