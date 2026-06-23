"""Import CST mutation — the single module for ALL import injection, rewriting,
extraction, and deduplication.

This is the *injection* layer of the 3-way import split::

    module_resolver  ->  dependency_resolver  ->  import_injector
    (file<->module)      (name -> module)         (source -> source)

Design: discrete, idempotent, stateless functions — source in, source out.
Each function does ONE thing and is safe to run unconditionally in sequence,
like linting passes (``ruff`` -> ``black`` -> ``isort``).  Re-running any
function on already-processed input is a no-op.

The LibCST transformer classes (``_ImportExtractor``, ``_ImportRewriter``) are
internal implementation details only; the public API is the set of flat
functions below.  Callers never touch the transformers directly.

This module consolidates and replaces:

- ``orka.core.import_fixer``   -> ``auto_import``, ``resolve_import_for_test``,
  ``inject_imports`` (was ``_inject_imports``).
- ``orka.core.cascade``        -> ``cascade_import_updates``, ``rewrite_import``
  (was ``ImportCascadeTransformer``).
- the import-stripping transformer in ``orka.surgery.modifier`` -> ``extract_imports``.
- ``orka.surgery.transplanter.process_imports``    -> ``dedupe_imports`` /
  ``harvest_and_dedupe``.
"""

import logging
import os
from typing import Optional

import libcst as cst
import libcst.helpers as helpers
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor

from orka.core.dependency_resolver import resolve_target, resolve_undefined_names
from orka.core.module_resolver import file_to_module

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Internal transformers (never part of the public API)
# ═══════════════════════════════════════════════════════════════════════


def _alias_bound_name(alias: cst.ImportAlias) -> str:
    """Return the local name bound by an import alias.

    For ``import a.b.c`` the bound name is ``a`` (the leftmost component);
    for ``import a.b.c as x`` it is ``x``; for ``from m import a as b`` it
    is ``b``; for ``from m import a`` it is ``a``.
    """
    if alias.asname:
        return alias.asname.name.value
    name = alias.name
    if isinstance(name, cst.Name):
        return name.value
    if isinstance(name, cst.Attribute):
        cur = name
        while isinstance(cur.value, cst.Attribute):
            cur = cur.value
        return cur.value.value
    return ""


def _render(node: cst.BaseSmallStatement) -> str:
    """Render a small statement (Import/ImportFrom) to its source string.

    A trailing ``;`` (from a compound line like ``import os; x = 1``) is
    stripped so the extracted import string is standalone.
    """
    rendered = cst.Module([]).code_for_node(node)
    rendered = rendered.rstrip()
    if rendered.endswith(";"):
        rendered = rendered[:-1].rstrip()
    return rendered


def _clean_aliases(
    aliases: list[cst.ImportAlias],
) -> tuple[cst.ImportAlias, ...]:
    """Return aliases with no trailing comma on the last element.

    Needed after subsetting an import's alias list: the kept aliases may
    retain a ``comma`` that was only valid mid-list, producing output like
    ``from x import b, ``.
    """
    if not aliases:
        return ()
    result = list(aliases)
    last = result[-1]
    if last.comma != cst.MaybeSentinel.DEFAULT:
        result[-1] = last.with_changes(comma=cst.MaybeSentinel.DEFAULT)
    return tuple(result)


