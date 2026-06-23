# Orka Benchmark Studies

This directory documents benchmarks that measure how Orka's surgery pipeline
compares against raw LLM file editing. Each study is captured as a **run** under
`runs/<date>/` so that, as the application evolves, we keep a reproducible record
of *what* was measured, *how*, and *with which code*.

## Layout

```
docs/benchmark/
  README.md                 # this file: methodology + conventions
  runs/
    2026-06-23_v1/          # one archived run
      benchmark_refactor.py # snapshot of the exact script that ran
      results.json          # raw per-target metrics
      report.md             # human-readable report (tables + mermaid)
```

## Versioning convention

The benchmark script lives at `scripts/benchmarks/benchmark_refactor.py`. It is
intentionally kept simple and is **versioned by duplication, not by flags**:

- Run the script; it writes `results.json` + `report.md` to
  `docs/benchmark/latest/` (overwritten each run).
- To archive a run, create `runs/<YYYY-MM-DD>/` and **copy** into it:
  `results.json`, `report.md`, and a snapshot of `benchmark_refactor.py`.
- The snapshot is the authoritative "version" of that run. When the methodology
  or metric definitions change, the new snapshot differs from the old one, so
  historical runs stay reproducible and comparable. Duplicating the script is
  expected and fine.

## Methodology

For each target method the script runs two approaches and compares them.

### Orka pipeline (dry-run)

Driven directly through `orka.operations.graph.run_surgery` with `dry_run=True`
(equivalent to `orka refactor --dry-run`), so the full state is available:

- `compiled_prompt` -> `prompt_chars`
- LLM call count is measured by monkeypatching
  `OrkaLangChainClient.generate_code` (the single chokepoint for every LLM call,
  including the context-gathering HyDE + data-construction-guide fast calls, the
  generator, and the fixer).
- `iteration_count` -> fix-loop iterations (`iteration_count - 1`).
- `is_valid` + `validation_output` -> which of the 4 gates passed (gate 4 / pytest
  is skipped in dry-run, so the max is 3).
- `draft_file_content` (assembled output) is additionally checked with pytest so
  "overall success" is judged by the same bar as the raw baseline.
- A pre/post SHA-256 of the source file proves it was never written to.

### Raw LLM baseline

The full source file plus a plain instruction is sent to the same provider/model
Orka uses. The output is written to a **temp** file (never the real source), then:

- `ast.parse` -> `syntax_valid`
- pytest against the module's existing test file -> `pytest_passes`

No validation, no fix loop, one LLM call.

### Test isolation (never touches real source files)

pytest is run in a subprocess where the modified module is loaded from a temp
file under its real dotted name (`orka.core.module_resolver`, etc.) and inserted
into `sys.modules` **before** pytest collects anything. `sys.modules` is always
checked before any finder, so this shadows the editable install cleanly. The real
source files are never modified by either approach.

## Metrics

| Metric | Orka | Raw |
| --- | --- | --- |
| `prompt_chars` | compiled prompt size | full-file prompt size |
| `llm_calls` | 1 generator + fix iterations + 2 context calls | 1 |
| `iterations` | fix-loop iterations (0 = first try) | 0 |
| `gates_passed` | 0-3 (dry-run) | n/a |
| `syntax_valid` | implied by gates | `ast.parse` on output |
| `pytest_passes` | run on assembled output | run on output |
| `success` (overall) | `is_valid AND pytest_passes` | `syntax_valid AND pytest_passes` |

Cross-provider rows:

- **Orka OK / Raw OK** - overall success rate.
- **Orka 1st** - first-try success (iterations==0 AND is_valid).
- **Syntax error prevention** - Orka 100% (gates block invalid code before disk);
  Raw 0% (no gating).
- **Raw output breakage rate** - % raw outputs with syntax errors OR test failures.

## Latest run: 2026-06-23 (v1)

See `runs/2026-06-23_v1/report.md` for the full report. Headline cross-provider
results:

| Profile | Model | Orka OK | Raw OK | Orka 1st | Orka prompt | Raw prompt | Orka t(s) | Raw t(s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| deepseek | deepseek-coder | 100% | 100% | 100% | 4142 | 11635 | 12.3 | 17.6 |
| zai-glm52 | glm-5.2 | 86% | 100% | 86% | 4142 | 11635 | 54.0 | 37.8 |
| together-glm52 | zai-org/GLM-5.2 | 100% | 100% | 86% | 4142 | 11635 | 31.3 | 17.4 |
| groq-llama | llama-3.3-70b-versatile | 86% | 57% | 100% | 4142 | 11635 | 9.0 | 12.5 |

Key takeaways:

- **Token efficiency**: Orka consistently sent ~4,142-char prompts vs raw's
  ~11,635 (2.8x smaller) across every provider - method body + context, not the
  full file.
- **Validation pays off most on weaker/faster models**: groq's raw edits broke
  43% of files, while Orka held 86% by catching syntax/assembly errors before disk.
- **z.ai GLM-5.2**: 100% raw, but slowest on the Orka path (54s) and failed one
  target (`collapse_blank_lines` - repeatedly returned over-indented snippets that
  Gate 1 rejected, exhausting the fix loop).
- **Fix loop rescues**: together/zai each needed a fix iteration on a target that
  ultimately succeeded - the 4-gate loop salvaged a failing first draft.
