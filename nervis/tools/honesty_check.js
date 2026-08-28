/* Does a card's badge match where its data actually came from?
 *
 * The defect this exists for was found eight times in one day, every time by a
 * person looking at the screen: a card rendering real data from a running
 * service while wearing a PROTOTYPE badge. Providers had seven. Sessions
 * shipped with six. "Evidence in the ranking" showed thirty-two live rows from
 * SIRVIS's own benchmark runs, faded out. Not one was caught by a check.
 *
 * `render_check` proves a screen assembles. `liveness_check` proves a card is
 * *able* to express liveness. Neither can tell whether the answer is right,
 * because both run with nothing up — and a card that hardcodes `live` looks
 * identical to one that earned it.
 *
 * **So render everything twice.** Once with fetch rejecting, which is the
 * from-disk state, and once against whatever is actually running. A card whose
 * body changes between the two runs was fed by a service. If it is not badged
 * live in the second run, it is understating — the eight. If it is badged live
 * in the *first* run, it is overstating: it claims a live read with nothing to
 * read from.
 *
 * Understating is the one that wastes a person's afternoon. Overstating is the
 * one that makes them trust a mock. Both are reported.
 *
 * This needs services up to say anything, so it is a check you run, not a gate
 * that blocks a commit: with nothing running every card is identical between
 * the two passes and it reports exactly that.
 *
 * **Findings are leads, not verdicts.** Two shapes report as understating and
 * are correct as they stand, so read the card before changing it:
 *
 *   • *Mixed provenance.* Discover's catalogue is a stored transcription that
 *     carries one live figure — free disk, which decides whether a row can be
 *     downloaded. Badging the whole card live would claim the catalogue is a
 *     live read of what exists to download, which it is not.
 *
 *   • *A read that timed out under this check's own load.* `LIVE_TIMEOUT_MS`
 *     is 1500, and this renders seventy screens against three services in a
 *     few seconds — far heavier than a person on one screen at a time. RAVIS's
 *     Providers card reports here on a busy machine, and rendering that one
 *     screen on its own emits `card full live`, correctly. Findings must
 *     survive a second lit pass, which removes the ones that move run to run
 *     but not one whose read is starved for the whole run.
 *
 * A card reporting the *same* body in both passes is invisible to this check
 * either way — if a mock happens to match live output exactly, nothing here
 * will notice.
 */
const path = require("node:path");
const { loadPage } = require("./page_context.js");

const PORTS = { nervis: 8790, ravis: 8731, sirvis: 8721 };

/* Cards, split where the next one starts. The trailing segment of each is its
   body; an exact DOM would be better and would need a DOM. */
function cardsOf(html) {
  const found = new Map();
  const parts = String(html || "").split('<div class="card ');
  for (const part of parts.slice(1)) {
    const cls = part.slice(0, part.indexOf('"'));
    const h3 = part.indexOf("<h3>");
    if (h3 === -1) continue;
    const title = part.slice(h3 + 4, part.indexOf("</h3>", h3)).replace(/<[^>]*>/g, "").trim();
    /* Digits and identifiers only. Whitespace and markup shift for reasons that
       are not "a service answered", and a check that cries wolf gets ignored. */
    const body = part.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    /* **Keyed by a title with its values stripped, not the raw title.**
       A heading like "Machine mc_7a1f" or "ravis/cheap unavailable" carries the
       data in it, so the same card had two different keys in the two passes and
       was silently never compared. Runs of digits and hex become `#`, which
       leaves the wording that identifies the card and removes the reading that
       distinguishes the run. */
    const key = (title || "(untitled)")
      .replace(/\b[0-9a-f]{6,}\b/gi, "#")
      .replace(/\d+([.,]\d+)?/g, "#");
    found.set(key, { live: /\blive\b/.test(cls), body, title: title || "(untitled)" });
  }
  return found;
}

async function renderAll(fetchImpl) {
  const { context, exported } = loadPage({ fetchImpl });
  const renderers = { nervis: exported.nervis, ravis: exported.ravis,
                      sirvis: exported.sirvis, clarvis: exported.clarvis };
  const screens = new Map();
  for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
    const render = renderers[app];
    if (typeof render !== "function") continue;
    for (const view of config.nav) {
      exported.state.app = app;
      exported.state.view = view;
      try {
        await Promise.race([render(),
          new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 8000))]);
      } catch { /* render_check owns render failures; this one owns badges */ }
      const el = context.__elements.get("sel:#content");
      screens.set(`${app}/${view}`, cardsOf(el && el.innerHTML));
    }
  }
  await new Promise((r) => setTimeout(r, 50));
  if (exported.stopPolling) exported.stopPolling();
  return screens;
}

