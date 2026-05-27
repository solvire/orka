import textwrap
from pathlib import Path
import pytest

from orka.orchestrator import Orchestrator

@pytest.fixture
def orka_workspace(tmp_path):
    """Creates a workspace with a Django controller."""
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

def test_full_orchestrator_pipeline(orka_workspace):
    """Tests the whole pipeline from ingestion to LibCST patching."""
    
    # 1. Initialize Orchestrator (Will trigger scan_directory and build the Graph)
    orchestrator = Orchestrator(str(orka_workspace))
    target_file = str(orka_workspace / "orders.py")

    # 2. Trigger Refactor
    success = orchestrator.refactor_method(
        file_path=target_file,
        class_name="OrderController",
        method_name="process_order",
        requirements="Update to use select_for_update() and execute the order."
    )

    assert success is True

    # 3. Verify the final file state
    modified_code = (orka_workspace / "orders.py").read_text(encoding="utf-8")
    
    # Structural integrity guarantees
    assert "@transaction.atomic" in modified_code
    assert "def process_order(self, order_id: str) -> bool:" in modified_code
    assert "class OrderController:" in modified_code
    
    # LLM business logic insertion — the prompt tells it to use select_for_update and execute
    assert "select_for_update" in modified_code
    
    # Old logic wiped out
    assert "Old logic" not in modified_code

def test_markdown_cleaner():
    """Verify that we safely strip markdown ticks from LLM output."""
    from orka.clients import OrkaLangChainClient
    
    dirty_llm_output = "```python\nx = 10\nreturn x\n```"
    
    clean_code = OrkaLangChainClient.fix_md_fences(dirty_llm_output)
    assert "```" not in clean_code
    assert "x = 10\nreturn x" in clean_code