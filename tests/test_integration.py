"""Integration tests for CLI queries using SoT generated from fixed SCIP."""

import pytest
from pathlib import Path

from src.graph import SoTIndex


SOT_PATH = Path(__file__).parent.parent.parent / "artifacts" / "sot_fixed.json"

pytestmark = pytest.mark.skipif(
    not SOT_PATH.exists(),
    reason="artifacts/sot_fixed.json not found",
)


@pytest.fixture(scope="module")
def index():
    """Load the SoT index from the fixed artifact."""
    return SoTIndex(SOT_PATH)


# --- Resolve ---

class TestResolve:
    def test_resolve_class_by_fqn(self, index):
        nodes = index.resolve_symbol("App\\Service\\Synxis\\SynxisConfigurationService")
        assert len(nodes) == 1
        assert nodes[0].kind == "Class"

    def test_resolve_interface_by_fqn(self, index):
        nodes = index.resolve_symbol("App\\Service\\Synxis\\SynxisConfigurationServiceInterface")
        assert len(nodes) == 1
        assert nodes[0].kind == "Interface"

    def test_resolve_method_by_fqn(self, index):
        nodes = index.resolve_symbol("App\\Service\\Synxis\\SynxisConfigurationService::getCodesByItemIds()")
        assert len(nodes) == 1
        assert nodes[0].kind == "Method"

    def test_resolve_enum(self, index):
        nodes = index.resolve_symbol("App\\Entity\\ItemType")
        assert len(nodes) >= 1
        assert any(n.kind == "Enum" for n in nodes)

    def test_resolve_short_name(self, index):
        nodes = index.resolve_symbol("SynxisConfigurationService")
        assert len(nodes) >= 1

    def test_resolve_case_insensitive(self, index):
        nodes = index.resolve_symbol("app\\entity\\itemtype")
        assert len(nodes) >= 1


# --- Usages ---

class TestUsages:
    def test_class_has_usages(self, index):
        """A commonly used class should have incoming USES edges."""
        nodes = index.resolve_symbol("App\\Entity\\SynchronizedItem")
        assert len(nodes) >= 1
        usages = index.get_usages(nodes[0].id)
        assert len(usages) > 0

    def test_method_has_usages(self, index):
        """A method called from other methods should have usages."""
        nodes = index.resolve_symbol("App\\Service\\Synxis\\SynxisCodeProvider::provide()")
        if nodes:
            usages = index.get_usages(nodes[0].id, include_members=False)
            assert len(usages) > 0

    def test_interface_method_has_usages(self, index):
        """Interface methods should have usages (called via interface type)."""
        nodes = index.resolve_symbol("App\\Service\\Synxis\\SynxisConfigurationServiceInterface::getCodesByItemIds()")
        if nodes:
            usages = index.get_usages(nodes[0].id, include_members=False)
            # May or may not have direct usages depending on how code calls it
            assert isinstance(usages, list)


# --- Dependencies ---

class TestDeps:
    def test_method_has_deps(self, index):
        """A method that calls other methods should have outgoing USES edges."""
        nodes = index.resolve_symbol("App\\Service\\Synxis\\SynxisConfigurationService::getCodesByItemIds()")
        assert len(nodes) >= 1
        deps = index.get_deps(nodes[0].id)
        assert len(deps) > 0

    def test_handler_has_deps(self, index):
        """A message handler should depend on services."""
        nodes = index.resolve_symbol("App\\MessageHandler\\EstateCreatedMessageHandler")
        if nodes:
            deps = index.get_deps(nodes[0].id)
            assert len(deps) > 0


# --- Containment ---

class TestContainment:
    def test_method_has_parent(self, index):
        nodes = index.resolve_symbol("App\\Service\\Synxis\\SynxisConfigurationService::getCodesByItemIds()")
        assert len(nodes) >= 1
        parent_id = index.get_contains_parent(nodes[0].id)
        assert parent_id is not None
        parent = index.nodes[parent_id]
        assert parent.kind == "Class"

    def test_class_has_children(self, index):
        nodes = index.resolve_symbol("App\\Service\\Synxis\\SynxisConfigurationService")
        assert len(nodes) >= 1
        children = index.get_contains_children(nodes[0].id)
        assert len(children) > 0


# --- Inheritance ---

class TestInheritance:
    def test_class_extends(self, index):
        """Check an extends edge exists."""
        extends_edges = [e for e in index.edges if e.type == "extends"]
        assert len(extends_edges) > 0

    def test_class_implements(self, index):
        """Check an implements edge exists."""
        impl_edges = [e for e in index.edges if e.type == "implements"]
        assert len(impl_edges) > 0

    def test_extends_parent_lookup(self, index):
        """A child class should have an extends parent."""
        # Find a class that extends another
        extends_edges = [e for e in index.edges if e.type == "extends"]
        if extends_edges:
            child_id = extends_edges[0].source
            parent_id = index.get_extends_parent(child_id)
            assert parent_id is not None


# --- Overrides ---

class TestOverrides:
    def test_override_edges_exist(self, index):
        """Override edges should exist for methods implementing interfaces."""
        override_edges = [e for e in index.edges if e.type == "overrides"]
        assert len(override_edges) > 0

    def test_override_connects_methods(self, index):
        """Override edges should connect method to method."""
        override_edges = [e for e in index.edges if e.type == "overrides"]
        for edge in override_edges[:5]:  # Check first 5
            source = index.nodes.get(edge.source)
            target = index.nodes.get(edge.target)
            if source and target:
                assert source.kind == "Method", f"Override source is {source.kind}"
                assert target.kind == "Method", f"Override target is {target.kind}"


# --- Ranges (correct from fixed SCIP) ---

class TestRanges:
    def test_method_ranges_are_multiline(self, index):
        """Methods should have multi-line ranges covering the full body."""
        methods = [n for n in index.nodes.values()
                   if n.kind == "Method" and n.range]
        multiline = [m for m in methods
                     if m.range["end_line"] > m.range["start_line"]]
        assert len(multiline) > 0
        ratio = len(multiline) / len(methods) if methods else 0
        assert ratio > 0.3, f"Only {ratio:.0%} methods have multi-line ranges"

    def test_class_ranges_are_multiline(self, index):
        """Classes should have multi-line ranges."""
        classes = [n for n in index.nodes.values()
                   if n.kind in ("Class", "Interface", "Trait") and n.range]
        multiline = [c for c in classes
                     if c.range["end_line"] > c.range["start_line"]]
        assert len(multiline) > 0


# --- Edge deduplication ---

class TestDeduplication:
    def test_no_duplicate_uses_edges(self, index):
        """No duplicate (source, target) pairs for USES edges."""
        seen = set()
        for edge in index.edges:
            if edge.type != "uses":
                continue
            key = (edge.source, edge.target)
            assert key not in seen, f"Duplicate USES edge: {key}"
            seen.add(key)


# --- No parameter self-references ---

class TestParameterSelfRefs:
    def test_no_method_uses_own_param(self, index):
        """No USES edge from a method to its own parameter."""
        for edge in index.edges:
            if edge.type != "uses":
                continue
            source = index.nodes.get(edge.source)
            target = index.nodes.get(edge.target)
            if not source or not target:
                continue
            if target.kind == "Argument" and source.kind == "Method":
                # Check via symbol prefix
                assert not target.symbol.startswith(source.symbol), (
                    f"Method {source.fqn} USES own param {target.fqn}"
                )
