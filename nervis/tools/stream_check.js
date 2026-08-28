/* The event stream's client, driven through the answers it must survive.
 *
 * §25.2 names three things: `Last-Event-ID` resumption, `retry: 3000`, and the
 * 409 `EVENT_CURSOR_EXPIRED` case. The third is the reason this client is not
 * an `EventSource`: that API never exposes the status of a response it rejects,
 * so a 409 reaches it as an indistinguishable failure and its answer to every
 * failure is to reconnect — an invisible three-second loop against a cursor the
 * server will refuse forever.
 *
 * A 409 is also the one case that cannot be produced by pointing at a running
 * NERVIS without deleting rows out from under it, which is why it is here
 * rather than left to a live check.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

/* One SSE response, from a list of frames. */
function streamOf(frames, { status = 200, body = null } = {}) {
  const chunks = frames.map((f) => Buffer.from(f, "utf8"));
  let at = 0;
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body || {},
    body: {
      getReader: () => ({
        read: async () =>
          at < chunks.length ? { value: chunks[at++], done: false } : { value: undefined, done: true },
      }),
    },
  };
}

const failures = [];

async function drive(fetchImpl, script) {
  const { context } = loadPage({ fetchImpl });
  /* Stop the poll *and* the connection the page opens on load, then zero the
     counters. The page starts streaming at boot, so without this the boot
     connection reads the same canned stream first and every count is doubled —
     which is what this check reported about itself on its first run. */
  vm.runInContext("stopPolling()", context);
  /* A tick before zeroing, because the boot connection is *in flight* rather
     than merely started: `stop()` aborts it, but the mock response has already
     resolved and its first chunk is already being counted. Resetting
     synchronously put the reset before the counting instead of after it. */
  await new Promise((resolve) => setTimeout(resolve, 20));
  vm.runInContext("EVENTS.received=0;EVENTS.gaps=0;EVENTS.cursor='';EVENTS.retryMs=3000", context);
  await vm.runInContext(script, context);
  return context;
}

/* A snapshot, not the object. `EVENTS` comes back by reference, so reading its
   state after calling `stop()` reports "stopped" for every case — which read as
   three client bugs that were three bugs in this file. */
function snapshot(context) {
  const events = vm.runInContext("EVENTS", context);
  const health = vm.runInContext("EVENTS.health()", context);
  return { state: events.state, cursor: events.cursor, received: events.received,
           gaps: events.gaps, retryMs: events.retryMs, detail: events.detail, health };
}

(async () => {
  /* 1 · An ordinary stream: the cursor advances, retry is taken from the wire,
     and a live marker is not counted as an event. */
  {
    const context = await drive(
      async () => streamOf([
        "retry: 4500\n\n",
        "event: ecosystem.stream.live\ndata: {}\n\n",
        'id: 41\nevent: nervis.test\ndata: {"n":1}\n\n',
        'id: 42\nevent: nervis.test\ndata: {"n":2}\n\n',
      ]),
      "(async()=>{EVENTS.state='connecting';await EVENTS._open();EVENTS.stop()})()");
    const events = snapshot(context);
    if (events.cursor !== "42") failures.push(`cursor is ${events.cursor}, expected 42`);
    if (events.received !== 2) failures.push(`received ${events.received}, expected 2`);
    if (events.retryMs !== 4500) failures.push(`retryMs is ${events.retryMs}, expected 4500`);
  }

  /* 2 · A frame with no `id:` must not clear the cursor.
     The server side of this rule had to be fixed too — the gap frame was
     emitting an empty `id:`, which per the SSE spec clears the client's place.
     A client that also cleared it on an absent one would reintroduce the same
     hole from the other end. */
  {
    const context = await drive(
      async () => streamOf([
        'id: 9\nevent: nervis.test\ndata: {"n":1}\n\n',
        'event: nervis.test\ndata: {"n":2}\n\n',
      ]),
      "(async()=>{EVENTS.state='connecting';await EVENTS._open();EVENTS.stop()})()");
    const events = snapshot(context);
    if (events.cursor !== "9") {
      failures.push(`a frame with no id changed the cursor to "${events.cursor}"`);
    }
  }

  /* 3 · The 409. The events between where this tab stopped and what the hub
     still holds are deleted, not delayed — so resuming from the present is the
     only move, and saying so is the whole point of the status existing. */
  {
    const context = await drive(
      async () => streamOf([], { status: 409,
        body: { detail: { code: "EVENT_CURSOR_EXPIRED", oldest_sequence: 900 } } }),
      "(async()=>{EVENTS.cursor='12';EVENTS.state='connecting';await EVENTS._open()})()");
    const events = snapshot(context);
    vm.runInContext("EVENTS.stop()", context);
    if (events.state !== "expired") failures.push(`a 409 left the state "${events.state}"`);
    if (events.cursor !== "") failures.push("a 409 did not clear the expired cursor");
    if (!events.gaps) failures.push("a 409 was not counted as a gap");
    if (!/away longer/.test(events.detail)) {
      failures.push(`a 409 said "${events.detail}", which does not tell a reader what was lost`);
    }
    if (!String(events.detail).includes("900")) {
      failures.push("a 409 dropped the retention floor the server reported");
    }
  }

  /* 4 · A cursor the server will not parse. Distinct from the 409: nothing was
     lost, the cursor was simply wrong, so this must not report a gap. */
  {
    const context = await drive(
      async () => streamOf([], { status: 400,
        body: { detail: { code: "EVENT_CURSOR_INVALID" } } }),
      "(async()=>{EVENTS.cursor='nonsense';EVENTS.state='connecting';await EVENTS._open()})()");
    const events = snapshot(context);
    vm.runInContext("EVENTS.stop()", context);
    if (events.cursor !== "") failures.push("a 400 did not clear the bad cursor");
    if (events.gaps) failures.push("a 400 was counted as a gap — nothing was missed");
  }

  /* 5 · The gap frame: told it fell behind, the client must record a gap and
     stop claiming a healthy stream. */
  {
    const context = await drive(
      async () => streamOf([
        'id: 5\nevent: nervis.test\ndata: {"n":1}\n\n',
        'event: ecosystem.stream.gap\ndata: {"reason":"fell behind"}\n\n',
      ]),
      "(async()=>{EVENTS.state='connecting';await EVENTS._open();EVENTS.stop()})()");
    const events = snapshot(context);
    if (!events.gaps) failures.push("a gap frame was not counted");
    if (events.health === "ok") {
      failures.push("a stream that lost events reported itself healthy");
    }
  }

  /* 6 · A service that is not there must not throw, and must not claim a
     connection. This is the from-disk case, which the whole page is built for. */
  {
    const context = await drive(
      async () => { throw new TypeError("fetch failed"); },
      "(async()=>{EVENTS.state='connecting';await EVENTS._open()})()");
    const events = snapshot(context);
    vm.runInContext("EVENTS.stop()", context);
    if (events.state !== "offline") failures.push(`an absent NERVIS left the state "${events.state}"`);
    if (events.health === "ok") failures.push("an absent NERVIS reported a healthy stream");
  }

  if (failures.length) {
    console.error(`${failures.length} stream client failure(s):\n`);
    for (const failure of failures) console.error(`  • ${failure}`);
    process.exit(1);
  }
  console.log(
    "the stream client resumes, honours the advertised retry, and tells " +
    "an expired cursor from a bad one and from an outage");
})();
