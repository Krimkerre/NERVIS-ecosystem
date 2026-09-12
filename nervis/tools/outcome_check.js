/* A refused read, a broken service and an absent one must not look alike.
 *
 * §25.2: "Views await and then paint, with nothing defined for slow or failed.
 * Service-level absence is handled; request-level is not."
 *
 * The mechanism behind that sentence was one line: `live()` turned every
 * non-2xx into `throw new Error('HTTP '+status)`, which landed in the same
 * `catch` as a network refusal, a timeout, a bad body and an adapter that
 * threw — and the binding was never read. Five different things to do next,
 * reported as one.
 *
 * This drives the page's own fetch layer through each of those five and
 * requires five distinct answers. It is a behavioural check rather than a
 * rendering one: it asserts what the page *knows*, because what it draws from
 * that is then a matter of markup.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

/* A real Response always has headers, and `live()` reads `x-nervis-relay` from
 * them since RAVIS reads went through NERVIS on 12 September 2026. These fakes
 * predate that: without headers every case threw a TypeError before its status
 * was read, and all seven were recorded as unreachable. */
const NO_HEADERS = { get: () => null };

/* Each case is a fetch that fails in one specific way. */
const CASES = [
  { name: "a refusal",        kind: "refused",
    fetch: async () => ({ headers: NO_HEADERS, ok: false, status: 401, json: async () => ({}) }) },
  { name: "a forbidden read", kind: "refused",
    fetch: async () => ({ headers: NO_HEADERS, ok: false, status: 403, json: async () => ({}) }) },
  { name: "a broken service", kind: "failed",
    fetch: async () => ({ headers: NO_HEADERS, ok: false, status: 503, json: async () => ({}) }) },
  { name: "an absent service", kind: "unreachable",
    fetch: async () => { throw new TypeError("fetch failed"); } },
  { name: "a body that is not JSON", kind: "malformed",
    fetch: async () => ({ headers: NO_HEADERS, ok: true, status: 200,
      json: async () => { throw new SyntaxError("Unexpected token <"); } }) },
  { name: "a port that hangs", kind: "slow",
    fetch: async () => { const e = new Error("aborted"); e.name = "AbortError"; throw e; } },
];

const failures = [];
const seen = new Map();

(async () => {
  for (const testCase of CASES) {
    const { context } = loadPage({ fetchImpl: testCase.fetch });
    /* Straight through the real helper, with a mock that cannot be mistaken for
       a live answer and an adapter that does nothing. */
    await vm.runInContext(
      "live('ravis','/api/v1/anything',async()=>({items:[]}),p=>p)", context);
    const outcome = vm.runInContext("OUTCOME.ravis", context);
    if (!outcome) { failures.push(`${testCase.name}: recorded no outcome at all`); continue; }
    if (outcome.kind !== testCase.kind) {
      failures.push(
        `${testCase.name}: recorded "${outcome.kind}", expected "${testCase.kind}"`);
    }
    seen.set(testCase.name, outcome.kind);
    if (vm.runInContext("SOURCE.ravis", context) !== "mock") {
      failures.push(`${testCase.name}: did not fall back to the transcription`);
    }
  }

  /* An adapter that throws is the page's own fault and must say so rather than
     blame the service — the service answered. */
  {
    const { context } = loadPage({
      fetchImpl: async () => ({ headers: NO_HEADERS, ok: true, status: 200, json: async () => ({ items: [] }) }),
    });
    await vm.runInContext(
      "live('ravis','/x',async()=>({items:[]}),()=>{throw new Error('no field')})", context);
    const outcome = vm.runInContext("OUTCOME.ravis", context);
    if (!outcome || outcome.kind !== "unreadable") {
      failures.push(
        `an adapter that throws: recorded "${outcome && outcome.kind}", expected "unreadable"`);
    }
    seen.set("an adapter that throws", outcome && outcome.kind);
  }

  /* And the ordinary case, so a check that reports everything as broken fails. */
  {
    const { context } = loadPage({
      fetchImpl: async () => ({ headers: NO_HEADERS, ok: true, status: 200, json: async () => ({ items: [1] }) }),
    });
    await vm.runInContext("live('ravis','/x',async()=>({items:[]}),p=>p)", context);
    const outcome = vm.runInContext("OUTCOME.ravis", context);
    if (!outcome || outcome.kind !== "answered") {
      failures.push(`a service that answers: recorded "${outcome && outcome.kind}"`);
    }
    if (vm.runInContext("SOURCE.ravis", context) !== "live") {
      failures.push("a service that answers: was not recorded as live");
    }
  }

  /* The point of the whole exercise: the kinds are actually different. A helper
     that returned one label for everything would pass every assertion above if
     the expectations were ever loosened to match it. */
  const distinct = new Set(seen.values());
  if (distinct.size < 5) {
    failures.push(
      `only ${distinct.size} distinct outcomes across ${seen.size} failure modes — ` +
      `${JSON.stringify([...seen])}`);
  }

  if (failures.length) {
    console.error(`${failures.length} outcome failure(s):\n`);
    for (const failure of failures) console.error(`  • ${failure}`);
    process.exit(1);
  }
  console.log(
    `${CASES.length + 2} request outcomes stay distinct: ` +
    `${[...new Set(seen.values())].join(", ")}, answered`);
})();
