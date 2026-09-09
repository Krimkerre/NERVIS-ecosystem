/* What the Files tab offers, and the two verbs it deliberately does not.
 *
 * The screen is a directory listing, so most of it is obvious and would pass
 * whatever it drew. Three things are not obvious and are the reason this file
 * exists:
 *
 *   - **No download button.** The workspace is a directory on this machine.
 *     Downloading copies a file that is already on disk into another folder on
 *     the same disk, which is a verb that looks helpful and does nothing. It
 *     was there and was removed; nothing else would notice it coming back.
 *   - **Open is offered only for what the browser will render.** The inline
 *     list in `files.py` excludes HTML and SVG because anything served inline
 *     runs in NERVIS's own origin, and a screen offering "Open" on an `.html`
 *     would be inviting exactly the request that list refuses.
 *   - **The rooms come in the order they mean something.** Alphabetical would
 *     lead with `clarvis`, the room a person visits least.
 *
 *   node tools/files_check.js
 */

const { loadPage } = require("./page_context.js");

const ROOMS = ["import", "library", "export", "clarvis"];

/* One listing, as `/api/v1/workspace/entries` would answer it. */
const listing = (path, items) => ({ items, path, workspace: "/w", rooms: ROOMS, detail: "" });

const FILES = [
  { name: "paper.pdf", path: "library/paper.pdf", kind: "file", bytes: 2048, modified: 1e9, readable: true },
  { name: "page.html", path: "library/page.html", kind: "file", bytes: 512, modified: 1e9, readable: true },
  { name: "art.svg", path: "library/art.svg", kind: "file", bytes: 256, modified: 1e9, readable: false },
  { name: "clip.mp4", path: "library/clip.mp4", kind: "file", bytes: 9e6, modified: 1e9, readable: false },
  { name: "papers", path: "library/papers", kind: "folder", bytes: 0, modified: 1e9, readable: false },
];

function pageWith(payload) {
  return loadPage({
    fetchImpl: async (url) => {
      const target = String(url);
      let body = { items: [] };
      if (target.includes("/api/v1/workspace/entries")) body = payload;
      else if (target.includes("/api/v1/workspace/trash")) body = { items: [] };
      return { ok: true, status: 200, json: async () => body,
               text: async () => JSON.stringify(body),
               headers: { get: () => "application/json" }, body: null };
    },
  });
}

async function drawn(payload) {
  const page = pageWith(payload);
  const { exported, elements } = page;
  exported.state.app = "nervis";
  exported.state.view = "Files";
  await exported.filesView();
  if (exported.stopPolling) exported.stopPolling();
  const content = elements.get("sel:#content");
  return String((content && content.innerHTML) || "");
}

async function main() {
  const failures = [];

  const top = await drawn(listing("", ROOMS.map((name) => ({
    name, path: name, kind: "folder", bytes: 0, modified: 1e9, readable: false,
  }))));

  /* The rail, in meaning order. Read as the order the room buttons appear in,
     because that is the order somebody's eye goes down. */
  const rail = [...top.matchAll(/data-room="([^"]+)"/g)].map((m) => m[1]);
  if (rail.join(",") !== "import,library,export,clarvis,.trash") {
    failures.push(`the rooms were not in the order they mean something: ${rail.join(", ")}`);
  }

  const inside = await drawn(listing("library", FILES));

  if (/data-act="download"/.test(inside) || />Download</.test(inside)) {
    failures.push("a download button came back. The workspace is on this machine: "
      + "downloading copies a file that is already on disk into another folder "
      + "on the same disk.");
  }

  /* Open, for what the browser will actually render — and for nothing else. */
  const opens = (name) => {
    const row = inside.split('data-name="').find((part) => part.startsWith(`${name}"`)) || "";
    return /data-act="view"/.test(row.split("</div>")[0]);
  };
  if (!opens("paper.pdf")) {
    failures.push("a PDF offered no way to open it, which is the one thing this "
      + "tab exists to save a trip to another application for.");
  }
  for (const runnable of ["page.html", "art.svg"]) {
    if (opens(runnable)) {
      failures.push(`${runnable} offered Open. Anything served inline runs in `
        + "NERVIS's own origin, and both of these can carry script — `files.py` "
        + "refuses them and the screen must not invite the request.");
    }
  }
  if (opens("clip.mp4")) {
    failures.push("a video offered Open, which the inline list does not serve: "
      + "the tab would open a tab that downloads.");
  }

  /* The header carries the handles that make the columns resizable, and says
     which column is which — a row of numbers with no header is a row of
     numbers. */
  if (!/class="entry-head"/.test(inside)) {
    failures.push("the listing drew no header row, so the columns have no names "
      + "and no resize handles.");
  }
  if ([...inside.matchAll(/class="grip"/g)].length < 2) {
    failures.push("fewer than two resize handles: the widths that suit one room "
      + "are not the ones that suit another.");
  }

  /* An unconfigured workspace is a state to render, not an empty table. */
  const off = await drawn({ items: [], path: "", detail: "Set NERVIS_WORKSPACE_PATH to …" });
  if (!/NERVIS_WORKSPACE_PATH/.test(off)) {
    failures.push("an unconfigured workspace drew no explanation, so an install "
      + "that was never asked to read files looks broken instead of off.");
  }
  if (/class="entry-head"/.test(off)) {
    failures.push("an unconfigured workspace still drew a file table.");
  }

  if (failures.length) {
    for (const failure of failures) console.error("  • " + failure);
    console.error(`${failures.length} file-manager failure(s)`);
    process.exit(1);
  }
  console.log("the files tab opens what the browser can render, and offers no download");
}

main().catch((failure) => {
  console.error(failure);
  process.exit(1);
});
