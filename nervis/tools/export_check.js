/* An export refuses rather than write half a conversation — and only for that.
 *
 * Six of the operator's stored conversations were written before the browser kept
 * NERVIS's own id for them. Reopen one and type, and NERVIS has nothing to append
 * to: it starts a fresh record, and the turns from before the reopen stay on
 * screen and out of anything an export reads. The export is faithful — it renders
 * what NERVIS holds — so it wrote the second half and called it the conversation.
 * The 404 recovery in `streamReply` forks a conversation the same way, on purpose.
 *
 * The fix cannot be the browser supplying what is missing: the command endpoint
 * takes *which* conversation, never *what* to write. So the page reads NERVIS's
 * record first and refuses by count. This drives the real `runOffer`, `runPlan`
 * and `sendChat` with every request recorded, because a refusal sentence that
 * exists in the source proves nothing while the export request still goes out
 * beside it.
 *
 * **The count is of messages NERVIS took.** The first version counted every
 * message typed, so one NERVIS turned away — RAVIS down for a moment — made the
 * conversation impossible to export ever again, over a message that was never
 * part of it. The last two cases send one through the page's own composer,
 * refused and then typed again, reopen the browser copy, and require the export
 * to go out; and the same sends on a forked record to still be refused.
 *
 *   node tools/export_check.js
 */

const vm = require("node:vm");
const { loadPage } = require("./page_context.js");

const failures = [];

/* Every command the page asked NERVIS to run, and what NERVIS's record of the
   conversation answers with — `null` for a conversation it no longer holds. */
let runs = [];
let record = null;
/* Whether NERVIS turns the next typed message away, as it does when RAVIS cannot
   take a completion: an error answer before anything is stored. */
let chatRefuses = false;

function answer(status, payload) {
  return Promise.resolve({
    ok: status < 400, status, json: async () => payload,
    text: async () => JSON.stringify(payload),
    headers: { get: () => "application/json" }, body: null,
  });
}

/* Response headers by name, the way `fetch` gives them: absent is null. */
const headers = (named) => ({ get: (name) => named[String(name).toLowerCase()] || null });

/* The two answers a typed message can get. Turned away: an error, no conversation
   id, nothing stored. Taken: the id, and a stream of one frame. */
function chatAnswer() {
  if (chatRefuses) {
    return Promise.resolve({
      ok: false, status: 409, headers: headers({}), body: null,
      json: async () => ({ error: { message: "RAVIS cannot take a completion right now" } }),
    });
  }
  const frame = `data: ${JSON.stringify({ model: "m", choices: [{ delta: { content: "Noted." } }] })}\n\n`;
  let given = false;
  return Promise.resolve({
    ok: true, status: 200,
    headers: headers({ "x-conversation-id": "c_new", "x-request-id": "rq_1" }),
    body: { getReader: () => ({ read: async () => {
      if (given) return { done: true };
      given = true;
      return { value: new TextEncoder().encode(frame), done: false };
    } }) },
  });
}

function fetchImpl(url, options = {}) {
  const target = String(url);
  const method = (options.method || "GET").toUpperCase();
  if (target.includes("/api/v1/commands/run")) {
    runs.push(JSON.parse(options.body || "{}"));
    return answer(200, { file: { name: "conversation.md", detail: "3 turns" } });
  }
  if (target.endsWith("/api/v1/chat") && method === "POST") return chatAnswer();
  if (target.includes("/api/v1/chat/conversations/c_new") && method === "GET") {
    return record === null
      ? answer(404, { error: { message: "no conversation 'c_new'" } })
      : answer(200, { conversation_id: "c_new", items: record });
  }
  return answer(200, {});
}

const { context } = loadPage({ fetchImpl });
vm.runInContext("stopPolling()", context);

const EXPORT = {
  proposal_id: "pr_export", operation: "nervis.conversation.export", service: "nervis",
  target: "conversation.md", summary: "export this conversation to conversation.md",
  ready: true, action: "Export", candidates: [], history: null,
};

/* On screen: a conversation reopened from this browser's copy — two exchanges
   from before the reopen, then the one typed after it. Around them, three rows
   that are not turns: the greeting NERVIS never stores, an attachment as it is
   drawn while the conversation is open, and the same attachment as the browser
   copy saves it, without its `kind`. A check that counted any of those would
   refuse every conversation that has one. */