class _ImportExtractor(cst.CSTTransformer):
    """Internal: extract import statements from a CST and remove them.

    When *name_filter* is ``None``, every ``Import``/``ImportFrom`` statement
    is extracted wholesale (whole statement, including multi-name imports)
    and removed from the tree — the behaviour previously used to strip
    imports from LLM snippets.

    When *name_filter* is a set, only the import *names* that intersect the
    filter are extracted: multi-name imports are pruned to the matching
    subset (the non-matching names are left behind in the source).  Lines
    that have no matching name are left untouched.  ``from m import *`` is
    extracted only when *name_filter* is ``None`` (a star import cannot be
    partitioned by name).
    """

    def __init__(self, name_filter: Optional[set[str]] = None) -> None:
        self.name_filter = name_filter
        self.extracted: list[str] = []

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine | cst.RemovalSentinel:
        new_body: list[cst.BaseSmallStatement] = []
        for stmt in updated_node.body:
            if isinstance(stmt, cst.ImportFrom) and isinstance(stmt.names, cst.ImportStar):
                if self.name_filter is None:
                    self.extracted.append(_render(stmt))
                else:
                    new_body.append(stmt)
                continue

            if isinstance(stmt, (cst.Import, cst.ImportFrom)):
                if self.name_filter is None:
                    self.extracted.append(_render(stmt))
                    continue

                matched, unmatched = self._partition(stmt)
                if matched:
                    self.extracted.append(
                        _render(stmt.with_changes(names=_clean_aliases(matched)))
                    )
                    if unmatched:
                        new_body.append(
                            stmt.with_changes(names=_clean_aliases(unmatched))
                        )
                else:
                    new_body.append(stmt)
                continue

            new_body.append(stmt)

        if not new_body:
            return cst.RemoveFromParent()
        return updated_node.with_changes(body=new_body)

    def _partition(
        self, stmt: cst.Import | cst.ImportFrom
    ) -> tuple[list[cst.ImportAlias], list[cst.ImportAlias]]:
        """Split an import's aliases into (matched, unmatched) by name_filter."""
        aliases = list(stmt.names)
        matched = [a for a in aliases if _alias_bound_name(a) in self.name_filter]
        unmatched = [a for a in aliases if _alias_bound_name(a) not in self.name_filter]
        return matched, unmatched


class _ImportRewriter(cst.CSTTransformer):
    """Internal: rewrite ``from old_module import target_name`` to a new module.

    Handles multi-name imports by splitting them: ``from old import A, B`` with
    ``target=B`` becomes ``from old import A`` + ``from new import B``.
    Identical behaviour to the old ``ImportCascadeTransformer``.
    """

    def __init__(self, old_module: str, new_module: str, target_name: str) -> None:
        self.old_module = old_module
        self.new_module = new_module
        self.target_name = target_name
        dummy = cst.parse_statement(f"from {new_module} import {target_name}")
        self.new_import_node: cst.BaseSmallStatement = dummy.body[0]

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.BaseStatement | cst.FlattenSentinel:
        new_body: list[cst.BaseSmallStatement] = []
        needs_split = False
        new_statements: list[cst.SimpleStatementLine] = []

        for stmt in updated_node.body:
            if (
                isinstance(stmt, cst.ImportFrom)
                and not isinstance(stmt.names, cst.ImportStar)
            ):
                mod_name = (
                    helpers.get_full_name_for_node(stmt.module) if stmt.module else ""
                )
                if mod_name == self.old_module and len(stmt.relative) == 0:
                    aliases = list(stmt.names)
                    target_alias = next(
                        (a for a in aliases if a.name.value == self.target_name),
                        None,
                    )
                    if target_alias:
                        needs_split = True
                        if len(aliases) == 1:
                            new_body.append(self.new_import_node)
                        else:
                            kept = [a for a in aliases if a.name.value != self.target_name]
                            new_body.append(
                                stmt.with_changes(names=_clean_aliases(kept))
                            )
                            new_statements.append(
                                cst.SimpleStatementLine(body=[self.new_import_node])
                            )
                        continue
            new_body.append(stmt)

        if needs_split:
            if new_statements:
                return cst.FlattenSentinel(
                    [updated_node.with_changes(body=new_body)] + new_statements
                )
            return updated_node.with_changes(body=new_body)
        return updated_node


# ═══════════════════════════════════════════════════════════════════════
# Discrete primitives — source in, source out (idempotent)
# ═══════════════════════════════════════════════════════════════════════


def extract_imports(
    source: str, name_filter: Optional[set[str]] = None
) -> tuple[str, list[str]]:
    """Extract import statements from Python source.

    Idempotent: running on source with no (matching) imports returns
    ``(source, [])``.

    Parameters
    ----------
    source
        Python source code string.
    name_filter
        If provided, only extract imports whose local names match this set
        (multi-name imports are pruned to the matching subset).  If ``None``,
        extract ALL import statements wholesale.

    Returns
    -------
    tuple[str, list[str]]
        ``(source_without_extracted_imports, list_of_import_statement_strings)``.

    Replaces: the import-stripping transformer in ``modifier.py`` and
    ``TransplantTransformer``'s import harvesting (transplanter.py).
    """
    try:
        tree = cst.parse_module(source)
    except Exception:
        logger.debug("Failed to parse source for import extraction.")
        return source, []

    extractor = _ImportExtractor(name_filter)
    modified = tree.visit(extractor)
    return modified.code, extractor.extracted


