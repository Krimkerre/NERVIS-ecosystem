/* Whether the Code tab embeds an editor that is not there.
 *
 * §10's code-server condition asks what happens when the browser VS Code that
 * NERVIS frames stops answering *after having answered*. The failure that
 * matters is specific and visual: an `<iframe>` pointed at a dead port renders
 * the browser's own error page inside the dashboard, which reads as NERVIS
 * being broken rather than as code-server being absent — and the reader's next
 * move is to restart the wrong thing.
 *
 * The page has the right rule already: it draws the frame only for a *usable*
 * registry state. Nothing exercised it. The registry deliberately keeps the
 * last derived `codeserver.workbench` capability across the loss — it is what
 * NERVIS last established — so the capability gate alone would still say
 * "available" for a process that is gone, and the state check is the whole of
 * what stands between that and a broken frame.
 *
 *   node tools/editor_check.js
 */

const vm = require("node:vm");
const { loadPage } = require("./page_context.js");

/* One `/api/v1/services` answer holding a code-server row in a given state,
   with the capability it had when it was last reachable. That pairing is the
   point: a row whose capability was cleared would pass this check for the
   wrong reason. */
const services = (state) => ({
  items: [{
    key: "codeserver",
    label: "code-server",
    state,
    endpoint: "http://127.0.0.1:8080",
    capabilities: { "codeserver.workbench": "available" },
    capability_reasons: { "codeserver.workbench": "graded PASS at 4.135.0" },
    capability_source: "adapted",
    detail: state === "healthy" ? "serving the workbench" : "no response: ConnectError",
  }],
});

/* The proxy session the tab now opens before it frames anything (§13.3). The
   default is one already open, because every check below is about the *editor*
   state and a closed session would make them all pass for the wrong reason —
   the frame is not drawn without one. `session: null` is the other case, and
   has its own check further down. */
/* Chosen to need encoding: a space and a `#`. A frame that interpolates the
   workspace raw truncates its own query at the hash, and the folder silently
   goes missing. */
const WORKSPACE = "/w space#1";

/* Every handoff the page sent and every outcome it recorded, so a check can say
   what reached NERVIS rather than what the screen implies. */
const RUNS = [];
const OUTCOMES = [];
/* A task the fixture treats as too vague to name — NERVIS's answer when neither
   the model nor the person gave the folder a name. */
const VAGUE = "fix it";

function pageIn(state, session = { open: true, workspace: WORKSPACE, proxied: true },
                extension = { configured: false }) {
  return loadPage({
    fetchImpl: async (url, init) => {
      const target = String(url);
      let payload = { items: [] };
      if (target.includes("/api/v1/services")) payload = services(state);
      else if (target.includes("/api/v1/code/extension")) payload = { extension };
      else if (target.includes("/api/v1/commands/run")) {
        const sent = JSON.parse((init && init.body) || "{}");
        RUNS.push(sent);
        payload = sent.target === VAGUE && !sent.name
          ? { needs_name: "What should this task's folder be called?" }
          : { file: { name: "clarvis-task.md", folder: "nervis-tasks/pomodoro-timer",
                      workspace: WORKSPACE, detail: "waiting" } };
      }
      else if (target.includes("/api/v1/proposals/outcome")) OUTCOMES.push(target);
      else if (target.includes("/api/v1/code/session")) {
        payload = {
          session: session || { open: false, proxied: true, reason: "closed", roots: [] },
        };
      }
      return { ok: true, status: 200, json: async () => payload,
               text: async () => JSON.stringify(payload),
               headers: { get: () => "application/json" }, body: null };
    },
  });
}

async function drawn(state, session, extension) {
  const page = pageIn(state, session, extension);
  const { exported, elements } = page;
  exported.state.app = "clarvis";
  exported.state.view = "Workspace";
  await exported.clarvis();
  if (exported.stopPolling) exported.stopPolling();
  // **Two sinks, and the split is the point.** The frame lives in `#editorHold`
  // — a holder nothing re-parents — because moving an iframe in the DOM reloads
  // it, and `#content` is rewritten on every navigation: an editor drawn there
  // started a VS Code workbench on every trip away from the tab. Everything
  // this file asserts about the frame is therefore read from the holder, and
  // everything about a *refusal* to draw one is still read from `#content`.
  const hold = elements.get("sel:#editorHold");
  const content = elements.get("sel:#content");
  return String((hold && hold.innerHTML) || "")
    + String((content && content.innerHTML) || "");
}

