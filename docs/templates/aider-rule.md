# Orka — AI-Powered Semantic Code Surgery

Orka is a CLI companion for Aider that provides AST-safe code surgery with 4-gate validation. While Aider excels at whole-file editing and git integration, Orka specializes in surgical method-body refactoring and test generation where preserving signatures and decorators matters.

## When to Use Orka vs Aider

| Scenario | Tool |
|----------|------|
| Refactoring a method body (preserve signature) | Orka |
| Generating focused pytest tests for a method | Orka |
| Extracting a class with cross-project import healing | Orka |
| Inspecting dependency graph before changes | Orka |
| Whole-file rewrites, bulk changes | Aider |
| Git operations, multi-file refactors | Aider |

## Commands

```bash
orka scan                                              # build dependency graph
orka inspect --id "File:src/app.py"                    # inspect dependencies
orka refactor --file src/app.py --cls Foo --method bar --req "add validation"
orka testgen --file src/app.py --method process --output tests/test_process.py --run
orka extract --file src/utils.py --cls OldClass --dest src/models/old.py
orka doctor
```

## Architecture

- **4-Gate Validation**: snippet AST → assembly → file AST → pytest (before disk write)
- **LibCST Surgery**: preserves signatures, decorators, formatting
- **Fix Loop**: up to 3 LLM repair iterations with rollback on failure