def inject_imports(
    source: str, imports: dict[str, tuple[str, str | None]]
) -> str:
    """Inject ``from X import Y`` statements at the top of source.

    Idempotent: ``AddImportsVisitor`` deduplicates, so injecting already-present
    imports is a no-op.

    Parameters
    ----------
    source
        Python source code string.
    imports
        ``{name: (module, obj_or_None)}`` as returned by
        :func:`orka.core.dependency_resolver.resolve_undefined_names`.
        ``obj=None`` means a bare ``import module``.

    Returns
    -------
    str
        Source with imports added at the top.

    Replaces: ``import_fixer._inject_imports``.
    """
    if not imports:
        return source

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


def rewrite_import(
    source: str, old_module: str, new_module: str, target_name: str
) -> str:
    """Rewrite ``from old_module import target_name`` to ``new_module``.

    Idempotent: if ``old_module`` does not appear in source, returns source
    unchanged.  Handles multi-name imports: ``from old import A, B, C`` with
    ``target=B`` splits into ``from old import A, C`` + ``from new import B``.

    Replaces: ``ImportCascadeTransformer`` (cascade.py).
    """
    try:
        tree = cst.parse_module(source)
    except Exception:
        return source

    rewriter = _ImportRewriter(old_module, new_module, target_name)
    modified = tree.visit(rewriter)
    return modified.code


def dedupe_imports(import_strings: list[str]) -> list[str]:
    """Merge, sort, and deduplicate import statement strings.

    Idempotent: running on an already-deduped list returns the same list.
    Merges ``from X import A`` + ``from X import B`` into
    ``from X import A, B``.  Bare ``import`` statements are deduplicated and
    sorted first, then ``from`` imports sorted by ``(relative-dots, module)``.

    Replaces: ``transplanter.process_imports``.
    """
    if not import_strings:
        return []

    regular: dict[str, set[str | None]] = {}
    from_imports: dict[tuple[int, str], set[tuple[str, str | None]]] = {}

    for s in import_strings:
        try:
            module = cst.parse_module(s)
        except Exception:
            continue
        for stmt in module.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for small in stmt.body:
                if isinstance(small, cst.Import):
                    for alias in small.names:
                        name = cst.Module([]).code_for_node(alias.name)
                        asname = alias.asname.name.value if alias.asname else None
                        regular.setdefault(name, set()).add(asname)
                elif isinstance(small, cst.ImportFrom):
                    if isinstance(small.names, cst.ImportStar):
                        continue
                    mod_str = (
                        cst.Module([]).code_for_node(small.module)
                        if small.module
                        else ""
                    )
                    rel_dots = len(small.relative) if small.relative else 0
                    key = (rel_dots, mod_str)
                    for alias in small.names:
                        name = alias.name.value
                        asname = alias.asname.name.value if alias.asname else None
                        from_imports.setdefault(key, set()).add((name, asname))

    result: list[str] = []

    for name in sorted(regular):
        for asname in sorted(regular[name], key=lambda a: (a is not None, a or "")):
            result.append(f"import {name} as {asname}" if asname else f"import {name}")

    for (rel_dots, mod_str) in sorted(from_imports):
        aliases = sorted(
            from_imports[(rel_dots, mod_str)],
            key=lambda x: x[1] if x[1] else x[0],
        )
        dots = "." * rel_dots
        name_strs = [f"{n} as {a}" if a else n for n, a in aliases]
        result.append(f"from {dots}{mod_str} import {', '.join(name_strs)}")

    return result


# ═══════════════════════════════════════════════════════════════════════
# High-level orchestrators (still flat functions composing the primitives)
# ═══════════════════════════════════════════════════════════════════════


def auto_import(
    source: str,
    file_path: str = "",
    graph_db: Optional[object] = None,
) -> str:
    """Detect undefined names -> resolve -> inject.  Idempotent.

    Runs after a LibCST body swap.  Uses pyflakes (via
    :func:`resolve_undefined_names`) to find undefined names, resolves each
    to its canonical ``from <module> import <name>`` path, then injects the
    imports via :func:`inject_imports`.

    Moves from ``import_fixer.py``.

    Parameters
    ----------
    source
        The full file source (after patching).
    file_path
        The file path, used for logging context only.
    graph_db
        An ``OrkaGraphDB`` instance.  If ``None``, only stdlib/module-level
        fallback heuristics are used.

    Returns
    -------
    str
        The source with imports added at the top.  If no undefined names are
        detected, returns the source unchanged.
    """
    resolved = resolve_undefined_names(source, graph_db=graph_db, file_path=file_path)
    if not resolved:
        return source
    return inject_imports(source, resolved)


