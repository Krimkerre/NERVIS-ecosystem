/* `prov()`'s badge map, held to the vocabulary that actually reaches it (§15).
 *
 * **Found live, on 5 September, by a person reading the Evidence screen rather
 * than by anything here.** RAVIS's own rolling observation over real traffic —
 * `OBSERVED_BY_RAVIS`, "a measurement, but not one taken under controlled
 * conditions" — wore the same green `MEASURED` badge as a SIRVIS benchmark run
 * with a fixed prompt and a warm runtime. `PROV_BADGE` and `prov()` were added
 * to fix it, and nothing was added to keep it fixed: eighteen dashboard gates
 * ran against this file and not one of them ever called `prov()` with a value
 * of its own choosing. §15 asks that provenance be distinguished end to end,
 * and the last inch was the one pixel nothing checked.
 *
 * **The vocabulary is read from the code that produces it, not written out
 * here twice.** `ravis/src/ravis/evidence/sirvis.py`'s `EvidenceProvenance` is
 * the full set of names RAVIS's evidence surface can send: `MEASURED_BY_SIRVIS`,
 * `OBSERVED_BY_RAVIS`, `PROVIDER_METADATA`, `ESTIMATED`, `UNKNOWN`. SIRVIS's own
 * `evidence_type` reaches `prov()` too, at call sites that read it straight off
 * a benchmark result rather than through RAVIS's mapping — `MEASURED` and
 * `PARTIALLY_MEASURED` are real inputs for exactly that reason, not extra cases
 * invented for this check. `KNOWN_KINDS` below is a literal table rather than a
 * second import from Python, because a Node gate reading a Python enum is a
 * cross-language coupling for three words that do not change — but each entry
 * cites the line that would have to move it, so the two cannot drift silently.
 *
 * **Closed against the CSS, not just the map.** A kind `prov()` does not
 * recognise falls through to itself unstyled — the function's own comment says
 * so: "an unmapped name reached `data-kind` with no rule to style it, a badge
 * that silently stops looking like a badge." The five `.prov[data-kind="…"]`
 * rules are read out of `index.html`'s own stylesheet, so a badge nothing
 * colours fails this even if the text it shows happens to be right.
 */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const { loadPage } = require("./page_context.js");

const INDEX_HTML = path.join(__dirname, "..", "index.html");
const page = fs.readFileSync(INDEX_HTML, "utf8");

/* Every kind `prov()` can actually be called with, and the badge it must show.
 * Sourced at: `ravis/src/ravis/evidence/sirvis.py`'s `EvidenceProvenance` enum
 * (the first five) and `sirvis/src/sirvis/core/evidence.py`'s `EvidenceKind`
 * (`MEASURED`, `PARTIALLY_MEASURED`, read straight off a benchmark result at
 * `nervis/index.html`'s `e.evidence_type` call sites). */
const KNOWN_KINDS = {
  // RAVIS's own words for where a number came from (§13.3). The two that
  // matter most are named separately below, because they are the pair that
  // was actually indistinguishable.
  MEASURED_BY_SIRVIS: "MEASURED",
  OBSERVED_BY_RAVIS: "OBSERVED",
  PROVIDER_METADATA: "ESTIMATED",
  // Already RAVIS's own vocabulary — nothing to translate.
  ESTIMATED: "ESTIMATED",
  UNKNOWN: "UNKNOWN",
  // SIRVIS's native `EvidenceKind`, reaching `prov()` unmapped at the call
  // sites that read `evidence_type` directly off a benchmark result rather
  // than through RAVIS. Passed through as themselves, deliberately: SIRVIS's
  // own words already read correctly and a second translation would be a
  // second place the pair above could go back to matching.
  MEASURED: "MEASURED",
  PARTIALLY_MEASURED: "PARTIALLY_MEASURED",
};

/* The closed set of badges the page can actually colour, read from its own
 * stylesheet rather than declared here — a badge this check calls correct and
 * the CSS has no rule for is the exact failure `prov()`'s own comment warns
 * about, and hardcoding the set here would let the two drift apart silently. */
function styledBadges() {
  const styled = [...page.matchAll(/\.prov\[data-kind="([^"]+)"\]/g)].map((m) => m[1]);
  if (styled.length < 3) {
    throw new Error(
      `found only ${styled.length} styled data-kind rule(s) in index.html — the ` +
      "regex is broken, not the page");
  }
  return new Set(styled);
}

(() => {
  const failures = [];
  const { context } = loadPage();
  const styled = styledBadges();

  const shown = {};
  for (const kind of Object.keys(KNOWN_KINDS)) {
    const html = vm.runInContext(`prov(${JSON.stringify(kind)})`, context);
    const match = /data-kind="([^"]*)"/.exec(html);
    if (!match) {
      failures.push(`prov(${JSON.stringify(kind)}) produced no data-kind attribute at all: ${html}`);
      continue;
    }
    shown[kind] = match[1];
  }

  for (const [kind, expected] of Object.entries(KNOWN_KINDS)) {
    const got = shown[kind];
    if (got === undefined) continue; // already reported above
    if (got !== expected) {
      failures.push(`prov(${kind}) shows "${got}", expected "${expected}"`);
    }
    if (!styled.has(got)) {
      failures.push(
        `prov(${kind}) shows "${got}", and index.html has no ` +
        `.prov[data-kind="${got}"] rule to colour it — an unstyled badge`);
    }
  }

  // The regression this file exists for, stated as its own assertion rather
  // than left to be implied by two entries in a table matching by coincidence:
  // a controlled SIRVIS benchmark and RAVIS's own rolling observation must
  // never render as the same badge.
  if (shown.MEASURED_BY_SIRVIS !== undefined && shown.OBSERVED_BY_RAVIS !== undefined
      && shown.MEASURED_BY_SIRVIS === shown.OBSERVED_BY_RAVIS) {
    failures.push(
      "MEASURED_BY_SIRVIS and OBSERVED_BY_RAVIS render as the same badge " +
      `("${shown.MEASURED_BY_SIRVIS}") — a benchmark and RAVIS's own rolling ` +
      "observation are indistinguishable again");
  }

  // The map itself, not just its effect: an entry added or renamed without an
  // expectation here should fail loudly rather than pass by falling through
  // to `prov`'s own `|| kind` default, which reads as "unmapped" either way.
  const mapKeys = vm.runInContext("Object.keys(PROV_BADGE).sort()", context);
  const expectedMapKeys = ["MEASURED_BY_SIRVIS", "OBSERVED_BY_RAVIS", "PROVIDER_METADATA"].sort();
  if (JSON.stringify(mapKeys) !== JSON.stringify(expectedMapKeys)) {
    failures.push(
      `PROV_BADGE's keys are ${JSON.stringify(mapKeys)}, expected ` +
      `${JSON.stringify(expectedMapKeys)} — a mapping was added, removed or renamed ` +
      "with nothing here updated to expect it");
  }

  if (failures.length) {
    console.error(`the provenance badge map does not hold (${failures.length}):\n`);
    for (const f of failures) console.error(`  • ${f}`);
    process.exit(1);
  }
  console.log(
    `prov() maps ${Object.keys(KNOWN_KINDS).length} known provenance kinds to ` +
    `${new Set(Object.values(shown)).size} badges, every one styled, and a ` +
    "benchmark never wears RAVIS's own observation badge");
})();
