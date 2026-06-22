---
description: Run read-only architecture + safety review on current uncommitted changes
agent: code
subtask: true
---
Review the current uncommitted changes. First run `git status` and `git diff` to see the change
surface (include untracked files via `git status --porcelain`).

Then invoke two reviewers via the Task tool, one after the other:
1. @reviewer-architecture -- enforces the 4 architectural constraints, module layering, LibCST flat grammar, naming
2. @reviewer-safety -- checks state-schema integrity, gate bypasses, raw-ast patching, secret exposure

Consolidate both into a single report with these sections:
- **BLOCKERS** (must fix before commit)
- **WARNINGS** (should fix)
- **NITS** (optional)

Do NOT edit any files. This is read-only review. Present the consolidated report to the user.
