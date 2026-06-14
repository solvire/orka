"""
Tests for the shared LLM-output sanitization functions in orka.core.snippet_utils.

These tests validate fence stripping, indent normalization, and the
full multi-pass sanitization pipeline, including edge cases around
empty/whitespace-only inputs.
"""

import textwrap

import pytest

from orka.core.snippet_utils import (
    normalize_snippet_indent,
    sanitize_llm_output,
    strip_md_fences,
)


# =============================================================================
# strip_md_fences
# =============================================================================


class TestStripMDFences:
    """Tests for strip_md_fences()."""

    def test_strip_md_fences_single(self):
        """Standard ```python … ``` block is extracted."""
        raw = textwrap.dedent("""
            ```python
            def foo():
                return 42
            ```
        """).strip()

        result = strip_md_fences(raw)
        expected = "def foo():\n    return 42"
        assert result == expected

    def test_strip_md_fences_unclosed(self):
        """A string that starts with ```python but has no closing fence."""
        raw = textwrap.dedent("""
            ```python
            def foo():
                return 42
        """).strip()

        result = strip_md_fences(raw)
        expected = "def foo():\n    return 42"
        assert result == expected

    def test_strip_md_fences_none(self):
        """A raw string with no fences at all is returned unchanged."""
        raw = "x = 1\ny = 2"
        result = strip_md_fences(raw)
        assert result == raw

    def test_strip_md_fences_no_language_label(self):
        """Triple backticks without a language label (``` alone)."""
        raw = textwrap.dedent("""
            ```
            print("hello")
            ```
        """).strip()

        result = strip_md_fences(raw)
        assert result == 'print("hello")'

    def test_strip_md_fences_multiple_blocks(self):
        """
        Multiple fenced blocks — intermediate fences survive so the
        output is *not* valid Python, which is deliberate: leftover
        ``\\````` markers cause LibCST's ``parse_snippet_to_cst_body``
        to raise ``ParserSyntaxError``, routing to ``fix_draft``.
        Failing fast at the parser is safer than guessing which block
        to inject.
        """
        raw = textwrap.dedent("""
            ```python
            first = 1
            ```
            ```python
            second = 2
            ```
        """).strip()

        result = strip_md_fences(raw)
        # First fence block content is present
        assert "first = 1" in result
        # Intermediate fences survive → not valid Python (by design)
        assert "```" in result

    def test_strip_md_fences_empty(self):
        """Empty string in, empty string out."""
        assert strip_md_fences("") == ""
        assert strip_md_fences("   ") == ""

    def test_strip_md_fences_only_fence(self):
        """A string that is nothing but a fence marker."""
        assert strip_md_fences("```") == ""
        assert strip_md_fences("```python") == ""


# =============================================================================
# normalize_snippet_indent
# =============================================================================


class TestNormalizeSnippetIndent:
    """Tests for normalize_snippet_indent()."""

    def test_normalize_snippet_indent_uniform(self):
        """Lines uniformly indented by 4 spaces → dedented."""
        raw = "    x = 1\n    y = 2"
        result = normalize_snippet_indent(raw)
        assert result == "x = 1\ny = 2"

    def test_normalize_snippet_indent_flush_first_line(self):
        """
        First line has 0 indent, subsequent lines have 8 spaces.

        The function finds ``min_indent=0``, so line 0 stays flush.
        Line 1 has ``relative_indent=8`` which is ``> 4`` so it gets
        reduced by 4 → 4 spaces.  Line 2 similarly → 4 spaces.
        """
        raw = "import os\n        x = os.getcwd()\n        print(x)"
        result = normalize_snippet_indent(raw)
        expected = "import os\n    x = os.getcwd()\n    print(x)"
        assert result == expected

    def test_normalize_snippet_indent_mixed_levels(self):
        """
        Lines initially have 4 and 8 spaces respectively.
        After normalization the first is flush, the second has 4.
        """
        raw = "    def foo():\n        return 42"
        result = normalize_snippet_indent(raw)
        assert result == "def foo():\n    return 42"

    def test_normalize_snippet_indent_deeply_nested(self):
        """
        Deeply nested block: all lines have at least 8 spaces.

        ``textwrap.dedent`` removes the common prefix (8 spaces) first,
        leaving ``if True:\n    if True:\n        pass`` (0/4/8).
        Since ``min_indent=0``, the function only adjusts lines where
        ``relative_indent > 4`` — the ``pass`` line drops from 8 → 4.
        """
        raw = "        if True:\n            if True:\n                pass"
        result = normalize_snippet_indent(raw)
        expected = "if True:\n    if True:\n    pass"
        assert result == expected

    def test_normalize_snippet_indent_empty(self):
        """Empty or single-line input returns as-is."""
        assert normalize_snippet_indent("") == ""
        assert normalize_snippet_indent("single_line") == "single_line"

    def test_normalize_snippet_indent_no_indent(self):
        """Block already flush left is unchanged."""
        raw = "a = 1\nb = 2"
        assert normalize_snippet_indent(raw) == raw


