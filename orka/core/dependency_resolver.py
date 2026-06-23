import logging
import ast
import os
from typing import Any

from orka.core.module_resolver import file_to_module, node_id_to_module

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Graph-based symbol resolution
#
# Consolidates the duplicated graph-lookup logic that previously lived in
# orka.core.import_fixer (_lookup_in_graph, _from_graph, _resolve_undefined,
# _stdlib_fallback, _detect_undefined_names) and orka.operations.graph_helpers
# (resolve_one_dependency, find_target_node, resolve_target_module,
# build_dependency_map, build_caller_constraints).
#
# This module is the "resolving" layer of the 3-way import split.  It depends
# only on orka.core.module_resolver and the graph DB interface (an object
# exposing a ``graph`` attribute that behaves like a networkx.DiGraph).  No
# other orka-internal imports are used — callers pass a ``base_dir`` when they
# need project-root-aware file->module conversion.
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════

_SYMBOL_NODE_TYPES = ("class", "function", "method")
"""Node types that represent importable symbols (not files/modules)."""


def _make_record(name: str, attrs: dict[str, Any], node_module: str) -> dict[str, str]:
    """Build a structured resolution record for a graph node.

    Each record exposes the keys consumed by the prompt-facing renderers
    and the import injector: ``name``, ``module``, ``import_path``,
    ``node_type``, ``file_path``, ``lineno``.
    """
    return {
        "name": name,
        "module": node_module,
        "import_path": f"from {node_module} import {name}",
        "node_type": attrs.get("node_type", "unknown"),
        "file_path": attrs.get("file_path", ""),
        "lineno": str(attrs.get("lineno", "")),
    }


def _find_target_node(
    graph_db: object,
    source_file: str,
    method_name: str,
    class_name: str | None,
) -> str | None:
    """Locate the graph node ID for the target function or method.

    Searches for a node whose ``name`` matches *method_name*, whose
    ``node_type`` is ``"function"`` or ``"method"`` (only ``"method"``
    when *class_name* is provided), and whose ``file_path`` suffix matches
    *source_file*.  Nodes without a ``file_path`` are skipped.

    Returns the node ID or ``None``.
    """
    target_types: tuple[str, ...] = ("function", "method") if not class_name else ("method",)
    for node, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("node_type") not in target_types:
            continue
        if attrs.get("name") != method_name:
            continue
        attr_file = attrs.get("file_path", "")
        if attr_file and source_file.endswith(attr_file):
            return node
    return None


# ═══════════════════════════════════════════════════════════════════════
# Public API — symbol resolution
# ═══════════════════════════════════════════════════════════════════════


def resolve_symbol(
    graph_db: object | None,
    name: str,
    source_module: str | None = None,
) -> dict[str, str] | None:
    """Resolve a single symbol name to its module and import path.

    Replaces ``_lookup_in_graph`` (import_fixer) and
    ``resolve_one_dependency`` (graph_helpers).

    Searches in this order:

    1. **Same module** — when *source_module* is provided, the first
       class/function/method node whose ``name`` matches and whose module
       (extracted from the node ID) equals *source_module*.
    2. **Any module** — the first class/function/method node whose
       ``name`` matches and whose module can be extracted.

    Returns a record ``{name, module, import_path, node_type, file_path,
    lineno}`` or ``None`` when no match is found (or *graph_db* is
    ``None``).
    """
    if graph_db is None:
        return None

    # Pass 1: same-module candidates first (only when scoped).
    if source_module:
        for node, attrs in graph_db.graph.nodes(data=True):
            if attrs.get("node_type") not in _SYMBOL_NODE_TYPES:
                continue
            if attrs.get("name") != name:
                continue
            node_module = node_id_to_module(node)
            if node_module and node_module == source_module:
                return _make_record(name, attrs, node_module)

    # Pass 2: broader graph search — any module.
    for node, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("node_type") not in _SYMBOL_NODE_TYPES:
            continue
        if attrs.get("name") != name:
            continue
        node_module = node_id_to_module(node)
        if node_module:
            return _make_record(name, attrs, node_module)

    return None


