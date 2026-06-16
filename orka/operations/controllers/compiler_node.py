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
from orka.operations.graph_helpers import (
    build_caller_constraints,
    build_dependency_map,
    get_graph_db,
    render_caller_constraints_table,
    render_dependency_map_table,
    resolve_target_module,
)
from orka.operations.helpers import load_template

logger = logging.getLogger(__name__)


# ── LibCST visitors for signature analysis ─────────────────────────────


class _SignatureCollector(cst.CSTVisitor):
    """Extract signature-level info from a method/function definition."""

    def __init__(self) -> None:
        """Initialise the collector.

        Attributes set after visiting a function definition:
        - ``params``: list of parameter strings (e.g. ``"x: int"``)
        - ``has_return_annotation``: whether a return type was declared
        - ``return_annotation``: the raw return type string
        - ``docblock``: the first docstring in the function body
        - ``has_decorators``: whether any decorators are present
        - ``decorator_count``: number of decorators
        - ``is_async``: whether the function is ``async def``
        - ``name``: the function/method name
        """
        self.params: list[str] = []
        self.has_return_annotation = False
        self.return_annotation: str = ""
        self.docblock: str = ""
        self.has_decorators = False
        self.decorator_count = 0
        self.is_async = False
        self.name: str = ""

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        """Extract signature metadata from a single function definition.

        Populates ``self`` attributes with:
        - name, async status, decorator count
        - parameter names and annotations
        - return annotation
        - first docstring in the body

        Returns ``False`` to prevent descending into nested functions.
        """
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
    """Parse a method/function definition and return structured signature info.

    Uses LibCST to extract the first function definition found in the code
    snippet.  If parsing fails (e.g. the snippet is empty or syntactically
    invalid), returns a default dict with empty values — this is non-fatal.

    Parameters
    ----------
    existing_code
        A Python source string containing a single function/method definition
        (or an empty string).

    Returns
    -------
    dict
        Keys:
        - ``name`` (str): function/method name
        - ``params`` (list[str]): parameter strings, e.g. ``["x: int", "y"]``
        - ``return_type`` (str): return annotation, e.g. ``"bool"``
        - ``docblock`` (str): first docstring in the body
        - ``is_async`` (bool): whether the function is ``async def``
        - ``decorator_count`` (int): number of decorators
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


# ── Main node executor ─────────────────────────────────────────────────


def execute(state: dict[str, Any]) -> dict[str, Any]:
    """Compile the prompt from gathered context and enriched analysis.

    This is the main node executor for the surgery graph.  It performs
    the following steps in order:

    1. Load the template (``"refactor"`` or ``"test"``).
    2. Resolve injection rules for the template.
    3. Analyse the existing code signature (params, return type, docblock).
    4. Open the graph DB once and resolve:
       - the target's own module path (``target_module``)
       - a dependency map of every reachable function/class
       - a caller-constraints list
    5. Build enriched context data with all analysis results.
    6. Render the template via ``PromptCompiler.compile()`` (similar
       examples are rendered inline via ``%%similar_examples%%``).
    7. Return both the flat compiled string and a structured sections dict.

    .. note:: Prompt ordering (Completion Trap fix)
       Similar examples from ChromaDB are injected *inside* the template
       (above the final output instruction) via ``%%similar_examples%%``.
       This prevents the LLM from treating trailing example code as the
       generation anchor.

    .. note:: Context Redundancy fix
       When rich ``dependency_signatures`` are available, the basic
       ``dependency_map`` table is suppressed to avoid token bloat.

    Parameters
    ----------
    state
        The current :class:`~orka.operations.state.SurgeryState`.  Expected
        keys include ``prompt_template_name``, ``source_file``,
        ``method_name``, ``class_name``, ``existing_code``,
        ``class_context``, ``requirements``, ``similar_examples``,
        ``target_node_id``.

    Returns
    -------
    dict
        Updated state keys:
        - ``compiled_prompt`` (str): the fully rendered prompt string.
        - ``compiled_prompt_sections`` (dict): structured breakdown with
          keys ``template_name``, ``rules_resolved``, ``signature``,
          ``target_module``, ``target_import``, ``dependency_map``,
          ``caller_constraints``, ``similar_examples_count``,
          ``char_count``.
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

    # ── 4. Open graph DB (lazy singleton) ──────────────────────────────
    graph_db = get_graph_db()

    # ── 5. Resolve target's own module path ───────────────────────────
    target_module = resolve_target_module(
        source_file, method_name, class_name, graph_db,
    )

    # Normalise: when class_name is provided, resolve_target_module() may
    # return "orka.surgery.modifier.SnippetImportExtractor" (class appended)
    # but the import should use module="orka.surgery.modifier" and
    # import the class name directly.
    actual_module = target_module
    if class_name and target_module and target_module.endswith(f".{class_name}"):
        actual_module = target_module[: -(len(class_name) + 1)]

    if target_module:
        logger.debug("Resolved target module: %s", target_module)

    # ── 6. Build dependency map and caller constraints ─────────────────
    dep_map = build_dependency_map(
        source_file, method_name, class_name, graph_db,
    )
    caller_constraints = build_caller_constraints(
        source_file, method_name, class_name, graph_db,
    )

    # ── 7. Grab dependency signatures from gathered context ────────────
    dependency_signatures = state.get("dependency_signatures", "")

    # ── 8. Build enriched context ─────────────────────────────────────
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
            + render_caller_constraints_table(caller_constraints)
            + "\nDo NOT change the signature or return type."
        )
    if dep_map:
        graph_constraints_parts.append(
            "### DEPENDENCY MAP (use these exact import paths):\n"
            + render_dependency_map_table(dep_map)
        )
    graph_constraints = "\n\n".join(graph_constraints_parts) if graph_constraints_parts else "No known callers or dependencies."

    # Target import instruction
    # For class methods, import the class; for standalone functions, import
    # the function/method name.  actual_module is the true dotted module
    # path (class name stripped from the tail) — see step 5b above.
    target_import = ""
    if actual_module:
        import_name = class_name or sig.get("name")
        if import_name:
            target_import = f"from {actual_module} import {import_name}"

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

    # ── Directive 2: Context Redundancy — conditional dependency map ───
    # If rich internal dependency signatures are available, suppress the
    # basic dependency-map table to avoid token bloat and attention dilution.
    has_rich_deps = bool(dependency_signatures and dependency_signatures.strip())
    effective_dep_map = "" if has_rich_deps else render_dependency_map_table(dep_map)

    # ── Directive 1: Completion Trap — render examples inside template ─
    # Format similar examples for inline rendering via %%similar_examples%%.
    # This ensures they appear ABOVE the final output instruction in the
    # template, not appended after it (which would cause the LLM to
    # generate example-like code instead of following the action trigger).
    if similar_examples:
        similar_examples_text = "\n---\n".join(similar_examples[:3])
    else:
        similar_examples_text = "No similar examples found."

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
        "dependency_map": effective_dep_map,
        "caller_constraints": render_caller_constraints_table(caller_constraints),
        "dependency_signatures": dependency_signatures,
        "similar_examples": similar_examples_text,
    }

    # ── 8. Compile ────────────────────────────────────────────────────
    compiler = PromptCompiler()
    compiled_prompt = compiler.compile(template, resolved_rules, context_data)

    # ── 9. Build structured sections for introspection ─────────────────
    sections: dict[str, Any] = {
        "template_name": template_name,
        "rules_resolved": [r.name for r in resolved_rules],
        "signature": sig,
        "target_module": actual_module or target_module or "",
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


