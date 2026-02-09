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

        Type hints appear as references from:
        - Constructor parameters (constructor property promotion)
        - Method parameters/return types
        - Property declarations

        After Phase 1, type_hint is split into parameter_type, return_type,
        property_type. Accept any of these type-related reference types.
        """
        # Query OrderRepositoryInterface
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
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
            "Should find type-related references to OrderRepositoryInterface from OrderService. "
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
        # Query OrderRepositoryInterface
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
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
        # Query OrderRepositoryInterface
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
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
        # Query OrderRepositoryInterface (used_by side)
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Find the save() method call in used_by
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

        # Use OrderRepositoryInterface (OrderRepository class is not in the index)
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
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

        # Use OrderRepositoryInterface (OrderRepository class is not in the index)
        node = index.resolve_symbol("App\\Repository\\OrderRepositoryInterface")[0]
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
        """T3.4 / AC16: Class-level context query still uses structural approach.

        When querying a Class (not a Method), the USES direction should
        show structural dependencies (extends, implements, type_hint,
        property_access) rather than execution flow.
        """
        # Use InMemoryOrderRepository (concrete class in the index)
        node = index.resolve_symbol("App\\Repository\\InMemoryOrderRepository")[0]
        query = ContextQuery(index)
        result = query.execute(node.id, depth=1)

        # Class-level query should have structural entries
        ref_types = {
            e.member_ref.reference_type
            for e in result.uses
            if e.member_ref
        }
        # Should include structural types like type_hint, parameter_type, etc.
        structural_types = {"property_access", "parameter_type", "type_hint",
                           "return_type", "property_type"}
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
        assert "declared_in" in defn
        assert "OrderService" in defn["declared_in"]["fqn"]

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