/* Where the frame is, rather than only what it points at: an iframe inside
   `#content` is an iframe reloaded on every navigation, which is a VS Code
   workbench started every time somebody looks at another tab and comes back. */
async function framedIn(state, session, extension) {
  const page = pageIn(state, session, extension);
  const { exported, elements } = page;
  exported.state.app = "clarvis";
  exported.state.view = "Workspace";
  await exported.clarvis();
  if (exported.stopPolling) exported.stopPolling();
  const read = (selector) => {
    const node = elements.get(`sel:${selector}`);
    return String((node && node.innerHTML) || "");
  };
  return { hold: read("#editorHold"), content: read("#content") };
}

/* Pressing Hand over on a chat offer, through the page's own `runOffer`, and
   then drawing whatever tab that left the page on. */
async function handedOver() {
  const page = pageIn("healthy", { open: false, proxied: false, roots: [] });
  const { context, exported, elements } = page;
  vm.runInContext("CHAT_SESSION.messages=[{role:'assistant',text:'ok',offer:"
    + "{operation:'nervis.clarvis.task',service:'nervis',target:'make me a pomodoro timer',ready:true}}]",
    context);
  await vm.runInContext("runOffer(0)", context);
  const done = vm.runInContext("CHAT_SESSION.messages[0].offer.done", context);
  const landed = `${exported.state.app}/${exported.state.view}`;
  await exported.clarvis();
  if (exported.stopPolling) exported.stopPolling();
  const hold = elements.get("sel:#editorHold");
  return { done: String(done), landed, html: String((hold && hold.innerHTML) || "") };
}

/* Pressing Hand over on a task too vague to name, then naming it in the card. */
async function askedForAName() {
  const page = pageIn("healthy", { open: false, proxied: false, roots: [] });
  const { context, exported } = page;
  RUNS.length = 0;
  OUTCOMES.length = 0;
  vm.runInContext("CHAT_SESSION.messages=[{role:'assistant',text:'ok',offer:"
    + `{operation:'nervis.clarvis.task',service:'nervis',target:'${VAGUE}',`
    + "action:'Hand over',proposal_id:'pr_1',ready:true}}]", context);
  await vm.runInContext("runOffer(0)", context);
  const firstStop = String(exported.state.app);
  const card = String(vm.runInContext("offerRow(CHAT_SESSION.messages[0].offer, 0)", context));
  vm.runInContext("$('#offerName0').value='Pomodoro Timer'", context);
  await vm.runInContext("handOverNamed(0)", context);
  return { firstStop, card, landed: `${exported.state.app}/${exported.state.view}`,
           runs: RUNS.slice(), outcomes: OUTCOMES.length };
}

async function main() {
const failures = [];

/* **A Hand over opens its task's own folder, on the editor's own address.**
   Each task is written into a new folder, and Clarvis reads the task from
   whichever folder its window has open — so the press has to reach the frame
   by value, or Clarvis opens on the previous task's folder and offers nothing.
   The address stays code-server's own, because its saved keys belong to that
   origin. The fixture folder carries a space and a hash, so a frame that
   forgets to encode it fails here too. */
const handed = await handedOver();
if (/SIRVIS|nothing was/.test(handed.done)) {
  failures.push(`pressing Hand over reported ${JSON.stringify(handed.done)} for a task `
    + "NERVIS had written — the offer ran through the SIRVIS job wording.");
}
if (handed.landed !== "clarvis/Workspace") {
  failures.push(`a Hand over left the page on ${handed.landed}, not the Code tab.`);
}
const handedSrc = /src="(http[^"]*)"/.exec(handed.html);
const handedFolder = handedSrc && new URL(handedSrc[1]).searchParams.get("folder");
if (!handedSrc || !handedSrc[1].startsWith("http://127.0.0.1:8080")
    || handedFolder !== WORKSPACE) {
  failures.push("after a Hand over the editor was framed at "
    + `${handedSrc ? JSON.stringify(handedSrc[1]) : "no address"}, not code-server's own `
    + `address asking for ${JSON.stringify(WORKSPACE)} — so Clarvis opens without the task.`);
}

/* **A task nobody could name is asked about, not opened.** The folder's name is
   what the editor shows and what the task is found by later, so when NERVIS has
   none it asks — and the card has to put that question with somewhere to
   answer it, send the answer, and count the whole exchange as one yes. */
