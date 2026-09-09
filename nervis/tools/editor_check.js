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
function pageIn(state, session = { open: true, workspace: "/w", proxied: true },
                extension = { configured: false }) {
  return loadPage({
    fetchImpl: async (url) => {
      const target = String(url);
      let payload = { items: [] };
      if (target.includes("/api/v1/services")) payload = services(state);
      else if (target.includes("/api/v1/code/extension")) payload = { extension };
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
  // `#content` alone, and deliberately: the page loads its default screen
  // before a checker can switch views, so reading every sink would pick up an
  // iframe another screen drew and report it as this one's.
  const content = elements.get("sel:#content");
  return String((content && content.innerHTML) || "");
}

async function main() {
const failures = [];

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
/* The session names a workspace; the frame has to ask for that one. code-server
   otherwise opens whatever it had open last, and the tab would say "workspace X"
   above an editor showing Y. */
if (!/src="\/code\/\?folder=/.test(alive)) {
  failures.push("the frame did not ask for the workspace the session authorised, "
    + "so the editor opens its own last folder and the two disagree.");
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
