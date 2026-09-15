/* NERVIS chat's slash commands and their pop-up (NERVIS 0.34.0).
 *
 * The owner's decisions of 15 September 2026: `/skill-name request` asks for a skill switched on
 * for Other models by its own name, `/skill <name or id> request` always reaches one, NERVIS chat
 * has three built-ins of its own — /help, /clear and /model — and a pop-up lists them as `/` is
 * typed. This drives the real page, with NERVIS's `GET /api/v1/chat/skills` and `POST /api/v1/chat`
 * answered by a recorded world, and asserts what the page draws, sends and refuses:
 *
 *   1. **Parsing.** A built-in counts only as the whole message, and /model takes an id. A first
 *      word nothing is called gets one line, a skill with nothing to do asks what to do, a slash
 *      mid-sentence or a path is a question, a request keeps its case, and `/?` or `/(` breaks
 *      nothing. A skill named like a built-in loses the short form to it and is reached with
 *      `/skill name`; two skills sharing a name are reached by id; a switched-off skill is unknown.
 *   2. **Local lines.** /help lists the three built-ins and every switched-on skill as it is typed,
 *      with its description and how to reach a clashing or duplicate one. What a command answers
 *      on the page is never sent, never read aloud, never counted as a turn, never kept in the
 *      browser's saved copy, and drawn as text. A skill asked for by name is sent with its id, as
 *      typed.
 *   3. **Clearing and choosing the model.** /clear goes through New chat's own path and deletes nothing. /model
 *      says which model answers and opens the picker; /model <id> goes through the picker's own
 *      path for this conversation only, and an id the picker doesn't offer is never guessed.
 *   4. **The pop-up** filters as you type, moves with the arrows, completes on Enter, Tab or a
 *      click with a trailing space, closes on Escape, shows only while the first word is typed,
 *      takes no key while closed, is a listbox the chat box points into, draws with `textContent`
 *      only, and reads the list when the chat panel opens and at most once a minute while typing.
 *   5. **An input method composing** owns Enter and the arrows.
 *   6. **While the latest reply waits for a click, or a reply streams**, a skill asked for by name
 *      is refused with one line (waiting) or stays in the box unsent (streaming); /help and /model
 *      still answer; /clear and /model <id> ask to finish or stop first.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
let checked = 0;
/* A check that stops early proves nothing, and it can stop without failing: when every promise still
   waiting is one nothing will resolve, node empties its loop and exits 0 before any failure is
   printed. So leaving without reaching the end of `main` is a failure of its own. */
let finished = false;
process.on("exit", (code) => {
  if (!finished && code === 0) {
    console.error("slash check never finished: something it waited on was never answered, so it proved nothing.");
    process.exitCode = 1;
  }
});

function check(label, ok, detail = "") {
  checked += 1;
  if (!ok) failures.push(detail ? `${label}: ${detail}` : label);
}
function same(label, actual, expected) {
  const got = JSON.stringify(actual);
  const wanted = JSON.stringify(expected);
  check(label, got === wanted, `got ${got}, expected ${wanted}`);
}

const HOSTILE = '<img src=x onerror="alert(1)"></span><b>&amp;';
const SKILLS = [
  { id: "nervis/changelog-generator", name: "changelog-generator", description: "Writes a changelog from the git history." },
  { id: "personal/help", name: "help", description: "A personal skill that happens to be called help." },
  { id: "nervis/pdf", name: "pdf", description: "Reads PDFs, from NERVIS's folder." },
  { id: "personal/pdf", name: "pdf", description: "Reads PDFs, the owner's own copy." },
  { id: "personal/hostile", name: "hostile", description: HOSTILE },
];
/* An offer as NERVIS sends one, the shape `export_check.js` uses. */
const OFFER = {
  proposal_id: "pr_export", operation: "nervis.conversation.export", service: "nervis",
  target: "conversation.md", summary: "export this conversation to conversation.md",
  ready: true, action: "Export", candidates: [], history: null,
};

