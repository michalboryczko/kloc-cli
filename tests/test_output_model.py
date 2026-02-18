"""Tests for the ContextOutput model and contract schema validation.

1. Equivalence: ContextOutput.from_result(result).to_dict() produces
   IDENTICAL output to the legacy context_tree_to_dict(result).
2. Schema: All 42 snapshot case outputs validate against
   kloc-contracts/kloc-cli-context.json.
"""

import json
import pytest
from pathlib import Path

from src.graph import SoTIndex
from src.queries import ContextQuery, ResolveQuery
from src.models.output import ContextOutput
from src.output.tree import context_tree_to_dict


# Paths
SOT_PATH = Path(__file__).parent.parent.parent / "artifacts" / "kloc-dev" / "context-final" / "sot.json"
CASES_PATH = Path(__file__).parent.parent.parent / "tests" / "cases.json"
SCHEMA_PATH = Path(__file__).parent.parent.parent / "kloc-contracts" / "kloc-cli-context.json"

pytestmark = pytest.mark.skipif(
    not SOT_PATH.exists() or not CASES_PATH.exists(),
    reason="Test fixtures not found (sot.json or cases.json)",
)


@pytest.fixture(scope="module")
def index():
    """Load the SoT index."""
    return SoTIndex(SOT_PATH)


@pytest.fixture(scope="module")
def cases():
    """Load snapshot test case definitions."""
    with open(CASES_PATH) as f:
        data = json.load(f)
    return data["cases"]


def _run_context_query(index, symbol: str, depth: int, impl: bool):
    """Resolve symbol and run context query, returning ContextResult."""
    resolve = ResolveQuery(index)
    result = resolve.execute(symbol)
    assert result.found, f"Symbol not found: {symbol}"
    assert result.unique, f"Symbol not unique: {symbol}"
    node = result.candidates[0]
    context_query = ContextQuery(index)
    return context_query.execute(node.id, depth=depth, include_impl=impl)


def _deep_sort(obj):
    """Recursively sort dict keys and list elements for stable comparison."""
    if isinstance(obj, dict):
        return {k: _deep_sort(v) for k, v in sorted(obj.items())}
    elif isinstance(obj, list):
        return [_deep_sort(item) for item in obj]
    return obj


@pytest.fixture(scope="module")
def equivalence_results(index, cases):
    """Run all cases and collect results for parametrized tests."""
    results = {}
    for case in cases:
        name = case["name"]
        result = _run_context_query(index, case["symbol"], case["depth"], case["impl"])
        old_output = context_tree_to_dict(result)
        new_output = ContextOutput.from_result(result).to_dict()
        results[name] = (old_output, new_output)
    return results


def test_case_count(cases):
    """Verify we have the expected 42 test cases."""
    assert len(cases) == 42


@pytest.mark.parametrize("case_index", range(42))
def test_output_equivalence(case_index, cases, equivalence_results):
    """Assert ContextOutput.to_dict() == context_tree_to_dict() for each case."""
    case = cases[case_index]
    name = case["name"]
    old_output, new_output = equivalence_results[name]

    # Compare with sorted keys for stable diff output
    old_json = json.dumps(_deep_sort(old_output), indent=2)
    new_json = json.dumps(_deep_sort(new_output), indent=2)

    assert old_json == new_json, (
        f"Output mismatch for case '{name}' "
        f"(symbol={case['symbol']}, depth={case['depth']}, impl={case['impl']})"
    )


def test_all_cases_produce_output(cases, equivalence_results):
    """Verify every case produced a non-empty result."""
    for case in cases:
        name = case["name"]
        old_output, new_output = equivalence_results[name]
        assert "target" in old_output, f"Missing target in old output for {name}"
        assert "target" in new_output, f"Missing target in new output for {name}"


# --- Schema validation tests ---

@pytest.fixture(scope="module")
def schema_validator():
    """Load the contract schema and return a Draft202012Validator."""
    jsonschema = pytest.importorskip("jsonschema")
    if not SCHEMA_PATH.exists():
        pytest.skip(f"Schema not found: {SCHEMA_PATH}")
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    return jsonschema.Draft202012Validator(schema)


@pytest.fixture(scope="module")
def schema_outputs(index, cases):
    """Run all cases and produce ContextOutput dicts for schema validation."""
    outputs = {}
    for case in cases:
        name = case["name"]
        result = _run_context_query(index, case["symbol"], case["depth"], case["impl"])
        outputs[name] = context_tree_to_dict(result)
    return outputs


@pytest.mark.parametrize("case_index", range(42))
def test_schema_validation(case_index, cases, schema_outputs, schema_validator):
    """Assert each snapshot case output validates against kloc-cli-context.json schema."""
    case = cases[case_index]
    name = case["name"]
    output = schema_outputs[name]

    errors = list(schema_validator.iter_errors(output))
    if errors:
        error_msgs = []
        for e in errors[:5]:
            path = " -> ".join(str(p) for p in e.absolute_path) if e.absolute_path else "(root)"
            error_msgs.append(f"  [{path}] {e.message}")
        msg = "\n".join(error_msgs)
        pytest.fail(
            f"Schema validation failed for '{name}' "
            f"(symbol={case['symbol']}, depth={case['depth']}):\n{msg}"
        )
