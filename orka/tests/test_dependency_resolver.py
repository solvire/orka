"""Tests for orka.core.dependency_resolver.

Covers the five public functions consolidated from import_fixer and
graph_helpers, plus the internal stdlib/undefined-name helpers that moved
with them.  Graph DB behaviour is exercised against lightweight mock
objects backed by a real ``networkx.DiGraph`` so the node/edge semantics
match the production ``OrkaGraphDB``.
"""

from __future__ import annotations

import os

import networkx as nx
import pytest

from orka.core.dependency_resolver import (
    _detect_undefined_names,
    _stdlib_fallback,
    build_caller_constraints,
    build_dependency_map,
    resolve_symbol,
    resolve_target,
    resolve_undefined_names,
)


# ═══════════════════════════════════════════════════════════════════════
# Mock graph DB fixtures
# ═══════════════════════════════════════════════════════════════════════


class _MockGraphDB:
    """Minimal stand-in for ``OrkaGraphDB`` exposing a ``.graph`` attr."""

    def __init__(self, graph: nx.DiGraph) -> None:
        self.graph = graph


def _node(node_type: str, name: str, **extra) -> dict:
    """Build a node attribute dict matching the ingester's schema."""
    attrs = {"node_type": node_type, "name": name}
    attrs.update(extra)
    return attrs


@pytest.fixture
def sample_graph() -> _MockGraphDB:
    """A small project graph used across the resolver tests.

    Layout::

        orka/core/compiler.py
          ├─ PromptCompiler (class)
          ├─ PromptProcessor.compile (method)        ← target
          ├─ helper (function)
          └─ imports → orka.core.rule_resolver (module)
                       └─ resolve_rules (function)
                     → _private (module, excluded)
    """
    g = nx.DiGraph()

    # File + module for the compiler package
    g.add_node("File:orka/core/compiler.py", **_node("file", "orka/core/compiler.py"))
    g.add_node(
        "Class:orka.core.compiler.PromptCompiler",
        **_node("class", "PromptProcessor", file_path="orka/core/compiler.py", lineno=10),
    )
    g.add_node(
        "Method:orka.core.compiler.PromptProcessor.compile",
        **_node("method", "compile", file_path="orka/core/compiler.py", lineno=20),
    )
    g.add_node(
        "Function:orka.core.compiler.helper",
        **_node("function", "helper", file_path="orka/core/compiler.py", lineno=30),
    )

    # Imported module + one exported symbol
    g.add_node(
        "Module:orka.core.rule_resolver",
        **_node("module", "orka.core.rule_resolver"),
    )
    g.add_node(
        "Function:orka.core.rule_resolver.resolve_rules",
        **_node("function", "resolve_rules", file_path="orka/core/rule_resolver.py", lineno=5),
    )

    # Private module (must be excluded from the dependency scope)
    g.add_node("Module:_private", **_node("module", "_private"))

    # File → Module import edges
    g.add_edge("File:orka/core/compiler.py", "Module:orka.core.rule_resolver")
    g.add_edge("File:orka/core/compiler.py", "Module:_private")

    return _MockGraphDB(g)


@pytest.fixture
def callers_graph() -> _MockGraphDB:
    """Graph with predecessor edges into the target method."""
    g = nx.DiGraph()
    g.add_node(
        "Method:orka.core.compiler.PromptProcessor.compile",
        **_node("method", "compile", file_path="orka/core/compiler.py", lineno=20),
    )
    # A real caller with a resolvable module
    g.add_node(
        "Function:orka.core.compiler.caller_func",
        **_node("function", "caller_func", file_path="orka/core/compiler.py", lineno=50),
    )
    # A caller whose node ID has no module (should be skipped)
    g.add_node("BareCaller", **_node("function", "bare_caller", file_path="x.py", lineno=1))
    # A caller with no name (should be skipped)
    g.add_node("Function:orka.core.compiler.unnamed", **_node("function", ""))

    g.add_edge("Function:orka.core.compiler.caller_func", "Method:orka.core.compiler.PromptProcessor.compile")
    g.add_edge("BareCaller", "Method:orka.core.compiler.PromptProcessor.compile")
    g.add_edge("Function:orka.core.compiler.unnamed", "Method:orka.core.compiler.PromptProcessor.compile")
    return _MockGraphDB(g)


