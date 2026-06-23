# AGENTS.md - Instructions for AI Agents

## Project Overview
Orka is an AI-powered Python code surgery toolkit. It uses a LangGraph state machine to refactor methods and generate tests via LLMs.

## Key Architectural Constraints
When modifying this codebase, strictly adhere to the following rules:

1. **Bounded State**: The surgery pipeline uses `SurgeryState` (a TypedDict). It has strictly bounded fields. Do NOT add an unbounded `messages` list or arbitrary dynamic keys.
2. **Prompt Compiler Delimiters**: The prompt compiler uses `%%variable%%` delimiters, NOT Jinja2 `{{ }}` for variable substitution. Jinja2 is only used for template control flow.
3. **Four-Gate Validation**: All LLM output must pass through the 4-gate validation pipeline (Snippet AST -> Assembly -> File AST -> Pytest) via `core/validator.py:validate_four_gates()`. Do not bypass `validate_draft`.
4. **LibCST over AST**: Code modifications must use LibCST (`orka/surgery/modifier.py`) to preserve formatting and syntax. Do not use raw `ast` for patching.
5. **Deterministic Routing**: The surgery graph is a deterministic state machine, not a ReAct agent. Routing logic is handled by `_router` in `orka/operations/graph.py`.
6. **LibCST Flat Grammar**: There is no `AsyncFunctionDef` node in LibCST. Async methods are `FunctionDef` nodes where `node.asynchronous is not None`. Never define `visit_AsyncFunctionDef`.

## Module Structure (v0.2.0)

### Three-Way Import Split
- `core/module_resolver.py` — Pure module path resolution (stdlib only, zero orka deps)
- `core/dependency_resolver.py` — Graph-based symbol resolution (depends on module_resolver)
- `core/import_injector.py` — All import CST mutation (depends on dependency_resolver + LibCST). Functions are idempotent: source in → source out.

### CST Finding & Whitespace
- `core/locator.py` — `find_method`, `find_class`, `get_signature`, `extract_docstring`, `extract_docstring_regex`
- `surgery/trivia.py` — `preserve_docstring`, `normalize_spacing`, `collapse_blank_lines` (pure functions)

### Deleted (do not import)
- `core/import_fixer.py` — replaced by `import_injector.py`
- `core/cascade.py` — replaced by `import_injector.cascade_import_updates`

### Validation
- `core/validator.py:validate_four_gates()` — unified 4-gate entry point. The controller (`operations/controllers/validator.py`) is a thin wrapper.

## CLI Usage
- `orka scan`: Build dependency graph + vector DB.
- `orka refactor --file <path> --method <name> --req <requirements>`: Refactor a method.
- `orka testgen --file <path> --method <name>`: Generate tests.
- `orka doctor`: Check configuration health.

## Running Tests
Run `env/bin/python -m pytest orka/tests/` from the project root.
Baseline: 419 passed, 1 pre-existing failure (`test_compile_real_test_template`).
