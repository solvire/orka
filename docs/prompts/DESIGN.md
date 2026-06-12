# Prompt Architecture: Design Brief

> This document describes the current state of Orka's prompt system and the
> target architecture.  Use this as the starting point for a dedicated design
> session on prompt templates, rules injection, and best-practice catalogs.

---

## 1. Current State

Orka is a CLI tool for AI-powered Python code surgery.  It has two commands
that interact with an LLM, each with its own hard-coded prompt:

### `orka refactor`

**Prompt template:** `build_synthesis_prompt()` in `orka/surgery/synthesizer.py`

A single f-string with these sections:
- System instruction (hard-coded): *"You are a pure code synthesis engine..."*
- Graph dependency constraints (injected at runtime from NetworkX)
- Class context (injected at runtime from LibCST extraction)
- Existing method source (injected)
- Business requirements (injected from `--req` flag)
- Closing instruction: *"SYNTHESIZED BODY LOGIC (RAW PYTHON ONLY):"*

**Output type:** `"body"` — injected into the existing method via LibCST.

### `build_testgen_prompt()` (just added)

Similar structure but hard-coded for pytest generation:
- System instruction: *"You are a pytest specialist..."*
- Same class context + source extraction
- Different constraints (test naming, coverage expectations)
- Different closing: *"PYTHON TEST FUNCTIONS (RAW PYTHON ONLY):"`

**Output type:** `"standalone"` — written to a new file or stdout.

### Problems with the current approach

1. **Duplication** — two nearly identical functions.
2. **Hard-coded** — every new use case requires a new function.
3. **No rule system** — best practices are baked into the template string,
   not separate and composable.
4. **No injection points** — you can't say "add logging best practices to
   every refactor prompt" without editing source code.
5. **No versioning** — prompt changes require code changes.

---

## 2. Target Architecture

### Core concept: A prompt template registry + composable rule system

```
                    ┌──────────────────┐
                    │   Template       │
                    │   Registry       │
                    │                  │
                    │  - "refactor"    │
                    │  - "test"        │
                    │  - "validate"    │
                    │  - "docstring"   │
                    │  - custom        │
                    └──────┬───────────┘
                           │
                           │ references
                           ▼
                    ┌──────────────────┐
                    │  Rule Catalog    │
                    │                  │
                    │  - no_imports    │
                    │  - ensure_logging│
                    │  - test_behavior │
                    │  - error_handling│
                    │  - ...           │
                    └──────────────────┘
```

#### Templates (scaffolds)

Each template is a structured object with:

```python
{
    "name": "test",
    "description": "Generate pytest test functions",
    "system": "You are a pytest specialist...\n{global_rules}\n{rules_system}",
    "user": "...{existing_code}...{class_context}...\n{rules_user}...",
    "output_type": "standalone",   # "body" | "standalone" | "new_file"
    "injection_points": [
        "constraints_top",     # rules injected near the top of the prompt
        "constraints_bottom",  # rules injected near the closing
        "quality_gates",       # rules about what "done" looks like
        "style_guide",         # rules about formatting, naming, patterns
    ],
}
```

The `{rules_*}` placeholders in the template are filled at generation time
by collecting all rules that target that injection point.

#### Rules (composable)

Each rule is a small, focused instruction:

```python
{
    "name": "test_behavior_not_mocks",
    "description": "Tests should verify behavior, not mock internals",
    "injection_point": "quality_gates",
    "text": "Tests must verify observable behavior.  Avoid mocking "
            "internal methods — use dependency injection or real "
            "implementations where possible.",
    "tags": ["testing", "pytest", "best-practice"],
    "applies_to": ["test"],
}
```

Rules are stored separately from templates.  A single rule can be used
across multiple templates.  Rules can be enabled/disabled globally or
per-invocation.

#### Command interface

```bash
# Built-in template
orka gen --file app.py --method process --cls Order --prompt test

# Template + selected rules
orka gen --file app.py --method process --cls Order \\
    --prompt refactor --rule no_imports --rule ensure_logging

# Custom prompt (treats text as business requirements, uses "refactor" template)
orka gen --file app.py --method process --cls Order \\
    --prompt "add input validation and retry logic"

