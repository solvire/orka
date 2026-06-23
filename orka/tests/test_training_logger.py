"""Tests for orka.core.training_logger — training data collection."""

import json
import os
from pathlib import Path

import pytest

from orka.core.training_logger import (
    log_generation_pair,
    log_fixer_pair,
    load_training_data,
    summarize_training_data,
    export_dataset,
)


@pytest.fixture
def isolated_training(tmp_path, monkeypatch):
    """Isolate training dir to tmp_path and enable logging."""
    from orka.config import settings
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "LOG_TRAINING", True)
    monkeypatch.setattr(settings, "TRAINING_DIR", ".orka/training")
    return tmp_path


@pytest.fixture
def disabled_training(tmp_path, monkeypatch):
    """Disable training logging."""
    from orka.config import settings
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "LOG_TRAINING", False)
    return tmp_path


# ── Generation pairs ────────────────────────────────────────────────────


def test_log_generation_writes_jsonl(isolated_training):
    log_generation_pair(
        instruction="Refactor this method...",
        output="def foo(): return True",
        operation="refactor",
        method="foo",
        file="app.py",
        provider="deepseek",
        model="deepseek-coder",
        iterations=0,
    )
    records = load_training_data("generations")
    assert len(records) == 1
    assert records[0]["type"] == "generation"
    assert records[0]["instruction"] == "Refactor this method..."
    assert records[0]["output"] == "def foo(): return True"
    assert records[0]["metadata"]["operation"] == "refactor"
    assert records[0]["metadata"]["provider"] == "deepseek"


def test_log_generation_noop_when_disabled(disabled_training):
    log_generation_pair(
        instruction="test",
        output="code",
        operation="refactor",
        method="foo",
        file="app.py",
        provider="deepseek",
    )
    records = load_training_data("generations")
    assert records == []


def test_log_multiple_generations(isolated_training):
    for i in range(3):
        log_generation_pair(
            instruction=f"prompt {i}",
            output=f"def func_{i}(): pass",
            operation="refactor",
            method=f"func_{i}",
            file="app.py",
            provider="deepseek",
        )
    records = load_training_data("generations")
    assert len(records) == 3
    assert records[2]["output"] == "def func_2(): pass"


# ── Fixer pairs ─────────────────────────────────────────────────────────


def test_log_fixer_writes_jsonl(isolated_training):
    log_fixer_pair(
        failing_draft="def foo(: pass",
        validation_error="SyntaxError: invalid syntax",
        fixed_draft="def foo(): pass",
        fixer_prompt="Fix this code...",
        operation="refactor",
        method="foo",
        file="app.py",
        provider="deepseek",
        model="deepseek-coder",
        iteration=1,
    )
    records = load_training_data("fixes")
    assert len(records) == 1
    assert records[0]["type"] == "fix"
    assert records[0]["input"] == "def foo(: pass"
    assert records[0]["output"] == "def foo(): pass"


def test_log_fixer_noop_when_disabled(disabled_training):
    log_fixer_pair(
        failing_draft="bad",
        validation_error="error",
        fixed_draft="good",
        fixer_prompt="fix",
        operation="refactor",
        method="foo",
        file="app.py",
        provider="deepseek",
    )
    records = load_training_data("fixes")
    assert records == []


# ── Loading and summary ─────────────────────────────────────────────────


def test_load_all(isolated_training):
    log_generation_pair("p1", "o1", "refactor", "m1", "f.py", "deepseek")
    log_fixer_pair("bad", "err", "good", "fix_prompt", "test", "m2", "f.py", "groq")
    records = load_training_data("all")
    assert len(records) == 2


def test_summarize_empty(isolated_training):
    s = summarize_training_data([])
    assert s["total"] == 0
    assert s["generations"] == 0
    assert s["fixes"] == 0


def test_summarize_mixed(isolated_training):
    log_generation_pair("p1", "o1", "refactor", "m1", "f.py", "deepseek")
    log_generation_pair("p2", "o2", "test", "m2", "f.py", "groq")
    log_fixer_pair("bad", "err", "good", "fix", "refactor", "m3", "f.py", "deepseek")

    records = load_training_data("all")
    s = summarize_training_data(records)
    assert s["total"] == 3
    assert s["generations"] == 2
    assert s["fixes"] == 1
    assert s["by_operation"]["refactor"] == 2
    assert s["by_operation"]["test"] == 1
    assert s["by_provider"]["deepseek"] == 2
    assert s["by_provider"]["groq"] == 1


# ── Export ──────────────────────────────────────────────────────────────


def test_export_jsonl(isolated_training, tmp_path):
    log_generation_pair("p1", "o1", "refactor", "m1", "f.py", "deepseek")
    log_fixer_pair("bad", "err", "good", "fix", "test", "m2", "f.py", "groq")

    out = tmp_path / "export.jsonl"
    count = export_dataset(str(out), data_type="all", format="jsonl")
    assert count == 2
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2
    json.loads(lines[0])  # valid JSON
    json.loads(lines[1])


def test_export_json_array(isolated_training, tmp_path):
    log_generation_pair("p1", "o1", "refactor", "m1", "f.py", "deepseek")

    out = tmp_path / "export.json"
    count = export_dataset(str(out), data_type="generations", format="json")
    assert count == 1
    data = json.loads(out.read_text())
    assert isinstance(data, list)
    assert len(data) == 1


def test_export_creates_parent_dirs(isolated_training, tmp_path):
    log_generation_pair("p1", "o1", "refactor", "m1", "f.py", "deepseek")
    out = tmp_path / "subdir" / "nested" / "export.jsonl"
    count = export_dataset(str(out))
    assert count == 1
    assert out.exists()
