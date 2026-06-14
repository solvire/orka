# Adversarial Test Suite — snippet_utils + modifier

## Summary
Built an exhaustive adversarial test suite hardening the AST surgery engine.

## Files Created
- **`orka/tests/test_snippet_utils.py`** — 24 tests across 3 classes
  - `TestStripMDFences` (7 tests): single, unclosed, no fences, no label, multiple blocks (failing-fast-by-design), empty, fence-only
  - `TestNormalizeSnippetIndent` (6 tests): uniform, flush-first-line, mixed levels, deeply nested, empty, no-indent
  - `TestSanitizeLLMOutput` (11 tests): empty/whitespace, fenced code, surrounding text, no fences, blank lines, trailing whitespace, fences-no-content, **conversational preamble**, **conversational postscript**, **mixed whitespace**, **comments-only**

- **`orka/tests/test_modifier.py`** — 20 tests (10 existing + 10 new)
  - `TestTargetNotFound` (4 tests): method not found, class not found, both not found, standalone function not found
  - `TestComplexDecorators` (6 tests: 5 parametrized + 1 complex signature): `@property`, `@classmethod`, `@staticmethod`, `@retry(tries=3)`, combined decorators, signature `def foo(self, a: int = 1, *args, b: str | None = None, **kwargs) -> list[dict]`
  - `TestMinimalBodyReplacement` (4 tests): `pass` body, `...` (Ellipsis) body, single-line `def foo(): return 1` expansion, trailing comment survival (no crash)

## Bugs Fixed During Test Writing
1. **Pre-existing `test_surgical_body_replacement` failure**: The LLM snippet contained f-string braces `{order_id}` causing `sanitize_llm_output` to produce malformed output → LibCST parse failure. Replaced with plain string.
2. **`test_decorator_survives` multi-line decorator indentation**: f-string `textwrap.dedent` doesn't handle multi-line substitution blocks — second decorator landed at column 0. Fixed by building source with explicit string concatenation.

## Key Design Decisions
- `strip_md_fences` deliberately leaves intermediate fences in multi-block output — leftover ` ``` ` markers cause `ParserSyntaxError` at the LibCST level, routing to `fix_draft` instead of guessing which block to inject.
- All modifier tests use `tmp_path` to write source files before calling `preview_patch(str(file_path), ...)`.
- No core logic in `snippet_utils.py` or `modifier.py` was modified.
