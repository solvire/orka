"""
Standalone configuration loader for the Orka CLI.

Loads .env from the current working directory (where ``orka`` is invoked),
or from the file specified by the ``ORKA_ENV_FILE`` environment variable.
or from a directory specified by ``ORKA_PROJECT_ROOT``.

Design
------
All values are read from environment variables at import time. Every setting
has a sensible default so the tool works with minimal configuration.

Model tiers follow Aider's pattern — a *smart* model handles architecture
and planning, a *fast* model handles quick edits and summarization, and an
*edit* model (defaults to smart) handles surgical code transformations.

API keys use standard environment variable names (``OPENAI_API_KEY``,
``DEEPSEEK_API_KEY``, etc.) so a single ``.env`` can be shared across tools.
"""

import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


# ===================================================================
# Internal helpers
# ===================================================================


def _resolve_project_root() -> Path:
    """
    Resolve the project root.

    Resolution order:
        1. ``ORKA_PROJECT_ROOT`` env var (explicit directory)
        2. ``ORKA_ENV_FILE`` env var (directory containing that file)
        3. Current working directory (``Path.cwd()``)
    """
    explicit = os.getenv("ORKA_PROJECT_ROOT")
    if explicit:
        return Path(explicit).resolve(strict=True)

    env_file = os.getenv("ORKA_ENV_FILE")
    if env_file:
        return Path(env_file).resolve().parent

    return Path.cwd()


def _load_env(project_root: Path) -> None:
    """
    Load .env from the project root, or from ORKA_ENV_FILE.

    This lets the user keep a project-local .env (automatic) or specify a
    central env file for cross-project API keys.

    Raises
    ------
    SystemExit
        If no ``.env`` file exists.
    """
    env_file = os.getenv("ORKA_ENV_FILE")
    if env_file:
        env_path = Path(env_file).resolve()
        if not env_path.is_file():
            print(
                f"[orka.config] FATAL: ORKA_ENV_FILE={env_file} is not a file or does not exist.",
                file=sys.stderr,
            )
            sys.exit(1)
        load_dotenv(env_path, override=False)
        return

    dotenv_path = project_root / ".env"
    if not dotenv_path.is_file():
        print(
            f"[orka.config] FATAL: No .env file found at {dotenv_path}. "
            "Create a .env file or set ORKA_PROJECT_ROOT / ORKA_ENV_FILE.",
            file=sys.stderr,
        )
        sys.exit(1)

    load_dotenv(dotenv_path, override=False)


