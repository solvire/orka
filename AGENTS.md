# AGENTS.md - Instructions for AI Agents

## Project Overview
Orka is an AI-powered Python code surgery toolkit. It uses a LangGraph state machine to refactor methods and generate tests via LLMs.

## Key Architectural Constraints
When modifying this codebase, strictly adhere to the following rules:

1. **Bounded State**: The surgery pipeline uses `SurgeryState` (a TypedDict). It has strictly bounded fields. Do NOT add an unbounded `messages` list or arbitrary dynamic keys.
2. **Prompt Compiler Delimiters**: The prompt compiler uses `%%variable%%` delimiters, NOT Jinja2 `{{ }}` for variable substitution. Jinja2 is only used for template control flow.
3. **Four-Gate Validation**: All LLM output must pass through the 4-gate validation pipeline (Snippet AST -> Assembly -> File AST -> Pytest). Do not bypass `validate_draft`.
4. **LibCST over AST**: Code modifications must use LibCST (`orka/surgery/modifier.py`) to preserve formatting and syntax. Do not use raw `ast` for patching.
5. **Deterministic Routing**: The surgery graph is a deterministic state machine, not a ReAct agent. Routing logic is handled by `_router` in `orka/operations/graph.py`.

## CLI Usage
- `orka scan`: Build dependency graph + vector DB.
- `orka refactor --file <path> --method <name> --req <requirements>`: Refactor a method.
- `orka testgen --file <path> --method <name>`: Generate tests.
- `orka doctor`: Check configuration health.

## Running Tests
Run `pytest` from the project root.
