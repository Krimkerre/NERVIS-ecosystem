/* NERVIS → Skills: installing, updating, removing and browsing skills (NERVIS 0.33.0 and 0.33.1,
 * RAVIS 0.28.0 and 0.28.1).
 *
 * The owner's decisions of 15 September 2026: a skill installs from a GitHub link or a zip file, and
 * never before a review; it arrives switched off; an installed skill can be updated, after reviewing
 * what changed, or removed to the Trash; and a Browse section lists the marketplace's sources. This
 * drives the real page against RAVIS's own contracts — `skills.json` for the list and
 * `skill-store/contract.json` for the store, both under `ravis/tests/fixtures/`, read rather than
 * copied — behind NERVIS, and asserts what it draws and what it sends:
 *
 *   1. **Install skill… takes a GitHub link and shows its review before anything is installed**:
 *      it sends only `{origin, url}` with the control header and says it is working; the review
 *      shows the name, description, license, where it came from, each file with scripts flagged,
 *      SKILL.md as escaped text, that it arrives switched off, and the specification line;
 *      **Install** sends only the review's id; **Cancel** discards it.
 *   2. **A zip file** over 8 MB is refused without a request; one under goes as its own bytes.
 *   3. **An installed skill** says where it came from, the commit and the date, with **Update…**
 *      — nothing newer said so, changes reviewed with the diff and what happens to the switches —
 *      and **Remove…**, which takes two clicks, never confirm(), says the Trash and sends only the
 *      name; a zip install updates only from a zip of the same skill.
 *   4. **Browse** reads the marketplace, reads stale sources once, marks openai deprecated by its
 *      owner and lists uncurated, shows license and an installed badge, looks the shown link-list
 *      entries up once each, never fetches a link kept outside GitHub, and **Review and install**
 *      sends the entry's own install body. **Since 0.33.1**, from what the owner found using it:
 *      skills.sh is searched with every source chosen as well as alone, 600 ms after typing stops or
 *      at once on Enter, never under two letters (the page says so), a question overtaken by newer
 *      words cancelled and its answer dropped, its results in their own labelled group after the
 *      listed entries, refusals in plain words, and asked again after an install for its badges;
 *      entries that can't be installed here are hidden, from any source, with one line and a switch
 *      this browser remembers; and the words filter every loaded entry as they are typed, after a
 *      short wait, keeping the box's focus and caret across a redraw.
 *   5. **Sources**: adding a GitHub repository or a website sends only what the form holds; RAVIS's
 *      own sources can be hidden, not removed; removing the owner's own takes two clicks.
 *   6. **Every refusal is said plainly**, RAVIS's own words first.
 *   7. **Nothing RAVIS sends injects markup** or breaks out of a handler's quotes.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { isDeepStrictEqual } = require("node:util");

const failures = [];
let finished = false;
process.on("exit", (code) => {
  if (!finished && code === 0) {
    console.error("skill store check never finished: something it waited on was never answered, so it proved nothing.");
    process.exitCode = 1;
  }
});

/* RAVIS's contracts, read rather than copied, so a change in RAVIS's answers reaches this gate. */
const FIXTURES = join(__dirname, "..", "..", "ravis", "tests", "fixtures");
const SKILLS = JSON.parse(readFileSync(join(FIXTURES, "relay-contract", "skills.json"), "utf8"));
const STORE = JSON.parse(readFileSync(join(FIXTURES, "skill-store", "contract.json"), "utf8"));
const response = (contract, method, path, name) => contract.routes
  .find((route) => route.method === method && route.path === path)
  .examples.find((example) => example.name === name).response;
const copy = (value) => JSON.parse(JSON.stringify(value));
const BOARD = response(SKILLS, "GET", "/api/v1/skills", "every skill with a switch for each engine").body;
const REVIEW = response(STORE, "POST", "/api/v1/skills/previews", "a GitHub folder").body;
const LIMITED = response(STORE, "POST", "/api/v1/skills/previews", "GitHub is rate-limiting");
const INSTALLED = response(STORE, "POST", "/api/v1/skills/installs", "installed").body;
const UPDATED = response(STORE, "POST", "/api/v1/skills/installs", "updated").body;
const UPDATE = response(STORE, "POST", "/api/v1/skills/installs/update-preview", "SKILL.md changed").body;
const UP_TO_DATE = response(STORE, "POST", "/api/v1/skills/installs/update-preview", "nothing newer").body;
const REMOVED = response(STORE, "POST", "/api/v1/skills/installs/remove", "to the Trash").body;
const SEARCH = response(STORE, "POST", "/api/v1/skills/market/search", "results").body;
const MARKET = response(STORE, "GET", "/api/v1/skills/market", "after a refresh").body;
const NOTES = BOARD.skills.find((skill) => skill.name === "nervis-notes").path;
const SENTRY = "https://github.com/getsentry/skills/tree/main/skills/code-review";
const ELSEWHERE = "https://skills.example.org/cool";

/* RAVIS's `Installs`: one skill, NERVIS's own nervis-notes, installed from GitHub (or a zip). */
function installsBody(origin = "github") {
  const install = { ...copy(INSTALLED.installed), name: "nervis-notes", path: NOTES };
  if (origin === "zip") install.source = { origin: "zip" };
  return { folder: BOARD.folders.nervis.path, installs: [install] };
}