def _str_env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _bool_env(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes", "y")


def _int_env(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _float_env(key: str, default: float) -> float:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ===================================================================
# Module-level init
# ===================================================================

_PROJECT_ROOT = _resolve_project_root()
_load_env(_PROJECT_ROOT)


# ===================================================================
# Validation — fail early on missing critical config
# ===================================================================

_REQUIRED_VARS = [
    # At least ONE API key must be present (depending on provider)
    ("OPENAI_API_KEY", "OpenAI"),
    ("DEEPSEEK_API_KEY", "DeepSeek"),
    ("TOGETHER_API_KEY", "Together AI"),
    ("GEMINI_API_KEY", "Gemini"),
    ("ANTHROPIC_API_KEY", "Anthropic"),
    ("OPENROUTER_API_KEY", "OpenRouter"),
    ("GROQ_API_KEY", "Groq"),
    ("API_KEY", "Generic OpenAI-compatible"),
]

_configured_providers = [
    label for var, label in _REQUIRED_VARS if os.getenv(var)
]

if not _configured_providers:
    print(
        "[orka.config] WARNING: No API keys found in environment. "
        "Set at least one (e.g. DEEPSEEK_API_KEY, OPENAI_API_KEY, TOGETHER_API_KEY).",
        file=sys.stderr,
    )

# ===================================================================
# Provider registry
# ===================================================================

# Supported providers and their default model names.
DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "deepseek": "deepseek-chat",
    "together_ai": "MiniMaxAI/MiniMax-M2.7",
    "gemini": "gemini-2.0-flash",
    "anthropic": "claude-sonnet-4-20250514",
    "openai_compat": "gpt-4o",  # catch-all for any OpenAI-compatible endpoint
}


# ===================================================================
# Settings
# ===================================================================


class Settings:
    """
    Thin settings container backed by ``os.environ``.

    Access as a module-level singleton::

        from orka.config import settings

        provider = settings.DEFAULT_PROVIDER
        model = settings.smart_model
        key = settings.get_api_key("openai")

    Model tier resolution order
    ---------------------------
    1. Explicit ``ORKA_SMART_MODEL`` / ``ORKA_FAST_MODEL`` / ``ORKA_EDIT_MODEL``
    2. Provider-specific model (e.g. ``TOGETHER_MODEL`` for together_ai)
    3. Default from :data:`DEFAULT_MODELS`
    """

    # -- paths -----------------------------------------------------------
    PROJECT_ROOT: Path = _PROJECT_ROOT

    # ------------------------------------------------------------------
    # Provider selection
    # ------------------------------------------------------------------
    DEFAULT_PROVIDER: str = _str_env("ORKA_DEFAULT_PROVIDER", "together_ai")

    # ------------------------------------------------------------------
    # API keys — standard env var names so .env is portable
    # ------------------------------------------------------------------
    OPENAI_API_KEY: str = _str_env("OPENAI_API_KEY")
    DEEPSEEK_API_KEY: str = _str_env("DEEPSEEK_API_KEY")
    TOGETHER_API_KEY: str = _str_env("TOGETHER_API_KEY")
    GEMINI_API_KEY: str = _str_env("GEMINI_API_KEY")
    ANTHROPIC_API_KEY: str = _str_env("ANTHROPIC_API_KEY")
    OPENROUTER_API_KEY: str = _str_env("OPENROUTER_API_KEY")
    GROQ_API_KEY: str = _str_env("GROQ_API_KEY")

    # Generic fallback for any OpenAI-compatible provider
    API_KEY: str = _str_env("API_KEY")

    # ------------------------------------------------------------------
    # Model tiers
    # ------------------------------------------------------------------
    ORKA_SMART_MODEL: str = _str_env("ORKA_SMART_MODEL", "")
    ORKA_FAST_MODEL: str = _str_env("ORKA_FAST_MODEL", "")
    ORKA_EDIT_MODEL: str = _str_env("ORKA_EDIT_MODEL", "")

    # ------------------------------------------------------------------
    # Provider-specific model overrides (backward compat + fine-grained)
    # ------------------------------------------------------------------
    OPENAI_MODEL: str = _str_env("OPENAI_MODEL", "")
    DEEPSEEK_MODEL: str = _str_env("DEEPSEEK_MODEL", "")
    TOGETHER_MODEL: str = _str_env("TOGETHER_MODEL", "")
    GEMINI_MODEL: str = _str_env("GEMINI_MODEL", "")
    ANTHROPIC_MODEL: str = _str_env("ANTHROPIC_MODEL", "")

    # ------------------------------------------------------------------
    # API connectivity
    # ------------------------------------------------------------------
    # Generic base URL for OpenAI-compatible providers (OpenRouter, Groq, etc.)
    API_BASE: str = _str_env("API_BASE", "")
    # Per-provider API base URLs
    OPENAI_API_BASE: str = _str_env("OPENAI_API_BASE", "https://api.openai.com/v1")
    DEEPSEEK_API_BASE: str = _str_env("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

    # ------------------------------------------------------------------
    # Client behaviour
    # ------------------------------------------------------------------
    TEMPERATURE: float = _float_env("ORKA_TEMPERATURE", 0.1)
    TIMEOUT: int = _int_env("ORKA_TIMEOUT", 120)
    MAX_RETRIES: int = _int_env("ORKA_MAX_RETRIES", 3)
    VERIFY_SSL: bool = _bool_env("ORKA_VERIFY_SSL", True)

    # ------------------------------------------------------------------
    # Behavioural flags
    # ------------------------------------------------------------------
    AUTO_SCAN_AFTER_MUTATION: bool = _bool_env("ORKA_AUTO_SCAN", True)
    DRY_RUN: bool = _bool_env("ORKA_DRY_RUN", False)
    VERBOSE: bool = _bool_env("ORKA_VERBOSE", False)

    # ================================================================
    # Derived helpers
    # ================================================================

    # -- model resolution --------------------------------------------

    def _provider_default_model(self) -> str:
        """Return the default model name for the current provider."""
        return DEFAULT_MODELS.get(self.DEFAULT_PROVIDER, "gpt-4o")

    def _provider_model_override(self) -> str:
        """Return a provider-specific model override if set."""
        provider_map: dict[str, str] = {
            "openai": self.OPENAI_MODEL,
            "deepseek": self.DEEPSEEK_MODEL,
            "together_ai": self.TOGETHER_MODEL,
            "gemini": self.GEMINI_MODEL,
            "anthropic": self.ANTHROPIC_MODEL,
        }
        return provider_map.get(self.DEFAULT_PROVIDER, "")

    @property
    def smart_model(self) -> str:
        """Effective smart model for the current provider."""
        return (
            self.ORKA_SMART_MODEL
            or self._provider_model_override()
            or self._provider_default_model()
        )

    @property
    def fast_model(self) -> str:
        """Effective fast/cheap model (falls back to smart)."""
        return self.ORKA_FAST_MODEL or self.smart_model

    @property
    def edit_model(self) -> str:
        """Effective edit model (falls back to smart)."""
        return self.ORKA_EDIT_MODEL or self.smart_model

    @property
    def model(self) -> str:
        """Alias for smart_model — the default model for most operations."""
        return self.smart_model

    # -- API key resolution ------------------------------------------

    def get_api_key(self, provider: Optional[str] = None) -> str:
        """
        Return the API key for *provider*, or the generic fallback.

        Resolution order:
            1. Provider-specific key (e.g. OPENAI_API_KEY)
            2. Generic API_KEY
            3. Empty string
        """
        prov = provider or self.DEFAULT_PROVIDER
        key_map: dict[str, str] = {
            "openai": self.OPENAI_API_KEY,
            "deepseek": self.DEEPSEEK_API_KEY,
            "together_ai": self.TOGETHER_API_KEY,
            "gemini": self.GEMINI_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
            "openai_compat": self.API_KEY,
        }
        return key_map.get(prov, self.API_KEY)

    # -- API base resolution -----------------------------------------

    def get_api_base(self, provider: Optional[str] = None) -> str:
        """Return the API base URL for *provider*."""
        prov = provider or self.DEFAULT_PROVIDER
        base_map: dict[str, str] = {
            "openai": self.OPENAI_API_BASE,
            "deepseek": self.DEEPSEEK_API_BASE,
            "openai_compat": self.API_BASE,
        }
        return base_map.get(prov, self.API_BASE)

    # -- Diagnostics -------------------------------------------------

    def report(self) -> str:
        """Return a human-readable summary of all loaded settings."""
        lines = [
            "Orka Configuration Report",
            "=" * 50,
            f"  Project root : {self.PROJECT_ROOT}",
            f"  .env file    : {self.PROJECT_ROOT / '.env'}",
            "",
            "  API Keys found:",
        ]
        for var, label in _REQUIRED_VARS:
            val = getattr(self, var, "")
            if val:
                lines.append(f"    ✓ {label} ({var})")
            else:
                lines.append(f"    ✗ {label} ({var}) — not set")
        lines.extend([
            "",
            f"  Default provider : {self.DEFAULT_PROVIDER}",
            f"  Smart model      : {self.smart_model}",
            f"  Fast model       : {self.fast_model}",
            f"  Edit model       : {self.edit_model}",
            f"  Temperature      : {self.TEMPERATURE}",
            f"  Timeout (s)      : {self.TIMEOUT}",
            f"  Max retries      : {self.MAX_RETRIES}",
            f"  Verify SSL       : {self.VERIFY_SSL}",
            f"  Auto-scan        : {self.AUTO_SCAN_AFTER_MUTATION}",
            f"  Dry run          : {self.DRY_RUN}",
            f"  Verbose          : {self.VERBOSE}",
        ])
        return "\n".join(lines)

    # ----------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"Settings(provider={self.DEFAULT_PROVIDER!r}, "
            f"smart={self.smart_model!r}, "
            f"fast={self.fast_model!r}, "
            f"edit={self.edit_model!r})"
        )


# Module-level singleton so consumers can do:  from orka.config import settings
settings = Settings()

