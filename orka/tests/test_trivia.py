"""Tests for orka.surgery.trivia — whitespace & docstring preservation helpers.

Covers ``preserve_docstring`` (CST-based), ``normalize_spacing`` and
``collapse_blank_lines`` (pure string operations).
"""

import textwrap

import libcst as cst

from orka.surgery.trivia import (
    collapse_blank_lines,
    normalize_spacing,
    preserve_docstring,
)


# ── helpers ────────────────────────────────────────────────────────────


def _body(source: str) -> cst.IndentedBlock:
    """Parse a body snippet into a LibCST ``IndentedBlock``."""
    indented = textwrap.indent(source, "    ")
    module = cst.parse_module(f"def _f():\n{indented}\n")
    func = module.body[0]
    return func.body


def _render_body(body: cst.IndentedBlock) -> str:
    """Render an ``IndentedBlock`` as the body of a dummy function."""
    dummy = cst.parse_module("def _f():\n    pass\n")
    func = dummy.body[0]
    patched = func.with_changes(body=body)
    return dummy.with_changes(body=(patched,)).code


# ═══════════════════════════════════════════════════════════════════════
# preserve_docstring
# ═══════════════════════════════════════════════════════════════════════


class TestPreserveDocstring:
    def test_prepended_when_new_lacks_docstring(self):
        """Original has a docstring, new body lacks one -> docstring prepended."""
        original = _body('"""Original docstring."""\nreturn "old"')
        new = _body('return "new"')

        result = preserve_docstring(original, new)
        rendered = _render_body(result)

        assert '"""Original docstring."""' in rendered
        assert 'return "new"' in rendered
        # The prepended node is the original's first statement (verbatim).
        assert result.body[0].deep_equals(original.body[0])

    def test_new_unchanged_when_both_have_docstrings(self):
        """Both bodies have docstrings -> new body returned unchanged."""
        original = _body('"""Original."""\nreturn 1')
        new = _body('"""New."""\nreturn 2')

        result = preserve_docstring(original, new)
        rendered = _render_body(result)

        assert '"""New."""' in rendered
        assert '"""Original."""' not in rendered
        assert "return 2" in rendered

    def test_new_unchanged_when_neither_has_docstring(self):
        """Neither body has a docstring -> new body returned unchanged."""
        original = _body("return 1")
        new = _body("return 2")

        result = preserve_docstring(original, new)
        rendered = _render_body(result)

        assert "return 2" in rendered
        assert "return 1" not in rendered

    def test_new_docstring_takes_precedence(self):
        """Original & new carry different docstrings -> new wins verbatim."""
        original = _body('"""Old doc."""\nreturn 1')
        new = _body('"""Fresh doc."""\nreturn 2')

        result = preserve_docstring(original, new)
        rendered = _render_body(result)

        assert '"""Fresh doc."""' in rendered
        assert '"""Old doc."""' not in rendered
        assert "return 2" in rendered


# ═══════════════════════════════════════════════════════════════════════
# collapse_blank_lines
# ═══════════════════════════════════════════════════════════════════════


class TestCollapseBlankLines:
    def test_three_blank_lines_collapse_to_one(self):
        source = "a\n\n\n\nb\n"  # a, 3 blank lines, b
        assert collapse_blank_lines(source) == "a\n\nb\n"

    def test_zero_blank_lines_unchanged(self):
        source = "a\nb\n"
        assert collapse_blank_lines(source) == "a\nb\n"

    def test_single_blank_line_unchanged(self):
        source = "a\n\nb\n"
        assert collapse_blank_lines(source) == "a\n\nb\n"

    def test_mixed_runs(self):
        # a, 3 blanks, b, 1 blank, c, 2 blanks, d
        source = "a\n\n\n\nb\n\nc\n\n\nd\n"
        assert collapse_blank_lines(source) == "a\n\nb\n\nc\n\nd\n"

    def test_leading_blanks_collapse(self):
        source = "\n\n\na\n"
        assert collapse_blank_lines(source) == "\na\n"

    def test_trailing_blanks_collapse(self):
        source = "a\n\n\n"
        assert collapse_blank_lines(source) == "a\n\n"

    def test_custom_max_consecutive(self):
        # max_consecutive=2: a run of 5 blanks -> 2.
        source = "a\n\n\n\n\n\nb\n"
        assert collapse_blank_lines(source, max_consecutive=2) == "a\n\n\nb\n"

    def test_no_trailing_newline_preserved(self):
        source = "a\n\n\n\nb"
        assert collapse_blank_lines(source) == "a\n\nb"

    def test_whitespace_only_lines_treated_as_blank(self):
        source = "a\n   \n\t\nb\n"
        assert collapse_blank_lines(source) == "a\n\nb\n"


# ═══════════════════════════════════════════════════════════════════════
# normalize_spacing
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeSpacing:
    def test_collapses_multiple_blank_lines(self):
        source = "a\n\n\n\nb\n"
        assert normalize_spacing(source) == "a\n\nb\n"

    def test_removes_trailing_whitespace(self):
        source = "x = 1   \ny = 2\t\n"
        assert normalize_spacing(source) == "x = 1\ny = 2\n"

    def test_ensures_blank_line_after_imports(self):
        source = "import os\nimport sys\ndef foo():\n    pass\n"
        expected = "import os\nimport sys\n\ndef foo():\n    pass\n"
        assert normalize_spacing(source) == expected

    def test_combined_collapses_strips_and_imports(self):
        source = (
            "import os\n"
            "import sys\n"
            "\n\n\n"
            "def foo():\n"
            "    x = 1   \n"
            "    return x\t\n"
        )
        expected = (
            "import os\n"
            "import sys\n"
            "\n"
            "def foo():\n"
            "    x = 1\n"
            "    return x\n"
        )
        assert normalize_spacing(source) == expected

    def test_ensures_blank_before_module_level_defs(self):
        source = "x = 1\ndef foo():\n    pass\nclass Bar:\n    pass\n"
        expected = "x = 1\n\ndef foo():\n    pass\n\nclass Bar:\n    pass\n"
        assert normalize_spacing(source) == expected

    def test_no_leading_blank_at_file_start(self):
        source = "def foo():\n    pass\n"
        assert normalize_spacing(source) == "def foo():\n    pass\n"

    def test_no_spurious_blank_without_imports(self):
        source = "x = 1\ny = 2\n"
        assert normalize_spacing(source) == "x = 1\ny = 2\n"

    def test_idempotent(self):
        source = "import os\n\n\n\ndef foo():\n    x = 1   \n"
        once = normalize_spacing(source)
        twice = normalize_spacing(once)
        assert once == twice
