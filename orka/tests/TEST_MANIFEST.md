# Orka Test Manifest

> Auto-generated. Updated when tests are added or changed.

## Test Validator (`tests/test_validator.py`) — 16 tests

Existing. No changes needed.

## Test RefactorResult (`tests/test_refactor_result.py`) — 6 tests

Existing. No changes needed.

## Test Prompt Compiler (`tests/test_prompt_compiler.py`) — ➕ NEEDS CREATION

### TestOutputTypeEnum (3 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_values` | OutputType.body == "body", .standalone == "standalone", .new_file == "new_file" |
| 2 | `test_from_string` | OutputType("body") == OutputType.body |
| 3 | `test_all_members_covered` | All 3 enum members exist |

### TestInjectionPointEnum (2 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_values` | All 5 injection points (system_header, constraints_top, constraints_bottom, quality_gates, style_guide) |
| 2 | `test_from_string` | InjectionPoint("system_header") == InjectionPoint.system_header |

### TestPromptTemplate (4 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_minimal_creation` | name, system, user — output_type defaults to body |
| 2 | `test_with_injection_points` | injection_points accepts list of InjectionPoint enums |
| 3 | `test_output_type_standalone` | output_type can be set to standalone |
| 4 | `test_extra_fields_ignored` | extra="ignore" swallows unknown fields |

### TestInjectionRule (4 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_minimal_creation` | name, text — defaults: tier=1, priority=100, applies_to=["*"] |
| 2 | `test_with_all_fields` | All fields including tier=3, priority=10, applies_to=["test"] |
| 3 | `test_tier_excluded_from_serialisation` | tier has exclude=True in model_dump() |
| 4 | `test_applies_to_default_wildcard` | Default applies_to is ["*"] |

### TestParseMdcFile (3 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_parse_builtin_no_imports` | Parse real .mdc, check name, injection_point, priority, tier |
| 2 | `test_parse_builtin_use_pytest_raises` | Parse real .mdc, check injection_point==constraints_bottom, applies_to=["test"] |
| 3 | `test_parse_builtin_test_behavior_not_mocks` | Parse real .mdc, check injection_point==quality_gates |

### TestLoadRulesFromDirectory (2 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_loads_all_builtin_rules` | Loads 4 rules from BUILTIN_RULES_DIR |
| 2 | `test_all_builtin_rules_have_tier_1` | Every rule loaded has tier=1 |

### TestResolveRules (4 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_resolve_for_refactor_template` | refactor gets 2 universal rules (no_imports, no_markdown) |
| 2 | `test_resolve_for_test_template` | test gets all 4 rules |
| 3 | `test_resolve_filters_by_injection_point` | Only matching injection points returned |
| 4 | `test_resolve_rules_are_sorted` | Rules sorted by (priority, -tier, name) |

### TestEnforceRuleBudget (3 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_all_rules_fit` | All rules kept when under budget |
| 2 | `test_drops_lowest_priority` | Drops least important when over budget |
| 3 | `test_single_rule_exceeds_budget` | Even huge single rule is kept (no alternatives) |

### TestPromptCompiler (6 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_compile_minimal` | Compile with one rule and context, check output contains both |
| 2 | `test_compile_without_rules` | Compile with zero rules still produces output |
| 3 | `test_compile_real_refactor_template` | Load refactor.yaml, resolve rules, compile with sample data |
| 4 | `test_compile_real_test_template` | Load test.yaml, resolve rules, compile with sample data |
| 5 | `test_all_injection_points_get_context` | No raw {{ }} remain when all points have empty fallbacks |
| 6 | `test_compiler_different_instances_independent` | Two compiler instances produce same output for same inputs |

### TestResolveImport (4 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_resolve_from_file_path_with_class` | Returns "from src.payments.processor import OrderProcessor\n" |
| 2 | `test_resolve_from_file_path_with_method` | Returns import with method_name |
| 3 | `test_resolve_class_takes_precedence_over_method` | class_name is used, method_name is ignored |
| 4 | `test_resolve_none_when_resolution_fails` | Empty file_path returns None |

## Test Helpers (`tests/test_helpers.py`) — ✅ DONE (14 tests)

### TestLoadTemplate (4 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_load_real_refactor_template` | Loads real refactor.yaml, checks name, output_type, injection_points, metadata |
| 2 | `test_load_real_test_template` | Loads real test.yaml, checks name, output_type, injection_points |
| 3 | `test_load_template_with_injection_points` | Creates fake YAML with injection_points strings, verifies they become InjectionPoint enums |
| 4 | `test_load_template_raises_file_not_found` | FileNotFoundError for nonexistent template |

### TestExtractErrorSummary (4 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_extracts_failures_section` | Extracts FAILURES section, stopping before short test summary |
| 2 | `test_falls_back_to_tail_lines` | Falls back to last lines when no FAILURES section |
| 3 | `test_returns_output_when_no_failures_and_few_lines` | Returns content when short and no FAILURES |
| 4 | `test_empty_output_returns_empty` | Empty output returns empty string |

### TestTruncateErrorSummary (3 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_short_summary_unchanged` | Under max_chars, unchanged |
| 2 | `test_long_summary_truncated` | Long output truncated with marker |
| 3 | `test_truncation_has_head_tail_and_marker` | Truncated output has head, marker, tail |

### TestBuildFixerPrompt (3 tests)
| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_builds_testgen_prompt` | Builds testgen fix prompt with all context |
| 2 | `test_builds_refactor_prompt` | Builds refactor fix prompt with all context |
| 3 | `test_build_includes_test_file_target_when_provided` | Accepts test_file_target parameter |

## Test CLI Commands (`tests/test_cli_commands.py`) — 🟡 PARTIAL (prompt + testgen)

Not yet needed — tested manually.

---

**Total: 55 test definitions across 5 new test classes**