const settle = (ms = 40) => new Promise((done) => setTimeout(done, ms));
/* The page draws its default screen as it loads; the Chat screen is drawn once that has landed. */
const quiet = () => new Promise((done) => setTimeout(done, 250));
const isList = (request) => request.method === "GET" && request.address.endsWith("/api/v1/chat/skills");

/* An element that keeps what was done to it: attributes, children, the text it was given, and every
   `innerHTML` written to it, which the pop-up must never do. `textContent` escapes into `innerHTML`
   the way `page_context.js`'s own stub does, so nothing else on the page reads differently. */
function recording(tag) {
  const attributes = new Map();
  let text = "";
  let html = "";
  const node = {
    tagName: String(tag).toUpperCase(), id: "", className: "", hidden: false, value: "",
    selectionStart: null, children: [], style: {}, dataset: {}, htmlWrites: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute(name, value) { attributes.set(name, String(value)); },
    getAttribute(name) { return attributes.has(name) ? attributes.get(name) : null; },
    removeAttribute(name) { attributes.delete(name); },
    hasAttribute(name) { return attributes.has(name); },
    appendChild(child) { this.children.push(child); return child; },
    replaceChildren(...children) { this.children = children; },
    setSelectionRange(start) { this.selectionStart = start; },
    focus() {}, blur() {}, remove() {}, scrollIntoView() {},
    addEventListener() {}, removeEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({ top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }),
  };
  Object.defineProperty(node, "textContent", {
    get: () => text,
    set(value) {
      text = String(value);
      html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    },
  });
  Object.defineProperty(node, "innerHTML", {
    get: () => html,
    set(value) { node.htmlWrites.push(String(value)); html = String(value); },
  });
  return node;
}
const everyNode = (node) => [node, ...(node.children || []).flatMap(everyNode)];

/* One streamed reply, "ok", as NERVIS relays RAVIS's frames. */
function streamed() {
  const frames = [
    `data: ${JSON.stringify({ model: "fake-1b", choices: [{ delta: { content: "ok" } }] })}\n\n`,
    "data: [DONE]\n\n",
  ].map((frame) => Buffer.from(frame, "utf8"));
  let at = 0;
  const headers = { "x-conversation-id": "c_slash" };
  return {
    ok: true, status: 200, headers: { get: (name) => headers[name] || null }, json: async () => ({}),
    body: { getReader: () => ({ read: async () => (at < frames.length
      ? { value: frames[at++], done: false } : { value: undefined, done: true }) }) },
  };
}

/* The page on the Chat screen with RAVIS usable, its chat box and pop-up recorded, NERVIS's list
   answered with `listed` (null: NERVIS not answering), and every chat request kept in `sent`. */
async function world({ listed = { read: true, skills: SKILLS } } = {}) {
  const requests = [];
  const sent = [];
  const fetchImpl = (url, init = {}) => {
    const address = String(url);
    const method = (init.method || "GET").toUpperCase();
    requests.push({ address, method });
    if (method === "GET" && address.endsWith("/api/v1/chat/skills")) {
      if (!listed) return Promise.reject(new TypeError("fetch failed"));
      return Promise.resolve({ ok: true, status: 200, headers: { get: () => null },
                               json: async () => JSON.parse(JSON.stringify(listed)) });
    }
    if (method === "POST" && address.endsWith("/api/v1/chat")) {
      sent.push(JSON.parse(init.body));
      return Promise.resolve(streamed());
    }
    return Promise.reject(new TypeError("fetch failed"));
  };
  const page = loadPage({ fetchImpl });
  const run = (code) => vm.runInContext(code, page.context);
  run("stopPolling()");
  await quiet();
  const input = recording("input");
  const menu = recording("div");
  for (const key of ["chatInput", "sel:#chatInput"]) page.elements.set(key, input);
  for (const key of ["slashMenu", "sel:#slashMenu"]) page.elements.set(key, menu);
  page.context.document.createElement = (tag) => recording(tag);
  page.context.__spoken = [];
  page.context.__starts = [];
  page.context.__picks = [];
  run(`SERVICES.ravis.state='healthy'; state.app='nervis'; state.view='Chat';
       CHAT_SESSION.greeted=true; CHAT_SESSION.busy=false; CHAT_SESSION.messages=[];
       SPEECH.say=(...said)=>{__spoken.push(said)};
       {const start=CHAT_SESSIONS.start.bind(CHAT_SESSIONS);CHAT_SESSIONS.start=()=>{__starts.push(1);return start()}}
       {const pick=MODELPICK.pick.bind(MODELPICK);MODELPICK.pick=(...chosen)=>{__picks.push(chosen);return pick(...chosen)}}`);
  const w = { page, run, requests, sent, input, menu };
  w.skills = (skills = SKILLS) => run(`SLASH.skills=${JSON.stringify(skills)}; SLASH.askedAt=Date.now()`);
  /* A message typed and sent through the page's own send. */
  w.send = async (text) => {
    input.value = text;
    input.selectionStart = text.length;
    await run("sendChat()");
    await settle();
  };
  /* Typing into the chat box through its own input handler. */
  w.type = (text, cursor = text.length) => {
    input.value = text;
    input.selectionStart = cursor;
    input.oninput();
  };
  w.last = (back = 1) => run(`CHAT_SESSION.messages.at(-${back})`);
  w.labels = () => menu.children.map((row) => row.children[0].textContent);
  w.content = () => (page.elements.get("sel:#content") || {}).innerHTML || "";
  return w;
}

