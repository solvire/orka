"""
Orka feedback collection — logs surgery pipeline outcomes for upgrade insights.

Every ``orka refactor`` and ``orka testgen`` run appends a structured entry to
``.orka/feedback.json``.  The ``orka feedback`` CLI command surfaces patterns
that indicate where orka needs improvement (e.g. repeated fix-loop failures,
edge cases the LLM gets wrong, import issues).

This is NOT telemetry — the data stays local in ``.orka/feedback.json`` and is
never transmitted.  It is purely for self-hardening: orka logs its own
struggles so they can be addressed.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orka.config import settings

logger = logging.getLogger(__name__)

FEEDBACK_DIR = ".orka"
FEEDBACK_FILE = "feedback.json"
MAX_ENTRIES = 500


def _feedback_path() -> Path:
    return settings.PROJECT_ROOT / FEEDBACK_DIR / FEEDBACK_FILE


def load_feedback() -> list[dict[str, Any]]:
    """Load all feedback entries. Returns empty list if none."""
    path = _feedback_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt feedback file at %s, ignoring.", path)
        return []


def record_feedback(
    operation: str,
    method: str,
    file: str,
    success: bool,
    iterations: int,
    gates_passed: int,
    dry_run: bool = False,
    error: str | None = None,
    note: str | None = None,
) -> None:
    """Append a feedback entry after a surgery run.

    Parameters
    ----------
    operation
        ``"refactor"`` or ``"test"``.
    method
        Method/function name that was operated on.
    file
        Source file path.
    success
        Whether all gates ultimately passed.
    iterations
        Number of fix-loop iterations (0 = first try).
    gates_passed
        Highest gate number that passed (1-4).
    dry_run
        Whether this was a dry run.
    error
        Validation error output (truncated) if failed.
    note
        Free-text note for edge cases (e.g. "LLM used pytest.raises without import").
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "method": method,
        "file": file,
        "success": success,
        "iterations": iterations,
        "gates_passed": gates_passed,
        "dry_run": dry_run,
        "error": (error[:300] if error else None),
        "note": note,
    }

    entries = load_feedback()
    entries.append(entry)

    # Cap at MAX_ENTRIES (keep most recent)
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]

    path = _feedback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2, default=str))
    logger.debug("Feedback recorded: %s/%s success=%s iter=%d", operation, method, success, iterations)


def summarize_feedback(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize feedback entries for the ``orka feedback`` command."""
    if not entries:
        return {
            "total_runs": 0,
            "first_try_success": 0,
            "first_try_rate": 0.0,
            "avg_iterations": 0.0,
            "rollbacks": 0,
            "common_failures": {},
            "edge_cases": [],
        }

    total = len(entries)
    first_try = sum(1 for e in entries if e.get("iterations", 0) == 0 and e.get("success"))
    rollbacks = sum(1 for e in entries if not e.get("success"))
    avg_iter = sum(e.get("iterations", 0) for e in entries) / total

    # Common failure patterns — group by error snippet
    failures: dict[str, int] = {}
    for e in entries:
        if not e.get("success") and e.get("error"):
            # Extract the key failure pattern (first line or gate reference)
            err = e["error"]
            key = err.split("\n")[0][:80] if err else "unknown"
            failures[key] = failures.get(key, 0) + 1

    # Sort by frequency
    common_failures = dict(sorted(failures.items(), key=lambda x: -x[1]))

    # Edge cases — entries with notes or multiple iterations
    edge_cases = [e for e in entries if e.get("note") or e.get("iterations", 0) > 0]

    return {
        "total_runs": total,
        "first_try_success": first_try,
        "first_try_rate": first_try / total if total else 0.0,
        "avg_iterations": avg_iter,
        "rollbacks": rollbacks,
        "common_failures": common_failures,
        "edge_cases": edge_cases,
    }
