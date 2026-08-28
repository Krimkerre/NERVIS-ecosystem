/* The page, loaded into a DOM shim, shared by every check that needs to run it.
 *
 * Extracted from `render_check.js` when a second checker needed the same
 * thing. Duplicating a hundred lines of shim would have meant two shims
 * drifting apart, and the one that drifted would be the one not being looked
 * at — a check that quietly stops exercising what it claims to is worse than
 * no check.
 *
 * **A DOM shim rather than jsdom, and that is the whole trade.** The failures
 * this catches happen while a render function builds its HTML *string*, before
 * anything touches an element, so the DOM only has to absorb writes rather than
 * model them. A hundred lines buys that; jsdom would buy a faithful DOM, an npm
 * dependency tree, and a lockfile in a repository that has almost none.
 */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const page = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const script = page.slice(page.indexOf("<script>") + 8, page.lastIndexOf("</script>"));

/* A plain element stub rather than a Proxy, and that is a correction.
 *
 * The first version was a Proxy that answered to any property, which is fewer
 * lines and looked elegant. It also hung the whole check with no output: a
 * Proxy responds to `then`, so awaiting anything that had touched the DOM
 * turned it into a thenable that never resolved — and once that was fixed it
 * hung again somewhere inside the page's canvas animation, where proxy
 * arithmetic turned a bounded loop into an unbounded one.
 *
 * Enumerating what the page actually uses is more lines and no cleverness, and
 * every gap announces itself as `x is not a function` naming the method. That
 * is the better failure mode for a thing whose whole job is to fail clearly.
 */
function element(id = "") {
  let text = "";
  const self = {
    id, tagName: "DIV", value: "", innerHTML: "", outerHTML: "",
    className: "", title: "", src: "", href: "", checked: false, hidden: false,
    disabled: false, selectedIndex: 0, scrollTop: 0, scrollLeft: 0,
    offsetTop: 0, offsetLeft: 0, offsetHeight: 0, offsetWidth: 0,
    clientHeight: 0, clientWidth: 0, scrollHeight: 0, scrollWidth: 0,
    dataset: {}, style: { setProperty() {}, removeProperty() {} },
    children: [], childNodes: [], options: [], files: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, removeAttribute() {}, getAttribute: () => null,
    hasAttribute: () => false, toggleAttribute() {},
    appendChild(node) { return node; }, removeChild(node) { return node; },
    replaceChildren() {}, insertAdjacentHTML() {}, remove() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true,
    focus() {}, blur() {}, click() {}, scrollIntoView() {}, select() {},
    closest: () => null, matches: () => false, contains: () => false,
    querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({
      top: 0, left: 0, right: 320, bottom: 240, width: 320, height: 240, x: 0, y: 0,
    }),
    /* A canvas that draws nothing. The page starts a background animation on
     * load; every 2D call is a no-op so it runs once and paints into nowhere. */
    getContext: () => new Proxy({}, { get: () => () => undefined }),
    width: 320, height: 240,
  };
  /* `textContent` and `innerHTML` are linked, because the page escapes through
     them: `escapeHtml` sets one and reads the other, and with two unrelated
     plain properties it returned the empty string for *every* value on the
     page. `render_check` never noticed — a screen that renders entirely blank
     still renders — and it was `shaping_check` pinning real markup that made
     it obvious. A shim that silently answers "" to the most-used function in
     the file is worse than one that throws. */
  Object.defineProperty(self, "textContent", {
    get: () => text,
    set(value) {
      text = String(value);
      self.innerHTML = text
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    },
    enumerable: true, configurable: true,
  });
  self.parentElement = null;
  self.parentNode = null;
  self.firstChild = null;
  self.nextElementSibling = null;
  self.ownerDocument = null;
  return self;
}

/* One context per load, because a checker that ran two loads in one process
   would otherwise share `localStorage` and the page's own module state between
   them — and a test that passes only when it runs second is worse than one
   that fails. */
