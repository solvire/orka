"""
Compile-prompt node — enriches raw context and renders the template.

Placed between ``gather_context`` and ``generate_draft`` in the surgery
graph. This node is pure Python (no LLM call) — it enriches the raw
context with:

1. **Signature analysis** — parses the existing code via LibCST to
   extract params, return type, docblock, decorators, async status.
2. **Graph DB lookup** — traces 1-level callers and callees with
   semantic summaries, plus resolves the target's own module path
   and builds a structured dependency map with import paths and
   signatures for every reachable node.
3. **Vector DB enrichment** — appends similar examples from ChromaDB
   (already gathered by ``gather_context``).
4. **Template compilation** — renders the YAML template with rules.

The node returns both the flat compiled string and a structured
sections dict so callers can inspect the parts independently.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import libcst as cst

from orka.config import settings
from orka.core.compiler import PromptCompiler
from orka.core.rule_resolver import resolve_rules
from orka.operations.helpers import load_template

logger = logging.getLogger(__name__)


# ── LibCST visitors for signature analysis ─────────────────────────────


class _SignatureCollector(cst.CSTVisitor):
    """Extract signature-level info from a method/function definition."""

    def __init__(self) -> None:
        self.params: list[str] = []
        self.has_return_annotation = False
        self.return_annotation: str = ""
        self.docblock: str = ""
        self.has_decorators = False
        self.decorator_count = 0
        self.is_async = False
        self.name: str = ""

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        self.name = node.name.value
        self.is_async = node.asynchronous is not None
        self.has_decorators = bool(node.decorators)
        self.decorator_count = len(node.decorators)

        # Extract parameters
        for param in node.params.params:
            p_name = param.name.value if hasattr(param, "name") else str(param)
            p_annotation = ""
            if hasattr(param, "annotation") and param.annotation:
                p_annotation = cst.Module(body=[]).code_for_node(param.annotation.annotation)
            if p_annotation:
                self.params.append(f"{p_name}: {p_annotation}")
            else:
                self.params.append(p_name)

        # Return annotation
        if node.returns:
            self.has_return_annotation = True
            self.return_annotation = cst.Module(body=[]).code_for_node(node.returns.annotation)

        # Docblock (first statement in body)
        if node.body.body:
            first_stmt = node.body.body[0]
            if isinstance(first_stmt, cst.SimpleStatementLine):
                for stmt in first_stmt.body:
                    if isinstance(stmt, cst.Expr) and isinstance(stmt.value, cst.SimpleString):
                        self.docblock = stmt.value.value.strip('"').strip("'").strip()

        return False  # Don't descend into nested functions


def _analyse_signature(existing_code: str) -> dict[str, Any]:
    """Parse a method/function definition and return structured info.

    Returns a dict with keys: ``name``, ``params``, ``return_type``,
    ``docblock``, ``is_async``, ``decorator_count``.
    """
    result: dict[str, Any] = {
        "name": "",
        "params": [],
        "return_type": "",
        "docblock": "",
        "is_async": False,
        "decorator_count": 0,
    }

    if not existing_code or not existing_code.strip():
        return result

    try:
        tree = cst.parse_module(existing_code)
        collector = _SignatureCollector()
        tree.visit(collector)

        result["name"] = collector.name
        result["params"] = collector.params
        result["return_type"] = collector.return_annotation
        result["docblock"] = collector.docblock
        result["is_async"] = collector.is_async
        result["decorator_count"] = collector.decorator_count

    except Exception as exc:
        logger.warning("Signature analysis failed (non-fatal): %s", exc)

    return result


# ── Graph DB helpers ────────────────────────────────────────────────────


def _find_target_node(
    graph_db: object,
    source_file: str,
    method_name: str,
    class_name: str | None,
) -> str | None:
    """Locate the graph node ID for the target function or method."""
    target_types = ("function", "method") if not class_name else ("method",)
    for node, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("node_type") not in target_types:
            continue
        if attrs.get("name") != method_name:
            continue
        # Match file path
        attr_file = attrs.get("file_path", "")
        if attr_file and source_file.endswith(attr_file):
            return node
    return None


def _module_from_node_id(node_id: str) -> str | None:
    """Extract dotted module path from a graph node ID.

    ``Function:orka.operations.helpers.load_template`` → ``orka.operations.helpers``
    ``Method:orka.core.compiler.PromptCompiler.compile`` → ``orka.core.compiler``
    """
    if ":" not in node_id:
        return None
    without_type = node_id.split(":", 1)[1]
    parts = without_type.split(".")
    if len(parts) < 2:
        return None
    return ".".join(parts[:-1])


def _resolve_target_module(
    source_file: str,
    method_name: str,
    class_name: str | None,
    graph_db: object | None = None,
) -> str | None:
    """Resolve the dotted module path for the target method/function.

    Uses the graph DB first (by finding the target's node ID and extracting
    the dotted module path), falls back to file-path heuristic.

    Returns something like ``"orka.operations.controllers.compiler_node"``
    or ``None`` if resolution fails.
    """
    # Strategy 1: graph DB
    if graph_db is not None:
        target_node = _find_target_node(graph_db, source_file, method_name, class_name)
        if target_node:
            module_path = _module_from_node_id(target_node)
            if module_path:
                return module_path

    # Strategy 2: file-path heuristic (same as import_fixer._from_file_path)
    path = os.path.normpath(source_file)
    ws = os.path.normpath(str(settings.PROJECT_ROOT))
    if path.startswith(ws):
        path = path[len(ws):].lstrip("/").lstrip("\\")
    if path.endswith(".py"):
        path = path[:-3]
    module_path = path.replace("/", ".").replace("\\", ".").lstrip(".")
    return module_path if module_path else None


def _resolve_one_dependency(
    graph_db: object,
    name: str,
    source_module: str,
) -> dict[str, str] | None:
    """Resolve a single callee name to its import path and module.

    Searches namespace order:
    1. Same module (sibling functions)
    2. Graph nodes matching ``Function:{module}.{name}`` or
       ``Class:{module}.{name}``
    3. Falls back to unknown resolution
    """
    # Same-module candidates first
    for node, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("name") != name:
            continue
        node_module = _module_from_node_id(node)
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
        node_module = _module_from_node_id(node)
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


def _build_dependency_map(
    source_file: str,
    method_name: str,
    class_name: str | None,
    graph_db: object | None = None,
    static_deps: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Build a structured dependency map of functions/classes the target calls.

    The dependency map is built from:
    1. The graph DB's ``File → Module`` edges (imports the file makes).
    2. Cross-referencing imported modules against known graph nodes.
    3. For dependencies in the same module, resolution against sibling nodes.
    4. Static overrides (``static_deps``) for dependencies that aren't in
       the graph (e.g., private same-module functions).

    Each entry has keys: ``name``, ``module``, ``import_path``, ``node_type``.

    Parameters
    ----------
    source_file
        Absolute or relative path to the source file.
    method_name
        Name of the target method/function.
    class_name
        Class name (or ``None`` for standalone functions).
    graph_db
        An open ``OrkaGraphDB`` instance (or ``None`` if unavailable).
    static_deps
        Optional override dict mapping ``{name: module}`` for dependencies
        that are known to be called by the target but might not be in the
        graph (e.g., the target's own private helpers in the same file).

    Returns
    -------
    list[dict]
        A list of structured dependency records, sorted by name.
    """
    if graph_db is None:
        return []

    deps: dict[str, dict[str, str]] = {}
    source_module = _resolve_target_module(source_file, method_name, class_name, graph_db)
    if not source_module:
        return []

    # Helper to resolve a known name against the graph
    def _resolve(name: str) -> dict[str, str] | None:
        return _resolve_one_dependency(graph_db, name, source_module)

    # 1. Static overrides (same-file private functions, known callees)
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

    # 2. Iterate all nodes in the graph that match the same module or
    #    any known imported module to find reachable functions/classes.
    for node, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("node_type") not in ("function", "method", "class"):
            continue
        resolved = _resolve(attrs["name"])
        if resolved and resolved["name"] not in deps:
            deps[resolved["name"]] = resolved

    return sorted(deps.values(), key=lambda d: d["name"])


