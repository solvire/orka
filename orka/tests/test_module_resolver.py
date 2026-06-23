"""Tests for orka.core.module_resolver.

Covers the two consolidated helpers that replaced the duplicated
``_module_from_node_id`` / ``module_from_node_id`` / ``path_to_module``
functions previously spread across import_fixer, graph_helpers, cascade,
and transplanter.
"""

import pytest

from orka.core.module_resolver import file_to_module, node_id_to_module


# ═══════════════════════════════════════════════════════════════════════
# node_id_to_module
# ═══════════════════════════════════════════════════════════════════════


def test_node_id_class_strips_last_part():
    assert node_id_to_module("Class:myapp.models.User") == "myapp.models"


def test_node_id_function_strips_last_part():
    assert node_id_to_module("Function:app.helpers.calc") == "app.helpers"


def test_node_id_method_strips_class_and_method():
    # Method: nodes are "module.ClassName.method" — strip last 2 parts.
    assert (
        node_id_to_module("Method:orka.core.compiler.PromptCompiler.compile")
        == "orka.core.compiler"
    )


def test_node_id_no_colon_returns_none():
    assert node_id_to_module("NoColonHere") is None


def test_node_id_empty_after_prefix_returns_none():
    assert node_id_to_module("Class:") is None


def test_node_id_single_part_returns_none():
    assert node_id_to_module("Class:Foo") is None


def test_node_id_method_too_few_parts_returns_none():
    # Method: requires at least 3 parts (module.Class.method).
    assert node_id_to_module("Method:pkg.func") is None


def test_node_id_function_two_parts_strips_to_module():
    assert node_id_to_module("Function:pkg.func") == "pkg"


def test_node_id_method_three_parts():
    assert node_id_to_module("Method:pkg.Cls.method") == "pkg"


def test_node_id_unknown_prefix_treated_as_class_or_function():
    # Any non-Method prefix strips a single part.
    assert node_id_to_module("Other:deeply.nested.Thing") == "deeply.nested"


# ═══════════════════════════════════════════════════════════════════════
# file_to_module
# ═══════════════════════════════════════════════════════════════════════


def test_file_to_module_absolute_with_base_dir():
    assert (
        file_to_module("/home/proj/src/payments/processor.py", "/home/proj")
        == "src.payments.processor"
    )


def test_file_to_module_relative_without_base_dir():
    assert file_to_module("payments/processor.py") == "payments.processor"


def test_file_to_module_init_py_collapses_to_package():
    assert (
        file_to_module("/home/proj/src/payments/__init__.py", "/home/proj")
        == "src.payments"
    )


def test_file_to_module_absolute_no_base_dir():
    # No base_dir -> the whole path (minus leading sep) becomes the module.
    assert file_to_module("/home/proj/src/app.py") == "home.proj.src.app"


def test_file_to_module_empty_string():
    assert file_to_module("") == ""


def test_file_to_module_strips_py_extension():
    assert file_to_module("a/b/c.py") == "a.b.c"


def test_file_to_module_no_py_extension():
    assert file_to_module("payments/processor") == "payments.processor"


def test_file_to_module_nested_init():
    assert (
        file_to_module("/proj/pkg/sub/__init__.py", "/proj") == "pkg.sub"
    )


def test_file_to_module_base_dir_not_prefix_keeps_full_path():
    # When base_dir is not actually a prefix, it is ignored.
    assert (
        file_to_module("/elsewhere/app.py", "/proj") == "elsewhere.app"
    )
