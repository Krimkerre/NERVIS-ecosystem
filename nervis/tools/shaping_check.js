/* Freeze what the live readers produce, so refactoring them cannot change it.
 *
 * `render_check.js` makes every `fetch` reject, so it proves each screen
 * assembles with nothing running — and exercises the *mock* path of every
 * reader, never the live one. That leaves the shaping code with no coverage at
 * all, which was fine while it was small and was not: `API.sirvis.runtimeSet`
 * reached cyclomatic complexity 30, almost entirely optional-field handling,
 * and not one of those branches had ever been executed by a check.
 *
 * This runs the readers against recorded payloads and compares the result to a
 * golden file. It is a characterisation test: it does not assert the output is
 * *right*, it asserts the output is *unchanged*. That is exactly what a
 * complexity refactor needs and it is worth being clear it is nothing more —
 * a bug frozen into the golden file stays frozen, and only a person reading a
 * diff will catch it.
 *
 *   node tools/shaping_check.js            # compare against the golden file
 *   node tools/shaping_check.js --update   # rewrite it, then read the diff
 */

const fs = require("node:fs");
const path = require("node:path");

const { loadPage } = require("./page_context.js");
const { CASES, CHAT_STREAMS, BUILDS } = require("./shaping_fixtures.js");

const GOLDEN = path.join(__dirname, "shaping_golden.json");

/* Route a fixture's two payloads by URL. The reader asks for runtime sets and
   benchmark runs; anything else it might reach for is absent, which is the
   honest answer for a fixture that does not carry it. */
function fetchFor(sets, runs) {
  return (url) => {
    const body = url.includes("/runtime-sets") ? sets
      : url.includes("/benchmark-runs") ? runs
      : null;
    if (body === null) return Promise.reject(new TypeError("fetch failed"));
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  };
}

async function shapeAll() {
  const shaped = {};
  for (const testCase of CASES) {
    /* A fresh page per case. The readers write to a module-level `SOURCE` to
       record whether an answer was live, so a shared context would let one
       case's verdict leak into the next one's. */
    const { exported } = loadPage({ fetchImpl: fetchFor(testCase.sets, testCase.runs) });
    shaped[testCase.name] = {
      runtimeSet: await exported.API.sirvis.runtimeSet(),
      source: exported.SOURCE.sirvis,
    };
    exported.stopPolling?.();
  }
  return shaped;
}

/* A path-by-path diff rather than a string compare, because the failure needs
   to name the field that moved. "the JSON differs" sends somebody to read two
   thousand lines. */
function differences(before, after, trail = "") {
  if (Object.is(before, after)) return [];
  const bothObjects = before && after && typeof before === "object" && typeof after === "object";
  if (!bothObjects) return [`${trail || "<root>"}: ${JSON.stringify(before)} → ${JSON.stringify(after)}`];
  const found = [];
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    found.push(...differences(before[key], after[key], trail ? `${trail}.${key}` : key));
  }
  return found;
}

/* Replay a recorded stream through the frame handler and settle it into the
   message the transcript would show. No model, no network, no service. */
function replayChat(exported) {
  const replayed = {};
  for (const stream of CHAT_STREAMS) {
    const acc = { reply: "", requestId: "req_1", served: "", conversationId: "",
                  reasoning: 0, failed: "", aborted: false };
    for (const item of stream.frames) exported.absorbFrame(item, acc, null);
    replayed[stream.name] = { acc, message: exported.replyMessage(acc) };
  }
  /* And the two ways a stream ends without frames at all. */
  const stopped = { reply: "half a sen", requestId: "", served: "", conversationId: "",
                    reasoning: 0, ...exported.transportFailure({ name: "AbortError" }) };
  const dropped = { reply: "", requestId: "", served: "", conversationId: "", reasoning: 0,
                    ...exported.transportFailure({ name: "TypeError", message: "load failed" }) };
  replayed["stopped by the reader"] = { acc: stopped, message: exported.replyMessage(stopped) };
  replayed["connection dropped"] = { acc: dropped, message: exported.replyMessage(dropped) };
  return replayed;
}

async function main() {
  const shaped = await shapeAll();
  const { exported } = loadPage();
  shaped["chat streams"] = replayChat(exported);
  shaped["build panels"] = Object.fromEntries(BUILDS.map(
    (b) => [b.name, exported.BUILD.render(b.model, b.evidence, b.residency)]));

  if (process.argv.includes("--update") || !fs.existsSync(GOLDEN)) {
    fs.writeFileSync(GOLDEN, JSON.stringify(shaped, null, 2) + "\n");
    console.log(`wrote ${CASES.length} shaped cases to ${path.basename(GOLDEN)} — read the diff`);
    return;
  }

  const golden = JSON.parse(fs.readFileSync(GOLDEN, "utf8"));
  const moved = differences(golden, shaped);
  if (moved.length) {
    console.error(`${moved.length} shaped value(s) changed:\n`);
    for (const line of moved.slice(0, 40)) console.error(`  • ${line}`);
    if (moved.length > 40) console.error(`  … and ${moved.length - 40} more`);
    console.error(
      "\nIf the change is intended, run with --update and commit the golden file" +
      "\nso the diff is reviewable. If it is not, a refactor changed behaviour."
    );
    process.exit(1);
  }
  console.log(`all ${CASES.length} shaped cases match the golden file`);
}

main().catch((failure) => {
  console.error(`the shaping check itself failed: ${failure.stack || failure}`);
  process.exit(1);
});
