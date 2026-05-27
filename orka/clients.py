"""
Orka LLM client factory.

Returns a LangChain-compatible object for any supported provider. Every
returned object obeys ``.invoke(messages) -> AIMessage`` so callers never
need to know which SDK is underneath.

Supported providers
-------------------
- ``openai``          — ChatOpenAI
- ``deepseek``        — ChatOpenAI (OpenAI-compatible endpoint)
- ``together_ai``     — Together SDK (wrapped to LangChain interface)
- ``gemini``          — ChatGoogleGenerativeAI
- ``anthropic``       — ChatAnthropic
- ``openai_compat``   — ChatOpenAI (generic, for OpenRouter, Groq, etc.)
"""

import logging
import re
from typing import Optional, Protocol, runtime_checkable

from orka.config import settings

logger = logging.getLogger("orka.clients")


# ===================================================================
# Interface
# ===================================================================


@runtime_checkable
class LangChainClient(Protocol):
    """Minimal LangChain-compatible protocol — just ``invoke(messages)``."""

    def invoke(self, messages: list) -> object: ...


# ===================================================================
# Together SDK adapter
# ===================================================================


class _TogetherWrapper:
    """
    Adapter that wraps the Together SDK to speak the LangChain ``invoke`` protocol.

    This exists because the Together SDK produces measurably better results
    than the OpenAI-compatible endpoint for Together-hosted models.
    """

    def __init__(self, client: object, model: str, temperature: float, max_retries: int, timeout: int) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_retries = max_retries
        self._timeout = timeout

    def invoke(self, messages: list) -> object:
        """
        Convert LangChain messages → Together dicts → return AIMessage-like object.

        Parameters
        ----------
        messages : list of langchain_core.messages.BaseMessage

        Returns
        -------
        The response ``Choice.message`` from ``together.Together.chat.completions.create``.
        It has ``.content`` which is all ``OrkaClientFactory`` callers consume.
        """
        from langchain_core.messages import AIMessage

        together_messages = []
        for msg in messages:
            role = "system" if getattr(msg, "type", None) == "system" else "user"
            together_messages.append({"role": role, "content": msg.content})

        resp = self._client.chat.completions.create(
            model=self._model,
            messages=together_messages,
            temperature=self._temperature,
        )

        choice = resp.choices[0].message
        return AIMessage(content=choice.content)


# ===================================================================
# Factory
# ===================================================================


