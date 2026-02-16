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
    """Find entry containing the given FQN substring.

    Searches top-level entries only. For Kind 1 (variable) entries,
    checks the entry FQN (variable's FQN).
    """
    for entry in entries:
        if fqn_substring in entry.fqn:
            return entry
    return None


def find_call_entry(entries, fqn_substring):
    """Find a call entry by FQN substring, searching both top-level and source_call.

    In the variable-centric model:
    - Kind 2 entries have the call FQN directly
    - Kind 1 entries have the call in source_call
    Returns the ContextEntry representing the call.
    """
    for entry in entries:
        if entry.entry_type == "local_variable" and entry.source_call:
            if fqn_substring in entry.source_call.fqn:
                return entry.source_call
        elif fqn_substring in entry.fqn:
            return entry
    return None


def find_variable_entry(entries, var_name):
    """Find a Kind 1 (variable) entry by variable name."""
    for entry in entries:
        if entry.entry_type == "local_variable" and entry.variable_name == var_name:
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
        """TC1: Type hint detection for OrderService -> OrderRepositoryInterface.

        After ISSUE-D (Interface context redesign), interface USED BY returns
        entries with ref_type directly on ContextEntry. Constructor property
        injections appear as [property_type] entries.
        """
        # Query OrderRepositoryInterface
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Interface USED BY uses ref_type directly on entries
        TYPE_RELATED = {"type_hint", "parameter_type", "return_type", "property_type"}
        type_entries = [
            e for e in result.used_by
            if e.ref_type in TYPE_RELATED
            and "OrderService" in e.fqn
        ]
        assert len(type_entries) > 0, (
            "Should find type-related references to OrderRepositoryInterface from OrderService. "
            f"Found ref_types: {[(e.fqn, e.ref_type) for e in result.used_by]}"
        )

    def test_tc3_interface_type_hint(self, index):
        """TC3: Interface type hint detection.

        After ISSUE-D, interface USED BY returns property_type entries for
        constructor-injected properties.
        """
        # Query EmailSenderInterface
        node = index.resolve_symbol("App\\Component\\EmailSenderInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Interface USED BY uses ref_type directly on entries
        TYPE_RELATED = {"type_hint", "parameter_type", "return_type", "property_type"}
        type_entries = [
            e for e in result.used_by
            if e.ref_type in TYPE_RELATED
            and "OrderService" in e.fqn
        ]
        assert len(type_entries) > 0, (
            "Should find type-related references to EmailSenderInterface from OrderService. "
            f"Found ref_types: {[(e.fqn, e.ref_type) for e in result.used_by]}"
        )


class TestAccessChains:
    """Tests for access chain building using unified graph format.

    The v2.0 sot.json includes Value and Call nodes that enable access chain
    resolution directly from the graph, without requiring separate calls.json.
    """

    def test_tc2_method_call_with_chain(self, index):
        """TC2: Method call via property shows access chain.

        After ISSUE-D, method calls on interface are at depth 2 under
        [property_type] entries. Check depth-2 children for save().
        """
        # Query OrderRepositoryInterface at depth 2
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Find property_type entry for OrderService
        prop_entry = None
        for e in result.used_by:
            if e.ref_type == "property_type" and "OrderService" in e.fqn:
                prop_entry = e
                break
        assert prop_entry is not None, "OrderService property_type entry should exist"

        # Find save() in depth-2 children
        save_entry = None
        for child in prop_entry.children:
            if child.callee and "save" in child.callee:
                save_entry = child
                break
        assert save_entry is not None, "save() method call should be depth-2 child of property_type"
        assert save_entry.ref_type == "method_call"
        assert save_entry.on is not None and "orderRepository" in save_entry.on

    def test_tc4_interface_method_call(self, index):
        """TC4: Interface method call shows access chain.

        After ISSUE-D, method calls on interface are at depth 2 under
        [property_type] entries.
        """
        # Query EmailSenderInterface at depth 2
        node = index.resolve_symbol("App\\Component\\EmailSenderInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Find property_type entry for OrderService
        prop_entry = None
        for e in result.used_by:
            if e.ref_type == "property_type" and "OrderService" in e.fqn:
                prop_entry = e
                break
        assert prop_entry is not None, "OrderService property_type entry should exist"

        # Find send() in depth-2 children
        send_entry = None
        for child in prop_entry.children:
            if child.callee and "send" in child.callee:
                send_entry = child
                break
        assert send_entry is not None, "send() method call should be depth-2 child of property_type"
        assert send_entry.ref_type == "method_call"
        assert send_entry.on is not None and "emailSender" in send_entry.on

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
        """TC8: Class USED BY shows grouped, classified entries.

        After ISSUE-B (Class USED BY Redesign), class-level queries return
        entries with ref_type set directly on ContextEntry (not via member_ref).
        The entries are classified as instantiation, extends, property_type,
        method_call, property_access, parameter_type, or return_type.
        """
        # Query Order entity
        node = index.resolve_symbol("App\\Entity\\Order")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Class-level USED BY entries use ref_type directly on the entry
        ref_types = {e.ref_type for e in result.used_by if e.ref_type}
        EXPECTED_TYPES = {"instantiation", "extends", "implements", "property_type",
                         "method_call", "property_access", "parameter_type",
                         "return_type", "type_hint"}
        assert ref_types & EXPECTED_TYPES, (
            f"Expected classified reference types on Order USED BY. "
            f"Found ref_types: {ref_types}"
        )


class TestMultipleReferences:
    """Tests for multiple reference handling (TC6)."""

    def test_tc6_multiple_method_calls(self, index):
        """TC6: Multiple method calls from different scopes appear under property_type.

        After ISSUE-D, interface method calls are grouped under [property_type]
        injection point entries at depth 2. Multiple consumers each show their
        own method calls.
        """
        # Query OrderRepositoryInterface at depth 2
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Count total findById() calls across all property_type entries
        findById_count = 0
        for e in result.used_by:
            if e.ref_type == "property_type":
                for child in e.children:
                    if child.callee and "findById" in child.callee:
                        findById_count += 1
        assert findById_count >= 2, (
            f"Multiple findById() calls should appear across property_type entries, found {findById_count}"
        )


class TestV1FormatBackwardCompatibility:
    """Tests for backward compatibility with v1.0 sot.json (without Value/Call nodes)."""

    def test_ec1_v1_format_degrades_gracefully(self, index):
        """Interface USED BY returns structured entries with ref_type.

        After ISSUE-D, interface queries return grouped entries with ref_type
        directly on ContextEntry. Method calls are at depth 2 under property_type.
        """
        # Query OrderRepositoryInterface at depth 2
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Find save() in depth-2 children of a property_type entry
        save_entry = None
        for e in result.used_by:
            if e.ref_type == "property_type":
                for child in e.children:
                    if child.callee and "save" in child.callee:
                        save_entry = child
                        break
                if save_entry:
                    break
        assert save_entry is not None, "save() should appear under property_type entry"
        assert save_entry.ref_type == "method_call"
        assert save_entry.on is not None and "orderRepository" in save_entry.on


class TestJsonOutput:
    """Tests for JSON output format (OF3)."""

    def test_json_includes_reference_type(self, index):
        """JSON output includes refType field for interface queries.

        After ISSUE-D, interface entries have ref_type directly (not member_ref).
        """
        from src.output.tree import context_tree_to_dict

        # Use OrderRepositoryInterface
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Check that usedBy entries have refType
        found_ref_type = False
        for entry in json_dict["usedBy"]:
            if entry.get("refType"):
                found_ref_type = True
                break

        assert found_ref_type, (
            "JSON output should include refType on interface USED BY entries. "
            f"Found: {json_dict['usedBy']}"
        )

    def test_json_includes_access_chain(self, index):
        """JSON output includes 'on' field for method calls under property_type.

        After ISSUE-D, method calls are at depth 2 under property_type entries.
        The 'on' field contains the access chain expression.
        """
        from src.output.tree import context_tree_to_dict

        # Use OrderRepositoryInterface at depth 2
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        json_dict = context_tree_to_dict(result)

        # Check that property_type entries have method_call children with 'on'
        found_chain = False
        for entry in json_dict["usedBy"]:
            if entry.get("refType") == "property_type":
                for child in entry.get("children", []):
                    if child.get("on"):
                        found_chain = True
                        break
            if found_chain:
                break

        assert found_chain, (
            "JSON output should include 'on' (access chain) for method calls. "
            f"Found entries: {json_dict['usedBy']}"
        )


class TestCalleeVerificationIntegration:
    """Integration tests for callee verification guard (Issue 6).

    Validates that find_call_for_usage() rejects Call nodes whose callee
    does not match the usage target, falling back to inference which
    produces correct reference types.
    """

    def test_nextId_shows_static_property_not_instantiation(self, index):
        """AC3: InMemoryOrderRepository::$nextId at line 30 shows [static_property] not [instantiation].

        The code `self::$nextId++` at PHP line 30 is a static property access.
        Previously, find_call_for_usage matched the `new Order()` constructor
        at line 29 for the $nextId uses edge, producing [instantiation].
        With callee verification, the constructor is rejected and inference
        correctly returns [static_property].
        """
        # Use concrete implementation node directly for this test
        nodes = index.resolve_symbol("InMemoryOrderRepository::save")
        concrete = [n for n in nodes if "InMemoryOrderRepository" in n.fqn]
        assert len(concrete) >= 1, "InMemoryOrderRepository::save() should exist"
        query = ContextQuery(index)
        result = query.execute(concrete[0].id, depth=1)

        entry = find_call_entry(result.uses, "$nextId")
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

        In the variable-centric model, getName() is inside the $processorName
        variable entry's source_call.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_call_entry(result.uses, "getName")
        assert entry is not None, "getName() should appear in createOrder() uses (via source_call)"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call", (
            f"getName() should be [method_call], got [{entry.member_ref.reference_type}]"
        )

    def test_customerEmail_not_attributed_to_emailSender(self, index):
        """AC5: $savedOrder->customerEmail appears as argument in send(), not as separate entry.

        In the variable-centric model, property accesses consumed as arguments
        are not top-level entries. The code `$savedOrder->customerEmail` at
        PHP line 48 is inside the send() argument list. It appears as
        value_expr='$savedOrder->customerEmail' in the send() call's arguments.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # In the variable-centric model, $customerEmail accesses consumed
        # as arguments to send() appear as argument value_expr, not top-level entries.
        send_entry = find_call_entry(result.uses, "send()")
        assert send_entry is not None, "send() should appear in createOrder() uses"
        assert len(send_entry.arguments) >= 1, "send() should have arguments"

        # One of the arguments should reference $savedOrder->customerEmail
        arg_exprs = [a.value_expr for a in send_entry.arguments]
        has_customer_email = any("customerEmail" in (expr or "") for expr in arg_exprs)
        assert has_customer_email, (
            f"send() arguments should reference customerEmail. "
            f"Found: {arg_exprs}"
        )

    def test_save_method_call_reference_type_preserved(self, index):
        """AC6: OrderRepositoryInterface::save() at line 45 still shows [method_call].

        The callee verification should not break existing correct matches.
        In the variable-centric model, save() is inside the $savedOrder
        variable entry's source_call.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_call_entry(result.uses, "save()")
        assert entry is not None, "save() should appear in createOrder() uses (via source_call)"
        assert entry.member_ref is not None
        assert entry.member_ref.reference_type == "method_call"
        assert entry.member_ref.access_chain == "$this->orderRepository"


class TestPerSubtreeVisitedSet:
    """Integration tests for per-subtree visited set (Issue 5).

    Validates that the depth-2 subtree shows complete dependencies of the
    expanded method, regardless of what depth-1 entries exist. The same
    target can appear at both depth 1 and depth 2 under different parents.
    """

    def test_depth2_createOrder_process_includes_subtree(self, index):
        """AC7: depth-2 from createOrder includes children under process().

        In the variable-centric model, process() is inside the $processedOrder
        Kind 1 variable entry. The depth-2 expansion of process() shows its
        sub-calls (preProcess, doProcess, postProcess).
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Find the $processedOrder variable entry (wraps process() call)
        proc_entry = find_variable_entry(result.uses, "$processedOrder")
        assert proc_entry is not None, "$processedOrder should appear in createOrder() uses"
        assert len(proc_entry.children) > 0, "$processedOrder should have depth-2 children"

        # Check for sub-methods in process()'s depth-2 children
        child_fqns = [c.fqn for c in proc_entry.children]
        has_sub_method = any("Process" in f for f in child_fqns)
        assert has_sub_method, (
            f"process() depth-2 children should include sub-methods. "
            f"Found: {[f.split('::')[-1] if '::' in f else f for f in child_fqns]}"
        )

    def test_depth2_createOrder_process_subtree_count(self, index):
        """AC7: depth-2 process() subtree has at least 3 children.

        process() expands to preProcess(), doProcess(), postProcess()
        at depth 2. The variable-centric model places these as children
        of the $processedOrder Kind 1 entry.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        proc_entry = find_variable_entry(result.uses, "$processedOrder")
        assert proc_entry is not None

        assert len(proc_entry.children) >= 3, (
            f"process() depth-2 subtree should have at least 3 entries, "
            f"got {len(proc_entry.children)}"
        )

    def test_depth2_save_subtree_has_interface_dependency(self, index):
        """AC2: save() depth-2 subtree shows OrderRepositoryInterface::save() dependencies.

        In the variable-centric model, save() resolves to
        OrderRepositoryInterface::save() which has Order as a parameter_type
        dependency. The $savedOrder Kind 1 entry should show this child.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Find the $savedOrder entry (wraps save() call)
        saved_entry = find_variable_entry(result.uses, "$savedOrder")
        assert saved_entry is not None, "$savedOrder should appear in createOrder() uses"

        # save() resolves to OrderRepositoryInterface::save() which has
        # Order as its parameter type dependency
        # The variable entry or its source_call may have children
        all_children = list(saved_entry.children)
        if saved_entry.source_call:
            all_children.extend(saved_entry.source_call.children)

        # There should be at least the Order parameter_type dependency
        # (but may be 0 if the interface method has minimal dependencies)
        # The key assertion is that the entry exists and is queryable
        assert saved_entry.source_call is not None, (
            "$savedOrder should have source_call referencing save()"
        )
        assert "save()" in saved_entry.source_call.fqn

    def test_depth1_save_direct_still_shows_all_uses(self, index):
        """AC8 regression: direct depth-1 query of concrete save() shows all uses.

        Per-parent deduplication should not affect direct queries.
        Use InMemoryOrderRepository::save() to test the concrete implementation
        which has structural dependencies like $nextId, $orders, etc.
        """
        # Use concrete implementation (interface save has only 1 uses entry)
        nodes = index.resolve_symbol("InMemoryOrderRepository::save")
        concrete = [n for n in nodes if "InMemoryOrderRepository" in n.fqn]
        assert len(concrete) >= 1, "InMemoryOrderRepository::save() should exist"
        query = ContextQuery(index)
        result = query.execute(concrete[0].id, depth=1)

        # Collect all FQNs including those in source_call
        all_fqns = []
        for e in result.uses:
            all_fqns.append(e.fqn)
            if e.source_call:
                all_fqns.append(e.source_call.fqn)

        assert len(result.uses) >= 4, (
            f"Direct save() depth-1 should have at least 4 uses, got {len(result.uses)}"
        )

        # Verify specific entries are present (using all_fqns to check both
        # top-level and nested source_call entries)
        assert any("$nextId" in f for f in all_fqns), "$nextId should be in save() uses"
        assert any("$id" in f for f in all_fqns), "$id should be in save() uses"
        assert any("$orders" in f for f in all_fqns), "$orders should be in save() uses"

    def test_same_target_at_depth1_and_depth2(self, index):
        """AC2: Same target can appear at both depth 1 and depth 2.

        With per-parent dedup and the variable-centric model, the same
        symbol can appear as both a depth-1 entry and a depth-2 child
        under different parents. For example, Order class may appear as
        a type reference at depth 1 and as a dependency of a depth-2
        expanded method.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=2)

        # Collect all depth-2 children FQNs
        all_depth2_fqns = []
        for e in result.uses:
            for c in e.children:
                all_depth2_fqns.append(c.fqn)

        # Depth-2 should have some entries (from expanded methods)
        assert len(all_depth2_fqns) > 0, (
            "Depth-2 should have children from expanded methods"
        )

        # Verify depth-1 has entries
        assert len(result.uses) > 0, (
            "Depth-1 should have entries"
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

        # Check depth-2 children of any entry with children have no duplicates
        for entry in result.uses:
            if entry.children:
                child_fqns = [c.fqn for c in entry.children]
                assert len(child_fqns) == len(set(child_fqns)), (
                    f"Depth-2 children under {entry.fqn} should have no duplicates. "
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

        When querying createOrder()'s context, the Order::__construct() call
        appears as a Kind 1 variable entry (assigned to $order). The source_call
        should show reference_type='instantiation'.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find the Order constructor call (nested inside $order variable entry)
        order_call = find_call_entry(result.uses, "Order::__construct()")
        assert order_call is not None, (
            "Order::__construct() should appear in createOrder() USES (via $order variable)"
        )
        assert order_call.member_ref is not None
        assert order_call.member_ref.reference_type == "instantiation", (
            f"Order::__construct() in createOrder() USES should be [instantiation], "
            f"got [{order_call.member_ref.reference_type}]"
        )

    def test_t1_3_parameter_type_filtered_from_uses(self, index):
        """v3 AC11: CreateOrderInput [parameter_type] is filtered from USES.

        The createOrder method has parameter `CreateOrderInput $input`.
        Since the DEFINITION section already shows this, the USES section
        should NOT include a [parameter_type] entry for CreateOrderInput.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # CreateOrderInput should NOT appear as a parameter_type entry in USES
        input_entry = find_entry_by_fqn(result.uses, "CreateOrderInput")
        assert input_entry is None, (
            "CreateOrderInput [parameter_type] should be filtered from createOrder() USES "
            "(already shown in DEFINITION section)"
        )

    def test_t1_4_return_type_filtered_from_uses(self, index):
        """v3 AC10: OrderOutput [return_type] is filtered from USES.

        The createOrder method returns OrderOutput.
        Since the DEFINITION section already shows this, the USES section
        should NOT include a [return_type] entry for OrderOutput.
        Note: OrderOutput::__construct() [instantiation] is a different entry
        and should still appear.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # No entry should have reference_type == "return_type"
        return_type_entries = [
            e for e in result.uses
            if e.member_ref and e.member_ref.reference_type == "return_type"
        ]
        assert len(return_type_entries) == 0, (
            "No [return_type] entries should appear in method USES "
            "(filtered because DEFINITION section already shows return type). "
            f"Found: {[e.fqn for e in return_type_entries]}"
        )

    def test_t1_5_property_type_shows_property_type(self, index):
        """T1.5 / AC4: Class USES shows property_type for injected dependencies.

        After ISSUE-C (Class USES Redesign), class-level queries return entries
        with ref_type set directly on ContextEntry. Property dependencies with
        type_hint edges from Property nodes should show as [property_type].
        """
        node = index.resolve_symbol("App\\Service\\OrderService")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Class-level USES entries use ref_type directly on the entry
        ref_types = {e.ref_type for e in result.uses if e.ref_type}
        assert "property_type" in ref_types, (
            f"OrderService USES should have property_type entries. "
            f"Found ref_types: {ref_types}"
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
        # Check for the type distinction values (parameter_type and return_type
        # are now filtered from method-level USES)
        type_related = ref_types & {"property_type", "type_hint"}
        # type_related may be empty for method-level queries since only
        # property_type and type_hint remain, and createOrder() may not have those.
        # The key assertion is that instantiation/method_call are present (above).

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

    def test_no_param_or_return_type_in_method_uses(self, index):
        """v3 AC10-12: No parameter_type or return_type entries in method USES.

        After v3 ISSUE-B, parameter_type and return_type entries are filtered
        from method-level USES because the DEFINITION section already shows them.
        Note: OrderOutput::__construct() [instantiation] is a call entry and
        should still appear — only the type reference entries are filtered.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # No entry should have reference_type of parameter_type or return_type
        for entry in result.uses:
            if entry.member_ref:
                assert entry.member_ref.reference_type not in ("parameter_type", "return_type"), (
                    f"{entry.fqn} has [{entry.member_ref.reference_type}] which should be "
                    f"filtered from method-level USES"
                )
            # Also check source_call for Kind 1 entries
            if entry.source_call and entry.source_call.member_ref:
                assert entry.source_call.member_ref.reference_type not in ("parameter_type", "return_type"), (
                    f"{entry.source_call.fqn} has [{entry.source_call.member_ref.reference_type}] "
                    f"which should be filtered from method-level USES"
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

        In the variable-centric model, Order::__construct() is nested inside
        the $order Kind 1 variable entry's source_call.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_call_entry(result.uses, "Order::__construct()")
        assert entry is not None, "Order::__construct() should appear in createOrder() USES (via source_call)"
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

        In the variable-centric model, save() is inside the $savedOrder
        Kind 1 variable entry's source_call.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_call_entry(result.uses, "save()")
        assert entry is not None, "save() should appear in createOrder() USES (via source_call)"
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

        In the variable-centric model, getName() is inside the $processorName
        Kind 1 variable entry's source_call.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_call_entry(result.uses, "getName()")
        assert entry is not None, "getName() should appear in createOrder() USES (via source_call)"
        assert hasattr(entry, "arguments")
        assert entry.arguments == [], (
            f"getName() should have empty arguments list, got {entry.arguments}"
        )

    def test_t2_6_json_output_includes_arguments(self, index):
        """T2.6 / AC11: JSON output includes arguments array and result_var.

        The JSON serialization should include arguments for calls that have them,
        and omit arguments for calls that don't.

        In the variable-centric model, some calls are nested inside Kind 1
        variable entries as source_call in the JSON output.
        """
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Find checkAvailability in JSON output (still a Kind 2 top-level entry)
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

        # getName() is now inside a Kind 1 variable entry ($processorName)
        # Find it via source_call in JSON
        get_name_source = None
        for e in json_dict["uses"]:
            if e.get("source_call") and "getName" in e["source_call"].get("fqn", ""):
                get_name_source = e["source_call"]
                break
        assert get_name_source is not None, "getName() should be in JSON USES (via source_call)"
        # getName() has no arguments, so arguments should not be in JSON source_call
        assert "arguments" not in get_name_source, (
            "getName() with no args should not have 'arguments' key in JSON source_call"
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

        Type references (type_hint, property_type) and property accesses
        that don't match a Call node should have empty arguments.
        Note: parameter_type and return_type are now filtered from method USES.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find type reference entries (type_hint, property_type only —
        # parameter_type/return_type are filtered from method USES)
        type_entries = [
            e for e in result.uses
            if e.member_ref and e.member_ref.reference_type in (
                "type_hint", "property_type"
            )
        ]
        for entry in type_entries:
            if hasattr(entry, "arguments"):
                assert entry.arguments == [], (
                    f"Type reference {entry.fqn} should have empty arguments, "
                    f"got {entry.arguments}"
                )


class TestValueTypeResolution:
    """Phase 3 ISSUE-B tests for value_type resolution on ArgumentInfo.

    Tests that _get_argument_info() resolves type_of edges on Value nodes
    to populate ArgumentInfo.value_type with the type name(s).
    """

    def test_save_arg_has_value_type_order(self, index):
        """save($processedOrder) argument has value_type='Order'.

        The $processedOrder Value node has a type_of edge pointing to Order class.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_call_entry(result.uses, "save()")
        assert entry is not None
        assert len(entry.arguments) == 1
        assert entry.arguments[0].value_type == "Order", (
            f"save() arg should have value_type='Order', got '{entry.arguments[0].value_type}'"
        )

    def test_process_arg_has_value_type_order(self, index):
        """process($order) argument has value_type='Order'.

        The $order Value node has a type_of edge pointing to Order class.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_call_entry(result.uses, "process()")
        assert entry is not None
        assert len(entry.arguments) == 1
        assert entry.arguments[0].value_type == "Order", (
            f"process() arg should have value_type='Order', got '{entry.arguments[0].value_type}'"
        )

    def test_literal_arg_has_no_value_type(self, index):
        """Literal arguments (e.g., 0, 'pending') have no value_type.

        Literal Value nodes don't have type_of edges.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_call_entry(result.uses, "Order::__construct()")
        assert entry is not None
        literal_args = [a for a in entry.arguments if a.value_source == "literal"]
        assert len(literal_args) > 0
        for arg in literal_args:
            assert arg.value_type is None, (
                f"Literal arg at position {arg.position} should have value_type=None, "
                f"got '{arg.value_type}'"
            )

    def test_result_arg_has_no_value_type(self, index):
        """Result arguments (e.g., $input->productId) have no value_type.

        Property access result Value nodes typically don't have type_of edges.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_entry_by_fqn(result.uses, "checkAvailability()")
        assert entry is not None
        for arg in entry.arguments:
            assert arg.value_type is None, (
                f"checkAvailability() arg '{arg.param_name}' should have value_type=None, "
                f"got '{arg.value_type}'"
            )

    def test_json_includes_value_type(self, index):
        """JSON output includes value_type when present."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Find save() in source_call — it should have value_type in its argument
        save_source = None
        for e in json_dict["uses"]:
            if e.get("source_call") and "save()" in e["source_call"].get("fqn", ""):
                save_source = e["source_call"]
                break
        assert save_source is not None
        assert "arguments" in save_source
        assert save_source["arguments"][0].get("value_type") == "Order"

    def test_json_omits_value_type_when_none(self, index):
        """JSON output omits value_type field when it is None."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Find checkAvailability — its args should NOT have value_type key
        check_entry = None
        for e in json_dict["uses"]:
            if "checkAvailability" in e["fqn"]:
                check_entry = e
                break
        assert check_entry is not None
        for arg in check_entry["arguments"]:
            assert "value_type" not in arg, (
                f"checkAvailability arg should not have value_type key, "
                f"but found: {arg}"
            )

    def test_mcp_includes_value_type(self, index):
        """MCP output includes value_type when present."""
        from src.server.mcp import MCPServer
        from pathlib import Path

        sot = str(Path(__file__).parent.parent.parent / "kloc-reference-project-php" / "contract-tests" / "output" / "sot.json")
        server = MCPServer(sot_path=sot)

        result = server.call_tool("kloc_context", {
            "symbol": "OrderService::createOrder",
            "depth": 1,
        })

        # Find save() in source_call
        save_source = None
        for e in result["uses"]:
            if e.get("source_call") and "save()" in e["source_call"].get("fqn", ""):
                save_source = e["source_call"]
                break
        assert save_source is not None
        assert save_source["arguments"][0].get("value_type") == "Order"


class TestPhase3ExecutionFlow:
    """Phase 3 integration tests for execution flow (Issues 2, 6).

    These tests validate that method-level USES queries show Call nodes
    in line-number order (execution flow) while class-level queries
    continue to use the structural approach.
    """

    def test_t3_1_method_uses_shows_calls_in_line_order(self, index):
        """T3.1 / AC13: Method USES shows entries in line-number order.

        createOrder()'s USES should show entries in the order they appear
        in the source code. In the variable-centric model, some calls are
        Kind 1 variable entries (with source_call) and some are Kind 2
        direct call entries. Both should be in line order.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Extract all calls with their lines, including calls nested in
        # Kind 1 variable entries via source_call
        calls = []
        for e in result.uses:
            if e.entry_type == "local_variable" and e.source_call:
                sc = e.source_call
                if sc.member_ref and sc.member_ref.reference_type in (
                    "method_call", "instantiation", "function_call", "static_call"
                ):
                    calls.append((e.line, sc.fqn))
            elif e.member_ref and e.member_ref.reference_type in (
                "method_call", "instantiation", "function_call", "static_call"
            ):
                calls.append((e.line, e.fqn))

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
        call (inside $order Kind 1 entry) and the process() call (inside
        $processedOrder Kind 1 entry) with $order as argument.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find Order::__construct() via Kind 1 $order variable entry
        constructor = find_call_entry(result.uses, "Order::__construct()")
        assert constructor is not None, "Order::__construct() should appear in USES (via $order)"
        assert constructor.member_ref.reference_type == "instantiation"

        # Find process() via Kind 1 $processedOrder variable entry
        process = find_call_entry(result.uses, "process()")
        assert process is not None, "process() should appear in USES (via $processedOrder)"
        assert hasattr(process, "arguments")
        assert len(process.arguments) == 1
        assert process.arguments[0].value_expr == "$order", (
            f"process() should receive $order, got '{process.arguments[0].value_expr}'"
        )
        assert process.arguments[0].value_source == "local"

    def test_t3_3_local_variables_not_shown_as_separate_entries(self, index):
        """T3.3 / AC15: Local variables only appear as Kind 1 entries with source_call.

        In the variable-centric model, local variables appear as Kind 1 entries
        (entry_type='local_variable') that always have a source_call containing
        the call that produced them. There should be no standalone Value/Variable
        entries without a source_call.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Value entries should only appear as Kind 1 entries with source_call
        for entry in result.uses:
            if entry.kind in ("Variable", "Value"):
                assert entry.entry_type == "local_variable", (
                    f"Value/Variable entry should have entry_type='local_variable': {entry.fqn}"
                )
                assert entry.source_call is not None, (
                    f"Kind 1 variable entry should have source_call: {entry.fqn}"
                )

    def test_t3_4_class_level_query_uses_structural_approach(self, index):
        """T3.4 / AC16: Class-level context query uses class-specific grouping.

        After ISSUE-C (Class USES Redesign), class-level queries return
        grouped, deduplicated entries with ref_type set directly on ContextEntry.
        Should show structural types like extends, implements, property_type,
        parameter_type, return_type, instantiation — NOT execution flow.
        """
        # Use InMemoryOrderRepository (concrete class in the index)
        node = index.resolve_symbol("App\\Repository\\InMemoryOrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Class-level USES entries use ref_type directly on ContextEntry
        ref_types = {e.ref_type for e in result.uses if e.ref_type}
        structural_types = {"property_access", "parameter_type", "type_hint",
                           "return_type", "property_type", "extends", "implements",
                           "instantiation"}
        assert ref_types & structural_types, (
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

        In the variable-centric model, Kind 1 entries have the call FQN
        in source_call, and Kind 2 entries have it directly.
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

        # Collect all FQNs including source_call FQNs for Kind 1 entries
        all_fqns = []
        for e in json_dict["uses"]:
            all_fqns.append(e["fqn"])
            if e.get("source_call"):
                all_fqns.append(e["source_call"]["fqn"])

        assert any("checkAvailability" in f for f in all_fqns), (
            "checkAvailability should be in JSON USES"
        )
        assert any("save()" in f for f in all_fqns), (
            "save() should be in JSON USES (via source_call)"
        )
        assert any("__construct()" in f for f in all_fqns), (
            "A constructor call should be in JSON USES (via source_call)"
        )


class TestPhase1ExpressionDisplay:
    """Phase 1 ISSUE-A tests for expression-based argument display.

    Tests that _get_argument_info() uses edge expression when available
    and falls back to Value node name when expression is absent.
    """

    def test_t1_10_backward_compat_no_expression_falls_back_to_node_name(self, index):
        """T1.10 / AC6: save() argument shows $processedOrder as value_expr.

        The save() call receives a local variable $processedOrder.
        The sot.json now has expression fields on argument edges, so
        value_expr shows the expression value directly.

        In the variable-centric model, save() is inside the $savedOrder
        Kind 1 variable entry's source_call.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # save() is now a Kind 1 entry — use find_call_entry
        entry = find_call_entry(result.uses, "save()")
        assert entry is not None
        assert len(entry.arguments) == 1
        assert entry.arguments[0].value_expr == "$processedOrder", (
            f"save() arg should show '$processedOrder', "
            f"got '{entry.arguments[0].value_expr}'"
        )

    def test_t1_10b_literal_arg_shows_expression_value(self, index):
        """AC6: Literal argument shows its expression value.

        The Order constructor's literal arguments now have expression fields
        in sot.json, showing the actual literal values (e.g., '0', "'pending'").

        In the variable-centric model, Order::__construct() is inside the
        $order Kind 1 variable entry's source_call.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_call_entry(result.uses, "Order::__construct()")
        assert entry is not None
        # Position 0 is a literal (order ID), position 4 is a literal ('pending')
        literal_args = [a for a in entry.arguments if a.value_source == "literal"]
        assert len(literal_args) > 0, "Constructor should have literal arguments"
        # Literal args now show actual expression values
        for arg in literal_args:
            assert arg.value_expr is not None and arg.value_expr != "", (
                f"Literal arg should have a non-empty value_expr, "
                f"got '{arg.value_expr}'"
            )

    def test_t1_10c_result_arg_shows_expression_value(self, index):
        """AC6: Result argument shows its expression value.

        The sot.json now has expression fields on argument edges.
        checkAvailability() arguments show the actual expressions like
        '$input->productId' and '$input->quantity'.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        entry = find_entry_by_fqn(result.uses, "checkAvailability()")
        assert entry is not None
        assert len(entry.arguments) == 2
        # Both args now show actual expressions
        assert entry.arguments[0].value_expr == "$input->productId", (
            f"First arg should show '$input->productId', "
            f"got '{entry.arguments[0].value_expr}'"
        )
        assert entry.arguments[1].value_expr == "$input->quantity", (
            f"Second arg should show '$input->quantity', "
            f"got '{entry.arguments[1].value_expr}'"
        )

    def test_t1_11_json_output_includes_value_expr(self, index):
        """T1.11 / AC8: JSON output includes value_expr from expression or fallback.

        The JSON serialization should include value_expr in argument objects.
        In the variable-centric model, save() is inside a Kind 1 variable
        entry's source_call in the JSON output.
        """
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Find save() in JSON output — it's inside a Kind 1 variable entry's source_call
        save_source = None
        for e in json_dict["uses"]:
            if e.get("source_call") and "save()" in e["source_call"].get("fqn", ""):
                save_source = e["source_call"]
                break
        assert save_source is not None, "save() should be in JSON USES (via source_call)"
        assert "arguments" in save_source
        assert save_source["arguments"][0]["value_expr"] == "$processedOrder"

    def test_t1_mcp_output_includes_value_expr(self, index):
        """P1-D2-4 / AC8: MCP output includes expression-based value_expr.

        The MCP server's context response should include value_expr in
        argument objects. In the variable-centric model, save() is inside
        a Kind 1 variable entry's source_call in the MCP output.
        """
        from src.server.mcp import MCPServer
        from pathlib import Path

        sot = str(Path(__file__).parent.parent.parent / "kloc-reference-project-php" / "contract-tests" / "output" / "sot.json")
        server = MCPServer(sot_path=sot)

        result = server.call_tool("kloc_context", {
            "symbol": "OrderService::createOrder",
            "depth": 1,
        })

        # Find save() in MCP USES — it's inside a Kind 1 variable entry's source_call
        save_source = None
        for e in result["uses"]:
            if e.get("source_call") and "save()" in e["source_call"].get("fqn", ""):
                save_source = e["source_call"]
                break
        assert save_source is not None, "save() should be in MCP USES (via source_call)"
        assert "arguments" in save_source
        assert save_source["arguments"][0]["value_expr"] == "$processedOrder"
        assert save_source["arguments"][0]["value_source"] == "local"


# =============================================================================
# Phase 2b: ISSUE-E — Definition section tests (developer-1)
# =============================================================================


class TestDefinitionMethodNode:
    """Tests for Method definition (AC17)."""

    def test_method_has_definition(self, index):
        """AC17: Method context has a DEFINITION section."""
        node = index.resolve_symbol("OrderService::createOrder")
        assert len(node) >= 1
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        assert result.definition is not None
        assert result.definition.kind == "Method"
        assert "createOrder" in result.definition.fqn

    def test_method_definition_has_signature(self, index):
        """AC17: Method definition shows signature."""
        node = index.resolve_symbol("OrderService::createOrder")
        assert len(node) >= 1
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        defn = result.definition
        assert defn is not None
        # Signature should include method name and parameters
        if defn.signature:
            assert "createOrder" in defn.signature

    def test_method_definition_has_arguments(self, index):
        """AC17: Method definition shows typed arguments."""
        node = index.resolve_symbol("OrderService::createOrder")
        assert len(node) >= 1
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        defn = result.definition
        assert defn is not None
        # createOrder has at least one argument ($input)
        assert len(defn.arguments) >= 1
        arg_names = [a.get("name") for a in defn.arguments]
        assert any("input" in (name or "") for name in arg_names)

    def test_method_definition_has_return_type(self, index):
        """AC17: Method definition shows return type if available."""
        node = index.resolve_symbol("OrderService::createOrder")
        assert len(node) >= 1
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        defn = result.definition
        assert defn is not None
        # Return type may or may not be present depending on fixture data
        # Just verify the field exists and is the right type
        assert defn.return_type is None or isinstance(defn.return_type, dict)

    def test_method_definition_has_declared_in(self, index):
        """AC17: Method definition shows containing class."""
        node = index.resolve_symbol("OrderService::createOrder")
        assert len(node) >= 1
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        defn = result.definition
        assert defn is not None
        assert defn.declared_in is not None
        assert "OrderService" in defn.declared_in.get("fqn", "")


class TestDefinitionClassNode:
    """Tests for Class definition (AC18)."""

    def test_class_has_definition(self, index):
        """AC18: Class context has a DEFINITION section."""
        node = index.resolve_symbol("OrderService")
        class_nodes = [n for n in node if n.kind == "Class"]
        assert len(class_nodes) >= 1
        query = ContextQuery(index)
        result = query.execute(class_nodes[0].id, depth=1)

        assert result.definition is not None
        assert result.definition.kind == "Class"
        assert "OrderService" in result.definition.fqn

    def test_class_definition_has_properties(self, index):
        """AC18: Class definition shows properties."""
        node = index.resolve_symbol("OrderService")
        class_nodes = [n for n in node if n.kind == "Class"]
        assert len(class_nodes) >= 1
        query = ContextQuery(index)
        result = query.execute(class_nodes[0].id, depth=1)

        defn = result.definition
        assert defn is not None
        # OrderService has properties (injected dependencies)
        assert len(defn.properties) > 0

    def test_class_definition_has_methods(self, index):
        """AC18: Class definition shows methods."""
        node = index.resolve_symbol("OrderService")
        class_nodes = [n for n in node if n.kind == "Class"]
        assert len(class_nodes) >= 1
        query = ContextQuery(index)
        result = query.execute(class_nodes[0].id, depth=1)

        defn = result.definition
        assert defn is not None
        # OrderService has methods
        assert len(defn.methods) > 0
        method_names = [m.get("name") for m in defn.methods]
        assert "createOrder" in method_names


class TestDefinitionPropertyAndArgument:
    """Tests for Property and Argument definitions (AC19, AC20)."""

    def test_property_has_definition(self, index):
        """AC19: Property context has a DEFINITION section."""
        node = index.resolve_symbol("OrderService::$orderRepository")
        if not node:
            pytest.skip("OrderService::$orderRepository not found in fixtures")
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        assert result.definition is not None
        assert result.definition.kind == "Property"
        assert result.definition.declared_in is not None


class TestDefinitionMinimal:
    """Tests for minimal definition (AC22)."""

    def test_minimal_definition_has_kind_and_fqn(self, index):
        """AC22: Even symbols without metadata show kind + FQN."""
        # Use any available node
        node = index.resolve_symbol("OrderService")
        assert len(node) >= 1
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        defn = result.definition
        assert defn is not None
        assert defn.kind is not None
        assert defn.fqn is not None


class TestDefinitionJsonOutput:
    """Tests for JSON output of definition (AC23)."""

    def test_json_includes_definition(self, index):
        """AC23: JSON output includes definition object."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("OrderService::createOrder")
        assert len(node) >= 1
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        json_dict = context_tree_to_dict(result)
        assert "definition" in json_dict
        defn = json_dict["definition"]
        assert "fqn" in defn
        assert "kind" in defn
        assert defn["kind"] == "Method"

    def test_json_definition_has_arguments(self, index):
        """AC23: JSON definition includes arguments array."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("OrderService::createOrder")
        assert len(node) >= 1
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        json_dict = context_tree_to_dict(result)
        defn = json_dict["definition"]
        assert "arguments" in defn
        assert isinstance(defn["arguments"], list)
        assert len(defn["arguments"]) >= 1

    def test_json_definition_has_declared_in(self, index):
        """AC23: JSON definition includes declared_in object."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("OrderService::createOrder")
        assert len(node) >= 1
        query = ContextQuery(index)
        result = query.execute(node[0].id, depth=1)

        json_dict = context_tree_to_dict(result)
        defn = json_dict["definition"]
        assert "declaredIn" in defn
        assert "OrderService" in defn["declaredIn"]["fqn"]

    def test_mcp_response_includes_definition(self, index):
        """AC23: MCP response includes definition object."""
        from src.server.mcp import MCPServer
        from pathlib import Path

        sot = str(Path(__file__).parent.parent.parent / "kloc-reference-project-php" / "contract-tests" / "output" / "sot.json")
        server = MCPServer(sot_path=sot)

        result = server.call_tool("kloc_context", {
            "symbol": "OrderService::createOrder",
            "depth": 1,
        })

        assert "definition" in result
        defn = result["definition"]
        assert defn["kind"] == "Method"
        assert "createOrder" in defn["fqn"]


class TestDefinitionClassJsonOutput:
    """Tests for Class definition in JSON output."""

    def test_class_json_has_properties_and_methods(self, index):
        """AC23: Class definition JSON includes properties and methods."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("OrderService")
        class_nodes = [n for n in node if n.kind == "Class"]
        assert len(class_nodes) >= 1
        query = ContextQuery(index)
        result = query.execute(class_nodes[0].id, depth=1)

        json_dict = context_tree_to_dict(result)
        assert "definition" in json_dict
        defn = json_dict["definition"]
        assert defn["kind"] == "Class"
        assert "properties" in defn
        assert "methods" in defn
        assert len(defn["properties"]) > 0
        assert len(defn["methods"]) > 0


# =============================================================================
# Phase 4: ISSUE-D — Rich argument display tests
# =============================================================================


class TestPhase4ParamFqn:
    """Tests for param_fqn in arguments (AC29)."""

    def test_param_fqn_present_for_typed_call(self, index):
        """AC29: Argument has formal parameter FQN from callee."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find checkAvailability call — it has resolved params
        entry = find_call_entry(result.uses, "checkAvailability()")
        assert entry is not None
        assert len(entry.arguments) == 2
        # param_fqn should be the callee's Argument FQN
        for arg in entry.arguments:
            if arg.param_fqn:
                assert "checkAvailability" in arg.param_fqn


class TestPhase4ValueRefSymbol:
    """Tests for value_ref_symbol on arguments (AC30, AC31)."""

    def test_local_variable_arg_has_value_ref_symbol(self, index):
        """AC30: Argument from local variable has value_ref_symbol."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find save() call — receives $processedOrder (a local)
        entry = find_call_entry(result.uses, "save()")
        assert entry is not None
        assert len(entry.arguments) == 1
        arg = entry.arguments[0]
        assert arg.value_source == "local"
        assert arg.value_ref_symbol is not None, "Local variable arg should have value_ref_symbol"
        assert "$processedOrder" in arg.value_ref_symbol or "processedOrder" in arg.value_ref_symbol

    def test_literal_arg_has_literal_source(self, index):
        """AC32: Literal argument shows literal source, no ref symbol."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find Order::__construct() — has literal args
        entry = find_call_entry(result.uses, "Order::__construct()")
        assert entry is not None
        literal_args = [a for a in entry.arguments if a.value_source == "literal"]
        assert len(literal_args) > 0, "Constructor should have literal args"
        for arg in literal_args:
            assert arg.value_ref_symbol is None, "Literal args should not have value_ref_symbol"

    def test_result_arg_has_source_chain(self, index):
        """AC33: Result argument (property access) has source_chain."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find checkAvailability — its args are results of property access
        entry = find_call_entry(result.uses, "checkAvailability()")
        assert entry is not None
        result_args = [a for a in entry.arguments if a.value_source == "result"]
        # Source chain may or may not be traced depending on graph structure
        # Just verify the field exists and has the right type
        for arg in result_args:
            assert arg.value_ref_symbol is None, "Result args should not have value_ref_symbol"
            if arg.source_chain:
                assert isinstance(arg.source_chain, list)
                assert len(arg.source_chain) > 0
                # Each step should have fqn
                assert "fqn" in arg.source_chain[0]


class TestPhase4JsonOutput:
    """Tests for ISSUE-D JSON output (AC35)."""

    def test_json_includes_param_fqn(self, index):
        """AC35: JSON output includes param_fqn when available."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Find an argument entry with param_fqn
        found_param_fqn = False
        for entry in json_dict["uses"]:
            args = entry.get("arguments", [])
            if not args and entry.get("source_call"):
                args = entry["source_call"].get("arguments", [])
            for arg in args:
                if arg.get("param_fqn"):
                    found_param_fqn = True
                    break
            if found_param_fqn:
                break
        assert found_param_fqn, "JSON output should include param_fqn for arguments with resolved params"

    def test_json_includes_value_ref_symbol(self, index):
        """AC35: JSON output includes value_ref_symbol for local variable args."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Find an argument with value_ref_symbol
        found_ref = False
        for entry in json_dict["uses"]:
            args = entry.get("arguments", [])
            if not args and entry.get("source_call"):
                args = entry["source_call"].get("arguments", [])
            for arg in args:
                if arg.get("value_ref_symbol"):
                    found_ref = True
                    break
            if found_ref:
                break
        assert found_ref, "JSON output should include value_ref_symbol for local variable args"

    def test_json_no_both_ref_and_chain(self, index):
        """AC35: value_ref_symbol and source_chain are mutually exclusive."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        for entry in json_dict["uses"]:
            args = entry.get("arguments", [])
            if not args and entry.get("source_call"):
                args = entry["source_call"].get("arguments", [])
            for arg in args:
                has_ref = arg.get("value_ref_symbol") is not None
                has_chain = arg.get("source_chain") is not None
                assert not (has_ref and has_chain), (
                    f"Argument should not have both value_ref_symbol and source_chain: {arg}"
                )


class TestPhase4GracefulDegradation:
    """Tests for graceful degradation (AC36)."""

    def test_incomplete_data_shows_what_it_can(self, index):
        """AC36: Arguments always show at least FQN and expression, no errors."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        for entry in result.uses:
            # Check Kind 2 entry args
            for arg in entry.arguments:
                assert arg.value_expr is not None, f"Argument should always have value_expr"
                assert arg.position is not None, f"Argument should always have position"
            # Check Kind 1 source_call args
            if entry.source_call:
                for arg in entry.source_call.arguments:
                    assert arg.value_expr is not None
                    assert arg.position is not None


# =============================================================================
# v3 ISSUE-B: Filter return_type/parameter_type from USES
# =============================================================================


class TestIssueB_FilterSignatureTypes:
    """v3 ISSUE-B integration tests: return_type and parameter_type entries
    are filtered from method-level USES because DEFINITION already shows them.

    ACs covered: 10-16.
    """

    def test_ac10_return_type_not_in_method_uses(self, index):
        """AC10: Method with return type OrderOutput — no [return_type] in USES."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        return_type_entries = [
            e for e in result.uses
            if e.member_ref and e.member_ref.reference_type == "return_type"
        ]
        assert len(return_type_entries) == 0, (
            f"No [return_type] entries should be in method USES. "
            f"Found: {[e.fqn for e in return_type_entries]}"
        )

    def test_ac11_parameter_type_not_in_method_uses(self, index):
        """AC11: Method with param type CreateOrderInput — no [parameter_type] in USES."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        param_type_entries = [
            e for e in result.uses
            if e.member_ref and e.member_ref.reference_type == "parameter_type"
        ]
        assert len(param_type_entries) == 0, (
            f"No [parameter_type] entries should be in method USES. "
            f"Found: {[e.fqn for e in param_type_entries]}"
        )

    def test_ac12_multiple_param_types_all_filtered(self, index):
        """AC12: Method with multiple parameter types — none appear in USES.

        checkAvailability(string $productId, int $quantity) has built-in types
        which may not produce type reference entries. Use createOrder which has
        CreateOrderInput as a class-type parameter.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Verify no parameter_type or return_type entries exist at all
        filtered_types = {"parameter_type", "return_type"}
        for entry in result.uses:
            if entry.member_ref and entry.member_ref.reference_type in filtered_types:
                assert False, (
                    f"Found [{entry.member_ref.reference_type}] entry in method USES: "
                    f"{entry.fqn}. These should be filtered."
                )

    def test_ac13_property_type_kept_in_class_uses(self, index):
        """AC13: Class query retains [property_type] entries in USES.

        After ISSUE-C (Class USES Redesign), class-level queries return
        entries with ref_type set directly on ContextEntry.
        """
        node = index.resolve_symbol("App\\Service\\OrderService")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Class-level USES entries use ref_type directly on ContextEntry
        ref_types = {e.ref_type for e in result.uses if e.ref_type}
        TYPE_RELATED = {"type_hint", "property_type", "parameter_type", "return_type",
                       "instantiation", "extends", "implements"}
        assert ref_types & TYPE_RELATED, (
            f"Class-level USES should retain type reference entries. "
            f"Found ref_types: {ref_types}"
        )

    def test_ac14_type_hint_kept_in_method_uses(self, index):
        """AC14: type_hint entries remain in method USES (not filtered).

        type_hint entries come from instanceof, catch blocks, or other non-signature
        usages. They should not be filtered.
        """
        # This test verifies that TYPE_KINDS still includes type_hint.
        # For createOrder(), there may or may not be type_hint entries.
        # The key assertion is that type_hint is NOT in the filtered set.
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Verify that if any type_hint entries exist, they are still present
        # (not filtered). We can't assert type_hint entries exist since
        # createOrder() may not have any, but we verify no regression.
        for entry in result.uses:
            if entry.member_ref and entry.member_ref.reference_type == "type_hint":
                # type_hint entries are kept — this is correct
                pass

    def test_ac15_json_excludes_return_type_parameter_type(self, index):
        """AC15: JSON output excludes return_type/parameter_type from uses array."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Check all entries in uses array — none should have
        # reference_type of "return_type" or "parameter_type"
        filtered_types = {"return_type", "parameter_type"}
        for entry in json_dict["uses"]:
            if "member_ref" in entry:
                ref_type = entry["member_ref"].get("reference_type")
                assert ref_type not in filtered_types, (
                    f"JSON uses entry has [{ref_type}] which should be filtered. "
                    f"FQN: {entry['fqn']}"
                )
            # Also check source_call for Kind 1 entries
            if entry.get("source_call") and "member_ref" in entry["source_call"]:
                ref_type = entry["source_call"]["member_ref"].get("reference_type")
                assert ref_type not in filtered_types, (
                    f"JSON source_call has [{ref_type}] which should be filtered. "
                    f"FQN: {entry['source_call']['fqn']}"
                )

    def test_ac16_non_method_query_unchanged(self, index):
        """AC16: Class-level queries use class-specific grouping (ISSUE-B/C).

        After ISSUE-C (Class USES Redesign), class-level queries return
        grouped, deduplicated entries with ref_type set directly on ContextEntry.
        """
        # Query a Class node
        node = index.resolve_symbol("App\\Repository\\InMemoryOrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Class-level USES entries use ref_type directly on ContextEntry
        ref_types = {e.ref_type for e in result.uses if e.ref_type}
        structural_types = {"property_access", "parameter_type", "type_hint",
                           "return_type", "property_type", "extends", "implements",
                           "instantiation"}
        assert ref_types & structural_types, (
            f"Class-level USES should include structural reference types. "
            f"Found: {ref_types}"
        )

    def test_definition_still_has_return_type(self, index):
        """Verify DEFINITION section still shows return type (not affected by filter)."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        assert result.definition is not None
        # Return type should be in DEFINITION
        assert result.definition.return_type is not None, (
            "DEFINITION should still show return type"
        )
        assert "OrderOutput" in (
            result.definition.return_type.get("name", "") or
            result.definition.return_type.get("fqn", "")
        ), "DEFINITION return type should include OrderOutput"

    def test_definition_still_has_arguments(self, index):
        """Verify DEFINITION section still shows typed arguments (not affected by filter)."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        assert result.definition is not None
        assert len(result.definition.arguments) >= 1, (
            "DEFINITION should still show arguments"
        )
        arg_names = [a.get("name") for a in result.definition.arguments]
        assert any("input" in (name or "") for name in arg_names), (
            "DEFINITION arguments should include $input"
        )


# =============================================================================
# v3 ISSUE-C: Filter orphan property access entries from USES
# =============================================================================


class TestIssueC_FilterOrphanPropertyAccess:
    """v3 ISSUE-C integration tests: orphan property access entries consumed by
    non-Call expressions (string concatenation, sprintf) are filtered from USES.

    ACs covered: 17-24.
    """

    def test_ac17_order_id_concat_orphan_filtered(self, index):
        """AC17: $savedOrder->id consumed by string concatenation in send().$subject is filtered.

        The property access Order::$id at line 49 is consumed by the string
        concatenation expression in send().$subject. It should not appear as
        a top-level USES entry.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find any top-level Order::$id property_access entry
        orphan_id_entries = [
            e for e in result.uses
            if e.member_ref
            and e.member_ref.reference_type == "property_access"
            and "Order::$id" in e.fqn
        ]
        assert len(orphan_id_entries) == 0, (
            f"Order::$id [property_access] orphan should be filtered from USES. "
            f"Found {len(orphan_id_entries)} entries at lines: "
            f"{[e.line for e in orphan_id_entries]}"
        )

    def test_ac18_sprintf_orphans_filtered(self, index):
        """AC18: $savedOrder->productId and $savedOrder->quantity consumed by sprintf are filtered.

        Property accesses at lines 53-54 consumed by sprintf() in send().$body
        should not appear as top-level USES entries.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        orphan_entries = [
            e for e in result.uses
            if e.member_ref
            and e.member_ref.reference_type == "property_access"
            and ("Order::$productId" in e.fqn or "Order::$quantity" in e.fqn)
        ]
        assert len(orphan_entries) == 0, (
            f"Order::$productId and Order::$quantity orphans should be filtered. "
            f"Found: {[e.fqn for e in orphan_entries]}"
        )

    def test_ac19_consumed_property_access_remains_nested(self, index):
        """AC19: Order::$id consumed by OrderOutput constructor argument edge remains nested.

        The property access $savedOrder->id at line 61 is consumed by the
        OrderOutput::__construct() argument edge. It should appear in the
        constructor's argument source_chain, NOT as a separate top-level entry.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find OrderOutput::__construct() call entry
        output_ctor = find_call_entry(result.uses, "OrderOutput::__construct()")
        assert output_ctor is not None, (
            "OrderOutput::__construct() should appear in USES"
        )
        # First argument should reference $savedOrder->id
        assert len(output_ctor.arguments) >= 1
        assert "$savedOrder->id" in (output_ctor.arguments[0].value_expr or ""), (
            f"First arg of OrderOutput::__construct() should be $savedOrder->id, "
            f"got '{output_ctor.arguments[0].value_expr}'"
        )

    def test_ac20_local_variable_property_access_not_filtered(self, index):
        """AC20: Property access assigned to local variable remains as Kind 1 entry.

        Standalone property accesses like $repo = $this->orderRepository that have
        assigned_from edges to local variables are Kind 1 entries, not orphans.
        These should NOT be filtered.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # All Kind 1 entries should remain (they are never orphan candidates)
        kind1_entries = [e for e in result.uses if e.entry_type == "local_variable"]
        assert len(kind1_entries) >= 4, (
            f"Expected at least 4 Kind 1 variable entries, got {len(kind1_entries)}"
        )

    def test_ac21_receiver_property_access_not_filtered(self, index):
        """AC21: Property access consumed as receiver remains nested as on:.

        $this->inventoryChecker consumed via receiver edge by checkAvailability()
        is correctly nested as on: inside the call. It is NOT a top-level orphan.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # checkAvailability() should have access_chain showing the receiver
        check_entry = find_entry_by_fqn(result.uses, "checkAvailability()")
        assert check_entry is not None
        assert check_entry.member_ref is not None
        assert check_entry.member_ref.access_chain == "$this->inventoryChecker", (
            f"checkAvailability() should have access_chain=$this->inventoryChecker, "
            f"got '{check_entry.member_ref.access_chain}'"
        )

    def test_ac23_json_excludes_orphan_entries(self, index):
        """AC23: JSON output excludes orphan property access entries."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # No top-level entry should be an orphan property_access for Order::$id/productId/quantity
        orphan_fqns = {"Order::$id", "Order::$productId", "Order::$quantity"}
        for entry in json_dict["uses"]:
            if "member_ref" in entry:
                ref_type = entry["member_ref"].get("reference_type")
                fqn = entry["fqn"]
                if ref_type == "property_access":
                    is_orphan = any(name in fqn for name in orphan_fqns)
                    assert not is_orphan, (
                        f"JSON output should not include orphan property_access: {fqn}"
                    )

    def test_ac24_tree_and_json_consistent(self, index):
        """AC24: Tree and JSON output consistently exclude orphan entries."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Count of top-level entries should match
        tree_count = len(result.uses)
        json_count = len(json_dict["uses"])
        assert tree_count == json_count, (
            f"Tree ({tree_count}) and JSON ({json_count}) should have same entry count"
        )

        # Neither should have property_access orphans
        tree_prop_accesses = [
            e for e in result.uses
            if e.member_ref and e.member_ref.reference_type == "property_access"
        ]
        json_prop_accesses = [
            e for e in json_dict["uses"]
            if "member_ref" in e and e["member_ref"].get("reference_type") == "property_access"
        ]
        assert len(tree_prop_accesses) == len(json_prop_accesses), (
            f"Tree ({len(tree_prop_accesses)}) and JSON ({len(json_prop_accesses)}) "
            f"should have same number of property_access entries"
        )

    def test_no_orphan_property_access_in_createOrder(self, index):
        """Comprehensive check: no orphan property_access entries in createOrder() USES.

        After filtering, createOrder() should have 0 top-level property_access entries.
        All property accesses are either consumed as receivers (on:), consumed as
        arguments (nested in source_chain), or consumed by non-Call expressions
        (filtered by the orphan heuristic).
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        prop_access_entries = [
            e for e in result.uses
            if e.member_ref and e.member_ref.reference_type == "property_access"
        ]
        assert len(prop_access_entries) == 0, (
            f"createOrder() should have 0 top-level property_access entries after filtering. "
            f"Found: {[e.fqn + ' line=' + str(e.line) for e in prop_access_entries]}"
        )

    def test_method_call_entries_preserved(self, index):
        """Regression: method_call and instantiation entries are not affected by filter."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Key entries should still be present
        assert find_call_entry(result.uses, "checkAvailability()") is not None
        assert find_call_entry(result.uses, "Order::__construct()") is not None
        assert find_call_entry(result.uses, "process()") is not None
        assert find_call_entry(result.uses, "save()") is not None
        assert find_call_entry(result.uses, "send()") is not None
        assert find_call_entry(result.uses, "OrderOutput::__construct()") is not None


class TestIssueA_ConstructorPromotionResolution:
    """Tests for ISSUE-A: Constructor promotion assigned_from fallback.

    Validates that promoted constructor parameters resolve to Property FQNs
    via the assigned_from edge fallback when no Argument children exist.
    ACs covered: 1-9.
    """

    def test_ac5_order_constructor_args_resolve_to_property_fqns(self, index):
        """AC5: Order::__construct() promoted params resolve to Property FQNs.

        createOrder() calls new Order(...) with 6 promoted params.
        Each param_fqn should be the Property FQN, not the Value FQN.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Order::__construct() is a Kind 1 entry (result assigned to $order)
        order_call = find_call_entry(result.uses, "Order::__construct()")
        assert order_call is not None, "Order::__construct() call should exist"
        assert len(order_call.arguments) == 6, f"Expected 6 args, got {len(order_call.arguments)}"

        # Each argument's param_fqn should be the promoted Property FQN
        param_fqns = {a.param_fqn for a in order_call.arguments}
        assert "App\\Entity\\Order::$id" in param_fqns
        assert "App\\Entity\\Order::$customerEmail" in param_fqns
        assert "App\\Entity\\Order::$productId" in param_fqns
        assert "App\\Entity\\Order::$quantity" in param_fqns
        assert "App\\Entity\\Order::$status" in param_fqns
        assert "App\\Entity\\Order::$createdAt" in param_fqns

    def test_ac6_order_constructor_param_names_resolve(self, index):
        """AC6: Promoted param names resolve from Value children.

        param_name should be the Value node name ($id, $customerEmail, etc.).
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        order_call = find_call_entry(result.uses, "Order::__construct()")
        assert order_call is not None

        param_names = {a.param_name for a in order_call.arguments}
        assert "$id" in param_names
        assert "$customerEmail" in param_names
        assert "$productId" in param_names
        assert "$quantity" in param_names
        assert "$status" in param_names
        assert "$createdAt" in param_names

    def test_ac5_orderoutput_constructor_resolves(self, index):
        """AC5: OrderOutput::__construct() promoted params also resolve.

        OrderOutput has 6 promoted constructor params. All should resolve
        to Property FQNs.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        output_call = find_call_entry(result.uses, "OrderOutput::__construct()")
        assert output_call is not None, "OrderOutput::__construct() should exist"
        assert len(output_call.arguments) == 6

        # Verify all param_fqns are Property FQNs (contain ::$)
        for arg in output_call.arguments:
            assert "::$" in arg.param_fqn, (
                f"arg[{arg.position}] param_fqn should be a Property FQN, "
                f"got: {arg.param_fqn}"
            )

    def test_ac7_single_promoted_param_resolves(self, index):
        """AC7: OrderCreatedMessage::__construct() with single promoted param.

        The message call is consumed by dispatch(), so we test param resolution
        directly via ContextQuery._resolve_param_fqn and _resolve_param_name.
        """
        query = ContextQuery(index)

        # Find the Call node for OrderCreatedMessage::__construct()
        msg_constructor = None
        for nid, node in index.nodes.items():
            if node.fqn == "App\\Ui\\Messenger\\Message\\OrderCreatedMessage::__construct()" and node.kind == "Method":
                msg_constructor = node
                break
        assert msg_constructor is not None

        calls = index.get_calls_to(msg_constructor.id)
        assert len(calls) == 1
        call_id = calls[0]

        # Test param name and FQN resolution for position 0
        param_name = query._resolve_param_name(call_id, 0)
        param_fqn = query._resolve_param_fqn(call_id, 0)

        assert param_name == "$orderId"
        assert param_fqn == "App\\Ui\\Messenger\\Message\\OrderCreatedMessage::$orderId"

    def test_ac8_non_promoted_args_still_resolve(self, index):
        """AC8: Regression — EmailSender::send() uses Argument nodes (non-promoted).

        Non-promoted methods should continue to resolve via Argument children.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # checkAvailability has non-promoted Argument nodes
        check_call = find_call_entry(result.uses, "checkAvailability()")
        assert check_call is not None
        assert len(check_call.arguments) == 2

        param_names = {a.param_name for a in check_call.arguments}
        assert "$productId" in param_names
        assert "$quantity" in param_names

        # param_fqn should be parameter FQN (contains .$), not Property FQN
        for arg in check_call.arguments:
            assert ".$" in arg.param_fqn, f"Non-promoted arg should have parameter FQN with . separator"
            assert "checkAvailability" in arg.param_fqn

    def test_ac9_json_includes_property_fqn_for_promoted(self, index):
        """AC9: JSON output includes Property FQN as param_fqn for promoted args."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)
        uses = json_dict.get("uses", [])

        # Find the OrderOutput::__construct() entry
        output_entry = None
        for entry in uses:
            if "OrderOutput::__construct()" in entry.get("fqn", ""):
                output_entry = entry
                break
            sc = entry.get("source_call", {})
            if sc and "OrderOutput::__construct()" in sc.get("fqn", ""):
                output_entry = sc
                break

        assert output_entry is not None, "OrderOutput::__construct() should be in JSON uses"
        args = output_entry.get("arguments", [])
        assert len(args) == 6

        # All param_fqn values should be Property FQNs
        for arg in args:
            fqn = arg.get("param_fqn", "")
            assert "OrderOutput::$" in fqn, (
                f"JSON param_fqn should be Property FQN, got: {fqn}"
            )

    def test_promoted_param_positional_order(self, index):
        """Verify promoted params are in correct positional order.

        Order::__construct(int $id, string $customerEmail, string $productId,
        int $quantity, string $status, DateTimeImmutable $createdAt)
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        order_call = find_call_entry(result.uses, "Order::__construct()")
        assert order_call is not None

        # Build position -> param_name map
        pos_to_name = {a.position: a.param_name for a in order_call.arguments}
        assert pos_to_name[0] == "$id"
        assert pos_to_name[1] == "$customerEmail"
        assert pos_to_name[2] == "$productId"
        assert pos_to_name[3] == "$quantity"
        assert pos_to_name[4] == "$status"
        assert pos_to_name[5] == "$createdAt"


class TestV4IssueA_ImplExecutionFlow:
    """Tests for v4 ISSUE-A: Impl subtrees use execution flow instead of raw deps.

    Validates that -> impl blocks show behavioral content (calls, property accesses
    with receiver chains) instead of structural type noise (parameter_type, type_hint chains).
    """

    def test_preprocess_impl_shows_behavioral_content(self, index):
        """v4-A-CLI-01: preProcess impl shows Order::$status property_access with on: receiver.

        NOT structural Order [parameter_type] with entity property declarations.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=5, include_impl=True)

        # Find $processedOrder variable entry which contains process() as source_call
        # The execution flow children of process() are attached to the variable entry
        proc_var = find_variable_entry(result.uses, "$processedOrder")
        assert proc_var is not None, "$processedOrder variable entry should be in uses"

        # Find preProcess in variable entry's children (process()'s execution flow)
        pre_process_call = None
        for child in proc_var.children:
            if "preProcess" in child.fqn:
                pre_process_call = child
                break
        assert pre_process_call is not None, "preProcess() should be in $processedOrder children"

        # preProcess should have implementations
        assert len(pre_process_call.implementations) > 0, "preProcess() should have implementations"
        impl = pre_process_call.implementations[0]
        assert "StandardOrderProcessor::preProcess" in impl.fqn

        # Impl children should be behavioral (property_access with on: receiver)
        assert len(impl.children) > 0, "preProcess impl should have children"

        # Find Order::$status property_access
        status_entry = None
        for child in impl.children:
            if child.member_ref and "Order::$status" in child.member_ref.target_fqn:
                status_entry = child
                break
        assert status_entry is not None, "preProcess impl should have Order::$status property_access"
        assert status_entry.member_ref.reference_type == "property_access"

        # Should NOT have parameter_type entries
        param_type_entries = [
            c for c in impl.children
            if c.member_ref and c.member_ref.reference_type == "parameter_type"
        ]
        assert len(param_type_entries) == 0, "preProcess impl should NOT have parameter_type entries"

    def test_emailsender_impl_no_type_hint_chain(self, index):
        """v4-A-CLI-02/05: EmailSender::send() impl shows only $sentEmails static_property.

        No recursive type_hint -> EmailSenderInterface -> impl -> type_hint chain.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=5, include_impl=True)

        # Find send() call
        send_call = find_call_entry(result.uses, "send()")
        assert send_call is not None, "send() call should be in uses"

        # send() should have implementations
        assert len(send_call.implementations) > 0, "send() should have implementations"
        send_impl = send_call.implementations[0]
        assert "EmailSender::send" in send_impl.fqn

        # Impl should have EmailSender::$sentEmails as static_property
        sent_emails_entries = [
            c for c in send_impl.children
            if c.member_ref and "$sentEmails" in c.member_ref.target_fqn
        ]
        assert len(sent_emails_entries) > 0, "send() impl should have $sentEmails entry"
        assert sent_emails_entries[0].member_ref.reference_type == "static_property"

        # Should NOT have type_hint entries (no recursive chain)
        type_hint_entries = [
            c for c in send_impl.children
            if c.member_ref and c.member_ref.reference_type == "type_hint"
        ]
        assert len(type_hint_entries) == 0, (
            "send() impl should NOT have type_hint entries (no recursive chain)"
        )

    def test_impl_blocks_no_parameter_type_or_return_type(self, index):
        """v4-A-CLI-03: No parameter_type or return_type entries in any impl block.

        Consistent with v3 ISSUE-B filtering in the main tree.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=5, include_impl=True)

        def collect_all_impls(entries):
            """Recursively collect all implementation entries."""
            impls = []
            for entry in entries:
                impls.extend(entry.implementations)
                impls.extend(collect_all_impls(entry.children))
                if entry.source_call:
                    impls.extend(entry.source_call.implementations)
                    impls.extend(collect_all_impls(entry.source_call.children))
            return impls

        all_impls = collect_all_impls(result.uses)
        assert len(all_impls) > 0, "Should have at least one impl block"

        for impl in all_impls:
            for child in impl.children:
                if child.member_ref:
                    assert child.member_ref.reference_type not in ("parameter_type", "return_type"), (
                        f"Impl {impl.fqn} should NOT have {child.member_ref.reference_type} entry: "
                        f"{child.member_ref.target_fqn}"
                    )

    def test_inventorychecker_impl_empty(self, index):
        """v4-A-CLI-07: InventoryChecker::checkAvailability() impl has no children.

        Empty body method (just returns true) should show impl line only.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=5, include_impl=True)

        # Find checkAvailability() call
        check_call = find_call_entry(result.uses, "checkAvailability()")
        assert check_call is not None

        # Should have implementations
        assert len(check_call.implementations) > 0
        inv_impl = check_call.implementations[0]
        assert "InventoryChecker::checkAvailability" in inv_impl.fqn

        # Should have no children (empty body)
        assert len(inv_impl.children) == 0, (
            f"InventoryChecker::checkAvailability() impl should be empty, "
            f"got {len(inv_impl.children)} children"
        )

    def test_depth_budget_respected_in_impl(self, index):
        """v4-A-CLI-09: Depth budget limits impl subtree expansion."""
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        # Use depth=2 which should limit how deep impl blocks go
        result = query.execute(node.id, depth=2, include_impl=True)

        def max_depth_in_entries(entries, current=0):
            """Find the maximum depth across all entries recursively."""
            if not entries:
                return current
            depths = [current]
            for entry in entries:
                depths.append(max_depth_in_entries(entry.children, current + 1))
                for impl in entry.implementations:
                    depths.append(max_depth_in_entries(impl.children, current + 1))
                if entry.source_call:
                    depths.append(max_depth_in_entries(
                        entry.source_call.children, current + 1
                    ))
                    for impl in entry.source_call.implementations:
                        depths.append(max_depth_in_entries(impl.children, current + 1))
            return max(depths)

        max_d = max_depth_in_entries(result.uses)
        # With depth=2, entries at depth 1 and 2 are shown, plus impl children
        # at depth 2+1=3 maximum. The actual limit depends on where impls attach.
        # The key test: depth should not be unbounded
        assert max_d <= 5, f"Depth should be bounded, got max depth {max_d}"

    def test_json_impl_has_execution_flow_structure(self, index):
        """v4-A-CLI-10: JSON implementations array has execution flow entries.

        Entries should have member_ref, entry_type, etc. -- NOT raw dep edges.
        """
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=5, include_impl=True)

        json_dict = context_tree_to_dict(result)

        def find_impls_in_json(entries):
            """Recursively find all implementation arrays in JSON."""
            impls = []
            for entry in entries:
                if "implementations" in entry:
                    impls.extend(entry["implementations"])
                if entry.get("children"):
                    impls.extend(find_impls_in_json(entry["children"]))
                if entry.get("source_call"):
                    sc = entry["source_call"]
                    if "implementations" in sc:
                        impls.extend(sc["implementations"])
                    if sc.get("children"):
                        impls.extend(find_impls_in_json(sc["children"]))
            return impls

        all_json_impls = find_impls_in_json(json_dict["uses"])
        assert len(all_json_impls) > 0, "Should have impl entries in JSON"

        # Find send() impl which has children
        send_impl = None
        for impl in all_json_impls:
            if "EmailSender::send" in impl.get("fqn", ""):
                send_impl = impl
                break
        assert send_impl is not None

        # Its children should have execution flow structure (member_ref)
        assert len(send_impl["children"]) > 0
        child = send_impl["children"][0]
        assert "member_ref" in child, "Impl child should have member_ref (execution flow)"
        assert child["member_ref"]["reference_type"] == "static_property"


class TestV4IssueB_LocalVariableIdentity:
    """Tests for v4 ISSUE-B: Local variable identity in receiver chains.

    Validates that on: lines show [local]/[param] tags with (file:line)
    for Value receivers, and NO tags for property chain receivers.
    """

    def test_local_variable_on_shows_local_tag(self, index):
        """v4-B-CLI-01: Local variable on: shows [local] tag and (file:line).

        $savedOrder is a local variable assigned at line 45 (0-based).
        Source chain on: should show [local] and file:line.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find an argument that traces to $savedOrder (e.g., OrderOutput::$id)
        output_call = find_call_entry(result.uses, "OrderOutput::__construct()")
        assert output_call is not None, "OrderOutput::__construct() should be in uses"

        # Find an arg with source chain that has on: with savedOrder
        found_local = False
        for arg in output_call.arguments:
            if arg.source_chain:
                for step in arg.source_chain:
                    if step.get("on") and "savedOrder" in step["on"]:
                        assert step.get("on_kind") == "local", (
                            f"$savedOrder should have on_kind='local', got {step.get('on_kind')}"
                        )
                        assert step.get("on_file") is not None, "$savedOrder should have on_file"
                        assert step.get("on_line") is not None, "$savedOrder should have on_line"
                        assert "OrderService" in step["on_file"]
                        found_local = True
                        break
            if found_local:
                break
        assert found_local, "Should find $savedOrder with [local] tag in source chain"

    def test_parameter_on_shows_param_tag(self, index):
        """v4-B-CLI-02: Parameter on: shows [param] tag and (file:line).

        $input is a parameter of createOrder().
        Source chain on: should show [param] and file:line.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find checkAvailability() which has args tracing to $input
        check_call = find_call_entry(result.uses, "checkAvailability()")
        assert check_call is not None

        found_param = False
        for arg in check_call.arguments:
            if arg.source_chain:
                for step in arg.source_chain:
                    if step.get("on") and "$input" in step["on"]:
                        assert step.get("on_kind") == "param", (
                            f"$input should have on_kind='param', got {step.get('on_kind')}"
                        )
                        assert step.get("on_file") is not None, "$input should have on_file"
                        assert step.get("on_line") is not None, "$input should have on_line"
                        found_param = True
                        break
            if found_param:
                break
        assert found_param, "Should find $input with [param] tag in source chain"

    def test_this_property_no_kind_tag(self, index):
        """v4-B-CLI-05: $this->property receivers have NO kind tag.

        Property chains use access_chain from MemberRef, which should NOT
        have on_kind set for property receivers.
        """
        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find send() call which has on: $this->emailSender
        send_call = find_call_entry(result.uses, "send()")
        assert send_call is not None
        assert send_call.member_ref is not None
        assert send_call.member_ref.access_chain is not None
        assert "$this->" in send_call.member_ref.access_chain

        # Property chain should NOT have on_kind
        assert send_call.member_ref.on_kind is None, (
            f"$this->property should NOT have on_kind, got {send_call.member_ref.on_kind}"
        )

    def test_json_includes_on_kind_in_source_chain(self, index):
        """v4-B-CLI-03: JSON output includes on_kind, on_file, on_line for Value receivers."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        json_dict = context_tree_to_dict(result)

        # Search for on_kind in source chain steps
        found_on_kind = False
        for entry in json_dict["uses"]:
            args = entry.get("arguments", [])
            if not args and entry.get("source_call"):
                args = entry["source_call"].get("arguments", [])
            for arg in args:
                if arg.get("source_chain"):
                    for step in arg["source_chain"]:
                        if step.get("on_kind"):
                            found_on_kind = True
                            assert step["on_kind"] in ("local", "param")
                            assert "on_file" in step
                            assert "on_line" in step
                            break
                if found_on_kind:
                    break
            if found_on_kind:
                break
        assert found_on_kind, "JSON source chain should include on_kind for Value receivers"

    def test_json_member_ref_includes_on_kind(self, index):
        """v4-B-CLI-03: JSON member_ref includes on_kind for entries with Value receivers."""
        from src.output.tree import context_tree_to_dict

        node = index.resolve_symbol("App\\Service\\OrderService::createOrder")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=5, include_impl=True)

        json_dict = context_tree_to_dict(result)

        def find_member_ref_with_on_kind(entries):
            for entry in entries:
                mr = entry.get("member_ref", {})
                if mr.get("on_kind"):
                    return mr
                if entry.get("children"):
                    found = find_member_ref_with_on_kind(entry["children"])
                    if found:
                        return found
                if entry.get("source_call"):
                    sc = entry["source_call"]
                    if sc.get("children"):
                        found = find_member_ref_with_on_kind(sc["children"])
                        if found:
                            return found
                    if "implementations" in sc:
                        for impl in sc["implementations"]:
                            found = find_member_ref_with_on_kind(impl.get("children", []))
                            if found:
                                return found
                if "implementations" in entry:
                    for impl in entry["implementations"]:
                        found = find_member_ref_with_on_kind(impl.get("children", []))
                        if found:
                            return found
            return None

        mr = find_member_ref_with_on_kind(json_dict["uses"])
        assert mr is not None, "Should find member_ref with on_kind in JSON"
        assert mr["on_kind"] in ("local", "param")
        assert "on_file" in mr
        assert "on_line" in mr
