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

# **A long work directory fails one step for a reason that has nothing to do
# with the code.** `clarvis test:host` starts a real VS Code, which opens a Unix
# domain socket under `.vscode-test/user-data/`, and a Unix socket path is
# capped at 104 bytes on macOS and 108 on Linux — a limit in the kernel struct,
# not in anything either project controls. Given a work directory a couple of
# directories deep in a temp path, that socket lands past the cap and `listen`
# returns `EINVAL: invalid argument`, which reads exactly like a broken
# extension host. Measured: 166 bytes from a session scratchpad, and it cost an
# investigation before this check existed.
SOCKET_PATH="$WORK/clean/clarvis/.vscode-test/user-data/1.13-main.sock"
if [ "${#SOCKET_PATH}" -gt 100 ]; then
  printf '  NOTE  work directory is deep: %s bytes of socket path, limit ~104\n' "${#SOCKET_PATH}"
  printf '        `clarvis test:host` will fail with EINVAL for that reason alone.\n'
  printf '        Re-run with a short path — tools/check_clean_clone.sh /tmp/ccg — to gate it.\n'
fi

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

# macOS ships no `timeout` binary, and the header above already names the cost
# of a gate with no bound at all: 31 minutes, once, on a socket that never
# opened. Anything that can hang — a download, a process that never exits on
# its own — gets wrapped in this rather than trusted to fail on its own.
run_with_timeout() {  # run_with_timeout <seconds> <command...>
  local seconds="$1"
  shift
  "$@" &
  local pid=$!
  ( sleep "$seconds" && kill -TERM "$pid" ) >/dev/null 2>&1 &
  local watcher=$!
  wait "$pid" 2>/dev/null
  local exit_status=$?
  kill "$watcher" 2>/dev/null
  wait "$watcher" 2>/dev/null
  return "$exit_status"
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
# A Claude Security scan found a committed enrollment secret whose own
# .gitignore pattern had existed since M8a and did nothing, because a pattern
# never untracks a path already committed — only `git rm --cached` does.
step "no gitignored path is tracked" nervis-eco python tools/check_no_tracked_secrets.py
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
# §12 asks for a published compatibility matrix and peer version windows. The
# gate prints the matrix and fails when a peer ships outside the window NERVIS
# declares for it, or when a window is too narrow for §12's rolling upgrade.
step "peer compatibility"      nervis-eco python tools/check_compatibility.py
step "nervis prototype checks" nervis-eco/nervis python tools/check.py
step "clarvis conformance"     nervis-eco/ravis ravis conformance clarvis

echo "=== dashboard gates (the twenty-two that need no live service) ==="
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
# **Every gate runs with Node's own permission model on, not by habit but
# because of what index.html is.** `page_context.js`'s `loadPage()` executes
# index.html's inline `<script>` inside `node:vm` — and index.html is exactly
# what an ordinary pull request edits. Node's `vm` module is not a security
# boundary (its own docs say so), and a Claude Security scan proved it: a
# branch that plants `(console.log.constructor('return process')()).mainModule
# .require('child_process').execSync(...)` in that script gets a real shell the
# moment any one of these twenty gates runs against it — reviewing the branch
# is the trigger, no merge required. Read access stays open (`--allow-fs-read=*`
# — every gate here genuinely reads across the tree) but filesystem *writes*
# and `child_process` stay off, which is what the demonstrated exploit needs
# and what none of these twenty gates uses in their own right (checked: only
# `shaping_check.js --update`, never invoked here, writes anything; nothing in
# this directory shells out to another process). A vm escape under this flag
# still gets a `process` reference — Node's permission model does not and
# cannot change what `vm` itself leaks — but it cannot act on it: no file goes
# unlinked or rewritten, no command runs.
#
# **Network is the gap Node's own model cannot close, and it is a real one.**
# `--permission` has no socket dimension at all — a script that reaches
# `process` under the flags above can still call `fetch()` and quietly read a
# file out over the network, which matters next to a repository that (as of
# this scan) has a committed secret sitting in it. Two real, no-extra-install
# mechanisms close it, one per kernel: `tools/no-network.sb`, a macOS Seatbelt
# profile denying network outright, wrapped with `sandbox-exec`; and on Linux,
# `unshare --net --map-root-user`, which drops the process into a fresh
# network namespace with nothing in it — the flag maps the caller to root
# *inside that new namespace only*, which is what makes creating it possible
# without already being root. **This is also the Windows answer.** Native
# Windows has no equivalent this script can wire in without either an
# administrator-level firewall rule (a system-security change, not something
# this repository's own tooling should be making on somebody's machine) or a
# custom compiled helper — disproportionate for what this is. WSL2 sidesteps
# the question rather than solving it: it runs a real Linux kernel, so
# `uname -s` reports `Linux` and the branch below applies unchanged. WSL1 does
# not count — it translates syscalls rather than running one, and does not
# have real network namespaces. An operator on Windows wanting this guarantee
# runs this under WSL2, not natively; that is a recommendation for the
# operator's runbook, not a thing this script can silently arrange.
#
# Together these are the mitigation the finding's own report calls "isolate
# the whole check process", made concrete without touching a single gate's
# logic; the fuller fix it also names — stop executing index.html at all, in
# favour of static parsing — is the repository owner's call, not a change this
# script makes for them.
case "$(uname -s)" in
  Darwin)
    PROFILE="$(pwd)/nervis-eco/tools/no-network.sb"
    NODE_GUARD=(sandbox-exec -f "$PROFILE" node --permission --allow-fs-read="*")
    ;;
  Linux)
    # Probed rather than assumed: unprivileged user namespaces (what
    # `--map-root-user` needs to unshare the network namespace without real
    # root) are disabled on some hardened or older distributions. `true` costs
    # nothing to run and fails exactly the way the real invocation would if
    # this is one of them.
    if unshare --net --map-root-user -- true >/dev/null 2>&1; then
      NODE_GUARD=(unshare --net --map-root-user -- node --permission --allow-fs-read="*")
    else
      echo "  (Linux, but 'unshare --net --map-root-user' is not usable here — unprivileged" \
           "user namespaces may be disabled; network is not sandboxed for these gates," \
           "fs/child-process still are)"
      NODE_GUARD=(node --permission --allow-fs-read="*")
    fi
    ;;
  *)
    echo "  (neither macOS nor Linux — network is not sandboxed for these gates; fs/child-process still are)"
    NODE_GUARD=(node --permission --allow-fs-read="*")
    ;;
