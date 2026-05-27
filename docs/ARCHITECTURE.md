# Orka Architecture

> Canonical reference for the Orka code surgery toolkit. Designed to be
> ingested by LLM coding assistants for accurate operations on the codebase.

## Package Layout

```
orka/
├── pyproject.toml
├── README.md
├── docs/
│   ├── ARCHITECTURE.md   (this file)
│   ├── ROADMAP.md
│   └── notes/            (implementation checkpoints)
└── orka/                 (installable package)
    ├── __init__.py
    ├── cli.py            (Typer CLI)
    ├── config.py         (dotenv settings, CWD-based)
    ├── clients.py        (Together AI + DeepSeek LLM clients)
    ├── orchestrator.py   (scan + refactor pipeline)
    ├── core/
    │   ├── __init__.py
    │   ├── cascade.py    (import cascade after class extraction)
    │   ├── ingester.py   (NetworkX graph DB + AST visitor)
    │   └── vector_store.py (ChromaDB embeddings)
    ├── surgery/
    │   ├── __init__.py
    │   ├── analyzer.py   (dependency scope analysis)
    │   ├── modifier.py   (LibCST method body replacement)
    │   ├── synthesizer.py (LLM prompt construction)
    │   └── transplanter.py (class extraction + import healing)
    └── tests/
        └── ...
```

## Entry Points

| Command | Module | Description |
|---------|--------|-------------|
| `orka scan` | `cli.py` | Build dependency graph + vector DB |
| `orka inspect --id ...` | `cli.py` | Query graph node neighbors |
| `orka extract --file --cls --dest` | `cli.py` → `transplanter.py` → `cascade.py` | Move class, heal imports |
| `orka refactor --file --cls --method --req` | `cli.py` → `orchestrator.py` → `modifier.py` | LLM-synthesize method body |

## Key Dependencies

| Library | Purpose |
|---------|---------|
| `typer` | CLI framework |
| `rich` | Terminal output |
| `python-dotenv` | Environment loading |
| `libcst` | Syntax-safe code transformations |
| `networkx` | Dependency graph |
| `chromadb` | Semantic vector search |
| `together` | Together AI SDK (native — measurably better than OpenAI-compat endpoint) |
| `langchain-openai` | OpenAI / DeepSeek / OpenAI-compatible providers |
| `langchain-google-genai` | Google Gemini (optional) |
| `langchain-anthropic` | Anthropic Claude (optional) |

## Configuration

- `.env` in the current working directory is loaded at import time.
- `ORKA_ENV_FILE` overrides the `.env` path.
- API keys use standard names (`OPENAI_API_KEY`, `TOGETHER_API_KEY`, etc.).
- Three model tiers: `smart`, `fast`, `edit` (see `example.env` for full docs).

### Supported providers

| Provider | LangChain backend | Key env var |
|----------|-------------------|-------------|
| OpenAI | `ChatOpenAI` | `OPENAI_API_KEY` |
| DeepSeek | `ChatOpenAI` | `DEEPSEEK_API_KEY` |
| Together AI | Together SDK (native wrapper) | `TOGETHER_API_KEY` |
| Google Gemini | `ChatGoogleGenerativeAI` | `GEMINI_API_KEY` |
| Anthropic | `ChatAnthropic` | `ANTHROPIC_API_KEY` |
| OpenRouter | `ChatOpenAI` | `OPENROUTER_API_KEY` |
| Groq | `ChatOpenAI` | `GROQ_API_KEY` |
| Generic OpenAI-compat | `ChatOpenAI` | `API_KEY` |

### Client architecture

```
OrkaClientFactory.create(provider, model_tier)
    │
    ├── together_ai   →  _TogetherWrapper (wraps Together SDK)
    ├── openai        →  ChatOpenAI
    ├── deepseek      →  ChatOpenAI
    ├── gemini        →  ChatGoogleGenerativeAI
    ├── anthropic     →  ChatAnthropic
    └── openai_compat →  ChatOpenAI
```

Every path returns an object obeying ``.invoke(messages) -> AIMessage``.
Callers never know which SDK is underneath.