# ═══════════════════════════════════════════════════════════════════════
# resolve_symbol
# ═══════════════════════════════════════════════════════════════════════


class TestResolveSymbol:
    def test_none_graph_db_returns_none(self):
        assert resolve_symbol(None, "helper") is None

    def test_name_not_in_graph_returns_none(self, sample_graph):
        assert resolve_symbol(sample_graph, "does_not_exist") is None

    def test_function_node_resolved(self, sample_graph):
        result = resolve_symbol(sample_graph, "helper")
        assert result is not None
        assert result["name"] == "helper"
        assert result["module"] == "orka.core.compiler"
        assert result["import_path"] == "from orka.core.compiler import helper"
        assert result["node_type"] == "function"
        assert result["file_path"] == "orka/core/compiler.py"
        assert result["lineno"] == "30"

    def test_class_node_resolved(self, sample_graph):
        result = resolve_symbol(sample_graph, "PromptProcessor")
        assert result is not None
        assert result["module"] == "orka.core.compiler"
        assert result["node_type"] == "class"

    def test_method_node_resolved(self, sample_graph):
        result = resolve_symbol(sample_graph, "compile")
        assert result is not None
        assert result["node_type"] == "method"
        assert result["module"] == "orka.core.compiler"

    def test_non_symbol_node_types_ignored(self):
        g = nx.DiGraph()
        g.add_node("Module:pkg.mod", **_node("module", "helper"))
        g.add_node("File:pkg/mod.py", **_node("file", "helper"))
        assert resolve_symbol(_MockGraphDB(g), "helper") is None

    def test_same_module_takes_precedence(self):
        g = nx.DiGraph()
        g.add_node(
            "Function:pkg.a.helper",
            **_node("function", "helper", file_path="a.py", lineno=1),
        )
        g.add_node(
            "Function:pkg.b.helper",
            **_node("function", "helper", file_path="b.py", lineno=2),
        )
        # Without scope, the first match (pkg.a) wins.
        unscoped = resolve_symbol(_MockGraphDB(g), "helper")
        assert unscoped["module"] == "pkg.a"
        # Scoped to pkg.b, the same-module match wins regardless of order.
        scoped = resolve_symbol(_MockGraphDB(g), "helper", source_module="pkg.b")
        assert scoped["module"] == "pkg.b"

    def test_source_module_with_no_match_falls_back_to_any(self):
        g = nx.DiGraph()
        g.add_node(
            "Function:pkg.a.helper",
            **_node("function", "helper", file_path="a.py", lineno=1),
        )
        result = resolve_symbol(_MockGraphDB(g), "helper", source_module="pkg.zzz")
        assert result is not None
        assert result["module"] == "pkg.a"


# ═══════════════════════════════════════════════════════════════════════
# resolve_target
# ═══════════════════════════════════════════════════════════════════════


class TestResolveTarget:
    def test_none_graph_db_uses_file_path_fallback(self):
        module = resolve_target(None, "src/payments/processor.py", "process_payment")
        assert module == "src.payments.processor"

    def test_none_graph_db_with_base_dir(self):
        module = resolve_target(
            None, "/proj/src/payments/processor.py", "process_payment", base_dir="/proj",
        )
        assert module == "src.payments.processor"

    def test_graph_lookup_finds_method(self, sample_graph):
        module = resolve_target(
            sample_graph, "orka/core/compiler.py", "compile", class_name="PromptProcessor",
        )
        assert module == "orka.core.compiler"

    def test_graph_lookup_finds_function_without_class(self, sample_graph):
        module = resolve_target(sample_graph, "orka/core/compiler.py", "helper")
        assert module == "orka.core.compiler"

    def test_class_name_restricts_to_method_nodes(self, sample_graph):
        # With class_name set, only "method" nodes are considered. "helper"
        # is a function node, so the graph lookup misses and falls back to
        # the file-path heuristic.
        module = resolve_target(
            sample_graph, "orka/core/compiler.py", "helper", class_name="PromptProcessor",
        )
        assert module == "orka.core.compiler"

    def test_target_not_in_graph_falls_back_to_file_path(self, sample_graph):
        module = resolve_target(sample_graph, "orka/core/compiler.py", "missing_method")
        assert module == "orka.core.compiler"

    def test_file_path_suffix_matching(self, sample_graph):
        # An absolute path ending in the node's file_path should still match.
        module = resolve_target(
            sample_graph,
            os.path.join("/home", "user", "proj", "orka", "core", "compiler.py"),
            "compile",
            class_name="PromptProcessor",
        )
        assert module == "orka.core.compiler"

    def test_empty_file_path_returns_none(self):
        assert resolve_target(None, "", "compile") is None


