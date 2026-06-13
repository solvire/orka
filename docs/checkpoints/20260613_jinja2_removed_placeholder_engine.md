# Session Checkpoint: Jinja2 Removed, `%%var%%` Placeholder Engine

## What Changed

### 1. `orka/core/compiler.py` — Complete rewrite
- **Removed** `from jinja2 import Environment` and `_build_jinja_env()`
- **Removed** all Jinja2 rendering (`env.from_string()`, `.render()`, `DebugUndefined`)
- **Replaced** with pure Python regex: `_PLACEHOLDER_RE = re.compile(r"%%([a-zA-Z_][a-zA-Z_0-9]*)%%")`
- **New `_render_template(source, context, label)`** — regex-based substitution, no dependencies
- **New `_validate_placeholders()`** — logs all `%%var%%` references at compile time (catches typos early)
- Removed `_render_string()` (was Jinja2-specific)
- `PromptCompiler.compile()` API unchanged — same signature, same return type

### 2. Template syntax change: `{{ var }}` → `%%var%%`
- `orka/prompts/templates/test.yaml` — all placeholders updated
- `orka/prompts/templates/refactor.yaml` — all placeholders updated (was also corrupted from a previous partial edit; now fixed)

### 3. `orka/operations/controllers/generator.py` — Removed brace escaping
- **Removed** the manual `{` → `{{` and `}` → `}}` escaping for `existing_code` and `class_context`
- This was the Jinja2 workaround that caused the original bug. With `%%var%%`, no escaping is ever needed because `%%` has zero collision with Python syntax.

### 4. `orka/tests/test_helpers.py` — Updated assertions
- `test_load_real_refactor_template` — now asserts `%%system_header%%` and `%%existing_code%%`
- `test_load_real_test_template` — same update
- `test_load_template_with_injection_points` — uses `%%existing_code%%` in custom template

### 5. `pyproject.toml` — Unchanged
- `jinja2` was never explicitly listed as a dependency (it came through transitively via langchain). No manifest change needed.

## Why `%%var%%`?

Chosen over all stdlib alternatives:

| Syntax | Problem |
|--------|---------|
| `{{var}}` (Jinja2) | Collides with f-string `{x}` → forced `{`→`{{` escaping |
| `{var}` (`str.format()`) | `{x}` in template values raises `KeyError` |
| `$var` (`string.Template`) | `$` in shell, math, finance, git examples looks wrong |
| `%%var%%` (custom) | **Zero collisions** — invisible in all languages |

## Test Results
- **128/128 tests passing** — all existing test suites unchanged (compiler module didn't have its own tests yet)

## What's Next
- Write `tests/test_prompt_compiler.py` — the compiler module is uncovered
- The `prompt` CLI command still uses placeholder context data (needs real extraction wiring)
- The `generate_tests()` in `orchestrator.py` still uses the old `build_synthesis_prompt()` for refactoring — needs migration to PromptCompiler