def resolve_target(
    graph_db: object | None,
    source_file: str,
    method_name: str,
    class_name: str | None = None,
    base_dir: str = "",
) -> str | None:
    """Resolve the dotted module path for a target method/function.

    Replaces ``find_target_node`` + ``resolve_target_module``
    (graph_helpers).

    Strategy 1: **Graph DB lookup** — locate the target's node ID via
    :func:`_find_target_node` and extract the module path.
    Strategy 2: **File-path heuristic** — convert *source_file* to a
    module path via :func:`file_to_module` using *base_dir* as the
    project root.

    Returns the dotted module path (e.g. ``"orka.core.compiler"``) or
    ``None``.
    """
    # Strategy 1: graph DB
    if graph_db is not None:
        target_node = _find_target_node(graph_db, source_file, method_name, class_name)
        if target_node:
            module_path = node_id_to_module(target_node)
            if module_path:
                return module_path

    # Strategy 2: file-path heuristic
    module_path = file_to_module(source_file, base_dir)
    return module_path if module_path else None


# ═══════════════════════════════════════════════════════════════════════
# Public API — dependency / caller maps
# ═══════════════════════════════════════════════════════════════════════


def build_dependency_map(
    source_file: str,
    method_name: str,
    class_name: str | None,
    graph_db: object | None,
    static_deps: dict[str, str] | None = None,
    base_dir: str = "",
) -> list[dict[str, str]]:
    """Build a structured dependency map of functions/classes the target calls.

    Moved from ``graph_helpers``.  The map is scoped to:

    1. **Same-module siblings** — functions/classes in the same module as
       the target (resolved via :func:`resolve_target`).
    2. **Imported modules** — modules the target's file imports (via
       ``File → Module`` edges in the graph), resolved to their exported
       functions/classes.
    3. **Static overrides** (*static_deps*) — caller-provided overrides
       for known dependencies (e.g. private helpers in the same file).

    Each entry has keys: ``name``, ``module``, ``import_path``,
    ``node_type``, ``file_path``, ``lineno``.

    Returns an empty list if *graph_db* is ``None`` or the target's module
    cannot be resolved.
    """
    if graph_db is None:
        return []

    deps: dict[str, dict[str, str]] = {}
    source_module = resolve_target(
        graph_db, source_file, method_name, class_name, base_dir=base_dir,
    )
    if not source_module:
        return []

    # ── 0. Locate the file node for the target's source file ──────────
    file_node_id: str | None = None
    for node, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("node_type") == "file":
            attr_file = attrs.get("name", "")
            if source_file.endswith(attr_file) or attr_file.endswith(
                os.path.basename(source_file)
            ):
                file_node_id = node
                break

    # ── 1. Collect scoped module paths ────────────────────────────────
    target_modules: set[str] = {source_module}

    if file_node_id:
        for successor in graph_db.graph.successors(file_node_id):
            succ_attrs = graph_db.graph.nodes[successor]
            if succ_attrs.get("node_type") == "module":
                mod_name = succ_attrs.get("name", "")
                if mod_name and not mod_name.startswith("_"):
                    target_modules.add(mod_name)

    # ── 2. Resolve every function/method/class node within those modules ──
    for node, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("node_type") not in ("function", "method", "class"):
            continue
        node_module = node_id_to_module(node)
        if node_module and node_module in target_modules:
            name = attrs["name"]
            if name not in deps:
                deps[name] = _make_record(name, attrs, node_module)

    # ── 3. Static overrides ───────────────────────────────────────────
    if static_deps:
        for name, module in static_deps.items():
            if name not in deps:
                deps[name] = {
                    "name": name,
                    "module": module,
                    "import_path": f"from {module} import {name}",
                    "node_type": "function",
                    "file_path": source_file,
                    "lineno": "",
                }

    return sorted(deps.values(), key=lambda d: d["name"])


