# Checkpoint: Surgery Graph Built & Dogfooded

## Summary
Built the `orka/operations/` module — a LangGraph-powered deterministic state machine for code surgery (refactoring + test generation). Successfully dogfooded by generating tests for the module's own `truncate_error_summary` and `extract_error_summary` functions.

## What Was Built

### `orka/operations/` Module

| File | Purpose |
|---|---|
| `state.py` | `SurgeryState` TypedDict — bounded fields, no unbounded `messages` list |
| `helpers.py` | Shared utilities: `load_template()`, `extract_error_summary()`, `truncate_error_summary()`, `build_fixer_prompt()` |
| `graph.py` | LangGraph wiring — 4 nodes + conditional edges + terminal rollback |
| `controllers/context.py` | Node 1: extract method/class source, ChromaDB lookup, file backup |
| `controllers/generator.py` | Node 2: YAML template → PromptCompiler → LLM with structured output |
| `controllers/validator.py` | Node 3: Gate 1 (snippet AST) → Assembly (LibCST/import_fixer) → Gate 2 (file AST) → Pytest + truncation |
| `controllers/fixer.py` | Node 4: fix prompt builder → LLM → validated fix |

## Key Bug Found & Fixed

**Jinja2 variable collision bug** — When `existing_code` contained f-string curly braces (e.g., `f"{head}...{tail}"`), Jinja2 consumed them as template variables during prompt compilation, silently emptying parts of the code. The LLM then received corrupted code and returned empty output.

**Fix:** Escape `{` → `{{` and `}` → `}}` in `existing_code` and `class_context` before passing to the PromptCompiler (in `generator.py`).

## Dogfooding Results

Generated **12 tests** for `truncate_error_summary()` and **9 tests** for `extract_error_summary()` via the surgery graph itself, covering:
- Empty strings
- Boundary conditions (under/at/over max_chars)
- Line-boundary-aware truncation
- Negative/zero max_chars
- FAILURES section extraction
- Multiple-line traceback preservation

## Test Results
All 114 existing tests pass.
