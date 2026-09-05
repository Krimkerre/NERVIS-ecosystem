/* Render every screen against services that are UP and offer NOTHING.
 *
 * `ECOSYSTEM_RUNBOOK.md` §15: *"NERVIS negotiates actual capabilities; no UI
 * assumes a missing API."* The page has three mechanisms for that —
 * `capable()`, `cell()`'s withheld branch and `gated()` — and until this gate
 * nothing exercised any of them. They were built after `capable` was found
 * defined-and-never-called, which is a fault this repository has now made twice;
 * a mechanism with no check is the same shape one step later.
 *
 * **The world here is the one the others do not cover.** `render_check` runs
 * with every read failing, and `empty_world_check` with every read answering
 * emptily — in both, services are *reachable or not*. This one has every service
 * healthy, answering, and advertising an empty capability list, with the
 * registry having actually answered so the page is entitled to believe it. That
 * is a peer which has withdrawn a surface, and it is the case §15's sentence is
 * about: the screen must say the surface is withheld rather than draw the
 * absence as data.
 *
 * Three things are asserted, and the third is the one that makes the first two
 * mean anything:
 *
 *   1. No screen throws in that world.
 *   2. Screens that need a capability render the withheld block, and every
 *      acting control that names one comes out disabled with a reason.
 *   3. The same screens, with the capabilities advertised, render neither —
 *      because a gate that reports "withheld" unconditionally would pass 1 and 2
 *      while telling the reader nothing.
 */
const { loadPage } = require("./page_context.js");
const vm = require("vm");

/* Healthy, answering, and advertising a list that does not contain what the
   screens ask for. **Not an empty list**, deliberately: `capabilitiesKnown`
   requires `advertised.length > 0`, because the page's from-disk fallback rows
   carry `caps: []` as the sentinel for "no registry has told us anything", and
   gating on that would turn thirty-five cards into "has withdrawn this surface"
   on a page opened with nothing running — a claim nobody is entitled to make.
   So the withdrawal this gate exercises is the realistic one and the one the
   branch was written for: a peer that is up, answering, and no longer offering
   the surface a screen needs. */
const services = (caps) => ({
  items: ["nervis", "ravis", "sirvis", "clarvis"].map((key) => ({
    key, label: key.toUpperCase(), endpoint: `http://127.0.0.1/${key}`,
    state: "healthy", detail: "", ownership: "external", optional: false,
    awaiting_first_contact: false, publishes_mep: true,
    /* The wire shape, which is an object of id → state rather than a list: the
       page keeps the ids reading `available` or `degraded`, so a check handing
       it an array would be testing a payload no service sends. */
    capabilities: Object.fromEntries(caps.map((id) => [id, "available"])),
  })),
  operations: [], refused: [],
});

/* Every other endpoint. `capabilities` is a *list* here — `/ecosystem/capabilities`
   returns one — and only the registry's own row carries the id → state object. A
   stub that used one shape everywhere made three screens throw on `list.filter`,
   which is the stub being wrong rather than the page. */
const body = (caps) => ({
  items: [], data: [], results: [], spans: [], events: [], models: [],
  latest_sequence: 0, total: 0, available: true,
  capabilities: caps.map((id) => ({ id, version: "1.0.0", state: "available",
                                    constraints: {}, reason: "" })),
  operations: [], refused: [],
});

function pageWith(caps) {
  const answer = (url) => (String(url).includes("/api/v1/services")
    ? services(caps) : body(caps));
  return loadPage({
    fetchImpl: async (url) => {
      const payload = answer(url);
      const text = JSON.stringify(payload);
      return { ok: true, status: 200, json: async () => payload, text: async () => text,
               headers: { get: () => "application/json" }, body: null };
    },
  });
}

/* Every capability the page's own screens declare a need for. Read out of the
   page rather than listed here: a list written in this file would be a second
   copy to keep in step, and the first thing it would miss is a capability added
   next week. */
function declared(context) {
  return vm.runInContext(
    "Array.from(new Set(Object.values(TILE_CAPABILITY).flatMap(Object.values)))",
    context,
  );
}

