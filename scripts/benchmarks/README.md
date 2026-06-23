# Benchmark scripts

Reusable scripts that benchmark Orka's surgery pipeline against raw LLM editing
across multiple providers/models.

## benchmark_refactor.py

Compares, for each target method, the Orka pipeline (`orka refactor --dry-run`
semantics via `run_surgery`) against a raw LLM baseline (full file + plain
instruction). Prints markdown tables + mermaid charts and writes
`results.json` + `report.md`.

### Run

```bash
# all configured profiles, all 7 targets (default output: docs/benchmark/latest/)
env/bin/python scripts/benchmarks/benchmark_refactor.py

# specific providers
env/bin/python scripts/benchmarks/benchmark_refactor.py --profiles zai-glm52,deepseek

# single provider, fewer targets
env/bin/python scripts/benchmarks/benchmark_refactor.py --provider deepseek --targets 3

# orka only (skip the raw baseline to save cost)
env/bin/python scripts/benchmarks/benchmark_refactor.py --skip-raw
```

### Provider profiles

Defined at the top of the script (all verified alive via a 1-token health check):

| Profile | Provider | Model | Endpoint |
| --- | --- | --- | --- |
| deepseek | deepseek | deepseek-coder | api.deepseek.com |
| zai-glm52 | openai_compat | glm-5.2 | api.z.ai/api/paas/v4 |
| together-glm52 | together_ai | zai-org/GLM-5.2 | Together |
| groq-llama | openai_compat | llama-3.3-70b-versatile | api.groq.com |

Requires the corresponding API keys in `.env` (`DEEPSEEK_API_KEY`,
`ZAI_API_KEY`, `TOGETHER_API_KEY`, `GROQ_API_KEY`).

### Outputs

- `docs/benchmark/latest/results.json` - full per-target records.
- `docs/benchmark/latest/report.md` - markdown tables + mermaid charts.

### Archiving a run (versioning by duplication)

The script is kept simple and versioned by **copy/paste**, not flags. After a
meaningful run, archive it so it stays reproducible as the application evolves:

```bash
DATE=2026-06-23
mkdir -p docs/benchmark/runs/$DATE
cp docs/benchmark/latest/results.json docs/benchmark/runs/$DATE/
cp docs/benchmark/latest/report.md   docs/benchmark/runs/$DATE/
cp scripts/benchmarks/benchmark_refactor.py docs/benchmark/runs/$DATE/
```

The copied `benchmark_refactor.py` snapshot is the authoritative "version" of
that run. See `docs/benchmark/README.md` for the full methodology and metric
definitions.
