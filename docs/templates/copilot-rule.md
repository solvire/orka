# Orka — AI-Powered Semantic Code Surgery

Orka is a CLI tool for AST-safe Python code surgery. It refactors methods, generates tests, and extracts classes using LibCST with 4-gate validation.

## When to Use Orka

- Refactoring a method body (10+ lines)
- Generating pytest tests for a method
- Extracting a class to a new file (with import healing)
- Inspecting code dependencies before changes

For tiny fixes (1-3 lines) or new file scaffolding, use direct edits.

## Commands

```bash
orka scan                                              # build dependency graph + vector index
orka inspect --id "File:src/app.py"                    # inspect dependencies
orka refactor --file src/app.py --cls Foo --method bar --req "add validation"
orka refactor --file src/app.py --method func --req "..." --dry-run
orka testgen --file src/app.py --method process --output tests/test_process.py --run
orka extract --file src/utils.py --cls OldClass --dest src/models/old.py
orka doctor
orka feedback
```

## MCP Integration (for IDEs that support MCP)

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

## Architecture

- **4-Gate Validation**: snippet AST → assembly → file AST → pytest (before disk write)
- **LibCST Surgery**: preserves signatures, decorators, formatting
- **Fix Loop**: up to 3 LLM repair iterations with rollback on failure
