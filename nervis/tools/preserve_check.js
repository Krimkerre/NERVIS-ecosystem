/* What a reader is in the middle of has to survive a repaint.
 *
 * §25.2: "Each view replaces the whole content region. Correct for a static
 * prototype; wrong once an event stream drives it, because every event would
 * rebuild the DOM and discard scroll position, focus, selection and any open
 * control."
 *
 * That is no longer a future tense. The registry poll repaints on a service
 * transition, the recovery path repaints up to three more times per state, and
 * the event stream repaints the Events screen whenever the hub says something.
 * The RAVIS Credentials fields carry no `value=` and no backing state, so a
 * pasted API key exists nowhere but the DOM until Save is pressed — and any of
 * those repaints landing in between silently emptied it.
 *
 * The shim models no element tree, so this drives the capture and restore
 * functions against a small one supplied here. That is the honest scope: it
 * pins the rules — what is remembered, what is deliberately not, and when it is
 * forgotten — and a browser was used for the rest.
 */

const { loadPage } = require("./page_context.js");
const vm = require("node:vm");

const failures = [];
const { context } = loadPage();
vm.runInContext("stopPolling()", context);

/* A tiny DOM, addressable by id, matching only what the capture queries. */
function fakeElement(id, extra = {}) {
  return { id, value: "", checked: false, open: false, scrollTop: 0, type: "text",
           style: { display: "" }, focus() { context.document.activeElement = this; },
           setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; },
           selectionStart: null, selectionEnd: null, ...extra };
}

const dom = new Map();
context.document.getElementById = (id) => dom.get(id) || null;
context.document.querySelectorAll = (selector) => {
  const all = [...dom.values()];
  if (selector === "details[id]") return all.filter((el) => "open" in el && el.tagName === "DETAILS");
  if (selector === "[id][data-keep-open]") return all.filter((el) => el.keepOpen);
  if (selector === "[id]") return all;
  return [];
};

const field = fakeElement("cred-anthropic");
const box = fakeElement("vpEnabled", { type: "checkbox" });
const disclosure = fakeElement("x-why", { tagName: "DETAILS" });
const panel = fakeElement("pm-pool", { keepOpen: true });
const scroller = fakeElement("feed");
for (const el of [field, box, disclosure, panel, scroller]) dom.set(el.id, el);

/* 1 · A value the reader typed survives, and the caret with it. */
field.value = "sk-typed-but-not-saved";
context.document.activeElement = field;
field.selectionStart = 5; field.selectionEnd = 5;
vm.runInContext("TOUCHED.set('cred-anthropic','sk-typed-but-not-saved')", context);
disclosure.open = true;
panel.style.display = "";
scroller.scrollTop = 240;

const was = vm.runInContext("captureInteraction()", context);

/* The repaint: every element is replaced by a fresh one, which is what the real
   `innerHTML =` does and what makes preserving anything a deliberate act. */
for (const el of [...dom.values()]) {
  dom.set(el.id, fakeElement(el.id, { tagName: el.tagName, type: el.type, keepOpen: el.keepOpen }));
}
dom.get("pm-pool").style.display = "none";  // as `poolRow` re-emits it
context.document.activeElement = null;

vm.runInContext(`restoreInteraction(${JSON.stringify(was)})`, context);

if (dom.get("cred-anthropic").value !== "sk-typed-but-not-saved") {
  failures.push("a typed value did not survive the repaint");
}
if (context.document.activeElement !== dom.get("cred-anthropic")) {
  failures.push("focus did not return to the field being edited");
}
if (dom.get("cred-anthropic").selectionStart !== 5) {
  failures.push("the caret jumped — focus was restored without the selection");
}
if (!dom.get("x-why").open) failures.push("an opened disclosure closed on repaint");
if (dom.get("pm-pool").style.display === "none") {
  failures.push("an open picker was re-emitted closed and left closed");
}
if (dom.get("feed").scrollTop !== 240) failures.push("scroll position was lost");

/* 2 · A field the reader never touched is NOT restored.
 *
 * The opposite failure, and the more likely one: restore everything and a form
 * that was just saved and cleared is un-cleared by the next repaint, which
 * makes a save look like it did not happen. */
{
  const untouched = fakeElement("vpName");
  dom.set("vpName", untouched);
  vm.runInContext("restoreTyping()", context);
  if (untouched.value !== "") {
    failures.push("a field nobody typed in was overwritten from the draft store");
  }
}

/* 3 · A checkbox is remembered by its checked state, not its value string. */
{
  vm.runInContext("TOUCHED.set('vpEnabled',true)", context);
  dom.set("vpEnabled", fakeElement("vpEnabled", { type: "checkbox" }));
  vm.runInContext("restoreTyping()", context);
  if (dom.get("vpEnabled").checked !== true) {
    failures.push("a ticked checkbox was restored as a value rather than a state");
  }
}

/* 4 · Leaving the screen forgets the draft.
 *
 * A repaint keeps what you typed; navigating away must not, or a half-typed
 * provider name reappears in the next screen's field with the same id, long
 * after the reader moved on. */
{
  vm.runInContext("TOUCHED.set('cred-anthropic','still-here')", context);
  vm.runInContext("go('sirvis','Models')", context);
  const size = vm.runInContext("TOUCHED.size", context);
  if (size !== 0) failures.push(`navigating away kept ${size} draft(s)`);
}

vm.runInContext("stopPolling()", context);

if (failures.length) {
  console.error(`${failures.length} preservation failure(s):\n`);
  for (const failure of failures) console.error(`  • ${failure}`);
  process.exit(1);
}
console.log(
  "a repaint keeps what was typed, focused, selected, opened and scrolled — " +
  "and leaving the screen forgets it");