const named = await askedForAName();
if (named.firstStop === "clarvis" || !named.runs.length || named.runs[0].name) {
  failures.push("a task NERVIS could not name was opened, or sent with a name nobody "
    + "gave — the first press has to stop and ask.");
}
if (!/id="offerName0"/.test(named.card) || !/folder be called/.test(named.card)) {
  failures.push("NERVIS asked for a folder name and the card showed no question, or no "
    + "field to answer it in.");
}
const sentName = named.runs.length > 1 ? named.runs[named.runs.length - 1].name : undefined;
if (sentName !== "Pomodoro Timer" || named.landed !== "clarvis/Workspace") {
  failures.push(`after naming the folder the page sent ${JSON.stringify(sentName)} and `
    + `landed on ${named.landed} — the typed name has to reach NERVIS and the task open.`);
}
if (named.outcomes !== 1) {
  failures.push(`one decision was recorded ${named.outcomes} times: pressing Hand over `
    + "and then naming the folder is one yes.");
}

const alive = await drawn("healthy");
if (!/<iframe/.test(alive)) {
  failures.push("a reachable code-server was not embedded at all, so the checks "
    + "below prove nothing: they would pass against a tab that never draws a frame.");
}

/* **The frame is served by NERVIS, not by code-server's own port.** Before
   §13.3's proxy the `src` was the peer's endpoint, which put the editor on a
   second origin NERVIS neither authenticates nor sets headers for. A revert to
   that is invisible on screen — the editor still loads — so it is checked
   here rather than left to be noticed. */
if (!/src="\/code\/(\?[^"]*)?"/.test(alive)) {
  failures.push("the editor was framed from somewhere other than NERVIS's own "
    + "/code/ path, which is the proxy that makes it same-origin. §13.3's auth, "
    + "header and redirect rules apply to nothing if the frame bypasses them.");
}
/* **The frame asks for the workspace the session authorised — that one, by
   value.** This read `src="/code/?folder=` and stopped, which proves a folder
   was asked for and nothing about *which*. An external audit replaced the
   page's folder expression with `/audit-wrong-workspace` and watched this gate
   pass, which is the whole failure it exists to prevent: code-server otherwise
   opens whatever it had open last, and the tab says "workspace X" above an
   editor showing Y.

   The fixture's workspace carries a space and a hash so the comparison also
   fails on a frame that forgets to encode one — a raw `#` would truncate the
   query at the browser, which looks like the folder simply being ignored. */
const framed = /src="(\/code\/[^"]*)"/.exec(alive);
const asked = framed && new URL(framed[1], "http://nervis.invalid")
  .searchParams.get("folder");
if (asked !== WORKSPACE) {
  failures.push("the frame asked for "
    + `${asked === null ? "no workspace at all" : JSON.stringify(asked)}, not the `
    + `${JSON.stringify(WORKSPACE)} the session authorised — so the editor opens `
    + "its own last folder while the tab names another.");
}
if (/src="http/.test(alive)) {
  failures.push("the frame's src is an absolute address, so the editor is on a "
    + "second origin again.");
}

/* **Not proxying frames the editor's own address, and that is not a bug.**
   VS Code's web state lives in the browser's IndexedDB, scoped to an origin, so
   moving an existing editor behind the proxy hides its keys and history rather
   than migrating them. The default therefore keeps the old address, and the tab
   still has to draw an editor there. */
const direct = await drawn("healthy", { open: false, proxied: false, roots: [] });
if (!/src="http:\/\/127\.0\.0\.1:8080"/.test(direct)) {
  failures.push("with the proxy off the editor was not framed at its own address, "
    + "so an editor whose browser-side state belongs to that origin shows none of it.");
}
if (/No workspace is configured/.test(direct)) {
  failures.push("the unproxied tab demanded a proxy session, which it does not use.");
}

/* A reachable editor and no session is not a frame. The session is what says
   who opened the editor and which workspace they opened, and drawing without
   one would make §13.3's "explicit workspace selection" decorative. */
const unopened = await drawn("healthy", { open: false, proxied: true, roots: [] });
if (/<iframe/.test(unopened)) {
  failures.push("a code-server was framed with no proxy session open, so the "
    + "editor was reachable without anything having authorised it.");
}
if (!/workspace is configured|session was refused|Choose a workspace/.test(unopened)) {
  failures.push("a tab that could not open a session said nothing about why, "
    + "which leaves the reader with an empty pane and no next move.");
}