esac
for gate in render complexity shaping empty_world liveness injection picture routing outcome \
            stream preserve attachment background handler learned notification plan proposal \
            supervision capability provenance; do
  step "dashboard $gate" nervis-eco/nervis "${NODE_GUARD[@]}" "tools/${gate}_check.js"
done
# **Held out of the loop above, and for the opposite reason of the two at the
# top.** `sandbox_check.js` proves the mitigation above actually holds — the
# fs/child-process lockdown and, on macOS, the network denial — which means it
# has to spawn its own child processes, network call included, one run
# wrapped exactly as the loop wraps it, one run bare, so it can tell a guarded
# escape from one nothing is stopping. Running it under those same wrappers
# would deny it the very capability its job is to test with.
step "dashboard sandbox" nervis-eco/nervis node "tools/sandbox_check.js"

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

# `npm test` (node's own runner, no VS Code) never ran `bridgeDisabled.spec.ts`
# and the rest of `src/test/*.spec.ts` — those need a real extension host,
# which is exactly what `npm run test:host` (`@vscode/test-cli`) starts, and
# §15 item 1 named this gap by name rather than assuming `npm test` covered it.
#
# **Why this wasn't simply added as another `step` line.** `test:host`
# downloads a real VS Code build the first time it runs, and by default caches
# it inside the project directory itself (`.vscode-test/`) — which is exactly
# nowhere, since this script clones into a fresh directory every run. Without
# sharing that cache, every single invocation would re-download a full VS Code
# build from the network before running four milliseconds of actual test —
# the download is the risk this header already names, not the test.
#
# The fix is a symlink, not a config change to the extension's own tracked
# `.vscode-test.mjs`: `.vscode-test` inside the fresh clone points at one
# persistent, shared cache on this machine, so the download happens once ever
# rather than once per run. `run_with_timeout` bounds the worst case at a
# generous but finite five minutes rather than the unbounded hang this file's
# own header already paid for once.
CLARVIS_VSCODE_CACHE="${CLARVIS_VSCODE_CACHE:-$HOME/.cache/clarvis-vscode-test}"
mkdir -p "$CLARVIS_VSCODE_CACHE"
ln -sfn "$CLARVIS_VSCODE_CACHE" clarvis/.vscode-test
# `test:host` loads the extension the real way — `main` in package.json,
# `dist/extension.js` — not `out/`, which only holds the compiled test files
# `tsc` produces. Nothing else in this section builds that bundle: a
# contributor's own checkout normally already has one from an earlier
# `npm run build`, which a fresh clone never does, and activation failed with
# exactly that missing-module error before this line existed.
step "clarvis build"     clarvis npm run build
step "clarvis test:host" clarvis run_with_timeout 300 npm run test:host

echo
echo "=== $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]
