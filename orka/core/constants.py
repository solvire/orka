"""
Orka constants — single source of truth for provider, editor, and key mappings.

Every mapping that was previously duplicated across cli.py, config.py,
clients.py, and init_helper.py lives here.  All values are plain strings
and dicts — no imports from orka.config or other runtime modules — so this
module is safe to import from anywhere without circular-dependency risk.
"""

from __future__ import annotations


# ===================================================================
# Provider registry
# ===================================================================

SUPPORTED_PROVIDERS: list[str] = [
    "openai",
    "deepseek",
    "together_ai",
    "gemini",
    "anthropic",
    "openai_compat",
]
"""Canonical list of LLM provider slugs accepted by the CLI and client factory."""


PROVIDER_KEY_ATTR_MAP: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together_ai": "TOGETHER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai_compat": "API_KEY",
}
"""Provider slug → Settings attribute name for its API key.

Consumers resolve the actual value via ``getattr(settings, attr_name, "")``.
"""


PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "deepseek": "deepseek-coder",
    "together_ai": "zai-org/GLM-5.1",
    "gemini": "gemini-2.0-flash",
    "anthropic": "claude-sonnet-4-20250514",
    "openai_compat": "gpt-4o",
}
"""Provider slug → default model name when no override is set."""


PROVIDER_MODEL_OVERRIDE_ATTR_MAP: dict[str, str] = {
    "openai": "OPENAI_MODEL",
    "deepseek": "DEEPSEEK_MODEL",
    "together_ai": "TOGETHER_MODEL",
    "gemini": "GEMINI_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
}
"""Provider slug → Settings attribute name for provider-specific model override."""


PROVIDER_API_BASE_ATTR_MAP: dict[str, str] = {
    "openai": "OPENAI_API_BASE",
    "deepseek": "DEEPSEEK_API_BASE",
    "openai_compat": "API_BASE",
}
"""Provider slug → Settings attribute name for API base URL override."""


# ===================================================================
# API key detection (for doctor / report)
# ===================================================================

API_KEY_ATTRS: list[tuple[str, str]] = [
    ("OPENAI_API_KEY", "OpenAI"),
    ("DEEPSEEK_API_KEY", "DeepSeek"),
    ("TOGETHER_API_KEY", "Together AI"),
    ("GEMINI_API_KEY", "Gemini"),
    ("ANTHROPIC_API_KEY", "Anthropic"),
    ("OPENROUTER_API_KEY", "OpenRouter"),
    ("GROQ_API_KEY", "Groq"),
    ("API_KEY", "Generic OpenAI-compatible"),
]
"""(Settings attr name, human label) for every detectable API key.

Used by ``orka doctor`` and ``Settings.report()`` to enumerate keys.
This is a superset of SUPPORTED_PROVIDERS because OpenRouter and Groq
are accessed via the ``openai_compat`` provider but have their own keys.
"""


# ===================================================================
# Editor / IDE registry
# ===================================================================

EDITOR_REGISTRY: dict[str, dict[str, str]] = {
    "continue-dev": {
        "display_name": "Continue.dev",
        "rule_path": ".continue/rules/orka-orchestrator.mdc",
        "template_path": "docs/templates/continue-rule.mdc",
    },
    "cursor": {
        "display_name": "Cursor",
        "rule_path": ".cursor/rules/orka-orchestrator.mdc",
        "template_path": "docs/templates/continue-rule.mdc",
    },
    "claude-code": {
        "display_name": "Claude Code",
        "rule_path": ".claude/rules/CLAUDE.md",
        "template_path": "docs/templates/continue-rule.mdc",
    },
    "kilo": {
        "display_name": "Kilo",
        "rule_path": ".kilorules",
        "template_path": "docs/templates/kilo-rule.md",
    },
}
"""Editor slug → config dict (display_name, rule_path, template_path).

Replaces the four separate maps that previously existed:
  - EDITOR_CHOICES (interactive prompt)
  - editor_map (slug → display name)
  - editor_path_map (slug → rule path)
  - _EDITOR_TEMPLATE_MAP (slug → template path)
"""

DEFAULT_EDITOR_TEMPLATE = "docs/templates/continue-rule.mdc"
"""Fallback template when an editor slug isn't found in EDITOR_REGISTRY."""


EDITOR_PROMPT_CHOICES: dict[str, tuple[str, str]] = {
    str(i + 1): (cfg["display_name"], cfg["rule_path"])
    for i, (slug, cfg) in enumerate(EDITOR_REGISTRY.items())
}
"""Numbered-choice mapping for the interactive ``orka init`` prompt.

Keys are ``"1"``, ``"2"``, …; values are ``(display_name, rule_path)`` tuples
derived from :data:`EDITOR_REGISTRY`.  The corresponding slug is recovered
via ``list(EDITOR_REGISTRY)[int(choice) - 1]``.
"""