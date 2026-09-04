/* Feed every screen data that is trying to inject markup, and see if it lands.
 *
 * `NERVIS.md` §25.2 states the requirement in one sentence: *do not port
 * `innerHTML` interpolation into a surface that will hold provider output*.
 * Model IDs, provider labels, route-explanation reasons, benchmark fields and
 * upstream error text are all written by somebody else, and every one of them
 * reaches this page and is interpolated into a template literal.
 *
 * **Why a probe rather than a grep.** The static question — "is this value
 * escaped?" — cannot be answered by reading the file. A value passes through
 * an adapter, a shaping function, three helpers and a builder before it is
 * interpolated, and the escape may be at any of those layers or at none. The
 * only reliable question is the one a browser asks: *given hostile input, did a
 * tag appear in the output that was not in the template?* That is decidable,
 * and this decides it for all thirty-five screens at once.
 *
 * **How the payload is delivered.** Every `API` method is wrapped so its result
 * has the marker appended to every string, at every depth, with the shape left
 * exactly as it was. Appending rather than replacing is deliberate: the page
 * compares strings against enum values in dozens of places, and replacing them
 * would send every screen down its unknown-state branch and prove nothing about
 * the branch that normally runs.
 *
 * **What a finding means.** `<vxs` in the output is a tag the page did not
 * write. There is no benign reading of it: the same position would take
 * `<script>` or an `onerror` handler from a provider's error text.
 *
 * **Quoted attributes used to be outside this check**, settled by reading
 * `escapeHtml` rather than by probing. That was a reasonable line to draw while
 * the count was nine and the tag probe had plenty to find; it is the wrong line
 * at zero, where the only remaining way back in is an escaper at some call site
 * that covers angle brackets and forgets quotes. `attributeBreakout` asks that
 * question directly now, on the same render machinery.
 */

const { loadPage } = require("./page_context.js");

/* **Zero, and the ratchet is over.** This was 28 sites across 27 screens when
   the check was written, with 579 individual interpolations reaching the markup
   unescaped; the invented-data sweep took it to nine, and §16 item 3 took the
   last nine.

   It stays a named constant rather than becoming a bare `if (findings.length)`
   so that raising it is a visible edit in a diff somebody reviews, with this
   comment attached. **It must not be raised.** A screen that cannot be escaped
   is a screen that needs `textContent`, not an allowance — and the two helpers
   most of the nine ran through, `kpis` and `prov`, now escape by default, so a
   new screen inherits the safe behaviour instead of having to remember it. */
const CEILING = 0;

/* Two markers, because there are two questions.
 *
 * `<vxs>` is a tag that does not exist. If it survives to the output as a tag,
 * an element was injected. `vxjs` rides along inside a handler attribute so a
 * finding can say *which* injection shape got through.
 *
 * The string is prefixed with a space and no quotes of its own, so appending it
 * to a value that is about to be interpolated into an attribute does not, by
 * itself, break the attribute — this check reports element injection, and a
 * quote-broken attribute would muddy that signal with a second failure mode. */
const PROBE = ' <vxs onerror=vxjs>';
const ESCAPED = '&lt;vxs';

/* The third question, and the one the tag probe deliberately cannot ask.
 *
 * `PROBE` carries no quotes of its own precisely so that element injection
 * reports cleanly (see above), which leaves *attribute breakout* unmeasured: a
 * value interpolated inside `title="…"` or `data-kind="…"` escapes its
 * attribute with a bare `"` and gains a handler without ever opening a tag.
 * `escapeHtml` covers it — it escapes quotes as well as angle brackets — and an
 * escaper that did not is the ordinary way this comes back.
 *
 * A raw `"` immediately before the marker means the quote arrived from data as
 * a quote. `&quot; vxatr` is the same value escaped, and is the pass. */
const ATTR_PROBE = '" vxatr=vxjs';
const ATTR_BROKE = '" vxatr';

function poison(value, depth = 0, probe = PROBE) {
  if (depth > 12) return value;
  if (typeof value === "string") return value + probe;
  if (Array.isArray(value)) return value.map((item) => poison(item, depth + 1, probe));
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value)) out[key] = poison(value[key], depth + 1, probe);
    return out;
  }
  return value;
}

/* Wrap every method of every `API` namespace. The mocks already produce
   correctly shaped payloads for all thirty-five screens, which is what makes
   this cover the whole page rather than the handful of endpoints a fixture
   file happens to describe. */
function poisonApi(api, probe = PROBE) {
  let wrapped = 0;
  for (const namespace of Object.keys(api)) {
    const group = api[namespace];
    if (!group || typeof group !== "object") continue;
    for (const name of Object.keys(group)) {
      const original = group[name];
      if (typeof original !== "function") continue;
      /* Internal helpers are left alone. A public method that returns one is
         wrapped itself, so the payload still arrives — and wrapping both would
         append the marker twice and make a finding harder to read. */
      if (name.startsWith("_")) continue;
      /* A `function`, not an arrow. Several methods call a sibling through
         `this._somethingMock()`, and an arrow captures the module's `this`
         instead of the namespace's — which turns every one of those into
         "is not a function" and stops the check before it proves anything. */
      group[name] = async function (...args) {
        return poison(await original.apply(this, args), 0, probe);
      };
      wrapped += 1;
    }
  }
  return wrapped;
}

