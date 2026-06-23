"""Surgical method body replacement using LibCST.

Entry points
------------
- ``preview_patch(...)`` — Apply a surgical body swap **in memory**.
  Returns the full patched source as a string, or ``None`` if the
  target is not found.
- ``apply_llm_patch(...)`` — Write the patched source to disk.
  Returns ``True`` on success, ``False`` if the target was not found.

Both share the exact same :class:`MethodBodyReplacer` CST transformer.
"""

from __future__ import annotations

import textwrap
from typing import Optional

import libcst as cst
from orka.core.snippet_utils import sanitize_llm_output
from orka.surgery.trivia import preserve_docstring


# ═══════════════════════════════════════════════════════════════════════
# CST Transformer
# ═══════════════════════════════════════════════════════════════════════


class SnippetImportExtractor(cst.CSTTransformer):
    """Extracts import statements from a CST snippet and removes them from the tree.

    The LLM is instructed not to include imports in the method body (see
    ``no_imports_in_body.mdc`` rule), but often disobeys.  This transformer
    strips them out cleanly — the imports are **discarded**, not hoisted:

    * If a statement line contains *only* imports, the entire line is removed
      from the parent (via ``cst.RemoveFromParent()``).
    * If a statement line contains imports mixed with other code, only the
      import nodes are stripped, leaving the rest of the line intact.

    The refactor pipeline uses ``auto_import()`` (a pyflakes-based scan) to
    detect genuinely missing imports after the body swap and resolves them
    via the Graph DB.  The testgen pipeline uses ``resolve_import()`` for
    deterministic import assembly instead.
    """

    def __init__(self) -> None:
        self.extracted_imports: list[cst.BaseSmallStatement] = []

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine | cst.RemovalSentinel:
        new_body: list[cst.BaseSmallStatement] = []
        for stmt in updated_node.body:
            if isinstance(stmt, (cst.Import, cst.ImportFrom)):
                self.extracted_imports.append(stmt)
            else:
                new_body.append(stmt)

        if not new_body:
            return cst.RemoveFromParent()

        return updated_node.with_changes(body=new_body)


