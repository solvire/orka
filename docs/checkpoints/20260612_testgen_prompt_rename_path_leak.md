# Session Checkpoint: testgen command, prompt compiler fixes, ARCHITECTURE.md sync

## Changes Made

### 1. Fixed `generate_tests()` to use PromptCompiler
- `orka/orchestrator.py` — replaced `build_testgen_prompt()` with `PromptCompiler.compile()` using `"test"` template
- Added `_load_template()` helper, imports for compiler modules

### 2. Fixed absolute path leak
- `orka/orchestrator.py` — `file_path` in prompt context is now **relative** to `workspace_dir`
- No more `/home/solvire/...` in LLM prompts

### 3. Fixed `{{ system_header }}` rendering
- `orka/core/compiler.py` — all template injection points now get empty context entries if no rules match
- No raw `{{ }}` template syntax in compiled output

### 4. Renamed `gen` → `prompt`
- `orka/cli.py` — command is now `orka prompt --template refactor` (was `orka gen --prompt refactor`)
- `--prompt` flag renamed to `--template` / `-t`

### 5. Added `testgen` CLI command
- `orka/cli.py` — supports `--file`, `--cls`/`--func`, `--method`, `--output`, `--dry-run`, `--run`, `--json`, `--provider`
- Dry-run without `--output` prints tests to stdout

### 6. Updated ARCHITECTURE.md
- Added package layout for `compiler.py`, `templates.py`, `rule_resolver.py`, `import_fixer.py`, `init_helper.py`, `prompts/` directory
- Added test generation pipeline mermaid diagram
- Added prompt compiler engine section with architecture diagram, three-tier hierarchy, context budgeting, key schemas
- Replaced stale "New CLI Flags" section with per-command flag tables

### 7. Created TEST_MANIFEST.md
- `orka/tests/TEST_MANIFEST.md` — 41 test definitions across 4 new test classes

## State at session end

- `git status -s`: modified `cli.py`, `orchestrator.py`, `synthesizer.py`, `ARCHITECTURE.md`; new files in `prompts/`, `core/`, `docs/`
- Prompt compiler works end-to-end
- `testgen` works end-to-end (generates tests, writes to file, runs pytest)
- Tests for the new modules are **not yet written**

## Open items

1. **Generate tests for the new modules** — `testgen` produces unreliable output in one shot. The LLM needs to read source + existing test style + manifest, then ReAct-loop (write → run → fix). Consider building a ReAct loop into `generate_tests()` or as a standalone script.

2. **Migrate `refactor_method()` to PromptCompiler** — still uses `build_synthesis_prompt()` from legacy `synthesizer.py`

3. **Remove legacy functions** — `build_synthesis_prompt()` and `build_testgen_prompt()` from `synthesizer.py` once migration complete

4. **Project-level rules** — `.orka/rules/` directory not yet wired
