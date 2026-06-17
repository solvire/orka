
## The Core Insight

You're absolutely right — this is about **machines writing instructions for future machines**. The current system embeds raw source code and hopes the generation-time LLM can figure out how to construct objects. That's backwards. We should:

1. **Pre-warm at scan time** — cheaper models write structured instruction blocks during idle `orka scan`
2. **Vectorize those instructions** — not just source, but docstrings, protocol fingerprints, and construction recipes
3. **Retrieve at compile time** — deterministic lookup, no generation-time hallucination

Let me lay out exactly what the current system stores vs. what it should store:

### Current State (What We Have)

| Store | Contents | Missing |
|-------|----------|---------|
| **Graph DB** nodes | `name`, `node_type`, `file_path`, `lineno`, `returns`, `docstring` (always empty!) | Docstrings, method lists, constructor signatures, bases, protocol fingerprints |
| **Graph DB** edges | `CONTAINS`, `IMPORTS`, `INHERITS` | `CALLS` (callee edges), `CONSTRUCTS` |
| **ChromaDB** `orka_semantic_graph` | Raw source code → single embedding | Docstring embeddings, instruction embeddings, protocol fingerprint embeddings |

### Target State (What We Need)

| Store | Contents | Purpose |
|-------|----------|---------|
| **Graph DB** node attrs | + `docstring`, `methods` list, `constructor_args`, `bases`, `is_singleton`, `protocol_fingerprint` | Compile-time deterministic lookup |
| **ChromaDB** `orka_semantic_graph` | Raw source code (unchanged) | Similar-example retrieval |
| **ChromaDB** `orka_instruction_index` | Pre-warmed instruction blocks + docstrings | "How do I construct X?" semantic search |
| **ChromaDB** `orka_protocol_index` | Protocol fingerprints as text | "What class has `.graph.nodes()`?" semantic search |
| **File** `.orka_cache.recipes.json` | Construction recipes per class | Deterministic O(1) lookup for known types |

---

## Architecture: The Three-Layer Context Engine

```
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 1: SCAN TIME (idle, cheap)             │
│                                                                 │
│  orka scan                                                      │
│  ├── AST walk → richer NodeMetadata (docstrings, methods, etc) │
│  ├── Fast LLM → construction recipes per class                 │
│  ├── Fast LLM → instruction blocks per method                  │
│  ├── Embed docstrings → orka_instruction_index                  │
│  ├── Embed protocol fingerprints → orka_protocol_index          │
│  └── Write .orka_cache.recipes.json                             │
│                                                                 │
│  Cost: ~$0.002/class (fast model)   Latency: Batch, async       │
└─────────────────────────────────────────────────────────────────┘
                              ↓ pre-computed
┌─────────────────────────────────────────────────────────────────┐
│              LAYER 2: COMPILE TIME (deterministic, no LLM)      │
│                                                                 │
│  compile_prompt node                                             │
│  ├── LibCST duck-type trace → ParameterProtocol per param       │
│  │   e.g. {attributes: ["graph"], methods: ["nodes"]}          │
│  ├── Protocol → Graph DB class lookup (method list match)       │
│  ├── Class → recipes.json lookup → construction hint            │
│  ├── Class → instruction_index semantic search (fallback)       │
│  └── Inject into %%data_construction_guide%% as structured block│
│                                                                 │
│  Cost: $0   Latency: <50ms                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓ enriched prompt
┌─────────────────────────────────────────────────────────────────┐
│                LAYER 3: GENERATION TIME (smart LLM)             │
│                                                                 │
│  generate_draft node                                             │
│  └── Receives prompt with:                                      │
│      - TARGET IMPORT (exact)                                    │
│      - DEPENDENCY MAP (exact paths)                              │
│      - CALLER CONSTRAINTS (don't break these)                   │
│      - DEPENDENCY SIGNATURES (now with real docstrings!)        │
│      - DATA CONSTRUCTION GUIDE (structured, deterministic)      │
│      └── "graph_db: Use orka.operations.graph_helpers.get_graph_db() │
│           — returns the lazy OrkaGraphDB singleton. Provides    │
│           .graph (DiGraph), .graph.nodes(data=True), ..."       │
│                                                                 │
│  No hallucination — all context is grounded in the codebase.    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Detailed Plan

### Phase 1: Fix the Broken Foundation

**Goal**: Make `docstring` actually populate in graph nodes. This immediately fixes GAG (dependency signatures currently get zero docstrings).

**Changes**:

1. **`orka/core/ingester.py`** — In `CodeASTVisitor`, extract docstrings from class and function nodes via `ast.get_docstring()` and store them in the visitor's `classes` and `functions` dicts. Then in `_parse_with_ast`, pass the docstring into `NodeMetadata`.

2. **`orka/core/ingester.py`** — Add `methods` list to class node metadata. The visitor already collects methods per class — we just need to write them to the graph node as `methods: [(name, signature_str), ...]`.

3. **`orka/core/ingester.py`** — Add `bases` to class node metadata (already extracted, just not stored on the node).

4. **`orka/core/ingester.py`** — Add `constructor_args` to class node metadata: extract `__init__` parameter names + defaults.

After this: `orka scan` → every node has a real docstring, method list, constructor info, and bases. GAG immediately produces better output with zero code changes to the pipeline.

**Test impact**: Update `test_orka_dual_brain.py` — verify docstrings are populated after scan.

### Phase 2: Multi-Collection Vector Store

**Goal**: Three ChromaDB collections, each serving a different retrieval purpose.

**Changes**:

1. **`orka/core/vector_store.py`** — Refactor from single `self.collection` to three:
   - `self.source_collection` — raw source code (current behavior, renamed)
   - `self.instruction_collection` — docstrings + pre-warmed instruction blocks
   - `self.protocol_collection` — protocol fingerprints as searchable text

2. **`orka/core/vector_store.py`** — Add new methods:
   - `upsert_instruction(node_id, docstring, instruction_block, file_path, node_type)` — embeds the docstring+instruction into `orka_instruction_index`
   - `upsert_protocol(node_id, protocol_text, file_path, node_type)` — embeds the protocol fingerprint into `orka_protocol_index`
   - `search_instructions(query, n_results, node_type)` — semantic search over instruction blocks
   - `search_protocols(query, n_results, node_type)` — semantic search over protocol fingerprints ("what class has `.graph.nodes()`?")

3. **`orka/core/vector_store.py`** — Update `delete_file_nodes` to clean all three collections.

The metadata schema for each collection:

```python
# orka_instruction_index metadata
{
    "file_path": "orka/core/ingester.py",
    "node_type": "class",
    "class_name": "OrkaGraphDB",
    "has_constructor": True,
    "is_singleton": False,
}