def build_caller_constraints(
    source_file: str,
    method_name: str,
    class_name: str | None,
    graph_db: object | None,
) -> list[dict[str, str]]:
    """Build a structured list of callers that depend on the target.

    Moved from ``graph_helpers``.  Uses the graph DB's predecessor edges
    to find every node that connects to the target.  Each entry has keys:
    ``name``, ``module``, ``import_path``, ``file_path``, ``lineno``.

    Returns an empty list if *graph_db* is ``None`` or the target has no
    callers in the graph.
    """
    if graph_db is None:
        return []

    target_node = _find_target_node(graph_db, source_file, method_name, class_name)
    if not target_node:
        return []

    callers: list[dict[str, str]] = []
    for caller_node in graph_db.graph.predecessors(target_node):
        caller_attrs = graph_db.graph.nodes[caller_node]
        caller_module = node_id_to_module(caller_node)
        caller_name = caller_attrs.get("name", "")
        if caller_module and caller_name:
            callers.append({
                "name": caller_name,
                "module": caller_module,
                "import_path": f"from {caller_module} import {caller_name}",
                "file_path": caller_attrs.get("file_path", ""),
                "lineno": str(caller_attrs.get("lineno", "")),
            })

    return callers


# ═══════════════════════════════════════════════════════════════════════
# Public API — undefined-name resolution
# ═══════════════════════════════════════════════════════════════════════


_STDLIB_MODULES = frozenset({
    "os", "sys", "re", "json", "math", "time", "datetime",
    "collections", "itertools", "functools", "pathlib",
    "typing", "uuid", "hashlib", "hmac", "base64",
    "subprocess", "shutil", "tempfile", "csv", "io",
    "abc", "enum", "dataclasses", "copy", "textwrap",
    "logging", "warnings", "fractions", "decimal",
    "random", "statistics", "inspect", "pprint",
    "threading", "multiprocessing", "concurrent",
    "asyncio", "socket", "http", "urllib",
    "xml", "html", "email", "string", "pickle",
    "sqlite3", "configparser", "argparse", "fileinput",
    "glob", "fnmatch", "linecache", "bisect",
    "heapq", "operator", "weakref", "types",
})


def _stdlib_fallback(name: str) -> tuple[str | None, str | None]:
    """Check if *name* is a known stdlib module.

    Returns ``(name, None)`` for a bare ``import <name>``, or
    ``(None, None)`` if unknown.
    """
    if name in _STDLIB_MODULES:
        return name, None
    return None, None


def _detect_undefined_names(source: str, file_path: str = "") -> list[str]:
    """Return a sorted, deduplicated list of undefined names in *source*.

    Uses ``pyflakes`` under the hood (same engine used by IDEs).  Returns
    an empty list when the source cannot be parsed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        logger.debug("Cannot parse source for auto-import — syntax error.")
        return []

    from pyflakes.checker import Checker
    from pyflakes.messages import UndefinedName

    checker = Checker(tree, file_path or "<string>")
    names: set[str] = set()
    for msg in checker.messages:
        if isinstance(msg, UndefinedName):
            # message_args is a tuple of (name,)
            names.add(msg.message_args[0])
    return sorted(names)


def resolve_undefined_names(
    source: str,
    graph_db: object | None = None,
    file_path: str = "",
) -> dict[str, tuple[str, str | None]]:
    """Detect undefined names via pyflakes and resolve them.

    Replaces ``_resolve_undefined`` + ``_detect_undefined_names`` +
    ``_stdlib_fallback`` (import_fixer).

    Resolution order for each detected name:

    1. **Graph DB lookup** — :func:`resolve_symbol` searches for a
       ``Class:``, ``Function:``, or ``Method:`` node whose ``name``
       matches, yielding ``from <module> import <name>``.
    2. **Stdlib heuristic** — :func:`_stdlib_fallback` emits a bare
       ``import <name>`` for known Python stdlib modules.

    Returns ``{name: (module, obj_or_None)}`` — ``obj`` is the symbol to
    import (``None`` for bare ``import <module>``).  Names that cannot be
    resolved are omitted.
    """
    names = _detect_undefined_names(source, file_path)
    if not names:
        return {}

    resolved: dict[str, tuple[str, str | None]] = {}
    for name in names:
        module, obj = None, None

        # Strategy A: Graph DB
        if graph_db is not None:
            hit = resolve_symbol(graph_db, name)
            if hit:
                module = hit["module"]
                obj = hit["name"]

        # Strategy B: Stdlib fallback
        if module is None:
            module, obj = _stdlib_fallback(name)

        if module:
            resolved[name] = (module, obj)
        else:
            logger.debug("Could not resolve import for '%s' — skipping", name)

    return resolved
