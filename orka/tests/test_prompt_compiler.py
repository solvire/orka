"""Tests for the prompt compiler module – templates, injection rules, resolution and compile."""

import textwrap
from pathlib import Path

import pytest
import yaml

from orka.core.templates import (
    OutputType,
    InjectionPoint,
    PromptTemplate,
    InjectionRule,
)
from orka.core.prompt_compiler import (
    parse_mdc_file,
    load_rules_from_directory,
    resolve_rules,
    enforce_rule_budget,
    compile_prompt,
    resolve_import,
)


# ---------------------------------------------------------------------------
# OutputType enum
# ---------------------------------------------------------------------------

class TestOutputTypeEnum:
    def test_values(self):
        assert OutputType.body.value == "body"
        assert OutputType.standalone.value == "standalone"
        assert OutputType.new_file.value == "new_file"

    def test_from_string(self):
        assert OutputType("body") == OutputType.body
        assert OutputType("standalone") == OutputType.standalone
        assert OutputType("new_file") == OutputType.new_file

    def test_all_members_covered(self):
        members = sorted(m.name for m in OutputType)
        assert members == ["body", "new_file", "standalone"]


# ---------------------------------------------------------------------------
# InjectionPoint enum
# ---------------------------------------------------------------------------

class TestInjectionPointEnum:
    def test_values(self):
        assert InjectionPoint.system_header.value == "system_header"
        assert InjectionPoint.constraints_top.value == "constraints_top"
        assert InjectionPoint.constraints_bottom.value == "constraints_bottom"
        assert InjectionPoint.quality_gates.value == "quality_gates"
        assert InjectionPoint.style_guide.value == "style_guide"

    def test_from_string(self):
        assert InjectionPoint("system_header") == InjectionPoint.system_header
        assert InjectionPoint("quality_gates") == InjectionPoint.quality_gates


# ---------------------------------------------------------------------------
# PromptTemplate
# ---------------------------------------------------------------------------

class TestPromptTemplate:
    def test_minimal_creation(self):
        t = PromptTemplate(name="test", system="S", user="U")
        assert t.name == "test"
        assert t.system == "S"
        assert t.user == "U"
        assert t.output_type == OutputType.body  # default
        assert t.injection_points == []

    def test_with_injection_points(self):
        points = [InjectionPoint.system_header, InjectionPoint.quality_gates]
        t = PromptTemplate(
            name="with_pts",
            system="S",
            user="U",
            injection_points=points,
        )
        assert t.injection_points == points

    def test_output_type_standalone(self):
        t = PromptTemplate(
            name="standalone_test",
            system="S",
            user="U",
            output_type=OutputType.standalone,
        )
        assert t.output_type == OutputType.standalone

    def test_extra_fields_ignored(self):
        t = PromptTemplate(
            name="extra",
            system="S",
            user="U",
            ignored_field="should be ignored",
        )
        assert not hasattr(t, "ignored_field")


# ---------------------------------------------------------------------------
# InjectionRule
# ---------------------------------------------------------------------------

class TestInjectionRule:
    def test_minimal_creation(self):
        r = InjectionRule(name="no_imports", text="No imports.")
        assert r.name == "no_imports"
        assert r.text == "No imports."
        assert r.tier == 1
        assert r.priority == 100
        assert r.applies_to == ["*"]

    def test_with_all_fields(self):
        r = InjectionRule(
            name="use_pytest",
            text="Use pytest.",
            tier=3,
            priority=10,
            applies_to=["test"],
        )
        assert r.tier == 3
        assert r.priority == 10
        assert r.applies_to == ["test"]

    def test_tier_excluded_from_serialisation(self):
        r = InjectionRule(name="no_side_effects", text="No side effects.", tier=2)
        dump = r.model_dump()
        assert "tier" not in dump

    def test_applies_to_default_wildcard(self):
        r = InjectionRule(name="wildcard", text="x")
        assert r.applies_to == ["*"]


# ---------------------------------------------------------------------------
# parse_mdc_file
# ---------------------------------------------------------------------------

class TestParseMdcFile:
    def test_parse_builtin_no_imports(self, tmp_path):
        """Parse a real .mdc file – no_imports rule."""
        mdc_content = textwrap.dedent("""\
            name: no_imports
            injection_point: system_header
            priority: 100
            tier: 1
            applies_to: ["*"]
            text: |
              Do not import anything in the generated code.
        """)
        path = tmp_path / "no_imports.mdc"
        path.write_text(mdc_content, encoding="utf-8")
        rule = parse_mdc_file(path)
        assert rule.name == "no_imports"
        assert rule.injection_point == InjectionPoint.system_header
        assert rule.priority == 100
        assert rule.tier == 1

    def test_parse_builtin_use_pytest_raises(self, tmp_path):
        """Parse a .mdc file with constraints_bottom and applies_to test."""
        mdc_content = textwrap.dedent("""\
            name: use_pytest_raises
            injection_point: constraints_bottom
            priority: 50
            tier: 1
            applies_to: ["test"]
            text: |
              Use `with pytest.raises(...)` for exception tests.
        """)
        path = tmp_path / "use_pytest_raises.mdc"
        path.write_text(mdc_content, encoding="utf-8")
        rule = parse_mdc_file(path)
        assert rule.name == "use_pytest_raises"
        assert rule.injection_point == InjectionPoint.constraints_bottom
        assert rule.applies_to == ["test"]

    def test_parse_builtin_test_behavior_not_mocks(self, tmp_path):
        """Parse a .mdc file with quality_gates injection point."""
        mdc_content = textwrap.dedent("""\
            name: test_behavior_not_mocks
            injection_point: quality_gates
            priority: 75
            tier: 1
            applies_to: ["test"]
            text: |
              Prefer testing behaviour, not implementation details. Avoid mocks where possible.
        """)
        path = tmp_path / "test_behavior_not_mocks.mdc"
        path.write_text(mdc_content, encoding="utf-8")
        rule = parse_mdc_file(path)
        assert rule.name == "test_behavior_not_mocks"
        assert rule.injection_point == InjectionPoint.quality_gates
        assert rule.priority == 75


