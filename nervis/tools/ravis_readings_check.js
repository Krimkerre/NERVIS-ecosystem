/* RAVIS → Diagnostics says only what RAVIS's answers support.
 *
 * Found live on 16 September 2026, with every provider freshly restarted and LM Studio
 * switched off, the screen read:
 *
 *   - "upstream not answering · models visible 0", while seven providers answered and
 *     645 models were routable. /api/v1/health's `upstream_reachable` and
 *     `models_known` are about RAVIS's **first declared provider** only.
 *   - "Conformance 0 / 0 · suite  · null" and "Verdict FAIL", for a suite nothing on
 *     the page can run or read — beside "0 of the suite's seventeen checks" and "passes
 *     today", when the suite runs twenty-four and the page cannot know its result.
 *   - Breaker "null" and error rate "0%" for providers RAVIS had never called, under
 *     "Every breaker closed and every enabled provider answering".
 *
 * This drives the real screen against recorded answers:
 *
 *   1. **A freshly started RAVIS** — the health rows are named for the first provider,
 *      with its name; no conformance run is "not run here", never FAIL; a provider never
 *      called is "not probed" with no error rate; the note counts them and names an
 *      enabled provider that is not answering, and claims nothing it was not told.
 *   2. **A RAVIS with history** — a breaker and an error rate RAVIS sent are shown as
 *      sent, and an open breaker still leads the note.
 *   3. **A real conformance result** — still shown as PASS or FAIL when one exists.
 *   4. **Nothing RAVIS sends becomes markup.**
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const HOSTILE = '<img src=x onerror="alert(1)">';

const HEALTH = { status: "degraded", upstream_reachable: false,
                 upstream_detail: "All connection attempts failed", upstream_latency_ms: null,
                 models_known: 0, targets: [], capability_suppressions: [] };

function provider(name, extra = {}) {
  return { name, base_url: "", local: false, enabled: true, protocol_mode: "OPENAI_TRANSPARENT",
           breaker: null, error_rate: null, latency_ms: null, reachable: true,
           credential_configured: true, credential_source: "keychain", ...extra };
}

const FRESH = [
  provider("lmstudio", { local: true, reachable: false }),
  provider("openai"),
  provider("anthropic", { protocol_mode: "ANTHROPIC_MESSAGES" }),
];

const CALLED = [
  provider("lmstudio", { local: true, breaker: "CLOSED", error_rate: 0.25, latency_ms: 800 }),
  provider("openai", { breaker: "OPEN", error_rate: 1 }),
  provider("anthropic", { protocol_mode: "ANTHROPIC_MESSAGES" }),
];

function answer(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
}

async function drawn(items, health = HEALTH) {
  const replies = {
    "/api/v1/relay/ravis/api/v1/health": health,
    "/api/v1/relay/ravis/api/v1/providers": { items },
  };
  const fetchImpl = (url) => {
    const found = Object.keys(replies).find((tail) => String(url).endsWith(tail));
    return found ? answer(200, replies[found]) : Promise.reject(new TypeError("fetch failed"));
  };
  const page = loadPage({ fetchImpl });
  vm.runInContext("stopPolling()", page.context);
  await vm.runInContext("ravisDiagnostics()", page.context);
  const content = page.elements.get("sel:#content");
  return { html: content ? content.innerHTML : "", page };
}

function expect(html, shown, why) {
  if (!html.includes(shown)) failures.push(why);
}

function refuse(html, pattern, why) {
  if (pattern.test(html)) failures.push(why);
}

async function fresh() {
  const { html } = await drawn(FRESH);
  expect(html, "Provider health", "the provider table did not render, so its absences prove nothing.");
  expect(html, "<b>first provider</b>", "the health row is not named for the first provider.");
  expect(html, '<span class="mono">lmstudio</span> · ', "the first provider is not named.");
  expect(html, "<b>its models</b>", "the model count is not said to be the first provider's.");
  refuse(html, /models visible|<b>upstream<\/b>/, "the health rows still read as RAVIS-wide.");
  expect(html, "not run here", "a conformance run nobody ran is not said as not run.");
  refuse(html, />FAIL</, "a conformance run nobody ran shows FAIL.");
  refuse(html, /suite\s+·\s+null|>0 \/ 0</, "an empty conformance result is drawn as figures.");
  refuse(html, /seventeen|ten checks below|passes today|0 of the suite/,
         "the page states a conformance count or result it cannot read.");
  expect(html, "Nothing run or listed here", "an empty conformance list is not said as empty.");
  expect(html, "not probed", "a provider never called is not said as not probed.");
  refuse(html, />null</, "a breaker RAVIS did not send shows as null.");
  refuse(html, /<span>0%<\/span>/, "a provider never called shows a 0% error rate.");
  expect(html, "3 of 3 not called since RAVIS started", "the note does not count the providers never called.");
  expect(html, "Not answering now: <b>lmstudio</b>", "an enabled provider not answering is not named.");
  refuse(html, /Every breaker closed|every enabled provider answering/,
         "the note claims health RAVIS did not report.");
}

async function called() {
  const { html } = await drawn(CALLED);
  expect(html, "CLOSED", "a breaker RAVIS sent is not shown.");
  expect(html, "<span>25%</span>", "an error rate RAVIS sent is not shown.");
  expect(html, "<span>100%</span>", "a full error rate is not shown.");
  expect(html, "<b>openai</b> is at OPEN", "an open breaker does not lead the note.");
  const probed = (html.match(/not probed/g) || []).length;
  if (probed !== 1) failures.push(`only the uncalled provider is "not probed" — found ${probed}.`);
}

async function realRun() {
  const { page } = await drawn(FRESH);
  if (vm.runInContext("typeof conformanceKpis", page.context) !== "function") {
    failures.push("the conformance tiles have no helper to read a real run with.");
    return;
  }
  const tiles = (run) => JSON.stringify(vm.runInContext(
    `conformanceKpis(${JSON.stringify(run)}, 16)`, page.context));
  const passed = tiles({ suite: "clarvis", ran_at: "2026-09-16", pass: true, checks: new Array(17) });
  const failed = tiles({ suite: "clarvis", ran_at: "2026-09-16", pass: false, checks: new Array(17) });
  if (!passed.includes('"PASS"') || !passed.includes('"16 / 17"')) failures.push("a passing run is not shown as PASS.");
  if (!failed.includes('"FAIL"')) failures.push("a failing run is not shown as FAIL.");
}

async function hostile() {
  const items = [provider(HOSTILE, { reachable: false }), provider("b", { breaker: HOSTILE })];
  const { html } = await drawn(items, { ...HEALTH, models_known: HOSTILE });
  if (html.includes("<img")) failures.push("something RAVIS sent became markup.");
}

async function main() {
  await fresh();
  await called();
  await realRun();
  await hostile();
  if (failures.length) {
    console.error("ravis readings check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "RAVIS readings hold: the health rows are the first provider's and name it; no " +
    "conformance run is 'not run here', never FAIL, and a real run still shows PASS or FAIL; " +
    "a provider never called is 'not probed' with no error rate, and the note counts them " +
    "and names one not answering; sent breakers and rates shown as sent; nothing injects"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
