"""
Shared graph DB helpers for the surgery pipeline.

Provides a **lazy singleton** (:func:`get_graph_db`) so that the large
``.orka_cache.graph.json`` is loaded at most once per process, plus all
the graph-traversal functions previously duplicated across controller
modules.

Functions
---------
- :func:`get_graph_db` — lazy singleton for ``OrkaGraphDB``
- :func:`find_target_node` — locate a method/function node in the graph
- :func:`resolve_target_module` — dotted module for the target
- :func:`resolve_one_dependency` — resolve a single callee name
- :func:`build_dependency_map` — all callable nodes in scope
- :func:`build_caller_constraints` — nodes that call the target
- :func:`render_dependency_map_table` — markdown table for prompts
- :func:`render_caller_constraints_table` — markdown table for prompts
- :func:`extract_dependency_signatures` — formatted signatures for GAG
"""

from __future__ import annotations

import logging
import os
from typing import Any

from orka.config import settings
from orka.core.module_resolver import node_id_to_module, file_to_module

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Lazy singleton
# ═══════════════════════════════════════════════════════════════════════

_GRAPH_DB_INSTANCE: object | None = None
"""Module-level singleton — loaded at most once per process."""


def get_graph_db() -> object | None:
    """Return the cached ``OrkaGraphDB`` instance, or ``None`` if unavailable.

    The graph files (``.orka_cache.json`` / ``.orka_cache.graph.json``) are
    loaded exactly once — subsequent calls return the same object.

    Returns
    -------
    object or None
        An ``OrkaGraphDB`` instance with a populated ``graph`` attribute,
        or ``None`` if the cache file is missing or loading fails.
    """
    global _GRAPH_DB_INSTANCE

    if _GRAPH_DB_INSTANCE is not None:
        return _GRAPH_DB_INSTANCE

    cache_file = os.path.join(str(settings.PROJECT_ROOT), ".orka_cache.json")
    if not os.path.exists(cache_file):
        logger.debug("Graph DB cache file not found at %s", cache_file)
        return None

    try:
        from orka.core.ingester import OrkaGraphDB

        g = OrkaGraphDB(cache_file=cache_file)
        if g.graph.number_of_nodes() > 0:
            logger.debug(
                "Graph DB loaded: %d nodes, %d edges",
                g.graph.number_of_nodes(),
                g.graph.number_of_edges(),
            )
            _GRAPH_DB_INSTANCE = g
        else:
            logger.debug("Graph DB loaded but has zero nodes — treating as unavailable")
        return _GRAPH_DB_INSTANCE
    except Exception as exc:
        logger.warning("Failed to load Graph DB: %s", exc)
        return None


def clear_graph_db_cache() -> None:
    """Reset the singleton (e.g. after a fresh ``orka scan`` in the same process)."""
    global _GRAPH_DB_INSTANCE
    _GRAPH_DB_INSTANCE = None


# ═══════════════════════════════════════════════════════════════════════
# Node lookups
# ═══════════════════════════════════════════════════════════════════════


def find_target_node(
    graph_db: object,
    source_file: str,
    method_name: str,
    class_name: str | None,
) -> str | None:
    """Locate the graph node ID for the target function or method.

    Searches the graph for a node whose ``name`` matches *method_name*,
    whose ``node_type`` is ``"function"`` or ``"method"`` (or only
    ``"method"`` if *class_name* is provided), and whose ``file_path``
    suffix matches *source_file*.

    Returns
    -------
    str or None
        The node ID (e.g. ``"Method:orka.core.compiler.PromptCompiler.compile"``)
        or ``None`` if no matching node is found.
    """
    target_types = ("function", "method") if not class_name else ("method",)
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
# Module resolution
# ═══════════════════════════════════════════════════════════════════════


def resolve_target_module(
    source_file: str,
    method_name: str,
    class_name: str | None,
    graph_db: object | None = None,
) -> str | None:
    """Resolve the dotted module path for the target method/function.

    Uses two strategies in order:

    1. **Graph DB lookup** — finds the target's node ID via
       :func:`find_target_node` and extracts the module path.
    2. **File-path heuristic** — strips the project root and ``.py``
       extension from *source_file* and converts path separators to dots.
    """
    # Strategy 1: graph DB
    if graph_db is not None:
        target_node = find_target_node(graph_db, source_file, method_name, class_name)
        if target_node:
            module_path = node_id_to_module(target_node)
            if module_path:
                return module_path

    # Strategy 2: file-path heuristic
    module_path = file_to_module(source_file, str(settings.PROJECT_ROOT))
    return module_path if module_path else None


def resolve_one_dependency(
    graph_db: object,
    name: str,
    source_module: str,
) -> dict[str, str] | None:
    """Resolve a single callee name to its import path and module.

    Searches in this order:

    1. **Same module** — nodes whose ``name`` matches and whose module
       (extracted from the node ID) equals *source_module*.
    2. **Any module** — any graph node whose ``name`` matches.

    Returns a record with keys ``name``, ``module``, ``import_path``,
    ``node_type``, ``file_path``, ``lineno``, or ``None``.
    """
    # Same-module candidates first
    for node, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("name") != name:
            continue
        node_module = node_id_to_module(node)
        if node_module and node_module == source_module:
            node_type = attrs.get("node_type", "unknown")
            return {
                "name": name,
                "module": node_module,
                "import_path": f"from {node_module} import {name}",
                "node_type": node_type,
                "file_path": attrs.get("file_path", ""),
                "lineno": str(attrs.get("lineno", "")),
            }

    # Broader graph search — any module
    for node, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("name") != name:
            continue
        node_module = node_id_to_module(node)
        if node_module:
            node_type = attrs.get("node_type", "unknown")
            return {
                "name": name,
                "module": node_module,
                "import_path": f"from {node_module} import {name}",
                "node_type": node_type,
                "file_path": attrs.get("file_path", ""),
                "lineno": str(attrs.get("lineno", "")),
            }

    return None


