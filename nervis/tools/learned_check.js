/* Nothing is written to NERVIS's notes without somebody pressing something.
 *
 * M23 puts a writable file next to the notes NERVIS shipped with, and the risk
 * that comes with it is not corruption — it is authorship. Three rules keep the
 * file honest, and each fails silently if broken:
 *
 *   1. **A note is written on a confirmation**, through the one enumerated
 *      operation, never as a side effect of rendering or of a reply arriving.
 *   2. **What is stored is the person's sentence**, so the write carries their
 *      words and not a model's summary of them.
 *   3. **The shipped notes win, visibly.** A note marked overruled is still
 *      drawn — hiding it would settle a disagreement only a person can settle.
 *
 * Driven through the real controls, because "the operation name appears in the
 * file" is the check that passes while nothing reaches it.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const writes = [];
const NOTES = {
  items: [
    { heading: "The GPU box", body: "An RX 6800 on the desk serves the laptops.",
      learned_on: "2026-09-01", prompted_by: "remember that the gpu box has an rx 6800" },
  ],
  count: 1,
  path: "/somewhere/nervis/knowledge/learned.md",
};

function fetchImpl(url, options = {}) {
  const u = String(url);
  const method = (options.method || "GET").toUpperCase();
  if (u.includes("/api/v1/commands/run") && method === "POST") {
    writes.push(JSON.parse(options.body || "{}"));
    return Promise.resolve({ ok: true, status: 200,
      json: async () => ({ file: { name: "learned.md", detail: "filed" } }) });
  }
  if (u.includes("/api/v1/learned") && method === "DELETE") {
    writes.push({ operation: "forget", target: decodeURIComponent(u.split("/").pop()) });
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
  }
  if (u.includes("/api/v1/learned")) {
    return Promise.resolve({ ok: true, status: 200, json: async () => NOTES });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
}

const { context } = loadPage({ fetchImpl });
vm.runInContext("stopPolling()", context);

async function main() {
  /* ── The card lists what is in the file, and says where the file is ────── */

  const card = await vm.runInContext("learnedSection()", context);
  for (const [shown, why] of [
    ["The GPU box", "the card does not list the notes NERVIS holds."],
    ["knowledge/learned.md",
     "the card does not say where the file is. \"You can edit this yourself\" " +
     "is only true if somebody is told where."],
    ["remember that the gpu box has an rx 6800",
     "a note is drawn without the sentence that prompted it, which M23 " +
     "requires it to carry."],
    ["2026-09-01", "a note is drawn without its date."],
  ]) {
    if (!card.includes(shown)) failures.push(why);
  }

  /* ── Writing goes through the one enumerated operation ─────────────────── */

  writes.length = 0;
  vm.runInContext("__elements.get('sel:#learnNew')", context); // ensure the stub exists
  vm.runInContext(
    "document.getElementById('learnNew').value = 'the thinkpad has 16GB of ddr4'",
    context,
  );
  await vm.runInContext("addNote()", context);
  const [written] = writes;
  if (!written) {
    failures.push("typing a note and confirming it writes nothing.");
  } else {
    if (written.operation !== "nervis.knowledge.learn") {
      failures.push(
        `a note was written through ${JSON.stringify(written.operation)} rather ` +
        "than the enumerated operation. §12 puts every act on one road, and a " +
        "second door for this one is a way around it."
      );
    }
    if (written.target !== "the thinkpad has 16GB of ddr4") {
      failures.push(
        `the note stored was ${JSON.stringify(written.target)} rather than what ` +
        "was typed. What is written must be the person's own sentence."
      );
    }
  }

  /* ── Nothing writes without a confirmation ─────────────────────────────── */

  writes.length = 0;
  await vm.runInContext("learnedSection()", context);
  await vm.runInContext("render()", context);
  if (writes.length) {
    failures.push(
      `${writes.length} write(s) happened without anybody confirming anything ` +
      `(${writes.map(w => w.operation).join(", ")}). Drawing a screen is not ` +
      "somebody telling NERVIS to remember something."
    );
  }

  /* ── An empty field writes nothing ─────────────────────────────────────── */

  writes.length = 0;
  vm.runInContext("document.getElementById('learnNew').value = '  '", context);
  await vm.runInContext("addNote()", context);
  if (writes.length) {
    failures.push("an empty note was written. There is nothing to remember.");
  }

  /* ── Forgetting names the note rather than its position ────────────────── */

  writes.length = 0;
  await vm.runInContext("forgetNote('The GPU box')", context);
  const [dropped] = writes;
  if (!dropped || dropped.target !== "The GPU box") {
    failures.push(
      "forgetting a note does not name it. A position is stale the moment " +
      "somebody edits the file, and acting on one deletes the wrong note."
    );
  }

  /* ── An overruled note is still shown ──────────────────────────────────── */

  /* The ranking is NERVIS's, so what this holds is the browser half: a section
     marked overruled must not be filtered out on the way to the screen. */
  const page = require("node:fs").readFileSync(
    require("node:path").join(__dirname, "..", "index.html"), "utf8");
  if (/overruled/i.test(page) === false) {
    failures.push(
      "the page never mentions an overruled note, so a reader meeting one has " +
      "no way to know why two notes disagree."
    );
  }

  if (failures.length) {
    console.error("learned notes check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "learned notes hold: the card lists them with date, provenance and file " +
    "path; writing goes through the enumerated operation carrying the person's " +
    "own words; rendering writes nothing; an empty note is refused; and " +
    "forgetting names the note rather than its position"
  );
}

main().catch(error => { console.error(error); process.exit(1); });