# ═══════════════════════════════════════════════════════════════════════
# build_dependency_map
# ═══════════════════════════════════════════════════════════════════════


class TestBuildDependencyMap:
    def test_none_graph_db_returns_empty(self):
        assert build_dependency_map("orka/core/compiler.py", "compile", "PromptProcessor", None) == []

    def test_collects_same_module_and_imported_symbols(self, sample_graph):
        deps = build_dependency_map(
            "orka/core/compiler.py", "compile", "PromptProcessor", sample_graph,
        )
        names = [d["name"] for d in deps]
        # Same-module siblings + imported-module symbol, sorted by name.
        assert names == ["PromptProcessor", "compile", "helper", "resolve_rules"]

        resolve_rules = next(d for d in deps if d["name"] == "resolve_rules")
        assert resolve_rules["module"] == "orka.core.rule_resolver"
        assert resolve_rules["import_path"] == "from orka.core.rule_resolver import resolve_rules"
        assert resolve_rules["node_type"] == "function"

    def test_private_modules_excluded(self, sample_graph):
        deps = build_dependency_map(
            "orka/core/compiler.py", "compile", "PromptProcessor", sample_graph,
        )
        # _private module has no symbols anyway, but ensure no record leaks.
        assert all(d["module"] != "_private" for d in deps)

    def test_each_record_has_required_keys(self, sample_graph):
        deps = build_dependency_map(
            "orka/core/compiler.py", "compile", "PromptProcessor", sample_graph,
        )
        for d in deps:
            assert set(d.keys()) == {
                "name", "module", "import_path", "node_type", "file_path", "lineno",
            }

    def test_unresolvable_target_returns_empty(self, sample_graph):
        # Target module cannot be resolved (empty source file → empty module),
        # so the dependency map is empty.
        assert build_dependency_map("", "compile", "PromptProcessor", sample_graph) == []

    def test_static_deps_added_when_missing(self, sample_graph):
        deps = build_dependency_map(
            "orka/core/compiler.py",
            "compile",
            "PromptProcessor",
            sample_graph,
            static_deps={"private_helper": "orka.core.compiler"},
        )
        names = [d["name"] for d in deps]
        assert "private_helper" in names
        rec = next(d for d in deps if d["name"] == "private_helper")
        assert rec["module"] == "orka.core.compiler"
        assert rec["node_type"] == "function"
        assert rec["lineno"] == ""

    def test_static_deps_do_not_override_existing(self, sample_graph):
        deps = build_dependency_map(
            "orka/core/compiler.py",
            "compile",
            "PromptProcessor",
            sample_graph,
            static_deps={"helper": "some.other.module"},
        )
        helper = next(d for d in deps if d["name"] == "helper")
        # The graph-resolved module wins; the static override is ignored.
        assert helper["module"] == "orka.core.compiler"


# ═══════════════════════════════════════════════════════════════════════
# build_caller_constraints
# ═══════════════════════════════════════════════════════════════════════


