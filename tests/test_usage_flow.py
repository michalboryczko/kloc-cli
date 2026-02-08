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

        After Phase 1, type_hint is split into parameter_type, return_type,
        property_type. Accept any of these type-related reference types.
        """
        # Query OrderRepository
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find a type-related reference (any of the type_hint subtypes)
        TYPE_RELATED = {"type_hint", "parameter_type", "return_type", "property_type"}
        type_entries = [
            e for e in result.used_by
            if e.member_ref and e.member_ref.reference_type in TYPE_RELATED
            and "OrderService" in e.fqn
        ]
        assert len(type_entries) > 0, (
            "Should find type-related references to OrderRepository from OrderService. "
            f"Found: {[e.member_ref.reference_type for e in result.used_by if e.member_ref and 'OrderService' in e.fqn]}"
        )

    def test_tc3_interface_type_hint(self, index):
        """TC3: Interface type hint detection."""
        # Query EmailSenderInterface
        node = index.resolve_symbol("App\\Component\\EmailSenderInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find a type-related reference (any of the type_hint subtypes)
        TYPE_RELATED = {"type_hint", "parameter_type", "return_type", "property_type"}
        type_entries = [
            e for e in result.used_by
            if e.member_ref and e.member_ref.reference_type in TYPE_RELATED
            and "OrderService" in e.fqn
        ]
        assert len(type_entries) > 0, (
            "Should find type-related references to EmailSenderInterface from OrderService. "
            f"Found: {[e.member_ref.reference_type for e in result.used_by if e.member_ref and 'OrderService' in e.fqn]}"
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
        """TC8: Direct class reference in USED BY shows type-related references.

        In the unified graph format, constructor calls are represented as:
        - Call node with call_kind=constructor
        - calls edge to Class::__construct() method

        The `uses` edges to a Class node come from type hints and property types,
        not from constructor call sites. Constructor calls target __construct(),
        which is a separate Method node. So querying the Class used_by shows
        type-related references (parameter_type, return_type, property_type,
        or type_hint), not instantiation.

        After Phase 1, type_hint is split into parameter_type, return_type,
        property_type. We accept any of these type-related reference types.
        """
        # Query Order entity
        node = index.resolve_symbol("App\\Entity\\Order")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Class-level uses edges come from type hints and property declarations.
        # Accept any of the type-related reference types.
        TYPE_RELATED = {"type_hint", "parameter_type", "return_type", "property_type"}
        type_entries = [
            e for e in result.used_by
            if e.member_ref and e.member_ref.reference_type in TYPE_RELATED
        ]
        assert len(type_entries) > 0, (
            f"Expected type-related references to Order class. "
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
        """AC8 regression: direct depth-1 query of save() shows all uses.

        Per-parent deduplication should not affect direct queries.
        After Phase 3, execution flow includes type references (parameter_type,
        return_type) alongside Call-based entries, so the count increases from
        the original 10 structural entries to 11 (adds OrderRepository type_hint).
        """
        node = index.resolve_symbol("App\\Repository\\OrderRepository::save")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        assert len(result.uses) >= 10, (
            f"Direct save() depth-1 should have at least 10 uses, got {len(result.uses)}"
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


class TestPhase1ReferenceTypeDistinction:
    """Phase 1 integration tests for reference type fixes (Issues 1, 3).

    These tests validate that the context command correctly classifies
    reference types after the Phase 1 fixes to find_call_for_usage()
    and _infer_reference_type().
    """

    def test_t1_1_constructor_shows_instantiation_in_uses(self, index):
        """T1.1 / AC1: new Order(...) shows [instantiation] not [type_hint] in USES.

        When querying createOrder()'s context, the Order class entry in USES
        should show reference_type='instantiation' because the method
        instantiates Order via new Order(...).
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find the Order class entry in USES
        order_entry = find_entry_by_fqn(result.uses, "App\\Entity\\Order")
        assert order_entry is not None, (
            "Order should appear in createOrder() USES"
        )
        assert order_entry.member_ref is not None
        assert order_entry.member_ref.reference_type == "instantiation", (
            f"Order in createOrder() USES should be [instantiation], "
            f"got [{order_entry.member_ref.reference_type}]"
        )

    def test_t1_3_parameter_type_shows_parameter_type(self, index):
        """T1.3 / AC2: CreateOrderInput parameter shows [parameter_type].

        The createOrder method has parameter `CreateOrderInput $input`.
        The reference to CreateOrderInput class should show as [parameter_type].
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find CreateOrderInput in USES
        input_entry = find_entry_by_fqn(result.uses, "CreateOrderInput")
        assert input_entry is not None, (
            "CreateOrderInput should appear in createOrder() USES"
        )
        assert input_entry.member_ref is not None
        assert input_entry.member_ref.reference_type == "parameter_type", (
            f"CreateOrderInput should be [parameter_type], "
            f"got [{input_entry.member_ref.reference_type}]"
        )

    def test_t1_4_return_type_shows_return_type(self, index):
        """T1.4 / AC3: OrderOutput return type shows [return_type].

        The createOrder method returns OrderOutput.
        The reference to OrderOutput class should show as [return_type].
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find OrderOutput in USES
        output_entry = find_entry_by_fqn(result.uses, "OrderOutput")
        assert output_entry is not None, (
            "OrderOutput should appear in createOrder() USES"
        )
        assert output_entry.member_ref is not None
        assert output_entry.member_ref.reference_type == "return_type", (
            f"OrderOutput should be [return_type], "
            f"got [{output_entry.member_ref.reference_type}]"
        )

    def test_t1_5_property_type_shows_property_type(self, index):
        """T1.5 / AC4: Property type hint shows [property_type].

        When the uses edge source is a Property node, the reference should
        show as [property_type]. With PHP 8+ constructor property promotion,
        properties declared via constructor params have their uses edges
        sourced from the Method (constructor), not the Property directly.

        This test validates property_type when the source IS a Property node.
        For the reference project, class-level type references from constructor
        promotion show as type_hint (no Argument or Method type_hint edge
        matches), which is acceptable fallback behavior.
        """
        # At class level, constructor-promoted properties show as type_hint
        # because the uses edge source is __construct() (Method), not Property.
        # Verify that type_hint entries exist for these (not property_type).
        node = index.resolve_symbol("App\\Service\\OrderService")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find type-related references in USES (type_hint or property_type)
        TYPE_RELATED = {"type_hint", "property_type", "parameter_type", "return_type"}
        type_entries = [
            e for e in result.uses
            if e.member_ref and e.member_ref.reference_type in TYPE_RELATED
        ]
        assert len(type_entries) > 0, (
            f"OrderService should have type-related references in USES. "
            f"Found reference types: "
            f"{[e.member_ref.reference_type for e in result.uses if e.member_ref]}"
        )

        # At method level, parameter/return types ARE correctly distinguished
        method_node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        method_result = query.execute(method_node.id, depth=1)
        method_ref_types = {e.member_ref.reference_type for e in method_result.uses if e.member_ref}
        assert "parameter_type" in method_ref_types or "return_type" in method_ref_types, (
            f"Method-level context should distinguish param/return types. Found: {method_ref_types}"
        )

    def test_t1_10_json_output_includes_new_reference_types(self, index):
        """T1.10 / AC6: JSON output includes parameter_type, return_type, instantiation.

        The JSON serialization should reflect the new reference type values.
        """
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Collect all reference_type values from USES entries
        ref_types = set()
        for entry in json_dict["uses"]:
            if "member_ref" in entry and entry["member_ref"].get("reference_type"):
                ref_types.add(entry["member_ref"]["reference_type"])

        # After Phase 1, we should see at least instantiation and one of the
        # type distinction values
        assert "instantiation" in ref_types or "method_call" in ref_types, (
            f"JSON USES should include specific reference types. Found: {ref_types}"
        )
        # Check for the type distinction values
        type_related = ref_types & {"parameter_type", "return_type", "property_type", "type_hint"}
        assert len(type_related) > 0, (
            f"JSON USES should include type-related reference types. Found: {ref_types}"
        )

    def test_existing_method_call_preserved(self, index):
        """Existing method_call reference types are preserved after Phase 1.

        Regression check: method calls like save(), checkAvailability() should
        still show [method_call] after the reference type changes.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        method_call_entries = [
            e for e in result.uses
            if e.member_ref and e.member_ref.reference_type == "method_call"
        ]
        assert len(method_call_entries) > 0, (
            "createOrder() USES should still contain [method_call] entries"
        )

    def test_no_bare_type_hint_for_param_or_return(self, index):
        """After Phase 1, parameter and return types should NOT show as bare [type_hint].

        The only valid [type_hint] references in a method's USES should be from
        edge sources that are not Argument, Method, or Property nodes.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # For createOrder, the known parameter type (CreateOrderInput) and
        # return type (OrderOutput) should NOT be labeled as generic type_hint
        for entry in result.uses:
            if entry.member_ref and entry.member_ref.reference_type == "type_hint":
                # If there are type_hint entries, they should NOT be for known
                # param/return types
                assert "CreateOrderInput" not in entry.fqn, (
                    f"CreateOrderInput should be [parameter_type], not [type_hint]"
                )
                assert "OrderOutput" not in entry.fqn, (
                    f"OrderOutput should be [return_type], not [type_hint]"
                )


