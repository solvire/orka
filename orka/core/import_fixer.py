"""Import resolution for generated test code and auto-import for refactored code.

Provides two entry points:

- ``resolve_import(...)`` — Given a source file path and a target (class or
  function), produces the ``from ... import ...`` statement needed to
  reference that target in a test file.  Used by the **testgen** pipeline.
- ``auto_import(...)`` — Scans refactored source for undefined names,
  resolves them via the Graph DB, and injects the correct imports at the
  top of the file via LibCST's ``AddImportsVisitor``.
  Used by the **refactor** pipeline after a body swap.

Symbol resolution is delegated to :mod:`orka.core.dependency_resolver`;
this module owns only the **injection concern** — LibCST's
``AddImportsVisitor`` and the ``from ... import ...`` statement formatting.
"""

import logging
from typing import Optional

from orka.core.dependency_resolver import resolve_target, resolve_undefined_names

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
    resolved = resolve_undefined_names(source, graph_db=graph_db, file_path=file_path)
    if not resolved:
        return source

    return _inject_imports(source, resolved)


# ═══════════════════════════════════════════════════════════════════════
# Import injection via AddImportsVisitor
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
        :func:`orka.core.dependency_resolver.resolve_undefined_names`.

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

    Resolution is delegated to
    :func:`orka.core.dependency_resolver.resolve_target`, which tries the
    Graph DB first (when *graph_db* is provided) and falls back to a
    file-path heuristic.

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
    import_name = class_name or method_name
    if not import_name:
        return None

    module = resolve_target(
        graph_db, file_path, method_name, class_name, base_dir=workspace_dir,
    )
    if not module:
        return None

    logger.debug("Import resolved: from %s import %s", module, import_name)
    return f"from {module} import {import_name}\n"