(async () => {
  const up = [];
  for (const [name, port] of Object.entries(PORTS)) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/ecosystem/health`,
        { signal: AbortSignal.timeout(5000) });
      if (r.ok) up.push(name);
    } catch { /* not running */ }
  }
  if (!up.length) {
    console.log("no service is running — nothing to compare against.");
    console.log("start the ecosystem and run this again; with everything down");
    console.log("both passes are identical and this can prove nothing.");
    return;
  }

  const dark = await renderAll();
  /* NERVIS's own base is the empty string — the page is served by NERVIS, so
     its reads are relative and the browser resolves them against the origin.
     `fetch` in node will not, and rejects a relative URL, which sent every
     NERVIS read down its fallback path and made this check report NERVIS's own
     cards as understating. Resolved against the same origin the shim claims. */
  const ORIGIN = "http://127.0.0.1:8790";
  const liveFetch = (url, opts) => fetch(new URL(url, ORIGIN), opts);
  const lit = await renderAll(liveFetch);

  /* **Read something, or say you read nothing.** The first version of this
     file captured no markup at all — the page writes through `querySelector`
     and the shim handed back a fresh stub every call — and it reported that
     every badge on every screen was correct. Empty input must never be
     indistinguishable from a clean result; that is the same mistake as a card
     reading an empty list as an absent service. */
  const seen = [...lit.values()].reduce((n, cards) => n + cards.size, 0);
  if (!seen) {
    console.error("no card markup was captured from any screen.");
    console.error("that is a fault in this checker, not a clean result.");
    process.exit(1);
  }

  /* Findings, then the same question asked a second time.

     This check renders seventy screens against three services in a few seconds,
     which is far heavier than a person clicking through one screen at a time.
     `LIVE_TIMEOUT_MS` is 1500, so a read that answers in one pass can time out
     in the next — and a card fed by that read then differs between passes for a
     reason that is not a defect. Three cards moved in and out of the list
     across consecutive runs on this machine.

     So a finding has to survive twice. A tool that reports two or three
     phantoms every run teaches people to skim it, which costs more than the
     findings are worth. */
  /* **A card with no counterpart in the dark pass is a finding, not a skip.**
     `then && …` short-circuited, so two whole classes fell through: a screen
     that collapses to a single "not answering" card when nothing is up, whose
     real cards therefore exist only in the lit pass, and any card whose heading
     is built from live data. Those are exactly the screens most worth checking,
     and the check was quietest about them. */
  const suspect = (litRun) => {
    const found = [];
    for (const [screen, cards] of litRun) {
      const before = dark.get(screen) || new Map();
      for (const [key, now] of cards) {
        const then = before.get(key);
        const changed = then ? then.body !== now.body : true;
        if (changed && !now.live) found.push(`${screen} :: ${now.title}`);
      }
    }
    return found;
  };
  const first = suspect(lit);
  const confirmed = first.length ? new Set(suspect(await renderAll(liveFetch))) : new Set();
  const understating = first.filter((f) => confirmed.has(f));
  const transient = first.filter((f) => !confirmed.has(f));
  const overstating = [];
  for (const [screen, cards] of dark) {
    for (const [title, card] of cards) if (card.live) overstating.push(`${screen} :: ${title}`);
  }

  console.log(`services up: ${up.join(", ")}`);
  if (understating.length) {
    console.log(`\n${understating.length} card(s) render service data under a PROTOTYPE badge:`);
    for (const c of understating) console.log(`  • ${c}`);
  }
  if (transient.length) {
    console.log(`\n${transient.length} card(s) appeared in one lit pass only. Two causes, both`);
    console.log("usually fine: a read starved by this check's own load, or a card on a");
    console.log("screen that returns early when nothing answers, so it has no counterpart");
    console.log("in the dark pass and is static anyway. Read before changing:");
    for (const c of transient) console.log(`  • ${c}`);
  }
  if (overstating.length) {
    console.log(`\n${overstating.length} card(s) claim a live read with nothing running:`);
    for (const c of overstating) console.log(`  • ${c}`);
  }
  if (!understating.length && !overstating.length) {
    console.log("\nevery card's badge matches where its data came from.");
  }
  /* Reported, not enforced. Which services happen to be up changes the answer,
     so failing the build on it would make the build depend on the machine. */
})();
