# Orka — AI-Powered Semantic Code Surgery

Orka is your primary tool for Python method body changes. It uses LibCST for AST-safe surgery and 4-gate validation (snippet AST → assembly → file AST → pytest) before writing to disk.

## When to Use Orka

- **Refactoring method internals** (10+ lines): `orka refactor`
- **Extracting classes**: `orka extract`
- **Generating tests**: `orka testgen`
- **Understanding dependencies**: `orka inspect`

Use direct edits for: tiny fixes (1-3 lines), new file scaffolding, or when orka is unavailable.

## Commands

```bash
# Build/refresh dependency graph
orka scan

# Inspect code structure
orka inspect --id "File:src/app.py"
orka inspect --id "Method:module.Class.method"

# Refactor a method
orka refactor --file src/app.py --cls MyService --method handle_order --req "add input validation"
orka refactor --file src/app.py --method calculate --req "handle edge cases" --dry-run

# Generate tests
orka testgen --file src/app.py --method process --output tests/test_process.py --run

# Extract a class to a new file (auto-heals imports)
orka extract --file src/utils.py --cls OldClass --dest src/models/old_class.py
```

## Pre-Flight Checklist

1. `orka doctor` — verify config and API keys
2. `orka scan` — build dependency graph
3. `orka inspect --id "File:<path>"` — understand the change surface
4. `orka refactor` or `orka testgen` — execute surgery
5. Run tests to verify

## MCP Integration

Add to `.claude/settings.json` MCP config:

```json
{
  "orka": {
    "type": "local",
    "command": ["env/bin/python", "-m", "orka.mcp.server"],
    "enabled": true
  }
}
```

Tools: `orka_scan`, `orka_inspect`, `orka_refactor`, `orka_testgen`, `orka_extract`, `orka_doctor`.
