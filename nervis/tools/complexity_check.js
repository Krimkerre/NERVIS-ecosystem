/* Cyclomatic complexity for the dashboard's inline script.
 *
 * The four Python packages are gated at ruff's `max-complexity = 8` with zero
 * exemptions. This file — 4,400 lines of JavaScript, the largest single source
 * in the repository and the one with the fewest tests — had no equivalent, so
 * "we hold complexity to 8" was true of the code that was easiest to hold and
 * silent about the code that needed it most.
 *
 * **A real parser, not a regular expression.** An earlier attempt counted
 * decision points between function keywords, which attributes every nested
 * arrow function to whichever named function precedes it — inflating the worst
 * entries by absorbing their neighbours and understating the rest. The numbers
 * were wrong in both directions and confidently presented. `acorn` is the one
 * dependency in this repository: a JavaScript parser, MIT, **zero transitive
 * dependencies**. A correct one is not a few lines, which is the rung of the
 * ladder where a dependency becomes the right answer.
 *
 * **Attributed to the innermost function.** A decision point inside a nested
 * arrow belongs to that arrow, not to its parent — which is the opposite of
 * ruff's rule for Python, where a nested `def` contributes its whole complexity
 * to the enclosing function. The difference is deliberate: Python's nesting is
 * usually a closure that could be a method, and JavaScript's is usually a
 * `.map` callback that could not be anything else.
 */

const fs = require("node:fs");
const path = require("node:path");
const acorn = require("acorn");

/* A ratchet, not a standard. The Python packages hold 8; this file cannot yet,
 * and setting 8 here would be actively harmful — only 21% of this file's
 * decision points are control flow, and 69% are `||`, `??` and `?:` shaping
 * optional fields, mostly inside template literals. Sixteen of the thirty
 * functions above 8 contain no `if`, loop, `case` or `catch` at all. A limit of
 * 8 would pressure a reader toward hiding optional-field handling rather than
 * writing it out, which is worse code that scores better.
 *
 * So: set just at the worst survivor, and lowered whenever one comes down. It
 * catches a NEW tangle, which is the thing worth catching. Raising it needs a
 * reason in the commit message. */
const LIMIT = Number(process.env.NERVIS_COMPLEXITY_LIMIT || 30);

const page = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const open = page.indexOf("<script>") + "<script>".length;
const script = page.slice(open, page.lastIndexOf("</script>"));
const before = page.slice(0, open).split("\n").length - 1;

const tree = acorn.parse(script, {
  ecmaVersion: "latest",
  locations: true,
  allowReturnOutsideFunction: true,
});

const FUNCTIONS = new Set([
  "FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression",
]);

/* One decision point each, which is the standard definition: a function starts
 * at 1 and every branch it can take adds one. `else` adds nothing — it is the
 * path already counted by its `if`. A `default:` adds nothing for the same
 * reason. */
function decisions(node) {
  switch (node.type) {
    case "IfStatement":
    case "ForStatement":
    case "ForInStatement":
    case "ForOfStatement":
    case "WhileStatement":
    case "DoWhileStatement":
    case "CatchClause":
    case "ConditionalExpression":
      return 1;
    case "SwitchCase":
      return node.test ? 1 : 0;
    case "LogicalExpression":
      return node.operator === "&&" || node.operator === "||" || node.operator === "??" ? 1 : 0;
    default:
      return 0;
  }
}

function nameOf(node, parent) {
  if (node.id && node.id.name) return node.id.name;
  if (!parent) return "(anonymous)";
  if (parent.type === "VariableDeclarator" && parent.id.name) return parent.id.name;
  if (parent.type === "Property" && parent.key) return parent.key.name || parent.key.value;
  if (parent.type === "MethodDefinition" && parent.key) return parent.key.name;
  if (parent.type === "AssignmentExpression" && parent.left.type === "MemberExpression") {
    return (parent.left.property && parent.left.property.name) || "(assigned)";
  }
  /* A callback passed straight to a method — `items.map(x => …)`. Named for the
   * call it is inside, because "the callback in `items.map`" is what a reader
   * needs and "(anonymous)" is what forty of them would say. */
  if (parent.type === "CallExpression" && parent.callee.type === "MemberExpression") {
    const object = parent.callee.object;
    const method = parent.callee.property && parent.callee.property.name;
    const owner = object.type === "Identifier" ? object.name
      : object.type === "MemberExpression" && object.property ? object.property.name : "?";
    return `${owner}.${method}() callback`;
  }
  return "(anonymous)";
}

const found = [];
const stack = [];

function walk(node, parent) {
  if (!node || typeof node.type !== "string") return;

  const isFunction = FUNCTIONS.has(node.type);
  if (isFunction) {
    stack.push({ name: nameOf(node, parent), line: node.loc.start.line + before, complexity: 1 });
  } else if (stack.length) {
    stack[stack.length - 1].complexity += decisions(node);
  }

  for (const key of Object.keys(node)) {
    if (key === "loc" || key === "start" || key === "end") continue;
    const child = node[key];
    if (Array.isArray(child)) {
      for (const item of child) if (item && typeof item.type === "string") walk(item, node);
    } else if (child && typeof child.type === "string") {
      walk(child, node);
    }
  }

  if (isFunction) found.push(stack.pop());
}

walk(tree, null);

const over = found.filter((f) => f.complexity > LIMIT).sort((a, b) => b.complexity - a.complexity);
const buckets = new Map();
for (const f of found) {
  const key = f.complexity >= 20 ? "20+" : f.complexity >= 10 ? "10-19"
    : f.complexity >= 5 ? "5-9" : f.complexity >= 2 ? "2-4" : "1";
  buckets.set(key, (buckets.get(key) || 0) + 1);
}

if (process.env.NERVIS_COMPLEXITY_REPORT) {
  console.log(`${found.length} functions`);
  for (const key of ["1", "2-4", "5-9", "10-19", "20+"]) {
    console.log(`  ${key.padStart(5)}  ${buckets.get(key) || 0}`);
  }
  console.log("\n  worst:");
  for (const f of found.slice().sort((a, b) => b.complexity - a.complexity).slice(0, 15)) {
    console.log(`   ${String(f.complexity).padStart(3)}  index.html:${f.line}  ${f.name}`);
  }
}

if (over.length) {
  console.error(`${over.length} function(s) exceed complexity ${LIMIT}:\n`);
  for (const f of over) {
    console.error(`  • ${String(f.complexity).padStart(3)}  index.html:${f.line}  ${f.name}`);
  }
  console.error(
    "\nThe Python packages hold 8. This file cannot yet, so the limit here is a" +
    "\nratchet rather than a standard: it is set just below the worst survivor," +
    "\nand every function brought down should lower it. Raising it needs a reason" +
    "\nin the commit message."
  );
  process.exit(1);
}
console.log(`all ${found.length} functions are within complexity ${LIMIT}`);
