/* A plan runs in order, stops at a boundary, and halts when a step fails (M24).
 *
 * The confirmation buys an *ordering*, and three things have to be true for
 * that to remain honest once the button is pressed:
 *
 *   1. **In order, one at a time.** Firing the steps together would run as a
 *      set what somebody approved as a sequence, and "then" would have meant
 *      nothing. Every step here is the same enumerated operation it would be
 *      alone — there is no plan endpoint, and there must not be.
 *   2. **A stop lands at a boundary.** What was started finishes; nothing after
 *      it begins. A stop that tore a step in half could not honestly report
 *      what had already happened, which is the other half of the clause.
 *   3. **A failure halts.** Continuing past a failed step runs the rest of a
 *      sequence whose premise is gone.
 *
 * Driven through the real `runPlan`, with the requests recorded in the order
 * they were made — the only place the ordering is actually observable.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
let calls = [];
let failAt = -1;      // index of the step whose request should fail
let onStep = null;    // called as each step's request is made
let holdStep = null;  // a promise a step's request waits on before answering

function fetchImpl(url, options = {}) {
  const u = String(url);
  if (u.includes("/api/v1/commands/run")) {
    const body = JSON.parse(options.body || "{}");
    const n = calls.length;
    calls.push(body);
    if (onStep) onStep(n);
    if (n === failAt) {
      return Promise.resolve({ ok: false, status: 500,
        json: async () => ({ error: { message: "SIRVIS refused it" } }) });
    }
    const answer = { ok: true, status: 200,
      json: async () => ({ job: { id: `j${n}`, state: "queued" } }) };
    // **Held open when the test asks for it**, which is the only way to observe
    // whether a second step starts while the first is still in flight.
    return holdStep ? holdStep.then(() => answer) : Promise.resolve(answer);
  }
  if (u.includes("/api/v1/proposals/outcome")) {
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
}

const { context } = loadPage({ fetchImpl });
vm.runInContext("stopPolling()", context);

const step = (target, n) => ({
  proposal_id: `pr_${n}`, operation: "sirvis.benchmark.submit", service: "sirvis",
  target, summary: `a benchmark of ${target} on SIRVIS`, ready: true,
  action: "Run", candidates: [], history: null,
});

const PLAN = {
  plan_id: "pl_abc", ready: true,
  steps: [step("qwen3-4b", 0), step("granite-4-micro", 1), step("gemma-3-4b", 2)],
};

function seed(plan = PLAN) {
  vm.runInContext(
    `CHAT_SESSION.messages = [{role:'assistant',text:'ok',plan:${JSON.stringify(plan)}}]`,
    context,
  );
  calls = []; failAt = -1; onStep = null; holdStep = null;
}

const plan = () => vm.runInContext("CHAT_SESSION.messages[0].plan", context);

async function main() {
  /* ── The whole plan is drawn before anything runs ──────────────────────── */

  seed();
  const card = vm.runInContext("planRow(CHAT_SESSION.messages[0].plan, 0)", context);
  for (const target of ["qwen3-4b", "granite-4-micro", "gemma-3-4b"]) {
    if (!card.includes(target)) {
      failures.push(`the plan card omits the step for ${target}; the whole sequence must be visible before it is confirmed.`);
    }
  }
  if (!/runPlan\(0\)/.test(card)) failures.push("the plan draws no way to run it.");
  if (!/declinePlan\(0\)/.test(card)) {
    failures.push("the plan draws no decline. Silence is not an answer for a plan any more than for one offer.");
  }
  if (calls.length) failures.push("drawing the plan ran something.");

  /* ── In order, one at a time, through the enumerated operation ─────────── */

  /* **Overlap is measured by holding a request open, not by reading a field.**
     This used to check that `plan().running` was defined as each request went
     out, which says nothing about how many are in flight: the fixture answered
     every call immediately, so a `runPlan` that fired all three at once would
     have set `running` and passed. An external audit pointed at the gap; the
     first request is now held while the assertion looks for a second, which is
     the thing the sentence has always claimed to prove.

     A sequence somebody approved as a sequence must not run as a set — a
     benchmark queue is serial, and three submissions at once is three jobs
     racing for one machine. */
  seed();
  let released = () => {};
  holdStep = new Promise((resolve) => { released = resolve; });
  const running = vm.runInContext("runPlan(0)", context);
  await new Promise((tick) => setTimeout(tick, 0));
  const startedWhileHeld = calls.length;
  released();
  await running;
  if (startedWhileHeld !== 1) {
    failures.push(`${startedWhileHeld} step(s) were in flight while the first `
      + "request was still open. A sequence approved as a sequence must not run "
      + "as a set: the queue is serial and three submissions race for one machine.");
  }

  seed();
  let overlapped = false;
  onStep = () => { if (plan().running === undefined) overlapped = true; };
  await vm.runInContext("runPlan(0)", context);
  const ran = calls.map(c => c.target);
  if (ran.join() !== "qwen3-4b,granite-4-micro,gemma-3-4b") {
    failures.push(`the steps ran as ${JSON.stringify(ran)}, not in the order they were confirmed in.`);
  }
  if (calls.some(c => c.operation !== "sirvis.benchmark.submit")) {
    failures.push("a step ran through something other than its own enumerated operation.");
  }
  if (overlapped) {
    failures.push("a step ran without the plan being marked as running, so the "
      + "card cannot show which step is in flight.");
  }
  if (!plan().finished) failures.push("a plan that ran every step does not report finishing.");

  /* ── A stop lands at the next boundary ─────────────────────────────────── */

  seed();
  onStep = (n) => { if (n === 0) vm.runInContext("stopPlan(0)", context); };
  await vm.runInContext("runPlan(0)", context);
  if (calls.length !== 1) {
    failures.push(`a stop during step 1 let ${calls.length} step(s) run; nothing after the current one may begin.`);
  }
  if (plan().stopped !== 1) {
    failures.push("a stopped plan does not say where it stopped, so it cannot report what had already happened.");
  }
  if (plan().finished) failures.push("a stopped plan reports as finished.");
  const stoppedCard = vm.runInContext("planRow(CHAT_SESSION.messages[0].plan, 0)", context);
  if (!/stopped after step 1 of 3/.test(stoppedCard)) {
    failures.push("the card does not say what had already run when the plan was stopped.");
  }

  /* ── A failure halts the rest ──────────────────────────────────────────── */

  seed();
  failAt = 1;
  await vm.runInContext("runPlan(0)", context);
  if (calls.length !== 2) {
    failures.push(`a failing step 2 let ${calls.length} step(s) run; the rest of a sequence whose premise is gone must not continue.`);
  }
  if (plan().halted !== 2) failures.push("a halted plan does not say which step halted it.");
  const haltedCard = vm.runInContext("planRow(CHAT_SESSION.messages[0].plan, 0)", context);
  if (!/halted at step 2/.test(haltedCard) || !/SIRVIS refused it/.test(haltedCard)) {
    failures.push("the card does not say which step failed or why.");
  }
  if (/stopped after/.test(haltedCard)) {
    failures.push("a halt is reported as a stop. Somebody stopping a plan and a plan breaking are different outcomes.");
  }

  /* ── An unready plan cannot be run ─────────────────────────────────────── */

  seed({ ...PLAN, ready: false, steps: [PLAN.steps[0], { ...PLAN.steps[1], ready: false }] });
  const unready = vm.runInContext("planRow(CHAT_SESSION.messages[0].plan, 0)", context);
  if (/runPlan\(0\)/.test(unready)) {
    failures.push("a plan with a step that could not be prepared still offers to run.");
  }
  await vm.runInContext("runPlan(0)", context);
  if (calls.length) failures.push("an unready plan ran anyway.");

  /* ── Declining answers every step, not the ordering ────────────────────── */

  seed();
  const declined = [];
  vm.runInContext("noteOutcome = (offer,outcome) => globalThis.__declined.push([offer.proposal_id,outcome])", context);
  vm.runInContext("globalThis.__declined = []", context);
  await vm.runInContext("declinePlan(0)", context);
  const answered = vm.runInContext("globalThis.__declined", context);
  if (answered.length !== 3 || answered.some(([, o]) => o !== "declined")) {
    failures.push(
      `declining a plan recorded ${answered.length} answer(s). The steps were offered ` +
      "individually and each is separately answerable — one verdict on an ordering " +
      "loses which offers were refused."
    );
  }

  if (failures.length) {
    console.error("plan check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "plans hold: every step drawn before anything runs, executed in order one " +
    "at a time through its own enumerated operation, a stop landing at the next " +
    "boundary and reporting what had run, a failure halting the rest, an unready " +
    "plan refusing to start, and a decline answering every step"
  );
}

main().catch(error => { console.error(error); process.exit(1); });
