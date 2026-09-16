/* A control that could stop something NERVIS did not start must not be offered.
 *
 * §12 is a list of negatives, and the browser can break every one of them
 * without the server noticing: a button enabled for an external service, a verb
 * the page invents, a control that acts without a confirmation. The server
 * refuses all of that — and a screen that offers it anyway teaches somebody
 * their machine works differently than it does, which is its own harm.
 *
 * So this drives the real card and asserts what it offers, not what it says:
 *
 *   1. **Every verb goes to the enumerated set.** No page-invented operation.
 *   2. **Controls are dead unless the service is `nervis_managed` and the
 *      switch is on** — and the *reason* is on screen, because "external" and
 *      "owned but never started here" refuse differently.
 *   3. **Acting is armed.** Stopping a service is not a thing a stray click does.
 *   4. **An open circuit offers clearing and nothing else**, which is §12's
 *      "a crash-loop cannot be re-entered by retry" made visible.
 *   5. **Every write carries the page's control token** (NERVIS 0.34.15), and a refusal —
 *      a NERVIS restarted since the page loaded — is said on screen, not swallowed.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const calls = [];
const untokened = [];
let posts = 0;
let refuseNext = false;

const SERVICES = [
  { service: "ravis", label: "RAVIS", declared: "external", mode: "external",
    adapter: { configured: false }, started_by_nervis: false, pid: null,
    circuit: { failures: 0, opened_at: "", reason: "" },
    why_not: "ravis is external — NERVIS observes it and never controls it" },
  { service: "sirvis", label: "SIRVIS", declared: "nervis_managed",
    mode: "nervis_managed", adapter: { configured: true },
    started_by_nervis: true, pid: 4242,
    circuit: { failures: 0, opened_at: "", reason: "" }, why_not: "" },
  { service: "clarvis", label: "CLARVIS", declared: "nervis_managed",
    mode: "user_managed", adapter: { configured: false },
    started_by_nervis: false, pid: null,
    circuit: { failures: 0, opened_at: "", reason: "" },
    why_not: "clarvis is user-managed: NERVIS can say where the control is and may not use it" },
  { service: "lmstudio", label: "LM Studio", declared: "nervis_managed",
    mode: "nervis_managed", adapter: { configured: true },
    started_by_nervis: false, pid: null,
    circuit: { failures: 3, opened_at: "2026-09-02T10:00:00Z", reason: "would not start" },
    why_not: "lmstudio's supervision circuit is open after 3 failures" },
];

let state = { enabled: false, operations: ["start", "stop", "restart"],
              services: SERVICES, history: [] };

function fetchImpl(url, options = {}) {
  const u = String(url);
  const method = (options.method || "GET").toUpperCase();
  if (u.includes("/api/v1/supervision")) {
    if (method === "POST") {
      calls.push(u.replace(/^.*\/api\/v1\/supervision/, ""));
      posts += 1;
      if (!("x-nervis-control" in (options.headers || {}))) untokened.push(u);
      if (refuseNext) {
        refuseNext = false;
        return Promise.resolve({ ok: false, status: 403, json: async () => ({ error: {
          code: "CONTROL_TOKEN_REQUIRED", message: "reload the dashboard and try again" } }) });
      }
      if (u.endsWith("/enable")) state = { ...state, enabled: JSON.parse(options.body).enabled };
      return Promise.resolve({ ok: true, status: 200, json: async () => state });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => state });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
}

const { context } = loadPage({ fetchImpl });
vm.runInContext("stopPolling()", context);

async function main() {
  let card = await vm.runInContext("supervisionSection()", context);

  /* ── Off by default, with every control dead ───────────────────────────── */

  if (!/>off</.test(card)) failures.push("the card does not show that service control is off.");
  const buttons = card.match(/<button[^>]*onclick="superviseNow[^>]*>/g) || [];
  const live = buttons.filter(b => !/disabled/.test(b));
  if (live.length) {
    failures.push(
      `${live.length} control(s) are pressable while supervision is off. The ` +
      "server refuses them; a screen that offers them teaches somebody their " +
      "machine works differently than it does."
    );
  }

  /* ── The reason is on screen, per service ──────────────────────────────── */

  for (const [shown, why] of [
    ["never controls it", "the card does not say why an external service cannot be controlled."],
    ["may not use it", "the card does not distinguish user-managed from external."],
    ["circuit open", "an open circuit is not shown as one."],
  ]) {
    if (!card.includes(shown)) failures.push(why);
  }

  /* ── Switched on: only the managed one becomes live ────────────────────── */

  await vm.runInContext("setSupervision(true)", context);
  card = await vm.runInContext("supervisionSection()", context);
  const pressable = (card.match(/<button[^>]*onclick="superviseNow\('([a-z]+)','([a-z]+)'\)"(?![^>]*disabled)/g) || []);
  const named = pressable.map(b => /superviseNow\('([a-z]+)'/.exec(b)[1]);
  if (named.includes("ravis")) {
    failures.push("an external service has a pressable control once supervision is on.");
  }
  if (named.includes("clarvis")) {
    failures.push("a user-managed service has a pressable control — §12 says NERVIS may point at it and not use it.");
  }
  if (!named.includes("sirvis")) {
    failures.push("the one nervis-managed service has no pressable control, so the card offers nothing at all.");
  }

  /* ── An open circuit offers clearing and nothing else ──────────────────── */

  const forLmstudio = card.split("LM Studio")[1] || "";
  if (/superviseNow\('lmstudio'/.test(forLmstudio.split("</div>")[3] || forLmstudio)) {
    failures.push(
      "a service with an open circuit still offers start/stop/restart. §12: a " +
      "crash-loop cannot be re-entered by retry."
    );
  }
  if (!/clearCircuit\('lmstudio'\)/.test(card)) {
    failures.push("an open circuit offers no way for an operator to clear it.");
  }

  /* ── Every verb is one of the three ────────────────────────────────────── */

  const verbs = new Set([...card.matchAll(/superviseNow\('[a-z]+','([a-z]+)'\)/g)].map(m => m[1]));
  for (const verb of verbs) {
    if (!["start", "stop", "restart"].includes(verb)) {
      failures.push(`the page offers ${JSON.stringify(verb)}, which is not in §12's set.`);
    }
  }
  if (!verbs.size) failures.push("no verbs found at all — this check is proving nothing.");

  /* ── Acting is armed ───────────────────────────────────────────────────── */

  calls.length = 0;
  let asked = "";
  vm.runInContext("armed = (key, detail) => { globalThis.__asked = detail; return false; }", context);
  await vm.runInContext("superviseNow('sirvis','stop')", context);
  asked = vm.runInContext("globalThis.__asked || ''", context);
  if (!asked) {
    failures.push(
      "stopping a service is not armed. It is not a thing a stray click should do."
    );
  }
  if (calls.length) {
    failures.push("a declined confirmation still sent the request.");
  }

  vm.runInContext("armed = () => true", context);
  await vm.runInContext("superviseNow('sirvis','restart')", context);
  if (!calls.some(c => c === "/sirvis/restart")) {
    failures.push(`a confirmed restart posted ${JSON.stringify(calls)}, not /sirvis/restart.`);
  }

  /* ── Every write carries the token, and a refusal is said ──────────────── */

  await vm.runInContext("clearCircuit('lmstudio')", context);
  if (untokened.length) {
    failures.push(`${untokened.length} supervision write(s) went without the control token, which NERVIS refuses: ${untokened.join(", ")}`);
  }
  if (posts < 3) failures.push("too few writes were sent for the token check to prove anything.");
  vm.runInContext("globalThis.__notes = []; notify = (m) => globalThis.__notes.push(String(m))", context);
  refuseNext = true;
  await vm.runInContext("setSupervision(false)", context);
  const said = vm.runInContext("globalThis.__notes.join(' | ')", context);
  if (!said.includes("reload the dashboard")) {
    failures.push(`a refused switch was not said on screen (said: ${JSON.stringify(said)}).`);
  }

  /* ── A dark NERVIS is reported, not drawn as "off" ─────────────────────── */

  const { context: dark } = loadPage({
    fetchImpl: () => Promise.reject(new TypeError("fetch failed")) });
  vm.runInContext("stopPolling()", dark);
  const darkCard = await vm.runInContext("supervisionSection()", dark);
  if (!/not answering/i.test(darkCard)) {
    failures.push("with NERVIS unreachable the card does not say so — an unread switch must not render as one that is off.");
  }

  if (failures.length) {
    console.error("supervision check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "service control holds: off by default with every control dead, each " +
    "refusal named on screen, only a nervis-managed service pressable, an open " +
    "circuit offering nothing but clearing, three verbs and no fourth, acting " +
    "armed, every write carrying the control token with a refusal said on screen, " +
    "and a dark NERVIS reported rather than drawn as off"
  );
}

main().catch(error => { console.error(error); process.exit(1); });
