/* An offer's fate is recorded only when somebody answers it (M22).
 *
 * The rule this protects is one sentence: **no outcome is inferred from
 * silence.** A person who closed the tab did not decline, and the tempting
 * implementation — mark it declined when the next message arrives, or when the
 * offer scrolls away, or on a timer — is wrong in a way nothing visibly breaks.
 * The record simply fills up with refusals nobody made, and the offer card
 * starts telling people they have declined things they never saw.
 *
 * So this drives the real offer control and asserts on the requests it makes:
 * three answers produce exactly three records, and every other interaction with
 * an offer produces none. It runs the page's own `runOffer`, `declineOffer` and
 * `chooseCandidate` rather than reading the file for the word "declined",
 * because "the string appears in the source" is precisely the check that passes
 * while the handler sits on a path nothing reaches.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];

/* Every write the page attempts, in order, so the assertions are about what it
   *did* rather than what it looks like it would do. */
const outcomes = [];

function fetchImpl(url, options = {}) {
  const u = String(url);
  const method = (options.method || "GET").toUpperCase();
  if (u.includes("/api/v1/proposals/outcome") && method === "POST") {
    outcomes.push(JSON.parse(options.body || "{}"));
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ recorded: true }) });
  }
  if (u.includes("/api/v1/commands/run")) {
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ job: { id: "j1", state: "queued" } }) });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
}

const { context } = loadPage({ fetchImpl });
vm.runInContext("stopPolling()", context);

/* An offer as NERVIS really sends one: an id minted at the moment it was made,
   which is what lets an answer be filed and an unanswered one leave nothing. */
const OFFER = {
  proposal_id: "pr_abc123", operation: "sirvis.benchmark.submit",
  service: "sirvis", target: "qwen3-4b", summary: "benchmark qwen3-4b",
  ready: true, action: "Run", candidates: [], history: null,
};

function seed(offer) {
  vm.runInContext(
    `CHAT_SESSION.messages = [{role:'assistant',text:'ok',offer:${JSON.stringify(offer)}}]`,
    context,
  );
}

async function main() {
  /* ── The offer draws both answers ──────────────────────────────────────── */

  seed(OFFER);
  const card = vm.runInContext("offerRow(CHAT_SESSION.messages[0].offer, 0)", context);
  if (!/runOffer\(0\)/.test(card)) {
    failures.push("the offer draws no Run control.");
  }
  if (!/declineOffer\(0\)/.test(card)) {
    failures.push(
      "the offer draws no decline control. Then the only outcome that can ever " +
      "be recorded is agreement, and M22's record becomes a preference NERVIS " +
      "learned about itself."
    );
  }

  /* ── Answering records exactly one outcome, of the right kind ──────────── */

  for (const [label, drive, want] of [
    ["accept", "runOffer(0)", "accepted"],
    ["decline", "declineOffer(0)", "declined"],
    ["edit", "chooseCandidate(0,'granite-4-micro')", "edited"],
  ]) {
    outcomes.length = 0;
    seed(label === "edit" ? { ...OFFER, ready: false, candidates: ["granite-4-micro"] } : OFFER);
    await vm.runInContext(drive, context);
    if (outcomes.length !== 1) {
      failures.push(`${label} recorded ${outcomes.length} outcome(s); exactly one decision was made.`);
      continue;
    }
    const [filed] = outcomes;
    if (filed.outcome !== want) {
      failures.push(`${label} filed ${JSON.stringify(filed.outcome)} rather than ${JSON.stringify(want)}.`);
    }
    if (filed.proposal_id !== OFFER.proposal_id) {
      failures.push(`${label} filed against ${JSON.stringify(filed.proposal_id)}, not the offer's own id.`);
    }
    if (want === "edited" && filed.edited_to !== "granite-4-micro") {
      failures.push(
        "an edit was recorded without what it was changed to — which keeps the " +
        "fact that somebody changed their mind and throws away what they wanted."
      );
    }
  }

  /* ── Silence records nothing ───────────────────────────────────────────── */

  /* Everything short of answering: drawing the card, sending another message,
     repainting, and abandoning the offer entirely. None of it is a decision. */
  outcomes.length = 0;
  seed(OFFER);
  vm.runInContext("offerRow(CHAT_SESSION.messages[0].offer, 0)", context);
  await vm.runInContext("render()", context);
  vm.runInContext("CHAT_SESSION.messages = []", context);
  await vm.runInContext("render()", context);
  if (outcomes.length) {
    failures.push(
      `${outcomes.length} outcome(s) were recorded without anybody answering ` +
      `(${outcomes.map(o => o.outcome).join(", ")}). M22 is explicit: a person ` +
      "who closed the tab did not decline."
    );
  }

  /* ── An answered offer cannot be answered twice ────────────────────────── */

  outcomes.length = 0;
  seed(OFFER);
  await vm.runInContext("declineOffer(0)", context);
  await vm.runInContext("declineOffer(0)", context);
  await vm.runInContext("runOffer(0)", context);
  if (outcomes.length !== 1) {
    failures.push(
      `one decision produced ${outcomes.length} records. A spent offer must ` +
      "stop answering, or the record says somebody was more certain than they were."
    );
  }

  /* ── What is remembered is shown, not applied ──────────────────────────── */

  seed({ ...OFFER, history: { declined: 2, accepted: 0, edited: 0, sentence: "you have declined this 2 times" } });
  const remembering = vm.runInContext("offerRow(CHAT_SESSION.messages[0].offer, 0)", context);
  if (!remembering.includes("you have declined this 2 times")) {
    failures.push(
      "an offer with a history does not show it. M22 requires a preference to " +
      "be visible in the proposal that uses it — one the person cannot see is " +
      "one they cannot argue with."
    );
  }
  if (!/runOffer\(0\)/.test(remembering)) {
    failures.push(
      "an offer that has been declined before is no longer offered. What is " +
      "remembered may be said; it may not quietly change what NERVIS proposes."
    );
  }

  if (failures.length) {
    console.error("proposal outcome check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "proposal outcomes hold: accept, decline and edit each record once and " +
    "correctly, an edit names what it was changed to, silence records nothing, " +
    "a spent offer stops answering, and a remembered history is shown without " +
    "changing the offer"
  );
}

main().catch(error => { console.error(error); process.exit(1); });
