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

## The Scalpel — Logic Changes

When modifying business logic in an existing method, DO NOT rewrite the file. Use:

```bash
orka refactor --file <path> --cls <class> --method <method> --req "<instructions>"
```

For standalone functions, omit `--cls`.

## The Transplanter — Splitting Files

When splitting a large file or moving a class, use:

```bash
orka extract --file <path> --cls <class> --dest <new_path>
```

## The Graph DB — Understanding Dependencies

Before major changes, run `orka scan` then `orka inspect --id "File:path/to/file.py"` to view semantic imports.
