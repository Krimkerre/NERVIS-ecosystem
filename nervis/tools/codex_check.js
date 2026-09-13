/* RAVIS → Credentials signs Codex in to the ChatGPT plan, and says where it stands.
 *
 * The owner looked at the Credentials screen on 13 September 2026 and found no way to
 * sign in. This drives the real screen against a recorded RAVIS behind NERVIS, and
 * asserts what it draws and what it sends:
 *
 *   1. **Every state draws its own words** and offers only the buttons RAVIS would
 *      accept: Sign in where no account is signed in and a sign-in can run (including
 *      paused-for-re-testing on a tested build, today's case), Sign out where one is,
 *      This is my account for a different account, Try again after a failed sign-in.
 *   2. **The sign-in tab is opened inside the click**, before the request is sent,
 *      because a tab opened after an await is a pop-up the browser blocks; it is
 *      pointed at the page RAVIS gave, cut off from this page, and closed again on a
 *      refusal or on an address that is not https.
 *   3. **Signing in follows itself:** the waiting panel shows the link again, the time
 *      left and Cancel; a look at the sign-in route that finds it ended redraws the
 *      outcome (signed in, or RAVIS restarted during it).
 *   4. **Every write goes through NERVIS's control routes** with the page's control
 *      header and the body RAVIS expects; Sign out takes two clicks and never uses
 *      confirm(); the account confirmed is the hint the card showed.
 *   5. **The link is read only from the admin route**, never through the relay, and a
 *      refusal there says what to fix instead of drawing a link.
 *   6. **Refusals are said plainly**: the ports held, a RAVIS too old to offer this.
 *   7. **Nothing RAVIS sends can inject markup**, the sign-in address included, which
 *      lands in an href.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];

const AUTH_URL = "https://auth.openai.com/oauth/authorize?fixture=1";
const HINT = "o…@example.com";
const RUNTIME = { source: "homebrew", version: "0.154.0", verdict: "tested", strict_rules: "unproven" };
const IDLE = { state: "idle", started_at: null, expires_at: null, error: null };
const ACCOUNT = { signed_in: true, auth_mode: "chatgpt", plan: "plus", email_hint: HINT,
                  fingerprint: "strong", fingerprint_matches: true, plan_changed: false };
const RESTARTED = { state: "failed", started_at: null, expires_at: null,
                    error: "RAVIS restarted during sign-in" };
/* One provider key row, configured, so the Credentials screen draws its ● dot. */
const KEY_ROW = { name: "openai", label: "OpenAI", configured: true, source: "keychain",
                  file_is_private: true, routable: true };

/* `GET /api/v1/codex` in codex-state.json's shape, with what a case changes. */
function codex(state, reason, extra = {}) {
  return {
    backend_id: "ravis/codex", execution: "delegated_agent", roles: ["agent"], state, reason,
    revision: 1, runtime: RUNTIME, account: null,
    usage: { known: false, source: null, observed_at: null, stale: false,
             allowance_not_cost: true, windows: [] },
    runs: [], models: [], sign_in: IDLE, ...extra,
  };
}

const later = (seconds) => new Date(Date.now() + seconds * 1000).toISOString();
const waitingPublic = () => ({ state: "waiting_for_browser", started_at: later(-60),
                               expires_at: later(540), error: null });
const waitingAdmin = (url = AUTH_URL) => ({ sign_in: { state: "waiting_for_browser",
  auth_url: url, callback_port: 1455, started_at: later(-60), expires_at: later(540) } });

function answer(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status,
                           headers: { get: () => null }, json: async () => body });
}

/* A recorded RAVIS behind NERVIS. `ravis.state` is what the relayed `GET /api/v1/codex`
   answers; `ravis.control["POST /sign-in"]` and its siblings answer NERVIS's control
   routes. Every Codex call is logged, with how many tabs were open when it was sent. */
