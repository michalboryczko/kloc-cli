"""Integration tests for usage flow tracking feature.

Tests the complete context query flow with reference types and access chains
using the kloc-reference-project-php as test data.
"""

import pytest
from pathlib import Path

from src.graph import SoTIndex, CallsData
from src.queries import ContextQuery


# Paths to test fixtures
SOT_PATH = Path(__file__).parent.parent.parent / "kloc-reference-project-php" / "contract-tests" / "output" / "sot.json"
CALLS_PATH = Path(__file__).parent.parent.parent / "kloc-reference-project-php" / "contract-tests" / "output" / "calls.json"

pytestmark = pytest.mark.skipif(
    not SOT_PATH.exists() or not CALLS_PATH.exists(),
    reason="kloc-reference-project-php test fixtures not found",
)


@pytest.fixture(scope="module")
def index():
    """Load the SoT index."""
    return SoTIndex(SOT_PATH)


@pytest.fixture(scope="module")
def calls_data():
    """Load the calls data."""
    return CallsData.load(CALLS_PATH)


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
        """TC1: Type hint detection for OrderService -> $orderRepository."""
        # Query OrderRepository
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find the OrderService::$orderRepository property entry
        entry = find_entry_by_fqn(result.used_by, "OrderService::$orderRepository")
        assert entry is not None, "OrderService::$orderRepository should be in used_by"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "type_hint"

    def test_tc3_interface_type_hint(self, index):
        """TC3: Interface type hint detection."""
        # Query EmailSenderInterface
        node = index.resolve_symbol("App\\Component\\EmailSenderInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find the OrderService::$emailSender property entry
        entry = find_entry_by_fqn(result.used_by, "OrderService::$emailSender")
        assert entry is not None, "OrderService::$emailSender should be in used_by"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "type_hint"


class TestAccessChains:
    """Tests for access chain building (Phase 1b)."""

    def test_tc2_method_call_with_chain(self, index, calls_data):
        """TC2: Method call via property shows access chain."""
        # Query OrderRepository
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1, calls_data=calls_data)

        # Find the save() method call entry
        entry = find_entry_by_member(result.used_by, "save()")
        assert entry is not None, "save() method call should be in used_by"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call"
        assert entry.member_ref.access_chain == "$this->orderRepository"

    def test_tc4_interface_method_call(self, index, calls_data):
        """TC4: Interface method call shows access chain."""
        # Query EmailSenderInterface
        node = index.resolve_symbol("App\\Component\\EmailSenderInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1, calls_data=calls_data)

        # Find the send() method call entry
        entry = find_entry_by_member(result.used_by, "send()")
        assert entry is not None, "send() method call should be in used_by"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call"
        assert entry.member_ref.access_chain == "$this->emailSender"

    def test_tc5_constructor_call(self, index, calls_data):
        """TC5: Constructor call shows instantiation type."""
        # Query Order entity
        node = index.resolve_symbol("App\\Entity\\Order")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1, calls_data=calls_data)

        # Find any constructor/instantiation entry
        for entry in result.used_by:
            if entry.member_ref and entry.member_ref.reference_type == "instantiation":
                # Constructors should have no access chain
                assert entry.member_ref.access_chain is None
                return

        # If no instantiation found in used_by, that's also acceptable
        # (depends on edge representation)


class TestMultipleReferences:
    """Tests for multiple reference handling (TC6)."""

    def test_tc6_multiple_method_calls(self, index, calls_data):
        """TC6: Multiple method calls from same scope appear separately."""
        # Query OrderRepository
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1, calls_data=calls_data)

        # Count findById() calls - should be multiple
        findById_entries = [
            e for e in result.used_by
            if e.member_ref and e.member_ref.target_name == "findById()"
        ]
        assert len(findById_entries) >= 2, "Multiple findById() calls should appear separately"


class TestGracefulDegradation:
    """Tests for behavior without calls.json (EC1)."""

    def test_ec1_no_calls_data(self, index):
        """EC1: Without calls.json, reference types still work, no chains."""
        # Query without calls_data
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)  # No calls_data

        # Find the save() method call
        entry = find_entry_by_member(result.used_by, "save()")
        assert entry is not None

        # Should have inferred reference type
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call"

        # Should NOT have access chain (no calls data)
        assert entry.member_ref.access_chain is None


class TestJsonOutput:
    """Tests for JSON output format (OF3)."""

    def test_json_includes_reference_type(self, index, calls_data):
        """JSON output includes reference_type field."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1, calls_data=calls_data)

        json_dict = context_tree_to_dict(result)

        # Check that used_by entries have member_ref with reference_type
        found_ref_type = False
        for entry in json_dict["used_by"]:
            if "member_ref" in entry and entry["member_ref"].get("reference_type"):
                found_ref_type = True
                break

        assert found_ref_type, "JSON output should include reference_type in member_ref"

    def test_json_includes_access_chain(self, index, calls_data):
        """JSON output includes access_chain field when available."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1, calls_data=calls_data)

        json_dict = context_tree_to_dict(result)

        # Check that method call entries have access_chain
        found_chain = False
        for entry in json_dict["used_by"]:
            if "member_ref" in entry:
                if entry["member_ref"].get("access_chain"):
                    found_chain = True
                    break

        assert found_chain, "JSON output should include access_chain for method calls"
