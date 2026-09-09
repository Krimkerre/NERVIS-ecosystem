/* Every screen has an address, the address survives a round trip, and a bad one
 * does not blank the page.
 *
 * `NERVIS.md` §25.2 asks for deep links and a back button. Those are easy to
 * add and easy to add *almost*: a router that writes the address bar but cannot
 * read it back, or that covers thirty-three screens because two view names
 * contain a character nobody encoded, looks completely correct from the
 * outside. "Run & debug" is the one that catches this — an unencoded `&` ends
 * the fragment and the address silently names a different screen.
 *
 * So this is a round trip rather than a spot check: for all 35 screens, build
 * the address, parse it back, and require the same screen out.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const { context, exported } = loadPage();
const run = (expression) => vm.runInContext(expression, context);

const failures = [];
let checked = 0;

/* 1 · Every screen round-trips through its own address. */
for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
  for (const view of config.nav) {
    checked += 1;
    const hash = run(`hashFor(${JSON.stringify(app)},${JSON.stringify(view)})`);
    context.location.hash = hash;
    const back = run("routeFromHash()");
    if (!back || back.app !== app || back.view !== view) {
      failures.push(
        `${app}/${view}: address ${hash} parsed back as ` +
        `${back ? `${back.app}/${back.view}` : "nothing"}`);
      continue;
    }
    const resolved = run("resolveRoute(routeFromHash())");
    if (resolved.app !== app || resolved.view !== view || resolved.note) {
      failures.push(
        `${app}/${view}: resolved to ${resolved.app}/${resolved.view}` +
        `${resolved.note ? ` with note "${resolved.note}"` : ""}`);
    }
  }
}

/* 2 · A route nobody can serve resolves to one that exists, and says so.
 *
 * Not merely "does not throw". `applyTheme()` reads `APP_CONFIG[state.app]`
 * unguarded and `render()` calls it first, so an unknown app reaching `state`
 * blanks the whole page — and the note is what stops the correction being
 * silent, which is the difference between a fixed link and a page lying about
 * which screen it is showing. */
const BAD = [
  { app: "nervis", view: "Nonexistent" },
  { app: "notanapp", view: "Overview" },
  { app: "", view: "" },
];
for (const bad of BAD) {
  const resolved = run(`resolveRoute(${JSON.stringify(bad)})`);
  if (!exported.APP_CONFIG[resolved.app]) {
    failures.push(`${bad.app}/${bad.view}: resolved to unknown app ${resolved.app}`);
  } else if (!exported.APP_CONFIG[resolved.app].nav.includes(resolved.view)) {
    failures.push(`${bad.app}/${bad.view}: resolved to unknown view ${resolved.view}`);
  } else if (!resolved.note) {
    failures.push(`${bad.app}/${bad.view}: corrected the address without saying so`);
  }
}

/* 3 · Nothing, which is the ordinary first visit, is not an error. */
context.location.hash = "";
if (run("routeFromHash()") !== null) failures.push("an empty fragment parsed as a route");
const fresh = run("resolveRoute(null)");
if (!exported.APP_CONFIG[fresh.app] || fresh.note) {
  failures.push("an empty fragment did not resolve cleanly to a real screen");
}

/* 4 · The token keeps its own fragment, and the route keeps its.
 *
 * These two share one fragment and the token got there first. The scrub used to
 * take the whole thing, which would delete a route on every launcher start —
 * and it fires after the first render, so the symptom would have been an
 * address bar that forgets the screen rather than anything resembling a scrub. */
const withBoth = `#/ravis/${encodeURIComponent("Routes")}&token=secret`;
context.location.hash = withBoth;
const carried = run("routeFromHash()");
if (!carried || carried.app !== "ravis" || carried.view !== "Routes") {
  failures.push(`a fragment carrying a token lost the route: ${JSON.stringify(carried)}`);
}

/* 5 · **Where a page load lands when the address named nothing.** The tab you
 * were on is remembered for one run of the stack: a fresh start opens the
 * dashboard, which is the screen that says what came up and what did not, and
 * a pasted link always beats the memory. Checked here because every one of
 * these failures looks like the router being wrong about something else — a
 * link that opens the wrong screen, or a restart that hides what broke. */
const BOOT = "2026-09-09T10:09:53Z";
const held = { app: "clarvis", view: "Editor", boot: BOOT };
const landed = (opened, memory, boot, remember) =>
  run(`landing(${JSON.stringify(opened)},${JSON.stringify(memory)},`
    + `${JSON.stringify(boot)},${JSON.stringify(remember)})`);

const back = landed(null, held, BOOT, true);
if (!back || back.app !== "clarvis" || back.view !== "Editor") {
  failures.push("a reload did not reopen the tab it was left on, so moving "
    + "between the chat and the editor means finding the way back each time.");
}
if (landed(null, held, "2026-09-09T18:00:00Z", true)) {
  failures.push("a freshly started stack reopened yesterday's screen instead "
    + "of the dashboard — which is the screen that says what came up.");
}
if (landed({ app: "ravis", view: "Routes" }, held, BOOT, true)) {
  failures.push("the memory overrode an address that named a screen, so a "
    + "pasted link opens somewhere else.");
}
if (landed(null, held, BOOT, false)) {
  failures.push("the switch was off and the tab was reopened anyway.");
}
if (landed(null, null, BOOT, true) || landed(null, held, "", true)) {
  failures.push("a page load with nothing remembered, or with no start time to "
    + "compare against, landed somewhere other than the dashboard.");
}

/* 6 · **A top-bar tab opens the screen you were last on in that app.** The bar
 * is how somebody moves between NERVIS and the editor, and opening every app
 * on its first nav item means the trip back lands on the overview rather than
 * on the Files tab that was open a second ago — the same "find your way back"
 * the memory exists to remove. A view that no longer exists falls back rather
 * than blanking the page. */
const views = { nervis: "Files", clarvis: "Nonexistent" };
const opens = (app, remember) =>
  run(`tabView(${JSON.stringify(app)},${JSON.stringify(views)},`
    + `${JSON.stringify(remember)})`);

if (opens("nervis", true) !== "Files") {
  failures.push("switching back to an app opened its first screen instead of "
    + "the one that was open in it a moment ago.");
}
if (opens("clarvis", true) !== exported.APP_CONFIG.clarvis.nav[0]) {
  failures.push("a remembered screen that no longer exists was opened anyway, "
    + "which is the address-bar failure this file's other half is about.");
}
if (opens("nervis", false) !== exported.APP_CONFIG.nervis.nav[0]) {
  failures.push("the switch was off and the tab reopened a remembered screen.");
}
if (opens("sirvis", true) !== exported.APP_CONFIG.sirvis.nav[0]) {
  failures.push("an app with nothing remembered did not open its first screen.");
}

if (!checked) {
  console.error("no screens were checked — that is a fault in this check.");
  process.exit(1);
}
if (failures.length) {
  console.error(`${failures.length} routing failure(s):\n`);
  for (const failure of failures) console.error(`  • ${failure}`);
  process.exit(1);
}
console.log(
  `all ${checked} screens are addressable and round-trip, ` +
  `and a bad address resolves with a stated correction`);
