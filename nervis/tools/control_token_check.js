/* Every write the page sends NERVIS carries the control token, and nothing else does.
 *
 * NERVIS 0.34.17 asks for the page's token on every write under /api/v1/, not only the
 * configuration ones, and the page adds it once, around `fetch`, instead of at each call site.
 * This loads the real page and sends through its `fetch`:
 *
 *   1. **A write to NERVIS** — POST, PUT and DELETE to a path on the page's own origin — carries
 *      the header, with a plain-object `headers`, a `Headers`-like one, or none at all, and keeps
 *      the headers it already had.
 *   2. **A read to NERVIS** carries nothing extra.
 *   3. **Anything to another origin** — SIRVIS, a protocol-relative address — never sees the
 *      token, write or not.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const sent = [];
const page = loadPage({
  fetchImpl: (url, options) => {
    sent.push({ url: String(url), options: options || {} });
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
  },
});
vm.runInContext("stopPolling()", page.context);

const has = (options) => {
  const headers = options.headers || {};
  return typeof headers.get === "function" ? headers.get("x-nervis-control") !== undefined
    : "x-nervis-control" in headers;
};

async function send(url, init) {
  sent.length = 0;
  page.context.__args = [url, init];
  await vm.runInContext("fetch(...globalThis.__args)", page.context);
  return sent[0];
}

class FakeHeaders {
  constructor() { this.map = new Map([["content-type", "application/json"]]); }
  set(key, value) { this.map.set(key, value); }
  get(key) { return this.map.get(key); }
}

async function main() {
  for (const method of ["POST", "PUT", "DELETE", "post"]) {
    const call = await send("/api/v1/chat", { method, headers: { "content-type": "application/json" } });
    if (!has(call.options)) failures.push(`a ${method} to NERVIS went without the control token.`);
    if (call.options.headers["content-type"] !== "application/json") {
      failures.push(`a ${method} to NERVIS lost the headers it already had.`);
    }
  }
  const bare = await send("/api/v1/learned", { method: "DELETE" });
  if (!has(bare.options)) failures.push("a write with no headers of its own went without the token.");
  const shaped = await send("/api/v1/notifications/n1/read", { method: "POST", headers: new FakeHeaders() });
  if (!has(shaped.options) || shaped.options.headers.get("content-type") !== "application/json") {
    failures.push("a write with a Headers object did not get the token, or lost its own headers.");
  }

  for (const read of [await send("/api/v1/health"), await send("/api/v1/health", { method: "GET" })]) {
    if (has(read.options)) failures.push("a read to NERVIS carried the control token.");
  }

  for (const [url, init] of [
    ["http://127.0.0.1:8721/api/v1/runtime/sessions", { method: "POST" }],
    ["//evil.example/api/v1/chat", { method: "POST" }],
    ["http://127.0.0.1:8721/api/v1/health", undefined],
  ]) {
    const call = await send(url, init);
    if (has(call.options)) failures.push(`the control token was sent to ${url}.`);
  }

  if (failures.length) {
    console.error("control token check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "the control token rides on every write to NERVIS, whatever headers it already had, and on " +
    "no read and nothing sent anywhere else"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