function world({ state, codexStatus = 200, control = {}, reads = {} }) {
  const ravis = { state, codexStatus, control };
  const sent = [];
  const tabs = [];
  const fetchImpl = (url, init = {}) => {
    const address = String(url);
    const method = (init.method || "GET").toUpperCase();
    if (address.endsWith("/api/v1/relay/ravis/api/v1/providers/credentials")) {
      return answer(200, { items: [KEY_ROW] });
    }
    const read = Object.keys(reads).find((path) => address.includes(path));
    if (read) return answer(200, reads[read]);
    if (address.endsWith("/api/v1/relay/ravis/api/v1/codex")) {
      if (ravis.codexStatus === 0) return Promise.reject(new TypeError("fetch failed"));
      return answer(ravis.codexStatus, ravis.state);
    }
    if (address.includes("/codex")) {
      const sub = address.split("/api/v1/ravis/codex")[1];
      const route = sub == null ? null : `${method} ${sub}`;
      sent.push({ address, route, headers: init.headers || {}, tabsOpen: tabs.length,
                  body: init.body == null ? null : JSON.parse(init.body) });
      const handler = route && ravis.control[route];
      return handler ? handler() : answer(500, {});
    }
    return Promise.reject(new TypeError("fetch failed"));
  };
  const loaded = loadPage({ fetchImpl });
  vm.runInContext("stopPolling()", loaded.context);
  loaded.context.open = () => {
    const tab = { closed: false, opener: {}, location: { href: "" },
                  close() { this.closed = true; } };
    tabs.push(tab);
    return tab;
  };
  loaded.context.confirm = () => {
    failures.push("the Codex card called confirm(), which a browser can mute for good.");
    return true;
  };
  return { ...loaded, ravis, sent, tabs };
}

const run = (page, code) => vm.runInContext(code, page.context);
const settle = () => new Promise((done) => setTimeout(done, 30));
/* The page draws its default screen, the Overview, as it loads. That draw awaits its own
   reads, and finishing after the screen under test it overwrote #content with the
   Overview; so each screen is drawn only once the load's draw has had time to land. */
const quiet = () => new Promise((done) => setTimeout(done, 250));

async function credentials(page) {
  await quiet();
  page.exported.state.app = "ravis";
  page.exported.state.view = "Credentials";
  await run(page, "ravisCredentials()");
  await settle();
  const content = page.elements.get("sel:#content");
  return content ? content.innerHTML : "";
}

/* The card's inner slot. The harness does not parse markup, so on the first draw the slot
   exists only inside #content's HTML, and is cut out of it here; once the card redraws the
   slot itself (after a click or a look), that element holds the newer markup. */
function slotOf(page) {
  const slot = page.elements.get("codex-sign-in");
  if (slot && slot.innerHTML) return slot.innerHTML;
  const content = page.elements.get("sel:#content");
  const html = content ? content.innerHTML : "";
  const start = html.indexOf('<div id="codex-sign-in">');
  const end = html.indexOf("this signs RAVIS", start);
  return start < 0 ? "" : html.slice(start, end < 0 ? html.length : end);
}

function expect(label, html, present, absent = []) {
  for (const words of present) {
    if (!html.includes(words)) failures.push(`${label}: does not draw ${JSON.stringify(words)}.`);
  }
  for (const words of absent) {
    if (html.includes(words)) failures.push(`${label}: still draws ${JSON.stringify(words)}.`);
  }
}

const START = "CODEX_SIGN_IN.start()";
const SIGN_OUT = "CODEX_SIGN_IN.signOut()";
const CONFIRM = "CODEX_SIGN_IN.confirm()";