# orka_protocol_index metadata
{
    "file_path": "orka/core/ingester.py",
    "node_type": "class",
    "class_name": "OrkaGraphDB",
    "method_count": 8,
    "attribute_count": 2,
}
```

### Phase 3: Scan-Time Pre-Warming

**Goal**: Use the fast LLM during `orka scan` to generate structured instruction blocks — machines writing for machines.

**Changes**:

1. **`orka/core/ingester.py`** — After the AST walk populates all nodes, add a new `_prewarm_instructions` step that:
   - Iterates over all **class** nodes
   - For each class, reads its source, method list, constructor args, and bases
   - Sends a **structured prompt** to the fast LLM asking it to produce a **construction recipe** (not free text — a structured format):
     ```
     Class: OrkaGraphDB
     Module: orka.core.ingester
     Constructor: OrkaGraphDB(cache_file='.orka_cache.json')
     Singleton: orka.operations.graph_helpers.get_graph_db()
     Construction hint: Use get_graph_db() for the lazy singleton;
       construct directly only for testing with a temp cache path.
     Required attributes: .graph (DiGraph)
     Key methods: scan_directory(root_dir), _save_cache(), _process_file(...)
     ```

2. **`orka/core/ingester.py`** — Also generate a **protocol fingerprint text** for each class:
   ```
   Provides attributes: graph, cache_file, chroma_dir
   Provides methods: scan_directory(root_dir), _save_cache(),
     _process_file(abs_file_path, rel_file_path),
     _parse_with_ast(abs_file_path, module_name, file_node_id, rel_file_path)
   ```

3. **`orka/core/ingester.py`** — Embed the instruction block and protocol fingerprint into the new ChromaDB collections.

**Critical design choice**: The pre-warming prompt must be **highly constrained**. Not "write a guide" but "fill in this structured template". This prevents the hallucination problem from Phase 1 — the LLM fills blanks, not free text.

The pre-warming prompt template:

```yaml
# orka/prompts/templates/prewarm_class.yaml
system: |
  You are a Python code analyst. Fill in the structured template.
  Use ONLY information from the source code provided. Do NOT guess
  or reference external libraries not shown in the source.

user: |
  ### CLASS SOURCE:
  ```python
  %%class_source%%
  ```

  ### KNOWN METHODS:
  %%method_list%%

  ### CONSTRUCTOR ARGS:
  %%constructor_args%%

  ### BASE CLASSES:
  %%bases%%

  Fill in EXACTLY this format — no additional text:

  CONSTRUCTOR: <exact constructor call with default values>
  SINGLETON: <module.factory_function()> or NONE
  CONSTRUCTION_HINT: <one sentence, grounded in source>
  REQUIRED_ATTRIBUTES: <comma-separated list of public attributes>
  KEY_METHODS: <comma-separated list of method signatures>
  PROTOCOL: <"Provides attributes: X. Provides methods: Y.">
