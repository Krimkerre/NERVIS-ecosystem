/* Render every screen with nothing running, and fail if one throws.
 *
 * The dashboard's one inviolable rule is that it opens from disk with no
 * services up: a live read that throws and stops the render destroys that
 * silently, for everyone who is not currently running the service under test.
 * `tools/check.py` catches a script that stopped parsing; it explicitly catches
 * nothing else. This catches the class that has actually bitten — four times in
 * one week, each a `TypeError` while building a template string:
 *
 *   • SERVICES[row.key].state, where the row's key had no entry
 *   • a reduce seeded with null and not guarded on it
 *   • usable() on a key that was not in the map
 *   • a node list built from a registry that had grown a row
 *
 * Every one blanked a whole screen. None failed a test, because there were no
 * tests — the check was a person opening a browser.
 *
 * **A DOM shim rather than jsdom, and that is the whole trade.** The failures
 * above happen while a render function builds its HTML *string*, before
 * anything touches an element, so the DOM only has to absorb writes rather than
 * model them. Eighty lines of shim buys that; jsdom would buy a faithful DOM,
 * an npm dependency tree and a lockfile in a repository that has none.
 *
 * What that costs, stated plainly: this proves a screen **assembles**. It does
 * not prove the markup is valid, that anything is laid out, or that a click
 * works. It is the cheap half of the check, and the half that keeps failing.
 */

const { loadPage } = require("./page_context.js");

/* **The shim is `page_context.js`, not a copy of it kept here.**
 *
 * This file used to carry its own, and the two drifted in the way that matters:
 * the shared one links `textContent` to `innerHTML` so that `escapeHtml` — the
 * most-called function on the page — returns the text it was given, and this
 * copy left them as two unrelated plain properties, so it returned the **empty
 * string for every value on the page**.
 *
 * The comment recording that fix is in `page_context.js` and says "render_check
 * never noticed". It was still not noticing, because the fix landed in the
 * shared module and this check was not using it. So the gate that runs in CI
 * was the one blind to escaped content: a screen whose text is escaped rendered
 * blank here and passed, which is the exact failure mode this whole file exists
 * to prevent, one level up.
 *
 * One shim, one place to fix, and every check sees the same page.
 */
const { context, exported } = loadPage();

/* Unhandled rejections are the failure mode this is here for: a render that
 * throws inside an `await` reports nowhere otherwise. */
const escaped = [];
process.on("unhandledRejection", (reason) => escaped.push(String(reason)));

/* A watchdog, because a hang and a failure need the same treatment in CI: a
   non-zero exit and a sentence saying what happened. Without one a stuck render
   is a job that runs until the runner's own limit kills it with no message. */
const watchdog = setTimeout(() => {
  console.error("render check did not finish within 60s — a screen is hanging.");
  process.exit(1);
}, 60000);
watchdog.unref?.();
void context;

/* What each tab opens on, pinned.

   `nav()` coerces an unrecognised view to `nav[0]`, so the first entry in a
   nav list is not merely the top of the rail — it is the screen somebody sees
   when they click the tab. Those are two different decisions that share one
   array, and M9 changed the second while meaning to change only the first:
   adding NERVIS's Clarvis diagnostics at the head of the rail moved the whole
   CLARVIS tab off the embedded editor, and the operator's report was *"our
   code-server disappeared"*.

   Nothing caught it. Every screen still rendered, so `render_check` passed
   while the tab no longer opened on the thing it exists for.

   Pinned rather than derived, because there is no property of a nav list that
   says which entry belongs first — only intent. Changing a landing screen now
   means editing this table, which puts the reason in a commit message instead
   of in a reordered array. */
const LANDS_ON = {
  nervis: "Overview",
  sirvis: "Dashboard",
  ravis: "Dashboard",
  clarvis: "Workspace",
};

/* Every screen in `APP_CONFIG`, counted from the page rather than typed here —
   so adding a screen raises the floor automatically and cannot lower it. */
