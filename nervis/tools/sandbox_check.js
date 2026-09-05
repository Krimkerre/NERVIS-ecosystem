/* `loadPage()` executes untrusted content, and this proves the mitigation
 * around that actually holds — not that `node:vm` contains it, which nothing
 * can promise, but that the process running it cannot act once it escapes.
 *
 * **Found by a Claude Security scan, not by this repository.** Every
 * host-realm function `page_context.js` hands its sandbox — the enumerated
 * globals in `makeContext`, and this file's own `element()`/`document`/
 * `history` shim objects just as much, since a plain function defined outside
 * `vm` carries the same real `Function.prototype` regardless of which object
 * holds it — lets a script inside reach `.constructor.constructor('return
 * process')` and obtain the real Node process. There is no fix inside
 * `page_context.js` that closes this without either rewriting `loadPage()`
 * around static parsing instead of execution, or accepting the escape and
 * containing what it can do — which is what every gate's invocation now does:
 * `tools/check_clean_clone.sh` runs each one under `--permission
 * --allow-fs-read=*` (filesystem writes and `child_process` withheld) and, on
 * macOS or Linux, a second layer denying network outright — `sandbox-exec -f
 * tools/no-network.sb` on macOS, `unshare --net --map-root-user` on Linux.
 * Node's own permission model has no socket dimension at all, on either
 * platform.
 *
 * **Windows has neither layer, and WSL2 is the documented way around that**
 * rather than a third mechanism: it runs a real Linux kernel, `uname`/
 * `process.platform` reads `linux` inside it, and the branch below applies
 * unchanged. WSL1 does not — no real network namespaces — and native Windows
 * has no equivalent this file can probe for at all.
 *
 * **Three escapes, because the mitigations close different doors on
 * different platforms.** The file-write PoC is what `--permission` stops,
 * everywhere. The network PoC is what only a second, platform-specific layer
 * stops — checked against whichever one this platform actually has, since a
 * script that reaches `process` still has the write escape *and* the network
 * escape available unless every applicable layer is in place. A repository
 * with a committed secret sitting in it (this scan's own F7) makes the quiet
 * one, reading a file and phoning it out, worth checking for on its own
 * rather than assuming the write mitigation covers it too.
 *
 * **This is not a test of `page_context.js`.** It spawns its own child
 * processes running the identical escape techniques standalone, because the
 * property under test — whether an escaped script can act — belongs to the
 * *process*, and asserting it against `loadPage()` itself would only prove
 * this one call site remembered the flags, not that the flags do what they
 * claim. A gate that ran the real exploit against a real gate script would
 * also mean shipping a working exploit as a test fixture in this repository.
 *
 *   node tools/sandbox_check.js
 */

const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const REPO_ROOT = path.join(__dirname, "..", "..");
const PROFILE = path.join(REPO_ROOT, "tools", "no-network.sb");
const PLATFORM = process.platform; // 'darwin', 'linux', 'win32', ...

/* Probed, never assumed — the same check `check_clean_clone.sh` runs before
 * relying on `unshare`. Unprivileged user namespaces (what `--map-root-user`
 * needs to create a network namespace without real root) are disabled on
 * some hardened or older distributions, and `true` costs nothing to run. */
