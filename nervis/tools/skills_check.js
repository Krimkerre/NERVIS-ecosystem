/* NERVIS → Skills: every skill, and its switch for Codex and for the other models (NERVIS 0.32.0).
 *
 * The owner's decisions of 15 September 2026 gave skills a page of their own in NERVIS's main menu,
 * with one switch per skill for Codex and one for the other models — Clarvis's own engine and NERVIS
 * chat together — and moved the list off RAVIS → Dashboard's Codex card. This drives the real page
 * against RAVIS's own contract (`ravis/tests/fixtures/relay-contract/skills.json`) behind NERVIS,
 * and asserts what it draws and what it sends:
 *
 *   1. **Skills is in NERVIS's menu**, and its top line says where NERVIS's skills folder is, that a
 *      change counts for Codex from a task's next start or reopen, and for the other models from
 *      their next request.
 *   2. **Every skill is listed under where it comes from** — NERVIS's folder, your personal skills,
 *      built into Codex, in that order — with its name, description and where it lives, and two
 *      switches, Codex and Other models (Clarvis and NERVIS chat). A built-in skill shows the Codex
 *      switch alone and "not available to other models"; a skill RAVIS couldn't read says why and
 *      has no switch for the other models.
 *   3. **A switch takes one click and never `confirm()`**, says it is working until RAVIS answers,
 *      sends only the path, the engine and on or off through NERVIS's control route with the
 *      control header, redraws from RAVIS's answer, says when the change counts, and sends nothing
 *      for a skill or an engine RAVIS didn't offer.
 *   4. **Every refusal is said plainly and the list read again**; an unread list says why; and while
 *      Codex isn't running its switches say so, and the other models' still work.
 *   5. **Nothing RAVIS sends can inject markup**, nor break out of a quoted handler argument.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const failures = [];
/* A check that stops early proves nothing, and it can stop without failing: when every promise still
   waiting is one nothing will resolve, node empties its loop and exits 0 before any failure is
   printed. So leaving without reaching the end of `main` is a failure of its own. */
let finished = false;
process.on("exit", (code) => {
  if (!finished && code === 0) {
    console.error("skills check never finished: something it waited on was never answered, so it proved nothing.");
    process.exitCode = 1;
  }
});

/* RAVIS's contract, read rather than copied, so a change in RAVIS's answer reaches this gate. */
const CONTRACT = JSON.parse(readFileSync(
  join(__dirname, "..", "..", "ravis", "tests", "fixtures", "relay-contract", "skills.json"), "utf8"));
const response = (method, path, name) => CONTRACT.routes
  .find((route) => route.method === method && route.path === path)
  .examples.find((example) => example.name === name).response;
const BOARD = response("GET", "/api/v1/skills", "every skill with a switch for each engine").body;
const ALONE = response("GET", "/api/v1/skills", "Codex isn't running: RAVIS's reading alone").body;
const copy = (value) => JSON.parse(JSON.stringify(value));
const pathOf = (board, name) => board.skills.find((skill) => skill.name === name).path;

function answer(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status,
                           headers: { get: () => null }, json: async () => body });
}
const failing = () => Promise.reject(new TypeError("fetch failed"));
const answering = (status, body) => (status ? answer(status, body) : failing());
const refusal = (code, message, details = {}) => ({ error: { code, message, retryable: false, details } });

/* A recorded RAVIS behind NERVIS: `board()` answers the relayed GET /api/v1/skills and `control()`
   NERVIS's POST /api/v1/ravis/skills. Every request of any kind is kept in `requests` and every
   control call in `sent`, so a request the page must never make is seen wherever it was aimed. */
function world({ board = () => answer(200, BOARD), control = () => answer(500, {}) } = {}) {
  const ravis = { reads: 0 };
  const sent = [];
  const requests = [];
  const fetchImpl = (url, init = {}) => {
    const address = String(url);
    const method = (init.method || "GET").toUpperCase();
    requests.push({ address, method });
    if (method === "GET" && address.endsWith("/api/v1/relay/ravis/api/v1/skills")) {
      ravis.reads += 1;
      return board();
    }
    if (address.endsWith("/api/v1/ravis/skills")) {
      sent.push({ method, headers: init.headers || {}, body: init.body == null ? null : JSON.parse(init.body) });
      return control();
    }
    return failing();
  };
  const loaded = loadPage({ fetchImpl });
  vm.runInContext("stopPolling()", loaded.context);
  loaded.context.confirm = () => {
    failures.push("the Skills page called confirm(), which a browser can mute for good.");
    return true;
  };
  return { ...loaded, ravis, sent, requests };
}