/* 1 — each state, from RAVIS's public state alone. */
async function everyStateDrawsItsWords() {
  const cases = [
    ["signed out", codex("signed_out", "Codex is signed out."),
     ["signed out", "Codex is signed out.", ">Sign in with ChatGPT<"], [SIGN_OUT, CONFIRM]],
    ["not installed", codex("not_installed", "The Homebrew link does not lead to a file."),
     ["not installed", "does not lead to a file"], [START, SIGN_OUT]],
    ["not available", codex("not_available", "Codex is switched off in the settings."),
     ["not available", "switched off in the settings"], [START, SIGN_OUT]],
    ["paused on a tested build whose file rules are unproven",
     codex("untested_version", "The file rules are not yet proven."),
     ["paused — Codex needs re-testing", "not yet proven", ">Sign in with ChatGPT<"], [SIGN_OUT]],
    ["paused on a build in neither list",
     codex("untested_version", "Codex changed (now 0.155.0) and needs re-testing before new work.",
           { runtime: { ...RUNTIME, verdict: "untested" } }),
     ["paused — Codex needs re-testing", "now 0.155.0"], [START]],
    ["the process restarting", codex("runtime_down", "The process is starting."),
     ["Codex process restarting", "The process is starting."], [START, SIGN_OUT]],
    ["signed out by OpenAI", codex("sign_in_expired", "OpenAI signed Codex out."),
     ["sign-in expired", "OpenAI signed Codex out.", ">Sign in with ChatGPT again<"], [SIGN_OUT]],
    ["signed in", codex("signed_in", "Codex is signed in with a ChatGPT Plus plan.", { account: ACCOUNT }),
     ["signed in", HINT, "ChatGPT Plus plan", ">Sign out…<"], [START, CONFIRM]],
    ["a different account",
     codex("account_changed", "Codex is signed in to a different account than the one you confirmed.",
           { account: { ...ACCOUNT, fingerprint_matches: false } }),
     ["different account", `This is my account: ${HINT}`, ">Sign out…<"], [START]],
    ["the allowance used up", codex("quota_exhausted", "The allowance is used up until 04:30.", { account: ACCOUNT }),
     ["allowance used up", "used up until 04:30", HINT, ">Sign out…<"], [START]],
    ["RAVIS restarted during a sign-in", codex("signed_out", "Codex is signed out.", { sign_in: RESTARTED }),
     ["RAVIS restarted while the sign-in page was waiting", ">Try again<"], [">Sign in with ChatGPT<"]],
    ["the sign-in page expired",
     codex("signed_out", "Codex is signed out.", { sign_in: { ...RESTARTED,
       error: "The sign-in page expired after 10 minutes; start the sign-in again." } }),
     ["expired after 10 minutes", ">Try again<"], []],
  ];
  for (const [label, state, present, absent] of cases) {
    const page = world({ state });
    const html = await credentials(page);
    if (!html.includes("Provider keys") || !html.includes("ChatGPT subscription (Codex)")) {
      failures.push(`${label}: the Credentials screen or its Codex card did not render, so nothing below proves anything.`);
      continue;
    }
    /* The ● before a stored key is markup the page writes, not RAVIS's text: escaped,
       it read "&#9679; macOS Keychain" on screen (13 September 2026). */
    if (!html.includes("&#9679; macOS Keychain") || html.includes("&amp;#9679;")) {
      failures.push(`${label}: a stored key's row does not draw its dot: the entity was escaped into text.`);
    }
    expect(label, slotOf(page), present, absent);
    run(page, "CODEX_SIGN_IN.stop()");
  }
}

