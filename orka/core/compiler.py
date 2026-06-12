"""
Prompt compilation engine — renders Jinja2 templates with injected rules.

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
from typing import Any

from jinja2 import Environment

from orka.core.templates import InjectionPoint, InjectionRule, PromptTemplate

logger = logging.getLogger(__name__)

# ===================================================================
# Constants
# ===================================================================

# Maximum total character length for all rules combined before truncation.
# This is a rough proxy for a ~2000-token budget (assuming ~2 chars/token
# for English prose). Adjust via env var or config in the future.
_DEFAULT_RULE_BUDGET_CHARS = 4000


# ===================================================================
# Jinja2 helpers
# ===================================================================


def _build_jinja_env() -> Environment:
    """Create a Jinja2 environment with safe defaults.

    - No auto-escaping (we're generating prompts, not HTML).
    - ``undefined`` set to ``DebugUndefined`` so that missing variables
      render as ``{{ MISSING }}`` instead of raising — useful for
      catching template bugs during development.
    """
    from jinja2 import DebugUndefined

    return Environment(
        undefined=DebugUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


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

    # Work from most important (lowest priority) to least important
    # The list is already sorted ascending by priority.
    total_chars = sum(len(r.text) for r in rules)

    if total_chars <= budget_chars:
        return rules  # all fit

    # Drop from the end (highest priority = least important)
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
# Main compiler
# ===================================================================


class PromptCompiler:
    """Compiles :class:`PromptTemplate` instances into final prompt strings.

    The compiler is stateless by design — all state (templates, rules,
    context data) is passed to :meth:`compile`. This makes it safe for
    concurrent use and easy to test.

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
        1. **Group** rules by their ``injection_point`` into a dict.
        2. **Budget** — apply ``_enforce_rule_budget`` to each group
           independently, ensuring no single injection point blows the
           budget.
        3. **Render** — render both the ``system`` and ``user`` Jinja2
           strings using the grouped rules and ``context_data``.
        4. **Return** — concatenate the system and user prompts with a
           blank line separator, or return just one if the other is empty.

        Parameters
        ----------
        template
            The :class:`PromptTemplate` to compile.
        resolved_rules
            Rules returned by :func:`~orka.core.rule_resolver.resolve_rules`.
            Expected to be sorted deterministically.
        context_data
            Arbitrary key-value pairs for Jinja2 interpolation.
            Common keys: ``existing_code``, ``business_requirements``,
            ``class_context``, ``graph_constraints``.

        Returns
        -------
        str
            The fully rendered prompt string, ready to send to the LLM.

        Raises
        ------
        jinja2.TemplateError
            If the template strings are malformed.
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
            ctx[f"{point}_rules"] = budgeted

        # Ensure all template injection points have at least empty values
        for ip in template.injection_points:
            key = ip.value
            if key not in ctx:
                ctx[key] = ""
                ctx[f"{key}_rules"] = []

        # ---- Step 3: Render ----
        env = _build_jinja_env()

        rendered_parts: list[str] = []

        if template.system.strip():
            system_rendered = self._render_string(env, template.system, ctx, "system")
            rendered_parts.append(system_rendered)

        if template.user.strip():
            user_rendered = self._render_string(env, template.user, ctx, "user")
            rendered_parts.append(user_rendered)

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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _render_string(
        env: Environment,
        source: str,
        context: dict[str, Any],
        label: str = "",
    ) -> str:
        """Render a single Jinja2 string with the given context.

        Parameters
        ----------
        env
            A Jinja2 :class:`Environment`.
        source
            The Jinja2 template string.
        context
            Variable context for rendering.
        label
            A human-readable label (e.g. ``"system"`` or ``"user"``)
            for error messages.

        Returns
        -------
        str
            The rendered string.

        Raises
        ------
        jinja2.TemplateError
            On syntax or rendering errors.
        """
        try:
            template = env.from_string(source)
            result = template.render(**context)
            return result.strip()
        except Exception as exc:
            logger.error("Jinja2 render error in %r section: %s", label, exc)
            raise
