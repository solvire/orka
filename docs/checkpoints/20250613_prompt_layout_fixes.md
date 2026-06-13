# Orka: Prompt Layout Fixes — Completion Trap & Context Redundancy
**Date:** June 13, 2025
**Phase:** Surgery Graph Pipeline — Prompt Compilation Hardening
**Status:** Complete

## 1. Context: Why This Session Happened

The surgery graph pipeline (`gather_context -> compile_prompt -> generate_draft -> validate_draft -> fix_draft`) was operational, but the compiled prompts had two structural bugs that degraded LLM output quality:

**The Completion Trap:** `compiler_node.py` was appending `similar_examples` (ChromaDB code snippets) **after** the template's final output instruction (`### SYNTHESIZED BODY LOGIC (RAW PYTHON ONLY):`). Because LLMs are autoregressive token predictors, the last tokens they read anchor their generation. When the prompt ended with Python code examples, the LLM would either start generating example-like code or wrap output in markdown fences — ignoring the constraints above.

**Context Redundancy:** The templates unconditionally rendered both a basic `%%dependency_map%%` table (import paths) AND rich `%%dependency_signatures%%` (full signatures + docstrings from Graph DB). When both were present, this wasted context window budget and diluted the LLM's attention on the same information expressed twice.

The Generation Node (`orka/operations/controllers/generator.py`) was already implemented with `fix_md_fences()` sanitization and `original_draft_code` preservation — confirmed working, no changes needed.

## 2. Architecture Decisions

### Decision 1: Inline similar_examples via template placeholder
- **Decision:** Add `%%similar_examples%%` to both `refactor.yaml` and `test.yaml` templates, placed ABOVE the final output instruction. Pass formatted examples through `context_data` instead of post-appending.
- **Rationale:** The `%%var%%` template engine has no conditionals or loops — it's pure string substitution. The only way to control ordering is to place the placeholder in the correct position within the template YAML. Placing it between `%%quality_gates%%` and the `ADDITIONAL CONSTRAINTS` block ensures the LLM reads examples as context, then reads the action trigger last.
- **Alternatives rejected:** Moving to Jinja2 (rejected — the `%%var%%` engine was deliberately chosen for zero-collision with Python/YAML/JSON syntax; Jinja2 `{{ }}` collides with dict literals and f-strings).

### Decision 2: Conditional dependency_map suppression in Python
- **Decision:** In `compiler_node.py`, check `has_rich_deps = bool(dependency_signatures.strip())`. If true, set `effective_dep_map = ""`. Pass the effective value into `context_data`.
- **Rationale:** The `%%var%%` engine cannot do conditionals, so the logic must live in Python before rendering. When rich signatures are available (the common case after `orka scan`), the basic table is redundant. When they're absent (no graph DB, fresh project), the table still provides value.
- **Tradeoffs accepted:** The template still renders the `### DEPENDENCY MAP` heading even when empty. This is a minor cosmetic issue — a blank section heading is far less harmful than duplicate information consuming tokens.

## 3. Files Changed

### Modified
| File | Key Changes |
|---|---|
| `orka/prompts/templates/refactor.yaml` | Added `%%similar_examples%%` placeholder between `%%quality_gates%%` and `ADDITIONAL CONSTRAINTS` — above the final `### SYNTHESIZED BODY LOGIC` trigger |
| `orka/prompts/templates/test.yaml` | Added `%%similar_examples%%` placeholder between `%%style_guide%%` and `ADDITIONAL CONSTRAINTS` — above the final `### PYTHON TEST FUNCTIONS` trigger |
| `orka/operations/controllers/compiler_node.py` | (1) Removed post-append block that tacked similar_examples onto compiled_prompt after compilation. (2) Added `similar_examples_text` formatting and passed into `context_data` for inline rendering. (3) Added `has_rich_deps` conditional to suppress `dependency_map` when `dependency_signatures` are present. (4) Updated docstring. |

## 4. Active Design Patterns

- **Prompt ordering principle:** Context data (examples, dependency maps, constraints) must ALWAYS precede the final action trigger. The last tokens the LLM reads should be the instruction, not example code. This is enforced by template layout, not by runtime logic.
- **Conditional rendering via pre-processing:** Since the `%%var%%` engine is substitution-only, conditional logic (show X only if Y is absent) must be implemented in the Python node executor before passing values into `context_data`.
- **Dual-field draft preservation:** `original_draft_code` captures the LLM's very first output (post-sanitization) and is never overwritten. `draft_snippet` is the current working draft that the fixer can modify. This gives the fixer a baseline of the LLM's original intent.

## 5. Outstanding Work / Next Steps

- [ ] Build the **Fixer Node** (`orka/operations/controllers/fixer.py`) — takes the failed draft + validation error + original_draft_code, sends a repair prompt to the LLM
- [ ] Build the **Validator Node** (`orka/operations/controllers/validator.py`) — AST parse gate + pytest gate, populates `validation_output` and `is_valid`
- [ ] Add a `### DEPENDENCY MAP` heading conditional — suppress the heading entirely when `effective_dep_map` is empty (requires either a template-level hack or a new placeholder like `%%dependency_map_section%%`)
- [ ] Write integration tests that assert `similar_examples` appears before `SYNTHESIZED BODY LOGIC` in the compiled prompt string
- [ ] Update `docs/FLOW_CONTROL.md` with the full surgery graph Mermaid diagram (file doesn't exist yet)