/* 2, 3, 4 — a sign-in started here, followed to its end. */
async function signingInFromTheButton() {
  const page = world({ state: codex("signed_out", "Codex is signed out."), control: {
    "POST /sign-in": () => {
      page.ravis.state = codex("signed_out", "Codex is signed out.", { sign_in: waitingPublic() });
      return answer(202, waitingAdmin());
    },
    "GET /sign-in": () => answer(200, waitingAdmin()),
  } });
  await credentials(page);
  await run(page, START);

  const post = page.sent.find((call) => call.route === "POST /sign-in");
  if (!post) {
    failures.push("pressing Sign in with ChatGPT sent nothing to NERVIS's sign-in route.");
    return;
  }
  if (post.tabsOpen !== 1) {
    failures.push("the sign-in tab was not opened before the request was sent, so a browser blocks it as a pop-up.");
  }
  if (!("x-nervis-control" in post.headers)) {
    failures.push("the sign-in request carried no control header, so NERVIS refuses it before RAVIS sees it.");
  }
  if (JSON.stringify(post.body) !== JSON.stringify({ method: "browser" })) {
    failures.push(`the sign-in request sent ${JSON.stringify(post.body)}, not {"method":"browser"}.`);
  }
  const [tab] = page.tabs;
  if (!tab || tab.location.href !== AUTH_URL || tab.opener !== null || tab.closed) {
    failures.push(`the new tab was not pointed at RAVIS's sign-in page and cut off from this one: ${JSON.stringify(tab)}.`);
  }
  expect("signing in", slotOf(page),
    ["signing in", "opened in a new tab", `href="${AUTH_URL}"`, "Open the sign-in page again",
     "before the page expires", "CODEX_SIGN_IN.cancel()"], [START]);

  await run(page, "CODEX_SIGN_IN.count()");
  const clock = page.elements.get("codex-sign-in-countdown");
  if (!clock || !/^\d+ min \d\d s before the page expires$/.test(clock.textContent)) {
    failures.push(`the countdown did not tick: ${JSON.stringify(clock && clock.textContent)}.`);
  }

  // The owner signs in in the other tab: the next look finds it over.
  page.ravis.control["GET /sign-in"] = () => answer(200, { sign_in: IDLE });
  page.ravis.state = codex("signed_in", "Codex is signed in with a ChatGPT Plus plan.", { account: ACCOUNT });
  await run(page, "CODEX_SIGN_IN.tick()");
  expect("after signing in", slotOf(page), [HINT, ">Sign out…<"], ["Open the sign-in page again", START]);

  if (page.sent.some((call) => call.address.includes("/relay/ravis/api/v1/codex/sign-in"))) {
    failures.push("the sign-in page's address was read through the relay, which must never carry it.");
  }
  run(page, "CODEX_SIGN_IN.stop()");
}

async function aRestartDuringTheSignInIsSaid() {
  const page = world({ state: codex("signed_out", "Codex is signed out.", { sign_in: waitingPublic() }),
    control: { "GET /sign-in": () => answer(200, waitingAdmin()) } });
  await credentials(page);
  expect("a sign-in found waiting when the screen opens", slotOf(page),
    ["signing in", `href="${AUTH_URL}"`, "CODEX_SIGN_IN.cancel()"]);

  page.ravis.control["GET /sign-in"] = () => answer(200, { sign_in: RESTARTED });
  page.ravis.state = codex("signed_out", "Codex is signed out.", { sign_in: RESTARTED });
  await run(page, "CODEX_SIGN_IN.tick()");
  expect("RAVIS restarted mid sign-in", slotOf(page),
    ["RAVIS restarted while the sign-in page was waiting", ">Try again<"], ["Open the sign-in page again"]);
  run(page, "CODEX_SIGN_IN.stop()");
}

async function cancelSendsDelete() {
  const page = world({ state: codex("signed_out", "Codex is signed out.", { sign_in: waitingPublic() }),
    control: {
      "GET /sign-in": () => answer(200, waitingAdmin()),
      "DELETE /sign-in": () => {
        page.ravis.state = codex("signed_out", "Codex is signed out.");
        return answer(200, { cancelled: true });
      },
    } });
  await credentials(page);
  await run(page, "CODEX_SIGN_IN.cancel()");
  const cancel = page.sent.find((call) => call.route === "DELETE /sign-in");
  if (!cancel || !("x-nervis-control" in cancel.headers)) {
    failures.push("Cancel did not send DELETE to NERVIS's sign-in route with the control header.");
  }
  expect("after Cancel", slotOf(page), ["Sign-in cancelled.", ">Sign in with ChatGPT<"], ["signing in"]);
  run(page, "CODEX_SIGN_IN.stop()");
}

