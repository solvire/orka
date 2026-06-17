
# Context Engineering for Code Injection

> Feature design document for hardening Orka's prompt context pipeline.
> Machines write instructions for future machines. The generation-time LLM
> should never need to *guess* what objects to construct — it should receive
> deterministic, grounded construction hints pre-computed at scan time.

## The Core Insight

This is about **machines writing instructions for future machines**. The current system embeds raw source code and hopes the generation-time LLM can figure out how to construct objects. That's backwards. We should:

1. **Pre-warm at scan time** — cheaper models write structured instruction blocks during idle `orka scan`
2. **Vectorize those instructions** — not just source, but docstrings, protocol fingerprints, and construction recipes
3. **Retrieve at compile time** — deterministic lookup, no generation-time hallucination

## Problem Origin (Phase 1 Failure)

The initial `data_construction_guide` experiment (see checkpoint
`docs/checkpoints/20250216_data_construction_guide_phase1.md`) failed because:

1. **`_collect_parameter_types`** reads type annotations from function
   signatures via LibCST, then searches the Graph DB for matching class
   nodes. But Python annotations are unreliable — `Optional[object]`
   and `list[str]` resolve to nothing useful in the graph.

2. **`generate_data_construction_guide`** sends the function code (with
   useless type annotations) to the fast LLM and asks it to explain how
   to construct valid inputs. Without seeing actual class definitions,
   the LLM hallucinated:

   > `graph_db` must be an object compatible with the internal helper
   > `_lookup_in_graph(graph_db, name)`, which typically expects a graph
   > database client (e.g., a connection or query interface from libraries
   > like `neo4j.Driver`, `py2proto.Graph`...)

   The real type is `OrkaGraphDB` from our own codebase — not a neo4j
   driver at all.

3. **The Graph DB stores too little metadata.** `NodeMetadata` has a
   `docstring` field but `CodeASTVisitor` never populates it. The
   `extract_dependency_signatures` function in `graph_helpers.py` calls
   `attrs.get("docstring")` — which always returns empty. GAG is
   effectively docstring-blind.

4. **ChromaDB only embeds raw source.** The single `orka_semantic_graph`
   collection has no way to search "how do I construct X?" or "what class
   has `.graph.nodes()`?" — it can only find similar code.

**Core insight**: Python's type annotations are not reliable enough to
determine what objects a function actually needs. The real clue is in
the function body (e.g. `graph_db.graph.nodes(data=True)`) but our
analysis only looks at signatures, not usage patterns.

## Key Files

| File | Role | What needs to change |
|------|------|---------------------|
| `orka/core/ingester.py` | AST visitor + Graph DB builder | Enrich `NodeMetadata` with docstrings, methods, bases, constructor_args |
| `orka/core/vector_store.py` | ChromaDB wrapper | Add `instruction_collection` and `protocol_collection`; new upsert/search methods |
| `orka/operations/controllers/context.py` | Node 1: gather_context | Replace LLM-based `generate_data_construction_guide` with deterministic `build_data_construction_guide` |
| `orka/operations/controllers/compiler_node.py` | Node 2: compile_prompt | Wire `data_construction_guide` into `context_data` (already done, keep as-is) |
| `orka/operations/graph_helpers.py` | Graph DB helpers | `extract_dependency_signatures` will automatically benefit from docstring fix |
| `orka/operations/state.py` | SurgeryState TypedDict | Keep `data_construction_guide` field (no rename) |
| `orka/prompts/templates/test.yaml` | Test generation template | Keep `%%data_construction_guide%%`; change content to structured table |
| `orka/prompts/templates/prewarm_class.yaml` | **NEW** — scan-time pre-warming template | Constrained fill-in-the-blanks template for fast LLM |
| `orka/cli.py` | CLI entry point | Add `--prewarm` flag to `orka scan` |

### Broken code to remove

- `context.py::_collect_parameter_types()` — replaced by duck-type protocol tracer
- `context.py::_read_class_source()` — logic moves to `graph_helpers.py`
- `context.py::generate_data_construction_guide()` — replaced by `build_data_construction_guide()`
- `context.py` top-level import of `OrkaLangChainClient` — no longer needed in this module

## Current vs. Target State

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
2. **Update** `test.yaml` — change `%%data_construction_guide%%` section content to structured table format (keep the placeholder name)
3. **Add** `orka/prompts/templates/prewarm_class.yaml` — the constrained template for scan-time pre-warming
4. **Update** `orka scan` CLI — add `--prewarm` flag (default True) to enable/disable the LLM pre-warming step

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
| Breaking change to `data_construction_guide` field | No rename — keep `data_construction_guide` throughout; only change content quality |