function key(name, extra = {}) {
  const event = { key: name, keyCode: 0, isComposing: false, prevented: false, stopped: false, ...extra };
  event.preventDefault = () => { event.prevented = true; };
  event.stopPropagation = () => { event.stopped = true; };
  return event;
}

async function parsing() {
  const w = await world();
  const parse = (text, skills = SKILLS) =>
    JSON.parse(JSON.stringify(w.run(`slashParse(${JSON.stringify(text)},${JSON.stringify(skills)})`)));
  const kindOf = (text, skills) => parse(text, skills).kind;

  same("a built-in is the whole message", parse("/help"), { kind: "builtin", name: "help", arg: "" });
  same("... in any case, and with space around it", [parse("/HELP").name, parse("  /clear  ").name], ["help", "clear"]);
  same("/model on its own", parse("/model"), { kind: "builtin", name: "model", arg: "" });
  same("/model takes an id, kept as typed", parse("/model ravis/openai/GPT-5").arg, "ravis/openai/GPT-5");
  same("any other built-in with something after it isn't one", parse("/help me"), { kind: "extra", name: "help", clash: true });
  same("... and says whether a skill has its name", parse("/clear everything"), { kind: "extra", name: "clear", clash: false });
  same("a first word nothing is called", parse("/nope do it"), { kind: "unknown", word: "/nope" });
  same("a skill with nothing to do", [kindOf("/changelog-generator"), parse("/changelog-generator").word], ["no-request", "/changelog-generator"]);
  const asked = parse("/changelog-generator Write a SHORT changelog  for v2");
  same("a skill by its own name, the request in its typed case",
       [asked.kind, asked.skill.id, asked.request], ["skill", "nervis/changelog-generator", "Write a SHORT changelog  for v2"]);
  same("a slash mid-sentence is a question", kindOf("what does /help do?"), "message");
  same("a message opening with a path is a question", kindOf("/usr/bin/python is broken"), "message");
  for (const odd of ["/?", "/(", "/[a-z]+", "/.*", "/\\"]) {
    let result = null;
    try { result = parse(odd); } catch (failure) { result = { threw: String(failure) }; }
    same(`${odd} is simply a word nothing is called`, result, { kind: "unknown", word: odd });
  }
  const shared = parse("/pdf summarise this");
  same("two skills sharing a name are named by id", [shared.kind, shared.skills.map((s) => s.id)], ["duplicate", ["nervis/pdf", "personal/pdf"]]);
  same("... through /skill with the name too", kindOf("/skill pdf summarise"), "duplicate");
  const byId = parse("/skill nervis/pdf Summarise IT");
  same("/skill with a full id", [byId.kind, byId.skill.id, byId.request], ["skill", "nervis/pdf", "Summarise IT"]);
  const clash = parse("/skill help What is on");
  same("/skill reaches a skill named like a built-in", [clash.kind, clash.skill.id], ["skill", "personal/help"]);
  same("/skill with nothing after it", kindOf("/skill"), "bare-skill");
  same("/skill with a name nothing has", parse("/skill ghost do it"), { kind: "unknown", word: "/skill ghost" });
  same("a switched-off skill is unknown, even typed", kindOf("/graphify draw it"), "unknown");
  same("with the list unread, a skill can't be told", kindOf("/changelog-generator do it", null), "unread");
  same("... and a built-in still can", kindOf("/help", null), "builtin");
}

