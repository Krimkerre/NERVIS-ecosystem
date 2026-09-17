/* The Traces screen's log lines say how they were tied to the trace, and never claim more.
 *
 * On 17 September 2026 a trace from the day before showed the newest lines of each log under
 * "These lines are from around the same time": nothing on a log line said when it was written.
 * NERVIS 0.34.20 offers a line by its own time only, and this drives the real card:
 *
 *   1. **Lines carrying the trace id** are said to be part of the trace.
 *   2. **Lines from the window** are said to be from around the same time, each with its time.
 *   3. **An empty window** is headed "No log line can be tied to this trace", with NERVIS's reason,
 *      never the "around the same time" heading over nothing.
 *   4. **Nothing a log line carries becomes markup.**
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const { context } = loadPage();
vm.runInContext("stopPolling()", context);
const HOSTILE = '<img src=x onerror="alert(1)">';

function card(logs) {
  context.__logs = logs;
  return vm.runInContext("logCorrelation(globalThis.__logs)", context);
}

function expect(label, html, present, absent = []) {
  for (const words of present) if (!html.includes(words)) failures.push(`${label}: missing ${JSON.stringify(words)}`);
  for (const words of absent) if (html.includes(words)) failures.push(`${label}: should not say ${JSON.stringify(words)}`);
}

const TIME = "2026-09-16T13:38:38.100Z";
const readable = new Date(TIME).toLocaleString();

expect("trace id", card({ how: "carries the trace id", reason: "",
  items: [{ time: TIME, service: "ravis", level: "INFO", message: "routed it" }] }),
  ["These lines are part of this trace", "routed it", readable], ["around the same time"]);

expect("window", card({ how: "written during the same window",
  reason: "no log line carries this trace id — these were written within 2 s of it",
  items: [{ time: TIME, service: "nervis", level: "INFO", message: "near it" }] }),
  ["These lines are from around the same time", "within 2 s", "near it", readable],
  ["No log line can be tied"]);

for (const reason of [
  "no log line carries this trace id, and none of the lines read says when it was written",
  "no log line carries this trace id, and none of the lines read was written within 2 s of it",
]) {
  expect("empty window", card({ how: "written during the same window", reason, items: [] }),
    ["No log line can be tied to this trace", reason], ["around the same time"]);
}

expect("a line with no time", card({ how: "carries the trace id", reason: "",
  items: [{ service: "sirvis", level: "", message: "untimed" }] }), ["untimed", "—"]);

const hostile = card({ how: HOSTILE, reason: HOSTILE,
  items: [{ time: HOSTILE, service: HOSTILE, level: HOSTILE, message: HOSTILE }] });
expect("hostile", hostile, ["&lt;img"], ["<img"]);

if (failures.length) {
  console.error("trace log lines check failed:\n");
  for (const line of failures) console.error("  - " + line + "\n");
  process.exit(1);
}
console.log("trace log lines say how they were tied: the trace id, a time window with each line's " +
  "time, or nothing with the reason — never 'around the same time' over nothing, and nothing injects");
