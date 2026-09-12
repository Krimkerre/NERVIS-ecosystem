/* RAVIS → Diagnostics shows the models resting from tool requests, and Lift ends one.
 *
 * Since 12 September 2026 a model that refuses tools is skipped for tool requests
 * for half an hour while it keeps serving plain chat, and RAVIS publishes the
 * resting models on /api/v1/health as capability_suppressions. This drives the
 * real screen against a recorded answer and asserts what it draws and what it
 * sends, not what a comment says it does:
 *
 *   1. **One row per resting model** — its name, that it still serves requests
 *      without tools, RAVIS's own reason, the minutes left, and a Lift button.
 *   2. **Nothing for an empty list, and nothing for an absent field.** A RAVIS
 *      from before suppressions sends no field, and a row or a placeholder there
 *      would be a claim the page cannot make. The card itself must still render,
 *      or "nothing drawn" would pass against a screen that drew nothing at all.
 *   3. **Lift goes through NERVIS** — the one path every RAVIS write on this page
 *      takes, because NERVIS holds the admin credential and the browser must not —
 *      with the page's control header and the model id's slashes intact, and the
 *      rows are redrawn from the list RAVIS answered with.
 *   4. **A 403 says the admin key is the problem**, and redraws nothing.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];

const RESTING = [
  { model: "qwen/qwen3-1.7b", provider: "lmstudio", capability: "tools",
    reason: "HTTP 400: this model does not support tools",
    window_seconds: 1800, lifts_in_seconds: 1742 },
  { model: "granite-4.0-h-tiny", provider: "lmstudio", capability: "tools",
    reason: "HTTP 200 carrying an error: tools are not supported",
    window_seconds: 1800, lifts_in_seconds: 30 },
];

const HEALTH = { status: "healthy", upstream_reachable: true, upstream_detail: "",
                 upstream_latency_ms: 12, models_known: 2, targets: [] };

function answer(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
}

/* A recorded RAVIS behind NERVIS. The health read answers `health`, a lift answers
   whatever `lift` returns, and everything else is down, so every other card on the
   screen takes the absent path render_check already proves it survives. */
function world(health, lift) {
  const sent = [];
  const fetchImpl = (url, options = {}) => {
    const address = String(url);
    const method = (options.method || "GET").toUpperCase();
    if (method === "POST" && address.includes("/health/suppressions/")) {
      sent.push({ address, headers: options.headers || {} });
      return lift();
    }
    if (address.endsWith("/api/v1/relay/ravis/api/v1/health")) return answer(200, health);
    return Promise.reject(new TypeError("fetch failed"));
  };
  const loaded = loadPage({ fetchImpl });
  vm.runInContext("stopPolling()", loaded.context);
  const alerts = [];
  loaded.context.alert = (message) => alerts.push(String(message));
  return { ...loaded, sent, alerts };
}

async function diagnostics(page) {
  await vm.runInContext("ravisDiagnostics()", page.context);
  const content = page.elements.get("sel:#content");
  return content ? content.innerHTML : "";
}

function slotOf(page) {
  const slot = page.elements.get("ravis-suppressions");
  return slot ? slot.innerHTML : "";
}

