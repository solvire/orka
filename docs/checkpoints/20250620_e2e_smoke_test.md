# Session Checkpoint: E2E Smoke Test

**Date:** 2025-06-20
**Status:** ✅ Complete — test passes repeatably (~8s, real LLM call)

## What was done

1. **Architectural diagnostic** confirmed `context.py` is already hardened against ChromaDB failures (`os.path.isdir` guard + `try/except Exception` block). Task 1 (Hardening ChromaDB) was already complete.

2. **Created `orka/tests/test_e2e_smoke.py`** — a live E2E test that exercises the entire LangGraph pipeline with a real LLM call:
   - `gather_context` → `compile_prompt` → `generate_draft` → `validate_draft` → terminal
   - Uses `dry_run=True` (no disk write, no pytest — AST validation only)
   - Module-level `pytestmark = skipif` when no API key is configured
   - 6 assertions: `is_valid`, `fatal_error is None`, `+ 10` in snippet, assembled file non-empty, `Calculator` preserved, source file unmodified

3. **Updated `orka/tests/TEST_MANIFEST.md`** — added E2E smoke test section, updated total to 198 tests / 16 files.

## Results

```
pytest orka/tests/test_e2e_smoke.py -v -s
1 passed in 7.91s  (repeatable)
```

## Pre-existing failures (not introduced this session)

- `test_orchestrator_refactor_wraps_run_surgery` — diff is empty on dry-run success
- `test_basic_prompt` — prompt command output format changed
- `test_load_real_test_template` — template no longer contains `%%system_header%%`

## Key architectural facts confirmed

| Component | Key | Behavior |
|-----------|-----|----------|
| `run_surgery()` | Entry point | 9 params, returns `dict[str, Any]` (SurgeryState) |
| `SurgeryState` | 22 keys | TypedDict with bounded fields, no `messages` list |
| `context.py` | ChromaDB | `os.path.isdir` guard + `try/except Exception` → `[]` |
| `validator.py` | `dry_run=True` | Stops after Gate 3 (AST), skips disk write + pytest |
| `cli.py` | `--dry-run` | Forces JSON output, passes `dry_run=True` to state |
