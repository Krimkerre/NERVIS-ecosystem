/* Render every screen against services that are UP and hold NOTHING.
 *
 * `render_check` covers the from-disk world: every read fails, every screen
 * takes its absent-or-mock path. That is one of three worlds this page has to
 * survive, and it is not the one a new user meets. **The fresh install is the
 * third world** -- the services are running and answer every call with an empty
 * collection -- and nothing exercised it.
 *
 * Six of thirty-five screens threw in it. `now.processes.length` on a sample
 * that carried no process list; `agent.members` where `find` missed and
 * `items[0]` was undefined; `active.label` where RAVIS published no default
 * profile; `payload.items.map` where a proxy reported `available` with a body
 * in another shape. Every one blanks a whole screen, which is the failure
 * `render_check` exists to prevent and could not see, because a mock is never
 * empty.
 *
 * It also caught a regression made an hour earlier in this same session: a
 * live-but-empty `runtimeSet` answer that omitted `members`, which the SIRVIS
 * Dashboard reads.
 *
 * **The stub is deliberately blunt.** Every URL gets the same object, so some
 * failures are shapes a real service would never send. That is the point: a
 * screen must not blank because a field it never checked was missing. Where the
 * fix would be to invent a guarantee the service does not make, guard the read.
 */
// Relative, like every other check here. An absolute path from the machine it
// was written on resolves nowhere else — this file ran green locally and failed
// on every CI push for a day and a half, with the path in the error message.
const { loadPage } = require("./page_context.js");

const EMPTY = JSON.stringify({
  items: [], data: [], results: [], spans: [], events: [], models: [],
  latest_sequence: 0, total: 0, available: true, capabilities: [],
});

const fetchImpl = async () => ({
  ok: true, status: 200,
  json: async () => JSON.parse(EMPTY),
  text: async () => EMPTY,
  headers: { get: () => "application/json" },
  body: null,
});

(async () => {
  const { exported } = loadPage({ fetchImpl });
  const renderers = { nervis: exported.nervis, ravis: exported.ravis,
                      sirvis: exported.sirvis, clarvis: exported.clarvis };
  const failures = [];
  let checked = 0;
  for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
    const render = renderers[app];
    if (typeof render !== "function") continue;
    for (const view of config.nav) {
      checked++;
      exported.state.app = app; exported.state.view = view;
      try {
        await Promise.race([render(),
          new Promise((_, r) => setTimeout(() => r(new Error("did not finish in 5s")), 5000))]);
      } catch (e) { const at=(e.stack||"").split("\n")[1]||""; failures.push(`${app}/${view}: ${e.message}\n      ${at.trim()}`); }
    }
  }
  await new Promise((r) => setTimeout(r, 50));
  if (exported.stopPolling) exported.stopPolling();
  if (!checked) { console.error("nothing rendered — that is a fault in this check"); process.exit(1); }
  if (failures.length) {
    console.error(`${failures.length} of ${checked} screens threw against live-but-empty services:\n`);
    for (const f of failures) console.error(`  • ${f}`);
    process.exit(1);
  }
  console.log(`all ${checked} screens render against live-but-empty services`);
})();