async function signOutTakesTwoClicks() {
  const signedOut = codex("signed_out", "Codex is signed out.");
  const page = world({ state: codex("signed_in", "Codex is signed in.", { account: ACCOUNT }),
    control: { "POST /sign-out": () => { page.ravis.state = signedOut; return answer(200, signedOut); } } });
  await credentials(page);
  await run(page, SIGN_OUT);
  if (page.sent.some((call) => call.route === "POST /sign-out")) {
    failures.push("one click on Sign out… signed Codex out; it takes two.");
  }
  expect("Sign out, armed", slotOf(page), ["Click again to sign out", "Stay signed in"]);
  await run(page, SIGN_OUT);
  const out = page.sent.find((call) => call.route === "POST /sign-out");
  if (!out || !("x-nervis-control" in out.headers) || JSON.stringify(out.body) !== "{}") {
    failures.push("the second click did not POST {} to NERVIS's sign-out route with the control header.");
  }
  expect("after Sign out", slotOf(page), ["Signed out.", ">Sign in with ChatGPT<"], [HINT]);
}

async function confirmSendsTheHintShown() {
  const confirmed = codex("signed_in", "Codex is signed in.", { account: ACCOUNT });
  const page = world({
    state: codex("account_changed", "A different account.", { account: { ...ACCOUNT, fingerprint_matches: false } }),
    control: { "POST /account/confirm": () => answer(200, confirmed) } });
  await credentials(page);
  await run(page, CONFIRM);
  const sent = page.sent.find((call) => call.route === "POST /account/confirm");
  if (!sent || JSON.stringify(sent.body) !== JSON.stringify({ email_hint: HINT })) {
    failures.push(`This is my account sent ${JSON.stringify(sent && sent.body)}, not the hint the card showed.`);
  }
  expect("after confirming", slotOf(page), ["Account confirmed.", ">Sign out…<"], [CONFIRM]);
}

/* 5, 6 — refusals. */
async function refusalsAreSaidPlainly() {
  const busy = world({ state: codex("signed_out", "Codex is signed out."), control: {
    "POST /sign-in": () => answer(409, { error: { code: "CODEX_SIGN_IN_PORT_BUSY",
      message: "Another program is holding the sign-in ports 1455 and 1457.", retryable: false, details: {} } }),
  } });
  await credentials(busy);
  await run(busy, START);
  if (!busy.tabs[0] || !busy.tabs[0].closed) failures.push("a refused sign-in left its empty tab open.");
  expect("the sign-in ports held", slotOf(busy),
    ["holding the sign-in ports 1455 and 1457", "ChatGPT app", ">Try again<"], ["signing in"]);

  const plain = world({ state: codex("signed_out", "Codex is signed out."), control: {
    "POST /sign-in": () => {
      plain.ravis.state = codex("signed_out", "Codex is signed out.", { sign_in: waitingPublic() });
      return answer(202, waitingAdmin("http://auth.example.invalid/steal"));
    },
    "GET /sign-in": () => answer(200, waitingAdmin("http://auth.example.invalid/steal")),
  } });
  await credentials(plain);
  await run(plain, START);
  if (!plain.tabs[0] || !plain.tabs[0].closed || plain.tabs[0].location.href) {
    failures.push("a sign-in address that is not https was opened.");
  }
  expect("an address that is not https", slotOf(plain), ["https, so this page won"], ["auth.example.invalid"]);
  run(plain, "CODEX_SIGN_IN.stop()");

  const refused = world({ state: codex("signed_out", "Codex is signed out.", { sign_in: waitingPublic() }),
    control: { "GET /sign-in": () => answer(403, { message: "NERVIS holds no admin credential for RAVIS" }) } });
  await credentials(refused);
  expect("the admin route refusing the link", slotOf(refused), ["admin key", "signing in"], ["href="]);
  run(refused, "CODEX_SIGN_IN.stop()");

  const old = world({ state: { detail: "Not Found" }, codexStatus: 404 });
  const html = await credentials(old);
  expect("a RAVIS from before the sign-in", html,
    ["ChatGPT subscription (Codex)", "offer the Codex sign-in yet"], [START]);
}

