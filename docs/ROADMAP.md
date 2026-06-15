# Orka Roadmap
A lightweight, standalone AI-powered Python code surgery toolkit.

## Current State
| Component | Status |
|-----------|--------|
| `orka scan` — Build graph DB + ChromaDB vectors | ✅ |
| `orka inspect` — Explore dependency graph | ✅ |
| `orka extract` — Transplant classes with cascade imports | ✅ |
| `orka refactor` — LLM-based method body replacement | ✅ |
| Standalone package (`pip install -e .`) | ✅ |
| `.env` loading from CWD | ✅ |
| LLM-native docs (`llms.txt`, `llms-full.txt`, `AGENTS.md`) | ✅ |

## Planned
| # | Item | Priority |
|---|------|----------|
| 1 | File read/write abstraction with ignore guards (`*.env`, `*.key`, `secrets/`, etc.) | High |
| 2 | Pre-flight safety check on mutation commands | High |
| 3 | `--dry-run` flag for extract/refactor | Medium |
| 4 | Backup system (`.orka/backups/`) | Medium |
| 5 | Publish to PyPI (`orka-tools`) | Medium |
| 6 | Provider fallback logic (Together → DeepSeek) | Low |
| 7 | Tool-use JSON schemas (`orka/tools_schema.json`) for tool-calling LLMs | Medium |
| 8 | MCP Server (Model Context Protocol) for native tool integration | Low |

## Notes
- See `docs/notes/` for implementation checkpoints.
- See `docs/ARCHITECTURE.md` for LLM-readable package layout.