/* RAVIS's `Market`: the contract's shapes, with a source of each kind and an entry in each state. */
function marketBody({ stale = true } = {}) {
  const base = MARKET.sources[0];
  const source = (id, label, kind, extra = {}) => ({ ...copy(base), id, label, kind, default: true,
    deprecated: false, uncurated: false, note: null, hidden: false, stale: false, problem: null, ...extra });
  const pdf = copy(MARKET.entries[0]);
  const listed = (name, link, state, extra = {}) => ({ ...copy(pdf), id: `voltagent:${link}`, source: "voltagent",
    state, name, description: `${name}, from a list`, license: null, lives_in: "getsentry/skills", repository: null,
    folder: null, ref: null, link, installed: false, install: null, ...extra });
  return {
    checked_against: MARKET.checked_against,
    sources: [
      source("anthropics", "anthropics/skills", "github"),
      source("openai", "openai/skills", "github", { deprecated: true, note: "Deprecated by its owner, in favour of OpenAI's plugins repository." }),
      source("voltagent", "VoltAgent/awesome-agent-skills", "link_list", { uncurated: true, stale, note: "Uncurated: a list of links." }),
      source("skills_sh", "skills.sh", "search", { uncurated: true, note: "Uncurated, ranked by installs." }),
      source("own-mine", "me/mine", "github", { default: false }),
    ],
    entries: [
      pdf,
      { ...copy(pdf), id: "openai::openai/skills/skills/.curated/gh-fix", source: "openai", name: "gh-fix",
        description: "Fix failing GitHub Actions.", label: "curated", lives_in: "openai/skills", installed: true },
      listed("sentry/code-review", SENTRY, "unresolved"),
      listed("Hosted elsewhere", ELSEWHERE, "not_installable", { lives_in: "skills.example.org",
        problem: "It is kept on skills.example.org, not GitHub, so it can't be installed here." }),
    ],
    github: copy(MARKET.github),
  };
}

function answer(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status,
                           headers: { get: () => null }, json: async () => body });
}
const failing = () => Promise.reject(new TypeError("fetch failed"));
const STORE_ROOT = "/api/v1/ravis/skills/";

/* RAVIS behind NERVIS: the three relayed reads, and `control(route, body)` for every POST to the
   skill store's control routes. Every control call is kept in `sent`, every request in `requests`. */
function world({ installs = () => answer(200, installsBody()), market = () => answer(200, marketBody()),
                 control = () => answer(500, {}), storage } = {}) {
  const ravis = { boardReads: 0, installReads: 0, marketReads: 0 };
  const sent = [];
  const requests = [];
  const fetchImpl = (url, init = {}) => {
    const address = String(url);
    const method = (init.method || "GET").toUpperCase();
    requests.push({ address, method });
    if (method === "GET" && address.endsWith("/api/v1/relay/ravis/api/v1/skills")) { ravis.boardReads += 1; return answer(200, BOARD); }
    if (method === "GET" && address.endsWith("/api/v1/relay/ravis/api/v1/skills/installs")) { ravis.installReads += 1; return installs(); }
    if (method === "GET" && address.endsWith("/api/v1/relay/ravis/api/v1/skills/market")) { ravis.marketReads += 1; return market(); }
    const at = address.indexOf(STORE_ROOT);
    if (method === "POST" && at >= 0) {
      const headers = init.headers || {};
      const json = headers["content-type"] === "application/json";
      const body = json ? JSON.parse(init.body) : init.body;
      const route = address.slice(at + STORE_ROOT.length);
      sent.push({ route, headers, body });
      return control(route, body, init);
    }
    return failing();
  };
  const loaded = loadPage({ fetchImpl, storage });
  vm.runInContext("stopPolling()", loaded.context);
  loaded.context.confirm = () => {
    failures.push("the skill store called confirm(), which a browser can mute for good.");
    return true;
  };
  return { ...loaded, ravis, sent, requests };
}

/* A control answer per route, and 500 for anything a check didn't expect. */
const routes = (table) => (route, body, init) => (route in table ? table[route](body, init) : answer(500, {}));

const run = (page, code) => vm.runInContext(code, page.context);
const settle = () => new Promise((done) => setTimeout(done, 30));
const quiet = () => new Promise((done) => setTimeout(done, 250));
const wait = (ms) => new Promise((done) => setTimeout(done, ms));

/* A control route answering after `ms`, or failing the way a browser does when the page cancels the
   call; each cancelled body is kept in `cancelled`. */
function slowly(ms, reply, cancelled = []) {
  return (body, init = {}) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => resolve(reply(body)), ms);
    if (!init.signal) return;
    init.signal.addEventListener("abort", () => {
      clearTimeout(timer);
      cancelled.push(body);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    });
  });
}

/* skills.sh's answer through RAVIS for whatever was asked: the contract's example, its one result
   renamed after the words, so a check can tell which question an answer on the page belongs to. */
const searchAnswer = (body) => answer(200, { ...copy(SEARCH), query: body.query,
  results: [{ ...copy(SEARCH.results[0]), name: `${body.query}-result` }] });

async function drawPage(options) {
  const page = world(options);
  await quiet();
  page.exported.state.app = "nervis";
  page.exported.state.view = "Skills";
  await run(page, "nervis()");
  await settle();
  return page;
}

