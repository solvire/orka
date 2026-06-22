#!/usr/bin/env bash
# .kilo/setup-script.sh — runs once when an Agent Manager worktree is created.
# Env provided: WORKTREE_PATH (absolute path to the new worktree),
#                REPO_PATH   (absolute path to the main repository root).
#
# Design: defensive + verbose. Never exit early — log every step's outcome so
# the agent (and the user) can see exactly what bootstrapped and what didn't.
#
# Adapted from the kidecon setup-script for orka (venv is .venv/, not env/;
# orka is a CLI tool, no Django server to health-check).

echo "[setup] ============================================"
echo "[setup] bootstrapping orka worktree"
echo "[setup]   WORKTREE_PATH = $WORKTREE_PATH"
echo "[setup]   REPO_PATH     = $REPO_PATH"
echo "[setup] ============================================"

# Guard: bail cleanly if the required env vars are missing.
if [ -z "${WORKTREE_PATH:-}" ] || [ -z "${REPO_PATH:-}" ]; then
  echo "[setup] FATAL: WORKTREE_PATH and REPO_PATH must both be set."
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. Venv — symlink the main repo's venv (instant; avoids a pip install of
#    heavy deps like libcst/langchain/chromadb).
#
# SAFETY: never let `ln -s` target itself. Two guards:
#   (a) If WORKTREE_PATH == REPO_PATH, skip (local session; venv already there).
#   (b) Check `-d` (real directory) not `-e` (exists, follows symlinks).
# ---------------------------------------------------------------------------
if [ "$WORKTREE_PATH" = "$REPO_PATH" ]; then
  echo "[setup] OK  WORKTREE_PATH == REPO_PATH (local session); venv already in place, skipping symlink"
elif [ -d "$REPO_PATH/.venv" ]; then
  if [ ! -e "$WORKTREE_PATH/.venv" ] && [ ! -L "$WORKTREE_PATH/.venv" ]; then
    ln -s "$REPO_PATH/.venv" "$WORKTREE_PATH/.venv" && echo "[setup] OK  venv symlinked -> $REPO_PATH/.venv" \
                                               || echo "[setup] WARN  venv symlink failed"
  else
    echo "[setup] OK  venv already present at $WORKTREE_PATH/.venv"
  fi
else
  echo "[setup] WARN  no venv at $REPO_PATH/.venv — agent must create one"
fi

# ---------------------------------------------------------------------------
# 2. Sync untracked Kilo config into the worktree.
#    CRITICAL: `git worktree add` checks out from the last COMMIT, so it only
#    brings committed files. Anything uncommitted (kilo.jsonc, agents, commands,
#    setup/run scripts, AGENTS.md, plans) is MISSING from the worktree until
#    we copy it. This prevents model drift to stale committed values.
# ---------------------------------------------------------------------------
copy_ok=0
copy_fail=0

if [ "$WORKTREE_PATH" = "$REPO_PATH" ]; then
  echo "[setup] OK  local session — config already in place, skipping copy loop"
else
  mkdir -p "$WORKTREE_PATH/.kilo/agent" "$WORKTREE_PATH/.kilo/command" "$WORKTREE_PATH/.kilo/plans"

  for f in \
    ".kilo/kilo.jsonc" \
    ".kilo/agent/surgeon.md" \
    ".kilo/agent/locator.md" \
    ".kilo/agent/reviewer-architecture.md" \
    ".kilo/agent/reviewer-safety.md" \
    ".kilo/command/review.md" \
    ".kilo/setup-script.sh" \
    ".kilo/run-script.sh" \
    "AGENTS.md"
  do
    src="$REPO_PATH/$f"
    if [ -f "$src" ]; then
      cp "$src" "$WORKTREE_PATH/$f" \
        && { echo "[setup] OK  copied $f"; copy_ok=$((copy_ok+1)); } \
        || { echo "[setup] FAIL  copy $f"; copy_fail=$((copy_fail+1)); }
    else
      echo "[setup] SKIP  $f not found in main repo"
    fi
  done

  echo "[setup] config sync: $copy_ok copied, $copy_fail failed"
fi

# ---------------------------------------------------------------------------
# 3. Verify the model is the intended one (catch drift before the agent runs).
# ---------------------------------------------------------------------------
if [ -f "$WORKTREE_PATH/.kilo/kilo.jsonc" ]; then
  model=$(grep '"model"' "$WORKTREE_PATH/.kilo/kilo.jsonc" | head -1 | sed 's/.*: *"\([^"]*\)".*/\1/')
  echo "[setup] worktree model = $model"
fi

# ---------------------------------------------------------------------------
# 4. Verify orka boots (best-effort, non-blocking).
#    orka is a CLI tool — no dev server. Check importability + doctor.
# ---------------------------------------------------------------------------
echo "[setup] verifying orka boots (best-effort, non-blocking)..."
cd "$WORKTREE_PATH" || { echo "[setup] WARN  cannot cd to worktree"; exit 0; }

# Source venv if available; fall back to REPO_PATH/.venv directly.
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate 2>/dev/null || true
elif [ -f "$REPO_PATH/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$REPO_PATH/.venv/bin/activate" 2>/dev/null || true
fi

python -c "import orka; print('[setup] orka importable')" 2>&1 | tail -1 || echo "[setup] WARN  orka import failed — agent will resolve."

# Quick test collection check (non-blocking)
python -m pytest --collect-only -q 2>&1 | tail -3 || echo "[setup] WARN  pytest collection failed — agent will resolve."

echo "[setup] ============================================"
echo "[setup] done. Worktree is ready for the agent session."
echo "[setup] ============================================"
