"""Tests for CallsData loader and access chain building."""

import pytest
from pathlib import Path

from src.graph.calls import CallsData


CALLS_PATH = Path(__file__).parent.parent.parent / "kloc-reference-project-php" / "contract-tests" / "output" / "calls.json"

pytestmark = pytest.mark.skipif(
    not CALLS_PATH.exists(),
    reason="kloc-reference-project-php/contract-tests/output/calls.json not found",
)


@pytest.fixture(scope="module")
def calls_data():
    """Load the calls.json from reference project."""
    return CallsData.load(CALLS_PATH)


class TestCallsDataLoad:
    """Tests for CallsData loading and indexing."""

    def test_load_version(self, calls_data):
        """Calls data has version."""
        assert calls_data.version.startswith("3.")

    def test_load_has_values(self, calls_data):
        """Calls data has values indexed."""
        assert len(calls_data.values_by_id) > 0

    def test_load_has_calls(self, calls_data):
        """Calls data has calls indexed."""
        assert len(calls_data.calls_by_id) > 0

    def test_load_has_location_index(self, calls_data):
        """Calls data has calls indexed by location."""
        assert len(calls_data.calls_by_location) > 0


class TestGetCallAt:
    """Tests for get_call_at() location lookup."""

    def test_get_call_at_line_40(self, calls_data):
        """Find method call at OrderService.php line 40 (save())."""
        # Line 40 has both property access and method call
        call = calls_data.get_call_at(
            "src/Service/OrderService.php", 40,
            callee="OrderRepository#save"
        )
        assert call is not None
        assert call.kind == "method"
        assert "save" in call.callee

    def test_get_call_at_line_42(self, calls_data):
        """Find method call at OrderService.php line 42 (send())."""
        call = calls_data.get_call_at(
            "src/Service/OrderService.php", 42,
            callee="send"
        )
        assert call is not None
        assert call.kind == "method"
        assert "send" in call.callee

    def test_get_call_at_nonexistent(self, calls_data):
        """Returns None for nonexistent location."""
        call = calls_data.get_call_at("nonexistent.php", 999)
        assert call is None


class TestBuildAccessChain:
    """Tests for build_chain_for_callee() access chain building."""

    def test_chain_for_save_call(self, calls_data):
        """Build chain for save() call returns $this->orderRepository."""
        call = calls_data.get_call_at(
            "src/Service/OrderService.php", 40,
            callee="OrderRepository#save"
        )
        assert call is not None
        chain = calls_data.build_chain_for_callee(call)
        assert chain == "$this->orderRepository"

    def test_chain_for_send_call(self, calls_data):
        """Build chain for send() call returns $this->emailSender."""
        call = calls_data.get_call_at(
            "src/Service/OrderService.php", 42,
            callee="send"
        )
        assert call is not None
        chain = calls_data.build_chain_for_callee(call)
        assert chain == "$this->emailSender"

    def test_chain_for_static_call(self, calls_data):
        """Static calls have no access chain."""
        # Find a constructor call which has no receiver
        for call in calls_data.calls_by_id.values():
            if call.kind == "constructor":
                chain = calls_data.build_chain_for_callee(call)
                assert chain is None
                break


class TestReferenceType:
    """Tests for get_reference_type() call kind mapping."""

    def test_method_kind_maps_to_method_call(self, calls_data):
        """kind=method maps to method_call reference type."""
        call = calls_data.get_call_at(
            "src/Service/OrderService.php", 40,
            callee="OrderRepository#save"
        )
        assert call is not None
        ref_type = calls_data.get_reference_type(call)
        assert ref_type == "method_call"

    def test_constructor_kind_maps_to_instantiation(self, calls_data):
        """kind=constructor maps to instantiation reference type."""
        # Find a constructor call
        for call in calls_data.calls_by_id.values():
            if call.kind == "constructor":
                ref_type = calls_data.get_reference_type(call)
                assert ref_type == "instantiation"
                break

    def test_access_kind_maps_to_property_access(self, calls_data):
        """kind=access maps to property_access reference type."""
        # Property access on line 40 (orderRepository)
        call = calls_data.get_call_at(
            "src/Service/OrderService.php", 40,
            callee="$orderRepository"
        )
        assert call is not None
        ref_type = calls_data.get_reference_type(call)
        assert ref_type == "property_access"
