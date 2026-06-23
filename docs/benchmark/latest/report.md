# Orka Benchmark Study (multi-provider)

- Run at: 2026-06-23T12:48:18
- Profiles: deepseek, zai-glm52, together-glm52, groq-llama
- Targets: 7
- Max Orka fix iterations: 3

## Cross-provider comparison

| Profile | Model | Orka OK | Raw OK | Orka 1st | Orka prompt | Raw prompt | Orka t(s) | Raw t(s) | Orka calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| deepseek | deepseek-coder | 100% | 100% | 100% | 4142 | 11635 | 12.28 | 17.59 | 3.0 |
| zai-glm52 | glm-5.2 | 86% | 100% | 86% | 4142 | 11635 | 54.02 | 37.84 | 3.29 |
| together-glm52 | zai-org/GLM-5.2 | 100% | 100% | 86% | 4142 | 11635 | 31.26 | 17.43 | 3.14 |
| groq-llama | llama-3.3-70b-versatile | 86% | 57% | 100% | 4142 | 11635 | 8.96 | 12.5 | 3.0 |

```mermaid
xychart-beta
    title "Avg prompt size (chars): Orka vs Raw by provider"
    x-axis ["deepseek", "zai-glm52", "together-glm52", "groq-llama"]
    y-axis "chars" 0 --> 13381"
    bar [4142, 4142, 4142, 4142]
    bar [11635, 11635, 11635, 11635]
```

```mermaid
xychart-beta
    title "Avg wall time (s): Orka vs Raw by provider"
    x-axis ["deepseek", "zai-glm52", "together-glm52", "groq-llama"]
    y-axis "seconds" 0 --> 63"
    bar [12, 54, 31, 8]
    bar [17, 37, 17, 12]
```

## Profile: deepseek (deepseek / deepseek-coder)

### Per-target breakdown

| # | Target | Approach | Prompt | LLM | Iter | Gates | Syntax | Pytest | Time(s) | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | module_resolver.py::node_id_to_module | orka | 4095 | 3 | 0 | 3/3 | yes | PASS | 12.7 | PASS |
| 1 | module_resolver.py::node_id_to_module | raw | 2786 | 1 | 0 | n/a | yes | PASS | 5.1 | PASS |
| 2 | module_resolver.py::file_to_module | orka | 3828 | 3 | 0 | 3/3 | yes | PASS | 12.4 | PASS |
| 2 | module_resolver.py::file_to_module | raw | 2791 | 1 | 0 | n/a | yes | PASS | 5.9 | PASS |
| 3 | trivia.py::collapse_blank_lines | orka | 3594 | 3 | 0 | 3/3 | yes | PASS | 12.7 | PASS |
| 3 | trivia.py::collapse_blank_lines | raw | 7402 | 1 | 0 | n/a | yes | PASS | 14.9 | PASS |
| 4 | modifier.py::parse_snippet_to_cst_body | orka | 4666 | 3 | 0 | 3/3 | yes | PASS | 11.8 | PASS |
| 4 | modifier.py::parse_snippet_to_cst_body | raw | 8290 | 1 | 0 | n/a | yes | PASS | 13.7 | PASS |
| 5 | validator.py::validate_code_snippet | orka | 4351 | 3 | 0 | 3/3 | yes | PASS | 11.4 | PASS |
| 5 | validator.py::validate_code_snippet | raw | 19662 | 1 | 0 | n/a | yes | PASS | 28.2 | PASS |
| 6 | validator.py::ValidationResult.__bool__ | orka | 3208 | 3 | 0 | 3/3 | yes | PASS | 11.1 | PASS |
| 6 | validator.py::ValidationResult.__bool__ | raw | 19663 | 1 | 0 | n/a | yes | PASS | 25.8 | PASS |
| 7 | import_injector.py::dedupe_imports | orka | 5257 | 3 | 0 | 3/3 | yes | PASS | 13.7 | PASS |
| 7 | import_injector.py::dedupe_imports | raw | 20855 | 1 | 0 | n/a | yes | PASS | 29.6 | PASS |

### Orka vs Raw

| Metric | Orka | Raw LLM |
| --- | --- | --- |
| First-try success rate | 100% | 100% |
| Avg fix iterations | 0.0 | N/A |
| Avg prompt size (chars) | 4142 | 11635 |
| Avg LLM calls | 3.0 | 1.0 |
| Validation success rate | 100% | 100% |
| Syntax error prevention | 100% | 0% |
| Raw output breakage rate | - | 0% |
| Avg wall time (s) | 12.28 | 17.59 |
| Overall success rate (valid+tests) | 100% | 100% |

