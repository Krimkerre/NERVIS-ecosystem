/* The notification centre keeps its three promises, exercised rather than read.
 *
 * M21 makes three commitments that are easy to write down and easy to lose in a
 * refactor, because breaking any of them still renders a plausible screen:
 *
 *   1. Every note says *why* it exists. A centre where notes merely appear is a
 *      place people stop looking.
 *   2. A note a model produced names the model and the cost. From M25 that is
 *      the difference between an assistant and a bill nobody can read.
 *   3. Dismissing is per-note. No mark-all-read, no per-kind mute — silencing a
 *      class is how the one that mattered gets missed.
 *
 * And one structural claim underneath them: **the spoken announcement and the
 * note are one event seen twice.** The record is written by NERVIS's probe
 * loop, so muting the voice cannot lose it. The Python half of that is
 * `test_notifications.py`; what this gate holds is the browser half — that the
 * page only ever *reads* the centre, and that a page which cannot reach NERVIS
 * says so instead of drawing an empty, reassuring list.
 *
 * It runs the real `notificationsView` against a stubbed fetch, because "the
 * word appears in the file" is precisely the check that passed while the field
 * sat on a code path nothing went down.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];

/* What the endpoint really returns, shaped as `api/notifications.py` builds it.
 * One ordinary note and one a model wrote, which is the pair the criteria are
 * about. */
const NOTES = [
  {
    note_id: "nt_aaa", kind: "service_state",
    title: "RAVIS has stopped answering",
    body: "connection refused",
    reason: "its state moved from healthy to unreachable",
    severity: "warning", source: "registry", produced_by: null,
    created_at: new Date(Date.now() - 60000).toISOString(),
    read_at: "", dismissed_at: "", unread: true,
  },
  {
    note_id: "nt_bbb", kind: "idea",
    title: "Three benchmarks have no tool trial",
    body: "",
    reason: "noticed while summarising last week's results",
    severity: "info", source: "background",
    produced_by: { model: "claude-haiku-4-5", cost: "$0.0012" },
    created_at: new Date(Date.now() - 7200000).toISOString(),
    read_at: "", dismissed_at: "", unread: true,
  },
];

/* Every request the page makes, recorded, so the last section can assert what
 * it did rather than what it looks like it would do. */
const calls = [];

function fetchImpl(url, options = {}) {
  calls.push({ url: String(url), method: (options.method || "GET").toUpperCase() });
  const u = String(url);
  if (u.includes("/api/v1/notifications")) {
    if ((options.method || "GET").toUpperCase() === "POST") {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ unread: 1 }) });
    }
    return Promise.resolve({
      ok: true, status: 200,
      json: async () => ({ items: NOTES, unread: 2, includes_dismissed: false }),
    });
  }
  return Promise.reject(new TypeError("fetch failed"));
}

const { context } = loadPage({ fetchImpl });
vm.runInContext("stopPolling()", context);

/* ── The screen, rendered from the real endpoint shape ─────────────────── */

let html = "";
try {
  html = vm.runInContext(
    "(async()=>{await notificationsView();return $('#content').innerHTML})()", context
  );
} catch (error) {
  failures.push(`notificationsView() threw before rendering: ${error.message}`);
}

