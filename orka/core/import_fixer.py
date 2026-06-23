"""Import resolution for generated test code and auto-import for refactored code.

Provides two entry points:

- ``resolve_import(...)`` — Given a source file path and a target (class or
  function), produces the ``from ... import ...`` statement needed to
  reference that target in a test file.  Used by the **testgen** pipeline.
- ``auto_import(...)`` — Scans refactored source for undefined names,
  resolves them via the Graph DB, and injects the correct imports at the
  top of the file via LibCST's ``AddImportsVisitor``.
  Used by the **refactor** pipeline after a body swap.
"""

import ast
import logging
import os
from typing import Optional
from orka.core.module_resolver import node_id_to_module, file_to_module

import libcst as cst
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Public API: auto-import (used by refactor pipeline)
# ═══════════════════════════════════════════════════════════════════════


def auto_import(
    source: str,
    file_path: str = "",
    graph_db: Optional[object] = None,
) -> str:
    """Detect undefined names and inject the correct imports.

    Runs after a LibCST body swap (``preview_patch``).  Uses:

    1. **pyflakes** — to find ``UndefinedName`` messages in the patched source.
    2. **Orka Graph DB** — to resolve each undefined symbol to its canonical
       ``from <module> import <name>`` path.
    3. **LibCST's ``AddImportsVisitor``** — to inject the imports at the
       correct position, deduplicating against any that already exist.

    Parameters
    ----------
    source
        The full file source (after patching).
    file_path
        The file path, used for logging context only.
    graph_db
        An ``OrkaGraphDB`` instance.  If ``None``, only stdlib/module-level
        fallback heuristics are used (``import <name>`` for names that
        match stdlib modules).

    Returns
    -------
    str
        The source with imports added at the top.  If no undefined names
        are detected, returns the source unchanged.
    """
    undefined_names = _detect_undefined_names(source, file_path)
    if not undefined_names:
        return source

    resolved = _resolve_undefined(undefined_names, graph_db)
    if not resolved:
        return source

    return _inject_imports(source, resolved)


# ═══════════════════════════════════════════════════════════════════════
# Step 1 — Detect undefined names via pyflakes
# ═══════════════════════════════════════════════════════════════════════