## Profile: zai-glm52 (openai_compat / glm-5.2)

### Per-target breakdown

| # | Target | Approach | Prompt | LLM | Iter | Gates | Syntax | Pytest | Time(s) | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | module_resolver.py::node_id_to_module | orka | 4095 | 3 | 0 | 3/3 | yes | PASS | 35.5 | PASS |
| 1 | module_resolver.py::node_id_to_module | raw | 2786 | 1 | 0 | n/a | yes | PASS | 22.3 | PASS |
| 2 | module_resolver.py::file_to_module | orka | 3828 | 3 | 0 | 3/3 | yes | PASS | 47.8 | PASS |
| 2 | module_resolver.py::file_to_module | raw | 2791 | 1 | 0 | n/a | yes | PASS | 24.3 | PASS |
| 3 | trivia.py::collapse_blank_lines | orka | 3594 | 5 | 2 | 0/3 | no | FAIL | 93.0 | FAIL |
| 3 | trivia.py::collapse_blank_lines | raw | 7402 | 1 | 0 | n/a | yes | PASS | 27.6 | PASS |
| 4 | modifier.py::parse_snippet_to_cst_body | orka | 4666 | 3 | 0 | 3/3 | yes | PASS | 48.3 | PASS |
| 4 | modifier.py::parse_snippet_to_cst_body | raw | 8290 | 1 | 0 | n/a | yes | PASS | 33.5 | PASS |
| 5 | validator.py::validate_code_snippet | orka | 4351 | 3 | 0 | 3/3 | yes | PASS | 53.2 | PASS |
| 5 | validator.py::validate_code_snippet | raw | 19662 | 1 | 0 | n/a | yes | PASS | 46.3 | PASS |
| 6 | validator.py::ValidationResult.__bool__ | orka | 3208 | 3 | 0 | 3/3 | yes | PASS | 40.9 | PASS |
| 6 | validator.py::ValidationResult.__bool__ | raw | 19663 | 1 | 0 | n/a | yes | PASS | 54.3 | PASS |
| 7 | import_injector.py::dedupe_imports | orka | 5257 | 3 | 0 | 3/3 | yes | PASS | 59.4 | PASS |
| 7 | import_injector.py::dedupe_imports | raw | 20855 | 1 | 0 | n/a | yes | PASS | 56.5 | PASS |

### Orka vs Raw

| Metric | Orka | Raw LLM |
| --- | --- | --- |
| First-try success rate | 86% | 100% |
| Avg fix iterations | 0.29 | N/A |
| Avg prompt size (chars) | 4142 | 11635 |
| Avg LLM calls | 3.29 | 1.0 |
| Validation success rate | 86% | 100% |
| Syntax error prevention | 100% | 0% |
| Raw output breakage rate | - | 0% |
| Avg wall time (s) | 54.02 | 37.84 |
| Overall success rate (valid+tests) | 86% | 100% |

## Profile: together-glm52 (together_ai / zai-org/GLM-5.2)

### Per-target breakdown

| # | Target | Approach | Prompt | LLM | Iter | Gates | Syntax | Pytest | Time(s) | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | module_resolver.py::node_id_to_module | orka | 4095 | 4 | 1 | 3/3 | yes | PASS | 42.3 | PASS |
| 1 | module_resolver.py::node_id_to_module | raw | 2786 | 1 | 0 | n/a | yes | PASS | 9.9 | PASS |
| 2 | module_resolver.py::file_to_module | orka | 3828 | 3 | 0 | 3/3 | yes | PASS | 33.1 | PASS |
| 2 | module_resolver.py::file_to_module | raw | 2791 | 1 | 0 | n/a | yes | PASS | 16.9 | PASS |
| 3 | trivia.py::collapse_blank_lines | orka | 3594 | 3 | 0 | 3/3 | yes | PASS | 16.6 | PASS |
| 3 | trivia.py::collapse_blank_lines | raw | 7402 | 1 | 0 | n/a | yes | PASS | 14.6 | PASS |
| 4 | modifier.py::parse_snippet_to_cst_body | orka | 4666 | 3 | 0 | 3/3 | yes | PASS | 30.1 | PASS |
| 4 | modifier.py::parse_snippet_to_cst_body | raw | 8290 | 1 | 0 | n/a | yes | PASS | 11.2 | PASS |
| 5 | validator.py::validate_code_snippet | orka | 4351 | 3 | 0 | 3/3 | yes | PASS | 20.6 | PASS |
| 5 | validator.py::validate_code_snippet | raw | 19662 | 1 | 0 | n/a | yes | PASS | 23.4 | PASS |
| 6 | validator.py::ValidationResult.__bool__ | orka | 3208 | 3 | 0 | 3/3 | yes | PASS | 19.6 | PASS |
| 6 | validator.py::ValidationResult.__bool__ | raw | 19663 | 1 | 0 | n/a | yes | PASS | 20.7 | PASS |
| 7 | import_injector.py::dedupe_imports | orka | 5257 | 3 | 0 | 3/3 | yes | PASS | 56.5 | PASS |
| 7 | import_injector.py::dedupe_imports | raw | 20855 | 1 | 0 | n/a | yes | PASS | 25.2 | PASS |

