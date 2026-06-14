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


# ═══════════════════════════════════════════════════════════════════════
# CST Transformer
# ═══════════════════════════════════════════════════════════════════════


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
            return updated_node.with_changes(body=self.new_body_node)

        # For class methods: match only when the current depth exactly
        # matches the target class hierarchy
        if self._current_depth == self._class_stack:
            self.modification_successful = True
            return updated_node.with_changes(body=self.new_body_node)

        return updated_node


# ═══════════════════════════════════════════════════════════════════════
# Snippet parsing
# ═══════════════════════════════════════════════════════════════════════


def parse_snippet_to_cst_body(llm_snippet: str) -> cst.IndentedBlock:
    """Parse raw LLM code into a LibCST ``IndentedBlock`` ready for injection.

    Handles three things the old ``MethodBodyReplacer`` logic did not:
    1. Strips markdown fences (`` ```python `` / `` ``` ``).
    2. Wraps body-level code in a dummy function so LibCST can handle
       bare statements like ``return x``.
    3. Catches :class:`cst.ParserSyntaxError` and raises ``ValueError``
       **before** any surgery attempt.

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
    # ── 1. Strip markdown fences ────────────────────────────────────
    cleaned = llm_snippet.strip()
    if not cleaned:
        raise ValueError("LLM snippet is empty.")

    # Remove opening fence (e.g. ```python or ```)
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        else:
            # Only the fence itself — treat as empty
            raise ValueError("LLM snippet is empty (only a markdown fence).")

    # Remove closing fence
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    # ── 2. Normalise indentation ────────────────────────────────────
    cleaned = textwrap.dedent(cleaned).strip()
    if not cleaned:
        raise ValueError("LLM snippet is empty after stripping fences and whitespace.")

    # ── 3. Wrap in dummy function and parse ─────────────────────────
    indented = textwrap.indent(cleaned, "    ")
    dummy_code = f"def __orka_dummy():\n{indented}\n"

    try:
        module = cst.parse_module(dummy_code)
    except cst.ParserSyntaxError as e:
        raise ValueError(
            f"LLM snippet contains invalid Python syntax: {e}"
        ) from e

    # ── 4. Extract the IndentedBlock of the dummy function ──────────
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