const run = (page, code) => vm.runInContext(code, page.context);
const settle = () => new Promise((done) => setTimeout(done, 30));
/* The page draws its default screen, the Overview, as it loads; the screen under test is drawn once
   that draw has had time to land, so it can't overwrite this one. */
const quiet = () => new Promise((done) => setTimeout(done, 250));

async function drawPage(options) {
  const page = world(options);
  await quiet();
  page.exported.state.app = "nervis";
  page.exported.state.view = "Skills";
  await run(page, "nervis()");
  return page;
}

function contentOf(page) {
  const content = page.elements.get("sel:#content");
  return content ? content.innerHTML : "";
}

/* The words a person reads: the page escapes ' and " as it writes them. The injection check reads
   the markup itself, never this. */
const readable = (html) => html.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
function words(label, html, present, absent = []) {
  const text = readable(html);
  for (const part of present) if (!text.includes(part)) failures.push(`${label}: missing ${JSON.stringify(part)}.`);
  for (const part of absent) if (text.includes(part)) failures.push(`${label}: should not show ${JSON.stringify(part)}.`);
}
const toggleOf = (path, engine) => `SKILLS_PAGE.toggle(${JSON.stringify(path)},${JSON.stringify(engine)})`;
/* A switch as the page writes it into its markup: path and engine in single quotes, in the handler. */
const toggleMarkup = (path, engine, label) => `SKILLS_PAGE.toggle('${path}','${engine}')">${label}</button>`;

/* 1 and 2 — the menu, the top line, the groups, and each skill's switches. */
async function everySkillIsListedWithItsSwitches() {
  const page = await drawPage();
  if (!run(page, "APP_CONFIG.nervis.nav.includes('Skills')")) failures.push("NERVIS's menu has no Skills entry.");
  const html = contentOf(page);
  words("the top line", html, [BOARD.folders.nervis.path, BOARD.folders.personal.path,
    "next start or reopen", "next request", "NERVIS chat"]);
  const order = ["<h3>In NERVIS&#39;s skills folder</h3>", "half-written", "nervis-notes", "<h3>Your personal skills</h3>",
    "graphify", "<h3>Built into Codex</h3>", "imagegen"];
  let from = 0;
  for (const part of order) {
    const at = html.indexOf(part, from);
    if (at < 0) {
      failures.push(`the skills aren't grouped under where they come from, in RAVIS's order (lost at ${JSON.stringify(part)}).`);
      break;
    }
    from = at + part.length;
  }
  words("each skill", html, ["How NERVIS tasks keep their notes.", pathOf(BOARD, "nervis-notes"),
    "Turn any input into a knowledge graph.", pathOf(BOARD, "graphify"), "Generate or edit images.",
    "Codex", "Other models (Clarvis and NERVIS chat)", "not available to other models",
    "its SKILL.md's front matter has no description"]);
  const notes = pathOf(BOARD, "nervis-notes"), graphify = pathOf(BOARD, "graphify");
  const imagegen = pathOf(BOARD, "imagegen"), half = pathOf(BOARD, "half-written");
  for (const [path, engine, label, name] of [
    [notes, "codex", "Switch off", "nervis-notes for Codex"], [notes, "models", "Switch off", "nervis-notes for the other models"],
    [graphify, "codex", "Switch on", "graphify for Codex"], [graphify, "models", "Switch on", "graphify for the other models"],
    [imagegen, "codex", "Switch off", "imagegen for Codex"],
  ]) {
    if (!html.includes(toggleMarkup(path, engine, label))) failures.push(`${name} has no ${label}.`);
  }
  if (html.includes(`SKILLS_PAGE.toggle('${imagegen}','models')`)) failures.push("a built-in skill offers a switch for the other models.");
  if (html.includes(`SKILLS_PAGE.toggle('${half}',`)) failures.push("a skill RAVIS couldn't read, and Codex doesn't list, offers a switch.");
  if (page.ravis.reads !== 1) failures.push(`drawing the page read the skills ${page.ravis.reads} times, not once.`);
}

