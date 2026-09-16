/* The Clarvis diagnostics screen lists the tasks NERVIS handed over, and how far each got.
 *
 * The owner decided on 16 September 2026 that a Clarvis "task" is a handover from NERVIS;
 * NERVIS 0.34.9 joins its own record of each handover with Clarvis's `clarvis.task.*`
 * reports and draws them above the window cards. This drives the real screen against
 * recorded answers:
 *
 *   1. **Handovers at every stage** — waiting, planning, building, paused, built — each in
 *      words, with its folder, id and times; one Clarvis reported with no handover record
 *      said as such.
 *   2. **With no editor window open** the list still draws: a task outlives its window.
 *   3. **None, and NERVIS not answering**, said in words.
 *   4. **A window's own task rows** show the stage too.
 *   5. **What a window reports beside its state** (Clarvis 0.17.15, §6.3): problem counts, the
 *      last build and test, the last model request, the task and the event cursor, each as a
 *      row only when reported.
 *   6. **Nothing a handover or a status carries becomes markup.**
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const HOSTILE = '<img src=x onerror="alert(1)">';
const WINDOW = "inst-1";

const ITEMS = [
  { task_id: "nt_000000000000000a", folder: "nervis-tasks/pomodoro-timer", state: "waiting", stage: "",
    outcome: "", handed_over_at: "2026-09-16T10:00:00Z", started_at: "", updated_at: "" },
  { task_id: "nt_000000000000000b", folder: "nervis-tasks/uploader-retry", state: "running", stage: "planning",
    outcome: "", handed_over_at: "2026-09-16T09:00:00Z", started_at: "2026-09-16T09:05:00Z",
    updated_at: "2026-09-16T09:05:00Z" },
  { task_id: "nt_000000000000000c", folder: "nervis-tasks/csv-export", state: "running", stage: "building",
    outcome: "", handed_over_at: "2026-09-16T08:00:00Z", started_at: "2026-09-16T08:01:00Z",
    updated_at: "2026-09-16T08:30:00Z" },
  { task_id: "nt_000000000000000d", folder: "nervis-tasks/dark-mode", state: "running", stage: "paused",
    outcome: "", handed_over_at: "2026-09-15T08:00:00Z", started_at: "2026-09-15T08:01:00Z",
    updated_at: "2026-09-15T09:00:00Z" },
  { task_id: "nt_000000000000000e", folder: "nervis-tasks/timer-app", state: "completed", stage: "building",
    outcome: "built", handed_over_at: "2026-09-14T08:00:00Z", started_at: "2026-09-14T08:01:00Z",
    completed_at: "2026-09-14T12:00:00Z", updated_at: "2026-09-14T12:00:00Z" },
  { task_id: "nt_000000000000000f", folder: "", state: "running", stage: "planning", outcome: "",
    handed_over_at: "", started_at: "2026-09-13T08:01:00Z", updated_at: "2026-09-13T08:01:00Z" },
];

const DIAGNOSTICS = {
  instance_id: WINDOW, label: "coding", endpoint: "http://127.0.0.1:1", live: true, capabilities: {},
  status: { state: "idle" }, agent_run: {}, gate: {}, events: [], event_count: 0,
  tasks: [{ task_id: "nt_000000000000000c", state: "running", stage: "building",
            started_at: "2026-09-16T08:01:00Z", completed_at: "", updated_at: "" }],
};

function answer(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
}

async function drawn({ handovers, windows = [{ instance_id: WINDOW, live: true, label: "coding" }],
                       status = { state: "idle" } }) {
  const fetchImpl = (url) => {
    const address = String(url);
    if (address.endsWith("/api/v1/handovers")) {
      return handovers ? answer(200, { items: handovers }) : Promise.reject(new TypeError("fetch failed"));
    }
    if (address.includes("/api/v1/registry/instances/clarvis/")) return answer(200, { ...DIAGNOSTICS, status });
    if (address.includes("/api/v1/registry/instances")) return answer(200, { items: windows });
    return Promise.reject(new TypeError("fetch failed"));
  };
  const page = loadPage({ fetchImpl });
  vm.runInContext("stopPolling()", page.context);
  await vm.runInContext("clarvisDiagnosticsView()", page.context);
  const content = page.elements.get("sel:#content");
  return content ? content.innerHTML : "";
}

function expect(html, shown, why) {
  if (!html.includes(shown)) failures.push(why);
}

async function everyStage() {
  const html = await drawn({ handovers: ITEMS });
  expect(html, "Handed over to Clarvis", "the handovers card is not drawn.");
  expect(html, "nervis-tasks/pomodoro-timer", "a handover's folder is not shown.");
  expect(html, "nt_000000000000000a", "a handover's id is not shown.");
  expect(html, "waiting to be opened", "a handover Clarvis has not reported is not said as waiting.");
  expect(html, ">being planned<", "a handover being planned is not said so.");
  expect(html, ">being built<", "a handover being built is not said so.");
  expect(html, ">paused between runs<", "a paused handover is not said so.");
  expect(html, ">done · built<", "a built handover is not said so.");
  expect(html, "no handover record", "a task with no handover record is not said as such.");
  expect(html, "running · being built", "a window's own task row does not show its stage.");
  if (!html.includes("Recent events")) failures.push("the window cards did not draw, so the page proves nothing about them.");
}

async function noWindow() {
  const html = await drawn({ handovers: ITEMS, windows: [] });
  expect(html, "No editor window has registered", "the no-window page did not draw.");
  expect(html, "nervis-tasks/csv-export", "handovers are hidden while no window is open.");
}

async function noneAndDown() {
  expect(await drawn({ handovers: [] }), "Nothing handed over", "no handover is not said in words.");
  expect(await drawn({ handovers: null }), "Handovers could not be read", "an unanswered read is not said.");
}

const REPORTED = {
  state: "agent_running", diagnostics_errors: 2, diagnostics_warnings: 5, diagnostics_information: 0,
  diagnostics_hints: 1, diagnostics_files: 3, build_result: "failed", build_finished_at: "2026-09-16T12:00:00Z",
  test_result: "unknown", last_request_id: "f".repeat(32), last_request_model: "ravis/clarvis-agent",
  last_request_provider: "custom", task_id: "nt_000000000000000c", task_stage: "building", event_cursor: 42,
};

async function statusRows() {
  const html = await drawn({ handovers: [], status: REPORTED });
  expect(html, "2 error(s), 5 warning(s), 0 info, 1 hint(s) in 3 file(s)", "problem counts are not shown.");
  expect(html, "<dt>build</dt><dd><span class=\"chip warn\">failed</span>", "a failed build is not flagged.");
  expect(html, "<dt>tests</dt><dd><span class=\"chip\">ended without an exit code</span>", "a test run with no exit code is not said so.");
  expect(html, "ravis/clarvis-agent · custom · in flight", "the last request in flight is not said so.");
  expect(html, "request ffffffffffffffffffffffffffffffff", "the last request's id is not shown.");
  expect(html, "nt_000000000000000c</span> · being built", "the window's task and stage are not shown.");
  expect(html, "42 published", "the event cursor is not shown.");
  const quiet = await drawn({ handovers: [], status: { state: "idle" } });
  for (const label of ["problems", "build", "tests", "last request", "task", "events"]) {
    if (quiet.includes(`<dt>${label}</dt>`)) failures.push(`a window that reported no ${label} still gets a ${label} row.`);
  }
}

async function hostile() {
  const html = await drawn({ handovers: [{ task_id: HOSTILE, folder: HOSTILE, state: "completed", stage: HOSTILE,
                                           outcome: HOSTILE, handed_over_at: HOSTILE, updated_at: HOSTILE }] });
  if (html.includes("<img")) failures.push("something a handover carries became markup.");
  const odd = await drawn({ handovers: [], status: { ...REPORTED, last_request_model: HOSTILE,
    last_request_provider: HOSTILE, last_request_result: HOSTILE, task_stage: HOSTILE, build_finished_at: HOSTILE,
    diagnostics_warnings: HOSTILE, event_cursor: HOSTILE } });
  if (odd.includes("<img")) failures.push("something a status carries became markup.");
}

async function main() {
  await everyStage();
  await noWindow();
  await noneAndDown();
  await statusRows();
  await hostile();
  if (failures.length) {
    console.error("handovers check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log(
    "Handovers hold: every stage in words with folder, id and times; a task with no handover " +
    "record said so; the list drawn with no window open; none and unanswered said in words; " +
    "a window's task rows show their stage; a window's problem counts, build, tests, last request, " +
    "task and event cursor shown only when reported; nothing a handover or a status carries injects"
  );
}

main().catch((error) => { console.error(error); process.exit(1); });