function contentOf(page) {
  const content = page.elements.get("sel:#content");
  return content ? content.innerHTML : "";
}
const readable = (html) => html.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
function words(label, html, present, absent = []) {
  const text = readable(html);
  for (const part of present) if (!text.includes(part)) failures.push(`${label}: missing ${JSON.stringify(part)}.`);
  for (const part of absent) if (text.includes(part)) failures.push(`${label}: should not show ${JSON.stringify(part)}.`);
}
function same(label, actual, expected) {
  if (!isDeepStrictEqual(JSON.parse(JSON.stringify(actual)), expected)) {
    failures.push(`${label}: sent ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}.`);
  }
}
const type = (page, selector, value) => run(page, `document.querySelector(${JSON.stringify(selector)}).value=${JSON.stringify(value)}`);
const sentTo = (page, route) => page.sent.filter((call) => call.route === route);

/* 1 — a GitHub link, its review, Install and Cancel. */
async function aLinkIsReviewedBeforeAnythingIsInstalled() {
  const page = await drawPage({ control: routes({
    previews: () => answer(200, REVIEW), installs: () => answer(200, INSTALLED),
    "previews/discard": () => answer(200, { discarded: true }) }) });
  words("the install card", contentOf(page), ["Install skill…", "Browse",
    "Checked against the Agent Skills specification at agentskills.io.", "arrives switched off"]);
  run(page, "SKILL_STORE.openInstall()");
  words("the install form", contentOf(page), ["Review", "Or choose a zip file…"]);
  run(page, "SKILL_STORE.reviewLink()");
  words("an empty link", contentOf(page), ["Paste a GitHub link to a skill folder first."]);
  if (page.sent.length) failures.push("an empty link was sent to RAVIS.");

  type(page, "#skillLink", "  https://github.com/anthropics/skills/tree/main/skills/pdf ");
  const reviewing = run(page, "SKILL_STORE.reviewLink()");
  words("while reviewing", contentOf(page), ["Fetching and checking the skill…"]);
  await reviewing;
  await settle();
  const [asked] = sentTo(page, "previews");
  same("the review's request", asked && asked.body, { origin: "github", url: "https://github.com/anthropics/skills/tree/main/skills/pdf" });
  if (!asked || !("x-nervis-control" in asked.headers)) failures.push("the review was asked for without the control header.");
  words("the review", contentOf(page), ["Review pdf before installing it", REVIEW.description, REVIEW.license.text,
    "GitHub anthropics/skills/skills/pdf (main) at 0123456", "scripts/extract.py", "script — Codex could run it",
    "LICENSE.txt", "has scripts", "It has 1 script Codex could run", "name: pdf", "# Instructions",
    "It will arrive switched off for Codex and for the other models.",
    "Checked against the Agent Skills specification at agentskills.io.", "Install", "Cancel"]);

  await run(page, `SKILL_STORE.confirm(${JSON.stringify(REVIEW.preview_id)})`);
  await settle();
  same("Install", sentTo(page, "installs").map((call) => call.body), [{ preview_id: REVIEW.preview_id }]);
  words("after Install", contentOf(page), ["pdf is installed, switched off for Codex and for the other models."],
    ["Review pdf before installing it"]);
  if (page.ravis.boardReads < 2 || page.ravis.installReads < 2) failures.push("Install didn't read the list and the installs again.");

  const cancelling = await drawPage({ control: routes({ previews: () => answer(200, REVIEW),
    "previews/discard": () => answer(200, { discarded: true }) }) });
  type(cancelling, "#skillLink", "https://github.com/anthropics/skills/tree/main/skills/pdf");
  await run(cancelling, "SKILL_STORE.reviewLink()");
  await run(cancelling, "SKILL_STORE.cancelReview()");
  await settle();
  same("Cancel", sentTo(cancelling, "previews/discard").map((call) => call.body), [{ preview_id: REVIEW.preview_id }]);
  words("after Cancel", contentOf(cancelling), ["Nothing was installed."], ["Review pdf before installing it"]);
  if (sentTo(cancelling, "installs").length) failures.push("Cancel installed the skill.");
}

/* 2 — a zip file: too large is said and not sent; otherwise its bytes go. */
async function aZipIsSentAsItsOwnBytes() {
  const page = await drawPage({ control: routes({ "previews/zip": () => answer(200, REVIEW) }) });
  await run(page, "SKILL_STORE.reviewZip({files:[{name:'huge.zip',size:8*1024*1024+1,arrayBuffer:async()=>'NEVER'}]})");
  await settle();
  words("a zip over 8 MB", contentOf(page), ["huge.zip is larger than 8 MB, more than RAVIS installs from."]);
  if (page.sent.length) failures.push("a zip over 8 MB was sent.");
  await run(page, "SKILL_STORE.reviewZip({files:[{name:'pdf.zip',size:9,arrayBuffer:async()=>'ZIP-BYTES'}]})");
  await settle();
  const [zip] = sentTo(page, "previews/zip");
  if (!zip || zip.body !== "ZIP-BYTES") failures.push("the zip file wasn't sent as its own bytes.");
  if (zip && (zip.headers["content-type"] !== "application/zip" || !("x-nervis-control" in zip.headers))) {
    failures.push("the zip file went without its content type or the control header.");
  }
  words("a zip's review", contentOf(page), ["Review pdf before installing it"]);
}