async function rowsAreDrawn(page) {
  const drawn = await diagnostics(page);
  if (!drawn.includes("RAVIS health")) {
    failures.push("the RAVIS health card did not render at all, so nothing below proves anything.");
    return;
  }
  for (const [resting, minutes] of [[RESTING[0], "in 30 min"], [RESTING[1], "in 1 min"]]) {
    const after = drawn.split(`<b>${resting.model}</b>`)[1];
    if (after == null) {
      failures.push(`no row names ${resting.model}, though RAVIS reported it resting.`);
      continue;
    }
    const row = after.split("</div>")[0];
    for (const [shown, why] of [
      ["resting from tool requests", "does not say it is resting from tool requests"],
      ["still used without tools", "does not say it still serves requests without tools"],
      [resting.reason, "does not carry RAVIS's reason"],
      [`tool requests resume ${minutes}`, `does not say tool requests resume ${minutes}`],
      [`liftSuppression('${resting.model}')`, "offers no Lift"],
    ]) {
      if (!row.includes(shown)) failures.push(`the row for ${resting.model} ${why}.`);
    }
  }
  const lifts = (drawn.match(/onclick="liftSuppression\(/g) || []).length;
  if (lifts !== RESTING.length) {
    failures.push(`${lifts} Lift button(s) for ${RESTING.length} resting models.`);
  }
}

async function nothingIsDrawnWithoutAList() {
  for (const [label, health] of [
    ["an empty list", { ...HEALTH, capability_suppressions: [] }],
    ["no field at all, as a RAVIS from before suppressions sends", { ...HEALTH }],
  ]) {
    const page = world(health, () => answer(500, {}));
    const html = await diagnostics(page);
    if (!html.includes("RAVIS health")) {
      failures.push(`with ${label} the RAVIS health card did not render, so "nothing drawn" would pass vacuously.`);
    }
    if (/resting from tool requests|liftSuppression\(/.test(html)) {
      failures.push(`with ${label} the screen still draws a resting-model row.`);
    }
  }
}

async function liftSendsAndRedraws(page) {
  await vm.runInContext("liftSuppression('qwen/qwen3-1.7b')", page.context);
  const [lift] = page.sent;
  if (!lift) {
    failures.push("pressing Lift sent nothing.");
    return;
  }
  if (lift.address !== "/api/v1/ravis/health/suppressions/qwen/qwen3-1.7b/lift") {
    failures.push(
      `Lift went to ${JSON.stringify(lift.address)}; NERVIS's proxy is ` +
      "/api/v1/ravis/health/suppressions/{model}/lift, with the id's slashes intact."
    );
  }
  if (!("x-nervis-control" in lift.headers)) {
    failures.push("Lift did not carry the page's control header, so NERVIS refuses it before RAVIS sees it.");
  }
  const redrawn = slotOf(page);
  if (!redrawn.includes("granite-4.0-h-tiny") || redrawn.includes("qwen/qwen3-1.7b")) {
    failures.push("after a successful Lift the rows were not redrawn from the list RAVIS returned.");
  }
  if (page.alerts.length) failures.push(`a successful Lift raised an alert: ${page.alerts[0]}`);

  await vm.runInContext("liftSuppression('lab/odd model?v=2')", page.context);
  const odd = page.sent[1];
  if (!odd || odd.address !== "/api/v1/ravis/health/suppressions/lab/odd%20model%3Fv%3D2/lift") {
    failures.push(
      `an id holding a space and a question mark went to ${JSON.stringify(odd && odd.address)}; ` +
      "each segment should be encoded and the slash left as a separator."
    );
  }
}

async function aRefusalNamesTheAdminKey() {
  const page = world({ ...HEALTH, capability_suppressions: RESTING },
    () => answer(403, { message: "NERVIS holds no admin credential for RAVIS" }));
  await diagnostics(page);
  const before = slotOf(page);
  await vm.runInContext("liftSuppression('qwen/qwen3-1.7b')", page.context);
  if (!page.alerts.some((said) => /admin key/.test(said))) {
    failures.push(`a 403 did not say lifting needs RAVIS's admin key: ${JSON.stringify(page.alerts)}`);
  }
  if (slotOf(page) !== before) failures.push("a refused Lift redrew the rows as if it had worked.");
}

async function main() {
  const fixture = world({ ...HEALTH, capability_suppressions: RESTING },
    () => answer(200, { model: "qwen/qwen3-1.7b", capability: "tools",
                        was_suppressed: true, capability_suppressions: [RESTING[1]] }));
  await rowsAreDrawn(fixture);
  await nothingIsDrawnWithoutAList();
  await liftSendsAndRedraws(fixture);
  await aRefusalNamesTheAdminKey();

  if (failures.length) {
    console.error("ravis diagnostics check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "RAVIS Diagnostics holds: a row per model resting from tool requests with its " +
    "reason and minutes left, nothing for an empty or absent list, Lift through " +
    "NERVIS with the control header and the id's slashes intact, a redraw from " +
    "RAVIS's answer, and a 403 that names the admin key"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
