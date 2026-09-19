/* What SIRVIS screens say while LM Studio or Hugging Face is down (§15.4, 19 September 2026).
 *
 * SIRVIS lowers the capabilities that need LM Studio or Hugging Face and starts each reason
 * "Waiting on <who>, which <why>: <what stops>". The page turns those into one banner per
 * cause (`sirvisWaiting`) and puts the reason on a button it disables (`gated`). Checked:
 *
 *   - one banner per cause, each loss listed once under it, even when one capability's
 *     reason names two causes;
 *   - nothing drawn when every capability is available, or when SIRVIS itself is down
 *     (the screen's own absence notice says that);
 *   - a disabled button carries SIRVIS's sentence, escaped, instead of the capability id.
 *
 *   node tools/sirvis_waiting_check.js
 */

const { loadPage } = require("./page_context.js");
const vm = require("vm");

const { context } = loadPage({
  fetchImpl: async () => ({ ok: false, status: 503, json: async () => ({}),
                            text: async () => "", headers: { get: () => "" }, body: null }),
});
const run = (code) => vm.runInContext(code, context);

const LM = "Waiting on LM Studio, which isn't answering: ";
const HF = "Waiting on Hugging Face, which failed SIRVIS's last read (Hugging Face answered "
  + "HTTP 503): ";
const world = (state, capStates, reasons) => run(`SERVICES.sirvis.state=${JSON.stringify(state)};
  SERVICES.sirvis.capStates=${JSON.stringify(capStates)};
  SERVICES.sirvis.reasons=${JSON.stringify(reasons)};
  SERVICES.sirvis.caps=Object.entries(SERVICES.sirvis.capStates)
    .filter(([,s])=>s!=='unavailable').map(([id])=>id);`);

const failures = [];
const expect = (ok, what) => { if (!ok) failures.push(what); };

world("healthy", {
  "sirvis.runtime.control": "unavailable",
  "sirvis.downloads": "degraded",
  "sirvis.catalog.read": "degraded",
  "sirvis.runtime_sets": "available",
}, {
  "sirvis.runtime.control": LM + "no model can be loaded or unloaded",
  "sirvis.downloads": HF + "a new download can't be checked before it starts; "
    + LM + "past downloads can be read, but a new one can't start",
  "sirvis.catalog.read": HF + "models can't be searched <right now>",
});
const drawn = run("sirvisWaiting()");
const banners = drawn.split('class="banner"').length - 1;
expect(banners === 2, `two causes should make two banners, drew ${banners}`);
expect(drawn.includes("<b>LM Studio isn&#39;t answering</b>") || drawn.includes("<b>LM Studio isn't answering</b>"),
  "the LM Studio banner is titled with its cause");
expect(drawn.includes("no model can be loaded or unloaded; past downloads can be read"),
  "LM Studio's losses are listed together under it, from both capabilities");
expect(drawn.includes("&lt;right now&gt;") && !drawn.includes("<right now>"),
  "a reason is escaped before it is drawn");

const button = run("gated('sirvis','sirvis.runtime.control')");
expect(button.includes("disabled") && button.includes("no model can be loaded or unloaded"),
  `a disabled button carries SIRVIS's reason — got ${button}`);
expect(run("gated('sirvis','sirvis.downloads')") === "", "a degraded capability stays usable");

world("healthy", { "sirvis.runtime.control": "available" }, {});
expect(run("sirvisWaiting()") === "", "nothing is drawn when everything is available");

world("unreachable", { "sirvis.runtime.control": "unavailable" },
  { "sirvis.runtime.control": LM + "no model can be loaded or unloaded" });
expect(run("sirvisWaiting()") === "", "nothing is drawn for a SIRVIS that is itself down");

if (failures.length) {
  console.error(`SIRVIS's waiting notice does not hold (${failures.length}):\n`);
  for (const f of failures) console.error(`  • ${f}`);
  process.exit(1);
}
console.log("SIRVIS's waiting notice holds: one banner per cause, reasons on disabled buttons, "
  + "nothing when all is well or SIRVIS is down");
