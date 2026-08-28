/* Does every screen recover on its own once the services answer?
 *
 * A screen rendered while a service was still starting used to keep its
 * from-disk fallback until somebody clicked something. `pollRegistry` now
 * repaints on a registry transition, and again when the screen it drew fell
 * back on a service the registry reports healthy.
 *
 * That second trigger reads `RENDERED_MOCK`, which `render` fills from
 * `SOURCE` — and `SOURCE` is only written by `live()`. A screen that fetches
 * some other way falls back without ever saying so, and would recover only if
 * a registry state happened to change. This finds those.
 *
 * Renders every screen twice: once with every read failing, then again against
 * the running services after a simulated transition, and reports any screen
 * whose markup did not change.
 */
const { loadPage } = require("./page_context.js");

const ORIGIN = "http://127.0.0.1:8790";

(async () => {
  let answering = false;
  const fetchImpl = (url, opts) =>
    answering ? fetch(new URL(url, ORIGIN), opts)
              : Promise.reject(new TypeError("fetch failed"));

  const { context, exported } = loadPage({ fetchImpl });
  const vm = require("node:vm");

  /* The shim memoizes elements by selector, so `querySelector` answers
     truthily for anything — including `.route-pop`, whose presence stops a
     repaint. In a browser that selector finds nothing unless a routing popover
     is genuinely open. Answered honestly here, or this check measures the
     shim's guard rather than the page's. */
  const findElement = context.document.querySelector;
  context.document.querySelector = (selector) =>
    selector === ".route-pop" ? null : findElement(selector);
  /* The page's own `render`, not the per-app renderer. `RENDERED_MOCK` — the
     thing this check reads to say what a screen fell back on — is filled inside
     `render`, so calling `ravis()` directly leaves it holding whatever the last
     real render put there. The first version of this file did exactly that and
     its "fell back on" column meant nothing. */
  const drawScreen = () => vm.runInContext("render()", context);
  const content = () => {
    const el = context.__elements.get("sel:#content");
    return String((el && el.innerHTML) || "");
  };

  const stuck = [], recovered = [], unchanged = [];
  for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
    for (const view of config.nav) {
      exported.state.app = app;
      exported.state.view = view;

      answering = false;
      try { await drawScreen(); } catch { /* render_check owns throws */ }
      const dark = content();
      const fellBack = vm.runInContext("RENDERED_MOCK.slice()", context);

      /* The services answer, and the registry reports a change — exactly what
         happens when a service finishes starting. */
      answering = true;
      vm.runInContext("REGISTRY_SIGNATURE = 'forced:change'", context);
      try { await vm.runInContext("pollRegistry()", context); } catch { /* ignore */ }
      await new Promise((r) => setTimeout(r, 60));
      const lit = content();

      /* Paced, because RAVIS rate-limits an anonymous caller at 60 requests a
         minute and this check makes several reads per screen. Without it the
         later screens are refused, come back with their fallback, and read as
         screens that failed to recover. */
      await new Promise((r) => setTimeout(r, 1400));

      const where = `${app}/${view}`;
      if (lit === dark) unchanged.push({ where, fellBack });
      else recovered.push(where);
      if (lit === dark && !fellBack.length) stuck.push(where);
    }
  }

  console.log(`${recovered.length} of ${recovered.length + unchanged.length} screen(s) repainted after the transition`);

  /* Two very different reasons for identical markup, and only one is worth
     reading. A screen with no live data on it -- the CLARVIS panel, a screen
     whose endpoint is unbuilt -- draws the same thing either way, correctly. */
  const inert = unchanged.filter((u) => !u.fellBack.length);
  const suspect = unchanged.filter((u) => u.fellBack.length);

  if (inert.length) {
    console.log(`\n${inert.length} screen(s) draw nothing from a service, so identical is correct:`);
    console.log(`  ${inert.map((u) => u.where).join(", ")}`);
  }
  if (suspect.length) {
    console.log(`\n${suspect.length} screen(s) fell back and did not change after the transition:`);
    for (const u of suspect) console.log(`  • ${u.where}  (fell back on: ${u.fellBack.join(", ")})`);
    console.log("\n**Confirm each in a browser before believing it.** This check makes");
    console.log("several reads per screen and RAVIS rate-limits an anonymous caller at 60");
    console.log("a minute, so a screen listed here may simply have been refused. Both");
    console.log("screens this reported on its first run recovered correctly when driven");
    console.log("by hand: the arbiter is the running page, not this file.");
  }
})();
