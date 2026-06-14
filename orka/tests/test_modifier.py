import textwrap
from pathlib import Path
import pytest

# Adjust import based on your actual path
from orka.surgery.modifier import apply_llm_patch, preview_patch

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
    
    # Clean multi-line LLM snippet (no f-string braces, clean indentation)
    new_llm_logic = textwrap.dedent("""\
        logger.info("Processing V2")
        order = Order.objects.select_for_update().get(id=order_id)
        
        if not order.is_valid():
            raise ValidationError("Invalid Order")
            
        order.process_v2()
        return True
    """)

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
    assert "Processing V2" in modified_code
    assert "select_for_update()" in modified_code

    # 3. VERIFY OTHER METHODS AND COMMENTS SURVIVED
    assert "Don't touch me!" in modified_code
    assert "def health_check(self):" in modified_code
    assert "# --- IMPORTANT SYSTEM COMMENTS ---" in modified_code

    # 4. VERIFY OLD LOGIC IS GONE
    assert 'logger.info("Starting order")' not in modified_code


def test_preserves_original_docstring(tmp_path):
    """Verifies that the original docstring is preserved when LLM snippet lacks one."""
    file_path = tmp_path / "docstring_test.py"

    original_code = textwrap.dedent("""
        class MyService:
            def do_work(self, x: int) -> str:
                \"\"\"This is a docstring.\"\"\"
                return "old"
    """).strip()

    file_path.write_text(original_code, encoding="utf-8")

    # LLM snippet with NO docstring
    new_llm_logic = """
        return "new"
    """

    success = apply_llm_patch(
        file_path=str(file_path),
        target_class="MyService",
        target_method="do_work",
        new_logic=new_llm_logic,
    )

    assert success is True

    modified_code = file_path.read_text(encoding="utf-8")

    # 1. Original docstring is preserved
    assert '"""This is a docstring."""' in modified_code

    # 2. New logic is present
    assert 'return "new"' in modified_code

    # 3. Old logic is gone
    assert 'return "old"' not in modified_code

    # 4. Signature and class survive
    assert "class MyService:" in modified_code
    assert "def do_work(self, x: int) -> str:" in modified_code


# ═══════════════════════════════════════════════════════════════════════
# Adversarial / edge-case tests
# ═══════════════════════════════════════════════════════════════════════


class TestTargetNotFound:
    """preview_patch returns None when the target does not exist."""

    def test_method_not_found(self, tmp_path):
        """Targeting a non-existent method returns None."""
        file_path = tmp_path / "not_found.py"
        file_path.write_text(
            textwrap.dedent("""\
                class Foo:
                    def bar(self):
                        return 1
            """),
            encoding="utf-8",
        )
        result = preview_patch(
            file_path=str(file_path),
            target_method="nonexistent",
            new_logic="return 2",
            target_class="Foo",
        )
        assert result is None

    def test_class_not_found(self, tmp_path):
        """Targeting a non-existent class returns None."""
        file_path = tmp_path / "class_not_found.py"
        file_path.write_text(
            textwrap.dedent("""\
                class Foo:
                    def bar(self):
                        return 1
            """),
            encoding="utf-8",
        )
        result = preview_patch(
            file_path=str(file_path),
            target_method="bar",
            new_logic="return 2",
            target_class="NonExistent",
        )
        assert result is None

    def test_both_not_found(self, tmp_path):
        """Neither method nor class exists — returns None."""
        file_path = tmp_path / "both_not_found.py"
        file_path.write_text(
            textwrap.dedent("""\
                class Foo:
                    def bar(self):
                        return 1
            """),
            encoding="utf-8",
        )
        result = preview_patch(
            file_path=str(file_path),
            target_method="nope",
            new_logic="return 2",
            target_class="Nada",
        )
        assert result is None

    def test_standalone_function_not_found(self, tmp_path):
        """Non-existent standalone function (target_class=None) returns None."""
        file_path = tmp_path / "standalone_not_found.py"
        file_path.write_text(
            textwrap.dedent("""\
                def existing():
                    pass
            """),
            encoding="utf-8",
        )
        result = preview_patch(
            file_path=str(file_path),
            target_method="nonexistent",
            new_logic="return 1",
            target_class=None,
        )
        assert result is None


