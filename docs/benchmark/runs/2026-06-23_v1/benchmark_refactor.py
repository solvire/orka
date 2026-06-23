#!/usr/bin/env python
"""Benchmark Orka's surgery pipeline against raw LLM file editing across
multiple models / providers.

For each target method this script runs two approaches:

* **Orka pipeline** -- ``orka refactor --dry-run`` semantics, driven directly
  through :func:`orka.operations.graph.run_surgery` so the compiled prompt
  size, LLM call count, fix-loop iterations and 4-gate results can be read
  from the returned state. The original source file is never written to
  (dry-run); an extra pre/post SHA-256 check proves it was untouched.

* **Raw LLM baseline** -- the full source file plus a plain instruction is
  sent to the same provider/model the Orka pipeline uses. The model's output
  is written to a *temp* file (never the real source), then checked with
  ``ast.parse`` (syntax) and pytest (does the module's existing test file
  still pass with the edit applied). No validation, no fix loop.

Both approaches are judged by the same bar where applicable. Orka's assembled
output (``draft_file_content``) is additionally checked with pytest so the
"overall success" comparison is apples-to-apples.

The benchmark runs across one or more provider *profiles* (model + endpoint +
key) and prints a cross-provider comparison so you can see which model leads.

Usage::

    env/bin/python scripts/benchmarks/benchmark_refactor.py
    env/bin/python scripts/benchmarks/benchmark_refactor.py --profiles zai-glm52,deepseek
    env/bin/python scripts/benchmarks/benchmark_refactor.py --provider deepseek --targets 7
    env/bin/python scripts/benchmarks/benchmark_refactor.py --skip-raw        # orka only

Each run writes results.json + report.md (default: docs/benchmark/latest/).
To archive a run, copy those two files plus a snapshot of this script into
docs/benchmark/runs/<date>/ -- see docs/benchmark/README.md.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from typing import Any

from orka.clients import OrkaClientFactory, OrkaLangChainClient
from orka.config import settings
from orka.operations.graph import run_surgery


TARGETS: list[dict[str, str | None]] = [
    {"file": "orka/core/module_resolver.py", "method": "node_id_to_module",
     "req": "Add type hints to all parameters and return type"},
    {"file": "orka/core/module_resolver.py", "method": "file_to_module",
     "req": "Add input validation for empty strings and log a warning"},
    {"file": "orka/surgery/trivia.py", "method": "collapse_blank_lines",
     "req": "Add a docstring explaining the algorithm"},
    {"file": "orka/surgery/modifier.py", "method": "parse_snippet_to_cst_body",
     "req": "Add better error message including the first line of the snippet"},
    {"file": "orka/core/validator.py", "method": "validate_code_snippet",
     "req": "Add logging of the snippet length before validation"},
    {"file": "orka/core/validator.py", "method": "__bool__",
     "cls": "ValidationResult", "req": "Add a debug log message when the result is checked"},
    {"file": "orka/core/import_injector.py", "method": "dedupe_imports",
     "req": "Add sorting by module name before deduplication"},
]


# ---------------------------------------------------------------------------
# Provider profiles -- each is a (provider slug, model, optional base/key).
# All entries below were verified alive via a 1-token health check.
# ---------------------------------------------------------------------------
PROFILES: dict[str, dict[str, Any]] = {
    "deepseek": {
        "provider": "deepseek", "model": "deepseek-coder",
        "fast_model": "deepseek-v4-flash", "key_env": "DEEPSEEK_API_KEY",
        "api_base": None,
    },
    "zai-glm52": {
        "provider": "openai_compat", "model": "glm-5.2",
        "fast_model": "glm-5.2", "key_env": "ZAI_API_KEY",
        "api_base": "https://api.z.ai/api/paas/v4",
    },
    "together-glm52": {
        "provider": "together_ai", "model": "zai-org/GLM-5.2",
        "fast_model": "zai-org/GLM-5.2", "key_env": "TOGETHER_API_KEY",
        "api_base": None,
    },
    "groq-llama": {
        "provider": "openai_compat", "model": "llama-3.3-70b-versatile",
        "fast_model": "llama-3.3-70b-versatile", "key_env": "GROQ_API_KEY",
        "api_base": "https://api.groq.com/openai/v1",
    },
}

PROJECT_ROOT = str(settings.PROJECT_ROOT)
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "docs", "benchmark", "latest", "results.json")
PYTEST_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _dotted_module(file_rel: str) -> str:
    return file_rel[:-3].replace("/", ".").replace("\\", ".")


def _test_file_for(file_rel: str) -> str | None:
    base = os.path.splitext(os.path.basename(file_rel))[0]
    test_rel = os.path.join("orka", "tests", f"test_{base}.py")
    if os.path.exists(os.path.join(PROJECT_ROOT, test_rel)):
        return test_rel
    return None


def _extract_code(raw: str) -> str:
    if not raw:
        return ""
    text = raw.strip()
    fenced = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        candidates = [c.strip() for c in fenced if c.strip()]
        candidates.sort(key=len, reverse=True)
        for cand in candidates:
            try:
                ast.parse(cand)
                return cand
            except SyntaxError:
                continue
        return candidates[0]
    return OrkaLangChainClient.fix_md_fences(text)


def _syntax_ok(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


# ---------------------------------------------------------------------------
# Isolated pytest via sys.modules shadowing (never touches real source files)
# ---------------------------------------------------------------------------

_RUNNER_TEMPLATE = '''\
import sys, importlib.util, traceback

MOD_NAME = {mod_name!r}
SOURCE_PATH = {source_path!r}
TEST_FILE = {test_file!r}

spec = importlib.util.spec_from_file_location(MOD_NAME, SOURCE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[MOD_NAME] = mod
try:
    spec.loader.exec_module(mod)
except Exception:
    print("BENCH_PYTEST_EXIT=2")
    print("BENCH_SHADOW_ERROR=" + (traceback.format_exc().splitlines()[-1] if traceback.format_exc() else "exec error"))
    sys.exit(0)

import pytest
rc = pytest.main([TEST_FILE, "-q", "--no-header", "--tb=line",
                  "-p", "no:cacheprovider", "--exitfirst"])
print("BENCH_PYTEST_EXIT=" + str(int(rc)))
sys.exit(0)
'''


def _run_pytest_shadow(file_rel: str, modified_content: str) -> tuple[bool, str]:
    if not modified_content or not _syntax_ok(modified_content):
        return False, "invalid/empty content"
    test_rel = _test_file_for(file_rel)
    if not test_rel:
        return False, "no test file"
    test_abs = os.path.join(PROJECT_ROOT, test_rel)
    dotted = _dotted_module(file_rel)

    with tempfile.TemporaryDirectory(prefix="orka_bench_") as td:
        shadow_path = os.path.join(td, "shadow_module.py")
        with open(shadow_path, "w", encoding="utf-8") as f:
            f.write(modified_content)
        runner_src = _RUNNER_TEMPLATE.format(
            mod_name=dotted, source_path=shadow_path, test_file=test_abs,
        )
        runner_path = os.path.join(td, "runner.py")
        with open(runner_path, "w", encoding="utf-8") as f:
            f.write(runner_src)

        env = os.environ.copy()
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        try:
            proc = subprocess.run(
                [sys.executable, runner_path],
                capture_output=True, text=True,
                timeout=PYTEST_TIMEOUT, env=env, cwd=PROJECT_ROOT,
            )
        except subprocess.TimeoutExpired:
            return False, f"pytest timed out after {PYTEST_TIMEOUT}s"
        except Exception as e:
            return False, f"subprocess error: {e}"

        out = proc.stdout + "\n" + proc.stderr
        m = re.search(r"BENCH_PYTEST_EXIT=(\d+)", out)
        if not m:
            return False, "no exit marker"
        rc = int(m.group(1))
        passed = rc == 0
        err_match = re.search(r"BENCH_SHADOW_ERROR=(.*)", out)
        detail = "" if passed else (err_match.group(1).strip() if err_match
                                    else f"pytest rc={rc}")
        return passed, detail


# ---------------------------------------------------------------------------
# Logging capture (keeps console clean, preserves diagnostics on failure)
# ---------------------------------------------------------------------------

class _BufferHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


def _setup_logging() -> _BufferHandler:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().setLevel(logging.WARNING)
    orka_logger = logging.getLogger("orka")
    orka_logger.setLevel(logging.INFO)
    orka_logger.propagate = False
    buf = _BufferHandler()
    buf.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    orka_logger.addHandler(buf)
    return buf


# ---------------------------------------------------------------------------
# Provider profile application + health check
# ---------------------------------------------------------------------------

def _apply_profile(name: str) -> str:
    """Mutate the global ``settings`` so OrkaClientFactory uses this profile."""
    p = PROFILES[name]
    settings.DEFAULT_PROVIDER = p["provider"]
    settings.ORKA_SMART_MODEL = p["model"]
    settings.ORKA_FAST_MODEL = p.get("fast_model") or p["model"]
    settings.ORKA_EDIT_MODEL = p["model"]
    settings.API_KEY = ""
    settings.API_BASE = ""
    if p["provider"] == "openai_compat":
        settings.API_KEY = os.getenv(p["key_env"], "")
        settings.API_BASE = p["api_base"] or ""
    return p["model"]


def _probe_profile(name: str) -> tuple[bool, str]:
    """Cheap 1-token health check; returns (alive, detail)."""
    try:
        r = OrkaClientFactory.check_provider_health(PROFILES[name]["provider"])
        return bool(r["alive"]), str(r.get("error") or "")
    except Exception as e:
        return False, f"{e!r}"


# ---------------------------------------------------------------------------
# Orka pipeline measurement
# ---------------------------------------------------------------------------

def _orka_gates_passed(result: dict[str, Any]) -> int:
    out = (result.get("validation_output") or "").lower()
    if result.get("is_valid"):
        return 3  # dry-run stops after gate 3
    if "syntax error in generated code" in out:
        return 0
    if "failed to assemble" in out:
        return 1
    if "syntax error in assembled file" in out:
        return 2
    if result.get("fatal_error"):
        return 0
    return 3


def run_orka(target: dict[str, Any], profile_name: str, max_iterations: int,
             buf: _BufferHandler) -> dict[str, Any]:
    p = PROFILES[profile_name]
    provider = p["provider"]
    model = p["model"]
    file_rel = target["file"]
    method = target["method"]
    cls = target.get("cls")
    req = target["req"]
    abs_file = os.path.join(PROJECT_ROOT, file_rel)
    label = f"{cls}.{method}" if cls else method

    record: dict[str, Any] = {
        "profile": profile_name, "model": model, "provider": provider,
        "target": f"{file_rel}::{label}", "approach": "orka",
        "prompt_chars": 0, "llm_calls": 0, "smart_calls": 0, "fast_calls": 0,
        "iterations": 0, "gates_passed": 0, "success": False,
        "pytest_passes": False, "wall_time_s": 0.0,
        "source_file_unchanged": True, "error": None,
    }

    before_hash = _sha256_file(abs_file)
    calls: list[dict[str, Any]] = []
    orig_generate = OrkaLangChainClient.generate_code

    def counting_generate(self, prompt, system_instruction=None):
        calls.append({"tier": self.model_tier, "prompt_chars": len(prompt or "")})
        return orig_generate(self, prompt, system_instruction)

    OrkaLangChainClient.generate_code = counting_generate
    buf.records.clear()
    t0 = time.perf_counter()
    try:
        result = run_surgery(
            source_file=abs_file,
            method_name=method,
            requirements=req,
            prompt_template_name="refactor",
            class_name=cls,
            dry_run=True,
            provider=provider,
            max_iterations=max_iterations,
        )
    except Exception as e:
        record["error"] = f"run_surgery raised: {e!r}"
        result = {}
    finally:
        OrkaLangChainClient.generate_code = orig_generate
    record["wall_time_s"] = round(time.perf_counter() - t0, 2)

    record["source_file_unchanged"] = (before_hash == _sha256_file(abs_file))
    record["llm_calls"] = len(calls)
    record["smart_calls"] = sum(1 for c in calls if c["tier"] == "smart")
    record["fast_calls"] = sum(1 for c in calls if c["tier"] != "smart")
    record["prompt_chars"] = len(result.get("compiled_prompt", "") or "")
    iter_count = int(result.get("iteration_count", 0) or 0)
    record["iterations"] = max(0, iter_count - 1)
    record["gates_passed"] = _orka_gates_passed(result)
    record["success"] = bool(result.get("is_valid", False))

    if result.get("fatal_error") and not record["error"]:
        record["error"] = str(result["fatal_error"])

    if record["success"]:
        assembled = result.get("draft_file_content", "") or ""
        passed, detail = _run_pytest_shadow(file_rel, assembled)
        record["pytest_passes"] = passed
        if not passed and not record["error"]:
            record["error"] = f"pytest: {detail}"

    if not record["success"] and not record["error"]:
        tail = "; ".join(buf.records[-4:]) if buf.records else "unknown"
        record["error"] = (result.get("validation_output") or tail)[:200]
    return record


# ---------------------------------------------------------------------------
# Raw LLM baseline measurement
# ---------------------------------------------------------------------------

def run_raw(target: dict[str, Any], profile_name: str,
            client: OrkaLangChainClient) -> dict[str, Any]:
    p = PROFILES[profile_name]
    file_rel = target["file"]
    method = target["method"]
    cls = target.get("cls")
    req = target["req"]
    abs_file = os.path.join(PROJECT_ROOT, file_rel)
    label = f"{cls}.{method}" if cls else method

    record: dict[str, Any] = {
        "profile": profile_name, "model": p["model"], "provider": p["provider"],
        "target": f"{file_rel}::{label}", "approach": "raw",
        "prompt_chars": 0, "llm_calls": 1, "iterations": 0,
        "syntax_valid": False, "pytest_passes": False, "success": False,
        "file_broken": True, "wall_time_s": 0.0, "error": None,
    }

    with open(abs_file, "r", encoding="utf-8") as f:
        full_source = f.read()

    target_desc = f"{cls}.{method}" if cls else f"'{method}'"
    prompt = (
        f"Refactor the method {target_desc} in this file. "
        f"Requirement: {req}. "
        f"Return the COMPLETE modified file.\n\n"
        f"```python\n{full_source}\n```"
    )
    record["prompt_chars"] = len(prompt)

    t0 = time.perf_counter()
    try:
        raw = client.generate_code(prompt=prompt)
    except Exception as e:
        record["wall_time_s"] = round(time.perf_counter() - t0, 2)
        record["error"] = f"LLM call failed: {e!r}"
        return record
    record["wall_time_s"] = round(time.perf_counter() - t0, 2)

    code = _extract_code(raw or "")
    record["syntax_valid"] = _syntax_ok(code)
    record["file_broken"] = not record["syntax_valid"]

    if record["syntax_valid"]:
        passed, detail = _run_pytest_shadow(file_rel, code)
        record["pytest_passes"] = passed
        if not passed:
            record["error"] = f"pytest: {detail}"
    else:
        record["error"] = "syntax error in LLM output"

    record["success"] = record["syntax_valid"] and record["pytest_passes"]
    return record


# ---------------------------------------------------------------------------
# Aggregation + presentation
# ---------------------------------------------------------------------------

def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / d)}%" if d else "n/a"


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    orka = [r for r in records if r["approach"] == "orka"]
    raw = [r for r in records if r["approach"] == "raw"]
    no, nr = len(orka), len(raw)

    return {
        "orka_first_try": sum(1 for r in orka if r["iterations"] == 0 and r["success"]),
        "orka_overall": sum(1 for r in orka if r["success"] and r.get("pytest_passes")),
        "orka_valid": sum(1 for r in orka if r["success"]),
        "orka_n": no,
        "orka_avg_iterations": _avg([r["iterations"] for r in orka]),
        "orka_avg_prompt": int(_avg([r["prompt_chars"] for r in orka])),
        "orka_avg_llm_calls": _avg([r["llm_calls"] for r in orka]),
        "orka_avg_wall": _avg([r["wall_time_s"] for r in orka]),
        "raw_overall": sum(1 for r in raw if r["success"]),
        "raw_syntax_ok": sum(1 for r in raw if r.get("syntax_valid")),
        "raw_broken": sum(1 for r in raw if not r.get("syntax_valid") or not r.get("pytest_passes")),
        "raw_n": nr,
        "raw_avg_prompt": int(_avg([r["prompt_chars"] for r in raw])),
        "raw_avg_wall": _avg([r["wall_time_s"] for r in raw]),
    }


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    """Render a plain-ASCII markdown pipe table (renders in any MD viewer)."""
    lines: list[str] = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def _target_short(target: dict[str, Any]) -> str:
    fn = target["file"].split("/")[-1]
    return f"{fn}::{target.get('cls') + '.' if target.get('cls') else ''}{target['method']}"


def _print_per_target(records: list[dict[str, Any]]) -> None:
    headers = ["#", "Target", "Approach", "Prompt", "LLM", "Iter", "Gates",
               "Syntax", "Pytest", "Time(s)", "OK"]
    rows: list[list[Any]] = []
    for i, target in enumerate(TARGETS, 1):
        label = _target_short(target)
        suffix = f"::{'ValidationResult.' if target.get('cls') else ''}{target['method']}" \
            if target.get("cls") else f"::{target['method']}"
        for approach in ("orka", "raw"):
            r = next((x for x in records if x["approach"] == approach
                      and x["target"].endswith(suffix)), None)
            if not r:
                continue
            if approach == "orka":
                syntax = "yes" if r["success"] else "no"
                gates = f"{r['gates_passed']}/3"
                ok = "PASS" if (r["success"] and r["pytest_passes"]) else "FAIL"
            else:
                syntax = "yes" if r["syntax_valid"] else "no"
                gates = "n/a"
                ok = "PASS" if r["success"] else "FAIL"
            rows.append([i, label, approach, r["prompt_chars"],
                         r["llm_calls"], r["iterations"], gates, syntax,
                         "PASS" if r.get("pytest_passes") else "FAIL",
                         f"{r['wall_time_s']:.1f}", ok])
    print(_md_table(headers, rows))


def _print_summary(a: dict[str, Any]) -> None:
    no, nr = a["orka_n"], a["raw_n"]
    rows = [
        ["First-try success rate", _pct(a["orka_first_try"], no), _pct(a["raw_overall"], nr)],
        ["Avg fix iterations", str(a["orka_avg_iterations"]), "N/A"],
        ["Avg prompt size (chars)", str(a["orka_avg_prompt"]), str(a["raw_avg_prompt"])],
        ["Avg LLM calls", str(a["orka_avg_llm_calls"]), "1.0"],
        ["Validation success rate", _pct(a["orka_valid"], no), _pct(a["raw_syntax_ok"], nr)],
        ["Syntax error prevention", "100%", "0%"],
        ["Raw output breakage rate", "-", _pct(a["raw_broken"], nr)],
        ["Avg wall time (s)", str(a["orka_avg_wall"]), str(a["raw_avg_wall"])],
        ["Overall success rate (valid+tests)", _pct(a["orka_overall"], no), _pct(a["raw_overall"], nr)],
    ]
    print(_md_table(["Metric", "Orka", "Raw LLM"], rows))


def _cross_profile_rows(per_profile: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    headers = ["Profile", "Model", "Orka OK", "Raw OK", "Orka 1st",
               "Orka prompt", "Raw prompt", "Orka t(s)", "Raw t(s)", "Orka calls"]
    rows: list[list[Any]] = []
    for pp in per_profile:
        a = pp["aggregate"]
        no, nr = a["orka_n"], a["raw_n"]
        rows.append([
            pp["profile"], pp["model"],
            _pct(a["orka_overall"], no), _pct(a["raw_overall"], nr),
            _pct(a["orka_first_try"], no),
            a["orka_avg_prompt"], a["raw_avg_prompt"],
            a["orka_avg_wall"], a["raw_avg_wall"],
            a["orka_avg_llm_calls"],
        ])
    return headers, rows


def _print_cross_profile(per_profile: list[dict[str, Any]]) -> None:
    headers, rows = _cross_profile_rows(per_profile)
    print(_md_table(headers, rows))


def _mermaid_bar_chart(title: str, y_label: str, categories: list[str],
                       series: list[tuple[str, list[float]]], y_max: float) -> str:
    """Build a mermaid xychart-beta bar chart (plain ASCII, renders in MD)."""
    cats = ", ".join(f'"{c}"' for c in categories)
    lines = [
        "xychart-beta",
        f'    title "{title}"',
        f"    x-axis [{cats}]",
        f'    y-axis "{y_label}" 0 --> {int(y_max) + 1}"',
    ]
    for name, vals in series:
        lines.append(f"    bar [{', '.join(str(int(v)) for v in vals)}]")
    return "\n".join(lines)


def _mermaid_charts(per_profile: list[dict[str, Any]]) -> list[str]:
    charts: list[str] = []
    if not per_profile:
        return charts
    cats = [pp["profile"] for pp in per_profile]

    orka_prompt = [pp["aggregate"]["orka_avg_prompt"] for pp in per_profile]
    raw_prompt = [pp["aggregate"]["raw_avg_prompt"] for pp in per_profile]
    pmax = max(orka_prompt + raw_prompt + [1]) * 1.15
    charts.append(_mermaid_bar_chart(
        "Avg prompt size (chars): Orka vs Raw by provider",
        "chars", cats, [("Orka", orka_prompt), ("Raw", raw_prompt)], pmax))

    orka_t = [pp["aggregate"]["orka_avg_wall"] for pp in per_profile]
    raw_t = [pp["aggregate"]["raw_avg_wall"] for pp in per_profile]
    tmax = max(orka_t + raw_t + [1]) * 1.15
    charts.append(_mermaid_bar_chart(
        "Avg wall time (s): Orka vs Raw by provider",
        "seconds", cats, [("Orka", orka_t), ("Raw", raw_t)], tmax))
    return charts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _resolve_profiles(args) -> list[str]:
    if args.profiles:
        names = [n.strip() for n in args.profiles.split(",") if n.strip()]
    elif args.provider:
        names = [args.provider]
    else:
        names = list(PROFILES)
    resolved: list[str] = []
    for n in names:
        if n in PROFILES:
            resolved.append(n)
        else:
            print(f"[warn] unknown profile/provider {n!r} -- skipping. "
                  f"Known: {', '.join(PROFILES)}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Orka surgery vs raw LLM editing across providers.")
    parser.add_argument("--provider", default=None,
                        help="Single provider/profile (default: run all profiles)")
    parser.add_argument("--profiles", default=None,
                        help="Comma-separated profile names (default: all)")
    parser.add_argument("--targets", type=int, default=len(TARGETS),
                        help="Number of targets to run (default: all)")
    parser.add_argument("--max-iterations", type=int, default=3,
                        help="Orka fix-loop cap (default: 3)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="JSON results path (default: docs/benchmark/latest/results.json)")
    parser.add_argument("--skip-orka", action="store_true", help="Skip the Orka pipeline")
    parser.add_argument("--skip-raw", action="store_true", help="Skip the raw LLM baseline")
    args = parser.parse_args()

    profile_names = _resolve_profiles(args)
    targets = TARGETS[: max(1, args.targets)]
    buf = _setup_logging()

    print("=== Orka Benchmark Study (multi-provider) ===")
    print()
    print(f"Profiles: {', '.join(profile_names)}")
    print(f"Target Methods: {len(targets)}")
    print(f"Run at: {datetime.now().isoformat(timespec='seconds')}")
    print(f"Max Orka fix iterations: {args.max_iterations}")
    print()

    per_profile: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    skipped: list[str] = []

    for pname in profile_names:
        model = _apply_profile(pname)
        alive, detail = _probe_profile(pname)
        print(f"### Profile: {pname}  ({PROFILES[pname]['provider']} / {model})")
        if not alive:
            print(f"  [skip] health check failed: {detail}")
            skipped.append(pname)
            print()
            continue
        print(f"  health check: OK")
        print()

        raw_client: OrkaLangChainClient | None = None
        if not args.skip_raw:
            try:
                raw_client = OrkaLangChainClient(provider=PROFILES[pname]["provider"],
                                                 model_tier="smart")
            except Exception as e:
                print(f"  [warn] could not create raw LLM client: {e!r}")
                raw_client = None

        records: list[dict[str, Any]] = []
        for i, target in enumerate(targets, 1):
            tlabel = _target_short(target)
            print(f"  [{i}/{len(targets)}] {tlabel}")

            if not args.skip_orka:
                rec = run_orka(target, pname, args.max_iterations, buf)
                records.append(rec)
                status = "valid" if rec["success"] else "FAILED"
                print(f"      orka -> {status} | gates {rec['gates_passed']}/3 | "
                      f"llm={rec['llm_calls']} | iter={rec['iterations']} | "
                      f"prompt={rec['prompt_chars']} | "
                      f"pytest={'PASS' if rec['pytest_passes'] else 'FAIL'} | "
                      f"{rec['wall_time_s']}s")
                if rec["error"]:
                    print(f"        err: {rec['error'][:150]}")

            if not args.skip_raw and raw_client is not None:
                rec = run_raw(target, pname, raw_client)
                records.append(rec)
                status = "valid" if rec["syntax_valid"] else "BROKEN"
                print(f"      raw  -> {status} | "
                      f"pytest={'PASS' if rec['pytest_passes'] else 'FAIL'} | "
                      f"prompt={rec['prompt_chars']} | {rec['wall_time_s']}s")
                if rec["error"]:
                    print(f"        err: {rec['error'][:150]}")

        agg = _aggregate(records)
        per_profile.append({"profile": pname, "model": model,
                            "provider": PROFILES[pname]["provider"],
                            "aggregate": agg})
        all_records.extend(records)

        print()
        print(f"  Per-target breakdown ({pname}):")
        _print_per_target(records)
        print()
        print(f"  Orka vs Raw ({pname}):")
        _print_summary(agg)
        print()

    if skipped:
        print(f"Skipped profiles (health check failed): {', '.join(skipped)}")
        print()

    if len(per_profile) > 1:
        print("Cross-provider comparison:")
        _print_cross_profile(per_profile)
        print()

        charts = _mermaid_charts(per_profile)
        for idx, chart in enumerate(charts, 1):
            print(f"```mermaid")
            print(chart)
            print("```")
            print()

    report_path = os.path.join(os.path.dirname(args.output), "report.md")
    _write_report(report_path, per_profile, all_records, args, targets, skipped)

    payload = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "profiles_run": [pp["profile"] for pp in per_profile],
        "profiles_skipped": skipped,
        "max_iterations": args.max_iterations,
        "targets": targets,
        "per_profile": {pp["profile"]: {"model": pp["model"],
                                        "provider": pp["provider"],
                                        "aggregate": pp["aggregate"]}
                        for pp in per_profile},
        "records": all_records,
        "cross_profile": per_profile,
        "metric_definitions": {
            "orka_first_try": "iterations==0 AND is_valid (gates 1-3 passed on first generation)",
            "orka_overall": "is_valid AND orka_pytest_passes (assembled output passes module tests)",
            "raw_overall": "syntax_valid AND pytest_passes (single LLM call, no fix loop)",
            "syntax_error_prevention": "Orka=100% (gates block invalid code before disk); Raw=0% (no gating)",
            "raw_output_breakage_rate": "% raw outputs with syntax errors OR test failures",
        },
    }
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"Results saved to {os.path.relpath(args.output, PROJECT_ROOT)}")
    print(f"Report saved to {os.path.relpath(report_path, PROJECT_ROOT)}")
    return 0


def _write_report(path: str, per_profile: list[dict[str, Any]],
                  all_records: list[dict[str, Any]], args, targets, skipped) -> None:
    parts: list[str] = []
    parts.append("# Orka Benchmark Study (multi-provider)\n")
    parts.append(f"- Run at: {datetime.now().isoformat(timespec='seconds')}")
    parts.append(f"- Profiles: {', '.join(pp['profile'] for pp in per_profile) or 'none'}")
    if skipped:
        parts.append(f"- Skipped (health check failed): {', '.join(skipped)}")
    parts.append(f"- Targets: {len(targets)}")
    parts.append(f"- Max Orka fix iterations: {args.max_iterations}\n")

    if per_profile:
        parts.append("## Cross-provider comparison\n")
        headers, rows = _cross_profile_rows(per_profile)
        parts.append(_md_table(headers, rows))
        parts.append("")
        for chart in _mermaid_charts(per_profile):
            parts.append("```mermaid")
            parts.append(chart)
            parts.append("```\n")

    for pp in per_profile:
        recs = [r for r in all_records if r["profile"] == pp["profile"]]
        parts.append(f"## Profile: {pp['profile']} ({pp['provider']} / {pp['model']})\n")
        parts.append("### Per-target breakdown\n")
        parts.append(_per_target_md(recs))
        parts.append("")
        parts.append("### Orka vs Raw\n")
        parts.append(_summary_md(pp["aggregate"]))
        parts.append("")

    parts.append("## Metric definitions\n")
    parts.append("- **orka_first_try**: iterations==0 AND is_valid (gates 1-3 on first generation)")
    parts.append("- **orka_overall**: is_valid AND orka_pytest_passes (assembled output passes module tests)")
    parts.append("- **raw_overall**: syntax_valid AND pytest_passes (single LLM call, no fix loop)")
    parts.append("- **syntax_error_prevention**: Orka=100% (gates block invalid code before disk); Raw=0% (no gating)")
    parts.append("- **raw_output_breakage_rate**: % raw outputs with syntax errors OR test failures\n")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def _per_target_md(records: list[dict[str, Any]]) -> str:
    headers = ["#", "Target", "Approach", "Prompt", "LLM", "Iter", "Gates",
               "Syntax", "Pytest", "Time(s)", "OK"]
    rows: list[list[Any]] = []
    for i, target in enumerate(TARGETS, 1):
        label = _target_short(target)
        suffix = f"::{'ValidationResult.' if target.get('cls') else ''}{target['method']}" \
            if target.get("cls") else f"::{target['method']}"
        for approach in ("orka", "raw"):
            r = next((x for x in records if x["approach"] == approach
                      and x["target"].endswith(suffix)), None)
            if not r:
                continue
            if approach == "orka":
                syntax = "yes" if r["success"] else "no"
                gates = f"{r['gates_passed']}/3"
                ok = "PASS" if (r["success"] and r["pytest_passes"]) else "FAIL"
            else:
                syntax = "yes" if r["syntax_valid"] else "no"
                gates = "n/a"
                ok = "PASS" if r["success"] else "FAIL"
            rows.append([i, label, approach, r["prompt_chars"],
                         r["llm_calls"], r["iterations"], gates, syntax,
                         "PASS" if r.get("pytest_passes") else "FAIL",
                         f"{r['wall_time_s']:.1f}", ok])
    return _md_table(headers, rows)


def _summary_md(a: dict[str, Any]) -> str:
    no, nr = a["orka_n"], a["raw_n"]
    rows = [
        ["First-try success rate", _pct(a["orka_first_try"], no), _pct(a["raw_overall"], nr)],
        ["Avg fix iterations", str(a["orka_avg_iterations"]), "N/A"],
        ["Avg prompt size (chars)", str(a["orka_avg_prompt"]), str(a["raw_avg_prompt"])],
        ["Avg LLM calls", str(a["orka_avg_llm_calls"]), "1.0"],
        ["Validation success rate", _pct(a["orka_valid"], no), _pct(a["raw_syntax_ok"], nr)],
        ["Syntax error prevention", "100%", "0%"],
        ["Raw output breakage rate", "-", _pct(a["raw_broken"], nr)],
        ["Avg wall time (s)", str(a["orka_avg_wall"]), str(a["raw_avg_wall"])],
        ["Overall success rate (valid+tests)", _pct(a["orka_overall"], no), _pct(a["raw_overall"], nr)],
    ]
    return _md_table(["Metric", "Orka", "Raw LLM"], rows)


if __name__ == "__main__":
    raise SystemExit(main())
