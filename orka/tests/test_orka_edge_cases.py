import textwrap
from pathlib import Path
import pytest

from orka.surgery.modifier import apply_llm_patch
from orka.surgery.synthesizer import extract_method_source

@pytest.fixture
def complex_file(tmp_path):
    """Creates a python file loaded with edge cases."""
    file_path = tmp_path / "complex_controllers.py"
    original_code = textwrap.dedent("""
        from celery import shared_task

        class UserAPI:
            def validate(self):
                \"\"\"User validation.\"\"\"
                return self.user.is_active

        class OrderAPI:
            @shared_task(bind=True, max_retries=3)
            async def validate(self, request, *args, **kwargs):
                # Order validation is complex
                if not request.user:
                    raise Exception("No user")
                return True
                
            def helper(self):
                pass
    """).strip()
    file_path.write_text(original_code, encoding="utf-8")
    return file_path


def test_method_name_collision(complex_file):
    """
    EDGE CASE: Two classes have a method named `validate`.
    Ensure LibCST only modifies the one inside `OrderAPI`, and leaves `UserAPI` untouched.
    """
    new_logic = 'return "ORDER_VALID"'

    success = apply_llm_patch(
        file_path=str(complex_file),
        target_class="OrderAPI",
        target_method="validate",
        new_logic=new_logic
    )

    assert success is True
    modified_code = complex_file.read_text(encoding="utf-8")

    # 1. UserAPI validate must remain untouched
    assert "return self.user.is_active" in modified_code
    
    # 2. OrderAPI validate must be updated
    assert 'return "ORDER_VALID"' in modified_code
    assert 'raise Exception("No user")' not in modified_code


def test_async_and_complex_decorator_preservation(complex_file):
    """
    EDGE CASE: Method is `async` and has a complex parameterized decorator.
    Ensure LibCST does not downgrade `async def` to `def` and keeps the decorator intact.
    """
    extracted_source = extract_method_source(
        file_path=str(complex_file),
        target_class="OrderAPI",
        target_method="validate"
    )

    # Verify Extractor didn't lose async
    assert "async def validate" in extracted_source
    assert "@shared_task(bind=True, max_retries=3)" in extracted_source

    new_logic = "await asyncio.sleep(1)\nreturn True"
    
    apply_llm_patch(
        file_path=str(complex_file),
        target_class="OrderAPI",
        target_method="validate",
        new_logic=new_logic
    )
    
    modified_code = complex_file.read_text(encoding="utf-8")

    # Verify Modifier didn't lose async or decorators during AST swap
    assert "async def validate(self, request, *args, **kwargs):" in modified_code
    assert "@shared_task(bind=True, max_retries=3)" in modified_code
    assert "await asyncio.sleep(1)" in modified_code


def test_llm_yap_and_markdown_extraction():
    """
    EDGE CASE: The LLM wraps code in markdown fences.
    fix_md_fences strips the outermost fence, but does NOT remove
    surrounding non-code text (the prompt is responsible for that).
    """
    from orka.clients import OrkaLangChainClient
    
    # Case 1: Entire response is a fenced code block (ideal scenario)
    code_only = textwrap.dedent("""\
    ```python
    logger.info("Initializing phase 2")
    for item in data:
        item.process()
    return True
    ```
    """)
    
    clean_code = OrkaLangChainClient.fix_md_fences(code_only)
    
    assert "```" not in clean_code
    assert "python" not in clean_code
    assert 'logger.info("Initializing phase 2")' in clean_code
    assert "return True" in clean_code

    # Case 2: No fences at all — passes through unchanged
    plain = 'return "hello"'
    assert OrkaLangChainClient.fix_md_fences(plain) == plain

    # Case 3: Empty string
    assert OrkaLangChainClient.fix_md_fences("") == ""


def test_missing_class_graceful_failure(complex_file):
    """
    EDGE CASE: The orchestrator tries to patch a class/method that doesn't exist.
    It should return False, not throw a Fatal AST Exception.
    """
    success = apply_llm_patch(
        file_path=str(complex_file),
        target_class="NonExistentAPI",
        target_method="validate",
        new_logic="return False"
    )
    
    assert success is False # Handled gracefully

    success_method = apply_llm_patch(
        file_path=str(complex_file),
        target_class="UserAPI",
        target_method="non_existent_method",
        new_logic="return False"
    )
    
    assert success_method is False # Handled gracefully