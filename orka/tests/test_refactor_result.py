"""Tests for ``RefactorResult`` dataclass and diff computation."""

import textwrap

from orka.orchestrator import RefactorResult, _compute_diff


class TestRefactorResult:
    def test_success_result(self):
        r = RefactorResult(True, "MyClass.my_method", "/path/to/file.py", diff="@@ ...")
        assert r.success is True
        assert r.label == "MyClass.my_method"
        assert r.file_path == "/path/to/file.py"
        assert r.diff == "@@ ..."
        assert r.error is None

    def test_failure_result(self):
        r = RefactorResult(False, "my_func", "/path/to/file.py", error="Something broke")
        assert r.success is False
        assert r.error == "Something broke"
        assert r.diff == ""

    def test_default_diff_is_empty(self):
        r = RefactorResult(True, "x", "x.py")
        assert r.diff == ""

    def test_default_error_is_none(self):
        r = RefactorResult(True, "x", "x.py")
        assert r.error is None


class TestComputeDiff:
    def test_no_changes(self):
        code = "x = 1\n"
        diff = _compute_diff(code, code)
        assert diff == ""

    def test_simple_addition(self):
        before = "x = 1\n"
        after = "x = 2\n"
        diff = _compute_diff(before, after)
        assert "-x = 1" in diff
        assert "+x = 2" in diff

    def test_multi_line(self):
        before = textwrap.dedent("""\
            def foo():
                return 1
        """)
        after = textwrap.dedent("""\
            def foo():
                return 2
        """)
        diff = _compute_diff(before, after)
        assert "-    return 1" in diff
        assert "+    return 2" in diff

    def test_new_file(self):
        before = ""
        after = "x = 1\n"
        diff = _compute_diff(before, after)
        assert "+x = 1" in diff

    def test_deletion(self):
        before = "x = 1\ny = 2\n"
        after = "x = 1\n"
        diff = _compute_diff(before, after)
        assert "-y = 2" in diff