# List available rules
orka rules list

# List available templates
orka templates list
```

---

## 3. What This Unlocks

| Capability | Today | After |
|-----------|-------|-------|
| Add a new gen mode | Write a new function + prompt | Add a template to the registry |
| Add a best-practice rule | Edit the prompt string | Add a rule to the catalog |
| Share rules across modes | Copy-paste | Tag + inject by injection point |
| Override for a project | Impossible | Optional `.orka/rules.yaml` |
| CI/CD integration | None | `orka gen --prompt test --output ... --run` |

---

## 4. Reference: How Others Do This

### Cursor (Composer / Cmd-K)

Cursor uses **`.cursorrules`** — a plain-text file at the project root whose
contents are prepended to every LLM context window.  This is the simplest
possible implementation: one global injection point, no structure.

**What we can learn:** The "inject project-level context" pattern is essential.
We should support an equivalent (`.orka/rules.yaml` or similar) that injects
rules from the project into every `orka gen` invocation.

### Continue.dev

Continue has a more sophisticated **rule system** with:

- **`.continue/rules/`** — a directory of `.mdc` files (Markdown with YAML
  frontmatter).  Each file can target specific files, directories, or
  languages via `globs`.
- **Rules can be "always" or manual** — some always apply, some are
  opt-in per chat.
- **Rules are Markdown** — easy to read, write, and version control.

**What we can learn:** A directory-based rule catalog (like
`.orka/rules/*.yaml`) where each file is a single rule is the right
granularity.  The `globs` concept is useful but we may not need it
immediately — we can start with per-template scoping.

### Aider

Aider uses **convention files** (`CONVENTIONS.md`) that are injected into
the edit window.  It also has a **lazy coding** mode where the model is
encouraged to make minimal, surgical changes.

**What we can learn:** Our "body-only" output for refactoring is directly
inspired by Aider's approach.  The prompt should explicitly discourage the
LLM from rewriting things it doesn't need to touch.

### GitHub Copilot Chat / Copilot Workspace

Copilot uses a combination of **workspace-level instructions** (set in the
IDE settings) and **repository-level rules** (via `.github/copilot-instructions.md`).
It can also scan the existing codebase for similar patterns before generating
code.

**What we can learn:** The "scan for examples" pattern — before generating a
test, find similar tests in the repo and include them as few-shot examples.
This is where our vector DB (ChromaDB) could actually shine.

---

## 5. Future: Automated Code Maintenance Pipeline

The long-term vision is not just an interactive CLI but a **headless pipeline**
that runs on every PR or on a schedule:

```yaml
# .orka/pipeline.yaml
steps:
  - gen:
      file: src/payments/processor.py
      method: calculate_fee
      prompt: test
      output: tests/test_processor.py
      rules: [test_behavior_not_mocks, use_pytest_approx]
      run: true    # execute pytest after generation

  - gen:
      file: src/payments/processor.py
      method: calculate_fee
      prompt: "add input validation for negative amounts"
      rules: [ensure_logging, error_handling]

  - refactor:
      file: src/payments/processor.py
      method: validate_order
      prompt: "use the new OrderValidator service"
      dry_run: true    # review before applying
```

This means:

- **Templates must be deterministic** — same input + same rules = same prompt.
- **Rules must be stable** — a rule change should be a version bump.
- **Output must be machine-parseable** — `RefactorResult` already supports JSON.
- **The prompt registry must be loadable from disk** — so a pipeline runner
  can load templates without importing the full Orka package.

---

## 6. Immediate Next Steps

1. **Design the file format** for templates and rules (YAML with Markdown
   content blocks is the strong candidate).

2. **Design the injection system** — how rules map to injection points,
   how they're rendered into the template, and how conflicts are resolved.

3. **Extract the existing prompts** into the new format — `refactor` and `test`
   become the first two templates in the registry.

4. **Design `.orka/rules.yaml` / `.orka/rules/`** — project-level rule
   overrides and additions.

5. **Build the `prompts.py` module** as the registry + resolver, then
   strip the hard-coded prompt functions from `synthesizer.py`.

6. **Dogfood** — once `orka gen --prompt test` works, use it to generate
   tests for Orka's own codebase.