# ---------------------------------------------------------------------------
# load_rules_from_directory
# ---------------------------------------------------------------------------

class TestLoadRulesFromDirectory:
    def test_loads_all_builtin_rules(self, tmp_path):
        """Load 4 .mdc files from a temporary directory."""
        for name, inf, pri, tie, app in [
            ("rule_a", "system_header", 100, 1, ["*"]),
            ("rule_b", "constraints_top", 90, 1, ["*"]),
            ("rule_c", "quality_gates", 80, 1, ["test"]),
            ("rule_d", "style_guide", 70, 1, ["*"]),
        ]:
            content = textwrap.dedent(f"""\
                name: {name}
                injection_point: {inf}
                priority: {pri}
                tier: {tie}
                applies_to: {app}
                text: "Sample rule text."
            """)
            path = tmp_path / f"{name}.mdc"
            path.write_text(content, encoding="utf-8")

        rules = load_rules_from_directory(tmp_path)
        names = {r.name for r in rules}
        assert names == {"rule_a", "rule_b", "rule_c", "rule_d"}

    def test_all_builtin_rules_have_tier_1(self, tmp_path):
        """Every rule loaded from directory has tier=1."""
        for name, inf, pri, tie, app in [
            ("only_tier_1", "system_header", 100, 1, ["*"]),
        ]:
            content = textwrap.dedent(f"""\
                name: {name}
                injection_point: {inf}
                priority: {pri}
                tier: {tie}
                applies_to: {app}
                text: "Tier 1 rule."
            """)
            path = tmp_path / f"{name}.mdc"
            path.write_text(content, encoding="utf-8")

        rules = load_rules_from_directory(tmp_path)
        assert all(r.tier == 1 for r in rules)


# ---------------------------------------------------------------------------
# resolve_rules
# ---------------------------------------------------------------------------

class TestResolveRules:
    def test_resolve_for_refactor_template(self, tmp_path):
        """refactor template should get 2 universal rules (no_imports, no_markdown)."""
        rule_a = InjectionRule(name="no_imports", text="x", applies_to=["*"], injection_point=InjectionPoint.system_header, priority=100, tier=1)
        rule_b = InjectionRule(name="no_markdown", text="y", applies_to=["*"], injection_point=InjectionPoint.constraints_top, priority=90, tier=1)
        rule_c = InjectionRule(name="test_only", text="z", applies_to=["test"], injection_point=InjectionPoint.quality_gates, priority=80, tier=1)
        all_rules = [rule_a, rule_b, rule_c]

        refactor_rules = resolve_rules("refactor", all_rules)
        names = {r.name for r in refactor_rules}
        assert names == {"no_imports", "no_markdown"}

    def test_resolve_for_test_template(self):
        """test template should get all 4 rules."""
        rules = [
            InjectionRule(name="a", text="x", applies_to=["*"]),
            InjectionRule(name="b", text="y", applies_to=["test"]),
            InjectionRule(name="c", text="z", applies_to=["*"]),
            InjectionRule(name="d", text="w", applies_to=["test"]),
        ]
        test_rules = resolve_rules("test", rules)
        assert len(test_rules) == 4

    def test_resolve_filters_by_injection_point(self):
        """Only rules matching the given injection points returned."""
        rules = [
            InjectionRule(name="sys", text="x", injection_point=InjectionPoint.system_header),
            InjectionRule(name="con", text="y", injection_point=InjectionPoint.constraints_top),
            InjectionRule(name="qua", text="z", injection_point=InjectionPoint.quality_gates),
        ]
        # Only system_header and constraints_top points are considered for refactor
        filtered = resolve_rules("refactor", rules, injection_points=[InjectionPoint.system_header, InjectionPoint.constraints_top])
        assert {r.name for r in filtered} == {"sys", "con"}

    def test_resolve_rules_are_sorted(self):
        """Rules sorted by (priority, -tier, name)."""
        rules = [
            InjectionRule(name="c", text="c", priority=50, tier=2),
            InjectionRule(name="a", text="a", priority=100, tier=1),
            InjectionRule(name="b", text="b", priority=50, tier=2),
        ]
        sorted_rules = resolve_rules("refactor", rules)
        names = [r.name for r in sorted_rules]
        # Highest priority first, then lower tier (higher number first), then alphabetical
        assert names == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# enforce_rule_budget
