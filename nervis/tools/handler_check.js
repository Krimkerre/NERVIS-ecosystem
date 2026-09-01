/* Every function an `onclick` names must actually exist.
 *
 * The dashboard wires its controls through inline handlers — `onclick="runOffer(2)"`
 * — and nothing checks that the name on the left is a function that is still
 * there. Not the parse, which sees a string. Not `check_dead_code`, which looks
 * for the opposite: things defined and never referenced. Not the type checker,
 * because there isn't one.
 *
 * **This is not hypothetical.** Splitting `notificationsView` into smaller
 * functions took `toggleNote` with the slice and left its `onclick` behind. The
 * page parsed, the rows drew, the carets drew, and clicking one threw a
 * `ReferenceError` into a console nobody was reading. It was found by hand, in a
 * browser, minutes before the work was committed.
 *
 * An 11,000-line single-file dashboard does not cause that mistake — a two-line
 * module would allow it just as easily — but it is what makes it likely to go
 * unnoticed, because there is nowhere smaller to look.
 *
 * **Markup is read from the parsed script, not from the file.** A first attempt
 * scanned the raw text and reported `pick` missing: the only `onclick="pick(` in
 * the file is inside a comment *explaining an escaping bug*. Comments are not
 * markup. Acorn already parses this file for `complexity_check`, and every piece
 * of HTML the page emits is a string or template literal in that tree, so taking
 * them from the AST excludes comments by construction rather than by a regex
 * that tries to recognise one.
 */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const acorn = require("acorn");
const { loadPage } = require("./page_context.js");

const page = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const script = page.slice(page.indexOf("<script>") + 8, page.lastIndexOf("</script>"));

/* `onkeydown="if(event.key==='Enter')…"` names a statement, not a function.
 * These are the words that can legitimately open one. */
const KEYWORDS = new Set([
  "if", "for", "while", "return", "switch", "try", "do", "with", "typeof",
  "void", "delete", "new", "throw", "await", "function", "catch",
]);

/* `on<event>="expr("` — the callee up to its opening parenthesis, dotted names
 * included so `VOICE.flag(…)` and `MODELPICK.pick(…)` are checked as written. */
const HANDLER = /\son[a-z]+\s*=\s*"\s*([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(/g;

function named(markup, into) {
  for (const found of markup.matchAll(HANDLER)) {
    const callee = found[1];
    if (!KEYWORDS.has(callee.split(".")[0])) into.add(callee);
  }
}

const wanted = new Set();

/* The static half: everything outside the script tag. */
named(page.slice(0, page.indexOf("<script>")), wanted);
named(page.slice(page.lastIndexOf("</script>")), wanted);

/* And the emitted half, from the AST rather than the text. */
const tree = acorn.parse(script, { ecmaVersion: "latest" });
(function walk(node) {
  if (!node || typeof node !== "object") return;
  if (node.type === "Literal" && typeof node.value === "string") named(node.value, wanted);
  if (node.type === "TemplateLiteral") for (const part of node.quasis) named(part.value.raw, wanted);
  for (const key of Object.keys(node)) {
    const child = node[key];
    if (Array.isArray(child)) child.forEach(walk);
    else if (child && typeof child.type === "string") walk(child);
  }
})(tree);

/* Resolved by *running* the page rather than by looking for a definition.
 * `VOICE.flag` is a method on an object literal and `stopPolling` is declared
 * inside a block — a scan for `function name(` finds neither, and would report
 * two working controls as broken. What matters is whether the name answers when
 * the browser calls it, which is a question only the loaded page can settle. */
const { context } = loadPage();
vm.runInContext("stopPolling()", context);

const missing = [];
for (const callee of [...wanted].sort()) {
  let kind = "undefined";
  try {
    kind = vm.runInContext(`typeof ${callee}`, context);
  } catch {
    kind = "unreachable";  // an object in the path does not exist either
  }
  if (kind !== "function") missing.push(`${callee} (typeof is ${kind})`);
}

if (!wanted.size) {
  console.error("no inline handlers were found at all — this check is matching");
  console.error("nothing and proving nothing.");
  process.exit(1);
}

if (missing.length) {
  console.error("handler check failed:\n");
  console.error(`  ${missing.length} control(s) name a function that does not exist.`);
  console.error("  The page parses and the control draws; pressing it throws.\n");
  for (const line of missing) console.error("    - " + line);
  console.error("");
  process.exit(1);
}

console.log(`${wanted.size} inline handler(s) all resolve to a function`);
