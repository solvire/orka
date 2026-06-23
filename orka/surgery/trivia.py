import logging
from typing import List

import libcst as cst
from orka.core.locator import extract_docstring

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Trivia — whitespace & docstring preservation helpers for CST mutations.
#
# Pure functions only: no state, no side effects. ``preserve_docstring``
# operates on LibCST nodes (depends on libcst + orka.core.locator); the
# spacing helpers are pure string operations and need no CST parsing.
# ═══════════════════════════════════════════════════════════════════════


def preserve_docstring(
    original_body: cst.IndentedBlock,
    new_body: cst.IndentedBlock,
) -> cst.IndentedBlock:
    """Prepend ``original_body``'s docstring onto ``new_body`` when missing.

    Extracted from
    ``modifier.MethodBodyReplacer._apply_with_docstring_preservation``.

    * If ``original_body`` has a docstring and ``new_body`` lacks one, the
      original docstring statement node is prepended **verbatim** so exact
      formatting (quotes, prefixes, indentation) is preserved.
    * Returns ``new_body`` unchanged when both bodies have a docstring or
      neither does — the new body always takes precedence when it carries
      its own docstring.
    """
    original_doc = extract_docstring(original_body)
    new_doc = extract_docstring(new_body)

    if original_doc is not None and new_doc is None:
        original_docstring_node = original_body.body[0]
        return new_body.with_changes(
            body=(original_docstring_node,) + new_body.body
        )

    return new_body


def collapse_blank_lines(source: str, max_consecutive: int = 1) -> str:
    """Collapse runs of more than ``max_consecutive`` blank lines.

    A blank line is a line that is empty or whitespace-only. Any run of
    ``max_consecutive + 1`` or more consecutive blank lines is reduced to
    exactly ``max_consecutive``; shorter runs are left untouched. A single
    trailing newline (if present) is preserved and not counted as a blank
    line. Used by :func:`normalize_spacing`.
    """
    trailing_newline = source.endswith("\n")
    lines = source.split("\n")
    if trailing_newline:
        # Drop the empty-string artifact produced by the final newline so
        # it isn't mistaken for a trailing blank line.
        lines = lines[:-1]

    lines = _collapse_blank_runs(lines, max_consecutive=max_consecutive)

    result = "\n".join(lines)
    if trailing_newline:
        result += "\n"
    return result


def normalize_spacing(source: str) -> str:
    """Normalize whitespace in Python source (pure string operations).

    * Collapse 2+ consecutive blank lines into 1.
    * Ensure a single blank line after the leading imports section.
    * Ensure a single blank line before module-level class/function defs.
    * Remove trailing whitespace on each line.

    No CST parsing is performed — the rules are line-based heuristics
    suitable for cleaning up LLM-generated snippets.
    """
    # 1. Remove trailing whitespace on each line (this also normalizes
    #    whitespace-only lines to empty strings).
    lines = [line.rstrip() for line in source.split("\n")]

    # Track & strip the trailing-newline artifact so it isn't treated as a
    # blank line; a single trailing newline is restored at the end.
    trailing_newline = False
    if lines and lines[-1] == "":
        trailing_newline = True
        lines = lines[:-1]

    # 2. Collapse 2+ consecutive blank lines into 1.
    lines = _collapse_blank_runs(lines, max_consecutive=1)

    # 3. Ensure a single blank line after the leading imports section.
    lines = _ensure_blank_after_imports(lines)

    # 4. Ensure a single blank line before module-level class/function defs.
    lines = _ensure_blank_before_defs(lines)

    result = "\n".join(lines)
    if trailing_newline:
        result += "\n"
    return result


# ── Internal helpers ───────────────────────────────────────────────────


def _collapse_blank_runs(lines: List[str], max_consecutive: int = 1) -> List[str]:
    """Collapse consecutive blank-line runs to at most ``max_consecutive``.

    Operates on a list of already-rstripped lines (blanks are ``""``).
    """
    result: List[str] = []
    blank_run = 0
    for line in lines:
        if line.strip() == "":
            blank_run += 1
            continue
        if blank_run:
            result.extend([""] * min(blank_run, max_consecutive))
            blank_run = 0
        result.append(line)
    if blank_run:
        result.extend([""] * min(blank_run, max_consecutive))
    return result


def _is_import_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("import ") or stripped.startswith("from ")


def _ensure_blank_after_imports(lines: List[str]) -> List[str]:
    """Ensure exactly one blank line after the leading imports section.

    The leading section may contain blank lines, comments and import
    statements. If it contains at least one import, exactly one blank line
    is placed between the section and the first line of real code.
    """
    n = len(lines)
    i = 0
    saw_import = False
    while i < n:
        stripped = lines[i].strip()
        if stripped == "" or stripped.startswith("#"):
            i += 1
            continue
        if _is_import_line(lines[i]):
            saw_import = True
            i += 1
            continue
        break  # first line of real code

    if not saw_import or i >= n:
        return lines

    # Walk back from the first code line to the last non-blank header line.
    header_end = i - 1
    while header_end >= 0 and lines[header_end].strip() == "":
        header_end -= 1
    if header_end < 0:
        return lines

    return lines[: header_end + 1] + [""] + lines[i:]


def _ensure_blank_before_defs(lines: List[str]) -> List[str]:
    """Ensure exactly one blank line before module-level class/function defs.

    A def block may be preceded by col-0 decorator lines (``@...``); the
    blank line is placed before the first decorator. Indented (nested)
    defs are left untouched. No leading blank is added at the start of the
    file.
    """
    n = len(lines)
    starts: set = set()
    for i, line in enumerate(lines):
        if not line or line[0].isspace():
            continue
        if not (
            line.startswith("class ")
            or line.startswith("def ")
            or line.startswith("async def ")
        ):
            continue
        # Walk back past col-0 decorator lines (which immediately precede
        # the def — never blank lines, which are the separator we normalize)
        # to find the true start of the def/decorator block.
        j = i
        while j > 0:
            prev = lines[j - 1]
            if prev and not prev[0].isspace() and prev.startswith("@"):
                j -= 1
                continue
            break
        if j > 0:
            starts.add(j)

    result: List[str] = []
    for idx, line in enumerate(lines):
        if idx in starts:
            while result and result[-1].strip() == "":
                result.pop()
            if result:
                result.append("")
        result.append(line)
    return result
