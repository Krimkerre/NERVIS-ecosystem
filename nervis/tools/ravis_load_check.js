/* RAVIS → Diagnostics shows what RAVIS has running now (RAVIS M20, §12.3).
 *
 * Since RAVIS 0.29.0 its /api/v1/health carries `load`: requests in flight by provider,
 * local generations, memory, congestion, providers' own statements of their limits, and
 * what it does not read. This drives the real screen against recorded answers:
 *
 *   1. **A busy RAVIS** — every part drawn: running counts with each provider and model,
 *      memory with its pressure, a congested provider with its counts and age, a provider's
 *      stated limits, the queue sentences and the "not read" lines, and that these are
 *      readings RAVIS does not route on.
 *   2. **An idle RAVIS** — zero shown as 0, not as a dash; no congestion and no limits said
 *      in words rather than drawn empty; memory it could not read said as not read.
 *   3. **An older RAVIS with no `load`** — no card at all, while the health card still draws.
 *   4. **Nothing RAVIS sends becomes markup.**
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const HOSTILE = '<img src=x onerror="alert(1)">';

const BASE_HEALTH = { status: "healthy", upstream_reachable: true, upstream_detail: "",
                      upstream_latency_ms: 12, models_known: 2, targets: [],
                      capability_suppressions: [] };

const BUSY = {
  active: 3, local_generations: 1, peak: 5, attempts_started: 42, since: 1789560000,
  by_provider: [
    { provider: "lmstudio", local: true, active: 1, models: { "google/gemma-4-12b-qat": 1 } },
    { provider: "openai", local: false, active: 2, models: { "gpt-4.1-2025-04-14": 2 } },
  ],
  queue: { held: 0, reason: "RAVIS sends each request upstream as it arrives and keeps no queue" },
  local_queues: { state: "unknown", reason: "LM Studio and Ollama publish no queue depth" },
  limits: [{ provider: "openai",
             requests: { remaining: "498", limit: "500", reset: "120ms", seconds_ago: 4.2 },
             retry_after: { value: "2", seconds_ago: 30 } }],
  memory: { available_bytes: 8 * 1024 ** 3, total_bytes: 32 * 1024 ** 3, free_fraction: 0.25,
            under_pressure: true, detail: "" },
  congestion: [{ provider: "openrouter", last: "rate_limit", seconds_ago: 12.4,
                 rate_limit: 3, provider_overload: 1 }],
  not_read: ["provider credits and spend limits: no provider's balance is asked for",
             "SIRVIS's contention evidence (how co-loaded models slow each other): not read yet"],
};

const IDLE = {
  ...BUSY, active: 0, local_generations: 0, peak: 0, attempts_started: 0, by_provider: [],
  limits: [], congestion: [],
  memory: { available_bytes: null, total_bytes: null, free_fraction: null, under_pressure: null,
            detail: "vm_stat could not be read" },
};

function answer(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
}

async function drawn(health) {
  const fetchImpl = (url) => String(url).endsWith("/api/v1/relay/ravis/api/v1/health")
    ? answer(200, health) : Promise.reject(new TypeError("fetch failed"));
  const page = loadPage({ fetchImpl });
  vm.runInContext("stopPolling()", page.context);
  await vm.runInContext("ravisDiagnostics()", page.context);
  const content = page.elements.get("sel:#content");
  return content ? content.innerHTML : "";
}

function expect(html, shown, why) {
  if (!html.includes(shown)) failures.push(why);
}

async function busy() {
  const html = await drawn({ ...BASE_HEALTH, load: BUSY });
  expect(html, "RAVIS load now", "a busy RAVIS draws no load card.");
  expect(html, "3 request(s) · 1 on this machine", "the running counts are not shown.");
  expect(html, "<b>openai</b>", "a provider with requests running is not named.");
  expect(html, "gpt-4.1-2025-04-14 ×2", "the models running are not shown with their counts.");
  expect(html, "<b>lmstudio · local</b>", "a local provider is not marked local.");
  expect(html, "42 attempt(s) · at most 5 at once", "attempts and the peak are not shown.");
  expect(html, "keeps no queue", "RAVIS's own queue is not explained.");
  expect(html, "publish no queue depth", "the local runtimes' unknown queue is not explained.");
  expect(html, "8.0 GB free of 32.0 GB (25%)", "memory is not shown.");
  expect(html, "under pressure", "memory pressure is not shown.");
  expect(html, "<b>openrouter</b>", "a congested provider is not named.");
  expect(html, "rate limit</span>", "the congestion kind is not shown in words.");
  expect(html, "12 s ago · 3 rate limit(s), 1 overload(s) since start", "congestion counts and age are missing.");
  expect(html, "requests: 498 left of 500 · resets 120ms (4 s ago)", "a provider's stated limit is not shown.");
  expect(html, "asked to wait 2s (30 s ago)", "a retry-after is not shown.");
  expect(html, "no provider&#39;s balance is asked for", "what RAVIS does not read is not said.");
  expect(html, "routes on none of them", "the card does not say these are readings only.");
}

async function idle() {
  const html = await drawn({ ...BASE_HEALTH, load: IDLE });
  expect(html, "0 request(s) · 0 on this machine", "zero running is not shown as 0.");
  expect(html, "0 attempt(s) · at most 0 at once", "zero attempts are not shown as 0.");
  expect(html, "no provider has answered with a rate limit", "no congestion is not said in words.");
  expect(html, "which is not the same as having none", "no stated limit is not said in words.");
  expect(html, "not read — vm_stat could not be read", "unread memory is not said as not read.");
  if (/<b>running now<\/b><span class="mono">—/.test(html)) {
    failures.push("an idle RAVIS shows its running count as a dash.");
  }
}

async function older() {
  const html = await drawn({ ...BASE_HEALTH });
  expect(html, "RAVIS health", "without `load` the health card did not render, so the absence proves nothing.");
  if (html.includes("RAVIS load now")) failures.push("a RAVIS that sends no `load` still gets a load card.");
}

async function hostile() {
  const load = {
    ...BUSY,
    by_provider: [{ provider: HOSTILE, local: false, active: 1, models: { [HOSTILE]: 1 } }],
    congestion: [{ ...BUSY.congestion[0], provider: HOSTILE, last: HOSTILE }],
    limits: [{ provider: HOSTILE, [HOSTILE]: { remaining: HOSTILE, seconds_ago: 1 } }],
    not_read: [HOSTILE],
    queue: { held: 0, reason: HOSTILE },
  };
  const html = await drawn({ ...BASE_HEALTH, load });
  if (html.includes("<img")) failures.push("something RAVIS sent became markup.");
}

async function main() {
  await busy();
  await idle();
  await older();
  await hostile();
  if (failures.length) {
    console.error("ravis load check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "RAVIS load holds: running counts by provider and model, attempts and peak, the queue " +
    "and local-queue sentences, memory and its pressure, congestion with counts and age, " +
    "providers' stated limits and retry-after, what is not read, readings-only said once; " +
    "zero shown as 0 and absences said in words; no card for a RAVIS without `load`; and " +
    "nothing RAVIS sends injects"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