# ═══════════════════════════════════════════════════════════════════════
# Dependency / caller maps
# ═══════════════════════════════════════════════════════════════════════


def build_dependency_map(
    source_file: str,
    method_name: str,
    class_name: str | None,
    graph_db: object | None = None,
    static_deps: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Build a structured dependency map of functions/classes the target calls.

    The dependency map is scoped to:
    1. **Same-module siblings** — functions/classes in the same file as
       the target.
    2. **Imported modules** — modules the target's file imports (via
       ``File → Module`` edges in the graph), resolved to their exported
       functions/classes.
    3. **Static overrides** (*static_deps*) — caller-provided overrides
       for known dependencies (e.g., private helpers in the same file).

    Each entry has keys: ``name``, ``module``, ``import_path``,
    ``node_type``, ``file_path``, ``lineno``.

    Returns an empty list if *graph_db* is ``None``.
    """
    if graph_db is None:
        return []

    deps: dict[str, dict[str, str]] = {}
    source_module = resolve_target_module(source_file, method_name, class_name, graph_db)
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
                deps[name] = {
                    "name": name,
                    "module": node_module,
                    "import_path": f"from {node_module} import {name}",
                    "node_type": attrs.get("node_type", "unknown"),
                    "file_path": attrs.get("file_path", ""),
                    "lineno": str(attrs.get("lineno", "")),
                }

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
    graph_db: object | None = None,
) -> list[dict[str, str]]:
    """Build a structured list of callers that depend on the target.

    Uses the graph DB's predecessor edges to find every node that connects
    to the target. Each entry has keys: ``name``, ``module``,
    ``import_path``, ``file_path``, ``lineno``.

    Returns an empty list if *graph_db* is ``None`` or the target has no
    callers in the graph.
    """
    if graph_db is None:
        return []

    target_node = find_target_node(graph_db, source_file, method_name, class_name)
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
# Rendering (markdown tables for templates)
# ═══════════════════════════════════════════════════════════════════════


def render_dependency_map_table(deps: list[dict[str, str]]) -> str:
    """Render the dependency map as a compact markdown table.

    Columns: Name, Import Path, Type.
    """
    if not deps:
        return "No resolvable dependencies found."
    lines = [
        "| Name | Import Path | Type |",
        "|------|-------------|------|",
    ]
    for d in deps:
        lines.append(f"| {d['name']} | `{d['import_path']}` | {d['node_type']} |")
    return "\n".join(lines)


def render_caller_constraints_table(callers: list[dict[str, str]]) -> str:
    """Render the caller constraints as a compact markdown table.

    Columns: Caller, Import Path.
    """
    if not callers:
        return "No known callers in the internal graph."
    lines = [
        "| Caller | Import Path |",
        "|--------|-------------|",
    ]
    for c in callers:
        lines.append(f"| {c['name']} | `{c['import_path']}` |")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# GAG — Dependency Signature Injection
# ═══════════════════════════════════════════════════════════════════════


def extract_dependency_signatures(
    graph_db: object,
    source_file: str,
    method_name: str,
    class_name: str | None,
) -> str:
    """Query the graph for the target's in-scope dependencies and return a
    formatted signature block the LLM can use to avoid hallucinating arguments.

    This builds on :func:`build_dependency_map` — it collects every
    function/method/class in the same module and imported modules, then
    formats their names, import paths, and docstrings into a compact
    block suitable for template injection.

    Returns an empty string if no dependencies are found or the graph DB
    is unavailable.
    """
    deps = build_dependency_map(
        source_file, method_name, class_name, graph_db,
    )
    if not deps:
        logger.debug("No dependencies found for dependency signatures")
        return ""

    dep_parts: list[str] = []
    visited: set[str] = set()

    for dep in deps:
        name = dep["name"]
        if name in visited:
            continue
        visited.add(name)

        # Try to get docstring from graph node metadata
        dep_docstring = ""
        for node, attrs in graph_db.graph.nodes(data=True):
            if attrs.get("name") == name:
                ds = attrs.get("docstring", "")
                if ds:
                    dep_docstring = ds.strip()[:300]
                break

        lines = [f"DEPENDENCY: {name}"]
        lines.append(f"TYPE: {dep['node_type']}")
        lines.append(f"IMPORT: {dep['import_path']}")
        if dep.get("file_path"):
            lines.append(f"LOCATION: {dep['file_path']}:{dep.get('lineno', '?')}")
        if dep_docstring:
            lines.append(f"DOCSTRING: {dep_docstring}")
        dep_parts.append("\n".join(lines))

    if not dep_parts:
        return ""

    block = "\n---\n".join(dep_parts)
    logger.info("Built %d dependency signature(s) for GAG", len(dep_parts))
    return block
