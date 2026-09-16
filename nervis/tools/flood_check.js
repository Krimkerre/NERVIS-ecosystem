/* The flood guard is said wherever events are listed (NERVIS 0.31.0).
 *
 * On 14 September 2026 a RAVIS loop sent about a hundred events a second for eight minutes,
 * filled NERVIS's event hub and pushed out ten days of history. NERVIS now holds back what one
 * service sends too fast, and the owner decided on 15 September that the dashboard says when the
 * guard kicked in. This drives the real Events screen and the Overview against a recorded
 * `GET /api/v1/events` answer, and asserts:
 *
 *   1. **A guard that is on is said on both screens**: which service, its id, since when and how
 *      many events it has held back so far, and that the last event of each kind is still kept.
 *   2. **A guard that ended in the last day is said with its count and between when**, and one
 *      NERVIS stopped during is said too rather than left out — but a guard is never said twice.
 *   3. **A stored event that arrived more than once shows its count** on both lists.
 *   4. **Nothing a service names can inject markup** into those lines.
 *   5. **With no guard in the answer, neither screen mentions one** — an older NERVIS, or a hub
 *      that has never needed it.
 *   6. **Refused senders are said on both screens too** (NERVIS 0.34.18): a known service's
 *      refused batches with their count and times and what to do, a stranger's never, and
 *      nothing when none were refused.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];

const RAVIS_ID = "ravis-6b773addc83d";
const SIRVIS_ID = "sirvis-0bb99b67716c";
const ON_SINCE = "2026-09-14T21:32:19.700Z";
const ENDED = { since: "2026-09-14T20:00:00.000Z", until: "2026-09-14T20:10:00.000Z" };
const STOPPED_AT = "2026-09-14T19:00:00.000Z";
const HOSTILE = '<img src=x onerror="alert(1)">';

/* `/api/v1/events` as NERVIS 0.31.0 answers it. `recent` is newest first. */
function eventsAnswer(overrides = {}) {
  return {
    latest_sequence: 7,
    next_cursor: 7,
    items: [
      { event_type: "sirvis.recommendation.created", occurred_at: "2026-09-14T20:05:00.000Z",
        severity: "info", source: { service_type: "sirvis", service_id: SIRVIS_ID },
        data: { records: 9 }, _sequence: 5, _received_at: "2026-09-14T20:05:00.100Z" },
      { event_type: "ravis.codex.state_changed", occurred_at: "2026-09-14T21:32:19.654Z",
        severity: "info", source: { service_type: "ravis", service_id: RAVIS_ID },
        data: { from: "signed_in", to: "runtime_down" }, _sequence: 7,
        _received_at: ON_SINCE, _repeats: 24978, _last_received_at: "2026-09-14T21:39:17.002Z" },
    ],
    guard: {
      on: true,
      limits: { burst: 120, per_minute: 12, daily_rows: 2500, collapse_seconds: 60 },
      active: [{ service_type: "ravis", service_id: RAVIS_ID, reason: "rate", since: ON_SINCE,
                 held_back: 49850, types: { "ravis.codex.state_changed": 49850 } }],
      recent: [
        guardEvent({ phase: "engaged", service_type: "ravis", service_id: RAVIS_ID, since: ON_SINCE }),
        guardEvent({ phase: "released", service_type: "sirvis", service_id: SIRVIS_ID, ...ENDED,
                     held_back: 1234 }),
        guardEvent({ phase: "engaged", service_type: "sirvis", service_id: SIRVIS_ID,
                     since: ENDED.since }),
        guardEvent({ phase: "engaged", service_type: "clarvis", service_id: "5b0f5aa7",
                     since: STOPPED_AT }),
      ],
    },
    ...overrides,
  };
}

function guardEvent(data) {
  return { event_type: "nervis.events.flood_guarded", source: { service_type: "nervis" },
           severity: data.phase === "engaged" ? "warning" : "info", data };
}

function answer(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status,
                           headers: { get: () => null }, json: async () => body });
}

/* A NERVIS whose event read answers `body` and whose every other read is refused. */
function world(body) {
  const fetchImpl = (url) => {
    const address = String(url);
    if (address.includes("/api/v1/events/quarantine")) return answer(200, { items: [] });
    if (address.includes("/api/v1/events/stream")) return answer(503, {});
    if (address.includes("/api/v1/events")) return answer(200, body);
    return answer(503, {});
  };
  return loadPage({ fetchImpl });
}

const run = (page, code) => vm.runInContext(code, page.context);
/* The page draws the Overview as it loads; the screen under test is drawn once that has landed. */
const quiet = () => new Promise((done) => setTimeout(done, 250));
/* The words as a browser shows them: the page's template wraps long sentences across lines,
   and a browser collapses that whitespace to one space, so this does too. */
const readable = (html) =>
  html.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/\s+/g, " ");

/* One card of a screen, from its heading to the next card. */
async function card(body, view, heading) {
  const page = world(body);
  await quiet();
  page.exported.state.app = "nervis";
  page.exported.state.view = view;
  await run(page, "nervis()");
  const content = page.elements.get("sel:#content");
  const html = content ? content.innerHTML : "";
  const found = html.split(heading)[1];
  return found == null ? null : found.split('<div class="card ')[0];
}

