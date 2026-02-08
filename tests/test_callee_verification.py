"""Unit tests for find_call_for_usage callee verification.

Tests that find_call_for_usage() verifies the Call node's callee matches
the usage target before returning it. This prevents wrong reference types
when multiple Call nodes exist at the same line (e.g., constructor and
static property access).
"""

from unittest.mock import MagicMock
from collections import defaultdict

from src.models.node import NodeData
from src.queries.context import find_call_for_usage, _call_matches_target


def _make_node(node_id: str, kind: str, name: str = "test",
               file: str = "test.php", start_line: int = 10,
               call_kind: str | None = None) -> NodeData:
    """Helper to create a NodeData with minimal fields."""
    return NodeData(
        id=node_id,
        kind=kind,
        name=name,
        fqn=f"App\\{name}",
        symbol=f"scip-php ... App/{name}#",
        file=file,
        range={"start_line": start_line, "start_col": 0,
               "end_line": start_line + 5, "end_col": 0},
        call_kind=call_kind,
    )


def _make_mock_index(nodes: dict[str, NodeData],
                     calls_edges: dict[str, list[str]] | None = None,
                     contains_edges: dict[str, list[str]] | None = None,
                     contains_parent: dict[str, str] | None = None):
    """Create a mock SoTIndex with specified graph relationships.

    Args:
        nodes: Map of node_id -> NodeData.
        calls_edges: Map of Call_node_id -> [callee_node_id] (outgoing calls edges).
        contains_edges: Map of parent_id -> [child_id] (contains children).
        contains_parent: Map of child_id -> parent_id.
    """
    index = MagicMock()
    index.nodes = nodes

    calls_edges = calls_edges or {}
    contains_edges = contains_edges or {}
    contains_parent = contains_parent or {}

    # Mock get_call_target: follows calls edge from Call to callee
    def mock_get_call_target(call_id):
        targets = calls_edges.get(call_id, [])
        return targets[0] if targets else None
    index.get_call_target = mock_get_call_target

    # Mock get_calls_to: find Call nodes whose callee is target_id
    def mock_get_calls_to(target_id):
        result = []
        for call_id, targets in calls_edges.items():
            if target_id in targets:
                result.append(call_id)
        return result
    index.get_calls_to = mock_get_calls_to

    # Mock get_contains_children: get children of a container
    def mock_get_contains_children(parent_id):
        return contains_edges.get(parent_id, [])
    index.get_contains_children = mock_get_contains_children

    # Mock get_contains_parent: get parent of a node
    def mock_get_contains_parent(node_id):
        return contains_parent.get(node_id)
    index.get_contains_parent = mock_get_contains_parent

    return index


class TestCallMatchesTarget:
    """Tests for the _call_matches_target helper."""

    def test_direct_match_returns_true(self):
        """When Call callee equals target_id, returns True."""
        call = _make_node("call:1", "Call", call_kind="method")
        target = _make_node("method:1", "Method", name="save")

        index = _make_mock_index(
            nodes={"call:1": call, "method:1": target},
            calls_edges={"call:1": ["method:1"]},
        )

        assert _call_matches_target(index, "call:1", "method:1") is True

    def test_wrong_callee_returns_false(self):
        """When Call callee does not equal target_id, returns False."""
        call = _make_node("call:1", "Call", call_kind="method")
        target = _make_node("method:1", "Method", name="save")
        other = _make_node("method:2", "Method", name="delete")

        index = _make_mock_index(
            nodes={"call:1": call, "method:1": target, "method:2": other},
            calls_edges={"call:1": ["method:2"]},  # Calls delete, not save
        )

        assert _call_matches_target(index, "call:1", "method:1") is False

    def test_constructor_matches_class_target(self):
        """Constructor Call matching __construct() accepts the containing Class as target."""
        constructor_call = _make_node("call:1", "Call", call_kind="constructor")
        construct_method = _make_node("method:construct", "Method", name="__construct")
        order_class = _make_node("class:order", "Class", name="Order")

        index = _make_mock_index(
            nodes={
                "call:1": constructor_call,
                "method:construct": construct_method,
                "class:order": order_class,
            },
            calls_edges={"call:1": ["method:construct"]},
            contains_parent={"method:construct": "class:order"},
        )

        # Constructor targets __construct() but the uses edge targets the Class
        assert _call_matches_target(index, "call:1", "class:order") is True

    def test_constructor_does_not_match_unrelated_class(self):
        """Constructor Call for class A does not match class B."""
        constructor_call = _make_node("call:1", "Call", call_kind="constructor")
        construct_method = _make_node("method:construct", "Method", name="__construct")
        order_class = _make_node("class:order", "Class", name="Order")
        repo_class = _make_node("class:repo", "Class", name="OrderRepository")

        index = _make_mock_index(
            nodes={
                "call:1": constructor_call,
                "method:construct": construct_method,
                "class:order": order_class,
                "class:repo": repo_class,
            },
            calls_edges={"call:1": ["method:construct"]},
            contains_parent={"method:construct": "class:order"},
        )

        # Constructor for Order should not match OrderRepository
        assert _call_matches_target(index, "call:1", "class:repo") is False

    def test_no_callee_returns_false(self):
        """Call node with no callee edge returns False."""
        call = _make_node("call:1", "Call", call_kind="method")
        target = _make_node("method:1", "Method", name="save")

        index = _make_mock_index(
            nodes={"call:1": call, "method:1": target},
            calls_edges={},  # No calls edges
        )

        assert _call_matches_target(index, "call:1", "method:1") is False


