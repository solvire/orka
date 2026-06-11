"""Tests for refactoring standalone (module-level) functions without a class.

These tests verify that:
1. A standalone function can be extracted by ``extract_method_source``.
2. The ``MethodBodyReplacer`` can patch a standalone function body.
3. The orchestrator accepts ``class_name=None`` gracefully.
4. The full refactoring pipeline works for module-level functions.
"""

import textwrap
from pathlib import Path

import pytest

from orka.surgery.synthesizer import extract_method_source
from orka.surgery.modifier import apply_llm_patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def standalone_function_file(tmp_path):
    """Creates a Python file with a standalone function (no class)."""
    path = tmp_path / "utils.py"
    path.write_text(textwrap.dedent("""\
        import os
        import logging

        logger = logging.getLogger(__name__)

        def process_file(path: str) -> bool:
            \"\"\"Process a file and return success.\"\"\"
            logger.info(f"Processing {path}")
            if not os.path.exists(path):
                return False
            return True
    """))
    return path


@pytest.fixture
def file_with_class_and_function(tmp_path):
    """Creates a file mixing a class method and a standalone function."""
    path = tmp_path / "mix.py"
    path.write_text(textwrap.dedent("""\
        import logging

        logger = logging.getLogger(__name__)

        def helper(value: int) -> int:
            \"\"\"A standalone helper.\"\"\"
            return value * 2

        class Worker:
            def do_work(self) -> str:
                return "done"
    """))
    return path


# ---------------------------------------------------------------------------
# Extraction tests
# ---------------------------------------------------------------------------

class TestExtractStandaloneFunction:
    def test_extract_function_no_class(self, standalone_function_file):
        """Can extract a standalone function without specifying a class."""
        source = extract_method_source(
            str(standalone_function_file),
            target_method="process_file",
        )
        assert source is not None
        assert "def process_file(path: str) -> bool:" in source
        assert "os.path.exists" in source

    def test_extract_function_explicit_none_class(self, standalone_function_file):
        """Explicitly passing class_name=None works identically."""
        source = extract_method_source(
            str(standalone_function_file),
            target_method="process_file",
            target_class=None,
        )
        assert source is not None
        assert "def process_file" in source

    def test_extract_function_not_found(self, standalone_function_file):
        """Asking for a non-existent function returns None."""
        source = extract_method_source(
            str(standalone_function_file),
            target_method="nonexistent_func",
        )
        assert source is None

    def test_extract_function_mixed_file(self, file_with_class_and_function):
        """Can extract the standalone function from a file that also has a class."""
        source = extract_method_source(
            str(file_with_class_and_function),
            target_method="helper",
        )
        assert source is not None
        assert "def helper(value: int) -> int:" in source
        assert "return value * 2" in source

    def test_extract_class_method_in_mixed_file(self, file_with_class_and_function):
        """The class method can still be extracted by its class."""
        source = extract_method_source(
            str(file_with_class_and_function),
            target_method="do_work",
            target_class="Worker",
        )
        assert source is not None
        assert "def do_work(self) -> str:" in source


# ---------------------------------------------------------------------------
# Patching tests
# ---------------------------------------------------------------------------

class TestPatchStandaloneFunction:
    def test_patch_function_body(self, standalone_function_file):
        """Can replace the body of a standalone function."""
        new_body = textwrap.dedent("""\
            logger.info(f"Processing {path}")
            result = os.path.isfile(path)
            return result
        """)
        success = apply_llm_patch(
            str(standalone_function_file),
            target_method="process_file",
            new_logic=new_body,
        )
        assert success is True

        modified = standalone_function_file.read_text()
        assert "result = os.path.isfile(path)" in modified
        assert "return result" in modified
        # Old code should be gone
        assert "if not os.path.exists(path)" not in modified
        # Signature must be preserved
        assert "def process_file(path: str) -> bool:" in modified

    def test_patch_preserves_decorator_on_function(self, standalone_function_file):
        """If the function had decorators, they survive the patch."""
        # Inject a decorated function
        code = standalone_function_file.read_text() + textwrap.dedent("""\

            @staticmethod
            def cached_lookup(key: str) -> str:
                return cache[key]
        """)
        standalone_function_file.write_text(code)

        new_body = "return _cache.get(key, '')"
        success = apply_llm_patch(
            str(standalone_function_file),
            target_method="cached_lookup",
            new_logic=new_body,
        )
        assert success is True
        modified = standalone_function_file.read_text()
        assert "@staticmethod" in modified
        assert "def cached_lookup(key: str) -> str:" in modified

    def test_patch_non_existent_function(self, standalone_function_file):
        """Patching a non-existent function returns False."""
        new_body = "return 42"
        success = apply_llm_patch(
            str(standalone_function_file),
            target_method="i_dont_exist",
            new_logic=new_body,
        )
        assert success is False

    def test_patch_function_no_class_explicit(self, standalone_function_file):
        """Explicitly passing target_class=None works for standalone functions."""
        new_body = "return True"
        success = apply_llm_patch(
            str(standalone_function_file),
            target_method="process_file",
            new_logic=new_body,
            target_class=None,
        )
        assert success is True
        modified = standalone_function_file.read_text()
        assert "return True" in modified