### Orka vs Raw

| Metric | Orka | Raw LLM |
| --- | --- | --- |
| First-try success rate | 86% | 100% |
| Avg fix iterations | 0.14 | N/A |
| Avg prompt size (chars) | 4142 | 11635 |
| Avg LLM calls | 3.14 | 1.0 |
| Validation success rate | 100% | 100% |
| Syntax error prevention | 100% | 0% |
| Raw output breakage rate | - | 0% |
| Avg wall time (s) | 31.26 | 17.43 |
| Overall success rate (valid+tests) | 100% | 100% |

## Profile: groq-llama (openai_compat / llama-3.3-70b-versatile)

### Per-target breakdown

| # | Target | Approach | Prompt | LLM | Iter | Gates | Syntax | Pytest | Time(s) | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | module_resolver.py::node_id_to_module | orka | 4095 | 3 | 0 | 3/3 | yes | PASS | 2.8 | PASS |
| 1 | module_resolver.py::node_id_to_module | raw | 2786 | 1 | 0 | n/a | yes | PASS | 1.6 | PASS |
| 2 | module_resolver.py::file_to_module | orka | 3828 | 3 | 0 | 3/3 | yes | PASS | 2.8 | PASS |
| 2 | module_resolver.py::file_to_module | raw | 2791 | 1 | 0 | n/a | yes | PASS | 1.9 | PASS |
| 3 | trivia.py::collapse_blank_lines | orka | 3594 | 3 | 0 | 3/3 | yes | PASS | 3.0 | PASS |
| 3 | trivia.py::collapse_blank_lines | raw | 7402 | 1 | 0 | n/a | yes | PASS | 3.3 | PASS |
| 4 | modifier.py::parse_snippet_to_cst_body | orka | 4666 | 3 | 0 | 3/3 | yes | PASS | 9.2 | PASS |
| 4 | modifier.py::parse_snippet_to_cst_body | raw | 8290 | 1 | 0 | n/a | yes | PASS | 8.4 | PASS |
| 5 | validator.py::validate_code_snippet | orka | 4351 | 3 | 0 | 3/3 | yes | PASS | 22.3 | PASS |
| 5 | validator.py::validate_code_snippet | raw | 19662 | 1 | 0 | n/a | no | FAIL | 22.3 | FAIL |
| 6 | validator.py::ValidationResult.__bool__ | orka | 3208 | 3 | 0 | 3/3 | yes | PASS | 9.2 | PASS |
| 6 | validator.py::ValidationResult.__bool__ | raw | 19663 | 1 | 0 | n/a | yes | FAIL | 23.7 | FAIL |
| 7 | import_injector.py::dedupe_imports | orka | 5257 | 3 | 0 | 3/3 | yes | FAIL | 13.4 | FAIL |
| 7 | import_injector.py::dedupe_imports | raw | 20855 | 1 | 0 | n/a | yes | FAIL | 26.3 | FAIL |

### Orka vs Raw

| Metric | Orka | Raw LLM |
| --- | --- | --- |
| First-try success rate | 100% | 57% |
| Avg fix iterations | 0.0 | N/A |
| Avg prompt size (chars) | 4142 | 11635 |
| Avg LLM calls | 3.0 | 1.0 |
| Validation success rate | 100% | 86% |
| Syntax error prevention | 100% | 0% |
| Raw output breakage rate | - | 43% |
| Avg wall time (s) | 8.96 | 12.5 |
| Overall success rate (valid+tests) | 86% | 57% |

## Metric definitions

- **orka_first_try**: iterations==0 AND is_valid (gates 1-3 on first generation)
- **orka_overall**: is_valid AND orka_pytest_passes (assembled output passes module tests)
- **raw_overall**: syntax_valid AND pytest_passes (single LLM call, no fix loop)
- **syntax_error_prevention**: Orka=100% (gates block invalid code before disk); Raw=0% (no gating)
- **raw_output_breakage_rate**: % raw outputs with syntax errors OR test failures
