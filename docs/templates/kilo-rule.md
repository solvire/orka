# Orka — AI-Powered Python Code Surgery

Orka is your primary tool for all Python file modifications. You do **not** use standard file editing tools. Instead, you invoke `orka` via the terminal for safe, AST-based editing and refactoring.

## Available Commands

| Command | Purpose |
|---------|---------|
| `orka scan` | Build the dependency graph + vector search index |
| `orka inspect --id <node>` | Explore codebase structure |
| `orka refactor --file <path> --method <name> --req "<instructions>"` | Surgically refactor a method |
| `orka extract --file <path> --cls <Class> --dest <path>` | Move a class to a new file, auto-heal imports |
| `orka testgen --file <path> --method <name>` | Generate tests |
| `orka doctor [--json]` | Check configuration and provider health |
| `orka prompt --template <name>` | Preview a compiled prompt (no LLM call) |

## The Scalpel — Logic Changes

When modifying business logic in an existing method, DO NOT rewrite the file. Use:

```bash
orka refactor --file <path> --cls <class> --method <method> --req "<instructions>"
```

For standalone functions, omit `--cls`. Use `--dry-run` to preview changes without writing.

## The Transplanter — Splitting Files

When splitting a large file or moving a class, use:

```bash
orka extract --file <path> --cls <class> --dest <new_path>
```

Orka auto-heals imports across the entire project using the dependency graph.

## Test Generation

Generate pytest tests for a method or function:

```bash
orka testgen --file <path> --method <name> --output tests/test_foo.py --run
```

Use `--run` to execute pytest after writing. Use `--n 3` to generate multiple test functions in a loop.

## The Graph DB — Understanding Dependencies

Before major changes, run `orka scan` then `orka inspect --id "File:path/to/file.py"` to view semantic imports.

Inspect specific nodes:
```bash
orka inspect --id "Class:module.path.ClassName"
orka inspect --id "Method:module.path.ClassName.method_name"
```

## Pre-Flight Checklist

1. `orka doctor` — verify configuration and API key health
2. `orka scan` — build/refresh the dependency graph
3. `orka inspect --id "File:<path>"` — understand the change surface
4. `orka refactor` or `orka testgen` — execute the surgery
5. Run the test suite to verify
