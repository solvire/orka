# Phase 1: Prompt Compiler Engine — Core Schemas & Resolution

## Summary

Implemented the three foundational modules of the Orka Prompt Compilation
Engine, following the industry-standard patterns (Continue.dev `.mdc` rules,
Jinja2 templating, three-tier override hierarchy, context budgeting).

## Files Created

### 1. `orka/core/templates.py` — Pydantic schemas

- `OutputType` enum: `body`, `standalone`, `new_file`
- `InjectionPoint` enum: `system_header`, `constraints_top`, `constraints_bottom`,
  `quality_gates`, `style_guide`
- `PromptTemplate` BaseModel: Jinja2 `system`/`user` strings, `injection_points[]`,
  `output_type`, `metadata`
- `InjectionRule` BaseModel: `name`, `injection_point`, `text` (Markdown),
  `applies_to[]`, `priority`, `tier` (excluded from serialisation)

### 2. `orka/core/rule_resolver.py` — Rule loading & resolution

- `parse_mdc_file(file_path)` — Parses `.mdc` files (YAML frontmatter + Markdown
  body). Handles malformed/missing frontmatter gracefully.
- `load_rules_from_directory(directory, tier)` — Loads all `.mdc` files from a
  directory, assigning a tier number.
- `load_rules_by_name(names, candidates)` — Resolves CLI `--rule` flags from a
  candidate pool, setting tier=3.
- `resolve_rules(...)` — Central algorithm implementing the three-tier hierarchy:
  1. Load all rules (Tier 1 builtin → Tier 2 project → Tier 3 CLI)
  2. Merge with override hierarchy (CLI > Project > Builtin)
  3. Filter by `applies_to` tag match
  4. Filter by supported `injection_points`
  5. Sort deterministically by `(priority, -tier, name)`

### 3. `orka/core/compiler.py` — Jinja2 rendering engine

- `PromptCompiler` class with `compile(template, resolved_rules, context_data)`
- Groups rules by `injection_point`, provides two context variables per point:
  - `{{ system_header }}` — joined Markdown text block
  - `{{ system_header_rules }}` — list of `InjectionRule` objects (for `{% for %}`)
- Context budget enforcement: drops lowest-priority rules when total chars > 4000
- Jinja2 with `DebugUndefined` for catching template bugs

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Jinja2 over `.format()` | Conditional logic (`{% if %}`) and loops (`{% for %}`) needed for future templates |
| `.mdc` files for rules | Follows Continue.dev convention; native Markdown rendering in IDEs |
| Dual context vars (`{point}` + `{point}_rules`) | Templates can choose between raw text block or object iteration |
| Per-group budgeting | Prevents one injection point from starving another |
| `synthesizer.py` untouched | Strangler Fig pattern — new code coexists with legacy until Phase 3 |

## Testing

All three modules tested via Python assertions:
- Pydantic enum/model creation and serialisation
- `.mdc` file parsing with frontmatter extraction
- Three-tier override correctness (CLI > Project > Builtin)
- `applies_to` filtering (template-scoped rules)
- Deterministic sorting (`(priority, -tier, name)`)
- Jinja2 template rendering with rules injection
- Context budget enforcement (drops lowest-priority rules)
- Edge cases: empty rules, system-only templates, non-existent rule files
