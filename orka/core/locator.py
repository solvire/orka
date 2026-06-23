import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

import libcst as cst

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Locator — single source of truth for LibCST node location & signatures.
#
# This module consolidates the CST-finding logic that was previously
# duplicated across:
#   - orka.surgery.synthesizer   (MethodExtractor / ClassExtractor)
#   - orka.surgery.modifier      (MethodBodyReplacer class-stack + docstring)
#   - orka.surgery.transplanter  (m.findall pre-check)
#   - orka.operations.controllers.compiler_node (_SignatureCollector)
#   - orka.operations.controllers.context      (_ParamTypeCollector, regex docblock helper)
#
# LibCST 1.8.x has a FLAT grammar: there is no ``AsyncFunctionDef`` node.
# Async methods are plain ``FunctionDef`` nodes where ``node.asynchronous
# is not None``. We must never define an async-specific visitor method.
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class FunctionSignature:
    """Structured signature info extracted from a ``cst.FunctionDef``.

    Replaces the 8 separate attributes previously held by
    ``_SignatureCollector`` with a single dataclass.
    """

    name: str = ""
    params: List[str] = field(default_factory=list)
    return_annotation: str = ""
    is_async: bool = False
    decorator_count: int = 0
    decorators: List[str] = field(default_factory=list)
    docstring: str = ""


# ── Internal visitors ──────────────────────────────────────────────────


class _MethodFinder(cst.CSTVisitor):
    """Depth-first class-stack visitor that locates a single ``FunctionDef``.

    A method only matches when the live nesting (``_current_depth``) exactly
    equals the requested class hierarchy (``_class_stack``).  For standalone
    functions (empty ``_class_stack``) the match happens at module level
    only (``_current_depth`` empty).

    Async methods are plain ``FunctionDef`` nodes (LibCST flat grammar) and
    are matched identically to sync ones — async has no dedicated visitor.
    """

    def __init__(self, class_stack: List[str], method_name: str) -> None:
        self._class_stack: List[str] = class_stack
        self._method_name: str = method_name
        self._current_depth: List[str] = []
        self.found: Optional[cst.FunctionDef] = None

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._current_depth.append(node.name.value)
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        if self._current_depth:
            self._current_depth.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        # Only record the first match (pre-order traversal).
        if self.found is None and node.name.value == self._method_name:
            if not self._class_stack:
                # Standalone function: match at module level only.
                if not self._current_depth:
                    self.found = node
            elif self._current_depth == self._class_stack:
                self.found = node
        # Never descend into a function body — preserves the historical
        # behaviour of not matching nested function definitions.
        return False


class _ClassFinder(cst.CSTVisitor):
    """Locates the first ``ClassDef`` (anywhere in the tree) by name.

    Pre-order depth-first search; the first match wins.  Mirrors both the
    old ``ClassExtractor`` and ``libcst.matchers.findall(...)[0]``.
    """

    def __init__(self, class_name: str) -> None:
        self._class_name: str = class_name
        self.found: Optional[cst.ClassDef] = None

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        if self.found is None and node.name.value == self._class_name:
            self.found = node
            return False  # Don't descend into the matched class.
        if self.found is not None:
            return False
        return True


# ── Public API ─────────────────────────────────────────────────────────


def find_method(
    tree: cst.Module,
    class_name: Optional[str],
    method_name: str,
) -> Optional[cst.FunctionDef]:
    """Find a method/function by name and class path.

    Uses depth-first class-stack traversal.  Supports dotted ``class_name``
    like ``"Outer.Inner"`` for nested classes.  For standalone functions,
    pass ``class_name=None`` (matches at module level only).

    Returns the ``FunctionDef`` node or ``None`` if not found.  Handles
    async methods correctly (they are ``FunctionDef`` with
    ``asynchronous is not None``).
    """
    class_stack: List[str] = class_name.split(".") if class_name else []
    finder = _MethodFinder(class_stack=class_stack, method_name=method_name)
    tree.visit(finder)
    return finder.found


