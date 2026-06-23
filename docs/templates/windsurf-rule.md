# Orka — AI-Powered Semantic Code Surgery

Orka performs AST-safe Python code surgery with 4-gate validation (snippet AST → assembly → file AST → pytest).

## When to Use Orka

- Method body refactoring (10+ lines): `orka refactor`
- Class extraction with import healing: `orka extract`
- Test generation: `orka testgen`
- Dependency inspection: `orka inspect`

Use direct edits for tiny fixes, new file scaffolding, or when orka is unavailable.

## Commands

```bash
orka scan
orka inspect --id "File:src/app.py"
orka refactor --file src/app.py --cls Foo --method bar --req "add validation"
orka testgen --file src/app.py --method process --output tests/test_process.py --run
orka extract --file src/utils.py --cls OldClass --dest src/models/old.py
orka doctor
orka feedback
```

## MCP Integration

Add to Windsurf's MCP config:

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