/* Wait until the page's own registry read has landed.

   Without it the gate would render against `REGISTRY_AT === 0`, where the page
   correctly falls back to reachability and the withheld branch never fires —
   and the check would report a mechanism as dead when it was only unasked. */
async function settled(context) {
  for (let waited = 0; waited < 2000; waited += 25) {
    if (vm.runInContext("typeof REGISTRY_AT !== 'undefined' && REGISTRY_AT > 0", context)) return;
    await new Promise((r) => setTimeout(r, 25));
  }
  throw new Error("the page never recorded a registry read; the stub is not being reached");
}

async function renderAll(page) {
  const { exported, elements, context } = page;
  const renderers = { nervis: exported.nervis, ravis: exported.ravis,
                      sirvis: exported.sirvis, clarvis: exported.clarvis };
  const drawn = [];
  const threw = [];
  for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
    const render = renderers[app];
    if (typeof render !== "function") continue;
    for (const view of config.nav) {
      exported.state.app = app; exported.state.view = view;
      try {
        await Promise.race([render(),
          new Promise((_, r) => setTimeout(() => r(new Error("did not finish in 5s")), 5000))]);
        /* `#content` is where a screen writes itself. `elements` is a Map keyed
           by selector, not a plain object — reading it as one collected nothing
           and made a working gate look like a dead mechanism. */
        const content = elements.get("sel:#content");
        drawn.push({ app, view, html: (content && content.innerHTML) || "" });
      } catch (e) { threw.push(`${app}/${view}: ${e.message}`); }
    }
  }
  await new Promise((r) => setTimeout(r, 50));
  if (exported.stopPolling) exported.stopPolling();
  return { drawn, threw, context };
}

(async () => {
  const failures = [];

  // ── The world where every surface has been withdrawn ───────────────────────
  const withheld = pageWith(["ecosystem.something_else@1"]);
  // The page's own read of /api/v1/services sets `REGISTRY_AT`; waiting for it
  // is what entitles the page to believe a capability list at all.
  await settled(withheld.context);
  const offered = declared(withheld.context);
  const gone = await renderAll(withheld);
  for (const failure of gone.threw) failures.push(`threw with every surface withdrawn — ${failure}`);

  const saidWithheld = gone.drawn.filter((s) => s.html.includes('data-state="incapable"'));
  if (!saidWithheld.length) {
    failures.push(
      "no screen reported a withdrawn surface while every service advertised none — " +
      "`cell()`'s withheld branch is unreached, so the gate is decorative");
  }

  // An acting control that names a capability must come out disabled. `gated()`
  // returns the attribute; a button carrying its reason and not `disabled` is
  // the click-time failure this replaced.
  for (const screen of gone.drawn) {
    const enabled = (screen.html.match(/<button(?:(?!>)[\s\S])*?does not currently offer[\s\S]*?>/g) || [])
      .filter((tag) => !tag.includes("disabled"));
    for (const tag of enabled) {
      failures.push(`${screen.app}/${screen.view}: a control names a missing capability and is `
        + `still clickable — ${tag.slice(0, 90)}`);
    }
  }

  // ── The control world: the same screens with the surfaces advertised ───────
  const held = pageWith(offered);
  await settled(held.context);
  const there = await renderAll(held);
  for (const failure of there.threw) failures.push(`threw with every surface advertised — ${failure}`);

  const stillWithheld = there.drawn.filter((s) => s.html.includes('data-state="incapable"'));
  if (stillWithheld.length) {
    failures.push(
      `${stillWithheld.length} screen(s) still report a withdrawn surface while every capability `
      + `is advertised (e.g. ${stillWithheld[0].app}/${stillWithheld[0].view}) — the gate is `
      + "reporting absence it cannot know");
  }

  if (!gone.drawn.length) {
    console.error("nothing rendered — that is a fault in this check");
    process.exit(1);
  }
  if (failures.length) {
    console.error(`capability gating does not hold (${failures.length}):\n`);
    for (const f of failures) console.error(`  • ${f}`);
    process.exit(1);
  }
  console.log(
    `capability gating holds: ${gone.drawn.length} screens survive every surface being `
    + `withdrawn, ${saidWithheld.length} say so, and none says so when the ${offered.length} `
    + `declared capabilities are advertised`);
})();