/* 3 — an installed skill's line, Update… and Remove…. */
async function anInstalledSkillCanBeUpdatedOrRemoved() {
  const page = await drawPage({ control: routes({
    "installs/update-preview": () => answer(200, UPDATE), installs: () => answer(200, UPDATED),
    "installs/remove": () => answer(200, REMOVED) }) });
  words("an installed row", contentOf(page), ["Installed from GitHub anthropics/skills/skills/pdf (main) at 0123456 on 2026-09-15",
    "Update…", "Remove…"]);

  await run(page, "SKILL_STORE.remove('nervis-notes')");
  if (page.sent.length) failures.push("Remove sent something before it was asked twice.");
  run(page, "SKILL_STORE.askRemove('nervis-notes')");
  words("Remove, asked once", contentOf(page), ["Move nervis-notes to the Trash?", "Move to the Trash", "Keep it"]);
  if (page.sent.length) failures.push("the first click of Remove sent something.");
  run(page, "SKILL_STORE.keep()");
  words("Keep it", contentOf(page), ["Remove…"], ["Move nervis-notes to the Trash?"]);
  run(page, "SKILL_STORE.askRemove('nervis-notes')");
  await run(page, "SKILL_STORE.remove('nervis-notes')");
  await settle();
  same("Remove", sentTo(page, "installs/remove").map((call) => call.body), [{ name: "nervis-notes" }]);
  words("after Remove", contentOf(page), ["nervis-notes is in the Trash, as “pdf 2026-09-15 22.00.00”. Its switches went with it."]);

  await run(page, "SKILL_STORE.update('nervis-notes')");
  await settle();
  same("Update…", sentTo(page, "installs/update-preview").map((call) => call.body), [{ name: "nervis-notes" }]);
  words("an update's review", contentOf(page), ["Review the update of pdf", "added", "changed", "removed",
    "+# New steps", "-# Instructions", UPDATE.update.why, "Update", "Cancel"],
    ["It will arrive switched off for Codex and for the other models."]);
  await run(page, `SKILL_STORE.confirm(${JSON.stringify(UPDATE.preview_id)})`);
  await settle();
  same("the update's confirm", sentTo(page, "installs").map((call) => call.body), [{ preview_id: UPDATE.preview_id }]);
  words("after the update", contentOf(page), ["pdf is updated. Both switches are off again", "The earlier version is in the Trash."]);

  const current = await drawPage({ control: routes({ "installs/update-preview": () => answer(200, UP_TO_DATE) }) });
  await run(current, "SKILL_STORE.update('nervis-notes')");
  await settle();
  words("nothing newer", contentOf(current), ["nervis-notes is up to date with where it came from."]);

  const zipped = await drawPage({ installs: () => answer(200, installsBody("zip")), control: routes({
    "previews/zip": () => answer(200, REVIEW), "previews/discard": () => answer(200, { discarded: true }) }) });
  await run(zipped, "SKILL_STORE.update('nervis-notes')");
  if (zipped.sent.length) failures.push("Update… of a zip install asked RAVIS for an update it can't fetch.");
  words("a zip install's Update…", contentOf(zipped), ["Choose the new zip file of nervis-notes"]);
  await run(zipped, "SKILL_STORE.reviewZip({files:[{name:'other.zip',size:9,arrayBuffer:async()=>'OTHER'}]})");
  await settle();
  words("a zip of another skill", contentOf(zipped), ["That zip file holds pdf, not an update of nervis-notes, so nothing was changed."],
    ["Review pdf before installing it"]);
  same("the other skill's review dropped", sentTo(zipped, "previews/discard").map((call) => call.body), [{ preview_id: REVIEW.preview_id }]);
}

