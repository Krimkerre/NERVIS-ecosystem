/* The switch governs, and the card shows what was spent (M25).
 *
 * This is the one feature that spends money without being asked to, so the
 * browser half has two jobs and both fail quietly if broken:
 *
 *   1. **The switch is real.** Off is the default, the card says so, and every
 *      other control is inert while it is off — a trigger you can tick inside a
 *      feature that is off is a control that does nothing, which is worse than
 *      one that is not offered.
 *   2. **The ledger is shown whole**, including the runs that produced nothing.
 *      A card listing only the runs that filed a note answers the cheerful half
 *      of "what has this been doing" and hides the expensive half.
 *
 * And one thing the card must never grow: a way to start a run. Unattended work
 * is unattended; a "run now" button exercises the timer, which is not the thing
 * anybody wants to test.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const posted = [];

const LEDGER = [
  { trigger: "service_health", prompted_by: "RAVIS has stayed unreachable",
    outcome: "noted", model: "claude-haiku-4-5", cost: "1,240 tokens" },
  { trigger: "daily_digest", prompted_by: "41 events since the last digest",
    outcome: "unusable", model: "claude-haiku-4-5", cost: "310 tokens" },
  { trigger: "service_health", prompted_by: "SIRVIS is degraded",
    outcome: "failed", model: "", cost: "" },
];

let state = {
  enabled: false, titles: true, pool: "ravis/auto", interval_minutes: 30, daily_runs: 12,
  ran_today: 3, why_not: "unattended work is switched off",
  triggers: [
    { name: "service_health", about: "a service has been unwell", on: true },
    { name: "daily_digest", about: "what the hub recorded", on: true },
  ],
  runs: LEDGER,
  contention: "ravis/background does not exist yet (RAVIS M26), so whether this competes with chat depends on the pool chosen above",
};

function fetchImpl(url, options = {}) {
  const u = String(url);
  if (u.includes("/api/v1/background")) {
    if ((options.method || "GET").toUpperCase() === "POST") {
      const change = JSON.parse(options.body || "{}");
      posted.push(change);
      state = { ...state, ...("enabled" in change ? { enabled: change.enabled } : {}) };
      return Promise.resolve({ ok: true, status: 200, json: async () => state });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => state });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
}

const { context } = loadPage({ fetchImpl });
vm.runInContext("stopPolling()", context);

async function main() {
  /* ── Off by default, and the card says so ──────────────────────────────── */

  let card = await vm.runInContext("backgroundSection()", context);
  if (!/>off</.test(card)) {
    failures.push("the card does not show that unattended work is off.");
  }
  if (!card.includes("unattended work is switched off")) {
    failures.push(
      "the card does not say why nothing is running. \"It is off\" and \"it hit " +
      "today's ceiling\" are different problems, and a screen that cannot tell " +
      "them apart sends somebody hunting for a bug they caused."
    );
  }

  /* ── Every other control is inert while it is off ──────────────────────── */

  const inert = (card.match(/<input[^>]*>/g) || []).filter(
    tag => !/onchange="setBackground\(\{(enabled|titles|pool)/.test(tag));
  const live = inert.filter(tag => !/disabled/.test(tag));
  if (live.length) {
    failures.push(
      `${live.length} control(s) are usable while the feature is off. A trigger ` +
      "you can tick inside something that is off is a control that does nothing."
    );
  }

  /* ── The pool and titles are live whatever the switch says ────────────── */
  const route = (card.match(/<input[^>]*>/g) || []).filter(
    tag => /setBackground\(\{(titles|pool)/.test(tag));
  if (route.length !== 2 || route.some(tag => /disabled/.test(tag))) {
    failures.push(
      "the pool and the title switch are not both offered and usable while thinking " +
      "is off. They govern titles, handoff folder names and the layout check too, " +
      "none of which that switch turns on."
    );
  }
  if (!/ravis\/private or ravis\/local/.test(card)) {
    failures.push("the card does not say how to keep background work on this machine.");
  }
  posted.length = 0;
  await vm.runInContext("setBackground({titles:false})", context);
  if (!posted.length || posted[0].titles !== false) {
    failures.push("the title switch does not write.");
  }
  /* ── The switch writes ─────────────────────────────────────────────────── */

  posted.length = 0;
  await vm.runInContext("setBackground({enabled:true})", context);
  if (!posted.length || posted[0].enabled !== true) {
    failures.push("the switch does not turn unattended work on.");
  }

  card = await vm.runInContext("backgroundSection()", context);
  if (!/>on</.test(card)) failures.push("the card does not show it is on once enabled.");
  if (/disabled/.test(card)) {
    failures.push("controls are still disabled after the feature was switched on.");
  }

  /* ── The ledger is whole ───────────────────────────────────────────────── */

  for (const [shown, why] of [
    ["noted", "a run that filed a note is not listed."],
    ["unusable", "a run whose reply could not be used is not listed."],
    ["failed", "a failed run is not listed — the ledger is only showing successes."],
    ["1,240 tokens", "a run is listed without what it cost."],
    ["claude-haiku-4-5", "a run is listed without which model ran."],
    ["RAVIS has stayed unreachable", "a run is listed without what prompted it."],
  ]) {
    if (!card.includes(shown)) failures.push(why);
  }

  /* ── The contention gap is stated, not hidden ──────────────────────────── */

  if (!card.includes("ravis/background does not exist yet")) {
    failures.push(
      "the card does not say that the contention guarantee is unmet. The pool " +
      "is the operator's choice until RAVIS M26 exists, and a card that implies " +
      "otherwise is making a promise this build cannot keep."
    );
  }

  /* ── And no way to start one ───────────────────────────────────────────── */

  if (/run now|start a run|think now|>Run</i.test(card)) {
    failures.push(
      "the card offers a way to start a run. Unattended work is unattended; a " +
      "button here exercises the timer, which is not what anybody wants to test."
    );
  }

  /* ── A dark NERVIS is reported, not drawn as off ───────────────────────── */

  const { context: dark } = loadPage({
    fetchImpl: () => Promise.reject(new TypeError("fetch failed")) });
  vm.runInContext("stopPolling()", dark);
  const darkCard = await vm.runInContext("backgroundSection()", dark);
  if (!/not answering/i.test(darkCard)) {
    failures.push(
      "with NERVIS unreachable the card does not say so — an unread setting " +
      "must not render as a setting that is off."
    );
  }

  if (failures.length) {
    console.error("background check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "unattended work holds: off by default and saying why, every other control " +
    "inert until it is on while the pool and title switch stay live, the switch " +
    "writing, the ledger showing failed and " +
    "unusable runs with model and cost, the contention gap stated, no way to " +
    "start a run, and a dark NERVIS reported rather than drawn as off"
  );
}

main().catch(error => { console.error(error); process.exit(1); });
