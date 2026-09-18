/* In-place updates: the content region is patched, not replaced (§25.2).
 *
 * `morphChildren` compares the markup a screen just produced with what is on screen and changes
 * only what differs. What this pins is the part a browser can't be trusted to show by accident:
 * that an unchanged element is the *same* element afterwards (so an animation, a selection or a
 * hover survives), that an element with an id is matched by it wherever it moved, that an id is
 * never patched into a different element, that a field somebody is in keeps its value, and that
 * a disclosure somebody opened stays open.
 *
 * The page shim has no element tree, so this supplies a small one — only the DOM calls the
 * patch makes. The rest was checked in a browser.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const { context } = loadPage();
vm.runInContext("stopPolling()", context);
const morphChildren = vm.runInContext("morphChildren", context);

class FakeNode {
  constructor(type, name) {
    this.nodeType = type;
    this.nodeName = name;
    this.parentNode = null;
    this.children_ = [];
  }
  get firstChild() { return this.children_[0] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    const siblings = this.parentNode.children_;
    return siblings[siblings.indexOf(this) + 1] || null;
  }
  insertBefore(node, before) {
    if (node.parentNode) node.parentNode.removeChild(node);
    const at = before ? this.children_.indexOf(before) : this.children_.length;
    this.children_.splice(at < 0 ? this.children_.length : at, 0, node);
    node.parentNode = this;
    return node;
  }
  removeChild(node) {
    this.children_.splice(this.children_.indexOf(node), 1);
    node.parentNode = null;
    return node;
  }
}

class FakeText extends FakeNode {
  constructor(text) { super(3, "#text"); this.nodeValue = text; }
}

class FakeElement extends FakeNode {
  constructor(tag, attributes = {}, children = []) {
    super(1, tag.toUpperCase());
    this.attrs = new Map(Object.entries(attributes));
    this.value = this.attrs.get("value") || "";
    this.checked = this.attrs.has("checked");
    this.type = this.attrs.get("type") || "text";
    for (const child of children) this.insertBefore(child, null);
  }
  get id() { return this.attrs.get("id") || ""; }
  // As in a browser, `open` is the attribute: opening a disclosure sets it, and a patch that
  // drops the attribute closes it.
  get open() { return this.attrs.has("open"); }
  set open(on) { if (on) this.attrs.set("open", ""); else this.attrs.delete("open"); }
  get attributes() { return [...this.attrs].map(([name, value]) => ({ name, value })); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  hasAttribute(name) { return this.attrs.has(name); }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  removeAttribute(name) { this.attrs.delete(name); }
}

const el = (tag, attributes, ...children) => new FakeElement(tag, attributes, children);
const text = (value) => new FakeText(value);
const root = (...children) => el("section", {}, ...children);
const texts = (node) => node.children_.map((child) => child.nodeType === 3 ? child.nodeValue
  : texts(child).join(""));
const expect = (ok, what) => { if (!ok) failures.push(what); };

// ── A changed text is patched into the same element ─────────────────────────
{
  const live = root(el("div", { class: "card" }, el("h3", {}, text("Spend")), text("$1.00")));
  const card = live.children_[0];
  morphChildren(live, root(el("div", { class: "card live" }, el("h3", {}, text("Spend")),
    text("$1.50"))));
  expect(live.children_[0] === card, "an unchanged card was replaced rather than kept");
  expect(card.getAttribute("class") === "card live", "a changed class was not applied");
  expect(texts(card).join("") === "Spend$1.50", "a changed text was not patched in");
}

// ── Rows added and removed ──────────────────────────────────────────────────
{
  const live = root(el("p", {}, text("a")), el("p", {}, text("b")), el("p", {}, text("c")));
  morphChildren(live, root(el("p", {}, text("a")), el("p", {}, text("b"))));
  expect(texts(live).join(",") === "a,b", "a row gone from the markup stayed on screen");
  morphChildren(live, root(el("p", {}, text("a")), el("p", {}, text("b")), el("p", {}, text("z"))));
  expect(texts(live).join(",") === "a,b,z", "a row added to the markup did not appear");
}

// ── An id is matched wherever it moved, and never patched into another ──────
{
  const first = el("div", { id: "one" }, text("1"));
  const second = el("div", { id: "two" }, text("2"));
  const live = root(first, second);
  morphChildren(live, root(el("div", { id: "two" }, text("2")), el("div", { id: "one" }, text("1"))));
  expect(live.children_[0] === second && live.children_[1] === first,
    "elements with ids were patched into each other instead of moved");
  morphChildren(live, root(el("div", { id: "three" }, text("3"))));
  expect(live.children_.length === 1 && live.children_[0] !== first && live.children_[0] !== second,
    "a new id was patched into an element that had another id");
}

// ── A field the reader is in keeps its value ────────────────────────────────
{
  const field = el("input", { id: "limit", value: "" });
  field.value = "25";
  const live = root(field);
  vm.runInContext("TOUCHED.set('limit','25')", context);
  morphChildren(live, root(el("input", { id: "limit", value: "" })));
  expect(field.value === "25", "a value somebody typed was overwritten by the markup");
  vm.runInContext("TOUCHED.clear()", context);
  morphChildren(live, root(el("input", { id: "limit", value: "10" })));
  expect(field.value === "10", "an untouched field did not follow the markup");
}

// ── A disclosure the reader opened stays open ───────────────────────────────
{
  const details = el("details", { class: "explains" }, el("summary", {}, text("how")));
  details.open = true;
  const live = root(details);
  morphChildren(live, root(el("details", { class: "explains" }, el("summary", {}, text("how")))));
  expect(details.open, "a disclosure the reader had opened was closed by the patch");
}

if (failures.length) {
  console.log(`in-place updates are wrong (${failures.length}):\n`);
  for (const failure of failures) console.log(`  • ${failure}`);
  process.exit(1);
}
console.log("in-place updates keep unchanged elements, match ids, and leave the reader's state alone.");