/* 4 — Browse: sources, entries, look-ups, Review and install, and skills.sh. */
async function browseListsTheMarketplace() {
  const resolved = marketBody({ stale: false });
  resolved.entries[2] = { ...copy(resolved.entries[0]), id: `voltagent:${SENTRY}:getsentry/skills/skills/code-review`, source: "voltagent",
    name: "code-review", lives_in: "getsentry/skills", link: SENTRY,
    install: { origin: "github", repository: "getsentry/skills", folder: "skills/code-review", ref: "main", via: "voltagent" } };
  const page = await drawPage({ control: routes({
    "market/refresh": () => answer(200, marketBody({ stale: false })), "market/resolve": () => answer(200, { ...resolved, note: null }),
    previews: () => answer(200, REVIEW), "market/search": () => answer(200, SEARCH) }) });
  await run(page, "SKILL_STORE.toggleBrowse()");
  await settle();
  if (page.ravis.marketReads !== 1) failures.push(`Browse read the marketplace ${page.ravis.marketReads} times, not once.`);
  same("stale sources read once", sentTo(page, "market/refresh").map((call) => call.body), [{}]);
  same("the shown links looked up", sentTo(page, "market/resolve").map((call) => call.body), [{ source: "voltagent", links: [SENTRY] }]);
  if (page.sent.some((call) => JSON.stringify(call.body).includes(ELSEWHERE))) failures.push("a link kept outside GitHub was sent to be looked up.");
  const html = contentOf(page);
  words("Browse", html, ["Browse skills", "All sources", "openai/skills — deprecated by its owner",
    "VoltAgent/awesome-agent-skills — uncurated", "skills.sh — uncurated", "pdf", MARKET.entries[0].description,
    "License: Proprietary. LICENSE.txt has complete terms", "gh-fix", "curated", "installed", "code-review",
    "From VoltAgent/awesome-agent-skills; kept in getsentry/skills", "1 skill that can't be installed here is hidden.",
    "Review and install", "Whatever you install from here arrives switched off, after its review.",
    "From skills.sh, uncurated, ranked by installs", HINT], ["Hosted elsewhere", "not installable here"]);
  const buttons = (html.match(/SKILL_STORE\.reviewEntry\(/g) || []).length;
  if (buttons !== 2) failures.push(`Browse offered Review and install ${buttons} times, not for the two installable entries alone.`);

  await run(page, `SKILL_STORE.reviewEntry(${JSON.stringify(resolved.entries[2].id)})`);
  await settle();
  same("Review and install", sentTo(page, "previews").map((call) => call.body), [resolved.entries[2].install]);

  run(page, "SKILL_STORE.cancelReview()");
  run(page, "SKILL_STORE.toggleBrowse()");
  await run(page, "SKILL_STORE.toggleBrowse()");
  await settle();
  if (sentTo(page, "market/resolve").length !== 1 || sentTo(page, "market/refresh").length !== 1) {
    failures.push("Browse opened again read the sources or looked the same links up again.");
  }

  type(page, "#skillQuery", "gh-fix");
  await run(page, "SKILL_STORE.search()");
  await settle();
  words("filtered by words", contentOf(page), ["gh-fix"], ["code-review", "Hosted elsewhere"]);
  same("Enter with every source chosen searches skills.sh too", sentTo(page, "market/search").map((call) => call.body), [{ query: "gh-fix" }]);

  await run(page, "SKILL_STORE.filterBy('skills_sh')");
  await settle();
  words("skills.sh alone", contentOf(page), [GROUP, "Search skills.sh"], ["Uncurated, ranked by installs."]);
  if (sentTo(page, "market/search").length !== 1) failures.push("choosing skills.sh asked it again for words whose answer the page holds.");
  type(page, "#skillQuery", "pdf");
  await run(page, "SKILL_STORE.search()");
  await settle();
  same("the search", sentTo(page, "market/search").map((call) => call.body).slice(-1), [{ query: "pdf" }]);
  words("skills.sh's results", contentOf(page), ["196349 installs", "From skills.sh; kept in anthropics/skills",
    "Its description and license show in its review."]);
  const answered = run(page, "SKILL_STORE.results.seq");
  await run(page, `SKILL_STORE.reviewResult(0,${answered - 1})`);
  await settle();
  if (sentTo(page, "previews").length !== 1) failures.push("a result's button from an earlier answer reviewed the result now in its place.");
  await run(page, `SKILL_STORE.reviewResult(0,${answered})`);
  await settle();
  same("a result's review", sentTo(page, "previews").map((call) => call.body).slice(-1), [SEARCH.results[0].install]);
  const searches = sentTo(page, "market/search").length;
  await run(page, `SKILL_STORE.confirm(${JSON.stringify(REVIEW.preview_id)})`);
  await settle();
  if (sentTo(page, "market/search").length !== searches + 1) failures.push("after Install, skills.sh's answer wasn't asked for again, so its installed badges stay old.");
}

const HINT = "skills.sh needs a search of at least 2 letters.";
const GROUP = "From skills.sh, uncurated, ranked by installs";

/* 4b — skills.sh as the owner types (NERVIS 0.33.1): the hint under two letters and nothing sent,
   a search 600 ms after typing stops or at once on Enter, a question overtaken by newer words
   cancelled and its answer never shown, the same words never asked twice, the results in their own
   group after the listed entries, and every refusal in plain words. */
async function skillsShIsSearchedAsTheOwnerTypes() {
  const cancelled = [];
  let reply = searchAnswer;
  const page = await drawPage({ market: () => answer(200, marketBody({ stale: false })), control: routes({
    "market/resolve": () => answer(200, { ...marketBody({ stale: false }), note: null }),
    /* Every answer takes 800 ms and the one to be overtaken 3 s, so each check below has a few
       hundred milliseconds either side of the page's own waits, even on a busy machine. */
    "market/search": (body, init) => slowly(body.query === "cha" ? 3000 : 800, (asked) => reply(asked), cancelled)(body, init) }) });
  await run(page, "SKILL_STORE.toggleBrowse()");
  await settle();
  const typing = async (value, ms) => { type(page, "#skillQuery", value); run(page, "SKILL_STORE.typed()"); await wait(ms); };
  const asked = () => sentTo(page, "market/search").map((call) => call.body.query);

  words("no words, every source", contentOf(page), [HINT, GROUP, "Filter and search skills.sh"]);
  await typing("c", 700);
  words("one letter", contentOf(page), [HINT]);
  await run(page, "SKILL_STORE.search()");
  await settle();
  if (asked().length) failures.push(`skills.sh was asked for fewer than two letters: ${JSON.stringify(asked())}.`);

  await typing("ch", 300);
  await typing("cha", 400);
  if (asked().length) failures.push("skills.sh was asked before typing had stopped for 600 ms.");
  await wait(500);
  same("asked once typing stopped", asked(), ["cha"]);
  words("while asking", contentOf(page), ["Searching skills.sh for “cha”…"]);

  await typing("chan", 900);
  same("newer words asked", asked(), ["cha", "chan"]);
  same("the question they overtook cancelled", cancelled.map((body) => body.query), ["cha"]);
  words("the newer question on its way", contentOf(page), ["Searching skills.sh for “chan”…"], ["cha-result"]);
  await wait(900);
  words("the newer answer", contentOf(page), ["chan-result", GROUP], ["cha-result", "Searching skills.sh"]);

  type(page, "#skillQuery", "pdf");
  await run(page, "SKILL_STORE.search()");
  await settle();
  same("Enter asks at once", asked(), ["cha", "chan", "pdf"]);
  const grouped = readable(contentOf(page));
  const listedAt = grouped.indexOf(MARKET.entries[0].description), groupAt = grouped.indexOf(GROUP);
  if (!(listedAt >= 0 && listedAt < groupAt && groupAt < grouped.indexOf("pdf-result"))) {
    failures.push("skills.sh's results aren't in a group of their own after the listed entries.");
  }
  await typing("pdf", 700);
  same("the same words not asked again", asked(), ["cha", "chan", "pdf"]);

  reply = () => answer(200, { ...copy(SEARCH), results: [], problem: "skills.sh refused the search: Query must be at least 2 characters." });
  await typing("docs", 1800);
  words("skills.sh's own refusal", contentOf(page), ["skills.sh refused the search: Query must be at least 2 characters."]);
  reply = () => answer(404, { error: { code: "MARKET_SOURCE_NOT_FOUND", message: "There is no source skills_sh in the list." } });
  await typing("docx", 1800);
  words("RAVIS's refusal", contentOf(page), ["There is no source skills_sh in the list."]);
  reply = failing;
  await typing("xlsx", 1800);
  words("no answer", contentOf(page), ["RAVIS didn't answer the search. Check the stack is running, then try again."]);

  await run(page, "SKILL_STORE.filterBy('skills_sh')");
  await typing("x", 700);
  const before = asked().length;
  await run(page, "SKILL_STORE.search()");
  await settle();
  words("skills.sh alone, one letter", contentOf(page), [HINT, GROUP, "Search skills.sh"]);
  if (asked().length !== before) failures.push("skills.sh chosen alone was asked for one letter.");
}

/* 4c — entries that can't be installed here, from any source: hidden, with one quiet line saying
   how many, and a Show them switch this browser remembers for its next visit. */
async function notInstallableEntriesAreHidden() {
  const market = marketBody({ stale: false });
  market.sources.push({ ...copy(market.sources[4]), id: "own-site", label: "example.com", kind: "website", site: "https://example.com" });
  market.entries.push({ ...copy(market.entries[3]), id: "own-site:broken", source: "own-site", name: "Broken index entry",
    description: "A website's skill whose index entry breaks the standard.", lives_in: "example.com",
    link: "https://example.com/broken", problem: "Its digest doesn't match its index." });
  const control = routes({ "market/resolve": () => answer(200, { ...market, note: null }) });
  const page = await drawPage({ market: () => answer(200, market), control });
  await run(page, "SKILL_STORE.toggleBrowse()");
  await settle();
  words("hidden at first", contentOf(page), ["2 skills that can't be installed here are hidden.", "Show them"],
    ["Hosted elsewhere", "Broken index entry", "not installable here"]);
  run(page, "SKILL_STORE.toggleUnavailable()");
  words("Show them", contentOf(page), ["Hosted elsewhere", "Broken index entry", "not installable here",
    "2 skills that can't be installed here are shown.", "Hide them"]);
  const key = run(page, "SKILL_UNAVAILABLE_KEY");
  if (run(page, `localStorage.getItem(${JSON.stringify(key)})`) !== "shown") failures.push("Show them wasn't remembered in this browser.");
  await run(page, "SKILL_STORE.filterBy('own-site')");
  words("one source's", contentOf(page), ["1 skill that can't be installed here is shown.", "Hide it", "Broken index entry"], ["Hosted elsewhere"]);
  run(page, "SKILL_STORE.toggleUnavailable()");
  words("Hide it", contentOf(page), ["1 skill that can't be installed here is hidden.", "Show it"], ["Broken index entry"]);
  if (run(page, `localStorage.getItem(${JSON.stringify(key)})`) !== "hidden") failures.push("Hide them wasn't remembered in this browser.");

  const later = await drawPage({ market: () => answer(200, market), control, storage: { [key]: "shown" } });
  await run(later, "SKILL_STORE.toggleBrowse()");
  await settle();
  words("a later visit", contentOf(later), ["2 skills that can't be installed here are shown.", "Hosted elsewhere"]);
}

/* 4d — the words filter every loaded entry as they are typed, not only the page shown, after a
   short wait; skills.sh hidden is neither asked nor mentioned; and a redraw keeps the words box's
   focus and caret, or a word typed into it would stop at the first redraw. */
async function theFilterFindsEntriesOnLaterPages() {
  const market = marketBody({ stale: false });
  const base = market.entries[0];
  const filler = Array.from({ length: 45 }, (_, i) => ({ ...copy(base), id: `anthropics::anthropics/skills/skills/filler-${i}`,
    name: `filler-${String(i).padStart(2, "0")}`, description: "Does one thing well.", license: null, installed: false }));
  const wanted = { ...copy(base), id: "anthropics::anthropics/skills/skills/changelog-generator", name: "changelog-generator",
    description: "Writes a changelog from commits.", license: null, installed: false };
  market.entries = [...filler, wanted, ...market.entries];
  market.sources[3] = { ...market.sources[3], hidden: true };
  const page = await drawPage({ market: () => answer(200, market), control: routes({
    "market/resolve": () => answer(200, { ...market, note: null }), "market/search": () => answer(200, SEARCH) }) });
  await run(page, "SKILL_STORE.toggleBrowse()");
  await settle();
  words("the first page", contentOf(page), ["filler-00", "Show more (", "Filter"], ["changelog-generator", HINT, GROUP]);

  type(page, "#skillQuery", "changelog");
  run(page, "SKILL_STORE.typed()");
  words("before the filter's wait", contentOf(page), ["filler-00"], ["changelog-generator"]);
  await wait(550);
  words("filtered as typed", contentOf(page), ["changelog-generator"], ["filler-00", "Show more ("]);
  await wait(400);
  if (sentTo(page, "market/search").length) failures.push("skills.sh was asked while it is hidden.");

  const box = { id: "skillQuery", tagName: "INPUT", selectionStart: 4, selectionEnd: 9, focused: false, range: null,
    focus() { this.focused = true; }, setSelectionRange(start, end) { this.range = [start, end]; } };
  const document = page.context.document, byId = document.getElementById;
  document.activeElement = box;
  document.getElementById = (id) => (id === "skillQuery" ? box : byId(id));
  run(page, "SKILLS_PAGE.draw()");
  document.getElementById = byId;
  document.activeElement = null;
  if (!box.focused || JSON.stringify(box.range) !== "[4,9]") {
    failures.push("a redraw of the Skills page dropped the words box's focus or caret, so the rest of a word typed into the filter would go nowhere.");
  }
}

/* 5 — adding, hiding and removing sources. */
async function sourcesAreAddedHiddenAndRemoved() {
  const page = await drawPage({ market: () => answer(200, marketBody({ stale: false })), control: routes({
    "market/sources": () => answer(200, marketBody({ stale: false })), "market/refresh": () => answer(200, marketBody({ stale: false })),
    "market/sources/hide": () => answer(200, marketBody({ stale: false })), "market/sources/remove": () => answer(200, marketBody({ stale: false })) }) });
  await run(page, "SKILL_STORE.toggleBrowse()");
  await settle();
  const html = contentOf(page);
  words("the sources", html, ["Sources", "comes with RAVIS", "added by you", "Hide", "GitHub repository",
    "Link list (a Markdown file of links)", "Website with an Agent Skills index (agentskills.io standard)", "Add source"]);
  const removals = (html.match(/SKILL_STORE\.askRemoveSource\(/g) || []).length;
  if (removals !== 1) failures.push(`${removals} sources offered Remove…, not only the owner's own.`);

  type(page, "#sourceRepository", "me/more");
  type(page, "#sourcePath", "skills");
  type(page, "#sourceRef", "dev");
  await run(page, "SKILL_STORE.addSource()");
  await settle();
  same("adding a GitHub source", sentTo(page, "market/sources").map((call) => call.body), [{ kind: "github", repository: "me/more", path: "skills", ref: "dev" }]);
  if (sentTo(page, "market/refresh").length !== 1) failures.push("a new source wasn't read after it was added.");

  run(page, "SKILL_STORE.addKindIs('search')");
  run(page, "SKILL_STORE.addKindIs('website')");
  type(page, "#sourceUrl", "https://example.com/docs");
  await run(page, "SKILL_STORE.addSource()");
  await settle();
  same("adding a website", sentTo(page, "market/sources").map((call) => call.body).slice(-1), [{ kind: "website", url: "https://example.com/docs" }]);

  await run(page, "SKILL_STORE.hideSource('openai',true)");
  same("hiding", sentTo(page, "market/sources/hide").map((call) => call.body), [{ source: "openai", hidden: true }]);
  await run(page, "SKILL_STORE.removeSource('own-mine')");
  if (sentTo(page, "market/sources/remove").length) failures.push("a source was removed on one click.");
  run(page, "SKILL_STORE.askRemoveSource('own-mine')");
  words("Remove…, asked", contentOf(page), ["Remove this source?", "Remove it", "Keep it"]);
  await run(page, "SKILL_STORE.removeSource('own-mine')");
  await settle();
  same("removing", sentTo(page, "market/sources/remove").map((call) => call.body), [{ source: "own-mine" }]);
}

/* 6 — refusals, in RAVIS's own words first. */
async function everyRefusalIsSaidPlainly() {
  const cases = [
    [() => answer(LIMITED.status, LIMITED.body), "GitHub is rate-limiting RAVIS; try again at 22:30."],
    [() => answer(404, {}), "This NERVIS or RAVIS doesn't have the skill store yet: NERVIS 0.33.0 and RAVIS 0.28.0 do."],
    [failing, "RAVIS didn't answer, so nothing changed."],
  ];
  for (const [refusal, said] of cases) {
    const page = await drawPage({ control: routes({ previews: refusal }) });
    type(page, "#skillLink", "https://github.com/anthropics/skills/tree/main/skills/pdf");
    await run(page, "SKILL_STORE.reviewLink()");
    await settle();
    words(`a refused review (${said})`, contentOf(page), [said], ["Review pdf before installing it"]);
  }
  const big = await drawPage({ control: routes({ "previews/zip": () => answer(413, { error: { code: "TOO_LARGE", message: "x" } }) }) });
  await run(big, "SKILL_STORE.reviewZip({files:[{name:'a.zip',size:9,arrayBuffer:async()=>'A'}]})");
  await settle();
  words("NERVIS refusing a zip", contentOf(big), ["The file is larger than 8 MB, more than RAVIS installs from."]);
  const old = await drawPage({ market: () => answer(404, {}) });
  await run(old, "SKILL_STORE.toggleBrowse()");
  await settle();
  words("a RAVIS without the marketplace", contentOf(old), ["This RAVIS doesn't have the marketplace yet; RAVIS 0.28.0 does."]);
}

/* 7 — nothing RAVIS sends injects markup or breaks a handler's quotes. */
async function nothingRavisSendsInjects() {
  const evil = `x'");alert(1);//<img src=x onerror=alert(2)>`;
  const review = { ...copy(REVIEW), preview_id: `sp_${evil}`, name: evil, description: evil,
    license: { text: evil, from: "front_matter" }, skill_md: evil, warnings: [{ code: evil, message: evil }],
    files: [{ path: evil, bytes: 1, kind: "script" }], source: { ...copy(REVIEW.source), repository: evil, folder: evil } };
  const market = marketBody({ stale: false });
  market.sources[4] = { ...market.sources[4], id: evil, label: evil, note: evil };
  market.entries[0] = { ...market.entries[0], id: evil, name: evil, description: evil, license: evil, lives_in: evil, label: evil };
  const installs = installsBody();
  installs.installs[0] = { ...installs.installs[0], name: evil, source: { origin: "github", repository: evil, commit: evil } };
  const page = await drawPage({ installs: () => answer(200, installs), market: () => answer(200, market),
    control: routes({ previews: () => answer(200, review),
      "market/search": (body) => answer(200, body.query === "pdf"
        ? { ...copy(SEARCH), results: [{ ...copy(SEARCH.results[0]), name: evil, repository: evil, installs: evil }] }
        : { ...copy(SEARCH), results: [], problem: evil }) }) });
  type(page, "#skillLink", "https://github.com/o/r");
  await run(page, "SKILL_STORE.reviewLink()");
  await run(page, "SKILL_STORE.toggleBrowse()");
  await settle();
  let html = contentOf(page);
  await run(page, "SKILL_STORE.filterBy('skills_sh')");
  type(page, "#skillQuery", "pdf");
  await run(page, "SKILL_STORE.search()");
  await settle();
  html += contentOf(page);
  await run(page, "SKILL_STORE.filterBy('all')");
  type(page, "#skillQuery", "docs");
  await run(page, "SKILL_STORE.search()");
  await settle();
  html += contentOf(page);
  if (html.includes("<img")) failures.push("markup RAVIS sent reached the Skills page unescaped.");
  for (const [, handler] of html.matchAll(/on(?:click|change|input|keydown)="([^"]*)"/g)) {
    const code = readable(handler);
    try {
      new vm.Script(code);
    } catch (error) {
      failures.push(`a handler no longer parses, so something broke out of its quotes: ${code}`);
      continue;
    }
    if (/alert\(/.test(code.replace(/'(?:[^'\\]|\\.)*'/g, "''"))) failures.push(`a handler runs something RAVIS sent: ${code}`);
  }
}

async function main() {
  await aLinkIsReviewedBeforeAnythingIsInstalled();
  await aZipIsSentAsItsOwnBytes();
  await anInstalledSkillCanBeUpdatedOrRemoved();
  await browseListsTheMarketplace();
  await skillsShIsSearchedAsTheOwnerTypes();
  await notInstallableEntriesAreHidden();
  await theFilterFindsEntriesOnLaterPages();
  await sourcesAreAddedHiddenAndRemoved();
  await everyRefusalIsSaidPlainly();
  await nothingRavisSendsInjects();

  finished = true;
  if (failures.length) {
    console.error("skill store check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "NERVIS → Skills' store holds: a GitHub link or a zip file is reviewed before anything is installed, " +
    "the review shows name, description, license, where it came from, files with scripts flagged, SKILL.md " +
    "as text, that it arrives switched off and the specification line, and Install sends only the review's " +
    "id; a zip over 8 MB is never sent; an installed skill shows where it came from with Update…, reviewed " +
    "first, and Remove…, two clicks to the Trash and never confirm(); Browse reads stale sources once, marks " +
    "openai deprecated and lists uncurated, looks shown links up once each and never one kept elsewhere, and " +
    "sends each entry's own install body; skills.sh is searched with every source or alone, 600 ms after " +
    "typing stops or on Enter, never under two letters, a stale question cancelled and its answer dropped, in a " +
    "labelled group of its own; entries that can't be installed here are hidden behind a remembered switch; the " +
    "words filter every loaded entry as they are typed and a redraw keeps the box's focus; sources are added, " +
    "hidden and removed as asked; refusals are said plainly; and nothing RAVIS sends injects"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
