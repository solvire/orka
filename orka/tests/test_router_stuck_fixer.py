"""Tests for the router's stuck-fixer early termination logic.

When the fixer produces code that fails with the SAME validation error as
the previous pass, the router should bail early instead of wasting the
remaining iteration(s).
"""

from orka.operations.graph import _router


def _make_state(**overrides):
    """Build a minimal SurgeryState dict for router testing."""
    base = {
        "is_valid": False,
        "fatal_error": None,
        "iteration_count": 1,
        "max_iterations": 3,
        "validation_output": "",
        "previous_validation_output": "",
        "target_node_id": "TestClass.test_method",
    }
    base.update(overrides)
    return base


def test_router_returns_end_on_success():
    state = _make_state(is_valid=True)
    assert _router(state) == "end"


def test_router_returns_end_on_fatal_error():
    state = _make_state(fatal_error="Something broke")
    assert _router(state) == "end"


def test_router_returns_end_on_max_iterations():
    state = _make_state(iteration_count=3, max_iterations=3)
    assert _router(state) == "end"


def test_router_returns_fix_draft_on_first_failure():
    state = _make_state(
        iteration_count=0,
        validation_output="SyntaxError: invalid syntax",
        previous_validation_output="",
    )
    assert _router(state) == "fix_draft"


def test_router_returns_fix_draft_on_different_error():
    state = _make_state(
        iteration_count=1,
        validation_output="Gate 4 pytest FAILED: NameError",
        previous_validation_output="Gate 1 snippet AST FAILED: SyntaxError",
    )
    assert _router(state) == "fix_draft"


def test_router_returns_end_on_same_error_twice():
    """The fixer is stuck — same error, bail early."""
    same_error = "Gate 4 pytest FAILED: NameError: name 'pytest' is not defined"
    state = _make_state(
        iteration_count=1,
        validation_output=same_error,
        previous_validation_output=same_error,
    )
    assert _router(state) == "end"


def test_router_still_ends_on_max_when_same_error():
    """Even with same-error detection, max iterations still applies."""
    same_error = "SyntaxError"
    state = _make_state(
        iteration_count=3,
        max_iterations=3,
        validation_output=same_error,
        previous_validation_output=same_error,
    )
    assert _router(state) == "end"


def test_router_does_not_trigger_on_empty_errors():
    """Empty validation outputs should not trigger the stuck-fisher check."""
    state = _make_state(
        iteration_count=1,
        validation_output="",
        previous_validation_output="",
    )
    # Empty errors + not valid + iterations remain → fix_draft
    assert _router(state) == "fix_draft"


def test_router_triggers_on_stripped_whitespace_match():
    """Whitespace-only differences should still count as the same error."""
    state = _make_state(
        iteration_count=1,
        validation_output="  SyntaxError: bad indent  \n",
        previous_validation_output="SyntaxError: bad indent",
    )
    assert _router(state) == "end"