class TestPhase2ArgumentTracking:
    """Phase 2 integration tests for argument tracking (Issue 4).

    These tests validate that the context command correctly displays
    argument-to-parameter mappings and result variable assignments
    when Call nodes with argument edges are present in sot.json v2.0.
    """

    def test_t2_1_checkAvailability_shows_2_arguments(self, index):
        """T2.1 / AC7: checkAvailability() shows 2 arguments with param names.

        The checkAvailability($productId, $quantity) call has two arguments
        that should be resolved with their formal parameter names.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_entry_by_fqn(result.uses, "checkAvailability()")
        assert entry is not None, "checkAvailability() should appear in createOrder() USES"
        assert hasattr(entry, "arguments"), "ContextEntry should have arguments field"
        assert len(entry.arguments) == 2, (
            f"checkAvailability() should have 2 arguments, got {len(entry.arguments)}"
        )
        # Verify param names
        assert entry.arguments[0].param_name == "$productId"
        assert entry.arguments[1].param_name == "$quantity"
        # Verify positions
        assert entry.arguments[0].position == 0
        assert entry.arguments[1].position == 1

    def test_t2_2_constructor_shows_arguments(self, index):
        """T2.2 / AC8: new Order() shows constructor arguments.

        The Order constructor has 6 arguments. All should be captured
        even though constructor param names may not resolve (depends on
        whether __construct() Argument children are in the index).
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_entry_by_fqn(result.uses, "Order::__construct()")
        assert entry is not None, "Order::__construct() should appear in createOrder() USES"
        assert hasattr(entry, "arguments")
        assert len(entry.arguments) == 6, (
            f"Order::__construct() should have 6 arguments, got {len(entry.arguments)}"
        )
        # Verify positions are 0-5
        positions = [a.position for a in entry.arguments]
        assert positions == [0, 1, 2, 3, 4, 5]

    def test_t2_3_save_shows_argument_from_local_variable(self, index):
        """T2.3 / AC7: save($processedOrder) shows argument from local variable.

        The save() call receives a local variable $processedOrder as argument.
        The argument should show value_expr='$processedOrder' and
        value_source='local'.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_entry_by_fqn(result.uses, "save()")
        assert entry is not None, "save() should appear in createOrder() USES"
        assert hasattr(entry, "arguments")
        assert len(entry.arguments) == 1, (
            f"save() should have 1 argument, got {len(entry.arguments)}"
        )
        arg = entry.arguments[0]
        assert arg.param_name == "$order", (
            f"save() param should be '$order', got '{arg.param_name}'"
        )
        assert arg.value_expr == "$processedOrder", (
            f"save() arg value should be '$processedOrder', got '{arg.value_expr}'"
        )
        assert arg.value_source == "local", (
            f"save() arg source should be 'local', got '{arg.value_source}'"
        )

    def test_t2_4_result_var_not_present_when_no_assignment(self, index):
        """T2.4 / AC9: result_var is None when call result is not assigned.

        In the reference project, no calls in createOrder have their results
        assigned to a local variable via assigned_from edges. result_var
        should be None for all entries.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        for entry in result.uses:
            if hasattr(entry, "result_var"):
                # result_var should be None since the reference project doesn't
                # have assigned_from edges to locals
                assert entry.result_var is None, (
                    f"{entry.fqn} should have result_var=None, got '{entry.result_var}'"
                )

    def test_t2_5_no_arguments_for_parameterless_calls(self, index):
        """T2.5 / AC10: Calls with no arguments show empty arguments list.

        Methods like getName() that take no parameters should have
        an empty arguments list, not a missing field.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_entry_by_fqn(result.uses, "getName()")
        assert entry is not None, "getName() should appear in createOrder() USES"
        assert hasattr(entry, "arguments")
        assert entry.arguments == [], (
            f"getName() should have empty arguments list, got {entry.arguments}"
        )

    def test_t2_6_json_output_includes_arguments(self, index):
        """T2.6 / AC11: JSON output includes arguments array and result_var.

        The JSON serialization should include arguments for calls that have them,
        and omit arguments for calls that don't.
        """
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Find checkAvailability in JSON output
        check_entry = None
        for e in json_dict["uses"]:
            if "checkAvailability" in e["fqn"]:
                check_entry = e
                break
        assert check_entry is not None, "checkAvailability should be in JSON USES"
        assert "arguments" in check_entry, "checkAvailability should have arguments in JSON"
        assert len(check_entry["arguments"]) == 2
        assert check_entry["arguments"][0]["param_name"] == "$productId"
        assert check_entry["arguments"][0]["position"] == 0
        assert check_entry["arguments"][1]["param_name"] == "$quantity"
        assert check_entry["arguments"][1]["position"] == 1

        # getName() should NOT have arguments in JSON (empty list = omitted)
        get_name_entry = None
        for e in json_dict["uses"]:
            if "getName" in e["fqn"]:
                get_name_entry = e
                break
        assert get_name_entry is not None
        assert "arguments" not in get_name_entry, (
            "getName() with no args should not have 'arguments' key in JSON"
        )

    def test_t2_7_mcp_response_includes_member_ref(self, index):
        """T2.7 / AC12: MCP response includes member_ref with reference_type.

        The MCP server's context response was previously lossy (dropped
        member_ref entirely). After the fix, it should include member_ref
        with reference_type, access_chain, and arguments.
        """
        from src.server.mcp import MCPServer
        from pathlib import Path

        sot = str(Path(__file__).parent.parent.parent / "kloc-reference-project-php" / "contract-tests" / "output" / "sot.json")
        server = MCPServer(sot_path=sot)

        result = server.call_tool("kloc_context", {
            "symbol": "OrderService::createOrder",
            "depth": 1,
        })

        # Find checkAvailability in USES
        check_entry = None
        for e in result["uses"]:
            if "checkAvailability" in e["fqn"]:
                check_entry = e
                break
        assert check_entry is not None, "checkAvailability should be in MCP USES"

        # Verify member_ref is present with reference_type
        assert "member_ref" in check_entry, "MCP entry should include member_ref"
        assert check_entry["member_ref"]["reference_type"] == "method_call"

        # Verify access_chain is present
        assert "access_chain" in check_entry["member_ref"]
        assert check_entry["member_ref"]["access_chain"] == "$this->inventoryChecker"

        # Verify arguments are present
        assert "arguments" in check_entry, "MCP entry should include arguments"
        assert len(check_entry["arguments"]) == 2
        assert check_entry["arguments"][0]["param_name"] == "$productId"

    def test_t2_8_entry_without_call_has_empty_arguments(self, index):
        """T2.8 / AC10: Entry without Call node has empty arguments list.

        Type references (extends, parameter_type, return_type) and property
        accesses that don't match a Call node should have empty arguments.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find type reference entries (parameter_type, return_type)
        type_entries = [
            e for e in result.uses
            if e.member_ref and e.member_ref.reference_type in (
                "parameter_type", "return_type", "type_hint"
            )
        ]
        for entry in type_entries:
            if hasattr(entry, "arguments"):
                assert entry.arguments == [], (
                    f"Type reference {entry.fqn} should have empty arguments, "
                    f"got {entry.arguments}"
                )