const SCREEN = [
  { role: "assistant", text: "Evening. What are we doing?" },
  { role: "user", text: "what is a pomodoro" },
  { role: "assistant", text: "Twenty-five minutes of work, then a break." },
  { role: "user", kind: "attachment", file: { name: "notes.pdf", bytes: 1200 } },
  { role: "user" },
  { role: "user", text: "and after four of them?" },
  { role: "assistant", text: "A longer break." },
  { role: "user", text: "export this conversation" },
];

const stored = (role, content) => ({ message_id: `m_${content.length}`, role, content });
/* NERVIS's record after the reopen: only what was said since. */
const FORKED = [stored("user", "export this conversation"), stored("assistant", "Done.")];
/* NERVIS's record of the same conversation, had it never forked. */
const WHOLE = [
  stored("user", "what is a pomodoro"),
  stored("assistant", "Twenty-five minutes of work, then a break."),
  stored("user", "and after four of them?"),
  stored("assistant", "A longer break."),
  ...FORKED,
];
/* Typed, turned away while RAVIS was down, and typed again once it was back —
   so NERVIS's record holds it once, from the second time. */
const RETRIED = "and how long is the long break?";
const RETRY_STORED = [stored("user", RETRIED), stored("assistant", "Noted.")];

/* The screen above plus one reply carrying the offer or plan under test, with
   NERVIS's own id restored — which is what the reopen fix already does, and why
   the id alone cannot tell a whole record from half of one. */
function seed(extra, held) {
  const messages = [...SCREEN, { role: "assistant", text: "ok", ...extra }];
  vm.runInContext(
    `CHAT_SESSION.messages = ${JSON.stringify(messages)}; CHAT_SESSION.remote_id = "c_new"`,
    context,
  );
  runs = [];
  record = held;
  return messages.length - 1;
}

const exportsSent = () => runs.filter((sent) => sent.operation === EXPORT.operation);
const read = (expression) => vm.runInContext(expression, context);

/* One message through the page's own composer and send. */
async function typed(text, refused) {
  chatRefuses = refused;
  read(`document.getElementById('chatInput').value = ${JSON.stringify(text)}`);
  await read("sendChat()");
}

/* The screen above, then a message NERVIS turns away and the same message typed
   again and taken; then the browser copy reopened, so whatever marks the refused
   one has to survive being saved; then Export pressed. */
async function exportAfterARefusal(held) {
  read(`CHAT_SESSION.messages = ${JSON.stringify(SCREEN)}; CHAT_SESSION.remote_id = "c_new"; `
    + "CHAT_SESSION.busy = false");
  runs = [];
  record = held;
  await typed(RETRIED, true);
  await typed(RETRIED, false);
  read("CHAT_SESSIONS.open(CHAT_SESSION.id)");
  read(`CHAT_SESSION.messages.push({role:'assistant',text:'ok',offer:${JSON.stringify(EXPORT)}})`);
  const at = read("CHAT_SESSION.messages.length - 1");
  await read(`runOffer(${at})`);
  return String(read(`CHAT_SESSION.messages[${at}].offer.done`));
}