function unshareNetWorks() {
  try {
    execFileSync("unshare", ["--net", "--map-root-user", "--", "true"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

const NETWORK_GUARD_AVAILABLE = PLATFORM === "darwin" || (PLATFORM === "linux" && unshareNetWorks());

/* The exact wrapper `check_clean_clone.sh` runs every gate under, computed
 * the same way there, platform by platform. */
function guardedCommand() {
  const node = [process.execPath, "--permission", "--allow-fs-read=*"];
  if (PLATFORM === "darwin") return ["sandbox-exec", "-f", PROFILE, ...node];
  if (PLATFORM === "linux" && NETWORK_GUARD_AVAILABLE) {
    return ["unshare", "--net", "--map-root-user", "--", ...node];
  }
  return node;
}

function run(command, scriptPath) {
  const [bin, ...args] = command;
  return execFileSync(bin, [...args, scriptPath], { encoding: "utf8" }).trim();
}

/* A PoC written to a real temp file and run as Node's entry point, never via
 * `-e`. `process.mainModule` — which the file-write escape needs to reach
 * `require('child_process')` — is only set when Node runs an actual
 * entry-point file, the same shape every gate script runs in; found by this
 * check failing on itself when it first tried `-e` and got a false BLOCKED
 * that had nothing to do with either mitigation. */
function withPoc(source, fn) {
  const pocPath = path.join(
    os.tmpdir(), `sandbox_check_poc_${process.pid}_${Math.floor(Math.random() * 1e9)}.js`
  );
  fs.writeFileSync(pocPath, source);
  try {
    return fn(pocPath);
  } finally {
    try {
      fs.unlinkSync(pocPath);
    } catch {
      /* the run may itself be permission-restricted from unlinking outside
         its own marker's directory; not this check's concern */
    }
  }
}

const failures = [];

// ── The file-write escape: what --permission closes, everywhere ────────────

const MARKER = path.join(REPO_ROOT, ".sandbox_check_marker_delete_me");
const WRITE_POC = `
const vm = require('vm');
const context = { console };
vm.createContext(context);
try {
  vm.runInContext(
    "(console.log.constructor('return process')()).mainModule.require('child_process')" +
    ".execSync('echo pwned > ${MARKER.replace(/\\\\/g, "\\\\\\\\")}')",
    context
  );
  console.log('ESCAPED');
} catch (e) {
  console.log('BLOCKED: ' + e.constructor.name);
}
`;

function ranWriteUnder(command) {
  try {
    fs.unlinkSync(MARKER);
  } catch {
    /* did not exist, which is the state every run starts from */
  }
  const output = withPoc(WRITE_POC, (pocPath) => run(command, pocPath));
  const marker = fs.existsSync(MARKER);
  try {
    fs.unlinkSync(MARKER);
  } catch {
    /* nothing to clean up when the escape was blocked */
  }
  return { output, acted: marker };
}

const guardedWrite = ranWriteUnder(guardedCommand());
if (guardedWrite.acted) {
  failures.push(
    `guarded run: the marker file was still written (output: ${guardedWrite.output}) ` +
    "— the fs/child-process mitigation does not hold"
  );
}
if (!/^BLOCKED/.test(guardedWrite.output)) {
  failures.push(
    `guarded run: the escape did not report BLOCKED (output: ${guardedWrite.output}) ` +
    "— check_clean_clone.sh's flags may have changed"
  );
}

// Unguarded, so this check can tell a guarded run from a broken one rather
// than pass by construction — without this half, a change that silently
// dropped the flags from check_clean_clone.sh would report green forever.
const unguardedWrite = ranWriteUnder([process.execPath]);
if (!unguardedWrite.acted) {
  failures.push(
    "unguarded run: the same escape did not write the marker either — this " +
    "check cannot tell a guarded run from a broken one"
  );
}

// ── The network escape: what only a platform-specific second layer closes ──

const NET_POC = `
(async () => {
  try {
    const escapedFetch = console.log.constructor('return fetch')();
    const r = await escapedFetch('http://example.com');
    console.log('ESCAPED-NET status ' + r.status);
  } catch (e) {
    console.log('BLOCKED-NET: ' + e.constructor.name);
  }
})();
`;

function ranNetUnder(command) {
  return { output: withPoc(NET_POC, (pocPath) => run(command, pocPath)) };
}

if (NETWORK_GUARD_AVAILABLE) {
  const guardedNet = ranNetUnder(guardedCommand());
  if (!/^BLOCKED-NET/.test(guardedNet.output)) {
    failures.push(
      `guarded run: the network escape was not blocked (output: ${guardedNet.output}) ` +
      "— the platform's network guard may not be applying, or its behaviour changed"
    );
  }
  const unguardedNet = ranNetUnder([process.execPath]);
  if (!/^ESCAPED-NET/.test(unguardedNet.output)) {
    // Not a failure of the mitigation — the opposite risk, that this half of
    // the check cannot prove anything either way (e.g. no network reachable
    // in this environment at all).
    failures.push(
      `unguarded run: the network escape did not reach the network either ` +
      `(output: ${unguardedNet.output}) — this half of the check cannot tell a ` +
      "guarded run from one with no network path to test against"
    );
  }
} else {
  const why = PLATFORM === "linux"
    ? "'unshare --net --map-root-user' is not usable here (unprivileged user namespaces may be disabled)"
    : PLATFORM === "win32"
      ? "there is no equivalent on native Windows — run under WSL2 (a real Linux kernel, not WSL1) for this guarantee"
      : `no network guard is defined for '${PLATFORM}'`;
  console.log(
    `  (${why}; the deployed gates run with fs/child-process withheld and network ` +
    "open on this platform, which check_clean_clone.sh states rather than checking here)"
  );
}

if (failures.length) {
  console.error(`the process-level mitigation for the vm escape does not hold (${failures.length}):\n`);
  for (const f of failures) console.error(`  • ${f}`);
  process.exit(1);
}
console.log(
  "the vm escape reaches `process` under the guarded run exactly as it does " +
  "unguarded, and is blocked from writing a file" +
  (NETWORK_GUARD_AVAILABLE ? " or reaching the network" : "") + " either way in the guarded run"
);
