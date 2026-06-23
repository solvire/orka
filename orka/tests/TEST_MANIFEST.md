# Orka Test Manifest

> Auto-generated. Updated when tests are added or changed.
> Last updated: v0.2.0 — Shared Controls Consolidation (7 phases).

## Summary

**420 test definitions across 23 test files.** 419 pass, 1 pre-existing failure
(`test_compile_real_test_template` — template key mismatch, unrelated to consolidation).

### Test files by count

| File | Tests | Module tested |
|------|-------|---------------|
| `test_import_injector.py` | 66 | `orka.core.import_injector` (extract, inject, rewrite, dedupe, auto_import, cascade, harvest) |
| `test_dependency_resolver.py` | 45 | `orka.core.dependency_resolver` (resolve_symbol, resolve_target, build_dependency_map, resolve_undefined_names) |
| `test_validator.py` | 38 | `orka.core.validator` (validate_code_snippet, validate_file, validate_four_gates, ValidationResult) |
| `test_prompt_compiler.py` | 35 | `orka.core.compiler` + `orka.core.rule_resolver` + `orka.core.import_injector.resolve_import_for_test` |
| `test_locator.py` | 28 | `orka.core.locator` (find_method, find_class, get_signature, extract_docstring, extract_docstring_regex) |
| `test_snippet_utils.py` | 24 | `orka.core.snippet_utils` (strip_md_fences, normalize_snippet_indent, sanitize_llm_output) |
| `test_module_resolver.py` | 23 | `orka.core.module_resolver` (node_id_to_module, file_to_module — incl. trailing-dot edge cases) |
| `test_trivia.py` | 21 | `orka.surgery.trivia` (preserve_docstring, normalize_spacing, collapse_blank_lines) |
| `test_orka_transplanter.py` | 19 | `orka.surgery.transplanter` (class extraction + import healing) |
| `test_orka_analyzer.py` | 19 | `orka.surgery.analyzer` (dependency scope analysis) |
| `test_modifier.py` | 16 | `orka.surgery.modifier` (MethodBodyReplacer, preview_patch, apply_llm_patch) |
| `test_import_fixer_detect_undefined.py` | 15 | `orka.core.dependency_resolver._detect_undefined_names` (legacy filename, tests now hit dependency_resolver) |
| `test_import_fixer_stdlib_fallback.py` | 14 | `orka.core.dependency_resolver._stdlib_fallback` (legacy filename) |
| `test_helpers.py` | 14 | `orka.operations.helpers` (template loading, error truncation, fixer prompt) |
| `test_standalone_function.py` | 9 | Standalone function refactoring |
| `test_refactor_result.py` | 9 | `orka.orchestrator.RefactorResult` + `_compute_diff` |
| `test_orka_cascade.py` | 5 | `orka.core.import_injector.cascade_import_updates` (legacy filename) |
| `test_orchestrator.py` | 5 | `orka.orchestrator.Orchestrator` |
| `test_orka_edge_cases.py` | 4 | Edge cases in surgery pipeline |
| `test_ingester.py` | 4 | `orka.core.ingester.OrkaGraphDB` |
| `test_cli_commands.py` | 4 | `orka.cli` (prompt + testgen commands) |
| `test_orka_dual_brain.py` | 2 | Dual-brain (smart + fast) LLM routing |
| `test_e2e_smoke.py` | 1 | Live E2E (skipped without API key) |

---

## New test files (v0.2.0 consolidation)

### test_module_resolver.py — 23 tests
Tests `node_id_to_module` (Class:, Function:, Method: nodes, edge cases: no colon,
empty prefix, single-part, trailing/leading/double dots) and `file_to_module`
(absolute, relative, `__init__.py`, base_dir stripping, Windows paths).

### test_dependency_resolver.py — 45 tests
Tests `resolve_symbol` (same-module-first, any-module, None), `resolve_target`
(graph lookup + file fallback), `build_dependency_map`, `build_caller_constraints`,
`resolve_undefined_names` (pyflakes + graph + stdlib fallback). Uses mock graph DB.

### test_import_injector.py — 66 tests
Tests all idempotent primitives: `extract_imports` (all/filtered, ImportStar,
mixed lines), `inject_imports` (dedup, empty source, bare import),
`rewrite_import` (simple, multi-name split, no-op, idempotent),
`dedupe_imports` (merge, sort, empty), plus orchestrators `auto_import`,
`resolve_import_for_test`, `cascade_import_updates`, `harvest_and_dedupe`.

### test_locator.py — 28 tests
Tests `find_method` (simple, nested `Outer.Inner`, standalone, async,
not-found), `find_class`, `get_signature` (params, return type, decorators,
async), `extract_docstring` (CST-based), `extract_docstring_regex`.

### test_trivia.py — 21 tests
Tests `preserve_docstring` (original has + new lacks → prepend; both have →
new wins; neither → unchanged), `normalize_spacing` (collapse blanks, spacing
after imports, before defs), `collapse_blank_lines`.

---

## Deleted test files (v0.2.0)

| File | Reason |
|------|--------|
| `test_snippet_import_extractor.py` | `SnippetImportExtractor` deleted from `modifier.py` — replaced by `import_injector.extract_imports` |
| `test_import_fixer_inject_imports.py` | `_inject_imports` moved to `import_injector.py` — folded into `test_import_injector.py` |
| `test_import_fixer_module_from_node_id.py` | `_module_from_node_id` moved to `module_resolver.py` — folded into `test_module_resolver.py` |
| `test_orka_synthesizer.py` | Deleted in earlier session (`build_synthesis_prompt` no longer exists) |

---

## Legacy test files (kept, updated imports)

### test_import_fixer_detect_undefined.py — 15 tests
> Tests `dependency_resolver._detect_undefined_names`. Filename retained for
> git history; imports updated to point at `dependency_resolver`.

### test_import_fixer_stdlib_fallback.py — 14 tests
> Tests `dependency_resolver._stdlib_fallback`. Filename retained; imports updated.

### test_orka_cascade.py — 5 tests
> Tests `import_injector.cascade_import_updates`. Filename retained; imports updated.

### test_prompt_compiler.py — 35 tests
> Imports `resolve_import_for_test` from `orka.core.import_injector` (was
> `orka.core.import_fixer`). The `test_compile_real_test_template` test
> FAILS — pre-existing template key mismatch (`data_construction_guide`
> not in context). Not related to consolidation.