async function localLines() {
  const w = await world();
  w.skills();
  await w.send("/help");
  const lines = w.run("CHAT_SESSION.messages.map(m=>[m.role,m.kind,m.stored===false])");
  same("/help answers with two local lines", lines, [["user", "local", true], ["assistant", "local", false]]);
  const help = w.last().text;
  for (const part of [
    "/help — List these commands", "/clear — Start a new conversation. This one stays under History.",
    "/model — Say which model answers this conversation", "/changelog-generator — Writes a changelog from the git history.",
    "/skill help — A personal skill that happens to be called help.", "/skill nervis/pdf — Reads PDFs, from NERVIS's folder.",
    "/skill personal/pdf — Reads PDFs, the owner's own copy.", "/skill and a name or id always reaches a switched-on skill",
    "two skills sharing a name with /skill and the full id",
  ]) check(`/help says ${JSON.stringify(part)}`, help.includes(part));
  same("nothing was sent to NERVIS", w.sent.length, 0);
  same("nothing was read aloud", w.page.context.__spoken.length, 0);
  check("the chat box is emptied, and forgotten as typed in", w.input.value === "" && !w.run("TOUCHED.has('chatInput')"));
  const drawn = w.content();
  check("a local line is drawn as text: a skill's description can't become markup",
        !drawn.includes("<img src=x") && drawn.includes("&lt;img src=x"));
  check("a local line says it was answered here", drawn.includes("answered here, not sent to a model"));
  w.run("CHAT_SESSION.messages.push({role:'user',text:'a real question',at:'x'},{role:'assistant',text:'an answer'})");
  w.run("CHAT_STORE.save(CHAT_SESSION)");
  same("local lines are left out of the browser's saved copy",
       w.run("CHAT_STORE.read().find(c=>c.conversation_id===CHAT_SESSION.id).messages.map(m=>m.text)"),
       ["a real question", "an answer"]);
  same("local lines aren't counted as turns", w.run("CHAT_SESSION.messages.filter(wroteTurn).length"), 1);
  same("a request doesn't take a local line as its question",
       w.run("CHAT_SESSION.messages.splice(-2); chatRequestBody('').content"), "");
  w.run("CHAT_SESSION.messages=[]");

  const typed = "/changelog-generator Write a SHORT changelog for v2";
  await w.send(typed);
  same("a skill asked for by name is sent, with its id and the message as typed",
       w.sent.map((body) => [body.skill, body.content]), [["nervis/changelog-generator", typed]]);
  same("the conversation shows it as typed, as an ordinary turn",
       w.run("CHAT_SESSION.messages.map(m=>[m.role,m.text,m.kind||''])"), [["user", typed, ""], ["assistant", "ok", ""]]);
  await w.send("and what about /help?");
  same("a slash mid-sentence is sent as an ordinary question, with no skill",
       [w.sent.length, "skill" in w.sent[1], w.sent[1].content], [2, false, "and what about /help?"]);

  for (const [text, line] of [
    ["/nope do it", "There's no command or switched-on skill called /nope. /help lists them."],
    ["/changelog-generator", "What should changelog-generator do? Type your request after /changelog-generator. Nothing was sent."],
    ["/pdf summarise it", "2 switched-on skills are called pdf, so nothing was sent. Type /skill nervis/pdf or /skill personal/pdf and your request instead."],
    ["/help me", "/help takes nothing after it, so nothing was sent. Type /help on its own, or /skill help and your request for the skill called help."],
    ["/?", "There's no command or switched-on skill called /?. /help lists them."],
    ["/skill ghost do it", "There's no switched-on skill called ghost. /help lists them."],
    ["/skill", "/skill needs a skill's name or id and then your request"],
  ]) {
    await w.send(text);
    const last = w.last();
    check(`${text} gets one local line`, last.kind === "local" && last.text.startsWith(line), `got ${JSON.stringify(last.text)}`);
  }
  same("none of those was sent", w.sent.length, 2);

  w.run("SLASH.skills=null");
  const asked = w.requests.filter(isList).length;
  await w.send("/changelog-generator write it");
  check("a list NERVIS couldn't read is said plainly",
        w.last().text.startsWith("NERVIS couldn't read the switched-on skills from RAVIS just now, so /changelog-generator wasn't sent."));
  same("... the list is asked for again, and nothing is sent", [w.requests.filter(isList).length, w.sent.length], [asked + 1, 2]);
}

