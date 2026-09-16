/* NERVIS → Diagnostics says "no packet" only while there is none.
 *
 * Found by the owner on 16 September 2026: the banner "No diagnostic packet has
 * been assembled" was drawn on every paint, so after Analyze trace… built a
 * packet the screen showed the packet in "What would be sent" and, below it, the
 * claim that none existed. This drives the real screen in the three states the
 * packet panel has and asserts what is drawn:
 *
 *   1. **No packet, nothing asked:** the banner is there, names Analyze trace…,
 *      and no "What would be sent" card is drawn.
 *   2. **A packet built:** the "What would be sent" card is there and the banner
 *      is not.
 *   3. **A packet that could not be built:** the card says why, and the banner
 *      does not also claim nothing was asked.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
/* The page paints its first screen on load, asynchronously; drawing Diagnostics
   before that lands would have it overwritten. Same wait as `slash_check.js`. */
const quiet = () => new Promise((done) => setTimeout(done, 250));
const BANNER = "No diagnostic packet yet";
const PANEL = "<h3>What would be sent</h3>";

async function drawn(setup) {
  const page = loadPage({ fetchImpl: () => Promise.reject(new TypeError("fetch failed")) });
  const run = (code) => vm.runInContext(code, page.context);
  run("stopPolling()");
  await quiet();
  run(`state.app='nervis'; state.view='Diagnostics'; DIAG_SECTION='services'; ${setup}`);
  await run("diagnosticsView()");
  const content = page.elements.get("sel:#content");
  return content ? content.innerHTML : "";
}

async function main() {
  const nothing = await drawn("DIAGNOSE.packet=null; DIAGNOSE.reason='';");
  if (!nothing.includes("Log adapters")) {
    failures.push("the Diagnostics screen did not render at all, so nothing below proves anything.");
  }
  if (!nothing.includes(BANNER)) failures.push("with no packet the screen does not say so.");
  if (!nothing.includes("Analyze trace…")) failures.push("the banner does not say how to build a packet.");
  if (nothing.includes(PANEL)) failures.push("with no packet a \"What would be sent\" card is still drawn.");

  const built = await drawn(
    "DIAGNOSE.packet={bounds:{events_included:3,events_available:3,truncated:false}};" +
    "DIAGNOSE.prompt='the packet'; DIAGNOSE.reason='';"
  );
  if (!built.includes(PANEL)) failures.push("a built packet is not shown as \"What would be sent\".");
  if (built.includes(BANNER)) failures.push("a built packet is shown and the banner still says there is none.");

  const refused = await drawn("DIAGNOSE.packet=null; DIAGNOSE.reason='the packet could not be built: offline';");
  if (!refused.includes("the packet could not be built: offline")) {
    failures.push("a packet that could not be built does not say why.");
  }
  if (refused.includes(BANNER)) {
    failures.push("after a failed build the banner still claims nothing was asked for.");
  }

  if (failures.length) {
    console.error("diagnose check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "NERVIS Diagnostics holds: the no-packet banner shows only while there is no packet " +
    "and names Analyze trace…, a built packet is shown without it, and a failed build " +
    "says why without it"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