async function main() {
  /* The content *and* the page actions. A "Mark all read" button lives in the
   * heading rather than in the table, so a check that only saw `#content`
   * would have watched the wrong half of the screen — found by falsifying this
   * gate rather than by reading it. */
  const drawn = (html ? await html : "")
    + vm.runInContext("$('#pageActions').innerHTML", context);

  if (drawn && !drawn.includes("RAVIS has stopped answering")) {
    failures.push(
      "the screen rendered without the note the endpoint returned, so the " +
      "centre is not reading what NERVIS files."
    );
  }

  /* 1. Every note says why it exists. */
  if (drawn && !drawn.includes("its state moved from healthy to unreachable")) {
    failures.push(
      "a note's `reason` is not drawn. M21 requires every note to say why it " +
      "exists; a title alone is the notification centre this milestone was " +
      "written to avoid."
    );
  }

  /* 2. A model-produced note names the model and the cost. */
  for (const shown of ["claude-haiku-4-5", "$0.0012"]) {
    if (drawn && !drawn.includes(shown)) {
      failures.push(
        `a model-produced note is drawn without ${JSON.stringify(shown)}. ` +
        "Output that does not name its author reads exactly like something " +
        "NERVIS observed directly, and those are very different claims."
      );
    }
  }

  /* 3. Dismissal is per-note, and there is no bulk silence. */
  const bulk = /mark all|dismiss all|clear all|mute .*(kind|class|type)/i;
  if (bulk.test(drawn)) {
    failures.push(
      "the screen offers a bulk dismiss or a per-kind mute. M21 forbids both: " +
      "the badge going to zero takes every unseen note with it."
    );
  }
  for (const verb of ["'nt_aaa','read'", "'nt_aaa','dismiss'"]) {
    if (drawn && !drawn.includes(verb)) {
      failures.push(`no per-note control calling actOnNote(${verb}) was drawn.`);
    }
  }

  /* The writes go where they claim to. */
  calls.length = 0;
  await vm.runInContext("actOnNote('nt_aaa','dismiss')", context);
  const posted = calls.find(c => c.method === "POST");
  if (!posted) {
    failures.push("actOnNote() sent no POST, so dismissing a note does nothing.");
  } else if (!posted.url.endsWith("/api/v1/notifications/nt_aaa/dismiss")) {
    failures.push(
      `actOnNote() posted to ${posted.url}, which is not the note's dismiss ` +
      "endpoint."
    );
  }

  /* ── The badge reaches somebody on another screen ─────────────────────── */

  await vm.runInContext("paintNotePip()", context);
  const pip = vm.runInContext("notePip()", context);
  if (!pip || !pip.includes("2")) {
    failures.push(
      "notePip() draws nothing with two notes unread. The badge is the only " +
      "part of the centre visible from Traces or Chat, which is the case it " +
      "exists for."
    );
  }
  if (pip && !pip.includes("Notifications")) {
    failures.push("the unread badge does not navigate to the Notifications screen.");
  }
  vm.runInContext("NOTE_UNREAD=0", context);
  if (vm.runInContext("notePip()", context) !== "") {
    failures.push(
      "notePip() draws with nothing unread. A permanent zero is a control, " +
      "not a signal, and it trains people to ignore the corner."
    );
  }

  /* ── A page that cannot reach NERVIS says so ──────────────────────────── */

  const { context: dark } = loadPage({ fetchImpl: () => Promise.reject(new TypeError("fetch failed")) });
  vm.runInContext("stopPolling()", dark);
  const darkHtml = await vm.runInContext(
    "(async()=>{await notificationsView();return $('#content').innerHTML})()", dark
  );
  if (/nothing waiting|dealt with/i.test(darkHtml)) {
    failures.push(
      "with NERVIS unreachable the screen reports an empty centre. A failed " +
      "read must never render as good news — that is the one direction this " +
      "screen must not fail in."
    );
  }
  if (!/not answering/i.test(darkHtml)) {
    failures.push("with NERVIS unreachable the screen does not say so.");
  }

  /* ── The page never writes a note ─────────────────────────────────────── */

  /* The structural half of "one event seen twice". If the dashboard could file
   * a note, muting the voice — or closing the tab — would lose the record,
   * which is exactly what M21 forbids. Every write must be NERVIS's. */
  const fs = require("node:fs");
  const path = require("node:path");
  const source = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  if (/\/api\/v1\/notifications['"`]?\s*,\s*\{\s*method:\s*['"]POST/.test(source)
      || /notifications['"`]\s*,\s*\{[^}]*method:\s*['"]POST/.test(source)) {
    failures.push(
      "the page POSTs to the notification collection, which means the browser " +
      "can file notes. Then a muted or closed tab loses the record, and the " +
      "announcement and the note stop being one event."
    );
  }

  if (failures.length) {
    console.error("notification check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    `notification centre holds: ${NOTES.length} notes rendered with their ` +
    "reasons, model provenance shown, dismissal per-note, badge visible " +
    "off-screen, and a dark NERVIS reported rather than drawn empty"
  );
}

main().catch(error => { console.error(error); process.exit(1); });
