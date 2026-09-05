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

# **One environment per package, and nothing of its siblings in it.**
#
# The section above installs all four packages into a single environment and
# runs every suite there — so a package that forgot to declare `psutil` passes,
# because a sibling brought it. §3 gave up two repositories for one on the
# argument that "the property that matters is independent buildability", and the
# gate meant to hold that property was the one place the dependency graph was
# guaranteed complete. It has happened: `ravis/pyproject.toml` still carries the
# note about `ecosystem-protocol` being "imported since M0 and never declared".
#
# Imports rather than tests, deliberately. Four suites in four environments take
# four times as long to answer a broader question badly; walking every module
# finds the undeclared dependency at the moment it is missing, which is the
# moment a service fails to start. `ecosystem-protocol` is installed alongside
# because three of them *declare* it — a declared dependency is the opposite of
# the thing being tested.
echo "=== per package: loadable from its own declarations alone ==="
for package in protocol ravis sirvis nervis; do
  alone="$WORK/clean/alone-$package"
  module=$([ "$package" = protocol ] && echo ecosystem_protocol || echo "$package")
  if python3 -m venv "$alone" >/dev/null 2>&1 \
     && "$alone/bin/pip" install -q --upgrade pip >/dev/null 2>&1 \
     && (cd nervis-eco && "$alone/bin/pip" install -q -e ./protocol >/dev/null 2>&1) \
     && (cd nervis-eco && "$alone/bin/pip" install -q -e "./$package" >/dev/null 2>&1); then
    step "$package alone" nervis-eco "$alone/bin/python" tools/import_alone.py "$module"
  else
    printf '  FAIL  %s alone (could not install it by itself)\n' "$package"
    fail=$((fail + 1))
  fi
done

echo "=== repository gates ==="
step "STATUS.md is current"    nervis-eco python tools/check_status.py
step "nothing unreferenced"    nervis-eco python tools/check_dead_code.py
step "build plans readable"   nervis-eco python tools/check_plans.py
# §3 prohibits shared business logic "enforced by an import check in CI rather
# than by the filesystem". There was no such check until 5 September; the rule
# held because nobody had broken it, which is the state that sentence exists to
# prevent.
step "no product imports a peer" nervis-eco python tools/check_imports.py
# The cross-service envelope suite. It was named only in
# `.github/workflows/checks.yml`, a file whose own header says it does not run,
# so the one gate that checks all three services against §4.5 was checked by
# nothing.
step "error envelope conformance" nervis-eco python tools/conformance_check.py
# §15 asks that MEP schemas, fixtures and versions be "released and pinned".
# `/ecosystem/version` published `schema_versions` for years and there were no
# schemas; these are generated from the models, so this fails when the released
# files and the code disagree, and when a service stops satisfying them.
step "released schemas"        nervis-eco python tools/schema_check.py
# §10 names nineteen failure conditions in one sentence and nothing scored them.
# The gate parses that sentence, so a condition added to the runbook and not to
# the matrix fails here rather than being noticed by nobody.
step "degradation matrix"      nervis-eco python tools/check_degradation.py
# §13.4's pairwise gate, against a real SIRVIS. The suite that claimed to be it
# served hand-authored payloads through a mock, so a renamed SIRVIS field left it
# passing while routing stopped seeing evidence.
step "sirvis→ravis pairwise"   nervis-eco python tools/pairwise_check.py
# §15 asks for published release notes and there were none, at any version, for
# any of the five. The gate reads each component's own manifest, so a version
# bumped without a note fails at the moment it is bumped.
step "release notes"           nervis-eco python tools/check_releases.py
step "nervis prototype checks" nervis-eco/nervis python tools/check.py
step "clarvis conformance"     nervis-eco/ravis ravis conformance clarvis

echo "=== dashboard gates (the nineteen that need no live service) ==="
if (cd nervis-eco/nervis && npm ci --no-audit --no-fund >/dev/null 2>&1); then
  echo "  npm ci ok"
else
  echo "  npm ci FAILED"
  fail=$((fail + 1))
fi
# **All eighteen, not the eleven this loop started with.** The other seven —
# background, handler, learned, notification, plan, proposal, supervision — were
# listed in `.github/workflows/checks.yml` and nowhere else, and that file does
# not run: GitHub Actions is switched off on both repositories, deliberately and
# for cost. So the workflow file stopped being a gate the day it was disabled,
# and seven checks quietly became things somebody had to remember to type.
#
# Each was run before being added here and passes with no service up, which is
# what "needs no live service" has to mean for a gate that runs against a fresh
# clone. `honesty_check.js` and `recovery_check.js` stay out of this loop
# because they read live endpoints and are run separately.
for gate in render complexity shaping empty_world liveness injection routing outcome stream \
            preserve attachment background handler learned notification plan proposal supervision \
            capability; do
  step "dashboard $gate" nervis-eco/nervis node "tools/${gate}_check.js"
done

# Chat quotes these files to operators. A name in them that no longer exists is
# a wrong answer delivered confidently, which is worse than no answer.
step "knowledge files" nervis-eco python3 tools/knowledge_check.py

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
