"""Tests for orka.core.locator — the LibCST node location & signature module.

Covers find_method, find_class, get_signature, extract_docstring (CST) and
extract_docstring_regex.  Async methods are exercised to confirm the LibCST
flat-grammar rule (async == FunctionDef with ``asynchronous is not None``).
"""

import libcst as cst

from orka.core.locator import (
    FunctionSignature,
    extract_docstring,
    extract_docstring_regex,
    find_class,
    find_method,
    get_signature,
)


# ── helpers ────────────────────────────────────────────────────────────


def _tree(source: str) -> cst.Module:
    return cst.parse_module(source)


# ═══════════════════════════════════════════════════════════════════════
# find_method
# ═══════════════════════════════════════════════════════════════════════


class TestFindMethod:
    def test_simple_class_method(self):
        tree = _tree(
            "class Service:\n"
            "    def greet(self, name):\n"
            "        return name\n"
        )
        node = find_method(tree, "Service", "greet")
        assert node is not None
        assert node.name.value == "greet"

    def test_nested_class_dotted(self):
        tree = _tree(
            "class Outer:\n"
            "    class Inner:\n"
            "        def method(self):\n"
            "            return 1\n"
            "    def outer_method(self):\n"
            "        return 2\n"
        )
        node = find_method(tree, "Outer.Inner", "method")
        assert node is not None
        assert node.name.value == "method"

        # A method directly on Outer must not match the Inner lookup.
        assert find_method(tree, "Outer.Inner", "outer_method") is None

    def test_standalone_function(self):
        tree = _tree(
            "def helper(x):\n"
            "    return x\n"
        )
        node = find_method(tree, None, "helper")
        assert node is not None
        assert node.name.value == "helper"

    def test_async_method(self):
        tree = _tree(
            "class Fetcher:\n"
            "    async def fetch(self, url):\n"
            "        return await get(url)\n"
        )
        node = find_method(tree, "Fetcher", "fetch")
        assert node is not None
        # Flat grammar: async is a FunctionDef with asynchronous is not None.
        assert isinstance(node, cst.FunctionDef)
        assert node.asynchronous is not None

    def test_method_not_found(self):
        tree = _tree(
            "class Service:\n"
            "    def greet(self):\n"
            "        return 1\n"
        )
        assert find_method(tree, "Service", "missing") is None

    def test_class_not_found(self):
        tree = _tree(
            "class Service:\n"
            "    def greet(self):\n"
            "        return 1\n"
        )
        assert find_method(tree, "NoClass", "greet") is None

    def test_method_name_collision_across_classes(self):
        """Two classes with the same method name — only the target matches."""
        tree = _tree(
            "class A:\n"
            "    def validate(self):\n"
            "        return 'a'\n"
            "class B:\n"
            "    def validate(self):\n"
            "        return 'b'\n"
        )
        node = find_method(tree, "B", "validate")
        assert node is not None
        assert cst.Module(body=[node]).code.count("'b'") == 1

    def test_standalone_does_not_match_method(self):
        """A standalone lookup must not match a method inside a class."""
        tree = _tree(
            "class Worker:\n"
            "    def do_work(self):\n"
            "        return 'work'\n"
        )
        assert find_method(tree, None, "do_work") is None


# ═══════════════════════════════════════════════════════════════════════
# find_class
# ═══════════════════════════════════════════════════════════════════════


class TestFindClass:
    def test_simple_class(self):
        tree = _tree(
            "class Foo:\n"
            "    def bar(self):\n"
            "        return 1\n"
        )
        node = find_class(tree, "Foo")
        assert node is not None
        assert node.name.value == "Foo"

    def test_nested_class(self):
        tree = _tree(
            "class Outer:\n"
            "    class Inner:\n"
            "        pass\n"
        )
        node = find_class(tree, "Inner")
        assert node is not None
        assert node.name.value == "Inner"

    def test_not_found(self):
        tree = _tree("class Foo:\n    pass\n")
        assert find_class(tree, "Bar") is None