def _detect_undefined_names(source: str, file_path: str = "") -> list[str]:
    """Return a sorted, deduplicated list of undefined names in *source*.

    Uses ``pyflakes`` under the hood (same engine used by IDEs).
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


# ═══════════════════════════════════════════════════════════════════════
# Step 2 — Resolve undefined names via Graph DB + fallbacks
# ═══════════════════════════════════════════════════════════════════════


def _resolve_undefined(
    names: list[str],
    graph_db: Optional[object],
) -> dict[str, tuple[str, str | None]]:
    """Map each undefined name to ``(module, obj_or_None)``.

    Resolution order for each name:

    1. **Graph DB lookup** — search for ``Class:``, ``Function:``,
       or ``Method:`` nodes whose ``name`` matches.
    2. **Stdlib heuristic** — if the name is a known Python stdlib
       module, emit ``import <name>``.
    """
    resolved: dict[str, tuple[str, str | None]] = {}

    for name in names:
        module, obj = None, None

        # Strategy A: Graph DB
        if graph_db is not None:
            module, obj = _lookup_in_graph(graph_db, name)

        # Strategy B: Stdlib fallback
        if module is None:
            module, obj = _stdlib_fallback(name)

        if module:
            resolved[name] = (module, obj)
        else:
            logger.debug("Could not resolve import for '%s' — skipping", name)

    return resolved


def _lookup_in_graph(
    graph_db: object,
    name: str,
) -> tuple[Optional[str], Optional[str]]:
    """Search the graph DB for a ``Class:``, ``Function:``, or ``Method:``
    node whose ``name`` attribute matches *name*.

    Returns ``(module_path, obj_name)`` or ``(None, None)``.
    """
    for node_id, attrs in graph_db.graph.nodes(data=True):
        node_type = attrs.get("node_type")
        if node_type not in ("class", "function", "method"):
            continue
        if attrs.get("name") != name:
            continue

        module_path = node_id_to_module(node_id)
        if module_path:
            return module_path, name

    return None, None


def _stdlib_fallback(name: str) -> tuple[Optional[str], Optional[str]]:
    """Check if *name* is a known stdlib module.

    Returns ``(name, None)`` for a bare ``import <name>``, or
    ``(None, None)`` if unknown.
    """
    STDLIB_MODULES: set[str] = {
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
    }
    if name in STDLIB_MODULES:
        return name, None
    return None, None


# ═══════════════════════════════════════════════════════════════════════
# Step 3 — Inject imports via AddImportsVisitor
# ═══════════════════════════════════════════════════════════════════════


def _inject_imports(
    source: str,
    imports: dict[str, tuple[str, str | None]],
) -> str:
    """Use LibCST's ``AddImportsVisitor`` to inject imports into *source*.

    Parameters
    ----------
    source
        The full file source.
    imports
        ``{name: (module, obj_or_None)}`` as returned by
        :func:`_resolve_undefined`.

    Returns
    -------
    str
        The source with imports added.
    """
    try:
        tree = cst.parse_module(source)
    except Exception:
        logger.debug("Failed to parse source for import injection.")
        return source

    ctx = CodemodContext()
    for name, (module, obj) in imports.items():
        AddImportsVisitor.add_needed_import(
            context=ctx,
            module=module,
            obj=obj,
        )

    try:
        visitor = AddImportsVisitor(ctx)
        modified = tree.visit(visitor)
        return modified.code
    except Exception as e:
        logger.debug("Failed to inject imports via AddImportsVisitor: %s", e)
        return source


# ═══════════════════════════════════════════════════════════════════════
# Public API: resolve_import (used by testgen pipeline)
# ═══════════════════════════════════════════════════════════════════════


def resolve_import(
    file_path: str,
    class_name: Optional[str] = None,
    method_name: Optional[str] = None,
    workspace_dir: str = "",
    graph_db: Optional[object] = None,
) -> Optional[str]:
    """Resolve ``from <module> import <name>`` for the given target.

    Parameters
    ----------
    file_path : str
        Absolute or relative path to the source file containing the target.
    class_name : str, optional
        The class being tested.  Provide this *or* *method_name* (for
        standalone functions).
    method_name : str, optional
        The standalone function being tested.  Only used when *class_name* is
        ``None``.
    workspace_dir : str, optional
        The project root directory.  Required when *file_path* is relative.
    graph_db : OrkaGraphDB, optional
        If provided, the graph DB is queried first for a more reliable
        module path.

    Returns
    -------
    str or None
        Something like ``"from src.payments.processor import OrderProcessor\\n"``,
        or ``None`` if resolution fails (e.g. file doesn't exist).
    """
    # Strategy 1: Graph DB lookup (requires scan)
    if graph_db is not None:
        result = _from_graph(graph_db, class_name, method_name, file_path)
        if result:
            logger.debug("Import resolved via graph DB: %s", result.strip())
            return result

    # Strategy 2: Heuristic from file path (always works)
    result = _from_file_path(file_path, class_name, method_name, workspace_dir)
    if result:
        logger.debug("Import resolved via file path: %s", result.strip())
        return result

    return None


# ---------------------------------------------------------------------------
# Strategy 1 — Graph DB lookup
# ---------------------------------------------------------------------------


def _from_graph(
    graph_db: object,
    class_name: Optional[str],
    method_name: Optional[str],
    file_path: Optional[str],
) -> Optional[str]:
    """Search the graph DB for a matching Class: or Function: node.

    Node IDs look like::

        Class:myapp.models.User
        Function:app.helpers.calculate_discount

    We extract the dotted module path by stripping the type prefix and
    removing the final component (which is the class/function name).
    """
    target_type = "class" if class_name else "function"
    target_name = class_name or method_name

    for node_id, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("node_type") != target_type:
            continue
        if attrs.get("name") != target_name:
            continue
        # If we know the file path, narrow the search
        if file_path and attrs.get("file_path"):
            norm_file = os.path.normpath(file_path)
            norm_attr = os.path.normpath(attrs["file_path"])
            if not norm_file.endswith(norm_attr) and not norm_attr.endswith(norm_file):
                continue

        module_path = node_id_to_module(node_id)
        if module_path:
            return f"from {module_path} import {target_name}\n"

    return None


# ---------------------------------------------------------------------------
# Strategy 2 — File path heuristic
# ---------------------------------------------------------------------------


def _from_file_path(
    file_path: str,
    class_name: Optional[str] = None,
    method_name: Optional[str] = None,
    workspace_dir: str = "",
) -> Optional[str]:
    """Convert a file path to a dotted module path."""
    module_path = file_to_module(file_path, workspace_dir)

    import_name = class_name or method_name
    if not import_name or not module_path:
        return None

    return f"from {module_path} import {import_name}\n"