---

## Implementation Order

```
Chunk 1 (docstrings)  →  Chunk 2 (method lists)  →  Chunk 3 (vector store)  →  Chunk 4 (protocol tracer)  →  Chunk 5 (prewarm template)  →  Chunk 6 (wiring)
```

Chunks 1-2 are independent of 3-4. Chunk 5 depends on chunks 2+3. Chunk 6 ties everything together.

**Recommendation**: Start with Chunk 1 (highest leverage, lowest risk — docstrings start flowing immediately). Then Chunk 4 (protocol tracer works even without the new collections). Chunks 2-3 can be done in parallel.

---

## Roadmap: Implementation Chunks

Each chunk is completable in a single session with all tests passing at the end.

### Chunk 1: Populate docstrings in Graph DB nodes

**Goal**: Make `NodeMetadata.docstring` actually contain data after `orka scan`.

**Files**: `orka/core/ingester.py`

**Changes**:
- In `CodeASTVisitor.visit_ClassDef`: add `docstring = ast.get_docstring(node) or ""` to `class_info` dict
- In `CodeASTVisitor.visit_FunctionDef`: add `docstring = ast.get_docstring(node) or ""` to `func_info` dict
- In `_parse_with_ast`: pass docstring into `NodeMetadata` for class, method, and function nodes (currently always `docstring=None`)
- Also store `bases` on class nodes (already extracted in visitor, just not written to `NodeMetadata`)

**Acceptance criteria**:
- [ ] `orka scan` on the orka codebase produces graph nodes where `docstring` is populated (not empty) for classes/methods with docstrings
- [ ] `extract_dependency_signatures` in `graph_helpers.py` returns real docstrings in its output (currently always empty)
- [ ] Existing tests pass
- [ ] Add 2-3 new tests in `test_orka_dual_brain.py` verifying docstring presence on scanned nodes

### Chunk 2: Enrich class nodes with method lists and constructor args

**Goal**: Graph DB class nodes carry `methods` list and `constructor_args`.

**Files**: `orka/core/ingester.py`

**Changes**:
- Extend `NodeMetadata` dataclass with `methods: Optional[list[str]]` and `constructor_args: Optional[str]` fields
- In `visit_ClassDef`: collect method names (already in `cls["methods"]`), format as `"method_name(arg1, arg2)"` strings, store on class node
- In `visit_ClassDef`: find `__init__` method among `cls["methods"]`, extract its parameter names + defaults, format as `"cache_file='.orka_cache.json'"`, store on class node

**Acceptance criteria**:
- [ ] After `orka scan`, class nodes have `methods` attribute containing `["scan_directory(root_dir)", "_save_cache()", ...]`
- [ ] Class nodes have `constructor_args` like `"(cache_file='.orka_cache.json')"`
- [ ] Existing tests pass
- [ ] Add tests verifying method list and constructor extraction on a sample class

### Chunk 3: Multi-collection vector store

**Goal**: Three ChromaDB collections replacing the current single collection.

**Files**: `orka/core/vector_store.py`

**Changes**:
- Rename `self.collection` → `self.source_collection` (collection name `orka_semantic_graph` unchanged — backward compat)
- Add `self.instruction_collection` (`orka_instruction_index`)
- Add `self.protocol_collection` (`orka_protocol_index`)
- Add methods: `upsert_instruction`, `upsert_protocol`, `search_instructions`, `search_protocols`
- Update `delete_file_nodes` to clean all three collections
- Update `upsert_node` to use `self.source_collection`

**Acceptance criteria**:
- [ ] `OrkaVectorDB.__init__` creates/opens all three collections
- [ ] `upsert_instruction` embeds docstring + instruction block with metadata `{file_path, node_type, class_name, has_constructor, is_singleton}`
- [ ] `upsert_protocol` embeds protocol fingerprint text with metadata `{file_path, node_type, class_name, method_count, attribute_count}`
- [ ] `search_instructions("how to construct a graph database client")` returns relevant class instruction blocks
- [ ] `search_protocols("graph.nodes(data=True)")` returns classes providing that method
- [ ] `delete_file_nodes` removes from all three collections
- [ ] Existing `source_collection` tests continue to pass
- [ ] Add 5-8 new tests for instruction and protocol collections