class TestComplexDecorators:
    """All decorators survive the body replacement."""

    @pytest.mark.parametrize(
        "decorator_lines",
        [
            ["@property"],
            ["@classmethod"],
            ["@staticmethod"],
            ["@retry(tries=3)"],
            ["@classmethod", "@retry(tries=3)"],
        ],
    )
    def test_decorator_survives(self, tmp_path, decorator_lines):
        """A method decorated with {deco} preserves all decorators."""
        # Build source manually (f-string dedent gets confused by
        # multi-line substitution blocks)
        indent = "        "
        deco_block = ("\n" + indent).join(decorator_lines)
        source = (
            "class MyClass:\n"
            f"{indent}{deco_block}\n"
            f"{indent}def do_it(self) -> str:\n"
            f'{indent}    return "old"\n'
        )
        file_path = tmp_path / f"deco_{'_'.join(d.replace('@','').replace('(','_').replace(')','') for d in decorator_lines)}.py"
        file_path.write_text(source, encoding="utf-8")

        result = preview_patch(
            file_path=str(file_path),
            target_method="do_it",
            new_logic='return "new_body"',
            target_class="MyClass",
        )
        assert result is not None, f"patch failed for decorators {decorator_lines}"
        for d in decorator_lines:
            assert d in result, f"decorator {d!r} missing from patched output"
        assert 'return "new_body"' in result

    def test_complex_signature(self, tmp_path):
        """A method with ``*args``, keyword-only args, ``**kwargs``, and
        a complex return type annotation survives the body replacement."""
        file_path = tmp_path / "complex_sig.py"
        original = textwrap.dedent("""\
            class MyClass:
                def foo(self, a: int = 1, *args, b: str | None = None, **kwargs) -> list[dict]:
                    return []
        """)
        file_path.write_text(original, encoding="utf-8")

        result = preview_patch(
            file_path=str(file_path),
            target_method="foo",
            new_logic='return [{}]',
            target_class="MyClass",
        )
        assert result is not None
        # Signature must be verbatim
        assert "def foo(self, a: int = 1, *args, b: str | None = None, **kwargs) -> list[dict]:" in result
        assert "return [{}]" in result


class TestMinimalBodyReplacement:
    """Replace ``pass`` / ``...`` and single-line function bodies."""

    def test_replace_pass_body(self, tmp_path):
        """Method whose body is just ``pass`` is replaced with a multi-line body."""
        file_path = tmp_path / "pass_body.py"
        file_path.write_text(
            textwrap.dedent("""\
                class MyClass:
                    def doit(self):
                        pass
            """),
            encoding="utf-8",
        )
        result = preview_patch(
            file_path=str(file_path),
            target_method="doit",
            new_logic="x = 1\ny = 2\nreturn x + y",
            target_class="MyClass",
        )
        assert result is not None
        assert "x = 1" in result
        assert "y = 2" in result
        assert "return x + y" in result
        assert "pass" not in result

    def test_replace_ellipsis_body(self, tmp_path):
        """Method whose body is just ``...`` (Ellipsis) is replaced."""
        file_path = tmp_path / "ellipsis_body.py"
        file_path.write_text(
            textwrap.dedent("""\
                class MyClass:
                    def doit(self):
                        ...
            """),
            encoding="utf-8",
        )
        result = preview_patch(
            file_path=str(file_path),
            target_method="doit",
            new_logic="x = 1\ny = 2\nreturn x + y",
            target_class="MyClass",
        )
        assert result is not None
        assert "x = 1" in result
        assert "return x + y" in result

    def test_single_line_function(self, tmp_path):
        """A method defined on a single line ``def foo(): return 1``
        is expanded into a multi-line body with correct indentation."""
        file_path = tmp_path / "single_line_fn.py"
        file_path.write_text(
            textwrap.dedent("""\
                class MyClass:
                    def foo(self): return 1
            """),
            encoding="utf-8",
        )
        result = preview_patch(
            file_path=str(file_path),
            target_method="foo",
            new_logic="x = 1\nreturn x + 2",
            target_class="MyClass",
        )
        assert result is not None
        # The expanded body should be multi-line
        assert "x = 1" in result
        assert "return x + 2" in result
        # The original one-liner return should be gone
        assert "return 1" not in result

    def test_preserve_trailing_comments(self, tmp_path):
        """A method with a trailing comment on its body does not crash
        when the body is replaced.  (The comment is attached to the
        original return statement and is therefore lost — the important
        thing is that the replacement succeeds.)"""
        file_path = tmp_path / "trailing_comment.py"
        file_path.write_text(
            textwrap.dedent("""\
                class MyClass:
                    def foo(self):
                        return 1  # End of foo
            """),
            encoding="utf-8",
        )
        # This must not crash; the trailing comment is expected to be
        # dropped (it's attached to the old return statement).
        result = preview_patch(
            file_path=str(file_path),
            target_method="foo",
            new_logic="return 42",
            target_class="MyClass",
        )
        assert result is not None
        assert "return 42" in result
