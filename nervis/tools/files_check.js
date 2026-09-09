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

/* The two controls live above the listing rather than in it, so they are read
   from their own sink. */
async function actionsOf(payload) {
  const page = pageWith(payload);
  const { exported, elements } = page;
  exported.state.app = "nervis";
  exported.state.view = "Files";
  await exported.filesView();
  if (exported.stopPolling) exported.stopPolling();
  const actions = elements.get("sel:#pageActions");
  return String((actions && actions.innerHTML) || "");
}

async function main() {
  const failures = [];

  const top = await drawn(listing("", ROOMS.map((name) => ({
    name, path: name, kind: "folder", bytes: 0, modified: 1e9, readable: false,
  }))));

  /* **The workspace is one rail entry that folds**, the same shape a share is,
     and it starts open — its rooms are what somebody opening this tab came for,
     and a twisty in front of them is ceremony on every visit. */
  if (!/class="room[^"]*" data-place="workspace" data-path=""/.test(top)) {
    failures.push("the rail lost its workspace entry, so there is nothing to "
      + "click to get back to the top of it.");
  }
  const subs = [...top.matchAll(/class="room sub[^"]*" data-place="workspace" data-path="([^"]*)"/g)]
    .map((m) => m[1]);
  /* **The rail's order is not the listing's.** A listing is about what a room
     is for — arriving, kept, produced — and reads in that order. The rail is
     about reaching one, so the two a person reaches for sit on top and the
     rest is alphabetical, which is where somebody looks for a folder they
     made themselves. */
  if (subs.join(",") !== "clarvis,library,export,import") {
    failures.push("the rail folded the rooms out in the wrong order: "
      + `${subs.join(", ") || "none of them"}`);
  }
  if (!/data-room="\.trash"/.test(top)) {
    failures.push("the trash left the rail, so a deleted file has nowhere to be found.");
  }
  /* Every place folds, because a drag to a particular folder on a share should
     be one gesture rather than a drop, a navigation and a second drop. */
  if (!/class="twist open"/.test(top)) {
    failures.push("rail entries offered no way to fold open, so reaching a "
      + "subfolder means navigating away from what you are dragging.");
  }

  /* **What a drag means, which is the rule and not the gesture.** Between a
     laptop and a NAS the ordinary intention is "have this in both", so a drag
     across places copies; within one place it moves, because a file dragged
     between rooms was being filed rather than duplicated. Shift swaps either.
     Checked here because the alternative — a drag that quietly emptied the
     room it came from — is a data-loss surprise nothing else would catch. */
  const { exported: rules } = pageWith(listing("", []));
  const verb = (from, to, shift) => rules.dragVerb(from, to, shift);
  const expected = [
    ["workspace", "nas", false, "copy"],
    ["workspace", "nas", true, "move"],
    ["workspace", "workspace", false, "move"],
    ["workspace", "workspace", true, "copy"],
  ];
  for (const [from, to, shift, want] of expected) {
    const got = verb(from, to, shift);
    if (got !== want) {
      failures.push(`a drag from ${from} to ${to}${shift ? " with Shift" : ""} `
        + `would ${got}, and should ${want}.`);
    }
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

  /* **The header sorts as well as resizes.** Both live in the same cell, so the
     two gestures have to stay apart — and a listing whose sort put the folders
     in among the files by size is a listing nobody has ever wanted. */
  if (!/data-sort="name"/.test(inside) || !/data-sort="size"/.test(inside)
      || !/data-sort="modified"/.test(inside)) {
    failures.push("a column header offered no way to sort by it, so a room of "
      + "two hundred files can only be read in the order it was written.");
  }
  const sorted = (by, dir) => rules.sortEntries(FILES, { by, dir })
    .map((item) => item.name);
  if (sorted("size", 1)[0] !== "papers" || sorted("size", -1)[0] !== "papers") {
    failures.push("sorting by size put a file above a folder, which reads as "
      + "the folders having gone missing into the middle of the list.");
  }
  const bySize = sorted("size", 1).slice(1);
  if (bySize[0] !== "art.svg" || bySize[bySize.length - 1] !== "clip.mp4") {
    failures.push(`sorting by size gave ${bySize.join(", ")}, which is not by size.`);
  }
  if (sorted("name", -1).slice(1).join(",") !== "paper.pdf,page.html,clip.mp4,art.svg") {
    failures.push("a second click on a column did not turn the order around.");
  }
  if (rules.sortEntries(FILES, { by: "", dir: 1 }) !== FILES) {
    failures.push("an unsorted listing was reordered anyway, losing the API's "
      + "own order — rooms by what they are for, files newest first.");
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

  /* **The right-click menu runs the same verbs as the row**, and offers the one
     a row cannot: send this somewhere. What it must never grow is a second
     delete with its own confirmation, or a download that copies a file into
     another folder on the same disk. */
  const row = (kind, name) => ({ dataset: { kind, name, path: `library/${name}` } });
  const forFile = rules.menuForEntry(
    row("file", "paper.pdf"), ROOMS, ["workspace", "nas"], "library");
  const forFolder = rules.menuForEntry(
    row("folder", "papers"), ROOMS, ["workspace", "nas"], "library");
  const forHere = rules.menuForHere();

  if (!/data-act="view"/.test(forFile) || !/data-act="rename"/.test(forFile)
      || !/data-act="delete"/.test(forFile)) {
    failures.push("the menu on a file lost one of the verbs the row offers.");
  }
  if (/data-act="download"/.test(forFile) || />Download</.test(forFile)) {
    failures.push("the menu grew a download, which the row deliberately does not have.");
  }
  if (!/data-act="open"/.test(forFolder)) {
    failures.push("the menu on a folder offered no way to open it.");
  }
  /* Copy-to lists every room and place except the one the file is already in:
     a menu offering to copy a file onto itself has a wrong answer in it. */
  const sends = [...forFile.matchAll(/data-act="send" data-place="([^"]*)" data-path="([^"]*)"/g)]
    .map((m) => m[2] || m[1]);
  if (!sends.includes("nas")) {
    failures.push("the menu offered no way to send a file to a configured share, "
      + "which is the one thing a row cannot do without becoming a form.");
  }
  if (sends.includes("library")) {
    failures.push("the menu offered to copy a file into the room it is already in.");
  }
  /* **Filing is a plural act**, so the verbs act on the selection when the row
     they were aimed at belongs to it — and on that row alone when it does not.
     The second half is the one that matters: a Delete on an unselected row
     that quietly took ten other files with it is the worst bug this screen
     could have. */
  const picked = new Set(["library/a.pdf", "library/b.pdf"]);
  if (rules.actingOn("library/a.pdf", picked).length !== 2) {
    failures.push("a verb aimed at a selected row acted on that row alone, so "
      + "selecting ten files and deleting them means doing it ten times.");
  }
  const outside = rules.actingOn("library/c.pdf", picked);
  if (outside.length !== 1 || outside[0] !== "library/c.pdf") {
    failures.push("a verb aimed at an unselected row acted on the selection "
      + "instead: Delete would take files the pointer was never on.");
  }
  /* Shift-click reads in screen order, whichever end it started from. */
  const span = rules.pickSpan(["a", "b", "c", "d"], "d", "b");
  if (span.join(",") !== "b,c,d") {
    failures.push(`a shift-click upwards selected ${span.join(",")}, not the span.`);
  }
  /* With several rows chosen the menu is about all of them — and Rename and
     Open are not verbs a set of files has. */
  const forMany = rules.menuForEntry(
    row("file", "paper.pdf"), ROOMS, ["workspace", "nas"], "library", 3);
  if (!/Delete 3 items/.test(forMany)) {
    failures.push("the menu said Delete over three selected files, which names "
      + "one of them and takes all three.");
  }
  if (/data-act="rename"/.test(forMany) || /data-act="view"/.test(forMany)) {
    failures.push("the menu offered Rename or Open for a set of files, and "
      + "both are verbs exactly one file has.");
  }

  if (!/data-act="refresh"/.test(forHere)) {
    failures.push("right-clicking the empty space offered no way to re-read the "
      + "directory, which is what a person does when something changed outside NERVIS.");
  }

  /* An unconfigured workspace is a state to render, not an empty table. */
  /* The header keeps both controls wherever it is: the workspace's own top
     level used to swap them for a sentence saying files go inside a room, and
     a paragraph standing where two buttons had been is not a tidier screen. */
  const atTop = await actionsOf(listing("", ROOMS.map((name) => ({
    name, path: name, kind: "folder", bytes: 0, modified: 1e9, readable: false,
  }))));
  if (!/id="newFolder"/.test(atTop) || !/id="addFiles"/.test(atTop)) {
    failures.push("the top of the workspace offered no way to make a folder or "
      + "add a file — the two things this tab exists for.");
  }

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
