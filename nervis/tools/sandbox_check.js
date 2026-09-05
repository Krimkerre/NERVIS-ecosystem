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
 * --allow-fs-read=*`, filesystem writes and `child_process` withheld.
 *
 * **This is not a test of `page_context.js`.** It spawns its own child
 * process running the identical escape technique standalone, because the
 * property under test — whether an escaped script can act — belongs to the
 * *process*, and asserting it against `loadPage()` itself would only prove
 * this one call site remembered the flag, not that the flag does what it
 * claims. A gate that ran the real exploit against a real gate script would
 * also mean shipping a working exploit as a test fixture in this repository.
 *
 *   node tools/sandbox_check.js
 */

const { execFileSync } = require("node:child_process");
const path = require("node:path");

/* The same technique the scan demonstrated, self-contained: escape a bare
 * `vm` context via `.constructor.constructor`, obtain `process`, and try to
 * run a shell command through it. Written to the marker file's *presence*, not
 * its content, because the property under test is "did anything run at all". */
const MARKER = path.join(__dirname, "..", "..", ".sandbox_check_marker_delete_me");
const POC = `
const vm = require('vm');
const fs = require('fs');
const context = { console };
vm.createContext(context);
try {
  vm.runInContext(
    "(console.log.constructor('return process')()).mainModule.require('child_process')" +
    ".execSync('echo pwned > ${MARKER.replace(/\\/g, "\\\\\\\\")}')",
    context
  );
  console.log('ESCAPED');
} catch (e) {
  console.log('BLOCKED: ' + e.constructor.name);
}
`;

function ranUnder(permissionFlags) {
  const fs = require("node:fs");
  try {
    fs.unlinkSync(MARKER);
  } catch {
    /* did not exist, which is the state every run starts from */
  }
  // **A real file, not `node -e`.** `process.mainModule` — the property the
  // escape needs to reach `require` — is only set when Node runs an actual
  // entry-point file, which is exactly how every gate here is invoked
  // (`node tools/x_check.js`). Running the same string through `-e` leaves
  // `mainModule` undefined and reports a false BLOCKED that has nothing to do
  // with the permission flags — found by this check failing on itself before
  // it ever ran against a real escape.
  const pocPath = path.join(require("node:os").tmpdir(),
    `sandbox_check_poc_${process.pid}_${Math.floor(Math.random() * 1e9)}.js`);
  fs.writeFileSync(pocPath, POC);
  let output;
  try {
    output = execFileSync(
      process.execPath, [...permissionFlags, pocPath], { encoding: "utf8" }
    );
  } finally {
    try {
      fs.unlinkSync(pocPath);
    } catch {
      /* the run may itself be permission-restricted from writing/unlinking
         outside MARKER's own directory; not this check's concern */
    }
  }
  const marker = fs.existsSync(MARKER);
  try {
    fs.unlinkSync(MARKER);
  } catch {
    /* nothing to clean up when the escape was blocked */
  }
  return { output: output.trim(), acted: marker };
}

const failures = [];

/* The gate as it actually runs: no child-process, no fs-write. The escape
 * still reaches `process` — nothing stops that — but must not be able to act
 * on it. */
const guarded = ranUnder(["--permission", "--allow-fs-read=*"]);
if (guarded.acted) {
  failures.push(
    `under --permission --allow-fs-read=*, the marker file was still written ` +
    `(output: ${guarded.output}) — the mitigation does not hold`
  );
}
if (!/^BLOCKED/.test(guarded.output)) {
  failures.push(
    `under --permission --allow-fs-read=*, the escape did not report BLOCKED ` +
    `(output: ${guarded.output}) — check_clean_clone.sh's flags may have changed`
  );
}

/* The same escape, unguarded — proof this check can actually fail, not only
 * pass. Without this half, a change that silently dropped the flags from
 * check_clean_clone.sh would leave this file reporting green forever. */
const unguarded = ranUnder([]);
if (!unguarded.acted) {
  failures.push(
    "the same escape, run with no permission flags at all, did not write the " +
    "marker either — this check cannot tell a guarded run from a broken one"
  );
}

if (failures.length) {
  console.error(`the process-level mitigation for the vm escape does not hold (${failures.length}):\n`);
  for (const f of failures) console.error(`  • ${f}`);
  process.exit(1);
}
console.log(
  "the vm escape reaches `process` under --permission --allow-fs-read=* exactly " +
  "as it does unguarded, and is blocked from acting either way in the guarded run"
);