/* **On first open the Bridge has not had time to be absent.** The editor is
   framed by the same paint; its extension host starts afterwards, Clarvis
   activates inside it, and a window registers seconds later. Declaring "no
   Bridge has registered" at paint time states as a finding something that has
   not happened yet — which is why a manual refresh always cleared it. */
const fresh = await drawn("healthy", { open: true, workspace: "/w", proxied: true });
if (!/Waiting for the Clarvis Bridge/.test(fresh)) {
  failures.push("the first paint with no window registered did not say it was "
    + "waiting, so the tab reports an absence it has not established yet.");
}
if (/No Clarvis Bridge has registered/.test(fresh)) {
  failures.push("the first paint announced the Bridge missing before the editor "
    + "it is inside had finished loading.");
}

/* Notes belong under the editor, not above it: above, they sit between NERVIS's
   tab bar and code-server's title bar and push the editor down; below, they read
   as a line under the editor's own status bar. */
const frameAt = fresh.indexOf("<iframe");
const notesAt = fresh.indexOf('id="editorNotes"');
if (frameAt === -1 || notesAt === -1 || notesAt < frameAt) {
  failures.push("the editor's notes were not rendered after the frame, so they "
    + "sit above the editor rather than under its status bar.");
}
/* And the words have to match the place. A note under the editor that says
   "the editor below" is a small wrongness the reader has to correct for every
   time they read it — and it was there for exactly as long as it took somebody
   to look. */
if (/editor below/i.test(fresh)) {
  failures.push("a note rendered under the editor called it 'the editor below'.");
}

/* §13.5: an editor running without Clarvis in it is a state this tab can see
   and now fix, and the fix has to be offered where the missing panel would
   have been rather than inside the frame that cannot render it. */
const stale = await drawn("healthy", { open: true, workspace: "/w", proxied: true },
  { configured: true, identifier: "krimkerre.clarvis", offered: "0.13.0",
    installed: "0.12.8", due: "the editor has krimkerre.clarvis 0.12.8" });
if (!/id="installClarvis"/.test(stale)) {
  failures.push("an editor holding an older Clarvis than the configured package "
    + "offered no way to update it, so the tab can see the problem and not fix it.");
}
if (!/<iframe/.test(stale)) {
  failures.push("an out-of-date extension hid the editor. The editor works; it is "
    + "the panel inside it that is missing.");
}

/* And the opposite, which is the ordinary case: current, so nothing is said.
   A tab that announced good news every visit would train the reader past the
   place the real message appears. */
const current = await drawn("healthy", { open: true, workspace: "/w", proxied: true },
  { configured: true, identifier: "krimkerre.clarvis", offered: "0.13.0",
    installed: "0.13.0", due: "" });
if (/id="installClarvis"/.test(current)) {
  failures.push("a current Clarvis still drew an install button.");
}

for (const gone of ["unreachable", "stale", "stopped"]) {
  const html = await drawn(gone);
  if (/<iframe/.test(html)) {
    failures.push(`a code-server reported '${gone}' was still framed. An iframe on a `
      + "dead port renders the browser's error page inside the dashboard, which reads "
      + "as NERVIS being broken.");
  }
  if (!/No editor to embed/.test(html)) {
    failures.push(`a code-server reported '${gone}' drew no explanation. Saying nothing `
      + "leaves the reader with an empty tab and no idea which process to start.");
  }
}

/* The editor survives a navigation because nothing rewrites what holds it. */
const placed = await framedIn("healthy", { open: true, workspace: "/w", proxied: true });
if (!/id="clarvis-frame"/.test(placed.hold)) {
  failures.push("the editor was not framed in the holder that survives a "
    + "navigation, so switching tabs and back restarts the workbench.");
}
if (/id="clarvis-frame"/.test(placed.content)) {
  failures.push("the editor was framed inside the region every navigation "
    + "rewrites — an iframe moved or re-created in the DOM reloads, which is a "
    + "VS Code start on every visit.");
}

if (failures.length) {
  for (const failure of failures) console.error("  • " + failure);
  console.error(`${failures.length} editor-embedding failure(s)`);
  process.exit(1);
}
console.log("the editor tab frames a reachable code-server and explains an absent one");
}

main().catch((failure) => {
  console.error(failure);
  process.exit(1);
});
