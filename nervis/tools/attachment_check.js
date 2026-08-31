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

/* ── The id the upload and the listing use ─────────────────────────────── */

/* Read off the source, because both are inside event handlers that would need a
 * live picker and a live fetch to reach. What matters is that the expression is
 * the same one on both, and that it is the same one `chatRequestBody` uses. */
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
const listId = uses(
  "the listing",
  /workspace\/files\?conversation_id='\s*\+encodeURIComponent\(([^)]+)\)/
);

const normalise = (expression) => (expression || "").replace(/\s|\|\|''/g, "");
if (uploadId && listId && normalise(uploadId) !== normalise(listId)) {
  failures.push(
    `the upload files under ${uploadId.trim()} and the listing reads back ` +
    `${listId.trim()}; the strip under the composer would show a different ` +
    "set of files than the one that exists."
  );
}

/* And the pair against the question. `chatRequestBody` reads `c.id`, which is
 * `CHAT_SESSION.id` under another name, so compare what they resolve to. */
for (const [label, expression] of [["upload", uploadId], ["listing", listId]]) {
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

if (failures.length) {
  console.error("Attachments are filed under one id and read under another:\n");
  for (const failure of failures) console.error("  • " + failure);
  process.exit(1);
}
console.log("the upload, the listing and the question agree on the conversation");