/* 7 — markup RAVIS could send, everywhere this card puts RAVIS's words. */
async function nothingInjects() {
  const tag = ' <vxs onerror=vxjs>" vxatr=vxjs';
  const poisoned = { ...ACCOUNT, email_hint: HINT + tag, plan: "plus" + tag, fingerprint: "weak" };
  const cases = [
    world({ state: codex("account_changed", "Different" + tag, { account: poisoned }) }),
    world({ state: codex("signed_out" + tag, "Out" + tag, { sign_in: { ...RESTARTED, error: "lost" + tag } }) }),
    world({ state: codex("signed_out", "Out", { sign_in: waitingPublic() }),
      control: { "GET /sign-in": () => answer(200, waitingAdmin(AUTH_URL + '" onmouseover="vxjs' + tag)) } }),
  ];
  for (const page of cases) {
    await credentials(page);
    const html = slotOf(page);
    if (!html.includes("vxs")) failures.push("the injection probe never reached the Codex card, so it proved nothing.");
    if (html.includes("<vxs") || /"\s*(vxatr|onmouseover)=/.test(html)) {
      const at = Math.max(0, html.search(/<vxs|"\s*(vxatr|onmouseover)=/) - 80);
      failures.push(`RAVIS's text broke out of the Codex card's markup: …${html.slice(at, at + 140)}…`);
    }
    run(page, "CODEX_SIGN_IN.stop()");
  }
}

/* 8 — the allowance: a tile beside Spend on RAVIS → Dashboard, and a line on the Overview. */
const USAGE_READ = { routed: 3, decisions_recorded: 3, no_route: 0, local_share: 0.5, executed: 2,
  cost_available: false, cost_detail: "nothing has been priced yet", calls_priced: 0,
  calls_unpriced: 0, spend_window: "24h" };
const windowsLeft = (primary, weekly) => [
  { id: "primary", label: "5-hour window", duration_minutes: 300, used_percent: 100 - primary,
    remaining_percent: primary, resets_at: later(2 * 3600 + 10 * 60) },
  { id: "secondary", label: "weekly window", duration_minutes: 10080, used_percent: 100 - weekly,
    remaining_percent: weekly, resets_at: later(4 * 86400) },
];
const knownUsage = (primary, weekly, extra = {}) => ({ known: true, source: "notification",
  observed_at: later(-120), stale: false, allowance_not_cost: true, limit_reached: null,
  spend_control_reached: null, windows: windowsLeft(primary, weekly), individual_limit: null,
  credits: null, ...extra });

async function dashboard(state, codexStatus = 200) {
  const page = world({ state, codexStatus, reads: { "/api/v1/relay/ravis/api/v1/usage": USAGE_READ } });
  await quiet();
  page.exported.state.app = "ravis";
  page.exported.state.view = "Dashboard";
  await run(page, "ravis()");
  const content = page.elements.get("sel:#content");
  const html = content ? content.innerHTML : "";
  const tiles = html.split('<div class="card kpi').slice(1).map((chunk) => {
    const own = chunk.split('<div class="card ')[0];
    return { title: (own.match(/<h3>([^<]*)<\/h3>/) || [])[1], html: own };
  });
  return { html, tiles, codex: (tiles.find((tile) => tile.title === "Codex") || { html: "" }).html };
}

async function overview(state, codexStatus = 200) {
  const page = world({ state, codexStatus });
  await quiet();
  page.exported.state.app = "nervis";
  page.exported.state.view = "Overview";
  await run(page, "nervis()");
  const content = page.elements.get("sel:#content");
  const html = content ? content.innerHTML : "";
  const card = html.split("<h3>Codex</h3>")[1];
  return card == null ? null : card.split('<div class="card ')[0];
}

/* The tile's visible part, and its tooltip: the tooltip is the tile's last element. */
function tileParts(tile) {
  const at = tile.indexOf('<div class="kpi-tip"');
  return { visible: at < 0 ? tile : tile.slice(0, at), tip: at < 0 ? "" : tile.slice(at) };
}

/* The owner's rule (13 September 2026): the tile keeps its neighbours' height, so its
   visible part is the figure and one line; no row of its own under them. */
function compact(label, visible) {
  if (/<div\b/.test(visible) || (visible.match(/<small\b/g) || []).length > 1) {
    failures.push(`${label}: the visible tile grew rows of its own; the details belong in the tooltip.`);
  }
}

async function theAllowanceSitsBesideSpend() {
  const signedIn = codex("signed_in", "Codex is signed in with a ChatGPT Plus plan.",
    { account: ACCOUNT, usage: knownUsage(62, 80) });
  const shown = await dashboard(signedIn);
  const titles = shown.tiles.map((tile) => tile.title);
  if (JSON.stringify(titles) !== JSON.stringify(["Decisions", "Local", "Spend", "Codex"])) {
    failures.push(`RAVIS → Dashboard's headline tiles are ${JSON.stringify(titles)}; the owner's layout is Decisions, Local, Spend, Codex.`);
  }
  if (shown.html.includes("Active profile")) failures.push("RAVIS → Dashboard still draws the Active profile tile.");
  if (!shown.html.includes("resetSpend()")) failures.push("the Spend tile lost its Reset to 0.");

  const { visible, tip } = tileParts(shown.codex);
  expect("the Codex tile, signed in", visible,
    ["62% left", "5-hour · resets in 2 h 10 min", 'aria-describedby="codex-allowance-tip"'],
    ["weekly", "ChatGPT Plus plan", HINT, "not money", "is signed in with", 'class="chip']);
  compact("the Codex tile, signed in", visible);
  expect("the Codex tooltip, signed in", tip,
    ['role="tooltip"', 'id="codex-allowance-tip"', "Codex is signed in with a ChatGPT Plus plan.",
     "ChatGPT Plus plan", HINT, "weekly window: <b>80% left</b>", "not money"]);
  if (!/5-hour window: <b>62% left<\/b> · resets (Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d{1,2} [A-Z][a-z]{2} \d\d:\d\d \(in 2 h 10 min\)/.test(tip)) {
    failures.push("the Codex tooltip does not give the 5-hour window's exact reset day, date and time.");
  }
  if (/[$€£]|spent|cost|priced/i.test(shown.codex.replace(/<[^>]*>/g, ""))) {
    failures.push("the Codex tile carries money wording, beside a tile that is money.");
  }
  const source = require("fs").readFileSync(require("path").join(__dirname, "..", "index.html"), "utf8");
  for (const [rule, how] of [[".card.kpi:hover .kpi-tip", "hover"], [".card.kpi:focus-within .kpi-tip", "keyboard focus"]]) {
    if (!source.includes(rule)) failures.push(`the Codex tooltip does not open on ${how}: no ${rule} rule.`);
  }

  const cases = [
    ["signed in, allowance not read yet", codex("signed_in", "Codex is signed in.", { account: ACCOUNT }),
     ["unknown", "not read yet"], ["no figure is shown", "Codex is signed in."]],
    ["signed out", codex("signed_out", "Codex is signed out."),
     ["signed out", "go('ravis','Credentials')"], ["Codex is signed out.", "not money"]],
    ["not installed", codex("not_installed", "The Homebrew link does not lead to a file."),
     ["not installed"], ["does not lead to a file"]],
  ];
  for (const [label, state, onTile, inTip] of cases) {
    const parts = tileParts((await dashboard(state)).codex);
    expect(`the Codex tile, ${label}`, parts.visible, onTile);
    expect(`the Codex tooltip, ${label}`, parts.tip, inTip);
    compact(`the Codex tile, ${label}`, parts.visible);
    if (/\d\s*%/.test(parts.visible + parts.tip)) failures.push(`the Codex tile, ${label}: draws a percentage RAVIS did not give.`);
  }

  const usedUp = tileParts((await dashboard(codex("quota_exhausted", "The allowance is used up until 04:30.",
    { account: ACCOUNT, usage: knownUsage(0, 80) }))).codex);
  expect("the Codex tile, used up", usedUp.visible, ["0% left", ">used up<"], ["used up until 04:30"]);
  expect("the Codex tooltip, used up", usedUp.tip, ["allowance used up", "used up until 04:30"]);

  const paused = tileParts((await dashboard(codex("untested_version", "The file rules are not yet proven.",
    { account: ACCOUNT, usage: knownUsage(62, 80) }))).codex);
  expect("the Codex tile, paused", paused.visible, ["62% left", ">paused<"], ["not yet proven"]);

  const stale = tileParts((await dashboard(codex("signed_in", "Codex is signed in.",
    { account: ACCOUNT, usage: knownUsage(62, 80, { stale: true, observed_at: later(-42 * 60) }) }))).codex);
  expect("the Codex tile, stale", stale.visible, ["62% left", ">stale<"], ["last read"]);
  compact("the Codex tile, stale", stale.visible);
  expect("the Codex tooltip, stale", stale.tip, ["last read 42 min ago", "may be out of date"]);

  for (const [label, status] of [["a RAVIS from before Codex", 404], ["RAVIS not answering the read", 0]]) {
    const tile = (await dashboard(codex("signed_in", "x"), status)).codex;
    if (!tile) failures.push(`the Codex tile, ${label}: not drawn at all; it should be drawn absent.`);
    if (/% left/.test(tile)) failures.push(`the Codex tile, ${label}: still draws an allowance.`);
    if (status === 404) expect(`the Codex tile, ${label}`, tile, ["report Codex"]);
  }

  const tag = ' <vxs onerror=vxjs>" vxatr=vxjs';
  const poisoned = await dashboard(codex("untested_version" + tag, "In" + tag, {
    account: { ...ACCOUNT, email_hint: HINT + tag, plan: "plus" + tag },
    usage: { ...knownUsage(62, 80), stale: true, observed_at: "then" + tag,
             windows: windowsLeft(62, 80).map((w) => ({ ...w, label: w.label + tag, resets_at: "soon" + tag })) } }));
  if (!poisoned.codex.includes("vxs")) failures.push("the injection probe never reached the Codex tile.");
  if (/<vxs|"\s*vxatr=/.test(poisoned.codex)) failures.push("RAVIS's text broke out of the Codex tile's markup.");
}

async function theOverviewCarriesOneLine() {
  const line = await overview(codex("signed_in", "Codex is signed in.", { account: ACCOUNT, usage: knownUsage(62, 80) }));
  if (line == null) {
    failures.push("the Overview draws no Codex line.");
    return;
  }
  expect("the Overview's Codex line", line, ["signed in", "62% left in the 5-hour window", "resets"]);
  const unknown = await overview(codex("signed_in", "Codex is signed in.", { account: ACCOUNT }));
  expect("the Overview's Codex line, allowance unknown", unknown || "", ["allowance unknown"], ["%"]);
  const down = await overview(codex("signed_in", "x", { account: ACCOUNT, usage: knownUsage(62, 80) }), 0);
  if (down == null || /% left/.test(down)) {
    failures.push("with RAVIS not answering, the Overview's Codex line is missing or still draws an allowance.");
  }
}

async function main() {
  await everyStateDrawsItsWords();
  await theAllowanceSitsBesideSpend();
  await theOverviewCarriesOneLine();
  await signingInFromTheButton();
  await aRestartDuringTheSignInIsSaid();
  await cancelSendsDelete();
  await signOutTakesTwoClicks();
  await confirmSendsTheHintShown();
  await refusalsAreSaidPlainly();
  await nothingInjects();

  if (failures.length) {
    console.error("codex check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "Codex sign-in on RAVIS → Credentials holds: every state draws its words and only the " +
    "buttons RAVIS accepts, the tab opens inside the click and closes on a refusal or a " +
    "non-https address, the waiting sign-in shows its link, time left and Cancel and redraws " +
    "when it ends, every write goes through NERVIS with the control header, Sign out takes " +
    "two clicks, the link never travels through the relay, and nothing RAVIS sends injects; " +
    "RAVIS → Dashboard's row is Decisions, Local, Spend, Codex, whose tile shows each window's " +
    "allowance and reset, unknown with no percentage, stale with its age, no money, and goes " +
    "absent without RAVIS; the Overview carries its line; and a stored key's dot is not escaped"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