class OrkaClientFactory:
    """
    Creates a LangChain-compatible LLM client for any supported provider.

    Usage::

        llm = OrkaClientFactory.create("together_ai", model_tier="smart")
        response = llm.invoke([HumanMessage(content="hello")])
        print(response.content)
    """

    _PROVIDER_REGISTRY: dict[str, str] = {
        "openai": "openai",
        "deepseek": "deepseek",
        "together_ai": "together_ai",
        "gemini": "gemini",
        "anthropic": "anthropic",
        "openai_compat": "openai_compat",
    }

    @classmethod
    def create(
        cls,
        provider: Optional[str] = None,
        model_tier: str = "smart",
    ) -> LangChainClient:
        """
        Create an LLM client for *provider* using the given *model_tier*.

        Parameters
        ----------
        provider : str, optional
            One of the supported providers. Defaults to ``settings.DEFAULT_PROVIDER``.
        model_tier : str
            ``"smart"``, ``"fast"``, or ``"edit"``.  Determines which model name
            is selected from the model-tier hierarchy.

        Returns
        -------
        An object that implements ``invoke(messages)``.

        Raises
        ------
        ValueError
            If the provider is unknown.
        RuntimeError
            If the required API key is missing.
        """
        provider = provider or settings.DEFAULT_PROVIDER
        model_name = cls._resolve_model(provider, model_tier)
        api_key = settings.get_api_key(provider)
        temperature = settings.TEMPERATURE
        max_retries = settings.MAX_RETRIES
        timeout = settings.TIMEOUT
        verify_ssl = settings.VERIFY_SSL

        logger.info(
            "Creating %s client  model=%s  tier=%s",
            provider, model_name, model_tier,
        )

        if provider == "together_ai":
            return cls._create_together(model_name, api_key, temperature, max_retries, timeout)

        if provider in ("deepseek", "openai", "openai_compat"):
            return cls._create_openai_compatible(provider, model_name, api_key, temperature, max_retries, timeout, verify_ssl)

        if provider == "gemini":
            return cls._create_gemini(model_name, api_key, temperature, timeout)

        if provider == "anthropic":
            return cls._create_anthropic(model_name, api_key, temperature, timeout)

        raise ValueError(
            f"Unknown provider {provider!r}. "
            f"Supported: {', '.join(sorted(cls._PROVIDER_REGISTRY))}"
        )

    # -- model resolution --------------------------------------------

    @classmethod
    def _resolve_model(cls, provider: str, tier: str) -> str:
        """Resolve model name for *provider* and *tier*."""
        if tier == "smart":
            return settings.smart_model
        if tier == "fast":
            return settings.fast_model
        if tier == "edit":
            return settings.edit_model
        # Unknown tier — fall back to smart
        logger.warning("Unknown model_tier %r, falling back to smart", tier)
        return settings.smart_model

    # -- provider factories ------------------------------------------

    @classmethod
    def _create_together(
        cls,
        model: str,
        api_key: str,
        temperature: float,
        max_retries: int,
        timeout: int,
    ) -> LangChainClient:
        """Build a Together SDK client wrapped to LangChain interface."""
        if not api_key:
            raise RuntimeError(
                "TOGETHER_API_KEY is not set. "
                "Add it to your .env file or export it in your shell."
            )
        from together import Together

        return _TogetherWrapper(
            client=Together(api_key=api_key),
            model=model,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
        )

    @classmethod
    def _create_openai_compatible(
        cls,
        provider: str,
        model: str,
        api_key: str,
        temperature: float,
        max_retries: int,
        timeout: int,
        verify_ssl: bool,
    ) -> LangChainClient:
        """Build a ChatOpenAI client (native or OpenAI-compatible)."""
        if not api_key:
            key_name = f"{provider.upper()}_API_KEY"
            raise RuntimeError(
                f"{key_name} is not set. "
                "Add it to your .env file or export it in your shell."
            )
        from langchain_openai import ChatOpenAI

        base_url = settings.get_api_base(provider) or None

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
            verify_ssl=verify_ssl,
        )

    @classmethod
    def _create_gemini(
        cls,
        model: str,
        api_key: str,
        temperature: float,
        timeout: int,
    ) -> LangChainClient:
        """Build a Google Generative AI client."""
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. "
                "Add it to your .env file or export it in your shell."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
        )

    @classmethod
    def _create_anthropic(
        cls,
        model: str,
        api_key: str,
        temperature: float,
        timeout: int,
    ) -> LangChainClient:
        """Build an Anthropic client."""
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to your .env file or export it in your shell."
            )
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
        )


# ===================================================================
# Convenience wrapper (backward compatible name)
# ===================================================================


class OrkaLangChainClient:
    """
    High-level wrapper around :class:`OrkaClientFactory` for simple use cases.

    This is the original public API — it still works exactly as before::

        client = OrkaLangChainClient(provider="together_ai")
        code = client.generate_code("def add(a, b): ...")

    But internally it now uses :class:`OrkaClientFactory` so it supports
    all providers and respects the full settings hierarchy.
    """

    def __init__(self, provider: Optional[str] = None, model_tier: str = "smart") -> None:
        self.provider = provider or settings.DEFAULT_PROVIDER
        self.model_tier = model_tier
        self._llm = OrkaClientFactory.create(self.provider, self.model_tier)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_code(
        self, prompt: str, system_instruction: Optional[str] = None
    ) -> str:
        """
        Send a prompt to the LLM and return the raw text response.

        Parameters
        ----------
        prompt : str
            The main user message.
        system_instruction : str, optional
            An optional system-level instruction sent before the prompt.

        Returns
        -------
        str
            The model's output, **without** any markdown-fence stripping.
            Call :meth:`fix_md_fences` separately if needed.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = []
        if system_instruction:
            messages.append(SystemMessage(content=system_instruction))
        messages.append(HumanMessage(content=prompt))

        response = self._llm.invoke(messages)
        return response.content

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def fix_md_fences(text: str) -> str:
        """
        Strip wrapping ```… fences from a model response.

        Handles fences that may be labelled (e.g. `` ```python ``) as well as
        plain `` ``` `` fences.  Also handles the case where the entire
        response is wrapped and the content is on interior lines.
        """
        pattern = r"^```(?:\w+)?\s*\n?(.*?)\n?```\s*$"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()