/* 3 — one click, the pending words, only the path, engine and switch sent, and RAVIS's answer drawn. */
async function aSwitchTakesOneClickAndSaysWhatRavisDid() {
  let board = copy(BOARD);
  const graphify = pathOf(board, "graphify");
  /* Each answer RAVIS owes, released together once the pending state has been looked at; each also
     releases itself after a moment, so no request is ever left waiting for good. */
  const owed = [];
  const page = await drawPage({ board: () => answer(200, board), control: () => new Promise((done) => {
    const release = () => {
      board = { ...board, skills: board.skills.map((skill) =>
        (skill.path === graphify ? { ...skill, models: { available: true, enabled: true } } : skill)) };
      done(answer(200, board));
    };
    owed.push(release);
    setTimeout(release, 1500);
  }) });

  await run(page, toggleOf("/Users/owner/.agents/skills/not-listed/SKILL.md", "models"));
  await run(page, toggleOf(pathOf(board, "imagegen"), "models"));
  await run(page, toggleOf(pathOf(board, "half-written"), "models"));
  await run(page, toggleOf(graphify, "clarvis"));
  await run(page, toggleOf(graphify, "constructor"));
  if (page.sent.length) failures.push("a switch was sent for a skill or an engine RAVIS didn't offer.");

  const clicked = run(page, toggleOf(graphify, "models"));
  await settle();
  const [sent] = page.sent;
  if (!sent || sent.method !== "POST" || !("x-nervis-control" in sent.headers)
      || JSON.stringify(sent.body) !== JSON.stringify({ path: graphify, engine: "models", enabled: true })) {
    failures.push("one click on the other models' Switch on didn't send POST /api/v1/ravis/skills with the control header and only the path, the engine and enabled: true.");
  }
  words("a switch, waiting", contentOf(page), ["switching on…"], [toggleMarkup(graphify, "models", "Switch on")]);
  // A second click while the first waits: not awaited, since a second request would wait too.
  run(page, toggleOf(graphify, "codex"));
  await settle();
  if (page.sent.length !== 1) failures.push("a second click while a switch was waiting sent another.");
  owed.forEach((release) => release());
  await clicked;
  await settle();
  words("after the switch", contentOf(page), ["graphify switched on for the other models", "next request"], ["switching on…"]);
  if (!contentOf(page).includes(toggleMarkup(graphify, "models", "Switch off"))) {
    failures.push("after switching graphify on for the other models, its row doesn't offer Switch off.");
  }
  if (!contentOf(page).includes(toggleMarkup(graphify, "codex", "Switch on"))) {
    failures.push("switching graphify for the other models changed what its Codex switch shows.");
  }
  if (page.ravis.reads !== 1) failures.push("the page read the list again although RAVIS answered with it.");

  const codexPage = await drawPage({ control: () => answer(200, BOARD) });
  await run(codexPage, toggleOf(graphify, "codex"));
  await settle();
  const [codexSent] = codexPage.sent;
  if (!codexSent || JSON.stringify(codexSent.body) !== JSON.stringify({ path: graphify, engine: "codex", enabled: true })) {
    failures.push("Codex's Switch on didn't send only the path, engine codex and enabled: true.");
  }
  words("a Codex switch", contentOf(codexPage), ["graphify switched on for Codex", "next start or reopen"]);
}

/* 4 — refusals said and the list read again; an unread list; Codex not running. */
async function everyRefusalIsSaidAndTheListReadAgain() {
  const graphify = pathOf(BOARD, "graphify");
  const cases = [
    ["Codex not taking it", "codex", 409, refusal("SKILL_NOT_CHANGED", "Codex didn't take that change, so the skill stays as it was.",
      { skill: "graphify", reason: "not_written" }), ["Codex didn't take that change, so graphify stays off for Codex", "Codex didn't switch it"]],
    ["a skill RAVIS no longer lists", "models", 404, refusal("SKILL_NOT_FOUND",
      "RAVIS doesn't list a skill at that path for that engine, so nothing was changed.", { engine: "models" }),
      ["RAVIS no longer lists graphify for the other models, so nothing changed"]],
    ["Codex not running", "codex", 503, refusal("CODEX_RUNTIME_UNAVAILABLE", "Codex isn't running, so its skills can't be read; try again in a moment."),
      ["Codex isn't running, so graphify stays off for Codex"]],
    ["RAVIS refusing NERVIS's key", "models", 403, refusal("FORBIDDEN", "An admin credential is required."), ["admin key"]],
    ["NERVIS restarted", "models", 403, refusal("CONTROL_TOKEN_REQUIRED", "The page's control token is missing."), ["Reload the page"]],
    ["a NERVIS or RAVIS from before these switches", "models", 404, { detail: "Not Found" }, ["NERVIS 0.32.0 and RAVIS 0.27.0"]],
    ["NERVIS not answering", "models", 0, null, ["NERVIS didn't answer"]],
  ];
  for (const [label, engine, status, body, expected] of cases) {
    const page = await drawPage({ control: () => answering(status, body) });
    const reads = page.ravis.reads;
    await run(page, toggleOf(graphify, engine));
    await settle();
    words(`a switch refused: ${label}`, contentOf(page), expected, ["switched on for", "switching on…"]);
    if (page.ravis.reads === reads) failures.push(`a switch refused (${label}) didn't read the list again.`);
  }

  const unread = [
    ["a RAVIS from before skills for every engine", () => answer(404, { detail: "Not Found" }), ["RAVIS 0.27.0"]],
    ["RAVIS refusing the read", () => answer(403, refusal("FORBIDDEN", "A NERVIS or admin credential is required to read the skills.")),
     ["refused NERVIS's read of the skills"]],
    ["RAVIS not answering", failing, ["didn't answer the read of the skills"]],
    ["an answer that isn't a list", () => answer(200, { skills: "none" }), ["not with a list of skills"]],
  ];
  for (const [label, board, expected] of unread) {
    words(`the skills, ${label}`, contentOf(await drawPage({ board })), expected, ["Switch on", "Switch off"]);
  }

  const alone = await drawPage({ board: () => answer(200, ALONE) });
  const html = contentOf(alone);
  const lonely = pathOf(ALONE, "graphify");
  words("Codex not running", html, ["Codex isn't running", "waits until Codex is running",
    "built-in skills can't be listed"], [toggleMarkup(lonely, "codex", "Switch on")]);
  if (!html.includes(toggleMarkup(lonely, "models", "Switch on"))) {
    failures.push("while Codex isn't running, the other models' switch is gone too.");
  }
  await run(alone, toggleOf(lonely, "codex"));
  if (alone.sent.length) failures.push("a Codex switch was sent while RAVIS said Codex isn't running.");
}

