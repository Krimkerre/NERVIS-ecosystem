/* What the chat bubble does with a picture link, and what it refuses to do.
 *
 * A model that answers with an image sends a megabyte of base64 in a stream
 * frame. NERVIS writes that into the workspace and puts a markdown link to it
 * in the reply, because the reply is the only thing a conversation stores and
 * the only thing a reload has to rebuild from. So the renderer has to turn
 * that one line into the picture — while `renderMarkdown`'s standing rule is
 * "no links and no images: a chat reply is not a place to introduce a
 * clickable destination the model composed".
 *
 * Both halves are checked here, and the second half is the point: a link to
 * anywhere but NERVIS's own documents endpoint must come out as the text it
 * is. A renderer that quietly widened would hand any model an `<img src>` to
 * an address of its choosing, which is a request to a third party made from
 * the operator's browser — and nothing else in this repository would notice.
 *
 *   node tools/picture_check.js
 */

const { loadPage } = require("./page_context.js");

const { exported } = loadPage();
const render = exported.renderMarkdown;

const SAVED = "![image-20260907-143012-1.png](/api/v1/documents/export/image-20260907-143012-1.png)";

/* Every one of these is a link a model could write. None may become an image,
   and none may become a clickable destination. */
const REFUSED = [
  ["another host", "![x](https://example.invalid/pixel.png)"],
  ["a protocol-relative host", "![x](//example.invalid/pixel.png)"],
  ["a path outside the endpoint", "![x](/api/v1/workspace/files/a.png)"],
  ["a traversal in the name", "![x](/api/v1/documents/../../etc/passwd.png)"],
  /* One room, from the set the workspace actually has. A picture link is the
     one thing this page renders straight into an <img>, so the path it accepts
     stays a closed list rather than "a path". */
  ["a room the workspace does not have", "![x](/api/v1/documents/elsewhere/a.png)"],
  ["two rooms deep", "![x](/api/v1/documents/export/nested/a.png)"],
  ["a query string", "![x](/api/v1/documents/a.png?to=example.invalid)"],
  ["a suffix that is not an image", "![x](/api/v1/documents/report.pdf)"],
  ["a javascript destination", "![x](javascript:alert(1))"],
  ["an ordinary markdown link", "[click me](/api/v1/documents/a.png)"],
];

const failures = [];

const shown = render(SAVED);
if (!/<img src="\/api\/v1\/documents\/export\/image-20260907-143012-1\.png"/.test(shown)) {
  failures.push(`the saved picture did not render as an image: ${shown}`);
}
if (!/<a href="\/api\/v1\/documents\/export\/image-20260907-143012-1\.png" download="image-20260907-143012-1\.png"/.test(shown)) {
  failures.push(`the saved picture had no download link: ${shown}`);
}

for (const [what, source] of REFUSED) {
  const out = render(source);
  if (/<img|<a /.test(out)) failures.push(`${what} became markup: ${out}`);
}

/* The falsifier for the falsifier: a check that never sees an image would pass
   the loop above no matter how the renderer changed. */
if (!/<img/.test(shown)) failures.push("nothing rendered an image, so the refusals prove nothing");

if (failures.length) {
  for (const line of failures) console.error(`  ${line}`);
  console.error(`${failures.length} picture-rendering failure(s)`);
  process.exit(1);
}
console.log(`a saved picture renders; ${REFUSED.length} other destinations stay text`);