async function main() {
  /* ── A forked conversation is refused, by count, and nothing is written ── */

  const forked = seed({ offer: EXPORT }, FORKED);
  await read(`runOffer(${forked})`);
  const refused = String(read(`CHAT_SESSION.messages[${forked}].offer.done`));
  if (exportsSent().length) {
    failures.push("a conversation whose first two exchanges NERVIS does not hold was exported "
      + "anyway — the file holds the last exchange and calls itself the conversation.");
  }
  if (!/^nothing was written/.test(refused) || !/holds 1 of the 3 messages/.test(refused)
      || !/missing 2$/.test(refused)) {
    failures.push(`the refusal read ${JSON.stringify(refused)}; it has to say nothing was `
      + "written and name what is missing — NERVIS holds 1 of the 3 messages written here, "
      + "so the file would be missing 2.");
  }

  /* ── A whole conversation still exports ────────────────────────────────── */

  /* The half that keeps the check honest: a guard that refused every export would
     pass everything above. The greeting and both attachment rows are still on
     screen here, and none of them is in the record. */
  const whole = seed({ offer: EXPORT }, WHOLE);
  await read(`runOffer(${whole})`);
  const written = String(read(`CHAT_SESSION.messages[${whole}].offer.done`));
  const sent = exportsSent();
  if (sent.length !== 1 || sent[0].conversation_id !== "c_new") {
    failures.push(`a conversation NERVIS holds in full sent ${sent.length} export(s); it should `
      + "send one, for NERVIS's own id.");
  }
  if (!/^exported conversation\.md/.test(written)) {
    failures.push(`a conversation NERVIS holds in full reported ${JSON.stringify(written)} `
      + "rather than the file — the greeting or an attachment was counted as a turn.");
  }

  /* ── A conversation NERVIS no longer holds says so ─────────────────────── */

  const gone = seed({ offer: EXPORT }, null);
  await read(`runOffer(${gone})`);
  const goneDone = String(read(`CHAT_SESSION.messages[${gone}].offer.done`));
  if (exportsSent().length || !/no longer holds this conversation/.test(goneDone)) {
    failures.push(`a conversation NERVIS no longer holds reported ${JSON.stringify(goneDone)} `
      + `after ${exportsSent().length} export(s); it should send none and say NERVIS does not `
      + "hold it.");
  }

  /* ── A plan's export step is refused the same way ──────────────────────── */

  /* A plan runs its steps through the same command function as a single offer,
     which is why the refusal lives there. A plan that exported half a file would
     be the same bug behind a different button. */
  const planned = seed({ plan: { plan_id: "pl_export", ready: true, steps: [EXPORT] } }, FORKED);
  await read(`runPlan(${planned})`);
  const plan = read(`CHAT_SESSION.messages[${planned}].plan`);
  if (exportsSent().length || plan.halted !== 1 || !/missing 2$/.test(String(plan.reason))) {
    failures.push(`a plan exporting a forked conversation sent ${exportsSent().length} `
      + `export(s) and halted at ${JSON.stringify(plan.halted)} for `
      + `${JSON.stringify(plan.reason)}; it should send none and halt at its first step, `
      + "naming what is missing.");
  }

  /* ── A message NERVIS turned away does not block an export ─────────────── */

  /* Three typed messages NERVIS holds, one it turned away, and the same one typed
     again and taken: four it holds, four it took. Refused here means the refused
     send counted as a lost turn, the retry counted twice, or the mark on the
     refused one did not survive the browser copy being saved and reopened. */
  const retried = await exportAfterARefusal([...WHOLE, ...RETRY_STORED]);
  if (exportsSent().length !== 1 || !/^exported conversation\.md/.test(retried)) {
    failures.push("a conversation with one message NERVIS turned away, typed again and taken, "
      + `reported ${JSON.stringify(retried)} after ${exportsSent().length} export(s); it should `
      + "export — a message that never reached NERVIS is not a turn its record lost.");
  }

  /* ── …and does not hide a fork ─────────────────────────────────────────── */

  const forkedRetry = await exportAfterARefusal([...FORKED, ...RETRY_STORED]);
  if (exportsSent().length || !/holds 2 of the 4 messages/.test(forkedRetry)
      || !/missing 2$/.test(forkedRetry)) {
    failures.push(`the same sends on a forked record reported ${JSON.stringify(forkedRetry)} `
      + `after ${exportsSent().length} export(s); it should still refuse — NERVIS holds 2 of `
      + "the 4 messages it took, so the file would be missing 2.");
  }

  if (failures.length) {
    console.error("export check failed:\n");
    for (const line of failures) console.error("  - " + line + "\n");
    process.exit(1);
  }
  console.log("exports hold: a conversation NERVIS holds only part of is refused by count with "
    + "nothing written, a whole one still exports with a greeting and attachments on screen, one "
    + "NERVIS no longer holds says so, a plan's export step is refused the same way, and a message "
    + "NERVIS turned away and typed again still exports, counted once, while the same sends on a "
    + "forked record are still refused");
}

main().catch((failure) => {
  console.error(failure);
  process.exit(1);
});
