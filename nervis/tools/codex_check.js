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
 *
 * And the Codex card on RAVIS → Dashboard (N2b, 14 September 2026):
 *
 *   8. **Every task RAVIS lists is said in words** — each state in RAVIS's own list, how long
 *      it has waited, its model and effort when RAVIS gives them and nothing when it doesn't,
 *      and "reconnecting" only while RAVIS reopens it; every Codex state word too.
 *   9. **Stop is the only task control.** Stop… sits only beside a task RAVIS would stop, takes
 *      two clicks and never `confirm()`, and sends one POST to NERVIS's Stop route with the
 *      control header, the folder and turn its row showed, and the page's Idempotency-Key —
 *      the same key when the click is retried, another for another task. No button reads
 *      Approve, Allow, Answer, Steer, Re-test, Start, Continue or Settle, and no request reaches
 *      an agent-session, project-lock, re-test or calibration route, or allows a site.
 *  10. **Every refusal and every missing answer is said plainly**, and a refused Stop reads the
 *      tasks again.
 *  11. **Remove sits beside the sites the owner added and never beside a default**, which stay
 *      folded away; it takes two clicks and redraws from RAVIS's answer. A new Codex build's
 *      report comes from NERVIS's control route, says each check and what changed, and says
 *      before Use this version that accepting neither starts the re-test nor spends allowance.
 *  12. **Nothing RAVIS sends can inject markup** into the card, nor break out of a quoted
 *      handler argument; and the Overview's line counts the tasks and opens the card.
 *
 * And the card's line to the Skills page (NERVIS 0.32.0):
 *
 *  13. **The card points to NERVIS → Skills** in one line, where Codex's skills moved with a switch
 *      for the other models beside each; the card neither reads nor switches skills itself.
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
    backend_id: "ravis/clarvis-codex", execution: "delegated_agent", roles: ["agent"], state, reason,
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
   answers; `ravis.sites`, when given, answers the relayed `GET /api/v1/codex/sites`;
   `ravis.control["POST /sign-in"]` and its siblings answer NERVIS's control routes. Every
   Codex control call is logged in `sent`, with how many tabs were open when it was sent,
   and every request of any kind in `requests`, so a request the card must never make is
   seen wherever it was aimed. */