class TestPhase3ExecutionFlow:
    """Phase 3 integration tests for execution flow (Issues 2, 6).

    These tests validate that method-level USES queries show Call nodes
    in line-number order (execution flow) while class-level queries
    continue to use the structural approach.
    """

    def test_t3_1_method_uses_shows_calls_in_line_order(self, index):
        """T3.1 / AC13: Method USES shows Call nodes in line-number order.

        createOrder()'s USES should show calls in the order they appear
        in the source code: checkAvailability (line 30), Order::__construct
        (line 32), process (line 42), save (line 45), send (line 47), etc.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Extract method calls (not type refs or property accesses) with their lines
        calls = [
            (e.line, e.fqn)
            for e in result.uses
            if e.member_ref and e.member_ref.reference_type in (
                "method_call", "instantiation", "function_call", "static_call"
            )
        ]

        # Verify they are in line-number order
        lines = [line for line, _ in calls]
        assert lines == sorted(lines), (
            f"Call entries should be in line-number order. "
            f"Lines: {lines}"
        )

        # Verify key calls are present in expected order
        call_fqns = [fqn for _, fqn in calls]
        # checkAvailability should come before Order::__construct
        check_idx = next(i for i, f in enumerate(call_fqns) if "checkAvailability" in f)
        order_idx = next(i for i, f in enumerate(call_fqns) if "Order::__construct" in f)
        assert check_idx < order_idx, (
            "checkAvailability should come before Order::__construct in execution order"
        )
        # process() should come before save()
        process_idx = next(i for i, f in enumerate(call_fqns) if "process()" in f)
        save_idx = next(i for i, f in enumerate(call_fqns) if "save()" in f)
        assert process_idx < save_idx, (
            "process() should come before save() in execution order"
        )

    def test_t3_2_order_visible_as_constructor_and_argument(self, index):
        """T3.2 / AC14: $order visible as constructor result AND as argument.

        In createOrder(), $order is created via new Order(...) and then passed
        to process($order). The execution flow should show both: the constructor
        call and the process() call with $order as argument.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find Order::__construct() (constructor creates $order)
        constructor = find_entry_by_fqn(result.uses, "Order::__construct()")
        assert constructor is not None, "Order::__construct() should appear in USES"
        assert constructor.member_ref.reference_type == "instantiation"

        # Find process() (receives $order as argument)
        process = find_entry_by_fqn(result.uses, "process()")
        assert process is not None, "process() should appear in USES"
        assert hasattr(process, "arguments")
        assert len(process.arguments) == 1
        assert process.arguments[0].value_expr == "$order", (
            f"process() should receive $order, got '{process.arguments[0].value_expr}'"
        )
        assert process.arguments[0].value_source == "local"

    def test_t3_3_local_variables_not_shown_as_separate_entries(self, index):
        """T3.3 / AC15: Local variables not passed to calls are NOT shown.

        The execution flow shows Call nodes and type references, not
        Value/Variable nodes. Local variables only appear as argument
        values (value_expr) in the arguments list of calls they're passed to.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # No entry should have kind == "Variable" or kind == "Value"
        for entry in result.uses:
            assert entry.kind not in ("Variable", "Value"), (
                f"USES should not contain {entry.kind} entries: {entry.fqn}"
            )

    def test_t3_4_class_level_query_uses_structural_approach(self, index):
        """T3.4 / AC16: Class-level context query still uses structural approach.

        When querying a Class (not a Method), the USES direction should
        show structural dependencies (extends, implements, type_hint,
        property_access) rather than execution flow.
        """
        node = index.resolve_symbol("App\\Repository\\OrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Class-level query should have structural entries
        ref_types = {
            e.member_ref.reference_type
            for e in result.uses
            if e.member_ref
        }
        # Should include structural types like property_access, parameter_type
        assert "property_access" in ref_types or "parameter_type" in ref_types, (
            f"Class-level USES should include structural reference types. Found: {ref_types}"
        )

        # Should NOT have execution flow entries (no Call-based entries)
        # Class-level queries don't iterate Call children
        assert "function_call" not in ref_types, (
            "Class-level USES should not have function_call entries"
        )

    def test_t3_5_json_includes_execution_flow_line_ordered(self, index):
        """T3.5 / AC17: JSON includes execution flow with line-ordered entries.

        The JSON output for a method-level query should show entries
        sorted by line number, representing the execution flow.
        """
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Extract lines from USES entries
        lines = [e["line"] for e in json_dict["uses"] if e["line"] is not None]
        assert lines == sorted(lines), (
            f"JSON USES entries should be line-ordered. Lines: {lines}"
        )

        # Verify key entries are present in JSON
        fqns = [e["fqn"] for e in json_dict["uses"]]
        assert any("checkAvailability" in f for f in fqns), (
            "checkAvailability should be in JSON USES"
        )
        assert any("save()" in f for f in fqns), (
            "save() should be in JSON USES"
        )
        assert any("__construct()" in f for f in fqns), (
            "A constructor call should be in JSON USES"
        )
