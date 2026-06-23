"""Tests for orka.core.feedback — surgery pipeline feedback collection."""

import json
import os
from pathlib import Path

import pytest

from orka.core.feedback import (
    record_feedback,
    load_feedback,
    summarize_feedback,
    _feedback_path,
)


@pytest.fixture
def isolated_feedback(tmp_path, monkeypatch):
    """Isolate feedback file to tmp_path."""
    from orka.config import settings
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".orka").mkdir()
    return tmp_path


def test_record_and_load(isolated_feedback):
    record_feedback("refactor", "my_method", "src/app.py", True, 0, 4)
    record_feedback("testgen", "test_func", "app.py", False, 3, 2, error="pytest failed")

    entries = load_feedback()
    assert len(entries) == 2
    assert entries[0]["operation"] == "refactor"
    assert entries[0]["method"] == "my_method"
    assert entries[0]["success"] is True
    assert entries[0]["iterations"] == 0
    assert entries[1]["success"] is False
    assert entries[1]["error"] == "pytest failed"


def test_load_empty(isolated_feedback):
    assert load_feedback() == []


def test_record_with_note(isolated_feedback):
    record_feedback(
        "testgen", "func", "app.py", False, 2, 1,
        note="LLM used pytest.raises without import"
    )
    entries = load_feedback()
    assert entries[0]["note"] == "LLM used pytest.raises without import"


def test_error_truncated(isolated_feedback):
    long_error = "x" * 1000
    record_feedback("refactor", "m", "f.py", False, 1, 0, error=long_error)
    entries = load_feedback()
    assert len(entries[0]["error"]) == 300


def test_summarize_empty(isolated_feedback):
    s = summarize_feedback([])
    assert s["total_runs"] == 0
    assert s["first_try_rate"] == 0.0


def test_summarize_all_success(isolated_feedback):
    for i in range(5):
        record_feedback("refactor", f"method_{i}", "app.py", True, 0, 4)
    entries = load_feedback()
    s = summarize_feedback(entries)
    assert s["total_runs"] == 5
    assert s["first_try_success"] == 5
    assert s["first_try_rate"] == 1.0
    assert s["avg_iterations"] == 0.0
    assert s["rollbacks"] == 0


def test_summarize_mixed(isolated_feedback):
    record_feedback("refactor", "a", "f.py", True, 0, 4)
    record_feedback("refactor", "b", "f.py", True, 1, 4)
    record_feedback("testgen", "c", "f.py", False, 3, 2, error="Gate 4 FAILED: NameError")
    record_feedback("testgen", "d", "f.py", False, 3, 2, error="Gate 4 FAILED: NameError")

    entries = load_feedback()
    s = summarize_feedback(entries)
    assert s["total_runs"] == 4
    assert s["first_try_success"] == 1
    assert s["first_try_rate"] == 0.25
    assert s["avg_iterations"] == 1.75  # (0+1+3+3)/4
    assert s["rollbacks"] == 2
    assert "Gate 4 FAILED: NameError" in s["common_failures"]


def test_max_entries_cap(isolated_feedback):
    for i in range(550):
        record_feedback("refactor", f"m_{i}", "f.py", True, 0, 4)
    entries = load_feedback()
    assert len(entries) == 500  # capped at MAX_ENTRIES
    # Most recent kept
    assert entries[-1]["method"] == "m_549"


def test_dry_run_recorded(isolated_feedback):
    record_feedback("refactor", "m", "f.py", True, 0, 4, dry_run=True)
    entries = load_feedback()
    assert entries[0]["dry_run"] is True


def test_edge_cases_have_notes_or_iterations(isolated_feedback):
    record_feedback("refactor", "a", "f.py", True, 0, 4)  # not edge case
    record_feedback("refactor", "b", "f.py", True, 2, 4)  # iterations > 0
    record_feedback("testgen", "c", "f.py", False, 0, 2, note="weird edge")  # has note

    entries = load_feedback()
    s = summarize_feedback(entries)
    assert len(s["edge_cases"]) == 2  # b and c