(async () => {
  const { exported, elements } = loadPage();
  const wrapped = poisonApi(exported.API);
  if (!wrapped) {
    console.error("no API methods were wrapped — this check proved nothing.");
    process.exit(1);
  }

  const renderers = { nervis: exported.nervis, ravis: exported.ravis,
                      sirvis: exported.sirvis, clarvis: exported.clarvis };
  const findings = [];
  const threw = [];
  let checked = 0;
  /* Set while sweeping, because the check below used to read only whatever the
     *last* screen left in the DOM. That was survivable while findings existed —
     a non-empty result skips the test — and it failed the moment the count
     reached zero, reporting "the probe never reached the markup" for a page
     where every screen had just escaped it correctly. The self-check was right
     to exist and wrong about where to look. */
  let everReached = false;

  for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
    const render = renderers[app];
    if (typeof render !== "function") continue;
    for (const view of config.nav) {
      checked += 1;
      exported.state.app = app;
      exported.state.view = view;
      for (const node of elements.values()) node.innerHTML = "";
      try {
        await Promise.race([render(),
          new Promise((_, reject) =>
            setTimeout(() => reject(new Error("did not finish in 5s")), 5000))]);
      } catch (failure) {
        threw.push(`${app}/${view}: ${failure.message}`);
      }
      for (const [key, node] of elements.entries()) {
        const markup = String(node.innerHTML || "");
        /* Recorded per screen, not read off the last one. See `reached` below. */
        if (markup.includes(ESCAPED)) everReached = true;
        if (!markup.includes("<vxs")) continue;
        const at = markup.indexOf("<vxs");
        findings.push({
          screen: `${app}/${view}`,
          sink: key,
          context: markup.slice(Math.max(0, at - 90), at + 24).replace(/\s+/g, " "),
        });
      }
    }
  }

  await new Promise((resolve) => setTimeout(resolve, 50));
  if (exported.stopPolling) exported.stopPolling();

  if (!checked) {
    console.error("nothing rendered — that is a fault in this check.");
    process.exit(1);
  }

  /* Proof the probe can be seen at all. If nothing anywhere contains even the
     *escaped* marker, the payload never reached the markup and a clean result
     would mean the check is broken rather than the page is safe — which is the
     failure mode that made an earlier honesty check report every badge correct
     while capturing nothing. */
  const reached = everReached
    || [...elements.values()].some((node) => String(node.innerHTML || "").includes(ESCAPED));
  if (!reached && !findings.length) {
    console.error(
      "the probe never reached the markup — no screen contains it in either\n" +
      "form, so this run proved nothing. Check that API wrapping still works."
    );
    process.exit(1);
  }

  if (threw.length) {
    console.log(`${threw.length} screen(s) threw on poisoned data (not injection):`);
    for (const failure of threw) console.log(`  · ${failure}`);
    console.log("");
  }

  /* Both directions are measured before either is reported. A run that stopped
     at the first finding would hide the second half of the property every time
     the first half regressed — and the two are fixed together. */
  const over = await overEscaped();
  const broke = await attributeBreakout();

  if (broke.length) {
    console.error(
      `${broke.length} attribute breakout(s) — a quote from data closed an attribute:\n`);
    for (const f of broke) console.error(`  • ${f.screen} → ${f.sink}\n      …${f.context}…`);
    console.error(
      "\nThe value needs an escaper that covers quotes, not only angle\n" +
      "brackets. `escapeHtml` does; a bespoke one at the call site may not.\n");
  }

  /* A ratchet, not a pass/fail, and one-sided.
   *
   * The page had 28 of these when this check was written, across 27 of the 35
   * screens, and every one of them is a template literal that predates the
   * rule. Failing outright would mean either reverting the check or converting
   * eight thousand lines in one commit — and PITFALLS.md section 1 is a list of
   * what happens to this file when it is edited in one commit.
   *
   * So the number may fall and may never rise. A new screen written the old way
   * fails immediately, which is the property worth having; the backlog comes
   * down in passes that can each be verified.
   *
   * `liveness_check` pins its count from both sides because a card that stops
   * hardcoding its class should be *noticed*. This one is different: there is
   * no reason to be told the page got safer, so lower is silent. */
  if (findings.length > CEILING) {
    const screens = new Set(findings.map((f) => f.screen));
    console.error(
      `${findings.length} injection site(s) across ${screens.size} screen(s), ` +
      `above the ceiling of ${CEILING}:\n`);
    for (const f of findings) {
      console.error(`  • ${f.screen} → ${f.sink}`);
      console.error(`      …${f.context}…`);
    }
    console.error(
      "\nEach of these renders a tag supplied by data. The same position takes" +
      "\nan onerror handler from a provider's error text or a model ID." +
      "\nEscape the value where it enters the markup.");
  } else if (findings.length) {
    console.log(
      `${findings.length} injection site(s) remain, at or under the ceiling of ` +
      `${CEILING}. Lower the ceiling in this file when you lower the count.`);
  }

  if (over.length) {
    console.error(`${over.length} screen(s) render the page's own markup as text:\n`);
    for (const f of over) {
      console.error(`  • ${f.screen} → ${f.sink}`);
      console.error(`      …${f.context}…`);
    }
    console.error(
      "\nA markup fragment was escaped as though it were data. The tags show on" +
      "\nscreen as text. This is the other half of the same mistake and the half" +
      "\nno security check looks for.");
  }
  if (findings.length > CEILING || over.length || broke.length) process.exit(1);

  /* Said only when it is true. A check that prints "no injection" while
     reporting twenty-two of them two lines above is the kind of confident
     wrong sentence this whole repository is written against. */
  if (!findings.length) {
    console.log(
      `no injection, no attribute breakout and no over-escaping — ` +
      `${wrapped} API methods across ${checked} screens, all three probes`);
  } else {
    console.log(
      `${checked} screens probed through ${wrapped} API methods; no screen ` +
      `escapes its own markup`);
  }
})();