/* 5 — nothing RAVIS sends injects markup or breaks out of a handler's quoted argument. */
async function nothingRavisSendsInjects() {
  const tag = "<vxs vxatr=1>";
  const quote = "x');vxjs('";
  const board = {
    folders: { nervis: { path: "/n" + tag, problem: "folder" + tag }, personal: { path: "/p" + quote, problem: null } },
    codex: { listed: true, problem: "codex" + tag },
    skills: [
      { id: "nervis/a" + tag, name: "name" + tag, description: "said" + tag, source: "nervis", path: "/a/" + quote + "/SKILL.md",
        problem: "problem" + tag, codex: { available: true, enabled: true }, models: { available: true, enabled: false } },
      { id: "personal/b", name: quote, description: quote, source: "personal", path: "/b/SKILL.md" + tag, problem: null,
        codex: { available: true, enabled: false }, models: { available: true, enabled: true } },
    ],
  };
  const page = await drawPage({ board: () => answer(200, board),
    control: () => answer(409, refusal("SKILL_NOT_CHANGED", "kept" + tag, { skill: "name" + tag, reason: "why" + tag })) });
  const drawn = [contentOf(page)];
  await run(page, toggleOf(board.skills[0].path, "codex"));
  await settle();
  drawn.push(contentOf(page));
  if (!drawn.join("").includes("vxs")) failures.push("the injection probe never reached the Skills page, so it proved nothing.");
  for (const html of drawn) {
    if (/<vxs|"\s*vxatr=/.test(html)) {
      const at = Math.max(0, html.search(/<vxs|"\s*vxatr=/) - 80);
      failures.push(`RAVIS's text broke out of the Skills page's markup: …${html.slice(at, at + 140)}…`);
    }
    if (html.includes("');vxjs('")) failures.push("RAVIS's text broke out of a quoted handler argument on the Skills page.");
  }
}

async function main() {
  await everySkillIsListedWithItsSwitches();
  await aSwitchTakesOneClickAndSaysWhatRavisDid();
  await everyRefusalIsSaidAndTheListReadAgain();
  await nothingRavisSendsInjects();

  finished = true;
  if (failures.length) {
    console.error("skills check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "NERVIS → Skills holds: it is in NERVIS's menu; its top line names NERVIS's skills folder and says " +
    "when a change counts for Codex and for the other models; every skill is listed under where it " +
    "comes from with its description, where it lives and a switch for Codex and one for the other " +
    "models, a built-in skill with Codex's alone and a skill RAVIS couldn't read with its problem; a " +
    "switch takes one click, never confirm(), says it is working, sends only the path, the engine and " +
    "the switch, redraws from RAVIS's answer and sends nothing RAVIS didn't offer; every refusal and " +
    "unread list is said and the list read again; Codex not running leaves the other models' switches " +
    "working; and nothing RAVIS sends injects"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
