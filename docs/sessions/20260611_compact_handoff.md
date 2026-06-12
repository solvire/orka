# Session Handoff: Orka Phase 2 — Prompt Compiler → Test Generator

> Use this prompt to resume after a conversation compact.

## Context

We are building **Orka**, an AI-powered Python code surgery CLI. The tool
is installed at `~/Documents/projects/orka/source/orka/`.  We are actively
dogfooding — using Orka to write Orka.

The project is roughly 4000 lines of Python.  Key layout:

```
orka/
  cli.py                    Typer commands (init, scan, inspect, extract,
                            refactor, gen [NEW])
  orchestrator.py           Pipeline: extract → prompt → LLM → validate → output
  surgery/
    synthesizer.py          Legacy prompt builders (build_synthesis_prompt,
                            build_testgen_prompt) — Strangler Fig pattern
    modifier.py             LibCST method body replacement
  core/
    templates.py            Pydantic schemas: PromptTemplate, InjectionRule,
                            OutputType, InjectionPoint
    rule_resolver.py        .mdc file parser + three-tier rule resolution
    compiler.py             Jinja2 prompt compiler with context budgeting
    import_fixer.py         resolve_import() — deterministic import generation
    validator.py            ast.parse validation (snippet + file)
    ingester.py             NetworkX graph DB + ChromaDB
  prompts/
    templates/
      refactor.yaml         Refactor template (Jinja2, output_type: body)
      test.yaml             Test template (Jinja2, output_type: standalone)
    rules/builtin/
      no_imports_in_body.mdc
      no_markdown_fences.mdc
      use_pytest_raises.mdc
      test_behavior_not_mocks.mdc
  tests/                    13 test files, 106 tests
docs/
  checkpoints/
    20260611_gen_command_and_templates.md    ← latest
  prompts/
    DESIGN.md               Full design brief for prompt architecture
```

## What Was Just Built (Phase 2)

The `orka gen` command is wired into the CLI.  It loads a YAML template,
resolves `.mdc` rule files through a three-tier hierarchy (builtin tier=1,
project tier=2, CLI tier=3), and compiles the prompt using Jinja2.

```bash
# Test it:
$ cd ~/Documents/projects/orka/source/orka
$ source env/bin/activate
$ orka prompt --template refactor
$ orka prompt --template test --rule use_pytest_raises --rule test_behavior_not_mocks
```

NOTE: The `prompt` command currently uses **placeholder values** for context
data (`existing_code = "def example(): pass"`).  Phase 3 wires real source
extraction.

## What We Were Working On (Before Detour)

We were building a **test generation feature**.  The original approach was:

1. `orka/core/import_fixer.py` — `resolve_import()` — deterministic import
   resolution from file path → module path (ALREADY BUILT).
2. `generate_tests()` method in `orchestrator.py` — already exists as a
   placeholder.  We used `orka refactor` to replace its body, but the LLM
   output had bugs (wrong positional args to `resolve_import`, wrong diff
   logic).  The file currently has the **LLM-generated buggy version**.

## Immediate Next Step

**Fix `generate_tests()` in `orka/orchestrator.py` and wire it to `orka gen`.**

The method currently has issues:
- `build_testgen_prompt()` is called with `method_name`/`class_name` kwargs
  that don't exist on the function (it takes `existing_code`, `class_context`,
  `file_path`)
- `resolve_import(file_path, method_name, class_name)` is called positionally
  but the signature is `resolve_import(file_path, class_name=..., method_name=..., ...)`
- It's not using the `PromptCompiler` at all yet — still calls the legacy
  `build_testgen_prompt()` directly

The goal: `generate_tests()` should:
1. Extract source + class context (same as today)
2. Use `PromptCompiler.compile()` with the `"test"` template instead of
   calling `build_testgen_prompt()` directly
3. Use `resolve_import()` with correct args
4. Return `RefactorResult` with `tests_content` for stdout or write to
   `--output` file

The CLI command is already designed:

```bash
orka testgen --file app.py --method process --cls OrderController \
    --output tests/test_processor.py --run
```

But we never finished writing it in `cli.py`.  There's only a placeholder
from an earlier session.

## What To Use

- **Dogfood with `orka refactor`** to fix `generate_tests()` in orchestrator.py
- Read `build_testgen_prompt()` in `synthesizer.py` — this is what we're
  replacing with the compiler
- Read `resolve_import()` in `import_fixer.py` — the correct call signature
- Read the `test.yaml` template at `orka/prompts/templates/test.yaml`
- The `PromptCompiler` usage pattern is in `cli.py` `gen()` command

## What NOT To Do

- Don't delete `synthesizer.py` — Strangler Fig pattern; remove the old
  functions only after everything uses the compiler
- Don't modify `templates.py`, `rule_resolver.py`, or `compiler.py` — they
  are stable and tested
- Don't modify `import_fixer.py` — it's stable

## File States (Critical)

- **`orka/orchestrator.py`** — has an LLM-generated buggy `generate_tests()`
  that needs fixing.  The imports for `build_testgen_prompt` and
  `resolve_import` were added correctly.  `RefactorResult.tests_content`
  field was added correctly.
- **`orka/cli.py`** — has the `gen` command working.  The `testgen` command
  is NOT added yet (only a placeholder from an earlier session that was
  removed).  Needs a new `testgen` Typer command.
- **`orka/core/import_fixer.py`** — already exists, stable, tested.

## Verification

After fixing `generate_tests()` and adding the `testgen` CLI command:

```bash
# Dry-run test generation (prints to stdout)
$ cd ~/Documents/projects/orka/source/orka
$ source env/bin/activate
$ orka testgen --file orka/core/import_fixer.py --method resolve_import --dry-run

# Write to a test file
$ orka testgen --file orka/core/import_fixer.py --method resolve_import \
    --output /tmp/test_import_fixer.py

# Generate and run
$ orka testgen --file orka/core/import_fixer.py --method resolve_import \
    --output /tmp/test_import_fixer.py --run
```

## Key Files Reference

| Purpose | Path |
|---------|------|
| CLI entry point | `orka/cli.py` |
| Orchestrator (needs fixing) | `orka/orchestrator.py` |
| Import fixer (stable) | `orka/core/import_fixer.py` |
| Test template | `orka/prompts/templates/test.yaml` |
| Prompt compiler | `orka/core/compiler.py` |
| Rule resolver | `orka/core/rule_resolver.py` |
| Schemas | `orka/core/templates.py` |
| Latest checkpoint | `docs/checkpoints/20260611_gen_command_and_templates.md` |
| Design brief | `docs/prompts/DESIGN.md` |