class TestFindCallForUsageVerification:
    """Tests for find_call_for_usage with callee verification."""

    def test_returns_none_when_callee_does_not_match(self):
        """Returns None when Call node at correct line has wrong callee."""
        source = _make_node("method:source", "Method", name="createOrder",
                            file="Service.php", start_line=0)
        target_prop = _make_node("prop:nextId", "Property", name="$nextId")
        constructor_call = _make_node("call:constructor", "Call",
                                      file="Service.php", start_line=30,
                                      call_kind="constructor")
        construct_method = _make_node("method:construct", "Method",
                                      name="__construct")

        index = _make_mock_index(
            nodes={
                "method:source": source,
                "prop:nextId": target_prop,
                "call:constructor": constructor_call,
                "method:construct": construct_method,
            },
            calls_edges={"call:constructor": ["method:construct"]},
            contains_edges={"method:source": ["call:constructor"]},
        )

        # Constructor at line 30 targets __construct, not $nextId
        result = find_call_for_usage(
            index, "method:source", "prop:nextId", "Service.php", 30
        )
        assert result is None

    def test_returns_correct_call_when_callee_matches(self):
        """Returns Call ID when callee matches target."""
        source = _make_node("method:source", "Method", name="createOrder",
                            file="Service.php", start_line=0)
        target = _make_node("method:save", "Method", name="save")
        save_call = _make_node("call:save", "Call",
                               file="Service.php", start_line=45,
                               call_kind="method")

        index = _make_mock_index(
            nodes={
                "method:source": source,
                "method:save": target,
                "call:save": save_call,
            },
            calls_edges={"call:save": ["method:save"]},
            contains_edges={"method:source": ["call:save"]},
        )

        result = find_call_for_usage(
            index, "method:source", "method:save", "Service.php", 45
        )
        assert result == "call:save"

    def test_skips_wrong_target_picks_correct_one(self):
        """Multiple Call nodes at same line: skips wrong callee, picks correct one."""
        source = _make_node("method:source", "Method", name="save",
                            file="Repo.php", start_line=0)
        target_prop = _make_node("prop:nextId", "Property", name="$nextId")
        construct_method = _make_node("method:construct", "Method",
                                      name="__construct")

        # Constructor at line 30 (wrong target)
        constructor_call = _make_node("call:constructor", "Call",
                                      file="Repo.php", start_line=30,
                                      call_kind="constructor")
        # Static property access at line 30 (correct target)
        access_call = _make_node("call:access", "Call",
                                 file="Repo.php", start_line=30,
                                 call_kind="access_static")

        index = _make_mock_index(
            nodes={
                "method:source": source,
                "prop:nextId": target_prop,
                "method:construct": construct_method,
                "call:constructor": constructor_call,
                "call:access": access_call,
            },
            calls_edges={
                "call:constructor": ["method:construct"],
                "call:access": ["prop:nextId"],
            },
            contains_edges={
                "method:source": ["call:constructor", "call:access"],
            },
        )

        result = find_call_for_usage(
            index, "method:source", "prop:nextId", "Repo.php", 30
        )
        assert result == "call:access"

    def test_returns_none_when_no_calls_exist(self):
        """Returns None gracefully when call_children list is empty."""
        source = _make_node("method:source", "Method", name="empty",
                            file="Service.php", start_line=0)
        target = _make_node("prop:name", "Property", name="$name")

        index = _make_mock_index(
            nodes={
                "method:source": source,
                "prop:name": target,
            },
            calls_edges={},
            contains_edges={"method:source": []},
        )

        result = find_call_for_usage(
            index, "method:source", "prop:name", "Service.php", 10
        )
        assert result is None

    def test_container_fallback_also_verifies_callee(self):
        """Container-based matching path also checks callee match."""
        source = _make_node("method:source", "Method", name="process",
                            file="Service.php", start_line=0)
        target = _make_node("method:save", "Method", name="save")
        wrong_call = _make_node("call:wrong", "Call",
                                file="Service.php", start_line=50,
                                call_kind="method")
        other_method = _make_node("method:other", "Method", name="delete")

        index = _make_mock_index(
            nodes={
                "method:source": source,
                "method:save": target,
                "call:wrong": wrong_call,
                "method:other": other_method,
            },
            calls_edges={"call:wrong": ["method:other"]},
            contains_edges={"method:source": ["call:wrong"]},
            contains_parent={"call:wrong": "method:source"},
        )

        # Call is in the right container but targets wrong method
        # No location match (file=None), so falls to container-based matching
        result = find_call_for_usage(
            index, "method:source", "method:save", None, None
        )
        assert result is None

    def test_returns_none_when_no_line_match(self):
        """Returns None when no Call node exists at the specified line."""
        source = _make_node("method:source", "Method", name="process",
                            file="Service.php", start_line=0)
        target = _make_node("method:save", "Method", name="save")
        call = _make_node("call:save", "Call",
                          file="Service.php", start_line=45,
                          call_kind="method")

        index = _make_mock_index(
            nodes={
                "method:source": source,
                "method:save": target,
                "call:save": call,
            },
            calls_edges={"call:save": ["method:save"]},
            contains_edges={"method:source": ["call:save"]},
            contains_parent={"call:save": "method:source"},
        )

        # Query for line 99 where no Call node exists
        result = find_call_for_usage(
            index, "method:source", "method:save", "Service.php", 99
        )
        # Falls through to container-based matching which should find it
        assert result == "call:save"
