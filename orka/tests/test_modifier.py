import textwrap
from pathlib import Path
import pytest

# Adjust import based on your actual path
from orka.surgery.modifier import apply_llm_patch

@pytest.fixture
def target_file(tmp_path):
    """Creates a dummy Django controller with complex formatting."""
    file_path = tmp_path / "api_views.py"
    
    original_code = textwrap.dedent("""
        import logging
        from django.db import transaction

        # --- IMPORTANT SYSTEM COMMENTS ---

        class OrderController:
            
            @transaction.atomic
            @login_required
            def process_order(self, request, order_id: str) -> bool:
                \"\"\"Old docstring to be overwritten or kept.\"\"\"
                logger.info("Starting order")
                order = Order.objects.get(id=order_id)
                order.process()
                return True

            def health_check(self):
                # Don't touch me!
                return "OK"
    """).strip()
    
    file_path.write_text(original_code, encoding="utf-8")
    return file_path


def test_surgical_body_replacement(target_file):
    """Verifies that LibCST replaces the logic without destroying decorators or other methods."""
    
    # This is exactly what Aider/DeepSeek would return as the "new logic" snippet
    new_llm_logic = """
        logger.info(f"Processing V2 for order: {order_id}")
        order = Order.objects.select_for_update().get(id=order_id)
        
        if not order.is_valid():
            raise ValidationError("Invalid Order")
            
        order.process_v2()
        return True
    """

    # Apply the patch
    success = apply_llm_patch(
        file_path=str(target_file),
        target_class="OrderController",
        target_method="process_order",
        new_logic=new_llm_logic
    )

    assert success is True

    # Read the modified file
    modified_code = target_file.read_text(encoding="utf-8")

    # 1. VERIFY SIGNATURE & DECORATORS SURVIVED
    assert "@transaction.atomic" in modified_code
    assert "@login_required" in modified_code
    assert "def process_order(self, request, order_id: str) -> bool:" in modified_code

    # 2. VERIFY NEW LOGIC WAS INJECTED
    assert "Processing V2 for order" in modified_code
    assert "select_for_update()" in modified_code

    # 3. VERIFY OTHER METHODS AND COMMENTS SURVIVED
    assert "Don't touch me!" in modified_code
    assert "def health_check(self):" in modified_code
    assert "# --- IMPORTANT SYSTEM COMMENTS ---" in modified_code

    # 4. VERIFY OLD LOGIC IS GONE
    assert 'logger.info("Starting order")' not in modified_code