async function clearAndModel() {
  const w = await world();
  w.skills();
  w.run("CHAT_SESSION.messages=[{role:'user',text:'keep me',at:'x'},{role:'assistant',text:'kept',served:'fake-1b'}]");
  const before = w.run("CHAT_SESSION.id");
  await w.send("/clear");
  same("/clear goes through New chat's own path", w.page.context.__starts.length, 1);
  check("... into a new, empty conversation", w.run("CHAT_SESSION.id") !== before);
  check("... and the old one stays under History",
        w.run(`CHAT_STORE.read().some(c=>c.conversation_id===${JSON.stringify(before)})`));
  check("... with nothing deleted", !w.requests.some((request) => request.method === "DELETE"));

  const m = await world();
  m.skills();
  m.run(`CHAT_SESSION.profile='ravis/chat'; CHAT_SESSION.drawer=null;
         CHAT_SESSION.messages=[{role:'user',text:'hi',at:'x'},{role:'assistant',text:'hello',served:'fake-1b'}];
         localStorage.setItem('nervis.chat.profile','ravis/chat');
         MODELPICK.groups=async()=>[{name:'RAVIS pools',items:[{id:'ravis/chat'},{id:'ravis/coding'}]},
                                    {name:'OpenAI',items:[{id:'ravis/openai/gpt-5'}]}]`);
  await m.send("/model");
  const said = m.last().text;
  check("/model says which model answers this conversation",
        said.includes("answered through ravis/chat") && said.includes("the last reply came from fake-1b"), said);
  same("/model opens the model picker", m.run("CHAT_SESSION.drawer"), "model");
  await m.send("/model ravis/coding");
  same("/model <id> goes through the picker's own path, for this conversation only",
       JSON.parse(JSON.stringify(m.page.context.__picks)), [["ravis/coding", true]]);
  same("... sets it, and says so", [m.run("CHAT_SESSION.profile"), m.last().text.split(".")[0]],
       ["ravis/coding", "This conversation now routes through ravis/coding"]);
  same("... without changing what new conversations start with",
       m.run("localStorage.getItem('nervis.chat.profile')"), "ravis/chat");
  await m.send("/model ravis/codin");
  same("an id the picker doesn't offer is never guessed", [m.run("CHAT_SESSION.profile"), m.page.context.__picks.length], ["ravis/coding", 1]);
  check("... and gets a line saying how to see the choices",
        m.last().text.startsWith("The model picker offers no pool or model called ravis/codin, so nothing changed. Type /model on its own"));
  same("none of it was sent to a model", m.sent.length, 0);
}

