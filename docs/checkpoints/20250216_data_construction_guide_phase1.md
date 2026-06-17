# Session Checkpoint: Data Construction Guide — Phase 1

## What was accomplished

### Test infrastructure
- Chunk 1: `_detect_undefined_names` — **15 tests written, all passing**
- Chunk 2: `_stdlib_fallback` — **13 tests written, all passing**
- Chunk 3: `_inject_imports` — **16 tests written, all passing**
- Chunk 4: `_module_from_node_id` — **10 tests written, all passing** (also fixed 2 bugs in production code:
  - Method nodes now strip both class and method name (split `parts[:-2]`)
  - Leading-dot edge case now returns `None` instead of empty string)
- Chunk 5: `_resolve_undefined` — **failed** (LLM generated mock classes instead of using real `OrkaGraphDB`)

### Prompt template improvements
- Added constraint #9 to `test.yaml`: banned `self` in test function signatures
- Added `%%data_construction_guide%%` placeholder to `test.yaml` (currently populated via fast LLM)

### Code changes (all in place, need review)
- `orka/prompts/templates/test.yaml` — added `%%data_construction_guide%%` section
- `orka/operations/state.py` — added `data_construction_guide` field
- `orka/operations/controllers/context.py` — added `generate_data_construction_guide`, `_collect_parameter_types`, `_read_class_source`; moved `OrkaLangChainClient` to top-level import
- `orka/operations/controllers/compiler_node.py` — wired `data_construction_guide` into `context_data`
- `orka/core/import_fixer.py` — fixed `_module_from_node_id` for Method nodes and leading-dot edge case

## Decision record

We started implementing a fast-LLM-powered "data construction guide" to help the
testgen LLM understand what objects to pass to functions. Initial approach:
1. Extract parameter type annotations via LibCST
2. Look up class definitions in Graph DB
3. Feed definitions + function code to fast LLM → guide

**Abandoned mid-session** because:
- Python is not strongly typed — many functions use `Optional[object]` or `Any`
- The fast LLM hallucinated (e.g., referencing `neo4j.Driver` when the codebase uses `OrkaGraphDB`)
- A single-shot prompt cannot replace understanding the codebase's data model
- Proper solution needs deeper architecture work

## Next steps (new session)

The next session should start fresh with a feature design document `docs/features/data_construction_guide.md`
covering:
1. How to use the Graph DB to find the *actual* types a function consumes
2. Whether we should generate structured docblocks (typed, schema-aware) rather than free-text guides
3. Whether the guide should be generated eagerly (at context-gather time) or lazily (on-demand by the smart LLM)

The experimental code in `context.py` had two issues:

1. **`_collect_parameter_types`** — Uses LibCST to find parameter type annotations, then searches the Graph DB for matching class nodes. For `_resolve_undefined`, the parameter types are `list[str]` and `Optional[object]` — neither of which resolves to a useful class in the graph. `list` is builtin, `Optional` is a generic wrapper, and `object` is the root type. The function *actually* expects an `OrkaGraphDB` instance, but the type annotation says `object`. So the extraction found nothing, and the fast LLM got no helpful context.

2. **`generate_data_construction_guide`** — Passed the function code (with its useless type annotations) to a fast LLM and asked it to explain how to construct valid inputs. Without seeing the actual class definitions the function relies on, the LLM hallucinated — guessed `neo4j.Driver` and `py2neo.Graph` because those are common graph patterns in its training data, not because they exist in this codebase.

The core problem: **Python's type annotations are not reliable enough to determine what objects a function actually needs.** The real clue is in the function body (where it calls `graph_db.graph.nodes(data=True)`) but our LibCST analysis only looks at signatures, not the actual usage patterns inside the function.

A proper solution would need to trace the *usage* of each parameter within the function body to discover what methods/properties are called on it, then search the Graph DB for classes that provide those methods. That's a much deeper analysis than what we attempted.

This was the reply from our prompt: 

```markdown
- **`graph_db`**: An `Optional` — can be `None` to skip graph DB lookups. When not `None`, it must be an object compatible with the internal helper `_lookup_in_graph(graph_db, name)`, which typically expects a graph database client (e.g., a connection or query interface from libraries like `neo4j.Driver`, `py2neo.Graph`, or a custom class) that can search for nodes by name. To construct a valid instance, import the client from the chosen graph library (e.g., `from neo4j import GraphDatabase`) and create a driver/session object (e.g., `driver = GraphDatabase.driver("bolt://localhost:7687", auth=("user","pwd"))`). The function uses `graph_db` only if it is not `None` to perform an initial name lookup; if the lookup succeeds, the stdlib fallback is skipped.

```

Obviously that was terrible. 
Let's talk about strategies around how to enhance our context. 
We might have a tool that goes around doing pre-warming of the context inside the docblocks with the instruction on how to make the models already in place