function makeContext({ fetchImpl } = {}) {
const store = new Map();
const elements = new Map();
const context = {
  console,
  /* Unreferenced, for the same reason as `setInterval` below and now for a
     second one: the page reconnects its event stream on a `setTimeout`, so a
     checker that does not call `stopPolling` is held open by a reconnect that
     will never be needed. `shaping_check` hung exactly there. Unref'd timers
     still fire while anything else keeps the loop alive; they simply stop being
     a reason to keep it alive. */
  setTimeout: (fn, ms, ...rest) => { const t = setTimeout(fn, ms, ...rest); t.unref?.(); return t; },
  clearTimeout, clearInterval,
  /* Unreferenced, so the dashboard's own polling cannot hold the process open
     after the check is done. It starts a registry poll on load; without this the
     script runs, every screen passes, and node then waits forever. */
  setInterval: (fn, ms) => { const t = setInterval(fn, ms); t.unref?.(); return t; },
  queueMicrotask, structuredClone,
  URLSearchParams, TextDecoder, TextEncoder,
  AbortController, AbortSignal,
  Date, Math, JSON, Promise, Object, Array, String, Number, Boolean, Error, RegExp, Map, Set,
  /* Nothing is running, which is the state being tested. Every read takes its
   * absent-or-mock path — the one a person opening the file from disk gets. */
  /* Nothing is running by default, which is the state `render_check` tests.
     A checker that wants to exercise the *live* path passes its own. */
  fetch: fetchImpl || (() => Promise.reject(new TypeError("fetch failed"))),
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  },
  location: { origin: "http://127.0.0.1:8790", href: "http://127.0.0.1:8790/index.html",
              search: "", hash: "", pathname: "/index.html" },
  /* Routing writes here, so it has to exist — and it has to *record*, because
     "the URL now addresses this screen" is a claim a check should be able to
     read back rather than take on trust. `location.hash` is updated alongside,
     since the page reads the hash it just wrote when resolving a route.

     A no-op stub would have been fewer lines and would have let a router that
     never actually changed the address pass every gate. */
  history: {
    entries: [],
    pushState(stateObject, title, url) {
      this.entries.push({ how: "push", url: String(url) });
      context.location.hash = String(url).includes("#")
        ? String(url).slice(String(url).indexOf("#")) : "";
    },
    replaceState(stateObject, title, url) {
      this.entries.push({ how: "replace", url: String(url) });
      context.location.hash = String(url).includes("#")
        ? String(url).slice(String(url).indexOf("#")) : "";
    },
    back() {}, forward() {}, go() {},
    get length() { return this.entries.length; },
  },
  /* A stream that connects and then says nothing, which is the state every
     check runs in: no service is up. It records its instances so a check can
     assert that a client opened one, closed it, and did not leak a second.

     `readyState` starts CONNECTING and stays there. A shim that reported OPEN
     would be claiming a connection nothing made — the same fabrication the page
     itself is written to avoid. */
  EventSource: class EventSource {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 2;
    constructor(url, options) {
      this.url = String(url);
      this.withCredentials = !!(options && options.withCredentials);
      this.readyState = 0;
      this.listeners = new Map();
      this.onmessage = null; this.onerror = null; this.onopen = null;
      context.EventSource.instances.push(this);
    }
    addEventListener(type, fn) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(fn);
    }
    removeEventListener(type, fn) {
      const list = this.listeners.get(type) || [];
      const at = list.indexOf(fn);
      if (at >= 0) list.splice(at, 1);
    }
    close() { this.readyState = 2; }
  },
  /* Runs the first frame and never schedules another. The page's background
     canvas animation is decorative; letting it loop would spin this process
     for as long as it lived. */
  requestAnimationFrame: () => 0,
  cancelAnimationFrame: () => undefined,
  /* Bare `addEventListener(…)` at the top level is `window.addEventListener`,
     and the script uses both spellings. */
  addEventListener() {},
  removeEventListener() {},
  scrollTo() {},
  scrollBy() {},
  alert() {},
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  devicePixelRatio: 2,
  innerWidth: 1440,
  innerHeight: 900,
  scrollY: 0,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  document: {
    /* Memoized by id, so a write survives to be read back.
       `getElementById` returned a fresh stub per call, which is enough for
       `render_check` — it only needs the render not to throw — but it throws
       away every `innerHTML` a screen produces. `honesty_check` compares what
       a screen *rendered* with nothing running against what it renders live,
       so the markup has to still be there afterwards. */
    getElementById: (id) => {
      if (!elements.has(id)) elements.set(id, element(id));
      return elements.get(id);
    },
    /* Memoized by selector, for the same reason as `getElementById` above —
       and this is the one that mattered. The page's `$` is `querySelector`, so
       every screen writes its markup through here. Returning a fresh stub made
       `honesty_check` read nothing at all and report that every badge was
       correct: a checker whose own absent-data path looked exactly like a pass,
       which is the defect it was written to hunt. */
    querySelector: (sel) => {
      const key = `sel:${sel}`;
      if (!elements.has(key)) elements.set(key, element(String(sel).replace(/^#/, "")));
      return elements.get(key);
    },
    querySelectorAll: () => [],
    createElement: () => element(),
    addEventListener() {}, removeEventListener() {},
    body: element("body"),
    documentElement: element("html"),
    hidden: false,
    title: "",
  },
};
/* Exposed so a checker can read back what a screen wrote. */
context.__elements = elements;
context.__elements = elements;
context.EventSource.instances = [];
context.window = context;
context.globalThis = context;
context.self = context;
vm.createContext(context);
  return context;
}


/* Everything a checker needs out of the page.
 *
 * `const` and `let` at the top of a script are script-scoped, not properties
 * of the context object — so `context.APP_CONFIG` is undefined however well
 * the script ran. A second evaluation in the same context sees those bindings,
 * which is the supported way to reach them.
 */
function loadPage({ fetchImpl } = {}) {
  const context = makeContext({ fetchImpl });
  try {
    vm.runInContext(script, context, { filename: "index.html", timeout: 10000 });
  } catch (failure) {
    throw new Error(`the inline script threw on load: ${failure.message}`);
  }
  const exported = vm.runInContext(
    "({ APP_CONFIG, state, nervis, ravis, sirvis, clarvis, API, SOURCE, " +
    "absorbFrame, replyMessage, transportFailure, BUILD, " +
    "stopPolling: typeof stopPolling === 'function' ? stopPolling : null })",
    context,
  );
  /* `elements` comes back too, because one checker needs to read *every* sink a
     screen wrote to rather than the one it knows the name of. `#content` holds
     most of a screen, and the status bar, the side nav and the avatar slot are
     written separately — a check that only reads `#content` is blind to three
     surfaces that also interpolate service data. */
  return { context, exported, elements: context.__elements };
}

module.exports = { loadPage, element };
