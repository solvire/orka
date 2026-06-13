"""
Prompt compilation engine — renders templates with injected rules using
custom ``%%variable%%`` delimiters.

``%%var%%`` was chosen over alternatives (Jinja2's ``{{var}}``, stdlib's
``{var}`` or ``$var``) because:

* **Zero collision** — ``%%`` is meaningless in Python, YAML, Markdown,
  JSON, shell, math, finance, LaTeX, and git.  F-string braces ``{x}``,
  dict literals, JSON, and ``$`` shell variables all pass through
  untouched — no escaping needed.
* **Visible** — ``%%existing_code%%`` is instantly recognisable as a
  placeholder to both humans and LLMs.
* **No dependencies** — simple regex substitution, pure stdlib.
* **No escaping** — the Jinja2 bug (``{`` → ``{{``) that forced manual
  escaping in ``generator.py`` simply doesn't exist here.

Core class
----------
:class:`PromptCompiler` — the central entry point for assembling a prompt.

Usage::

    from orka.core.templates import PromptTemplate, InjectionPoint
    from orka.core.rule_resolver import resolve_rules
    from orka.core.compiler import PromptCompiler

    compiler = PromptCompiler()
    template = PromptTemplate(...)
    rules = resolve_rules(template.name, template.injection_points, ...)
    final_prompt = compiler.compile(template, rules, context_data={...})
"""

from __future__ import annotations

import logging
import re
from typing import Any

from orka.core.templates import InjectionPoint, InjectionRule, PromptTemplate

logger = logging.getLogger(__name__)

# ===================================================================
# Constants
# ===================================================================

# Maximum total character length for all rules combined before truncation.
# This is a rough proxy for a ~2000-token budget (assuming ~2 chars/token
# for English prose). Adjust via env var or config in the future.
_DEFAULT_RULE_BUDGET_CHARS = 4000

# Regex to find %%variable%% placeholders — custom delimiter, zero collisions.
_PLACEHOLDER_RE = re.compile(r"%%([a-zA-Z_][a-zA-Z_0-9]*)%%")


# ===================================================================
# Context budgeting
# ===================================================================


def _enforce_rule_budget(
    rules: list[InjectionRule],
    budget_chars: int = _DEFAULT_RULE_BUDGET_CHARS,
) -> list[InjectionRule]:
    """Truncate rules to fit within *budget_chars* total characters.

    Rules are already sorted by ``(priority, -tier, name)`` from the
    resolver — **lower** ``priority`` means **more important**. This
    function drops the least important rules (highest priority integer)
    until the total character count of the remaining rules fits within
    the budget.

    Parameters
    ----------
    rules
        A list of :class:`InjectionRule` instances, assumed to be sorted
        by increasing priority (most important first).
    budget_chars
        Maximum total character length for all rules' ``text`` fields
        combined.

    Returns
    -------
    list[InjectionRule]
        Rules that fit within the budget. Dropped rules are logged as
        warnings.

    Notes
    -----
    This operates on the **flattened** list of all rules across all
    injection points. If you need per-injection-point budgets, apply
    this function to each group separately before rendering.
    """
    if not rules:
        return rules

    total_chars = sum(len(r.text) for r in rules)

    if total_chars <= budget_chars:
        return rules

    kept: list[InjectionRule] = []
    running = 0
    dropped: list[str] = []

    for rule in rules:
        rule_len = len(rule.text)
        if running + rule_len <= budget_chars:
            kept.append(rule)
            running += rule_len
        else:
            dropped.append(rule.name)
            logger.warning(
                "Context budget exceeded — dropping rule %r "
                "(priority=%d, tier=%d, chars=%d)",
                rule.name,
                rule.priority,
                rule.tier,
                rule_len,
            )

    if dropped:
        logger.warning(
            "Context budget: kept %d rules (%d chars), dropped %d rules: %s",
            len(kept),
            running,
            len(dropped),
            ", ".join(dropped),
        )

    return kept


# ===================================================================
# Template rendering helpers
# ===================================================================


def _render_template(source: str, context: dict[str, Any], label: str = "") -> str:
    """Render a template string with ``%%variable%%`` placeholders.

    Uses a simple regex substitution: every ``%%name%%`` pattern in
    *source* is replaced with ``str(context["name"])``.

    This is intentionally simpler than Jinja2 — there are no conditionals,
    no loops, no filters.  If you need conditional logic, add it in
    Python before calling this function.

    Parameters
    ----------
    source
        The template string with ``%%variable%%`` placeholders.
    context
        Variable context for substitution.
    label
        A human-readable label (e.g. ``"system"`` or ``"user"``)
        for error messages.

    Returns
    -------
    str
        The rendered string with all placeholders replaced and whitespace
        stripped.

    Raises
    ------
    KeyError
        If a placeholder in the template is missing from *context*.
    """
    def _replacer(m: re.Match) -> str:
        key = m.group(1)
        if key not in context:
            logger.error(
                "Template variable %r not found in context for %r section. "
                "Available keys: %s",
                key,
                label,
                sorted(context.keys()),
            )
            raise KeyError(key)
        return str(context[key])

    try:
        result = _PLACEHOLDER_RE.sub(_replacer, source)
        return result.strip()
    except KeyError:
        raise