function expect(label, text, present, absent = []) {
  if (text == null) {
    failures.push(`${label}: the card was not drawn`);
    return;
  }
  for (const words of present) {
    if (!text.includes(words)) failures.push(`${label}: missing ${JSON.stringify(words)}`);
  }
  for (const words of absent) {
    if (text.includes(words)) failures.push(`${label}: should not say ${JSON.stringify(words)}`);
  }
}

const when = (stamp) => new Date(stamp).toLocaleString();
const SCREENS = [
  ["Events screen", "Events", "<h3>Feed"],
  ["Overview", "Overview", "<h3>Recent events</h3>"],
];

async function theGuardIsSaidOnBothScreens() {
  for (const [label, view, heading] of SCREENS) {
    const html = await card(eventsAnswer(), view, heading);
    const text = html == null ? null : readable(html);
    expect(`${label}, a guard that is on`, text, [
      "The flood guard is holding back events from <b>RAVIS</b> (ravis-6b773addc83d)",
      `Since ${when(ON_SINCE)}`, "<b>49,850</b> so far", "The last event of each kind is still kept",
    ]);
    expect(`${label}, a guard that ended`, text, [
      "The flood guard held back <b>1,234</b> events from <b>SIRVIS</b> (sirvis-0bb99b67716c)",
      when(ENDED.since), when(ENDED.until),
    ]);
    expect(`${label}, a guard NERVIS stopped during`, text, [
      "started holding back events from <b>Clarvis</b> (5b0f5aa7)", when(STOPPED_AT),
      "NERVIS stopped before it could count them",
    ]);
    const unfinished = text == null ? 0 : text.split("started holding back").length - 1;
    if (unfinished !== 1) {
      failures.push(`${label}: ${unfinished} guards said as stopped-during; only Clarvis's was`);
    }
  }
}

async function aRepeatedEventShowsItsCount() {
  const feed = await card(eventsAnswer(), "Events", "<h3>Feed");
  expect("Events screen, a collapsed row", feed, ["×24,978 · last 21:39:17 · "]);
  const overview = await card(eventsAnswer(), "Overview", "<h3>Recent events</h3>");
  expect("Overview, a collapsed row", overview, ["×24,978"]);
}

async function nothingAServiceNamesInjects() {
  const hostile = eventsAnswer();
  hostile.guard.active = [{ ...hostile.guard.active[0], service_type: HOSTILE, service_id: HOSTILE,
                            held_back: HOSTILE, since: HOSTILE }];
  hostile.guard.recent = [guardEvent({ phase: "released", service_type: HOSTILE,
                                       service_id: HOSTILE, since: HOSTILE, until: HOSTILE,
                                       held_back: HOSTILE })];
  for (const [label, view, heading] of SCREENS) {
    const html = await card(hostile, view, heading);
    expect(`${label}, hostile service names`, html, ["&lt;img"], ["<img"]);
  }
}

async function noGuardSaysNothing() {
  for (const [label, view, heading] of SCREENS) {
    const bare = eventsAnswer({ guard: undefined });
    delete bare.guard;
    expect(`${label}, no guard in the answer`, await card(bare, view, heading), [],
           ["flood guard"]);
    const quietHub = eventsAnswer({ guard: { on: true, limits: null, active: [], recent: [] } });
    expect(`${label}, a guard that never kicked in`, await card(quietHub, view, heading), [],
           ["flood guard"]);
  }
}

async function refusedSendersAreSaid() {
  const refused = eventsAnswer({ refused_senders: {
    ravis: { batches: 1234, since: ENDED.since, last: ENDED.until },
    clarvis: { batches: 2, since: ENDED.since, last: ENDED.since },
    mallory: { batches: 9, since: ENDED.since, last: ENDED.since },
    sirvis: { batches: HOSTILE, since: HOSTILE, last: HOSTILE },
  } });
  for (const [label, view, heading] of SCREENS) {
    const html = await card(refused, view, heading);
    const text = html == null ? null : readable(html);
    expect(`${label}, refused senders`, text, [
      "NERVIS is refusing events that claim to come from RAVIS", "<b>1,234</b> batch(es) since",
      when(ENDED.since), when(ENDED.until), "start it with the launcher",
      "claim to come from Clarvis", "reloading the window registers it again",
      "claim to come from SIRVIS", "&lt;img",
    ], ["mallory", "<img", "undefined"]);
    const lines = text == null ? 0 : text.split("NERVIS is refusing events").length - 1;
    if (lines !== 3) failures.push(`${label}: ${lines} refused-sender lines; RAVIS, SIRVIS and Clarvis only`);
    expect(`${label}, nothing refused`, await card(eventsAnswer({ refused_senders: {} }), view, heading),
           [], ["refusing events"]);
  }
}

async function main() {
  await theGuardIsSaidOnBothScreens();
  await refusedSendersAreSaid();
  await aRepeatedEventShowsItsCount();
  await nothingAServiceNamesInjects();
  await noGuardSaysNothing();
  if (failures.length) {
    console.error(`${failures.length} flood guard check(s) failed:\n`);
    for (const failure of failures) console.error(`  • ${failure}`);
    process.exit(1);
  }
  console.log("the flood guard is said on the Events screen and the Overview, "
    + "with counts, times and services, and nothing a service names injects markup; "
    + "refused senders are said the same way, a stranger never");
  process.exit(0);
}

main().catch((error) => { console.error(error); process.exit(1); });
