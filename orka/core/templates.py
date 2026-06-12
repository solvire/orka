"""
Prompt template and rule schemas for the Orka Prompt Compilation Engine.

Defines the Pydantic models that form the backbone of Orka's composable
prompt system. Models are designed for deterministic serialisation and
disk-backed storage (YAML / ``.mdc`` files).

See Also
--------
docs/prompts/DESIGN.md : Full design brief for the prompt compiler.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ===================================================================
# Enums
# ===================================================================


class OutputType(str, Enum):
    """What kind of output the template expects the LLM to produce.

    Attributes
    ----------
    body
        Code to be injected into an existing method body (via LibCST).
    standalone
        Code written to a new file (e.g. test functions).
    new_file
        A complete new file (e.g. generated module, docstring page).
    """

    body = "body"
    standalone = "standalone"
    new_file = "new_file"


class InjectionPoint(str, Enum):
    """Named slots inside a template where rules can be injected.

    Templates declare which injection points they support. The compiler
    groups resolved rules by their ``injection_point`` field and passes
    each group to the Jinja2 renderer under the corresponding variable
    name (e.g. ``{{ system_header }}``, ``{{ quality_gates }}``).

    Attributes
    ----------
    system_header
        Top of the system prompt — used for overarching role definitions.
    constraints_top
        High-priority constraints, near the top of the user prompt.
    constraints_bottom
        Lower-priority constraints, near the closing of the user prompt.
    quality_gates
        Rules describing what "done" or "acceptable" looks like.
    style_guide
        Formatting, naming conventions, and stylistic preferences.
    """

    system_header = "system_header"
    constraints_top = "constraints_top"
    constraints_bottom = "constraints_bottom"
    quality_gates = "quality_gates"
    style_guide = "style_guide"


# ===================================================================
# Models
# ===================================================================


class PromptTemplate(BaseModel):
    """A prompt template defines the skeleton of an LLM interaction.

    The ``system`` and ``user`` fields are Jinja2 template strings.
    At compile time, the compiler populates them with:

    * Rule groups — each ``InjectionPoint`` value becomes a template
      variable containing a list of ``InjectionRule`` instances that
      target that point.
    * Runtime data — arbitrary key-value pairs passed via
      ``context_data`` (e.g. ``existing_code``, ``graph_constraints``).

    Example
    -------
    A template with ``injection_points=["system_header", "quality_gates"]``
    will have ``{{ system_header }}`` and ``{{ quality_gates }}`` available
    as variables in its Jinja2 strings.

    Attributes
    ----------
    name
        Unique identifier for the template (e.g. ``"refactor"``, ``"test"``).
    description
        Human-readable summary of what this template is for.
    system
        Jinja2 template string for the system-level prompt.
    user
        Jinja2 template string for the user-level prompt.
    output_type
        What the LLM is expected to produce (body, standalone, or new_file).
    injection_points
        List of :class:`InjectionPoint` values that this template supports.
        The compiler uses this to filter which rules are eligible.
    metadata
        Arbitrary key-value store for versioning, tags, or tool-specific
        configuration (e.g. ``{"version": 1, "author": "orka"}``).
    """

    name: str
    description: str = ""
    system: str
    user: str
    output_type: OutputType = OutputType.body
    injection_points: list[InjectionPoint] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # --- pydantic config ------------------------------------------------

    model_config = {"frozen": False, "extra": "ignore"}

    def __str__(self) -> str:
        return (
            f"PromptTemplate(name={self.name!r}, "
            f"output_type={self.output_type.value!r}, "
            f"injection_points={[p.value for p in self.injection_points]})"
        )


class InjectionRule(BaseModel):
    """A single composable rule injected into a prompt template.

    Rules are stored on disk as ``.mdc`` files (Markdown with YAML
    frontmatter) or ``.yaml`` files. The :func:`parse_mdc_file` function
    in :mod:`orka.core.rule_resolver` handles deserialisation.

    The **tier** field is not part of the file format — it is assigned at
    load time by the resolver to track the override hierarchy:

    * ``tier=1`` — built-in rules shipped with Orka (lowest priority).
    * ``tier=2`` — project-level overrides in ``.orka/rules/``.
    * ``tier=3`` — CLI ``--rule`` flags (highest priority).

    Attributes
    ----------
    name
        Unique rule name (used for override matching across tiers).
    description
        Human-readable summary of the rule's intent.
    injection_point
        Which slot in the template this rule targets.
    text
        The rule body as Markdown — this is what gets rendered into
        the prompt.
    applies_to
        List of template names this rule applies to. The wildcard ``"*"``
        means all templates.
    priority
        Numeric priority — **lower** values = higher importance.
        Used by the context budget enforcer to decide which rules to
        truncate first when the prompt exceeds the token budget.
    tier
        Assigned by the resolver at load time. Not serialised to disk.
    """

    name: str
    description: str = ""
    injection_point: InjectionPoint = InjectionPoint.constraints_top
    text: str
    applies_to: list[str] = Field(default_factory=lambda: ["*"])
    priority: int = 100
    tier: int = Field(default=1, exclude=True)  # not serialised

    # --- pydantic config ------------------------------------------------

    model_config = {"frozen": False, "extra": "ignore"}

    def __str__(self) -> str:
        return (
            f"InjectionRule(name={self.name!r}, "
            f"point={self.injection_point.value!r}, "
            f"priority={self.priority}, "
            f"tier={self.tier})"
        )
