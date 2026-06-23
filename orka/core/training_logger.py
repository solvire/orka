"""
Orka training data logger — captures prompt→completion pairs from real
surgery operations for future fine-tuning.

Two types of training pairs are collected:

1. **Generation pairs** — the compiled prompt → the final successful draft.
   These teach a model how to generate correct code from orka's context-enriched
   prompts. Only logged when the pipeline succeeds (all gates passed).

2. **Fixer pairs** — (failing draft + validation error) → the fixed draft.
   These teach a model how to repair code given a specific error. Logged after
   each fixer iteration, regardless of final outcome.

Data is stored as JSONL in ``.orka/training/`` — one file per type. Each line
is a self-contained JSON object with instruction, input, output, and metadata.

The format is compatible with standard fine-tuning pipelines:

    {"instruction": "...", "input": "...", "output": "...", "metadata": {...}}

Convert to Hugging Face dataset::

    import json
    from datasets import Dataset
    rows = [json.loads(line) for line in open('.orka/training/generations.jsonl')]
    ds = Dataset.from_list(rows)

This is NOT telemetry — data stays local and is never transmitted.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orka.config import settings

logger = logging.getLogger(__name__)

TRAINING_DIR = ".orka"
TRAINING_SUBDIR = "training"
GENERATIONS_FILE = "generations.jsonl"
FIXES_FILE = "fixes.jsonl"


def _training_dir() -> Path:
    return settings.PROJECT_ROOT / settings.TRAINING_DIR


def _is_enabled() -> bool:
    """Check if training data logging is enabled (ORKA_LOG_TRAINING=true)."""
    return getattr(settings, "LOG_TRAINING", False)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON record as a line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _base_metadata(
    operation: str,
    method: str,
    file: str,
    provider: str,
    model: str = "",
    iterations: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build the common metadata block for all training records."""
    return {
        "operation": operation,
        "method": method,
        "file": file,
        "provider": provider,
        "model": model,
        "iterations": iterations,
        "dry_run": dry_run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Generation pairs ────────────────────────────────────────────────────


def log_generation_pair(
    instruction: str,
    output: str,
    operation: str,
    method: str,
    file: str,
    provider: str,
    model: str = "",
    iterations: int = 0,
    dry_run: bool = False,
    input_context: str = "",
) -> None:
    """Log a successful generation pair (compiled prompt → final draft).

    Only call this when the pipeline succeeded (all gates passed). No-op if
    ``ORKA_LOG_TRAINING`` is not set to ``true`` in the environment.

    Parameters
    ----------
    instruction
        The fully compiled prompt sent to the LLM.
    output
        The final draft code that passed all validation gates.
    operation
        ``"refactor"`` or ``"test"``.
    method
        Method/function name operated on.
    file
        Source file path.
    provider
        LLM provider slug.
    model
        Model name used for generation.
    iterations
        Number of fix-loop iterations (0 = first try).
    dry_run
        Whether this was a dry run.
    input_context
        Optional additional context (e.g. dependency signatures) for
        multi-turn fine-tuning. Empty by default.
    """
    if not _is_enabled():
        return

    record = {
        "type": "generation",
        "instruction": instruction,
        "input": input_context,
        "output": output,
        "metadata": _base_metadata(
            operation, method, file, provider, model, iterations, dry_run
        ),
    }
    path = _training_dir() / GENERATIONS_FILE
    _append_jsonl(path, record)
    logger.debug(
        "Training pair logged: %s/%s (generations.jsonl, %d chars)",
        operation, method, len(output),
    )


# ── Fixer pairs ─────────────────────────────────────────────────────────


def log_fixer_pair(
    failing_draft: str,
    validation_error: str,
    fixed_draft: str,
    fixer_prompt: str,
    operation: str,
    method: str,
    file: str,
    provider: str,
    model: str = "",
    iteration: int = 0,
    dry_run: bool = False,
) -> None:
    """Log a fixer correction pair (failing code + error → fixed code).

    Logged after each fixer iteration. Even if the fix didn't ultimately
    succeed, the pair is valuable — it shows the model what correction was
    attempted for a given error.

    Parameters
    ----------
    failing_draft
        The draft code that failed validation.
    validation_error
        The validation error output (truncated).
    fixed_draft
        The fixer's corrected output.
    fixer_prompt
        The full prompt sent to the fixer LLM.
    operation
        ``"refactor"`` or ``"test"``.
    method
        Method/function name.
    file
        Source file path.
    provider
        LLM provider slug.
    model
        Model name.
    iteration
        Fix-loop iteration number (1-indexed).
    dry_run
        Whether this was a dry run.
    """
    if not _is_enabled():
        return

    record = {
        "type": "fix",
        "instruction": f"Fix the following code. Validation error:\n{validation_error}",
        "input": failing_draft,
        "output": fixed_draft,
        "full_prompt": fixer_prompt,
        "metadata": _base_metadata(
            operation, method, file, provider, model, iteration, dry_run
        ),
    }
    path = _training_dir() / FIXES_FILE
    _append_jsonl(path, record)
    logger.debug(
        "Fixer pair logged: %s/%s iter=%d (fixes.jsonl)",
        operation, method, iteration,
    )


# ── Inspection / export ─────────────────────────────────────────────────


def load_training_data(data_type: str = "all") -> list[dict[str, Any]]:
    """Load training records from disk.

    Parameters
    ----------
    data_type
        ``"generations"``, ``"fixes"``, or ``"all"``.

    Returns
    -------
    list[dict]
        Parsed JSONL records.
    """
    records: list[dict[str, Any]] = []
    tdir = _training_dir()

    files = []
    if data_type in ("generations", "all"):
        files.append(tdir / GENERATIONS_FILE)
    if data_type in ("fixes", "all"):
        files.append(tdir / FIXES_FILE)

    for path in files:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Error reading %s: %s", path, e)

    return records


def summarize_training_data(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize training data for the ``orka training`` command."""
    if not records:
        return {
            "total": 0,
            "generations": 0,
            "fixes": 0,
            "by_operation": {},
            "by_provider": {},
            "avg_output_chars": 0,
        }

    gens = [r for r in records if r.get("type") == "generation"]
    fixes = [r for r in records if r.get("type") == "fix"]

    by_op: dict[str, int] = {}
    by_prov: dict[str, int] = {}
    total_output_chars = 0

    for r in records:
        meta = r.get("metadata", {})
        op = meta.get("operation", "unknown")
        prov = meta.get("provider", "unknown")
        by_op[op] = by_op.get(op, 0) + 1
        by_prov[prov] = by_prov.get(prov, 0) + 1
        out = r.get("output", "")
        if out:
            total_output_chars += len(out)

    return {
        "total": len(records),
        "generations": len(gens),
        "fixes": len(fixes),
        "by_operation": by_op,
        "by_provider": by_prov,
        "avg_output_chars": total_output_chars // len(records) if records else 0,
    }


def export_dataset(
    output_path: str,
    data_type: str = "all",
    format: str = "jsonl",
) -> int:
    """Export training data to a file for fine-tuning.

    Parameters
    ----------
    output_path
        Where to write the exported dataset.
    data_type
        ``"generations"``, ``"fixes"`, or ``"all"``.
    format
        ``"jsonl"`` (default) or ``"json"`` (array).

    Returns
    -------
    int
        Number of records exported.
    """
    records = load_training_data(data_type)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if format == "json":
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str))
    else:
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    return len(records)