def _build_caller_constraints(
    source_file: str,
    method_name: str,
    class_name: str | None,
    graph_db: object | None = None,
) -> list[dict[str, str]]:
    """Build a structured list of callers that depend on the target.

    Each entry has keys: ``name``, ``module``, ``import_path``,
    ``file_path``, ``lineno``.

    Returns an empty list if the target has no callers in the graph or
    the graph DB is unavailable.
    """
    if graph_db is None:
        return []

    target_node = _find_target_node(graph_db, source_file, method_name, class_name)
    if not target_node:
        return []

    callers: list[dict[str, str]] = []
    for caller_node in graph_db.graph.predecessors(target_node):
        caller_attrs = graph_db.graph.nodes[caller_node]
        caller_module = _module_from_node_id(caller_node)
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


def _render_dependency_map_table(deps: list[dict[str, str]]) -> str:
    """Render the dependency map as a compact markdown table.

    Shows: Name, Import Path, Type.
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


def _render_caller_constraints_table(callers: list[dict[str, str]]) -> str:
    """Render the caller constraints as a compact markdown table.

    Shows: Caller, Import Path.
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


# ── Main node executor ─────────────────────────────────────────────────


def execute(state: dict[str, Any]) -> dict[str, Any]:
    """Compile the prompt from gathered context and enriched analysis.

    Steps
    -----
    1. Load the template (``"refactor"`` or ``"test"``).
    2. Resolve injection rules for the template.
    3. Analyse the existing code signature (params, return type, docblock).
    4. Open the graph DB once and resolve:
       - the target's own module path (``target_module``)
       - a dependency map of every reachable function/class
       - a caller-constraints list
    5. Build enriched context data with all analysis results.
    6. Render the template via ``PromptCompiler.compile()``.
    7. Return both the flat compiled string and structured sections.

    Parameters
    ----------
    state
        The current :class:`~orka.operations.state.SurgeryState`.

    Returns
    -------
    dict
        Updated state keys: ``compiled_prompt``, ``compiled_prompt_sections``.
    """
    template_name = state["prompt_template_name"]
    source_file = state["source_file"]
    method_name = state["method_name"]
    class_name = state.get("class_name")
    existing_code = state.get("existing_code", "")
    class_context = state.get("class_context", "")
    requirements = state.get("requirements", "")
    similar_examples = state.get("similar_examples", [])

    logger.info(
        "Compiling prompt for %s (%s template)",
        state["target_node_id"],
        template_name,
    )

    # ── 1. Load template ──────────────────────────────────────────────
    template = load_template(template_name)

    # ── 2. Resolve rules ──────────────────────────────────────────────
    resolved_rules = resolve_rules(
        template_name=template.name,
        injection_points=template.injection_points,
    )

    # ── 3. Analyse signature ──────────────────────────────────────────
    sig = _analyse_signature(existing_code)
    logger.debug(
        "Signature analysis: name=%r, %d params, return=%r, async=%s, %d decorators",
        sig["name"],
        len(sig["params"]),
        sig.get("return_type", ""),
        sig.get("is_async", False),
        sig.get("decorator_count", 0),
    )

    # ── 4. Open graph DB once (shared by all lookups below) ───────────
    graph_db: object | None = None
    try:
        from orka.core.ingester import OrkaGraphDB

        cache_file = os.path.join(str(settings.PROJECT_ROOT), ".orka_cache.json")
        if os.path.exists(cache_file):
            g = OrkaGraphDB(cache_file=cache_file)
            if g.graph.number_of_nodes() > 0:
                graph_db = g
    except Exception as exc:
        logger.debug("Graph DB unavailable: %s", exc)

    # ── 5. Resolve target's own module path ───────────────────────────
    target_module = _resolve_target_module(
        source_file, method_name, class_name, graph_db,
    )
    if target_module:
        logger.debug("Resolved target module: %s", target_module)

    # ── 6. Build dependency map and caller constraints ─────────────────
    dep_map = _build_dependency_map(
        source_file, method_name, class_name, graph_db,
    )
    caller_constraints = _build_caller_constraints(
        source_file, method_name, class_name, graph_db,
    )

    # ── 7. Build enriched context ─────────────────────────────────────
    # Relative path for the prompt
    prompt_file_path = source_file
    workspace_dir = str(settings.PROJECT_ROOT)
    if workspace_dir and source_file.startswith(workspace_dir):
        prompt_file_path = os.path.relpath(source_file, workspace_dir)

    # Build graph constraints string from caller data
    graph_constraints_parts: list[str] = []
    if caller_constraints:
        graph_constraints_parts.append(
            "### CALLER CONSTRAINTS (these depend on the target):\n"
            + _render_caller_constraints_table(caller_constraints)
            + "\nDo NOT change the signature or return type."
        )
    if dep_map:
        graph_constraints_parts.append(
            "### DEPENDENCY MAP (use these exact import paths):\n"
            + _render_dependency_map_table(dep_map)
        )
    graph_constraints = "\n\n".join(graph_constraints_parts) if graph_constraints_parts else "No known callers or dependencies."

    # Target import instruction
    target_import = ""
    if target_module and sig.get("name"):
        target_import = f"from {target_module} import {sig['name']}"

    # Build signature/dockblock summary for the template placeholders
    docblock_text = sig["docblock"] if sig["docblock"] else "No docblock found."
    sig_context_parts = []
    if sig["params"]:
        sig_context_parts.append(f"Parameters: {', '.join(sig['params'])}")
    if sig["return_type"]:
        sig_context_parts.append(f"Returns: {sig['return_type']}")
    if sig["is_async"]:
        sig_context_parts.append("Method is async")
    signature_context = ""
    if sig_context_parts:
        signature_context = "Signature info: " + "; ".join(sig_context_parts)

    # Core context for the template placeholders
    context_data: dict[str, str] = {
        "existing_code": existing_code,
        "class_context": class_context,
        "business_requirements": requirements,
        "graph_constraints": graph_constraints,
        "docblock": docblock_text,
        "file_path": prompt_file_path,
        "target_import": target_import,
        "target_module": target_module or "",
        "target_name": sig.get("name", ""),
        "dependency_map": _render_dependency_map_table(dep_map),
        "caller_constraints": _render_caller_constraints_table(caller_constraints),
    }

    # ── 8. Compile ────────────────────────────────────────────────────
    compiler = PromptCompiler()
    compiled_prompt = compiler.compile(template, resolved_rules, context_data)

    # Append similar examples (not in template — appended directly)
    extra_sections: list[str] = []

    if similar_examples:
        extra_sections.append(
            "### SIMILAR EXISTING CODE (for reference):\n"
            + "\n---\n".join(similar_examples[:3])
        )

    if extra_sections:
        compiled_prompt += "\n\n" + "\n\n".join(extra_sections)

    # ── 9. Build structured sections for introspection ─────────────────
    sections: dict[str, Any] = {
        "template_name": template_name,
        "rules_resolved": [r.name for r in resolved_rules],
        "signature": sig,
        "target_module": target_module or "",
        "target_import": target_import,
        "dependency_map": dep_map,
        "caller_constraints": caller_constraints,
        "similar_examples_count": len(similar_examples),
        "char_count": len(compiled_prompt),
    }

    logger.debug(
        "Compiled prompt for %s — %d chars, %d rules, %d deps",
        state["target_node_id"],
        len(compiled_prompt),
        len(resolved_rules),
        len(dep_map),
    )

    return {
        "compiled_prompt": compiled_prompt,
        "compiled_prompt_sections": sections,
    }

