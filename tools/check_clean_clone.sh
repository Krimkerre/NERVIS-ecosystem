#!/bin/bash
# The gates, against a checkout that has only what was committed.
#
# **This is the gate now, not a rehearsal of one.** GitHub Actions is switched
# off on both repositories and is not coming back, so `.github/workflows/` is a
# record of what the gates *are* rather than a thing that runs them. A rule that
# only exists in a document is a preference — that sentence is the workflow
# file's own, and this script is what keeps it from applying to itself.
#
# What it adds over running the suites in place, which is the whole reason it
# exists: a file works locally whether or not anybody committed it, and a
# dependency resolves whether or not anybody declared it. Cloning from the
# remote and building a virtual environment from the packages' own metadata is
# the only way to find out.
#
# Two gates are deliberately absent, and their absence is not an oversight:
# `honesty_check.js` and `recovery_check.js` drive the dashboard against a live
# NERVIS on 127.0.0.1:8790. They belong to a local run against a started
# ecosystem. Wiring them in here would hang on a socket that never opens — which
# it did, for thirty-one minutes, before this comment existed.
#
# Usage: tools/check_clean_clone.sh [workdir]     (default: a temp directory)
set -u

WORK="${1:-$(mktemp -d)}"
NERVIS_REMOTE="https://github.com/Krimkerre/NERVIS-ecosystem.git"
CLARVIS_REMOTE="https://github.com/Krimkerre/clarvis.git"

rm -rf "${WORK:?}/clean"
mkdir -p "$WORK/clean"
cd "$WORK/clean" || exit 1

pass=0
fail=0

step() {  # step <label> <dir> <command...>
  local label="$1" dir="$2"
  shift 2
  local out
  out=$( (cd "$dir" && "$@") 2>&1 )
  if [ $? -eq 0 ]; then
    printf '  PASS  %s\n' "$label"
    pass=$((pass + 1))
  else
    printf '  FAIL  %s\n' "$label"
    printf '%s\n' "$out" | tail -12 | sed 's/^/          /'
    fail=$((fail + 1))
  fi
}

echo "=== cloning ==="
git clone -q "$NERVIS_REMOTE" nervis-eco || exit 1
git clone -q "$CLARVIS_REMOTE" clarvis || exit 1
echo "  nervis-eco @ $(cd nervis-eco && git rev-parse --short HEAD)"
echo "  clarvis    @ $(cd clarvis && git rev-parse --short HEAD)"

echo "=== a virtual environment from the committed declarations alone ==="
python3 -m venv "$WORK/clean/venv" >/dev/null 2>&1
PIP="$WORK/clean/venv/bin/pip"
BIN="$WORK/clean/venv/bin"
"$PIP" install -q --upgrade pip >/dev/null 2>&1
for package in ./protocol "./ravis[dev]" "./sirvis[dev]" "./nervis[dev]"; do
  if (cd nervis-eco && "$PIP" install -q -e "$package" >/dev/null 2>&1); then
    echo "  installed $package"
  else
    echo "  INSTALL FAILED $package"
    fail=$((fail + 1))
  fi
done
export PATH="$BIN:$PATH"

echo "=== per package: lint, types, tests ==="
for package in protocol ravis sirvis nervis; do
  step "$package ruff"   "nervis-eco/$package" ruff check src tests
  step "$package mypy"   "nervis-eco/$package" mypy
  step "$package pytest" "nervis-eco/$package" pytest -q
done

echo "=== repository gates ==="
step "STATUS.md is current"    nervis-eco python tools/check_status.py
step "nothing unreferenced"    nervis-eco python tools/check_dead_code.py
step "nervis prototype checks" nervis-eco/nervis python tools/check.py
step "clarvis conformance"     nervis-eco/ravis ravis conformance clarvis

echo "=== dashboard gates (the ten that need no live service) ==="
if (cd nervis-eco/nervis && npm ci --no-audit --no-fund >/dev/null 2>&1); then
  echo "  npm ci ok"
else
  echo "  npm ci FAILED"
  fail=$((fail + 1))
fi
for gate in render complexity shaping empty_world liveness injection routing outcome stream preserve; do
  step "dashboard $gate" nervis-eco/nervis node "tools/${gate}_check.js"
done

echo "=== clarvis ==="
if (cd clarvis && npm ci --no-audit --no-fund >/dev/null 2>&1); then
  echo "  npm ci ok"
else
  echo "  npm ci FAILED"
  fail=$((fail + 1))
fi
step "clarvis types" clarvis npx tsc --noEmit -p .
step "clarvis lint"  clarvis npm run lint
step "clarvis tests" clarvis npm test

echo
echo "=== $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]