def resolve_import_for_test(
    file_path: str,
    class_name: Optional[str] = None,
    method_name: Optional[str] = None,
    workspace_dir: str = "",
    graph_db: Optional[object] = None,
) -> Optional[str]:
    """Format a ``from X import Y`` string for test files.  Pure string op.

    Renamed from ``import_fixer.resolve_import``.  Resolution is delegated to
    :func:`orka.core.dependency_resolver.resolve_target`.

    Parameters
    ----------
    file_path
        Absolute or relative path to the source file containing the target.
    class_name
        The class being tested.  Provide this *or* *method_name*.
    method_name
        The standalone function being tested.  Only used when *class_name*
        is ``None``.
    workspace_dir
        The project root directory.  Required when *file_path* is relative.
    graph_db
        If provided, the graph DB is queried first for a more reliable module
        path.

    Returns
    -------
    str or None
        Something like ``"from src.payments.processor import OrderProcessor\\n"``,
        or ``None`` if resolution fails.
    """
    import_name = class_name or method_name
    if not import_name:
        return None

    module = resolve_target(
        graph_db, file_path, method_name, class_name, base_dir=workspace_dir
    )
    if not module:
        return None

    logger.debug("Import resolved: from %s import %s", module, import_name)
    return f"from {module} import {import_name}\n"


def cascade_import_updates(
    graph_db: object,
    target_class: str,
    old_file_path: str,
    new_file_path: str,
    base_dir: str,
) -> int:
    """Graph query for dependent files -> :func:`rewrite_import` on each.

    Moves from ``cascade.py``.  Queries the graph DB for every file that
    imports *target_class* from *old_file_path*'s module, then rewrites each
    such import to point at *new_file_path*'s module.

    Returns
    -------
    int
        The number of files whose imports were updated.
    """
    old_module = file_to_module(old_file_path, base_dir)
    new_module = file_to_module(new_file_path, base_dir)

    files_to_update: set[str] = set()

    old_module_node = f"Module:{old_module}"
    if not graph_db.graph.has_node(old_module_node):
        logger.warning("Old module '%s' not found in graph.", old_module)
        return 0

    inward_edges = list(graph_db.graph.predecessors(old_module_node))
    for predecessor in inward_edges:
        edge_data = graph_db.graph.get_edge_data(predecessor, old_module_node)
        aliases = edge_data.get("alias", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        logger.debug(
            "Graph shows %s imports these from %s: %s",
            predecessor,
            old_module,
            aliases,
        )
        if target_class in aliases:
            if predecessor.startswith("File:"):
                files_to_update.add(predecessor.replace("File:", "", 1))

    if not files_to_update:
        logger.info(
            "No external dependencies found for %s. Cascade complete.", target_class
        )
        return 0

    updated_count = 0
    for rel_file_path in files_to_update:
        abs_file_path = os.path.join(base_dir, rel_file_path)
        if not os.path.exists(abs_file_path):
            logger.warning("File not found for cascade: %s", abs_file_path)
            continue

        with open(abs_file_path, "r", encoding="utf-8") as f:
            source = f.read()

        try:
            modified = rewrite_import(source, old_module, new_module, target_class)
            with open(abs_file_path, "w", encoding="utf-8") as f:
                f.write(modified)
            logger.info("Cascaded import update in %s", abs_file_path)
            updated_count += 1
        except Exception as e:
            logger.error("Failed to update imports in %s: %s", abs_file_path, e)

    return updated_count


def harvest_and_dedupe(
    source: str, required_deps: set[str]
) -> list[str]:
    """Extract imports matching *required_deps* -> dedupe.

    Composes :func:`extract_imports` (with ``name_filter=required_deps``) and
    :func:`dedupe_imports`.  Returns the merged, sorted, deduplicated import
    statement strings needed to satisfy *required_deps*.

    Replaces ``transplanter.process_imports``.
    """
    _, import_strings = extract_imports(source, name_filter=set(required_deps))
    return dedupe_imports(import_strings)