### Chunk 4: Duck-type protocol tracer

**Goal**: At compile time, deterministically trace what each parameter is used for in the function body — no LLM in the hot path.

**Files**: `orka/operations/controllers/context.py`

**Changes**:
- Add `ParameterProtocol` dataclass: `{attributes: set[str], method_calls: dict[str, int], subscripted: bool, iterated: bool}`
- Add `_trace_parameter_protocols(existing_code) -> dict[str, ParameterProtocol]`: LibCST visitor walks the function body, for each parameter name records attribute accesses, method calls, subscripts, iteration. Skips `self`.
- Add `_resolve_protocols_to_classes(protocols, graph_db) -> dict[str, ClassMatch]`: For each protocol, search Graph DB class nodes by method overlap. Return `(class_name, module, confidence, constructor_args)`.
- Add `build_data_construction_guide(existing_code, graph_db) -> str`: Calls tracer → resolver → formats the markdown table shown in the "After" example. Falls back to empty string if no matches.
- **Remove** `_collect_parameter_types`, `_read_class_source`, `generate_data_construction_guide`
- Remove `OrkaLangChainClient` import from `context.py` (no longer needed here)

**Acceptance criteria**:
- [ ] `_trace_parameter_protocols` correctly identifies that `graph_db` is used with `.graph.nodes(data=True)` (attributes: `{"graph"}`, method_calls: `{"nodes": 1}`)
- [ ] `_resolve_protocols_to_classes` matches that protocol to `OrkaGraphDB` (which has `.graph` attribute and `nodes` in its method list)
- [ ] `build_data_construction_guide` returns a structured markdown table with grounded type info — no hallucinated external libraries
- [ ] For functions with only builtin-typed params (e.g. `x: int, name: str`), the guide correctly reports "builtin type, construct directly"
- [ ] Existing tests pass
- [ ] Add 10-15 new tests for tracer + resolver

### Chunk 5: Pre-warming prompt template

**Goal**: Create the constrained `prewarm_class.yaml` template and wire it into the scan pipeline.

**Files**: `orka/prompts/templates/prewarm_class.yaml`, `orka/core/ingester.py`, `orka/cli.py`

**Changes**:
- Create `prewarm_class.yaml` with the constrained fill-in-the-blanks template (see Phase 3 above)
- In `ingester.py`: add `_prewarm_instructions` step after AST walk that iterates class nodes, calls fast LLM with the template, embeds results into `instruction_collection` and `protocol_collection`
- In `cli.py`: add `--prewarm` flag to `orka scan` command (default True)
- Also generate deterministic protocol fingerprint text from method list (no LLM needed for this part)

**Acceptance criteria**:
- [ ] `orka scan --prewarm` generates instruction blocks for each class via fast LLM
- [ ] `orka scan` (default, prewarm=True) stores instruction blocks in `orka_instruction_index` collection
- [ ] `orka scan --no-prewarm` skips the LLM step (still does AST enrichment from Chunks 1-2)
- [ ] Protocol fingerprint text is generated deterministically (no LLM) and stored in `orka_protocol_index`
- [ ] Construction recipes are written to `.orka_cache.recipes.json`
- [ ] Existing tests pass
- [ ] Add 5-8 new tests for pre-warming pipeline

### Chunk 6: Wiring & end-to-end validation

**Goal**: Connect the deterministic `build_data_construction_guide` to the prompt template and verify the full pipeline produces grounded output.

**Files**: `orka/operations/controllers/context.py`, `orka/prompts/templates/test.yaml`

**Changes**:
- In `context.py` `execute()`: replace the call to `generate_data_construction_guide` with `build_data_construction_guide`
- In `test.yaml`: update the `%%data_construction_guide%%` section header to explain the structured table format
- Run full `orka testgen` against a real method (e.g. `_resolve_undefined` in `import_fixer.py`) and verify the output no longer contains hallucinated external libraries
- Update `tests/TEST_MANIFEST.md` with new test files

**Acceptance criteria**:
- [ ] `orka testgen --file orka/core/import_fixer.py --func _resolve_undefined` produces a prompt where `%%data_construction_guide%%` contains `OrkaGraphDB` (not `neo4j.Driver`)
- [ ] `orka testgen` for a simple method (all builtin params) produces "builtin type, construct directly" guidance
- [ ] Full pipeline test: `orka testgen` produces valid tests that pass 4-gate validation
- [ ] All existing tests pass
- [ ] `tests/TEST_MANIFEST.md` updated