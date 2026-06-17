# Session Checkpoint — Bugfix handoff prompt

## Context

We dogfooded `orka testgen` on the new `SnippetImportExtractor` class we added to `orka/surgery/modifier.py`. It produced valid tests (8 pass), but we found two bugs in the pipeline that caused a duplicate import in the output.

## Current git diff (3 files changed, 61 insertions, 4 deletions)

`git diff` shows:

1. **orka/surgery/modifier.py** — The new `SnippetImportExtractor` CST transformer (36 lines, already committed before this session).
2. **orka/operations/controllers/validator.py** — Removed `import pytest\n` from `_assemble_test_file()`.
3. **orka/operations/controllers/compiler_node.py** — Partial fix for Bug A (normalised `actual_module` by stripping class name from tail, and updated `target_import` to use `class_name` when available).
4. **orka/tests/TEST_MANIFEST.md** — Added documentation for the new 8-test file.

## Known issue: `re` import still in validator.py

The `_strip_import_lines` regex helper was added and then replaced with a better approach, but there's a stale `import re` that was accidentally left in the file. It needs to be removed — `re` is no longer used.

## Remaining work: Bug B (import dedup in assembly)

Bug B is NOT yet fixed. Here's what's needed:

### The problem
The `_assemble_test_file()` function in `orka/operations/controllers/validator.py` does:
```python
result = f"{import_stmt}{clean_snippet}\n"
```

`import_stmt` is determined by `resolve_import()` (correct). But the LLM-generated `snippet` often also contains import statements (despite being told not to). These end up duplicated in the output.

### The fix needed
Before concatenation, strip any import statements from the LLM's snippet. Use the `SnippetImportExtractor` CST transformer (already in `orka/surgery/modifier.py`) to do this properly — it handles edge cases like `import os; x = 1` on the same line. Do NOT use regex.

In `_assemble_test_file()`, before the `result = ...` line:
```python
import libcst as cst
from orka.surgery.modifier import SnippetImportExtractor

try:
    tree = cst.parse_module(snippet)
    extractor = SnippetImportExtractor()
    clean_tree = tree.visit(extractor)
    clean_snippet = clean_tree.code
except Exception:
    clean_snippet = snippet  # fallback
```

Then use `clean_snippet` in the result assembly instead of raw `snippet`.

### Verification
Run: `orka testgen --file orka/surgery/modifier.py --cls SnippetImportExtractor --method leave_SimpleStatementLine --dry-run --json`

The output should NOT contain duplicate `from orka.surgery.modifier import SnippetImportExtractor` or `import libcst as cst` — those should only appear once via the deterministic `resolve_import()`.

Also run: `cd orka && python -m pytest tests/test_snippet_import_extractor.py tests/test_modifier.py -v --tb=short`