async function popUp() {
  const w = await world();
  w.skills();
  await w.run("nervis()");
  await settle();
  check("the chat box's keys go through composerKey", w.input.onkeydown === w.run("composerKey"));
  const drawn = w.content();
  for (const part of ['id="slashMenu"', 'role="listbox"', 'role="combobox"', 'aria-controls="slashMenu"', 'aria-expanded="false"']) {
    check(`the chat panel draws ${part}`, drawn.includes(part));
  }

  w.type("/");
  same("'/' opens every command and switched-on skill", w.labels(),
       ["/help", "/clear", "/model", "/changelog-generator", "/skill help", "/skill nervis/pdf", "/skill personal/pdf", "/hostile"]);
  same("... each with its description", w.menu.children[3].children[1].textContent, "Writes a changelog from the git history.");
  same("... shown, and the chat box says so", [w.menu.hidden, w.input.getAttribute("aria-expanded")], [false, "true"]);
  const first = w.menu.children[0];
  same("... the first highlighted, as the listbox's active option",
       [w.input.getAttribute("aria-activedescendant"), first.id, first.getAttribute("role"), first.getAttribute("aria-selected")],
       ["slashOption0", "slashOption0", "option", "true"]);
  w.type("/c");
  same("filtered as you type", w.labels(), ["/clear", "/changelog-generator"]);
  w.type("/he");
  same("a skill named like a built-in shows as /skill name", w.labels(), ["/help", "/skill help"]);
  w.type("/pdf");
  same("two skills sharing a name show their full ids", w.labels(), ["/skill nervis/pdf", "/skill personal/pdf"]);
  for (const odd of ["/?", "/(", "/.*"]) {
    let threw = "";
    try { w.type(odd); } catch (failure) { threw = String(failure); }
    same(`${odd} matches nothing and closes, without breaking`, [threw, w.menu.hidden, w.labels().length], ["", true, 0]);
  }

  w.type("/c");
  let event = key("ArrowDown");
  w.input.onkeydown(event);
  same("ArrowDown moves the highlight, and the key stops there",
       [w.run("SLASH.index"), w.input.getAttribute("aria-activedescendant"), w.menu.children[1].getAttribute("aria-selected"), event.prevented],
       [1, "slashOption1", "true", true]);
  w.input.onkeydown(key("ArrowUp"));
  same("ArrowUp moves it back", w.run("SLASH.index"), 0);
  w.input.onkeydown(key("ArrowUp"));
  same("... and wraps around", w.run("SLASH.index"), 1);
  event = key("Enter");
  w.input.onkeydown(event);
  await settle();
  same("Enter completes the highlighted entry, with a trailing space", w.input.value, "/changelog-generator ");
  same("... closes the pop-up and sends nothing",
       [w.menu.hidden, w.input.getAttribute("aria-expanded"), w.input.getAttribute("aria-activedescendant"), event.prevented, w.sent.length],
       [true, "false", null, true, 0]);
  w.type("/cl");
  w.input.onkeydown(key("Tab"));
  same("Tab completes too", w.input.value, "/clear ");
  w.type("/ch write it", 3);
  w.input.onkeydown(key("Enter"));
  same("completing keeps what was typed after the first word", w.input.value, "/changelog-generator write it");
  w.type("/he");
  event = key("Escape");
  w.input.onkeydown(event);
  same("Escape closes the pop-up, and goes no further", [w.menu.hidden, event.prevented, event.stopped], [true, true, true]);
  w.input.onkeyup(key("ArrowLeft"));
  check("... it stays closed on the same word", w.menu.hidden);
  w.type("/hel");
  check("... and opens again when the word changes", !w.menu.hidden);
  w.type("/mo");
  event = key("mousedown");
  w.menu.children[0].onmousedown(event);
  check("pressing an entry keeps the focus in the chat box", event.prevented);
  w.menu.children[0].onclick();
  same("a click completes it", w.input.value, "/model ");

  w.type("/help me", 8);
  check("closed once the cursor leaves the first word", w.menu.hidden);
  w.type("/help me", 3);
  check("open again with the cursor back in it", !w.menu.hidden);
  w.type(" /help");
  check("closed when the box doesn't open with /", w.menu.hidden);
  w.type("what does /he");
  check("closed for a slash mid-sentence", w.menu.hidden);
  w.type("hello");
  event = key("ArrowDown");
  w.input.onkeydown(event);
  check("a closed pop-up takes no key", !event.prevented);

  w.type("/hos");
  same("a hostile description is drawn as text, exactly", w.menu.children[0].children[1].textContent, HOSTILE);
  check("nothing in the pop-up was ever written as markup", everyNode(w.menu).every((node) => node.htmlWrites.length === 0),
        JSON.stringify(everyNode(w.menu).flatMap((node) => node.htmlWrites)));

  const lists = () => w.requests.filter(isList).length;
  w.run("SLASH.askedAt=Date.now()");
  const typing = lists();
  w.type("/");
  w.type("/c");
  same("typing / reads the list at most once a minute", lists(), typing);
  w.run("SLASH.askedAt-=61000");
  w.type("/ch");
  w.type("/cha");
  same("... and again once a minute has passed", lists(), typing + 1);
}

