# Orka: Headless Operation Convergence

**Date:** June 15, 2026
**Phase:** Infrastructure — Config & Test Hygiene
**Status:** Complete

## 1. Context: Why This Session Happened

The codebase had three distinct problems blocking reliable headless (CI) operation:

1. **Eager config crash:** `orka/config.py` called `sys.exit(1)` at import time if `.env` was missing. This meant `orka --help`, `orka doctor`, and any other command would crash before Typer could render anything — even for commands that don't need API keys. CI pipelines and new-user onboarding were broken.

2. **Inconsistent LLM output sanitization:** The generator node used `sanitize_llm_output()` (a 6-pass pipeline: strip fences, dedent, remove preamble/postscript, normalize whitespace), but the fixer node used `OrkaLangChainClient.fix_md_fences()` (a single-regex fence stripper). This meant the fix loop could produce output that passes one pipeline but fails the other, causing unpredictable behavior.

3. **Stale test imports:** Three test files imported from modules that had been renamed or split during the prompt compiler refactoring:
   - `test_cli_commands.py` imported `cli` from `orka.cli` (exports `app`)
   - `test_orka_synthesizer.py` imported `build_synthesis_prompt` (function deleted)
   - `test_prompt_compiler.py` imported everything from `orka.core.prompt_compiler` (module split into `rule_resolver.py`, `compiler.py`, `import_fixer.py`)

These were blocking `pytest --co` from running cleanly.

## 2. Architecture Decisions

### Decision 1: Make config loading non-fatal

- **Decision:** Replace `sys.exit(1)` in `_load_env()` with `logger.debug()` / `logger.warning()`. Remove `import sys`.
- **Rationale:** The `.env` file is only needed when an LLM client is instantiated. `OrkaClientFactory.create()` already raises `RuntimeError` with a clear message when an API key is missing. Fatal exit at import time violates the principle of lazy validation.
- **Tradeoffs accepted:** Users won't see a bold FATAL message at startup if `.env` is missing. The `logger.debug()` message is only visible with `ORKA_VERBOSE=true`. This is acceptable — the real error (missing API key) surfaces at the point of use with a clear message.

### Decision 2: Converge on `sanitize_llm_output()`

- **Decision:** Replace `OrkaLangChainClient.fix_md_fences()` with `sanitize_llm_output()` in the fixer node.
- **Rationale:** Both LLM-invoking nodes in the surgery graph must use the same sanitization pipeline. If one strips differently than the other, the fix loop becomes inconsistent.
- **Tradeoffs accepted:** `fix_md_fences()` still exists as a static method on `OrkaLangChainClient` — it could be removed in a future cleanup, but that's not this task's scope.

### Decision 3: Delete `test_orka_synthesizer.py` rather than fix it

- **Decision:** Delete the file entirely.
- **Rationale:** The `build_synthesis_prompt` function was replaced by the prompt compiler pipeline. The remaining functions in `synthesizer.py` (`extract_method_source`, `extract_class_source`) are tested by `test_orka_transplanter.py`. The prompt compilation has its own test file (`test_prompt_compiler.py`). Keeping a test file that tests a deleted function serves no purpose.

### Decision 4: Rewrite `test_prompt_compiler.py` for the new module structure

- **Decision:** Full rewrite of imports, test data format, and API calls.
- **Rationale:** The module was split from `prompt_compiler` into three modules (`rule_resolver`, `compiler`, `import_fixer`). The API changed from standalone functions to classes (`PromptCompiler`). The `.mdc` file format requires `---` frontmatter delimiters that the old tests didn't include.
- **Tradeoffs accepted:** The test file is significantly different from its predecessor, making `git diff` noisy. But the test logic is the same — only the input mechanism and API calls changed.

## 3. Files Changed

### Modified
| File | Key Changes |
|---|---|
| `orka/config.py` | Removed `sys.exit(1)` from `_load_env()`, added `logging` module, replaced `print()` with `logger.debug()`/`logger.warning()`, removed `import sys` |
| `orka/operations/controllers/fixer.py` | Added `from orka.core.snippet_utils import sanitize_llm_output`, swapped `OrkaLangChainClient.fix_md_fences()` → `sanitize_llm_output()` |
| `orka/tests/test_cli_commands.py` | Fixed import: `from orka.cli import cli` → `from orka.cli import app as cli` |
| `orka/tests/test_prompt_compiler.py` | Full rewrite — imports from `rule_resolver`/`compiler`/`import_fixer`, `.mdc` files use `---` frontmatter, `resolve_rules` writes to directories, `compile_prompt()` → `PromptCompiler().compile()`, budget test matches real behavior, template tests include new context keys |

### Deleted
| File | Reason |
|---|---|
| `orka/tests/test_orka_synthesizer.py` | Imported `build_synthesis_prompt` which no longer exists; remaining functions tested elsewhere |

## 4. Active Design Patterns

- **Lazy validation:** Config loading is non-fatal. API key validation happens at LLM client instantiation time, not import time. This is the correct pattern for CLI tools — `--help` must always work.
- **Unified sanitization pipeline:** Both LLM-invoking nodes (`generate_draft`, `fix_draft`) now use `sanitize_llm_output()`. This ensures the fix loop is consistent.
- **`.mdc` frontmatter format:** All rule files use `---` delimited YAML frontmatter (the Continue.dev/Cursor convention). Tests must write files in this format.

## 5. Outstanding Work / Next Steps

- [ ] `test_cli_commands.py` still has 4 failures — Typer's `app` object isn't directly compatible with Click's `CliRunner`. Needs a deeper fix (possibly using `typer.testing.CliRunner` or wrapping `app` as a Click group).
- [ ] `test_helpers.py` has 2 stale expectations about template injection points — the YAML templates evolved but the tests didn't.
- [ ] `test_orchestrator.py` has 1 failure — dry-run refactor produces empty diff.
- [ ] `OrkaLangChainClient.fix_md_fences()` could be removed in a future cleanup since nothing calls it anymore.
- [ ] Consider adding `ORKA_VERBOSE` check before logging debug messages about missing `.env`.