def _validate_placeholders(
    source: str,
    template_name: str,
    section: str,
) -> list[str]:
    """Find all ``%%variable%%`` references in a template string.

    Useful for early validation — catch typos in variable names before
    rendering.

    Parameters
    ----------
    source
        The template string.
    template_name
        Name of the template (for error messages).
    section
        Which section (``"system"`` or ``"user"``).

    Returns
    -------
    list[str]
        Sorted list of unique variable names found.
    """
    variables = sorted(set(m.group(1) for m in _PLACEHOLDER_RE.finditer(source)))
    logger.debug(
        "Template %%r (%%s) uses variables: %%s",
        template_name,
        section,
        ", ".join(variables),
    )
    return variables


# ===================================================================
# Main compiler
# ===================================================================


class PromptCompiler:
    """Compiles :class:`PromptTemplate` instances into final prompt strings.

    The compiler is stateless by design — all state (templates, rules,
    context data) is passed to :meth:`compile`. This makes it safe for
    concurrent use and easy to test.

    Templates use ``%%variable%%`` syntax — a custom delimiter with zero
    collisions.  No escaping of Python braces, dollar signs, or any other
    characters is ever needed.

    Typical workflow::

        compiler = PromptCompiler()
        prompt = compiler.compile(
            template=my_template,
            resolved_rules=rules,           # from resolve_rules()
            context_data={
                "existing_code": "...",
                "business_requirements": "...",
                "graph_constraints": "...",
            },
        )
    """

    def __init__(self, rule_budget_chars: int = _DEFAULT_RULE_BUDGET_CHARS) -> None:
        """Initialise the compiler.

        Parameters
        ----------
        rule_budget_chars
            Maximum total character length for all injected rules.
            Defaults to 4000 (roughly 2000 tokens).
        """
        self.rule_budget_chars = rule_budget_chars

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile(
        self,
        template: PromptTemplate,
        resolved_rules: list[InjectionRule],
        context_data: dict[str, Any] | None = None,
    ) -> str:
        """Render the template with injected rules and runtime data.

        Steps
        -----
        1. **Group** rules by ``injection_point`` into a dict.
        2. **Budget** — apply ``_enforce_rule_budget`` to each group.
        3. **Validate** — log all ``%%placeholder%%`` variables found.
        4. **Render** — substitute all placeholders in ``system`` and
           ``user`` sections via ``_render_template``.
        5. **Return** — concatenate with blank line separator.

        Parameters
        ----------
        template
            The :class:`PromptTemplate` to compile.
        resolved_rules
            Rules returned by :func:`~orka.core.rule_resolver.resolve_rules`.
        context_data
            Arbitrary key-value pairs for placeholder substitution.
            Common keys: ``existing_code``, ``business_requirements``,
            ``class_context``, ``graph_constraints``.

        Returns
        -------
        str
            The fully rendered prompt string, ready to send to the LLM.

        Raises
        ------
        KeyError
            If a required placeholder is missing from *context_data*.
        """
        ctx = dict(context_data or {})

        # ---- Step 1: Group rules by injection point ----
        grouped: dict[str, list[InjectionRule]] = {}
        for rule in resolved_rules:
            point = rule.injection_point.value
            grouped.setdefault(point, []).append(rule)

        # ---- Step 2: Enforce budget per group ----
        for point, rules_at_point in grouped.items():
            budgeted = _enforce_rule_budget(rules_at_point, self.rule_budget_chars)
            grouped[point] = budgeted
            ctx[point] = "\n".join(r.text for r in budgeted)

        # Ensure all template injection points have at least empty values
        for ip in template.injection_points:
            key = ip.value
            if key not in ctx:
                ctx[key] = ""

        # ---- Step 3: Validate placeholders (early warning) ----
        _validate_placeholders(template.system, template.name, "system")
        _validate_placeholders(template.user, template.name, "user")

        # ---- Step 4: Render ----
        rendered_parts: list[str] = []

        if template.system.strip():
            rendered_parts.append(
                _render_template(template.system, ctx, f"{template.name}:system")
            )

        if template.user.strip():
            rendered_parts.append(
                _render_template(template.user, ctx, f"{template.name}:user")
            )

        final = "\n\n".join(rendered_parts).strip()

        # ---- Audit logging ----
        logger.debug(
            "Compiled template %r — %d chars, %d rules across %d injection points",
            template.name,
            len(final),
            len(resolved_rules),
            len(grouped),
        )

        return final