async function refreshOnOpen() {
  const w = await world();
  const lists = () => w.requests.filter(isList).length;
  const real = w.page.context.document.getElementById;
  w.page.context.document.getElementById = (id) => (id === "chatInput" ? null : real(id));
  const opening = w.run("nervis()");
  w.page.context.document.getElementById = real;
  await opening;
  await settle();
  same("the list is read when the chat panel opens", lists(), 1);
  same("... and holds what NERVIS answered", w.run("SLASH.skills.map(s=>s.id)"), SKILLS.map((skill) => skill.id));
  await w.run("nervis()");
  await settle();
  same("... and not on a repaint", lists(), 1);

  const down = await world({ listed: null });
  const real2 = down.page.context.document.getElementById;
  down.page.context.document.getElementById = (id) => (id === "chatInput" ? null : real2(id));
  const drawing = down.run("nervis()");
  down.page.context.document.getElementById = real2;
  await drawing;
  await settle();
  same("NERVIS not answering leaves the list unread, not empty", down.run("SLASH.skills"), null);
  down.type("/");
  same("... and the pop-up still offers the built-ins", down.labels(), ["/help", "/clear", "/model"]);
  await down.send("/help");
  check("... and /help says the skills couldn't be read", down.last().text.includes("NERVIS couldn't read the switched-on skills from RAVIS just now."));
}

async function composing() {
  const w = await world();
  w.skills();
  await w.run("nervis()");
  await settle();
  w.input.value = "/help";
  w.input.selectionStart = 5;
  w.run("SLASH.close()");
  w.input.onkeydown(key("Enter", { isComposing: true }));
  await settle();
  same("Enter while an input method composes sends nothing", [w.run("CHAT_SESSION.messages.length"), w.input.value], [0, "/help"]);
  w.input.onkeydown(key("Enter", { keyCode: 229 }));
  await settle();
  same("... nor when the browser says so with keyCode 229", w.run("CHAT_SESSION.messages.length"), 0);
  w.input.oninput();
  const event = key("ArrowDown", { isComposing: true });
  w.input.onkeydown(event);
  same("a composing key isn't the pop-up's either", [event.prevented, w.run("SLASH.index")], [false, 0]);
  w.run("SLASH.close()");
  w.input.onkeydown(key("Enter"));
  await settle();
  same("Enter once composing is done sends as usual", w.last().kind, "local");
}

