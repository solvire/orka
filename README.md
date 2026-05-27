# Orka Tools

AI-powered semantic code surgery for Python codebases.  
Uses AST analysis, LibCST surgical patching, and LLM backends (Together AI / DeepSeek) to refactor and transplant code.

## Installation

```bash
pip install -e .
```

## Commands

| Command | Description |
|---------|-------------|
| `orka scan` | Scan the codebase, build dependency graph and ChromaDB vectors |
| `orka inspect --id <node>` | Inspect a graph node and its connections |
| `orka extract --file --cls --dest` | Extract a class into a new file, auto-healing imports |
| `orka refactor --file --cls --method --req` | Surgically refactor a method's body using AI |

## Configuration

Orka loads configuration from a `.env` file in the current working directory.
Everything is optional — set your API key and go.

### Minimal setup

```env
ORKA_DEFAULT_PROVIDER=together_ai
TOGETHER_API_KEY=tgp_...
```

### Model tiers

Orka supports three model tiers:

| Tier | Purpose | Fallback |
|------|---------|----------|
| `smart` | Architecture, planning, complex reasoning | Provider default |
| `fast` | Summarisation, simple edits | Falls back to `smart` |
| `edit` | Surgical code transformations | Falls back to `smart` |

Each tier resolves in this order:

1. **Explicit override** — `ORKA_SMART_MODEL`, `ORKA_FAST_MODEL`, `ORKA_EDIT_MODEL`
2. **Provider-specific** — `TOGETHER_MODEL`, `DEEPSEEK_MODEL`, etc. (active provider only)
3. **Built-in default** — sensible default for the chosen provider

```env
ORKA_SMART_MODEL=gpt-4o
ORKA_FAST_MODEL=gpt-4o-mini
```

### Full reference

See [example.env](example.env) for every available setting.

## More Info
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)


