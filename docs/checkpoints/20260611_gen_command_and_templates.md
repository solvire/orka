# Checkpoint: `orka gen` Command + Template/Rule Files

## Summary

Phase 2 of the Prompt Compilation Engine.  Created the template and rule
directories, extracted the two existing prompt patterns into YAML templates,
and wired the `orka gen` CLI command to load, resolve, and compile prompts.

## Files Created

### `orka/prompts/templates/refactor.yaml`

Extracted from `build_synthesis_prompt()` in `synthesizer.py`.  Jinja2 template
with injection points: `system_header`, `constraints_top`, `constraints_bottom`,
`quality_gates`.  Output type: `body`.

### `orka/prompts/templates/test.yaml`

Extracted from `build_testgen_prompt()` in `synthesizer.py`.  Jinja2 template
with additional `style_guide` injection point.  Output type: `standalone`.

### `orka/prompts/rules/builtin/no_imports_in_body.mdc`

Builtin rule (priority 10, `constraints_top`, applies to `*`).
Prevents the LLM from including import statements.

### `orka/prompts/rules/builtin/no_markdown_fences.mdc`

Builtin rule (priority 20, `constraints_top`, applies to `*`).
Prevents the LLM from wrapping output in markdown code blocks.

### `orka/prompts/rules/builtin/use_pytest_raises.mdc`

Builtin rule (priority 30, `constraints_bottom`, applies to `test`).
Encourages `pytest.raises()` over try/except.

### `orka/prompts/rules/builtin/test_behavior_not_mocks.mdc`

Builtin rule (priority 30, `quality_gates`, applies to `test`).
Encourages testing observable behaviour over mocking internals.

### `orka/prompts/__init__.py`

Package init for the prompts directory.

## Files Modified

### `orka/cli.py`

- Added imports for `PromptCompiler`, `PromptTemplate`, `InjectionPoint`,
  `resolve_rules`, `BUILTIN_RULES_DIR`, `PROJECT_RULES_DIRNAME`, and `yaml`.
- Added `_load_template(name)` helper — loads a YAML template file from
  `orka/prompts/templates/` and returns a `PromptTemplate` instance.
- Added `@app.command(name="gen")` with flags: `--prompt` (required template
  name), `--rule` (repeatable CLI rule names), `--file`, `--cls`, `--method`.
- The `gen` command: loads template → resolves rules (three-tier hierarchy) →
  assembles placeholder context data → compiles via `PromptCompiler` →
  prints to terminal via Rich.

## Verified

- `orka gen --prompt refactor` — loads 2 builtin rules, compiles 1024 chars
- `orka gen --prompt test --rule use_pytest_raises --rule test_behavior_not_mocks`
  — loads 4 rules (2 builtin tier=1 + 2 CLI tier=3), compiles 1323 chars
- All four modules compile cleanly via `py_compile`

## Still on Deck (Phase 3)

1. Replace `build_synthesis_prompt()` and `build_testgen_prompt()` calls in
   the orchestrator with `PromptCompiler.compile()`.
2. Wire real source extraction into the `gen` command context data (replace
   placeholder values).
3. Move `refactor` and `testgen` (or `generate_tests`) commands to use
   `gen` internally.
4. Support `.orka/rules/` project-level rules directory.
5. Write proper pytest tests for the YAML loading, rule resolution
   integration, and CLI output.