# =============================================================================
# sanitize_llm_output
# =============================================================================


class TestSanitizeLLMOutput:
    """Tests for sanitize_llm_output()."""

    def test_sanitize_llm_output_empty(self):
        """Empty or whitespace-only string returns empty without crashing."""
        assert sanitize_llm_output("") == ""
        assert sanitize_llm_output("   ") == ""
        assert sanitize_llm_output("\n\n  \n") == ""

    def test_sanitize_llm_output_fenced_code(self):
        """Standard ```python … ``` block is cleaned."""
        raw = textwrap.dedent("""
            ```python
            def greet():
                print("hello")
            ```
        """).strip()

        result = sanitize_llm_output(raw)
        expected = "def greet():\n    print(\"hello\")"
        assert result == expected

    def test_sanitize_llm_output_surrounding_text(self):
        """Text outside fences is stripped."""
        raw = textwrap.dedent("""
            Here is the updated code:

            ```python
            x = 1
            ```

            Let me know if this works.
        """).strip()

        result = sanitize_llm_output(raw)
        assert result == "x = 1"

    def test_sanitize_llm_output_no_fences(self):
        """
        Plain code without fences.

        ``raw.strip()`` strips leading whitespace from the first line,
        so ``textwrap.dedent`` sees ``min_indent=0`` and cannot dedent.
        The second line keeps its relative indent.
        """
        raw = "    y = 2\n    z = 3"
        result = sanitize_llm_output(raw)
        assert result == "y = 2\n    z = 3"

    def test_sanitize_llm_output_blank_lines(self):
        """Leading and trailing blank lines are removed."""
        raw = "\n\nprint('hello')\n\n"
        result = sanitize_llm_output(raw)
        assert result == "print('hello')"

    def test_sanitize_llm_output_trailing_whitespace(self):
        """Trailing spaces on each line are removed."""
        raw = "x = 1   \ny = 2   "
        result = sanitize_llm_output(raw)
        assert result == "x = 1\ny = 2"

    def test_sanitize_llm_output_only_whitespace_after_clean(self):
        """Only fences with no content → empty string."""
        raw = "```python\n```"
        result = sanitize_llm_output(raw)
        assert result == ""

    # ── Adversarial / edge-case tests ──────────────────────────────

    def test_conversational_preamble(self):
        """Preamble text before the fenced block is stripped."""
        raw = "Here is the code you requested:\n```python\nreturn x\n```"
        result = sanitize_llm_output(raw)
        assert result == "return x"

    def test_conversational_postscript(self):
        """Postscript text after the fenced block is stripped."""
        raw = "```python\nreturn x\n```\nLet me know if you need anything else!"
        result = sanitize_llm_output(raw)
        assert result == "return x"

    def test_mixed_whitespace(self):
        """
        A snippet with a mix of tabs and spaces.

        ``sanitize_llm_output`` passes the mixed whitespace through
        without crashing.  (LibCST may handle it, or the validation
        node may reject it later; that is fine.)
        """
        raw = "\tdef foo():\n    \treturn 42"
        result = sanitize_llm_output(raw)
        # Should not raise; the exact whitespace varies, but it must
        # be non-empty and not crash.
        assert result, "mixed-whitespace snippet should not be empty"

    def test_comments_only(self):
        """The LLM outputs only comment lines — no executable code."""
        raw = "# just a comment\n# nothing else"
        result = sanitize_llm_output(raw)
        # Pass-through: comments are valid inside a method body
        assert result == raw

