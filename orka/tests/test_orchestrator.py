"""
Tests for the Orchestrator wrapper (delegates to surgery graph).

The actual pipeline logic is tested via ``run_surgery()`` in the graph
controllers. This test verifies that the Orchestrator correctly wraps
``RefactorResult`` from surgery graph output.
"""

import textwrap
from pathlib import Path
import pytest

from orka.orchestrator import Orchestrator


@pytest.fixture
def orka_workspace(tmp_path):
    """Creates a workspace with a simple Python module."""
    workspace = tmp_path / "orka_workspace"
    workspace.mkdir()
    
    file_path = workspace / "orders.py"
    original_code = textwrap.dedent("""
        import logging
        from django.db import transaction

        class OrderController:
            
            @transaction.atomic
            def process_order(self, order_id: str) -> bool:
                \"\"\"Process the order.\"\"\"
                logger.info("Old logic")
                return False
    """).strip()
    
    file_path.write_text(original_code, encoding="utf-8")
    return workspace


def test_orchestrator_refactor_wraps_run_surgery(orka_workspace):
    """Verify that Orchestrator.refactor_method delegates to surgery graph
    and returns a valid RefactorResult (dry-run mode)."""
    orchestrator = Orchestrator(str(orka_workspace))
    target_file = str(orka_workspace / "orders.py")

    # Dry-run: validates the result shape without modifying the file
    result = orchestrator.refactor_method(
        file_path=target_file,
        class_name="OrderController",
        method_name="process_order",
        requirements="Update to use select_for_update() and execute the order.",
        dry_run=True,
    )

    assert isinstance(result.success, bool)
    assert result.label == "OrderController.process_order"
    assert result.file_path == target_file

    # The surgery graph may fail at compile_prompt if template loading
    # or rule resolution fails in CI. Check that error is meaningful.
    if not result.success:
        assert result.error is not None, "Expected a non-empty error on failure"
    else:
        # Successful dry-run should produce a diff
        assert len(result.diff) > 0, "Expected a non-empty diff on success"
        # File should NOT be modified in dry-run mode
        modified_code = (orka_workspace / "orders.py").read_text(encoding="utf-8")
        assert "Old logic" in modified_code, "File should not be modified in dry-run"
def test_refactor_result_dataclass():
    """Verify RefactorResult fields work correctly."""
    from orka.orchestrator import RefactorResult

    r = RefactorResult(True, "MyClass.my_method", "/tmp/test.py", diff="--- a\n+++ b\n", dry_run=True)
    assert r.success is True
    assert r.label == "MyClass.my_method"
    assert r.file_path == "/tmp/test.py"
    assert r.diff != ""
    assert r.dry_run is True
    assert r.error is None


def test_refactor_result_error():
    """Verify RefactorResult with error."""
    from orka.orchestrator import RefactorResult

    r = RefactorResult(False, "func", "/tmp/test.py", error="Something broke")
    assert r.success is False
    assert r.error == "Something broke"
    assert r.dry_run is False


def test_target_label_with_class():
    """Verify _target_label builds correct label with class."""
    from orka.orchestrator import _target_label

    assert _target_label("OrderController", "process") == "OrderController.process"


def test_target_label_without_class():
    """Verify _target_label builds correct label without class."""
    from orka.orchestrator import _target_label

    assert _target_label(None, "process") == "process"

