#!/usr/bin/env bash
# .kilo/run-script.sh — starts the live feedback loop for the selected Agent
# Manager context.
#
# Orka is a headless CLI tool (no dev server, unlike Django projects). The
# closest equivalent to a "live server" is a pytest watcher that re-runs
# tests on every file change, giving instant feedback during surgery.
#
# Env provided: WORKTREE_PATH (run dir), REPO_PATH (main repo root).
set -euo pipefail
cd "$WORKTREE_PATH"

# Source venv if available.
if [ -f env/bin/activate ]; then
  # shellcheck disable=SC1091
  source env/bin/activate 2>/dev/null || true
elif [ -f "$REPO_PATH/env/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$REPO_PATH/env/bin/activate" 2>/dev/null || true
fi

echo "[run] starting pytest watcher for orka"
echo "[run] worktree: $WORKTREE_PATH"

# Try pytest-watch (ptw) first; fall back to pytest -f; final fallback to a
# single pytest run.
if command -v ptw &>/dev/null; then
  exec ptw orka/tests/ -- -v --tb=short
elif python -m pytest --version &>/dev/null; then
  exec python -m pytest orka/tests/ -f -v --tb=short
else
  echo "[run] WARN  no pytest watcher available; running tests once"
  python -m pytest orka/tests/ -v --tb=short || true
fi
