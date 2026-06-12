# Session Restart Prompt

Copy and paste this into a new session after compacting history.

---

## Session Setup

```bash
cd /home/solvire/Documents/projects/orka/source/orka
source env/bin/activate
```

## What We Know

### The system instruction in `generator.py` overrides the template system prompt

The `test.yaml` template has a well-structured system prompt:
```yaml
system: |
  You are a pytest specialist working on a Python codebase.
  {{ system_header }}
```

But `generator.py` replaces it entirely with a 3-line generic instruction:

```python
system_instruction = (
    "You are a pytest specialist. Output ONLY a single raw Python "
    "test function - no imports, no module docstrings, no markdown "
    "fences. Generate exactly one function per response."
)
```

**Fix applied:** Updated to include `def test_(tmp_path)` + no monkeypatch + all code inside function. But the override pattern itself is wrong — the template's system should be used.

### Jinja2 is unnecessary complexity

- Templates only use `{{ variable }}` — no `{% for %}`, `{% if %}`, filters
- Jinja2 `{{ }}` collides with f-string `{ }` in Python code, requiring manual escaping in `generator.py` line 59:
  ```python
  escaped_code = existing_code.replace("{", "{{").replace("}", "}}")
  ```
- **Goal:** Replace with `string.Template` (`$var` syntax) — stdlib, no escaping issues

### The LLM doesn't see model schemas

When generating tests for `load_template`, the LLM invents field names like `result.system_prompt` because it never sees the `PromptTemplate` model definition (it's 150 lines below in a different module). `class_context` is empty for standalone functions.

### The `prompt` CLI command uses placeholder data

`cli.py` line ~580 hardcodes:
```python
context_data = {
    "existing_code": "def example():\n    pass",
    "class_context": "class Placeholder:\n    pass",
}
```
So `orka prompt --template test` shows fake data. The real `testgen` run works correctly because `gather_context` populates `SurgeryState` before `generator` runs.

## What to Read First

1. `docs/checkpoints/` — newest checkpoint files first
2. `docs/ARCHITECTURE.md` — package layout, pipeline diagrams
3. `orka/core/compiler.py` — **needs Jinja2 → string.Template swap**
4. `orka/operations/controllers/generator.py` — **remove escaping after swap**
5. `orka/prompts/templates/test.yaml` — **change `{{ }}` to `$var`**
6. `orka/prompts/templates/refactor.yaml` — same
7. `orka/operations/controllers/validator.py` — assembly logic (Gate 2)
8. `orka/operations/helpers.py` — fixer prompt builder (already updated)
9. `orka/operations/graph.py` — surgery graph wiring
10. `orka/cli.py` — CLI commands

## Existing Test Files

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_validator.py` | 20 | Validator |
| `tests/test_helpers.py` | 14 | load_template, extract_error_summary, truncate_error_summary, build_fixer_prompt |
| `tests/test_orchestrator.py` | 2 | Orchestrator pipeline |
| `tests/test_modifier.py` | 1 | LibCST patching |
| `tests/test_standalone_function.py` | 9 | Function extraction & patching |
| `tests/test_refactor_result.py` | 9 | RefactorResult + diff |
| `tests/test_orka_synthesizer.py` | 12 | Method/class extraction |
| `tests/test_orka_analyzer.py` | 18 | Dependency analysis |
| `tests/test_orka_cascade.py` | 5 | Import cascade |
| `tests/test_orka_dual_brain.py` | 2 | Graph + ChromaDB |
| `tests/test_orka_edge_cases.py` | 4 | Edge cases |
| `tests/test_orka_transplanter.py` | 16 | Class extraction |
| `tests/test_ingester.py` | 4 | Graph DB |

**Total: 116 tests, all passing.**

## Next Goals

### 1. Swap Jinja2 → string.Template

`compiler.py` → replace with `string.Template`. Templates use `$var` syntax instead of `{{ var }}`. Remove the escape logic in `generator.py`. Run all tests after.

### 2. Make the LLM see model schemas

In `context.py` gather step, when the target is a standalone function that returns a known type, include the return type's class source. For `load_template`, that means including `PromptTemplate`'s definition.

### 3. Fix the system instruction override

Instead of replacing the template's system prompt in `generator.py`, concatenate: template system + generator's additional instruction + inject `{{ system_header }}` rules. The template owner should control the base system message.

### 4. Fill missing test coverage

From TEST_MANIFEST.md, the remaining gaps are:
- `tests/test_prompt_compiler.py` — templates.py enums/models, rule_resolver.py, compiler.py, import_fixer.py
- `tests/test_cli_commands.py` — CLI command behavior