# ═══════════════════════════════════════════════════════════════════════
# get_signature
# ═══════════════════════════════════════════════════════════════════════


class TestGetSignature:
    def test_simple_params(self):
        node = find_method(_tree("def f(a, b):\n    return a\n"), None, "f")
        sig = get_signature(node)
        assert sig.name == "f"
        assert sig.params == ["a", "b"]
        assert sig.return_annotation == ""
        assert sig.is_async is False

    def test_typed_params(self):
        node = find_method(
            _tree("def f(a: int, b: str):\n    return a\n"), None, "f"
        )
        sig = get_signature(node)
        assert sig.params == ["a: int", "b: str"]

    def test_return_annotation(self):
        node = find_method(_tree("def f() -> bool:\n    return True\n"), None, "f")
        sig = get_signature(node)
        assert sig.return_annotation == "bool"

    def test_async_method(self):
        node = find_method(
            _tree("async def f():\n    return 1\n"), None, "f"
        )
        sig = get_signature(node)
        assert sig.is_async is True

    def test_multiple_decorators(self):
        tree = _tree(
            "@dec1\n"
            "@dec2(flag=True)\n"
            "def f():\n"
            "    return 1\n"
        )
        node = find_method(tree, None, "f")
        sig = get_signature(node)
        assert sig.decorator_count == 2
        # Decorators are rendered in source order (top to bottom).
        assert sig.decorators == ["dec1", "dec2(flag=True)"]

    def test_no_decorators(self):
        node = find_method(_tree("def f():\n    return 1\n"), None, "f")
        sig = get_signature(node)
        assert sig.decorator_count == 0
        assert sig.decorators == []

    def test_docstring_present(self):
        node = find_method(
            _tree(
                "def f():\n"
                '    """A docstring."""\n'
                "    return 1\n"
            ),
            None,
            "f",
        )
        sig = get_signature(node)
        assert sig.docstring == "A docstring."

    def test_docstring_absent(self):
        node = find_method(
            _tree("def f():\n    return 1\n"), None, "f"
        )
        sig = get_signature(node)
        assert sig.docstring == ""

    def test_returns_dataclass_instance(self):
        node = find_method(_tree("def f():\n    pass\n"), None, "f")
        sig = get_signature(node)
        assert isinstance(sig, FunctionSignature)


# ═══════════════════════════════════════════════════════════════════════
# extract_docstring (CST)
# ═══════════════════════════════════════════════════════════════════════


class TestExtractDocstring:
    def test_present(self):
        node = find_method(
            _tree(
                "def f():\n"
                '    """Hello world."""\n'
                "    return 1\n"
            ),
            None,
            "f",
        )
        assert extract_docstring(node.body) == "Hello world."

    def test_absent(self):
        node = find_method(_tree("def f():\n    return 1\n"), None, "f")
        assert extract_docstring(node.body) is None

    def test_triple_single_quote(self):
        node = find_method(
            _tree(
                "def f():\n"
                "    '''Single quoted doc.'''\n"
                "    return 1\n"
            ),
            None,
            "f",
        )
        assert extract_docstring(node.body) == "Single quoted doc."


# ═══════════════════════════════════════════════════════════════════════
# extract_docstring_regex
# ═══════════════════════════════════════════════════════════════════════


class TestExtractDocstringRegex:
    def test_present(self):
        source = '"""Module level doc."""\nx = 1\n'
        assert extract_docstring_regex(source) == "Module level doc."

    def test_absent(self):
        assert extract_docstring_regex("x = 1\nreturn x") is None

    def test_triple_single_quote(self):
        source = "'''Single quote doc.'''\nx = 1\n"
        assert extract_docstring_regex(source) == "Single quote doc."

    def test_empty_string(self):
        assert extract_docstring_regex("") is None

    def test_multiline_docstring(self):
        source = '"""\nLine one.\nLine two.\n"""\nx = 1\n'
        assert extract_docstring_regex(source) == "Line one.\nLine two."
