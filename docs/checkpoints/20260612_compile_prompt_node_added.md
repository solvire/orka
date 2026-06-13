# Session Checkpoint: Compile-Prompt Node Added to Surgery Graph

## Summary

Introduced a new `compile_prompt` node between `gather_context` and `generate_draft` in the LangGraph surgery pipeline. This node enriches raw context before sending to the LLM, and decouples prompt compilation from LLM invocation.

## Files Created

- **`orka/operations/controllers/compiler_node.py`** — New compile-prompt node with:
  - `_SignatureCollector` (LibCST visitor) — extracts params, return type, docblock, decorators, async status from existing code
  - `_analyse_signature()` — parses method/function and returns structured signature info
  - `_lookup_graph_neighbours()` — queries NetworkX graph for 1-level callers and callees
  - `execute()` — the node entry point: loads template, resolves rules, enriches context, compiles prompt

## Files Modified

- **`orka/operations/controllers/generator.py`** — Simplified to only invoke the LLM. Removed all template loading, rule resolution, and compiler logic. Now reads `state["compiled_prompt"]` (pre-compiled by `compile_prompt` node) and sends it to the LLM. No longer imports `PromptCompiler`, `resolve_rules`, or `load_template`.

- **`orka/operations/graph.py`** — Added `compiler_node` import, inserted `compile_prompt` node between `gather_context` and `generate_draft`. Updated docstring pipeline diagram. Added `compiled_prompt` and `compiled_prompt_sections` to initial state.

- **`orka/operations/state.py`** — Added two new state fields:
  - `compiled_prompt: str` — the fully compiled prompt string
  - `compiled_prompt_sections: dict` — structured breakdown for introspection

- **`orka/cli.py`** — `prompt` command now runs the `gather_context` and `compile_prompt` nodes directly instead of hardcoding placeholder context. Accepts `--req` flag. Shows signature info, graph neighbours, rules, and the full compiled prompt. No LLM is invoked.

## New Pipeline Flow

```
START -> gather_context -> compile_prompt -> generate_draft -> validate_draft
                                           |
                                    (new node — enriches
                                     context, compiles
                                     template, no LLM call)
```

## Key Design Decisions

- **No LLM in compile_prompt** — The node is pure Python. Signature analysis uses LibCST, graph lookup uses NetworkX. A small LLM refinement step can be added later without changing the node interface.

- **Generator is now dumber** — It receives a ready-made prompt and only invokes the LLM. This makes it testable: you can pass any string as `compiled_prompt` and verify the LLM is called correctly.

- **prompt command reuses nodes** — It manually calls `gather_context` then `compile_prompt` with a minimal state dict. This avoids graph overhead while reusing the same extraction and enrichment logic that `refactor`/`testgen` use.

- **Both output formats** — `compile_prompt` returns `compiled_prompt` (flat string) and `compiled_prompt_sections` (structured dict with signature, rules, graph info).

- **No token budgeting** — Budget enforcement removed per discussion. All rules are included.

## Next Goals

1. **Beef up templates** — The YAML templates (`refactor.yaml`, `test.yaml`) are still thin. Add better instructions for zero-collateral-damage refactoring.

2. **Small LLM refinement in compile_prompt** — Add a fast/cheap LLM call that reviews the compiled prompt and suggests missing context. Could feed back into `gather_context` loop.

3. **Migrate refactor_method() to surgery graph** — Legacy `orchestrator.py` still uses `build_synthesis_prompt()`. Should use `run_surgery("refactor", ...)` instead.

4. **Tests for compile_prompt** — Test `_analyse_signature` with various method signatures (async, decorators, no docblock, etc.) and test the node end-to-end with mock state.