/* The mirror of everything above.
 *
 * Escaping is a property with two failure directions and only one of them is a
 * security bug, which is why only one of them ever gets a check. Escape too
 * little and a provider writes markup into the page; escape too much and the
 * page writes its own markup into itself as text — `&lt;div class="card"&gt;`
 * rendered as words. The second is not exploitable and is *more* likely,
 * because it is what happens every time somebody wraps the wrong value while
 * fixing the first.
 *
 * Together these two make an automated sweep safe to attempt: without this
 * half, "escape everything" passes the injection check and destroys the page.
 *
 * A fresh load, because the run above deliberately poisoned every API method. */
/* Attribute breakout, over every screen, on the same render machinery. */
async function attributeBreakout() {
  const { exported, elements } = loadPage();
  /* Shared with the tag pass rather than re-implemented: the first version
     wrapped the methods here with an arrow and lost `this`, which is the exact
     trap `poisonApi` already carries a comment about. */
  poisonApi(exported.API, ATTR_PROBE);
  const renderers = { nervis: exported.nervis, ravis: exported.ravis,
                      sirvis: exported.sirvis, clarvis: exported.clarvis };
  const found = [];
  for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
    const render = renderers[app];
    if (typeof render !== "function") continue;
    for (const view of config.nav) {
      exported.state.app = app;
      exported.state.view = view;
      for (const node of elements.values()) node.innerHTML = "";
      try {
        await Promise.race([render(),
          new Promise((_, reject) =>
            setTimeout(() => reject(new Error("slow")), 5000))]);
      } catch { /* a throw is render_check's finding, not this one */ }
      for (const [key, node] of elements.entries()) {
        const markup = String(node.innerHTML || "");
        const at = markup.indexOf(ATTR_BROKE);
        if (at === -1) continue;
        found.push({ screen: `${app}/${view}`, sink: key,
          context: markup.slice(Math.max(0, at - 80), at + 30).replace(/\s+/g, " ") });
      }
    }
  }
  if (exported.stopPolling) exported.stopPolling();
  return found;
}

async function overEscaped() {
  const { exported, elements } = loadPage();
  const renderers = { nervis: exported.nervis, ravis: exported.ravis,
                      sirvis: exported.sirvis, clarvis: exported.clarvis };
  /* Tag names this page actually writes. Matching `&lt;` followed by any letter
     would flag ordinary prose — a route explanation reading "context &lt; 8192"
     is data being escaped correctly, which is the opposite of a finding. */
  const TAGS = "div|span|b|i|small|strong|button|table|tr|td|th|h3|h4|p|br|hr" +
               "|details|summary|svg|path|circle|rect|input|select|option|label" +
               "|a|ul|li|code|pre|iframe|canvas|img|form|textarea";
  const escapedTag = new RegExp(`&lt;/?(?:${TAGS})(?:[ >]|&gt;)`, "i");
  const found = [];
  for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
    const render = renderers[app];
    if (typeof render !== "function") continue;
    for (const view of config.nav) {
      exported.state.app = app;
      exported.state.view = view;
      for (const node of elements.values()) node.innerHTML = "";
      try {
        await Promise.race([render(),
          new Promise((_, reject) =>
            setTimeout(() => reject(new Error("slow")), 5000))]);
      } catch { /* a throw is render_check's finding, not this one */ }
      for (const [key, node] of elements.entries()) {
        const markup = String(node.innerHTML || "");
        const hit = escapedTag.exec(markup);
        if (!hit) continue;
        found.push({ screen: `${app}/${view}`, sink: key,
          context: markup.slice(Math.max(0, hit.index - 70), hit.index + 40)
            .replace(/\s+/g, " ") });
      }
    }
  }
  if (exported.stopPolling) exported.stopPolling();
  return found;
}
