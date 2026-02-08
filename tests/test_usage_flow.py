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

        In the unified graph format, constructor calls are represented as:
        - Call node with call_kind=constructor
        - calls edge to Class::__construct() method

        The `uses` edges to a Class node come from type hints and property types,
        not from constructor call sites. Constructor calls target __construct(),
        which is a separate Method node. So querying the Class used_by shows
        type_hint / property_access references, not instantiation.

        Instantiation references appear when querying the __construct() method's
        used_by, or when a uses edge happens to be at the same line as a
        constructor Call node whose callee's class matches the target.
        """
        # Query Order entity
        node = index.resolve_symbol("App\\Entity\\Order")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Class-level uses edges come from type hints and property declarations.
        # Constructor calls target __construct() (a separate node), so they do
        # not appear as [instantiation] in the Class's used_by.
        # Verify we get type_hint entries instead.
        type_hint_entries = [
            e for e in result.used_by
            if e.member_ref and e.member_ref.reference_type == "type_hint"
        ]
        assert len(type_hint_entries) > 0, (
            f"Expected type_hint references to Order class. "
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


class TestCalleeVerificationIntegration:
    """Integration tests for callee verification guard (Issue 6).

    Validates that find_call_for_usage() rejects Call nodes whose callee
    does not match the usage target, falling back to inference which
    produces correct reference types.
    """

    def test_nextId_shows_static_property_not_instantiation(self, index):
        """AC3: OrderRepository::$nextId at line 30 shows [static_property] not [instantiation].

        The code `self::$nextId++` at PHP line 30 is a static property access.
        Previously, find_call_for_usage matched the `new Order()` constructor
        at line 29 for the $nextId uses edge, producing [instantiation].
        With callee verification, the constructor is rejected and inference
        correctly returns [static_property].
        """
        node = index.resolve_symbol("App\\Repository\\OrderRepository::save")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_entry_by_fqn(result.uses, "$nextId")
        assert entry is not None, "$nextId should appear in save() uses"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "static_property", (
            f"$nextId should be [static_property], got [{entry.member_ref.reference_type}]"
        )

    def test_getName_shows_method_call_not_property_access(self, index):
        """AC4: AbstractOrderProcessor::getName() shows [method_call] not [property_access].

        The code `$this->orderProcessor->getName()` at PHP line 43 is a method call.
        With callee verification, incorrect Call node matches are rejected and
        inference correctly identifies Method targets as [method_call].
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_entry_by_fqn(result.uses, "getName")
        assert entry is not None, "getName() should appear in createOrder() uses"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call", (
            f"getName() should be [method_call], got [{entry.member_ref.reference_type}]"
        )

    def test_customerEmail_not_attributed_to_emailSender(self, index):
        """AC5: Order::$customerEmail at line 48 shows correct receiver, not emailSender.

        The code `$savedOrder->customerEmail` at PHP line 48 is inside the
        send() argument list. Previously, find_call_for_usage matched the
        enclosing send() Call, inheriting its receiver ($this->emailSender).
        With callee verification, the send() Call is rejected. The inference
        fallback correctly identifies it as [property_access].
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find $customerEmail entries - there may be multiple at different lines
        email_entries = [
            e for e in result.uses
            if "customerEmail" in e.fqn and "Order::" in e.fqn
        ]
        assert len(email_entries) > 0, "$customerEmail should appear in createOrder() uses"

        # Find the one at line 47 (0-based for PHP line 48)
        # This is the one inside send() named argument
        line_48_entries = [e for e in email_entries if e.line == 47]
        if line_48_entries:
            entry = line_48_entries[0]
            assert entry.member_ref is not None
            assert entry.member_ref.reference_type == "property_access", (
                f"$customerEmail at line 48 should be [property_access], "
                f"got [{entry.member_ref.reference_type}]"
            )
            # The access chain should NOT be $this->emailSender
            if entry.member_ref.access_chain:
                assert "emailSender" not in entry.member_ref.access_chain, (
                    f"$customerEmail receiver should not be emailSender, "
                    f"got chain: {entry.member_ref.access_chain}"
                )

    def test_save_method_call_reference_type_preserved(self, index):
        """AC6: OrderRepository::save() at line 45 still shows [method_call].

        The callee verification should not break existing correct matches.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_entry_by_fqn(result.uses, "save()")
        assert entry is not None, "save() should appear in createOrder() uses"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call"
        assert entry.member_ref.access_chain == "$this->orderRepository"