async function waiting() {
  const w = await world();
  w.skills();
  w.run(`CHAT_SESSION.profile='ravis/chat';
         CHAT_SESSION.messages=[{role:'user',text:'export this conversation',at:'x'},
                                {role:'assistant',text:'Here it is.',served:'fake-1b',offer:${JSON.stringify(OFFER)}}];
         MODELPICK.groups=async()=>[{name:'RAVIS pools',items:[{id:'ravis/coding'}]}]`);
  await w.send("/changelog-generator write it");
  same("a skill asked for while the latest reply's offer waits isn't sent", w.sent.length, 0);
  same("... it gets one line", w.last().text, "Answer the question first; the skill can wait.");
  check("... and the offer still waits, unanswered", !w.run("CHAT_SESSION.messages[1].offer.done"));
  await w.send("/help");
  check("/help still answers while an offer waits", w.last().text.startsWith("Commands, answered here and never sent to a model:"));
  await w.send("/model");
  check("/model on its own still answers", w.run("CHAT_SESSION.drawer") === "model" && w.last().text.includes("answered through ravis/chat"));
  await w.send("/clear");
  check("/clear asks for the offer to be answered first",
        w.last().text === "The reply above is waiting for your answer. Press one of its buttons or No thanks, then type /clear again. Nothing was cleared.",
        w.last().text);
  same("... and starts nothing", w.page.context.__starts.length, 0);
  await w.send("/model ravis/coding");
  check("/model with an id asks too, and changes nothing",
        w.last().text.startsWith("The reply above is waiting for your answer.") && w.run("CHAT_SESSION.profile") === "ravis/chat");
  await w.send("something else entirely");
  same("an ordinary message is still sent while an offer waits, as it always was", w.sent.length, 1);
  check("... and leaves the offer pressable", !w.run("CHAT_SESSION.messages[1].offer.done"));
  await w.send("/changelog-generator write it");
  same("once the latest reply has nothing waiting, the skill goes", w.sent.length, 2);
  w.run("CHAT_SESSION.messages.push({role:'assistant',text:'Two steps.',plan:{ready:true,steps:[{action:'Run',summary:'one',ready:true}]}})");
  await w.send("/changelog-generator write it");
  same("a plan waiting holds a skill back too", [w.sent.length, w.last().text], [2, "Answer the question first; the skill can wait."]);
  w.run("CHAT_SESSION.messages.findLast(m=>m.plan).plan.declined=true");
  await w.send("/changelog-generator write it");
  same("... until it is answered", w.sent.length, 3);

  const s = await world();
  s.skills();
  s.run(`CHAT_SESSION.profile='ravis/chat';
         CHAT_SESSION.messages=[{role:'user',text:'a long question',at:'x'},{role:'assistant',text:'',pending:true}];
         CHAT_SESSION.busy=true`);
  await s.send("/help");
  same("/help answers while a reply streams, above the reply still coming",
       s.run("CHAT_SESSION.messages.map(m=>m.kind==='local'?'local':m.pending?'pending':m.role)"),
       ["user", "local", "local", "pending"]);
  await s.send("/clear");
  same("/clear asks to finish or stop first",
       [s.last(2).text, s.page.context.__starts.length],
       ["A reply is still coming. Let it finish, or stop it with ■, then type /clear again. Nothing was cleared.", 0]);
  const lines = s.run("CHAT_SESSION.messages.length");
  await s.send("/changelog-generator write it");
  same("a skill typed while a reply streams stays in the box, unsent, like any message",
       [s.sent.length, s.input.value, s.run("CHAT_SESSION.messages.length")], [0, "/changelog-generator write it", lines]);
  await s.send("/model");
  same("/model on its own still answers", s.run("CHAT_SESSION.drawer"), "model");
  await s.send("/model ravis/coding");
  check("/model with an id asks to finish or stop first",
        s.last(2).text.startsWith("A reply is still coming.") && s.run("CHAT_SESSION.profile") === "ravis/chat");
  same("the reply's placeholder is still last, where the stream writes", s.last().pending, true);
}

/* Each part on its own: one that throws is a failure named after it, and the parts after it still
   run, so one broken behaviour can't hide what the rest would have said. */
async function main() {
  for (const part of [parsing, localLines, clearAndModel, popUp, refreshOnOpen, composing, waiting]) {
    try {
      await part();
    } catch (failure) {
      const where = failure && failure.stack ? failure.stack.split("\n").slice(0, 2).join(" ") : String(failure);
      failures.push(`${part.name} threw: ${where}`);
    }
  }
}

main().then(() => {
  finished = true;
  if (failures.length) {
    console.error(`slash check: ${failures.length} of ${checked} checks failed\n  - ${failures.join("\n  - ")}`);
    process.exit(1);
  }
  console.log(`slash check: ${checked} checks passed`);
  process.exit(0);
}, (failure) => {
  finished = true;
  console.error(`slash check threw: ${failure && failure.stack ? failure.stack : failure}`);
  process.exit(1);
});
