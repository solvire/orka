# Checkpoint: Class Context, Validator, Standalone Functions, Structured Output & Dry-Run

## Completed

### Phase 1A — Class Context in Refactor Prompt

**File:** `orka/orchestrator.py`

- Before building the LLM prompt, the orchestrator calls `extract_class_source()` to get the full class body
- Passed as `class_context` to `build_synthesis_prompt()` — the parameter already existed but was never populated
- The LLM now sees the entire class (sibling methods, class variables, decorators) instead of just the isolated method

### Phase 1B — Reusable Code Validator

**New file:** `orka/core/validator.py`

Two validation functions, both using `ast.parse()`:

1. **`validate_code_snippet(code, label)`** — Validates raw LLM output before it touches disk. Wraps body-level snippet in a dummy function so bare statements (`return x`) parse correctly.
2. **`validate_file(file_path)`** — Validates a Python file on disk after a patch has been applied.

Both return a `ValidationResult` with `passed`, `error`, `lineno`, and `msg` attributes (truthy dataclass).

**New file:** `orka/tests/test_validator.py` — 28 tests.

### Phase 2A — Standalone Function Support

`--cls` is now `Optional[str] = None` throughout the pipeline:

- **`orka/cli.py`**: `--cls` is `Optional[str]`; added `--func` as alias (mutually exclusive with `--cls`)
- **`orka/orchestrator.py`**: `refactor_method()` accepts `class_name: Optional[str] = None`; skips class context extraction and graph constraints when `None`
- **`orka/surgery/synthesizer.py`**: No changes needed — both `MethodExtractor` and `ClassExtractor` already handled `target_class=None`

**New file:** `orka/tests/test_standalone_function.py` — 9 tests (extraction + patching).

### Phase 2B — Structured JSON Output

- **`RefactorResult` dataclass** in `orka/orchestrator.py` with fields: `success`, `label`, `file_path`, `diff`, `dry_run`, `error`
- `refactor_method()` returns `RefactorResult` instead of bare `bool`
- **`_compute_diff()`** — uses `difflib.unified_diff` to compute patch
- **`_target_label()`** — consistent display naming
- **`--json` flag** in CLI — emits single JSON line via `_emit_json()`

**New file:** `orka/tests/test_refactor_result.py` — 9 tests (dataclass + diff).

### Phase 2C — Dry-Run Mode

- **`preview_patch()`** in `orka/surgery/modifier.py` — mirrors `apply_llm_patch()` but returns patched source string instead of writing to disk. `apply_llm_patch()` now delegates to it internally.
- **`--dry-run` flag** in CLI — implies `--json`; skips background scan
- When `dry_run=True`, orchestrator calls `preview_patch()` instead of `apply_llm_patch()`, validates via `ast.parse()`, computes diff, returns `RefactorResult(dry_run=True)` — never touches disk

### Integration in Orchestrator

The orchestrator's `refactor_method()` pipeline:

```
Capture file before content
  1. Gather graph constraints
  2. extract_method_source()
  3. extract_class_source()             [Phase 1A]
  4. build_synthesis_prompt()
  5. Invoke LLM
  6. validate_code_snippet()            [Phase 1B]
  7. dry_run? → preview_patch() in memory  [Phase 2C]
     else    → apply_llm_patch() to disk
  8. validate_file()                    [Phase 1B]
  9. _compute_diff()
  → Return RefactorResult               [Phase 2B]
```

## Tests

All 106 tests pass (non-API):
- 28 validator tests
- 19 transplanter tests
- 6 cascade tests
- 3 modifier tests
- 9 standalone function tests
- 9 refactor result tests
- Various others (synthesizer, analyzer, edge cases, ingester)

## Next up

- Phase 3A: `orka testgen` command (follows same pipeline, different prompt + output)
- Phase 3B: Improved dependency analysis with LibCST
- Phase 3C: Multi-method refactoring
