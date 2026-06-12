# Session Restart Prompt

Copy and paste this into a new session after compacting history.

---

## Session Setup

Date: $(date +%Y-%m-%d)
Environment: `source /home/solvire/Documents/projects/orka/source/orka/env/bin/activate`
Workspace: `/home/solvire/Documents/projects/orka/source/orka`

## Read First

Read these files in order to understand the architecture:

1. `.continue/rules/orka-orchestrator.mdc` — orchestrator manual, tool usage rules
2. `docs/checkpoints/` — the most recent checkpoint files (sorted by date, newest first)
3. `orka/operations/graph.py` — surgery graph pipeline (entry point for all code generation)
4. `orka/operations/state.py` — SurgeryState type
5. `orka/operations/controllers/generator.py` — LLM invocation
6. `orka/operations/controllers/validator.py` — validation gates
7. `orka/operations/controllers/context.py` — context gathering
8. `orka/operations/controllers/fixer.py` — fix loop
9. `orka/operations/helpers.py` — template loading, fixer prompt builder
10. `orka/clients.py` — OrkaClientFactory, OrkaLangChainClient
11. `orka/cli.py` — CLI commands (refactor, testgen, prompt, doctor)

## Next Session Goal: Dogfood Tests

The surgery graph pipeline generates tests via `orka testgen --file <src> --method <func> --n <count> --output <path>`. 

Item 5 from the priority list: use the surgery graph to generate tests for its own utility functions:

```bash
# Example (adjust paths as needed)
orka testgen --file orka/operations/helpers.py \
  --method build_fixer_prompt \
  --n 3 \
  --output orka/tests/test_helpers.py \
  --run
```

What needs attention:
- `tests/TEST_MANIFEST.md` lists expected tests vs actual files — many listed don't exist yet
- Test discipline says all tests hit real APIs (DeepSeek, Tavily) and real DB objects
- Recent changes (CLI wired into surgery graph, LangSmith removed, graph.py cleanup) may have broken existing tests
- Run `python -m pytest orka/tests/ -x -v 2>&1 | head -80` first to get baseline