# ---------------------------------------------------------------------------

class TestEnforceRuleBudget:
    def test_all_rules_fit(self):
        rules = [
            InjectionRule(name="a", text="x", priority=100),
            InjectionRule(name="b", text="y", priority=90),
        ]
        kept = enforce_rule_budget(rules, max_chars=1000)
        assert len(kept) == 2

    def test_drops_lowest_priority(self):
        rules = [
            InjectionRule(name="high", text="x" * 200, priority=100),
            InjectionRule(name="low", text="y" * 200, priority=10),
        ]
        kept = enforce_rule_budget(rules, max_chars=300)
        assert len(kept) == 1
        assert kept[0].name == "high"

    def test_single_rule_exceeds_budget(self):
        rule = InjectionRule(name="huge", text="z" * 500, priority=50)
        kept = enforce_rule_budget([rule], max_chars=100)
        assert len(kept) == 1
        assert kept[0].name == "huge"


# ---------------------------------------------------------------------------
# compile_prompt
# ---------------------------------------------------------------------------

class TestPromptCompiler:
    def test_compile_minimal(self):
        """Compile with one rule and context, check output contains both."""
        template = PromptTemplate(name="min", system="System: %%system_header%%", user="User: %%existing_code%%")
        rules = [InjectionRule(name="x", text="rule text", injection_point=InjectionPoint.system_header)]
        context = {"existing_code": "def f(): pass"}
        result = compile_prompt(template, rules, context)
        assert "rule text" in result
        assert "def f(): pass" in result

    def test_compile_without_rules(self):
        template = PromptTemplate(name="norules", system="Hello", user="World")
        result = compile_prompt(template, [], {})
        assert "Hello" in result
        assert "World" in result

    def test_compile_real_refactor_template(self):
        """Load refactor.yaml, resolve rules, compile with sample data."""
        from orka.operations.helpers import load_template
        tmpl = load_template("refactor")
        rules = resolve_rules("refactor", load_rules_from_directory())
        context = {
            "existing_code": "def add(a, b): return a + b",
            "class_context": "",
            "requirements": "Add documentation",
        }
        result = compile_prompt(tmpl, rules, context)
        assert "def add(a, b): return a + b" in result

    def test_compile_real_test_template(self):
        from orka.operations.helpers import load_template
        tmpl = load_template("test")
        rules = resolve_rules("test", load_rules_from_directory())
        context = {
            "existing_code": "def mult(a, b): return a * b",
        }
        result = compile_prompt(tmpl, rules, context)
        assert "def mult(a, b): return a * b" in result

    def test_all_injection_points_get_context(self):
        """No raw {{ }} remain when all points have empty fallbacks."""
        tmpl = PromptTemplate(
            name="all_pts",
            system="%%system_header%% %%constraints_top%%",
            user="%%existing_code%% %%constraints_bottom%% %%quality_gates%% %%style_guide%%",
            injection_points=list(InjectionPoint),
        )
        rules = [
            InjectionRule(name=x, text="", injection_point=InjectionPoint.system_header),
            InjectionRule(name=x, text="", injection_point=InjectionPoint.constraints_top),
            InjectionRule(name=x, text="", injection_point=InjectionPoint.constraints_bottom),
            InjectionRule(name=x, text="", injection_point=InjectionPoint.quality_gates),
            InjectionRule(name=x, text="", injection_point=InjectionPoint.style_guide),
        ]
        result = compile_prompt(tmpl, rules, {"existing_code": "x"})
        assert "%%" not in result

    def test_compiler_different_instances_independent(self):
        tmpl = PromptTemplate(name="indep", system="S", user="U")
        r1 = compile_prompt(tmpl, [], {"existing_code": "a"})
        tmpl2 = PromptTemplate(name="indep2", system="T", user="V")
        r2 = compile_prompt(tmpl2, [InjectionRule(name="r", text="rule", injection_point=InjectionPoint.system_header)], {"existing_code": "b"})
        assert r1 != r2


# ---------------------------------------------------------------------------
# resolve_import
# ---------------------------------------------------------------------------

class TestResolveImport:
    def test_resolve_from_file_path_with_class(self):
        result = resolve_import("src/payments/processor.py", class_name="OrderProcessor")
        assert result == "from src.payments.processor import OrderProcessor\n"
        assert "OrderProcessor" in result

    def test_resolve_from_file_path_with_method(self):
        result = resolve_import("src/payments/processor.py", method_name="process_payment")
        assert result == "from src.payments.processor import process_payment\n"

    def test_resolve_class_takes_precedence_over_method(self):
        result = resolve_import("src/payments/processor.py", class_name="OrderProcessor", method_name="reject")
        assert "OrderProcessor" in result
        assert "reject" not in result

    def test_resolve_none_when_resolution_fails(self):
        result = resolve_import("")
        assert result is None
