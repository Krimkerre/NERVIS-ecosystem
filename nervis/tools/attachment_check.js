/* An attachment is only reachable if both sides file it under the same id.
 *
 * This gate exists because that failed twice, silently and identically. Uploads
 * went to `?conversation_id=<CHAT_SESSION.id>` and landed correctly on disk;
 * every question about them was answered "nothing is attached", because the
 * chat request carried a different id — first none at all, then the right field
 * added to the unprompted-remark path rather than to the one a typed message
 * takes. Both times the server was verified in isolation and passed, both times
 * the browser was verified in isolation and passed, and the pair was broken.
 *
 * So the assertion is about the *pair*, which is the only place the bug lives:
 * the value the upload files under, the value the listing reads back, and the
 * value a question carries must all be one expression. Nothing here checks that
 * the id is any particular thing — only that the three agree.
 *
 * It runs the real `chatRequestBody` rather than reading the source for a
 * substring, because "the field appears in the file" is exactly the check that
 * passed while the field sat on a code path no message goes down.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const failures = [];
const { context } = loadPage();
const source = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
vm.runInContext("stopPolling()", context);

/* ── The id a question carries ─────────────────────────────────────────── */

const carried = vm.runInContext(
  "chatRequestBody('some-server-id').attachment_id", context
);
const session = vm.runInContext("CHAT_SESSION.id", context);

if (!carried) {
  failures.push(
    "chatRequestBody() sends no attachment_id, so every question about an " +
    "attached file is answered as though nothing were attached."
  );
} else if (carried !== session) {
  failures.push(
    `chatRequestBody() sends attachment_id ${JSON.stringify(carried)} but the ` +
    `conversation is ${JSON.stringify(session)}; a file attached to one is ` +
    "invisible to the other."
  );
}

/* ── The id the upload uses ────────────────────────────────────────────── */

/* Read off the source, because it lives inside an event handler that would need
 * a live file picker to reach. What matters is that it is the same expression
 * `chatRequestBody` sends.
 *
 * There was a second leg here checking the strip under the composer, which
 * listed the conversation's files. The strip is gone — an attachment is drawn
 * in the transcript now, from what the upload returned, so there is no second
 * reader to disagree with. The pattern outlived it and silently matched the
 * *delete* call instead, which correctly uses a different variable; a gate that
 * keeps matching after its subject is deleted is worse than no gate. */
const uses = (label, pattern) => {
  const found = source.match(pattern);
  if (!found) {
    failures.push(`${label}: could not find the conversation id it sends.`);
    return null;
  }
  return found[1];
};

const uploadId = uses(
  "the upload",
  /workspace\/files\/'\+encodeURIComponent\(file\.name\)[\s\S]{0,120}?conversation_id='\+encodeURIComponent\(([^)]+)\)/
);

/* And the pair against the question. `chatRequestBody` reads `c.id`, which is
 * `CHAT_SESSION.id` under another name, so compare what they resolve to. */
for (const [label, expression] of [["upload", uploadId]]) {
  if (!expression) continue;
  let resolved;
  try {
    resolved = vm.runInContext(expression, context);
  } catch (error) {
    failures.push(`the ${label} id ${expression.trim()} does not evaluate: ${error.message}`);
    continue;
  }
  if (resolved !== carried) {
    failures.push(
      `the ${label} uses ${expression.trim()} (${JSON.stringify(resolved)}) but a ` +
      `question carries ${JSON.stringify(carried)} — the file is stored where ` +
      "nothing will look for it."
    );
  }
}

/* ── The card an attachment is drawn as ────────────────────────────────── */

/* **Called, not read for.** `attachmentCard` reaches for `fileSize`, and
 * `fileSize` was deleted along with the strip it used to belong to — a
 * ReferenceError that `node --check` cannot see, that every other gate passed,
 * and that took out the whole message list: the render threw, so the transcript
 * went blank and the file picker along with it. Attaching a file was impossible
 * and nothing said why.
 *
 * Calling it with a plausible message is the only check that finds that. */
for (const [label, sample] of [
  ["a readable file", { role: "user", kind: "attachment", at: "2026-01-01T00:00:00Z",
                        file: { name: "notes.md", bytes: 4096, readable: true } }],
  ["one chat cannot read", { role: "user", kind: "attachment",
                             file: { name: "shot.png", bytes: 12, readable: false } }],
  ["a file with nothing known about it", { role: "user", kind: "attachment" }],
]) {
  let drawn;
  try {
    drawn = vm.runInContext("attachmentCard", context)(sample);
  } catch (error) {
    failures.push(`attachmentCard threw on ${label}: ${error.message}. The whole `
      + "message list is one template — a throw here blanks the transcript and "
      + "takes the file picker with it.");
    continue;
  }
  if (!drawn || !String(drawn).includes("attachment")) {
    failures.push(`attachmentCard drew nothing usable for ${label}.`);
  }
  if (sample.file && !String(drawn).includes(sample.file.name)) {
    failures.push(`attachmentCard drew ${label} without naming the file.`);
  }
  if (sample.file && sample.file.readable === false
      && !String(drawn).includes("not readable")) {
    failures.push(
      "a file chat cannot read must say so on the card: announcing it without "
      + "that is an invitation to a refusal."
    );
  }
}

if (failures.length) {
  console.error("Attachments are filed under one id and read under another:\n");
  for (const failure of failures) console.error("  • " + failure);
  process.exit(1);
}
console.log("the upload and the question agree on the conversation");
