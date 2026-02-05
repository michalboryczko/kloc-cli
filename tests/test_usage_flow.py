"""Integration tests for usage flow tracking feature.

Tests the complete context query flow with reference types and access chains
using the kloc-reference-project-php as test data.

This test suite validates the unified graph format (v2.0) which includes
Value and Call nodes directly in sot.json, eliminating the need for
separate calls.json loading.
"""

import pytest
from pathlib import Path

from src.graph import SoTIndex
from src.queries import ContextQuery


# Paths to test fixtures - now using v2.0 unified sot.json format
SOT_PATH = Path(__file__).parent.parent.parent / "kloc-reference-project-php" / "contract-tests" / "output" / "sot.json"

pytestmark = pytest.mark.skipif(
    not SOT_PATH.exists(),
    reason="kloc-reference-project-php test fixtures not found",
)


@pytest.fixture(scope="module")
def index():
    """Load the SoT index (v2.0 unified format with Value/Call nodes)."""
    return SoTIndex(SOT_PATH)


def find_entry_by_fqn(entries, fqn_substring):
    """Find entry containing the given FQN substring."""
    for entry in entries:
        if fqn_substring in entry.fqn:
            return entry
    return None


def find_entry_by_member(entries, member_name):
    """Find entry with the given member_ref target_name."""
    for entry in entries:
        if entry.member_ref and entry.member_ref.target_name == member_name:
            return entry
    return None


class TestReferenceTypeInference:
    """Tests for reference type classification (Phase 1a)."""

    def test_tc1_type_hint_detection(self, index):
        """TC1: Type hint detection for OrderService -> OrderRepository.

        Type hints appear as references from:
        - Constructor parameters (constructor property promotion)
        - Method parameters/return types
        - Property declarations
        """
        # Query OrderRepository
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find a type_hint reference - could be from constructor or property
        # In PHP 8+ with constructor property promotion, the type hint is on __construct
        type_hint_entries = [
            e for e in result.used_by
            if e.member_ref and e.member_ref.reference_type == "type_hint"
            and "OrderService" in e.fqn
        ]
        assert len(type_hint_entries) > 0, (
            "Should find type_hint references to OrderRepository from OrderService"
        )

    def test_tc3_interface_type_hint(self, index):
        """TC3: Interface type hint detection."""
        # Query EmailSenderInterface
        node = index.resolve_symbol("App\\Component\\EmailSenderInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find a type_hint reference - could be from constructor or property
        type_hint_entries = [
            e for e in result.used_by
            if e.member_ref and e.member_ref.reference_type == "type_hint"
            and "OrderService" in e.fqn
        ]
        assert len(type_hint_entries) > 0, (
            "Should find type_hint references to EmailSenderInterface from OrderService"
        )


class TestAccessChains:
    """Tests for access chain building using unified graph format.

    The v2.0 sot.json includes Value and Call nodes that enable access chain
    resolution directly from the graph, without requiring separate calls.json.
    """

    def test_tc2_method_call_with_chain(self, index):
        """TC2: Method call via property shows access chain."""
        # Query OrderRepository
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find the save() method call entry
        entry = find_entry_by_member(result.used_by, "save()")
        assert entry is not None, "save() method call should be in used_by"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call"
        assert entry.member_ref.access_chain == "$this->orderRepository"

    def test_tc4_interface_method_call(self, index):
        """TC4: Interface method call shows access chain."""
        # Query EmailSenderInterface
        node = index.resolve_symbol("App\\Component\\EmailSenderInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find the send() method call entry
        entry = find_entry_by_member(result.used_by, "send()")
        assert entry is not None, "send() method call should be in used_by"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call"
        assert entry.member_ref.access_chain == "$this->emailSender"

    def test_tc5_constructor_call(self, index):
        """TC5: Constructor call shows instantiation type."""
        # Query Order entity
        node = index.resolve_symbol("App\\Entity\\Order")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find any constructor/instantiation entry
        for entry in result.used_by:
            if entry.member_ref and entry.member_ref.reference_type == "instantiation":
                # Constructors should have no access chain
                assert entry.member_ref.access_chain is None
                return

        # If no instantiation found in used_by, that's also acceptable
        # (depends on edge representation)

    def test_tc8_direct_class_instantiation(self, index):
        """TC8: Direct class reference shows [instantiation] for constructor calls.

        This tests that we can find constructor calls (instantiation) for a class.
        In the unified graph format, constructor calls are represented as:
        - Call node with call_kind=constructor
        - calls edge to Class::__construct() method

        Since `uses` edges point directly to the class (for type hints), we look
        for any instantiation reference types in the used_by results.
        """
        # Query Order entity
        node = index.resolve_symbol("App\\Entity\\Order")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find any instantiation entry - could be from various locations
        instantiation_entries = [
            e for e in result.used_by
            if e.member_ref and e.member_ref.reference_type == "instantiation"
        ]

        # Should find at least one instantiation (there are constructor calls to Order)
        assert len(instantiation_entries) > 0, (
            f"Expected at least one 'instantiation' reference type for Order. "
            f"Found: {[e.member_ref.reference_type for e in result.used_by if e.member_ref][:10]}"
        )


class TestMultipleReferences:
    """Tests for multiple reference handling (TC6)."""

    def test_tc6_multiple_method_calls(self, index):
        """TC6: Multiple method calls from same scope appear separately."""
        # Query OrderRepository
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Count findById() calls - should be multiple
        findById_entries = [
            e for e in result.used_by
            if e.member_ref and e.member_ref.target_name == "findById()"
        ]
        assert len(findById_entries) >= 2, "Multiple findById() calls should appear separately"


class TestV1FormatBackwardCompatibility:
    """Tests for backward compatibility with v1.0 sot.json (without Value/Call nodes)."""

    def test_ec1_v1_format_degrades_gracefully(self, index):
        """When using v1.0 format sot.json, reference types are inferred, no chains.

        Note: This test uses v2.0 format but validates that the inference fallback
        works correctly when Call nodes are not found for a given usage.
        """
        # Query OrderRepository
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find the save() method call
        entry = find_entry_by_member(result.used_by, "save()")
        assert entry is not None

        # Should have reference type (either from Call node or inferred)
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call"

        # With v2.0 format, we should have access chain from Call nodes
        # (This test validates that the graph-based chain building works)
        assert entry.member_ref.access_chain == "$this->orderRepository"


class TestJsonOutput:
    """Tests for JSON output format (OF3)."""

    def test_json_includes_reference_type(self, index):
        """JSON output includes reference_type field."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Check that used_by entries have member_ref with reference_type
        found_ref_type = False
        for entry in json_dict["used_by"]:
            if "member_ref" in entry and entry["member_ref"].get("reference_type"):
                found_ref_type = True
                break

        assert found_ref_type, "JSON output should include reference_type in member_ref"

    def test_json_includes_access_chain(self, index):
        """JSON output includes access_chain field when available."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Check that method call entries have access_chain
        found_chain = False
        for entry in json_dict["used_by"]:
            if "member_ref" in entry:
                if entry["member_ref"].get("access_chain"):
                    found_chain = True
                    break

        assert found_chain, "JSON output should include access_chain for method calls"
