# Phase 2: Graph-Augmented Generation (GAG) — Complete

## Summary

Implemented the second half of the `gather_context` node: **Dependency Signature Injection** via the NetworkX `OrkaGraphDB`. The LLM now receives exact signatures, import paths, and docstrings of the target function's in-scope dependencies, eliminating hallucination of internal API arguments.

## Architecture Change

Extracted all graph-traversal functions from `compiler_node.py` into a new shared module `orka/operations/graph_helpers.py` with a **lazy singleton** `get_graph_db()` to avoid loading the large `.orka_cache.graph.json` file twice per pipeline.

### Files Created

- **`orka/operations/graph_helpers.py`** — Shared graph DB module:
  - `get_graph_db()` / `clear_graph_db_cache()` — lazy singleton
  - `find_target_node()` — locate a method/function node in the graph
  - `module_from_node_id()` — extract dotted module from node ID
  - `resolve_target_module()` — dotted module for the target
  - `resolve_one_dependency()` — resolve a single callee name
  - `build_dependency_map()` — all callable nodes in scope
  - `build_caller_constraints()` — nodes that call the target
  - `render_dependency_map_table()` / `render_caller_constraints_table()` — markdown tables
  - `extract_dependency_signatures()` — **NEW**: formats deps as DEPENDENCY/TYPE/IMPORT/DOCSTRING blocks

### Files Modified

- **`orka/operations/state.py`** — Added `dependency_signatures: str` field to `SurgeryState`
- **`orka/operations/graph.py`** — Added `"dependency_signatures": ""` to initial state
- **`orka/operations/controllers/context.py`** — Removed duplicate `_find_target_graph_node` and `_extract_dependency_signatures`. Now imports from `graph_helpers` and uses `get_graph_db()` singleton. Populates `dependency_signatures` in step 7.
- **`orka/operations/controllers/compiler_node.py`** — Removed 7 graph helper functions (407 lines). Uses imports from `graph_helpers` and `get_graph_db()`. Reads `dependency_signatures` from state and passes it to template `context_data`.
- **`orka/prompts/templates/refactor.yaml`** — Added `%%dependency_signatures%%` block after class context
- **`orka/prompts/templates/test.yaml`** — Added `%%dependency_signatures%%` block after class context

### Validation

| Component | Status |
|-----------|--------|
| `graph_helpers` imports | ✅ |
| `get_graph_db()` singleton | ✅ Same instance returned |
| `find_target_node` | ✅ `context.execute` → `Function:orka.operations.controllers.context.execute` |
| `module_from_node_id` | ✅ Correct dotted paths |
| `resolve_target_module` | ✅ Both graph and heuristic strategies |
| `build_dependency_map` | ✅ 12 deps for `context.execute` |
| `extract_dependency_signatures` | ✅ 12 sections, 59 lines, all formatted |
| Template rendering (refactor) | ✅ 2300 chars, section inline |
| Template rendering (test) | ✅ 2909 chars, section inline |
| `orka --help` boots | ✅ |
| `orka prompt` renders | ✅ No KeyError |

### How It Works

When `gather_context` runs:

1. Extract source code (as before)
2. HyDE query → ChromaDB for similar examples (as before)
3. **NEW**: `get_graph_db()` → `extract_dependency_signatures()` → produces a block like:

```
DEPENDENCY: charge_card
TYPE: function
IMPORT: from payments import charge_card
DOCSTRING: Charges the Stripe API with a token
---
DEPENDENCY: db_execute
TYPE: function
IMPORT: from database import db_execute
DOCSTRING: Executes a SQL query with retry support
```

4. This block is stored in `state['dependency_signatures']`
5. `compiler_node` injects it into the template via `%%dependency_signatures%%`

### Next Steps

The `gather_context` node is now fully complete (HyDE RAG + GAG). The pipeline can proceed to **Code Generation** and **Pytest Execution** nodes.
