from orka.core.dependency_resolver import _detect_undefined_names
def test__detect_undefined_names_empty_source():
    """An empty source string should return an empty list."""
    result = _detect_undefined_names("")
    assert result == []


def test__detect_undefined_names_no_undefined_names():
    """Source with all names defined should return an empty list."""
    source = """
x = 1
y = x + 2
print(y)
"""
    result = _detect_undefined_names(source)
    assert result == []


def test__detect_undefined_names_single_undefined_name():
    """Source with one undefined name should return that name in a list."""
    source = "print(undefined_var)"
    result = _detect_undefined_names(source)
    assert result == ["undefined_var"]


def test__detect_undefined_names_multiple_undefined_names():
    """Source with multiple undefined names should return them sorted and deduplicated."""
    source = """
z = a + b
print(c)
d = e
"""
    result = _detect_undefined_names(source)
    assert result == ["a", "b", "c", "e"]


def test__detect_undefined_names_duplicate_undefined_names():
    """Duplicate undefined names should appear only once in the result."""
    source = """
x = undefined_var
y = undefined_var
"""
    result = _detect_undefined_names(source)
    assert result == ["undefined_var"]


def test__detect_undefined_names_builtin_names_not_reported():
    """Built-in names like 'print' or 'len' should not be reported as undefined."""
    source = """
print(len([1, 2, 3]))
"""
    result = _detect_undefined_names(source)
    assert result == []


def test__detect_undefined_names_imported_names_not_reported():
    """Names that are imported should not be reported as undefined."""
    source = """
import os
print(os.path.join("a", "b"))
"""
    result = _detect_undefined_names(source)
    assert result == []


def test__detect_undefined_names_syntax_error_returns_empty_list():
    """Source with a syntax error should return an empty list."""
    source = "def broken("
    result = _detect_undefined_names(source)
    assert result == []


def test__detect_undefined_names_with_file_path():
    """Providing a file_path should not affect the result for valid code."""
    source = "print(undefined_var)"
    result = _detect_undefined_names(source, file_path="test.py")
    assert result == ["undefined_var"]


def test__detect_undefined_names_syntax_error_with_file_path():
    """Syntax error with a file_path should still return an empty list."""
    source = "if True"
    result = _detect_undefined_names(source, file_path="test.py")
    assert result == []


def test__detect_undefined_names_class_definition_with_undefined():
    """Undefined names inside a class definition should be detected."""
    source = """
class MyClass:
    def method(self):
        return undefined_var
"""
    result = _detect_undefined_names(source)
    assert result == ["undefined_var"]


def test__detect_undefined_names_function_definition_with_undefined():
    """Undefined names inside a function definition should be detected."""
    source = """
def my_func():
    return undefined_var
"""
    result = _detect_undefined_names(source)
    assert result == ["undefined_var"]


def test__detect_undefined_names_undefined_in_comprehension():
    """Undefined names used in list comprehensions should be detected."""
    source = "[x for x in undefined_iterable]"
    result = _detect_undefined_names(source)
    assert result == ["undefined_iterable"]


def test__detect_undefined_names_undefined_in_lambda():
    """Undefined names used in lambda expressions should be detected."""
    source = "f = lambda x: x + undefined_var"
    result = _detect_undefined_names(source)
    assert result == ["undefined_var"]


def test__detect_undefined_names_undefined_in_global_scope():
    """Undefined names at the module level should be detected."""
    source = "result = some_undefined_function()"
    result = _detect_undefined_names(source)
    assert result == ["some_undefined_function"]