const EXPECTED_SCREENS = Object.values(exported.APP_CONFIG)
  .reduce((total, config) => total + config.nav.length, 0);

async function main() {
  const renderers = { nervis: exported.nervis, ravis: exported.ravis,
                      sirvis: exported.sirvis, clarvis: exported.clarvis };
  const failures = [];
  let checked = 0;

  for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
    const render = renderers[app];
    if (typeof render !== "function") continue;
    for (const view of config.nav) {
      checked += 1;
      if (process.env.RENDER_CHECK_TRACE) require("node:fs").writeSync(2, `  -> ${app}/${view}
`);
      exported.state.app = app;
      exported.state.view = view;
      try {
        /* Bounded, and reported as its own failure. A render that never settles
           is as broken as one that throws, and without a deadline it takes the
           whole check with it instead of naming itself. */
        await Promise.race([
          render(),
          new Promise((_, reject) =>
            setTimeout(() => reject(new Error("did not finish within 5s")), 5000)),
        ]);
      } catch (failure) {
        failures.push(`${app}/${view}: ${failure.message}`);
      }
    }
  }

  /* Give anything the renders kicked off a tick to reject. */
  await new Promise((resolve) => setTimeout(resolve, 50));
  if (exported.stopPolling) exported.stopPolling();

  /* A check that rendered nothing must fail, not pass quietly.
   *
   * The loop skips any app whose renderer is not a top-level function, and the
   * twelve names it destructures out of script scope are a contract nothing
   * enforces — rename `nervis`, or move it inside a block, and `renderers.nervis`
   * becomes undefined, every one of its screens is skipped, and this printed
   * "all 27 screens render" in green. `empty_world_check` has had this guard
   * since it was written; this file did not, which made the CI gate the one that
   * could congratulate itself on doing nothing. */
  if (checked < EXPECTED_SCREENS) {
    console.error(
      `only ${checked} of ${EXPECTED_SCREENS} screens were rendered — a renderer` +
      `\nis missing from the page's top-level scope, so this check proved nothing.`);
    process.exit(1);
  }

  const moved = Object.entries(LANDS_ON)
    .filter(([app, view]) => exported.APP_CONFIG[app]?.nav?.[0] !== view)
    .map(([app, view]) =>
      `${app} opens on ${exported.APP_CONFIG[app]?.nav?.[0]}, not ${view}`);
  if (moved.length) {
    console.error(`${moved.length} tab(s) open on a different screen than before:\n`);
    for (const one of moved) console.error(`  • ${one}`);
    console.error(
      "\n`nav()` coerces an unknown view to nav[0], so the first entry is the" +
      "\nscreen a tab opens on and not just the top of the rail. If the move was" +
      "\ndeliberate, update LANDS_ON in this file and say why in the commit.");
    process.exit(1);
  }

  if (failures.length) {
    console.error(`${failures.length} of ${checked} screens failed to render:\n`);
    for (const failure of failures) console.error(`  • ${failure}`);
    console.error(
      "\nEvery one of these blanks a whole screen in a browser. The dashboard's" +
      "\nrule is that it renders with nothing running — a live read that throws" +
      "\nand stops the render breaks that silently."
    );
    process.exit(1);
  }
  if (escaped.length) {
    console.error(`${escaped.length} unhandled rejection(s) escaped a render:\n`);
    for (const reason of new Set(escaped)) console.error(`  • ${reason}`);
    process.exit(1);
  }
  clearTimeout(watchdog);
  console.log(`all ${checked} screens render with nothing running`);
}

main().catch((failure) => {
  /* Without this, a throw inside `main` itself lands in the unhandledRejection
     collector, the process exits 0, and the check reports nothing at all —
     which is the one outcome worse than a false failure. */
  console.error(`the render check itself failed: ${failure.stack || failure}`);
  process.exit(1);
});