class MethodBodyReplacer(cst.CSTTransformer):
    """Replace the body of a single method/function, preserving everything else.

    Tracks nesting via a **depth-first stack** so that deeply nested classes
    (e.g. ``class Outer.Inner``) are handled correctly.  A method only
    matches when we are inside *all* the relevant classes.

    Parameters
    ----------
    target_method
        The name of the method/function to modify.
    new_body_source
        Raw LLM output (body-level code).  Validated and parsed via
        :func:`parse_snippet_to_cst_body`.
    target_class
        If ``None``, the function is treated as a standalone function
        (matches at module level).  For nested classes, use dotted
        notation — e.g. ``"Outer.Inner"``.
    """

    def __init__(
        self,
        target_method: str,
        new_body_source: str,
        target_class: Optional[str] = None,
    ) -> None:
        self.target_method = target_method
        self.target_class = target_class  # e.g. "OrderController" or "Outer.Inner"

        # Split into a list for depth-first matching
        self._class_stack: list[str] = (
            self.target_class.split(".") if self.target_class else []
        )
        self._current_depth: list[str] = []  # tracks live nesting

        self.new_body_node = parse_snippet_to_cst_body(new_body_source)
        self.modification_successful = False

    # ── Class entry/exit ────────────────────────────────────────────

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        self._current_depth.append(node.name.value)
        return True

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self._current_depth.pop()
        return updated_node

    # ── Method body swap ────────────────────────────────────────────

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        if original_node.name.value != self.target_method:
            return updated_node

        # For standalone functions (no target_class): match at module level
        if not self._class_stack:
            self.modification_successful = True
            return self._apply_with_docstring_preservation(original_node, updated_node)

        # For class methods: match only when the current depth exactly
        # matches the target class hierarchy
        if self._current_depth == self._class_stack:
            self.modification_successful = True
            return self._apply_with_docstring_preservation(original_node, updated_node)

        return updated_node

    def _apply_with_docstring_preservation(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.FunctionDef:
        """Apply body replacement, preserving the original docstring if missing in new body."""
        preserved_body = preserve_docstring(original_node.body, self.new_body_node)
        return updated_node.with_changes(body=preserved_body)


# ═══════════════════════════════════════════════════════════════════════
# Snippet parsing
# ═══════════════════════════════════════════════════════════════════════


def parse_snippet_to_cst_body(llm_snippet: str) -> cst.IndentedBlock:
    """Parse raw LLM code into a LibCST ``IndentedBlock`` ready for injection.

    Delegates markdown fence stripping and indentation normalization to
    :func:`orka.core.snippet_utils.sanitize_llm_output`.

    Parameters
    ----------
    llm_snippet
        A string containing only the method body (no signature, no
        decorators).  May or may not be wrapped in markdown fences.

    Returns
    -------
    cst.IndentedBlock
        The body node ready to pass to :class:`MethodBodyReplacer`.

    Raises
    ------
    ValueError
        If the snippet is empty, syntactically invalid, or cannot be parsed.
    """
    # ── 1. Centralized sanitization ─────────────────────────────────
    cleaned = sanitize_llm_output(llm_snippet)
    if not cleaned:
        raise ValueError("LLM snippet is empty after sanitization.")

    # ── 2. Wrap in dummy function and parse ─────────────────────────
    # Use 4-space indent for the dummy wrap — LibCST will normalise the
    # depth to match the target method automatically.
    indented = textwrap.indent(cleaned, "    ")
    dummy_code = f"def __orka_dummy():\n{indented}\n"

    try:
        module = cst.parse_module(dummy_code)
    except cst.ParserSyntaxError as e:
        raise ValueError(
            f"LLM snippet contains invalid Python syntax: {e}"
        ) from e

    # ── 3. Extract the IndentedBlock of the dummy function ──────────
    dummy_func: cst.FunctionDef = module.body[0]  # type: ignore[assignment]
    return dummy_func.body


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


def preview_patch(
    file_path: str,
    target_method: str,
    new_logic: str,
    target_class: Optional[str] = None,
) -> Optional[str]:
    """Simulate a surgical patch **in memory** — return the full source.

    This is the **preferred entry point** for the surgery pipeline.
    The file on disk is never touched.

    Parameters
    ----------
    file_path
        Absolute or relative path to the Python source file.
    target_method
        The name of the method/function whose body will be replaced.
    new_logic
        Raw LLM output (body-level code).  May include markdown fences.
    target_class
        The enclosing class name, or ``None`` for standalone functions.
        Supports dotted names for nested classes (e.g. ``"Outer.Inner"``).

    Returns
    -------
    str or None
        The full patched source code as a string, or ``None`` if the
        target method/class was not found.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    tree = cst.parse_module(source_code)
    transformer = MethodBodyReplacer(
        target_method=target_method,
        new_body_source=new_logic,
        target_class=target_class,
    )
    modified_tree = tree.visit(transformer)

    if transformer.modification_successful:
        return modified_tree.code
    return None


def apply_llm_patch(
    file_path: str,
    target_method: str,
    new_logic: str,
    target_class: Optional[str] = None,
) -> bool:
    """Apply the patch to disk.  Returns ``True`` on success.

    Parameters
    ----------
    file_path
        Absolute or relative path to the Python source file.
    target_method
        The name of the method/function whose body will be replaced.
    new_logic
        Raw LLM output (body-level code).  May include markdown fences.
    target_class
        The enclosing class name, or ``None`` for standalone functions.

    Returns
    -------
    bool
        ``True`` if the patch was applied, ``False`` if the target was
        not found (file unchanged).
    """
    result = preview_patch(file_path, target_method, new_logic, target_class)
    if result is None:
        return False
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(result)
    return True