```

### Phase 4: Duck-Type Protocol Tracer

**Goal**: At compile time, trace what each parameter is *used for* in the function body, then find the matching class deterministically.

**Changes**:

1. **`orka/operations/controllers/context.py`** — Add `_trace_parameter_protocols(existing_code) -> dict[str, ParameterProtocol]`:
   - LibCST visitor walks the function body
   - For each parameter, records: attributes accessed, methods called, subscripts, iteration
   - Skips `self` (it's the class under test)
   - Returns structured protocol per parameter

2. **`orka/operations/controllers/context.py`** — Add `_resolve_protocols_to_classes(protocols, graph_db) -> dict[str, ClassMatch]`:
   - For each parameter protocol, search Graph DB class nodes
   - Match by method overlap — a class that has 4/5 methods from the protocol is a strong match
   - Return `(class_name, module, confidence, construction_recipe)`

3. **`orka/operations/controllers/context.py`** — Replace `generate_data_construction_guide` with `build_data_construction_guide`:
   - Calls protocol tracer → resolver → formats structured block
   - If Graph DB is unavailable, falls back to ChromaDB `search_instructions` (semantic search over pre-warmed instruction blocks)
   - If both fail, falls back to ChromaDB `search_protocols` (semantic search over protocol fingerprints)
   - **Never calls an LLM in the hot path**

### Phase 5: Wiring & Cleanup

1. **Remove** `generate_data_construction_guide` and `_collect_parameter_types` from `context.py` — replaced by deterministic pipeline
2. **Update** `test.yaml` — rename `%%data_construction_guide%%` section to `%%parameter_construction_hints%%` with structured format
3. **Update** `state.py` — rename `data_construction_guide` to `parameter_construction_hints`
4. **Add** `orka/prompts/templates/prewarm_class.yaml` — the constrained template for scan-time pre-warming
5. **Update** `orka scan` CLI — add `--prewarm` flag (default True) to enable/disable the LLM pre-warming step
6. **Write** `docs/features/data_construction_guide.md` — the feature design document

---

## What the LLM Sees: Before vs. After

### Before (current — hallucinated)

```
### FUNCTION USAGE GUIDE:
- **graph_db**: An Optional — can be None to skip graph DB lookups.
  When not None, it must be an object compatible with the internal
  helper _lookup_in_graph(graph_db, name), which typically expects
  a graph database client (e.g., a connection or query interface
  from libraries like neo4j.Driver, py2neo.Graph, or a custom class)...
```

### After (deterministic + pre-warmed)

```
### PARAMETER CONSTRUCTION HINTS:
| Parameter | Expected Type | Construction | Notes |
|-----------|--------------|--------------|-------|
| `names` | `list[str]` | `["os", "sys"]` | Simple string list |
| `graph_db` | `OrkaGraphDB` | `orka.operations.graph_helpers.get_graph_db()` | Lazy singleton; provides .graph (DiGraph), .graph.nodes(data=True); for tests, construct with temp path: `OrkaGraphDB(cache_file=tmp_path / ".orka_cache.json")` |
```

The second version is:
- **Grounded** — every word comes from the codebase, not the LLM's training data
- **Actionable** — the testgen LLM knows exactly what import to use and how to construct the object
- **Fast** — zero LLM calls in the hot path at generation time

---

## Risk Analysis

| Risk | Mitigation |
|------|-----------|
| Pre-warming costs money at scan time | `--prewarm` flag to disable; fast model is ~$0.002/class; only runs on changed files |
| Protocol tracer misses chained calls (e.g., `a.b.c()`) | Two-level tracing: record `b` as attribute on `a`, then `c` as method on the return type of `a.b` |
| Structured LLM output in pre-warming is still LLM output | Template is highly constrained (fill-in-the-blanks, not free text); validation step checks for hallucinated module names |
| ChromaDB three collections = 3x storage | Protocol and instruction embeddings are tiny (~500 chars each vs. multi-KB source); total increase ~30% |
| Breaking change to `data_construction_guide` field | Rename is optional; can keep the old field name for backward compatibility |

---

## Implementation Order

```
Phase 1 (fix foundation)  →  Phase 2 (multi-collection)  →  Phase 3 (pre-warming)  →  Phase 4 (protocol tracer)  →  Phase 5 (wiring)
    2-3 hours                    2-3 hours                      3-4 hours                  3-4 hours                      1-2 hours
```

Phases 1 and 2 are independent of each other. Phase 3 depends on Phase 2. Phase 4 is independent of 2 and 3. Phase 5 ties everything together.

**Recommendation**: Start with Phase 1 (it's the highest-leverage, lowest-risk change — docstrings start flowing immediately). Then proceed to Phase 4 (the protocol tracer), since it works even without the new collections. Phases 2-3 can be done in parallel.

Would you like to switch to Agent mode to start implementing Phase 1?