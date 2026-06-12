# Phase 2 Checkpoint: `testgen` Command & Prompt Compiler Fixes

## Completed

### 1. Fixed `generate_tests()` to use PromptCompiler
- Replaced `build_testgen_prompt()` call with `PromptCompiler.compile()` using the `"test"` template
- Added imports for `PromptCompiler`, `PromptTemplate`, `InjectionPoint`, `resolve_rules`, `BUILTIN_RULES_DIR`, `yaml`, `Path`
- Added `_load_template()` helper to orchestrator.py for loading YAML templates
- Fixed `resolve_import()` call (was already correct from previous session)
- Removed unused `build_testgen_prompt` import from `synthesizer`

### 2. Fixed absolute path leak in prompts
- `generate_tests()` now strips the workspace directory prefix from `file_path` before sending to LLM
- The prompt shows `orka/core/import_fixer.py` instead of `/home/solvire/.../orka/core/import_fixer.py`
- Prevents leaking developer's local filesystem structure to third-party LLM APIs

### 3. Fixed `{{ system_header }}` rendering in compiler
- `PromptCompiler.compile()` now ensures ALL template injection points have at least empty string values in context
- Previously, injection points with no matching rules would render as literal `{{ variable_name }}` text
- Now renders as empty string (invisible in prompt)

### 4. Added `testgen` CLI command
- Supports: `--file`, `--cls`/`--func`, `--method`, `--output`, `--dry-run`, `--run`, `--json`, `--provider`, `--rule`
- Dry-run without `--output` prints tests to stdout
- `--run` executes `sys.executable -m pytest` after writing
- Handles `--dry-run` correctly even when no `--output` is given

### 5. Fixed dry-run flag propagation
- `generate_tests()` with no `--output` and `--dry-run` now correctly returns `dry_run=True`

## Verification

```bash
# Print compiled tests to stdout (no file written)
orka testgen --file orka/core/import_fixer.py --method resolve_import --json

# Write to file (with run)
orka testgen --file orka/core/import_fixer.py --method resolve_import \
    --output tests/test_import_fixer.py --run

# Dry-run (preview without writing)
orka testgen --file orka/core/import_fixer.py --method resolve_import --dry-run
```

## Files Changed

| File | Change |
|------|--------|
| `orka/orchestrator.py` | Added imports, `_load_template()`, switched to `PromptCompiler`, relative paths |
| `orka/core/compiler.py` | All injection points get empty context entries |
| `orka/cli.py` | Added `testgen` command |

## Next Steps

- **P1**: Migrate `refactor_method()` to use `PromptCompiler` with `"refactor"` template
- **P1**: Remove legacy `build_synthesis_prompt()` and `build_testgen_prompt()` from `synthesizer.py`
- **P2**: Wire project-level `.orka/rules/` support
- **P2**: Add tests for `testgen` command and compiler integration
