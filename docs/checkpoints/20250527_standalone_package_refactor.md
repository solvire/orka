# Checkpoint: Standalone Package Refactor

## Changes Made
1. **Fixed `orka_pkg` → `orka`** — Single reference in `cli.py` line 71 (`-m orka_pkg.cli` → `-m orka.cli`)
2. **Updated `README.md`** — Removed stale `orka-pack` references, added Documentation section with links to ARCHITECTURE.md, ROADMAP.md, notes/
3. **Created `docs/ARCHITECTURE.md`** — Package layout, entry points, key dependencies, configuration
4. **Created `docs/notes/.gitkeep`** — Placeholder for implementation checkpoints

## Already Correct (no changes needed)
- `pyproject.toml` already had `orka = "orka.cli:app"`, `include = ["orka*"]`, `exclude = ["orka.tests*"]`
- `orka/config.py` already had the standalone CWD-based .env loader
- All internal imports already used `orka` (not `orka_pkg`)
- `__init__.py` files already existed in `core/`, `surgery/`, `tests/`
- `docs/ROADMAP.md` already existed

## Verification
- `pip install -e .` — Success
- `orka --help` — Shows all 4 commands: scan, inspect, extract, refactor
