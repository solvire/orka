# Orka — AI-Powered Semantic Code Surgery

Use Orka for Python method body changes, class extraction, and test generation. It uses LibCST for AST-safe surgery and validates through 4 gates (snippet AST → assembly → file AST → pytest) before writing to disk.

## When to Use Orka vs Direct Edit

- **Orka**: method refactoring (10+ lines), class extraction, test generation, dependency inspection
- **Direct edit**: tiny fixes (1-3 lines), new file scaffolding

## Commands

```bash
orka scan                                              # build dependency graph
orka inspect --id "File:src/app.py"                    # inspect dependencies
orka refactor --file src/app.py --cls Foo --method bar --req "add validation"
orka refactor --file src/app.py --method func --req "..." --dry-run
orka testgen --file src/app.py --method process --output tests/test_process.py --run
orka testgen --file src/app.py --method process --n 3 --output tests/test_process.py
orka extract --file src/utils.py --cls OldClass --dest src/models/old.py
orka doctor                                            # health check
orka feedback                                          # self-hardening insights
```

## MCP Integration

Add to Cline's MCP config:

```json
{
  "orka": {
    "type": "local",
    "command": ["env/bin/python", "-m", "orka.mcp.server"],
    "enabled": true
  }
}
```

Available MCP tools: `orka_scan`, `orka_inspect`, `orka_refactor`, `orka_testgen`, `orka_extract`, `orka_doctor`.

## Architecture

- **4-Gate Validation**: Every LLM output passes snippet AST → assembly → file AST → pytest before disk
- **LibCST Surgery**: Preserves signatures, decorators, formatting
- **Fix Loop**: Failed validations trigger LLM repair (up to 3 iterations) with rollback
- **Dependency Graph**: NetworkX graph of all files, classes, methods, imports
