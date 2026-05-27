# Checkpoint: Settings & Client Factory Overhaul

## Changes Made

### `orka/config.py` — Full settings rewrite
- **Provider selection**: `ORKA_DEFAULT_PROVIDER` (openai, deepseek, together_ai, gemini, anthropic, openai_compat)
- **API keys**: Standard names (`OPENAI_API_KEY`, `TOGETHER_API_KEY`, etc.) for cross-tool portability
- **Model tiers**: `ORKA_SMART_MODEL`, `ORKA_FAST_MODEL`, `ORKA_EDIT_MODEL` with 3-level resolution (explicit > provider-specific > built-in default)
- **Connectivity**: `ORKA_TEMPERATURE`, `ORKA_TIMEOUT`, `ORKA_MAX_RETRIES`, `ORKA_VERIFY_SSL`, per-provider API base URLs
- **Behaviour flags**: `ORKA_AUTO_SCAN`, `ORKA_DRY_RUN`, `ORKA_VERBOSE`
- Derived properties: `smart_model`, `fast_model`, `edit_model`, `model`, `get_api_key()`, `get_api_base()`
- Helper functions: `_str_env`, `_bool_env`, `_int_env`, `_float_env` for clean typed reads

### `orka/clients.py` — Factory pattern
- **`OrkaClientFactory.create(provider, model_tier)`** — returns LangChain-compatible object
- **`_TogetherWrapper`** — preserved native Together SDK with temperature/timeout/retries passed through
- **`OrkaLangChainClient`** — kept as backward-compatible high-level wrapper
- All providers: openai, deepseek, together_ai, gemini, anthropic, openai_compat

### `orka/cli.py`
- Updated `--provider` help text to list all supported providers

### `pyproject.toml`
- Added optional deps: `[gemini]` → `langchain-google-genai`, `[anthropic]` → `langchain-anthropic`

### `README.md`
- Rewrote Configuration section with minimal setup, model tier docs, link to example.env

### `docs/ARCHITECTURE.md`
- Added supported providers table, client architecture diagram, updated deps list

### `example.env` (new)
- Annotated reference for every available setting

## Verification
- `pip install -e .` — success
- `orka --help` — all 4 commands display correctly
- All module imports resolve
- `pytest orka/tests/ -v` — **67/68 pass**; the 1 failure (`test_full_orchestrator_pipeline`) needs a real TOGETHER_API_KEY (pre-existing, not a regression)
- Backward compat: `OrkaLangChainClient(provider="deepseek")` raises expected RuntimeError about missing key