def find_class(tree: cst.Module, class_name: str) -> Optional[cst.ClassDef]:
    """Find a class by name.  Returns the ``ClassDef`` node or ``None``.

    Returns the first match in pre-order depth-first traversal (matches
    both the old ``ClassExtractor`` and ``m.findall(...)[0]`` semantics).
    """
    finder = _ClassFinder(class_name=class_name)
    tree.visit(finder)
    return finder.found


def get_signature(node: cst.FunctionDef) -> FunctionSignature:
    """Extract structured signature info from a ``FunctionDef``.

    Replaces ``_SignatureCollector``'s 8 separate attributes with a single
    dataclass.  Extracts: name, params (with annotations), return annotation,
    async status (via ``node.asynchronous is not None``), decorator count +
    names, and docstring.
    """
    sig = FunctionSignature()
    sig.name = node.name.value
    sig.is_async = node.asynchronous is not None
    sig.decorator_count = len(node.decorators)

    empty_module = cst.Module(body=[])

    # Decorators — render each decorator's expression (e.g. ``foo(bar=1)``).
    for decorator in node.decorators:
        sig.decorators.append(empty_module.code_for_node(decorator.decorator))

    # Parameters — positional params only, matching the historical
    # _SignatureCollector behaviour (which iterated ``node.params.params``).
    for param in node.params.params:
        p_name = param.name.value if hasattr(param, "name") else str(param)
        p_annotation = ""
        if hasattr(param, "annotation") and param.annotation:
            p_annotation = empty_module.code_for_node(param.annotation.annotation)
        if p_annotation:
            sig.params.append(f"{p_name}: {p_annotation}")
        else:
            sig.params.append(p_name)

    # Return annotation.
    if node.returns:
        sig.return_annotation = empty_module.code_for_node(node.returns.annotation)

    # Docstring (CST-based).
    sig.docstring = extract_docstring(node.body) or ""

    return sig


def extract_docstring(body: cst.BaseSuite) -> Optional[str]:
    """Extract the first docstring from a function body (CST-based).

    Replaces ``modifier._extract_docstring_node`` and
    ``compiler_node._SignatureCollector``'s inline docstring logic.  Returns
    the docstring text (surrounding quotes stripped) or ``None``.
    """
    if not hasattr(body, "body") or not body.body:
        return None
    first_stmt = body.body[0]
    if not isinstance(first_stmt, cst.SimpleStatementLine):
        return None
    if len(first_stmt.body) != 1:
        return None
    expr = first_stmt.body[0]
    if not isinstance(expr, cst.Expr):
        return None
    value = expr.value
    if isinstance(value, cst.SimpleString):
        return _strip_docstring_quotes(value.value)
    if isinstance(value, cst.ConcatenatedString):
        rendered = cst.Module(body=[]).code_for_node(value)
        return _strip_docstring_quotes(rendered)
    return None


def extract_docstring_regex(source: str) -> Optional[str]:
    """Extract the first triple-quoted docstring from source text (regex).

    Replaces the duplicated regex docstring-extraction helper previously in
    the context node.  Handles both triple-double-quote and
    triple-single-quote.  Returns the docstring body (stripped) or ``None``.
    """
    if not source:
        return None
    match = _DOCSTRING_DOUBLE_RE.search(source)
    if match:
        return match.group(1).strip()
    match = _DOCSTRING_SINGLE_RE.search(source)
    if match:
        return match.group(1).strip()
    return None


# ── Internal helpers ───────────────────────────────────────────────────


_DOCSTRING_DOUBLE_RE = re.compile(r'"""(.*?)"""', re.DOTALL)
_DOCSTRING_SINGLE_RE = re.compile(r"'''(.*?)'''", re.DOTALL)


def _strip_docstring_quotes(raw: str) -> str:
    """Strip surrounding quotes from a raw string-literal source fragment.

    Mirrors the historical ``.strip('"').strip("'").strip()`` behaviour used
    by ``compiler_node._SignatureCollector`` so prompt output is unchanged
    for the common triple-quoted docstring cases.
    """
    return raw.strip('"').strip("'").strip()