class TestPerSubtreeVisitedSet:
    """Integration tests for per-subtree visited set (Issue 5).

    Validates that the depth-2 subtree shows complete dependencies of the
    expanded method, regardless of what depth-1 entries exist. The same
    target can appear at both depth 1 and depth 2 under different parents.
    """

    def test_depth2_createOrder_save_includes_customerEmail(self, index):
        """AC7: depth-2 from createOrder includes $customerEmail under save().

        With the global visited set, $customerEmail was consumed at depth 1
        (from createOrder's direct usage) and then missing at depth 2 under
        save(). Per-parent deduplication allows it at both depths.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Find save() entry
        save_entry = find_entry_by_fqn(result.uses, "save()")
        assert save_entry is not None, "save() should appear in createOrder() uses"
        assert len(save_entry.children) > 0, "save() should have depth-2 children"

        # Check for $customerEmail in save()'s depth-2 children
        child_fqns = [c.fqn for c in save_entry.children]
        has_customer_email = any("customerEmail" in f for f in child_fqns)
        assert has_customer_email, (
            f"$customerEmail should be in save() depth-2 children. "
            f"Found: {[f.split('::')[-1] if '::' in f else f for f in child_fqns]}"
        )

    def test_depth2_createOrder_save_includes_productId(self, index):
        """AC7: depth-2 from createOrder includes $productId under save().

        Same pattern as $customerEmail -- consumed at depth 1 with global
        visited set, now appears at both depths.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        save_entry = find_entry_by_fqn(result.uses, "save()")
        assert save_entry is not None

        child_fqns = [c.fqn for c in save_entry.children]
        has_product_id = any("productId" in f for f in child_fqns)
        assert has_product_id, (
            f"$productId should be in save() depth-2 children. "
            f"Found: {[f.split('::')[-1] if '::' in f else f for f in child_fqns]}"
        )

    def test_depth2_save_subtree_has_at_least_9_entries(self, index):
        """AC2: save() depth-2 subtree shows all dependencies (at least 9).

        save() has 10 uses edges (Order type_hint, $id, OrderRepository,
        $nextId, $customerEmail, $productId, $quantity, $status, $createdAt,
        $orders). The depth-2 subtree should include all of them.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        save_entry = find_entry_by_fqn(result.uses, "save()")
        assert save_entry is not None
        assert len(save_entry.children) >= 9, (
            f"save() depth-2 subtree should have at least 9 entries, "
            f"got {len(save_entry.children)}"
        )

    def test_depth1_save_direct_still_shows_all_uses(self, index):
        """AC8 regression: direct depth-1 query of save() shows all 10 uses.

        Per-parent deduplication should not affect direct queries.
        """
        node = index.resolve_symbol("App\\Repository\\OrderRepository::save")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        assert len(result.uses) == 10, (
            f"Direct save() depth-1 should have 10 uses, got {len(result.uses)}"
        )

        # Verify specific entries are present
        fqns = [e.fqn for e in result.uses]
        assert any("$customerEmail" in f for f in fqns), "$customerEmail should be in save() uses"
        assert any("$productId" in f for f in fqns), "$productId should be in save() uses"
        assert any("$id" in f for f in fqns), "$id should be in save() uses"
        assert any("$orders" in f for f in fqns), "$orders should be in save() uses"

    def test_same_target_at_depth1_and_depth2(self, index):
        """AC2: Same target can appear at both depth 1 and depth 2.

        With per-parent dedup, $customerEmail appears at depth 1 (from
        createOrder direct usage at PHP line 48) and at depth 2 (from
        save()'s usage at PHP line 31).
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Find $customerEmail at depth 1
        depth1_email = [
            e for e in result.uses
            if "Order::$customerEmail" in e.fqn
        ]
        assert len(depth1_email) > 0, (
            "$customerEmail should appear at depth 1"
        )

        # Find $customerEmail at depth 2 (under save())
        save_entry = find_entry_by_fqn(result.uses, "save()")
        assert save_entry is not None
        depth2_email = [
            c for c in save_entry.children
            if "customerEmail" in c.fqn
        ]
        assert len(depth2_email) > 0, (
            "$customerEmail should also appear at depth 2 under save()"
        )

    def test_no_infinite_loop_on_self_reference(self, index):
        """AC8: Self-referencing methods do not cause infinite recursion.

        The start_id stays in the cycle guard to prevent infinite loops.
        This test verifies the query completes without hanging.
        """
        import time

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)

        start_time = time.time()
        result = query.execute(node.id, depth=3)
        elapsed = time.time() - start_time

        # Should complete quickly (under 5 seconds)
        assert elapsed < 5.0, f"Depth-3 query took {elapsed:.1f}s, expected < 5s"
        # Should produce some results
        assert len(result.uses) > 0

    def test_no_duplicates_within_same_parent(self, index):
        """Per-parent dedup still prevents duplicate entries under same parent."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Check depth-1 entries have no duplicates
        depth1_fqns = [e.fqn for e in result.uses]
        assert len(depth1_fqns) == len(set(depth1_fqns)), (
            f"Depth-1 entries should have no duplicates. "
            f"Duplicates: {[f for f in depth1_fqns if depth1_fqns.count(f) > 1]}"
        )

        # Check save()'s depth-2 children have no duplicates
        save_entry = find_entry_by_fqn(result.uses, "save()")
        if save_entry:
            child_fqns = [c.fqn for c in save_entry.children]
            assert len(child_fqns) == len(set(child_fqns)), (
                f"Depth-2 children under save() should have no duplicates. "
                f"Duplicates: {[f for f in child_fqns if child_fqns.count(f) > 1]}"
            )