class TestBuildCallerConstraints:
    def test_none_graph_db_returns_empty(self):
        assert build_caller_constraints("orka/core/compiler.py", "compile", "PromptProcessor", None) == []

    def test_target_not_found_returns_empty(self, sample_graph):
        assert build_caller_constraints(
            "orka/core/compiler.py", "missing", "PromptProcessor", sample_graph,
        ) == []

    def test_collects_predecessors(self, callers_graph):
        callers = build_caller_constraints(
            "orka/core/compiler.py", "compile", "PromptProcessor", callers_graph,
        )
        assert len(callers) == 1
        caller = callers[0]
        assert caller["name"] == "caller_func"
        assert caller["module"] == "orka.core.compiler"
        assert caller["import_path"] == "from orka.core.compiler import caller_func"
        assert caller["file_path"] == "orka/core/compiler.py"
        assert caller["lineno"] == "50"

    def test_callers_without_module_or_name_skipped(self, callers_graph):
        callers = build_caller_constraints(
            "orka/core/compiler.py", "compile", "PromptProcessor", callers_graph,
        )
        names = [c["name"] for c in callers]
        assert "bare_caller" not in names
        assert "" not in names


# ═══════════════════════════════════════════════════════════════════════
# resolve_undefined_names
# ═══════════════════════════════════════════════════════════════════════


class TestResolveUndefinedNames:
    def test_no_undefined_names_returns_empty(self):
        assert resolve_undefined_names("x = 1\nprint(x)") == {}

    def test_syntax_error_returns_empty(self):
        assert resolve_undefined_names("def broken(") == {}

    def test_graph_resolved_name(self, sample_graph):
        source = "result = helper()"
        resolved = resolve_undefined_names(source, graph_db=sample_graph)
        assert resolved == {"helper": ("orka.core.compiler", "helper")}

    def test_stdlib_fallback_name(self):
        source = "print(os.getcwd())"
        resolved = resolve_undefined_names(source)
        assert resolved == {"os": ("os", None)}

    def test_unknown_name_omitted(self, sample_graph):
        source = "print(totally_made_up_xyz_123())"
        resolved = resolve_undefined_names(source, graph_db=sample_graph)
        assert resolved == {}

    def test_graph_takes_precedence_over_stdlib(self):
        # "os" resolves to a graph node when present, so the stdlib fallback
        # is not used.
        g = nx.DiGraph()
        g.add_node(
            "Function:myproj.os",
            **_node("function", "os", file_path="myproj/os.py", lineno=1),
        )
        resolved = resolve_undefined_names("print(os)", graph_db=_MockGraphDB(g))
        assert resolved == {"os": ("myproj", "os")}

    def test_mixed_resolution(self, sample_graph):
        source = "a = helper()\nb = os.getcwd()\nc = unknown_thing_xyz()\n"
        resolved = resolve_undefined_names(source, graph_db=sample_graph)
        assert resolved == {
            "helper": ("orka.core.compiler", "helper"),
            "os": ("os", None),
        }


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers (moved from import_fixer)
# ═══════════════════════════════════════════════════════════════════════


class TestStdlibFallback:
    def test_known_module(self):
        assert _stdlib_fallback("os") == ("os", None)
        assert _stdlib_fallback("asyncio") == ("asyncio", None)

    def test_unknown_module(self):
        assert _stdlib_fallback("requests") == (None, None)

    def test_empty_string(self):
        assert _stdlib_fallback("") == (None, None)

    def test_case_sensitive(self):
        assert _stdlib_fallback("OS") == (None, None)

    def test_submodule_not_matched(self):
        assert _stdlib_fallback("os.path") == (None, None)


class TestDetectUndefinedNames:
    def test_empty_source(self):
        assert _detect_undefined_names("") == []

    def test_no_undefined(self):
        assert _detect_undefined_names("x = 1\nprint(x)") == []

    def test_single_undefined(self):
        assert _detect_undefined_names("print(undefined_var)") == ["undefined_var"]

    def test_multiple_sorted_dedup(self):
        # a, b undefined; c undefined (used in print); e undefined (d is assigned).
        source = "z = a + b\nprint(c)\nd = e\n"
        assert _detect_undefined_names(source) == ["a", "b", "c", "e"]

    def test_syntax_error_returns_empty(self):
        assert _detect_undefined_names("def broken(") == []

    def test_builtins_not_reported(self):
        assert _detect_undefined_names("print(len([1,2,3]))") == []
