# Checkpoint: Surgery Graph Operational

## What was fixed

### 1. Client model resolution (`orka/clients.py`)
- `_resolve_model()` was using `settings.smart_model` which resolved against `DEFAULT_PROVIDER`, not the requested provider
- Calling `create("together_ai")` with `DEFAULT_PROVIDER=deepseek` would send `deepseek-v4-pro` to Together's API → 404
- **Fix:** `_resolve_model()` now resolves per-provider: `DEEPSEEK_MODEL` → `ORKA_SMART_MODEL` → `DEFAULT_MODELS[provider]`
- Tier env vars (`ORKA_SMART_MODEL`) only apply when `provider == settings.DEFAULT_PROVIDER`
- Also fixed `verify_ssl` → `model_kwargs` for ChatOpenAI compatibility

### 2. Generator: single function per response
- System instruction changed from "raw Python test functions" (plural) to "a single raw Python test function"
- Prevents LLM from dumping 14 test cases at once

### 3. Debug logging removed
- All `_debug()` calls stripped from generator.py and validator.py

## Working flow
```
run_surgery(source_file, method_name, requirements, provider='deepseek')
  → gather_context   (extracts method, ChromaDB similar examples, file backup)
  → generate_draft   (compiled prompt → LLM → single test function)
  → validate_draft   (Gate 1: snippet AST → Gate 2: assembly → Gate 3: file AST → Gate 4: pytest)
  → fix_draft (if failed, max_iterations loop)
```

## Test results
- Generator produces valid single-test snippets consistently with DeepSeek
- Full pipeline passes (dry_run and real pytest execution)