function world({ state, codexStatus = 200, control = {}, reads = {}, sites = null }) {
  const ravis = { state, codexStatus, control, sites, codexReads: 0 };
  const sent = [];
  const requests = [];
  const tabs = [];
  const fetchImpl = (url, init = {}) => {
    const address = String(url);
    const method = (init.method || "GET").toUpperCase();
    requests.push({ address, method });
    if (address.endsWith("/api/v1/relay/ravis/api/v1/providers/credentials")) {
      return answer(200, { items: [KEY_ROW] });
    }
    const read = Object.keys(reads).find((path) => address.includes(path));
    if (read) return answer(200, reads[read]);
    if (address.endsWith("/api/v1/relay/ravis/api/v1/codex/sites")) {
      return ravis.sites ? ravis.sites() : answer(200, { defaults: DEFAULT_SITES, added: [] });
    }
    if (address.endsWith("/api/v1/relay/ravis/api/v1/codex")) {
      ravis.codexReads += 1;
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
  return { ...loaded, ravis, sent, requests, tabs };
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

/* 9 — RAVIS → Pools: a read-only Clarvis Codex row beside the two Clarvis pools. The owner
   looked for Codex there, didn't find it and took it for unbuilt (13 September 2026). */
const POOLS_READ = { items: [
  { pool_id: "ravis/auto", requirements: { required: [], minimum_context: 0 }, member_count: 5, available: true },
  { pool_id: "ravis/clarvis-agent", requirements: { required: ["tool_use"], minimum_context: 32768 },
    member_count: 2, available: true },
  { pool_id: "ravis/clarvis-chat", requirements: { required: [], minimum_context: 0 }, member_count: 3, available: true },
  { pool_id: "ravis/local", requirements: { required: [], minimum_context: 0 }, member_count: 1, available: true },
] };

async function poolsScreen(state, codexStatus = 200) {
  const page = world({ state, codexStatus, reads: { "/api/v1/relay/ravis/api/v1/pools": POOLS_READ } });
  await quiet();
  page.exported.state.app = "ravis";
  page.exported.state.view = "Pools";
  await run(page, "ravisPools()");
  const content = page.elements.get("sel:#content");
  const html = content ? content.innerHTML : "";
  const start = html.indexOf('<div class="table-row five codex-pool">');
  const ends = ['<div class="table-row five">', "<b>Reason</b> is filled"]
    .map((mark) => html.indexOf(mark, start + 1)).filter((at) => at > start);
  const row = start < 0 ? "" : html.slice(start, ends.length ? Math.min(...ends) : html.length);
  return { html, row, at: start };
}

async function codexSitsBesideTheClarvisPools() {
  const paused = await poolsScreen(codex("untested_version", "The file rules are not yet proven.",
    { account: ACCOUNT, usage: knownUsage(44, 80) }));
  if (!paused.row) {
    failures.push("RAVIS → Pools draws no Clarvis Codex row.");
    return;
  }
  expect("the Pools screen's Codex row, paused", paused.row,
    ["Clarvis Codex", "ravis/clarvis-codex", "read-only", "coding tasks through your ChatGPT plan, not chats",
     "no fallback", "44% left", ">paused<", 'aria-describedby="codex-allowance-tip"'],
    ["POOLEDIT", "ravis/codex<", "80% left · "]);
  /* By each pool's own row link: "ravis/local" is also named in every pool picker's note. */
  const chat = paused.html.indexOf("POOLEDIT.toggle('clarvis-chat')");
  const local = paused.html.indexOf("POOLEDIT.toggle('local')");
  if (!(chat >= 0 && chat < paused.at && paused.at < local)) {
    failures.push("the Codex row is not placed straight after the Clarvis pools.");
  }
  if (!paused.html.includes("published stable ids") || /<strong[^>]*>5<\/strong>/.test(paused.html)) {
    failures.push("the Pools tile counted Codex as a pool.");
  }
  const source = require("fs").readFileSync(require("path").join(__dirname, "..", "index.html"), "utf8");
  if (!source.includes(".table-row.codex-pool:hover .kpi-tip")) {
    failures.push("the Codex row's tooltip does not open on hover: no .table-row.codex-pool:hover .kpi-tip rule.");
  }

  const signedOut = (await poolsScreen(codex("signed_out", "Codex is signed out."))).row;
  expect("the Pools screen's Codex row, signed out", signedOut,
    [">signed out<", "go('ravis','Credentials')"]);
  if (/\d\s*%/.test(signedOut)) failures.push("the Codex row, signed out: draws a percentage RAVIS did not give.");

  for (const [label, status, words] of [["RAVIS not answering", 0, "RAVIS"], ["a RAVIS from before Codex", 404, "report Codex"]]) {
    const row = (await poolsScreen(codex("signed_in", "x", { account: ACCOUNT, usage: knownUsage(44, 80) }), status)).row;
    if (!row) failures.push(`the Codex row, ${label}: not drawn; it should say why it has nothing.`);
    expect(`the Codex row, ${label}`, row, ["Clarvis Codex", "<span>—</span>", words], ["% left", 'class="chip']);
  }

  const tag = ' <vxs onerror=vxjs>" vxatr=vxjs';
  const poisoned = (await poolsScreen(codex("untested_version" + tag, "In" + tag, { account: ACCOUNT,
    usage: { ...knownUsage(44, 80), windows: windowsLeft(44, 80).map((w) => ({ ...w, label: w.label + tag })) } }))).row;
  if (!poisoned.includes("vxs")) failures.push("the injection probe never reached the Pools screen's Codex row.");
  if (/<vxs|"\s*vxatr=/.test(poisoned)) failures.push("RAVIS's text broke out of the Pools screen's Codex row.");
}

/* 10 — the Codex card on RAVIS → Dashboard (N2b, 14 September 2026): each task RAVIS lists with
   its state, waiting time, model and effort, and "reconnecting"; Stop, the only task control, in
   two clicks, carrying the confirmation and the page's own Idempotency-Key; the allowed sites, with
   Remove beside the owner's only; and a new build's report with Use this version, in two clicks. */
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
/* RAVIS's contract, read rather than copied, so a state or check RAVIS adds reaches this gate. */
const RELAY_CONTRACT = join(__dirname, "..", "..", "ravis", "tests", "fixtures", "relay-contract");
const STATE_CONTRACT = JSON.parse(readFileSync(join(RELAY_CONTRACT, "codex-state.json"), "utf8"));
const ADMIN_CONTRACT = JSON.parse(readFileSync(join(RELAY_CONTRACT, "codex-admin.json"), "utf8"));
const VERSION_REPORT = ADMIN_CONTRACT.routes
  .find((route) => route.method === "GET" && route.path === "/api/v1/codex/version-check")
  .examples[0].response.body;
const SID_A = "as_01J9ZK4T6Q8M2V7R3N5B1C0D";
const SID_B = "as_01J9ZK7W1X2Y3Z4A5B6C7D8E";
const TURN_A = "019a1c2e-8c4f-7a21-b5d3-3e6c9b7f2a41";
const TURN_B = "019a1c30-2e3f-7d4c-b5a6-7f8e9d0c1b23";
const KEY_SHAPE = /^[A-Za-z0-9_-]{16,128}$/;
const DEFAULT_SITES = ["registry.npmjs.org", "pypi.org", "*.crates.io", "github.com"];
const SHA_NEW = VERSION_REPORT.version_check.sha256;
/* Each task state RAVIS lists, with the words the card must say for it: written out, and held
   against RAVIS's own list, so a state RAVIS adds fails here until the card words it. */
const RUN_WORDS = {
  running: "running", waiting_on_you: "waiting for your answer",
  paused_unanswered: "paused — waited 30 min", paused_for_update: "paused — Codex updated",
  completed_needs_review: "finished — needs review", uncertain: "uncertain",
  leftover: "processes left over", clarvis_engine: "Clarvis's own engine",
};

const refusal = (code, message, details = {}) =>
  ({ error: { code, message, retryable: false, details } });
const task = (extra = {}) => ({
  id: SID_A, turn_id: TURN_A, project: "add-utc-demo", state: "running", since: later(-42 * 60),
  age_minutes: 42, waiting_minutes: null, attached_windows: 1, paused_reason: null,
  model: "gpt-6-astra", effort: "medium", reopening: null, ...extra,
});
const busy = (runs, extra = {}) => codex("signed_in", "Codex is signed in with a ChatGPT Plus plan.",
  { account: ACCOUNT, usage: knownUsage(62, 80), runs, ...extra });
const NEW_BUILD = { source: "homebrew", version: "0.155.0", installed_sha256: SHA_NEW,
                    running_sha256: SHA_NEW, verdict: "untested", strict_rules: "unproven" };
const untested = (runtime = {}, extra = {}) => codex("untested_version",
  "Codex changed (now 0.155.0) and needs re-testing before new work.",
  { account: ACCOUNT, usage: knownUsage(62, 80), runtime: { ...NEW_BUILD, ...runtime }, ...extra });
const failing = () => Promise.reject(new TypeError("fetch failed"));
const answering = (status, body) => (status ? answer(status, body) : failing());

/* The words a person reads: the page escapes ' and " as it writes them. The injection checks read
   the markup itself, never this. */
const readable = (html) => html.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
const words = (label, html, present, absent = []) => expect(label, readable(html), present, absent);
const buttons = (html) =>
  [...html.matchAll(/<button\b[^>]*>([^<]*)<\/button>/g)].map((found) => readable(found[1]).trim());
const calls = (page, route) => page.sent.filter((call) => call.route === route);
const stopOf = (id) => `CODEX_CARD.stop(${JSON.stringify(id)})`;
const removeOf = (host) => `CODEX_CARD.remove(${JSON.stringify(host)})`;

async function drawCard(state, { control = {}, sites = null, codexStatus = 200, random = true } = {}) {
  const page = world({ state, codexStatus, control, sites,
                       reads: { "/api/v1/relay/ravis/api/v1/usage": USAGE_READ } });
  // The browser's random source, which the harness doesn't carry; left out, the page's fallback runs.
  if (random) page.context.crypto = require("node:crypto").webcrypto;
  await quiet();
  page.exported.state.app = "ravis";
  page.exported.state.view = "Dashboard";
  await run(page, "ravis()");
  return page;
}

/* The card's inner slot: cut out of the screen on the first draw, and the slot's own markup once a
   click has redrawn it (the harness doesn't parse markup, as `slotOf` above explains). */
function cardOf(page) {
  const slot = page.elements.get("codex-card-body");
  if (slot && slot.innerHTML) return slot.innerHTML;
  const content = page.elements.get("sel:#content");
  const html = content ? content.innerHTML : "";
  const start = html.indexOf('<div id="codex-card-body">');
  if (start < 0) return "";
  const end = html.indexOf('<div class="card ', start);
  return html.slice(start, end < 0 ? html.length : end);
}

async function everyCodexStateIsWorded() {
  const page = world({ state: busy([]) });
  await quiet();
  for (const { state } of STATE_CONTRACT.states) {
    const chip = readable(run(page, `codexStateChip(${JSON.stringify(state)})`));
    if (state.includes("_") && chip.includes(state)) failures.push(`Codex's state ${state} is drawn as its raw word.`);
  }
  for (const word of STATE_CONTRACT.run_states) {
    const shown = { id: SID_A, turn_id: TURN_A, state: word };
    const chip = readable(run(page, `codexTaskStateChip(${JSON.stringify(shown)})`));
    if (!Object.hasOwn(RUN_WORDS, word)) failures.push(`RAVIS lists a task state this gate doesn't word: ${word}.`);
    else if (!chip.includes(RUN_WORDS[word]) || (word.includes("_") && chip.includes(word))) {
      failures.push(`a ${word} task is not said in words: ${chip}.`);
    }
  }
}

async function theTaskListSaysEveryTask() {
  for (const word of Object.keys(RUN_WORDS)) {
    const engine = word === "clarvis_engine";
    const listed = task(engine ? { state: word, id: null, turn_id: null, model: null, effort: null }
                               : { state: word });
    const html = cardOf(await drawCard(busy([listed])));
    words(`a ${word} task`, html, ["add-utc-demo", RUN_WORDS[word]], word.includes("_") ? [word] : []);
    const stops = buttons(html).filter((label) => label === "Stop…").length;
    const offered = word === "running" || word === "waiting_on_you" ? 1 : 0;
    if (stops !== offered) failures.push(`a ${word} task draws ${stops} Stop… buttons, not ${offered}.`);
  }

  const reopening = { group_id: "sg_01J9ZKE8F9G0H1J2K3L4M5N6P7", hosts: ["download.pytorch.org"],
                      since: later(-60) };
  const rich = cardOf(await drawCard(busy([
    task({ state: "waiting_on_you", waiting_minutes: 12, attached_windows: 0 }),
    task({ id: SID_B, turn_id: TURN_B, project: "weather-cli", effort: null, age_minutes: 14,
           attached_windows: 2, reopening }),
  ])));
  words("the task list", rich, ["waiting 12 min", "no editor open", "gpt-6-astra · medium effort",
    "gpt-6-astra · default effort", "started 14 min ago", "2 editors open", "reconnecting",
    "download.pytorch.org", "Answer a Codex task in Clarvis"]);
  if ((rich.match(/>reconnecting</g) || []).length !== 1) {
    failures.push("the task list says reconnecting beside a task RAVIS isn't reopening, or beside none.");
  }

  const older = task();
  for (const field of ["model", "effort", "reopening"]) delete older[field];
  words("a task from a RAVIS before 0.25.1", cardOf(await drawCard(busy([older]))),
    ["add-utc-demo", "running"], ["medium effort", "default effort", "default model", "reconnecting",
                                  "undefined", "null"]);
  words("no tasks", cardOf(await drawCard(busy([]))), ["No Codex task is running or waiting."], ["Stop…"]);
  words("a RAVIS that listed no tasks", cardOf(await drawCard(busy(null))),
    ["didn't list Codex's tasks"], ["Stop…"]);
  for (const [label, status, reason] of [["a RAVIS from before Codex", 404, "report Codex"],
                                         ["RAVIS not answering", 0, "did not answer"]]) {
    const html = cardOf(await drawCard(busy([task()]), { codexStatus: status }));
    if (!html) failures.push(`the Codex card, ${label}: not drawn; it should say why it has nothing.`);
    words(`the Codex card, ${label}`, html, [reason], ["add-utc-demo", "Stop…", "allowed sites"]);
  }
}

async function stopIsTheOnlyTaskControl() {
  const view = { defaults: DEFAULT_SITES, added: ["download.pytorch.org"] };
  const runs = Object.keys(RUN_WORDS).map((word, at) => task({
    state: word, project: `project-${at}`,
    id: word === "clarvis_engine" ? null : `as_01J9ZK4T6Q8M2V7R3N5B1C${String(at).padStart(2, "0")}`,
  }));
  const first = runs[0].id;
  const page = await drawCard(untested({}, { runs }), {
    sites: () => answer(200, view),
    control: {
      [`POST /runs/${first}/stop`]: () => answer(202, { state: "stopping" }),
      "DELETE /sites/download.pytorch.org": () => answer(200, { ...view, added: [] }),
      "GET /version-check": () => answer(200, VERSION_REPORT),
      "POST /accept-version": () => answer(200, untested({ verdict: "accepted" }, { runs })),
    },
  });
  const labels = new Set();
  const called = new Set();
  const look = () => {
    const html = cardOf(page);
    buttons(html).forEach((label) => labels.add(label));
    for (const handler of html.matchAll(/\bon[a-z]+="([^"]*)"/g)) {
      for (const name of handler[1].matchAll(/([A-Za-z_$][\w$.]*)\s*\(/g)) called.add(name[1]);
    }
  };
  look();
  await run(page, "CODEX_CARD.check()");
  look();
  const clicks = [stopOf(first), removeOf("download.pytorch.org"), "CODEX_CARD.accept()"];
  for (const click of clicks) {
    await run(page, click); // the first click arms, and says what the second would do
    look();
    await run(page, "CODEX_CARD.disarm()");
  }
  for (const click of clicks) {
    await run(page, click);
    await run(page, click); // and the second sends it
    look();
  }

  const allowed = /^(Stop…|Remove|Check this version|Use this version…|Keep it running|Keep it|Not now|Click again to (stop the task in|remove|accept Codex) .+)$/;
  for (const label of labels) {
    if (!allowed.test(label)) failures.push(`the Codex card draws a button it has no business drawing: ${JSON.stringify(label)}.`);
    if (/\b(approve|allow|answer|steer|re-?test|start|continue|carry on|settle|run)\b/i.test(label)) {
      failures.push(`the Codex card offers a task control beside Stop: ${JSON.stringify(label)}.`);
    }
  }
  for (const needed of ["Stop…", "Remove", "Check this version", "Use this version…"]) {
    if (!labels.has(needed)) failures.push(`the task-control check never saw ${needed}, so it proved nothing about it.`);
  }
  const own = ["CODEX_CARD.stop", "CODEX_CARD.remove", "CODEX_CARD.disarm", "CODEX_CARD.check", "CODEX_CARD.accept",
               "CODEX_CARD.openSkills"];
  for (const name of called) {
    if (!own.includes(name)) failures.push(`a control on the Codex card calls ${name}, which isn't one of the card's own.`);
  }
  for (const request of page.requests) {
    if (/agent-sessions|project-locks|reprove|calibration/.test(request.address)) {
      failures.push(`the Codex card sent ${request.method} ${request.address}, a route NERVIS never reaches.`);
    }
    if (request.method !== "GET" && request.address.includes("/relay/")) {
      failures.push(`the Codex card wrote through the read-only relay: ${request.method} ${request.address}.`);
    }
    if (request.method === "POST" && request.address.includes("/codex/sites")) {
      failures.push("the Codex card tried to allow a site; only the owner's click in Clarvis adds one.");
    }
  }
  const tasks = page.sent.filter((call) => /^POST \/runs\//.test(call.route || ""));
  if (tasks.length !== 1 || tasks[0].route !== `POST /runs/${first}/stop`) {
    failures.push(`the Codex card's task requests were ${JSON.stringify(tasks.map((call) => call.route))}, not the one Stop.`);
  }
}

async function stopSendsTheConfirmationAndThePagesOwnKey() {
  let tries = 0;
  const runs = [task({ state: "waiting_on_you", waiting_minutes: 12 }),
                task({ id: SID_B, turn_id: TURN_B, project: "weather-cli" })];
  const page = await drawCard(busy(runs), { control: {
    [`POST /runs/${SID_A}/stop`]: () => (++tries === 1
      ? answer(502, { message: "RAVIS did not answer: ConnectTimeout" })
      : answer(202, { state: "stopping" })),
    [`POST /runs/${SID_B}/stop`]: () => answer(202, { state: "stopping" }),
  } });

  await run(page, stopOf(SID_A));
  if (calls(page, `POST /runs/${SID_A}/stop`).length) failures.push("one click on Stop… sent the stop; it takes two.");
  words("Stop, armed", cardOf(page), ["Click again to stop the task in add-utc-demo", "Keep it running"]);
  await run(page, stopOf(SID_A));
  const [sentOnce] = calls(page, `POST /runs/${SID_A}/stop`);
  if (!sentOnce) {
    failures.push("the second click on Stop sent nothing to NERVIS's Stop route.");
    return;
  }
  const key = sentOnce.headers["Idempotency-Key"];
  if (!("x-nervis-control" in sentOnce.headers)) failures.push("the Stop carried no control header, so NERVIS refuses it.");
  if (!KEY_SHAPE.test(key || "")) failures.push(`the Stop's Idempotency-Key ${JSON.stringify(key)} isn't 16 to 128 letters, digits, - or _.`);
  if (JSON.stringify(sentOnce.body) !== JSON.stringify({ project: "add-utc-demo", turn_id: TURN_A })) {
    failures.push(`the Stop sent ${JSON.stringify(sentOnce.body)}, not the folder and turn its row showed.`);
  }
  words("a Stop RAVIS didn't answer", cardOf(page), ["didn't answer", "Click Stop again"], ["stopping…"]);

  await run(page, stopOf(SID_A));
  await run(page, stopOf(SID_A));
  const retried = calls(page, `POST /runs/${SID_A}/stop`);
  if (retried.length !== 2 || retried[1].headers["Idempotency-Key"] !== key) {
    failures.push("a retried Stop didn't reuse the page's Idempotency-Key, so RAVIS could act on it twice.");
  }
  const stopped = cardOf(page);
  words("a Stop RAVIS accepted", stopped, ["stopping…", "Stopping Codex's task in add-utc-demo"]);
  if (stopped.includes(`CODEX_CARD.stop('${SID_A}')`)) failures.push("a task already stopping still offers Stop.");

  await run(page, stopOf(SID_B));
  await run(page, stopOf(SID_B));
  const [other] = calls(page, `POST /runs/${SID_B}/stop`);
  if (!other || other.headers["Idempotency-Key"] === key) failures.push("another task's Stop reused the first task's Idempotency-Key.");

  page.ravis.state = busy([task({ state: "completed_needs_review" }), runs[1]]);
  await run(page, "CODEX_CARD.redraw()");
  words("a task stopped from this page", cardOf(page), ["stopped — open the project in an editor"]);
  for (const id of [SID_A, "as_01J9ZK0000000000UNKNOWN0"]) {
    await run(page, stopOf(id));
    await run(page, stopOf(id));
  }
  if (calls(page, `POST /runs/${SID_A}/stop`).length !== 2 || page.sent.some((call) => call.route.includes("UNKNOWN"))) {
    failures.push("Stop was sent for a task that is no longer running, or one the card never showed.");
  }

  const plain = await drawCard(busy(runs), { random: false,
    control: { [`POST /runs/${SID_A}/stop`]: () => answer(202, { state: "stopping" }) } });
  await run(plain, stopOf(SID_A));
  await run(plain, stopOf(SID_A));
  const [fallback] = calls(plain, `POST /runs/${SID_A}/stop`);
  if (!fallback || !KEY_SHAPE.test(fallback.headers["Idempotency-Key"] || "")) {
    failures.push("without the browser's random source, Stop sent no usable Idempotency-Key.");
  }
}

async function everyStopRefusalIsSaid() {
  const cases = [
    ["the task changed", 409, refusal("CONFIRMATION_MISMATCH", "That task changed; refresh and try again."),
     ["The task changed", "The list is fresh now"]],
    ["nothing running", 409, refusal("NOTHING_RUNNING", "That task isn't running."), ["isn't running any more"]],
    ["an unknown task", 404, refusal("AGENT_SESSION_NOT_FOUND", "No such agent session."), ["no longer has that task"]],
    ["RAVIS refusing NERVIS's key", 403, refusal("OWNER_STOP_NOT_ALLOWED", "Only the owner's menu bar or the dashboard may stop a task this way; editors use interrupt."),
     ["admin key"]],
    ["too many stops", 429, refusal("RATE_LIMITED", "Too many stop requests; try again shortly."), ["too many stop requests"]],
    ["NERVIS holding no admin key", 403, { message: "NERVIS holds no admin credential for RAVIS, so it cannot change its configuration." },
     ["admin key", "NERVIS holds no admin credential"]],
    ["NERVIS refusing the key", 400, { message: "The Stop request needs an Idempotency-Key of 16 to 128 letters, digits, - or _." },
     ["Idempotency-Key of 16 to 128"]],
    ["a NERVIS restarted since the page loaded", 403, refusal("CONTROL_TOKEN_REQUIRED", "changing RAVIS's configuration through NERVIS needs the dashboard's control token"),
     ["Reload the page"]],
    ["a NERVIS from before Stop", 404, { detail: "Not Found" }, ["NERVIS 0.29.0"]],
    ["RAVIS not answering NERVIS", 502, { message: "RAVIS did not answer: ConnectError" }, ["didn't answer", "Click Stop again"]],
    ["NERVIS not answering", 0, null, ["NERVIS didn't answer"]],
  ];
  for (const [label, status, body, expected] of cases) {
    const page = await drawCard(busy([task()]), {
      control: { [`POST /runs/${SID_A}/stop`]: () => answering(status, body) } });
    const reads = page.ravis.codexReads;
    await run(page, stopOf(SID_A));
    await run(page, stopOf(SID_A));
    words(`a Stop refused: ${label}`, cardOf(page), expected, ["stopping…"]);
    if (page.ravis.codexReads === reads) failures.push(`a Stop refused (${label}) didn't read the tasks again.`);
  }
}

async function theAllowedSitesOfferRemoveOnlyForTheOwners() {
  let view = { defaults: DEFAULT_SITES, added: ["download.pytorch.org", "huggingface.co", "weird/host.example"] };
  const page = await drawCard(busy([]), { sites: () => answer(200, view), control: {
    "DELETE /sites/huggingface.co": () => {
      view = { ...view, added: view.added.filter((host) => host !== "huggingface.co") };
      return answer(200, view);
    },
    "DELETE /sites/weird%2Fhost.example": () => answer(400, { message: "'weird/host.example' is not a site name NERVIS will forward to RAVIS" }),
  } });
  const html = cardOf(page);
  words("the allowed sites", html, ["RAVIS's 4 default sites, and 3 sites you added", "download.pytorch.org",
    "huggingface.co", "never removed here", "stops reaching Codex conversations that start or reopen"]);
  const folded = html.match(/<details\b[^>]*id="codex-default-sites"[^>]*>/);
  if (!folded || /\sopen\b/.test(folded[0])) failures.push("the default sites aren't folded away under their own heading.");
  for (const host of DEFAULT_SITES) {
    if (html.includes(`CODEX_CARD.remove('${host}')`)) failures.push(`a default site, ${host}, has Remove.`);
    if (!html.slice(html.indexOf('id="codex-default-sites"')).includes(host)) failures.push(`a default site, ${host}, isn't listed.`);
  }
  for (const host of view.added) {
    if (!html.includes(`CODEX_CARD.remove('${host}')">Remove</button>`)) failures.push(`a site you added, ${host}, has no Remove.`);
  }

  for (const host of ["pypi.org", "*.crates.io", "not-listed.example"]) {
    await run(page, removeOf(host));
    await run(page, removeOf(host));
  }
  if (page.sent.some((call) => (call.route || "").startsWith("DELETE"))) {
    failures.push("Remove sent RAVIS a site that isn't one you added.");
  }
  await run(page, removeOf("huggingface.co"));
  if (calls(page, "DELETE /sites/huggingface.co").length) failures.push("one click on Remove removed the site; it takes two.");
  words("Remove, armed", cardOf(page), ["Click again to remove huggingface.co", "Keep it"]);
  await run(page, removeOf("huggingface.co"));
  const [removal] = calls(page, "DELETE /sites/huggingface.co");
  if (!removal || !("x-nervis-control" in removal.headers) || removal.body !== null) {
    failures.push("the second click on Remove didn't send DELETE to NERVIS's site route with the control header and no body.");
  }
  words("after Remove", cardOf(page), ["huggingface.co removed", "2 sites you added"], ["CODEX_CARD.remove('huggingface.co')"]);
  await run(page, removeOf("weird/host.example"));
  await run(page, removeOf("weird/host.example"));
  if (!calls(page, "DELETE /sites/weird%2Fhost.example").length) failures.push("a site's name wasn't encoded into NERVIS's address.");
  words("a site name NERVIS won't forward", cardOf(page), ["is not a site name NERVIS will forward"]);

  const refusals = [
    ["a default site", 409, refusal("SITE_NOT_REMOVED", "That is one of RAVIS's default sites, so it stays allowed.",
      { host: "huggingface.co", reason: "default_site" }), ["one of RAVIS's default sites"]],
    ["Codex not taking it", 409, refusal("SITE_NOT_REMOVED", "Codex didn't remove that site, so it stays allowed.",
      { host: "huggingface.co", reason: "overridden" }), ["stays allowed", "try again in a moment"]],
    ["a name RAVIS won't take", 422, refusal("SITES_REFUSED", "huggingface.co can't be removed as a site.",
      { refused: [{ host: "huggingface.co", reason: "not_a_host_name" }] }), ["doesn't take huggingface.co as a site name"]],
    ["RAVIS refusing NERVIS's key", 403, refusal("FORBIDDEN", "An admin credential is required."), ["admin key"]],
    ["NERVIS not answering", 0, null, ["NERVIS didn't answer"]],
  ];
  for (const [label, status, body, expected] of refusals) {
    const refused = await drawCard(busy([]), {
      sites: () => answer(200, { defaults: DEFAULT_SITES, added: ["huggingface.co"] }),
      control: { "DELETE /sites/huggingface.co": () => answering(status, body) } });
    await run(refused, removeOf("huggingface.co"));
    await run(refused, removeOf("huggingface.co"));
    words(`Remove refused: ${label}`, cardOf(refused), expected, ["huggingface.co removed"]);
  }

  const unread = [
    ["Codex not running", () => answer(503, refusal("CODEX_RUNTIME_UNAVAILABLE", "Codex isn't running, so the sites it allows can't be read; try again in a moment.")),
     ["Codex isn't running, so the sites it allows can't be read"]],
    ["a RAVIS from before the sites", () => answer(404, { detail: "Not Found" }), ["RAVIS 0.25.0 does"]],
    ["RAVIS refusing the read", () => answer(403, refusal("FORBIDDEN", "A Clarvis, NERVIS or admin credential is required.")), ["refused NERVIS's read"]],
    ["RAVIS not answering", failing, ["didn't answer the read of the sites"]],
  ];
  for (const [label, sites, expected] of unread) {
    const page = await drawCard(busy([task()]), { sites });
    words(`the allowed sites, ${label}`, cardOf(page), expected, [">Remove<"]);
    const screen = page.elements.get("sel:#content").innerHTML;
    if (!screen.includes("62% left") || !cardOf(page).includes("add-utc-demo")) {
      failures.push(`the allowed sites, ${label}: a failed read of the sites took the rest of RAVIS's cards down with it.`);
    }
  }
}

/* 13 — the card's line to NERVIS → Skills (NERVIS 0.32.0): Codex's skills moved to a page of their own,
   and the card neither reads nor switches them. `skills_check.js` holds the page. */
async function theCardPointsToTheSkillsPage() {
  const page = await drawCard(busy([task()]));
  const html = cardOf(page);
  words("the card's skills line", html, ["NERVIS → Skills"], ["Switch on", "Switch off", "skills Codex can use"]);
  if (!html.includes(`onclick="CODEX_CARD.openSkills();return false"`)) failures.push("the Codex card's skills line has no link to NERVIS → Skills.");
  if (page.requests.some((request) => request.address.includes("skills"))) failures.push("the Codex card still reads or switches skills itself.");
  if (run(page, "typeof CODEX_CARD.switchSkill") !== "undefined") failures.push("the Codex card still carries a skill switch.");
  run(page, "CODEX_CARD.openSkills()");
  await quiet();
  if (page.exported.state.app !== "nervis" || page.exported.state.view !== "Skills") {
    failures.push("the Codex card's skills line doesn't open NERVIS → Skills.");
  }
}

async function aNewBuildIsReportedAndAcceptedInTwoClicks() {
  const page = await drawCard(untested(), { control: {
    "GET /version-check": () => answer(200, VERSION_REPORT),
    "POST /accept-version": () => {
      page.ravis.state = untested({ verdict: "accepted" });
      return answer(200, page.ravis.state);
    },
  } });
  words("a build RAVIS hasn't tested", cardOf(page), ["Codex 0.155.0 (Homebrew)", "a build RAVIS has not tested",
    "file rules not yet re-tested", "new tasks are paused"], ["Use this version", "version report"]);
  if (!buttons(cardOf(page)).includes("Check this version")) failures.push("a build RAVIS hasn't tested has no Check this version.");

  await run(page, "CODEX_CARD.check()");
  const [asked] = calls(page, "GET /version-check");
  if (!asked || !("x-nervis-control" in asked.headers)) failures.push("Check this version didn't ask NERVIS's version route with the control header.");
  if (page.requests.some((request) => request.address.includes("/relay/ravis/api/v1/codex/version-check"))) {
    failures.push("the version report was read through the relay, which carries no admin credential.");
  }
  const report = cardOf(page);
  words("the version report", report, [
    "signed by OpenAI's team", "the file checked is the one installed", "its version reads correctly",
    "Codex's protocol descriptions generate", "every call RAVIS makes to Codex is still there",
    "it starts and answers in a throwaway home", "the file rules still load the same way",
    "AdditionalPermissionProfile changed", "1 of 7 checks didn't pass",
    "the part of Codex the file rules depend on changed", "ThreadItem", "14 other definitions changed",
    "doesn't start that re-test", "spends none of your plan's allowance", "Re-test the file rules",
    "one short Codex turn"]);
  if (!buttons(report).includes("Use this version…")) failures.push("an acceptable build's report has no Use this version.");
  await run(page, "CODEX_CARD.accept()");
  if (calls(page, "POST /accept-version").length) failures.push("one click on Use this version accepted the build; it takes two.");
  words("Use this version, armed", cardOf(page), ["Click again to accept Codex 0.155.0", "Not now"]);
  await run(page, "CODEX_CARD.accept()");
  const [accepted] = calls(page, "POST /accept-version");
  if (!accepted || !("x-nervis-control" in accepted.headers) || JSON.stringify(accepted.body) !== JSON.stringify({ sha256: SHA_NEW })) {
    failures.push(`Use this version sent ${JSON.stringify(accepted && accepted.body)}, not the reported build's sha256 with the control header.`);
  }
  words("after accepting", cardOf(page), ["accepted by you", "Codex 0.155.0 accepted", "New tasks stay paused",
    "Re-test the file rules"], ["Use this version"]);
  page.ravis.state = untested({ installed_sha256: "0".repeat(64), version: "0.156.0" });
  await run(page, "CODEX_CARD.redraw()");
  words("after another build was installed", cardOf(page), ["Codex 0.156.0", "Check this version"], ["ThreadItem"]);

  const refusals = [
    ["another build installed since the report", "accept", 409, refusal("CODEX_HASH_MISMATCH", "That sha256 is not the installed Codex binary."),
     ["changed again", "Check this version again"]],
    ["a check failed at RAVIS", "accept", 409, refusal("CODEX_VERSION_CHECK_FAILED", "The version check failed, so this version can't be accepted."),
     ["can't be accepted"]],
    ["RAVIS refusing NERVIS's key", "check", 403, refusal("FORBIDDEN", "An admin credential is required."), ["admin key"]],
    ["NERVIS not answering", "check", 0, null, ["NERVIS didn't answer"]],
  ];
  for (const [label, step, status, body, expected] of refusals) {
    const refused = await drawCard(untested(), { control: {
      "GET /version-check": () => (step === "check" ? answering(status, body) : answer(200, VERSION_REPORT)),
      "POST /accept-version": () => answering(status, body),
    } });
    await run(refused, "CODEX_CARD.check()");
    if (step === "accept") {
      await run(refused, "CODEX_CARD.accept()");
      await run(refused, "CODEX_CARD.accept()");
    }
    words(`the version report, ${label}`, cardOf(refused), expected);
  }

  const broken = JSON.parse(JSON.stringify(VERSION_REPORT));
  broken.version_check.checks = broken.version_check.checks.map((check) =>
    (check.name === "handshake" ? { name: "handshake", ok: false, detail: "initialize timed out" } : check));
  const unacceptable = await drawCard(untested(), { control: { "GET /version-check": () => answer(200, broken) } });
  await run(unacceptable, "CODEX_CARD.check()");
  words("a build whose handshake failed", cardOf(unacceptable),
    ["can't be accepted", "it starts and answers in a throwaway home", "initialize timed out"], ["Use this version"]);
  if (buttons(cardOf(await drawCard(busy([])))).some((label) => /version/i.test(label))) {
    failures.push("a build RAVIS tested offers a version button.");
  }
}

async function nothingRavisSendsInjectsIntoTheCard() {
  const tag = ' <vxs onerror=vxjs>" vxatr=vxjs';
  const quote = "x');vxjs('";
  const runs = [
    task({ project: "add-utc-demo" + tag, model: "gpt" + tag, effort: "high" + tag,
           reopening: { group_id: "sg" + tag, hosts: ["pypi.org" + tag], since: "then" + tag } }),
    task({ id: SID_B, turn_id: TURN_B + tag, state: "waiting_on_you" + tag, project: quote, waiting_minutes: 3 }),
    task({ id: quote, project: "quoted" }),
  ];
  const view = { defaults: ["pypi.org" + tag], added: ["huggingface.co" + tag, quote] };
  const report = JSON.parse(JSON.stringify(VERSION_REPORT));
  const check = report.version_check;
  check.version = "0.155.0" + tag;
  check.checks[0].name = "signature" + tag;
  check.checks[6].detail = "changed" + tag;
  check.protocol.used_definitions_changed = ["ThreadItem" + tag];
  check.protocol.used_methods_missing = ["turn/start" + tag];
  const page = await drawCard(untested({ version: "0.155.0" + tag, source: "brew" + tag }, { runs }), {
    sites: () => answer(200, view),
    control: {
      "GET /version-check": () => answer(200, report),
      [`POST /runs/${SID_A}/stop`]: () => answer(409, refusal("CONFIRMATION_MISMATCH" + tag, "changed" + tag)),
      [`DELETE /sites/${encodeURIComponent(view.added[0])}`]: () => answer(409, refusal("SITE_NOT_REMOVED", "kept" + tag)),
    },
  });
  const drawn = [cardOf(page)];
  await run(page, "CODEX_CARD.check()");
  drawn.push(cardOf(page));
  await run(page, stopOf(SID_A));
  drawn.push(cardOf(page));
  await run(page, stopOf(SID_A));
  drawn.push(cardOf(page));
  await run(page, removeOf(view.added[0]));
  drawn.push(cardOf(page));
  await run(page, removeOf(view.added[0]));
  drawn.push(cardOf(page));
  if (!drawn.join("").includes("vxs")) failures.push("the injection probe never reached the Codex card, so it proved nothing.");
  for (const html of drawn) {
    if (/<vxs|"\s*vxatr=/.test(html)) {
      const at = Math.max(0, html.search(/<vxs|"\s*vxatr=/) - 80);
      failures.push(`RAVIS's text broke out of the Codex card's markup: …${html.slice(at, at + 140)}…`);
    }
    if (html.includes("');vxjs('")) failures.push("RAVIS's text broke out of a quoted handler argument on the Codex card.");
  }
}

async function theOverviewCountsCodexsTasks() {
  const cases = [
    ["a task waiting", [task({ state: "waiting_on_you" }), task({ id: SID_B, turn_id: TURN_B, project: "weather-cli" }),
      task({ id: null, turn_id: null, project: "notes-app", state: "clarvis_engine" })], "2 tasks · 1 waiting for your answer"],
    ["a task needing you", [task({ state: "paused_unanswered" })], "1 task · 1 needs you in Clarvis"],
    ["one running", [task()], "1 task running"],
    ["none", [], "no tasks"],
  ];
  for (const [label, runs, phrase] of cases) {
    words(`the Overview's Codex line, ${label}`, (await overview(busy(runs))) || "",
      [phrase, "62% left in the 5-hour window", "the Codex card"]);
  }
  words("the Overview's Codex line, a RAVIS that lists no tasks", (await overview(busy(null))) || "",
    ["62% left"], ["task"]);
  words("the Overview's Codex line, RAVIS not answering", (await overview(busy([task()]), 0)) || "",
    [], ["task", "% left"]);

  const page = world({ state: busy([task()]), reads: { "/api/v1/relay/ravis/api/v1/usage": USAGE_READ } });
  await quiet();
  page.exported.state.app = "nervis";
  page.exported.state.view = "Overview";
  await run(page, "nervis()");
  run(page, "codexOpenCard()");
  await quiet();
  if (page.exported.state.app !== "ravis" || page.exported.state.view !== "Dashboard") {
    failures.push("the Overview's link to the Codex card doesn't open RAVIS → Dashboard.");
  } else if (run(page, "CODEX_CARD.scroll") !== false || !cardOf(page)) {
    failures.push("the Overview's link opened RAVIS → Dashboard without drawing the Codex card or bringing it into view.");
  }
}

async function main() {
  await everyStateDrawsItsWords();
  await theAllowanceSitsBesideSpend();
  await theOverviewCarriesOneLine();
  await codexSitsBesideTheClarvisPools();
  await signingInFromTheButton();
  await aRestartDuringTheSignInIsSaid();
  await cancelSendsDelete();
  await signOutTakesTwoClicks();
  await confirmSendsTheHintShown();
  await refusalsAreSaidPlainly();
  await nothingInjects();
  await everyCodexStateIsWorded();
  await theTaskListSaysEveryTask();
  await stopIsTheOnlyTaskControl();
  await stopSendsTheConfirmationAndThePagesOwnKey();
  await everyStopRefusalIsSaid();
  await theAllowedSitesOfferRemoveOnlyForTheOwners();
  await theCardPointsToTheSkillsPage();
  await aNewBuildIsReportedAndAcceptedInTwoClicks();
  await nothingRavisSendsInjectsIntoTheCard();
  await theOverviewCountsCodexsTasks();

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
    "absent without RAVIS; the Overview carries its line; RAVIS → Pools draws a read-only Clarvis " +
    "Codex row after the Clarvis pools with its state, tightest window and sign-in link, and says " +
    "why when RAVIS doesn't answer; a stored key's dot is not escaped; and the Codex card on " +
    "RAVIS → Dashboard says every task in words with its wait, model, effort and reconnecting, " +
    "offers Stop as its only task control, in two clicks, sending the folder, the turn and the " +
    "page's own Idempotency-Key (the same one on a retry), words every refusal, offers Remove " +
    "beside added sites only with the defaults folded, reports a new build and accepts it in two " +
    "clicks saying it neither starts the re-test nor spends allowance, points to NERVIS → Skills in " +
    "one line without reading or switching skills itself, injects nothing, and the Overview counts the " +
    "tasks and opens the card"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
