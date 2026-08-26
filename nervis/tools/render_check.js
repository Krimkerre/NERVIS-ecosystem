/* Render every screen with nothing running, and fail if one throws.
 *
 * The dashboard's one inviolable rule is that it opens from disk with no
 * services up: a live read that throws and stops the render destroys that
 * silently, for everyone who is not currently running the service under test.
 * `tools/check.py` catches a script that stopped parsing; it explicitly catches
 * nothing else. This catches the class that has actually bitten — four times in
 * one week, each a `TypeError` while building a template string:
 *
 *   • SERVICES[row.key].state, where the row's key had no entry
 *   • a reduce seeded with null and not guarded on it
 *   • usable() on a key that was not in the map
 *   • a node list built from a registry that had grown a row
 *
 * Every one blanked a whole screen. None failed a test, because there were no
 * tests — the check was a person opening a browser.
 *
 * **A DOM shim rather than jsdom, and that is the whole trade.** The failures
 * above happen while a render function builds its HTML *string*, before
 * anything touches an element, so the DOM only has to absorb writes rather than
 * model them. Eighty lines of shim buys that; jsdom would buy a faithful DOM,
 * an npm dependency tree and a lockfile in a repository that has none.
 *
 * What that costs, stated plainly: this proves a screen **assembles**. It does
 * not prove the markup is valid, that anything is laid out, or that a click
 * works. It is the cheap half of the check, and the half that keeps failing.
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
  const self = {
    id, tagName: "DIV", value: "", textContent: "", innerHTML: "", outerHTML: "",
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
  self.parentElement = null;
  self.parentNode = null;
  self.firstChild = null;
  self.nextElementSibling = null;
  self.ownerDocument = null;
  return self;
}

const store = new Map();
const context = {
  console,
  setTimeout, clearTimeout, clearInterval,
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
  fetch: () => Promise.reject(new TypeError("fetch failed")),
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  },
  location: { origin: "http://127.0.0.1:8790", href: "http://127.0.0.1:8790/index.html",
              search: "", hash: "", pathname: "/index.html" },
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
    getElementById: (id) => element(id),
    querySelector: () => element(),
    querySelectorAll: () => [],
    createElement: () => element(),
    addEventListener() {}, removeEventListener() {},
    body: element("body"),
    documentElement: element("html"),
    hidden: false,
    title: "",
  },
};
context.window = context;
context.globalThis = context;
context.self = context;
vm.createContext(context);

/* Unhandled rejections are the failure mode this is here for: a render that
 * throws inside an `await` reports nowhere otherwise. */
const escaped = [];
process.on("unhandledRejection", (reason) => escaped.push(String(reason)));

/* A watchdog, because a hang and a failure need the same treatment in CI: a
   non-zero exit and a sentence saying what happened. Without one a stuck render
   is a job that runs until the runner's own limit kills it with no message. */
const watchdog = setTimeout(() => {
  console.error("render check did not finish within 60s — a screen is hanging.");
  process.exit(1);
}, 60000);
watchdog.unref?.();

try {
  /* A timeout, because the alternative to a clean failure here is a process
     that never exits. The shim is a Proxy, and a Proxy answering to arithmetic
     or comparison can turn a bounded loop in the page into an unbounded one —
     which is what happened, and cost half an hour of looking in the wrong
     place. */
  vm.runInContext(script, context, { filename: "index.html", timeout: 10000 });
} catch (failure) {
  console.error(`the inline script threw on load: ${failure.message}`);
  process.exit(1);
}

/* `const` and `let` at the top of a script are script-scoped, not properties of
 * the context object — so `context.APP_CONFIG` is undefined however well the
 * script ran. A second evaluation in the same context sees those bindings,
 * which is the supported way to reach them. */
const exported = vm.runInContext(
  "({ APP_CONFIG, state, nervis, ravis, sirvis, clarvis, " +
  "stopPolling: typeof stopPolling === 'function' ? stopPolling : null })",
  context,
);

async function main() {
  const renderers = { nervis: exported.nervis, ravis: exported.ravis,
                      sirvis: exported.sirvis, clarvis: exported.clarvis };
  const failures = [];
  let checked = 0;

  for (const [app, config] of Object.entries(exported.APP_CONFIG)) {
    const render = renderers[app];
    if (typeof render !== "function") continue;
    for (const view of config.nav) {
      checked += 1;
      if (process.env.RENDER_CHECK_TRACE) require("node:fs").writeSync(2, `  -> ${app}/${view}
`);
      exported.state.app = app;
      exported.state.view = view;
      try {
        /* Bounded, and reported as its own failure. A render that never settles
           is as broken as one that throws, and without a deadline it takes the
           whole check with it instead of naming itself. */
        await Promise.race([
          render(),
          new Promise((_, reject) =>
            setTimeout(() => reject(new Error("did not finish within 5s")), 5000)),
        ]);
      } catch (failure) {
        failures.push(`${app}/${view}: ${failure.message}`);
      }
    }
  }

  /* Give anything the renders kicked off a tick to reject. */
  await new Promise((resolve) => setTimeout(resolve, 50));
  if (exported.stopPolling) exported.stopPolling();

  if (failures.length) {
    console.error(`${failures.length} of ${checked} screens failed to render:\n`);
    for (const failure of failures) console.error(`  • ${failure}`);
    console.error(
      "\nEvery one of these blanks a whole screen in a browser. The dashboard's" +
      "\nrule is that it renders with nothing running — a live read that throws" +
      "\nand stops the render breaks that silently."
    );
    process.exit(1);
  }
  if (escaped.length) {
    console.error(`${escaped.length} unhandled rejection(s) escaped a render:\n`);
    for (const reason of new Set(escaped)) console.error(`  • ${reason}`);
    process.exit(1);
  }
  clearTimeout(watchdog);
  console.log(`all ${checked} screens render with nothing running`);
}

main().catch((failure) => {
  /* Without this, a throw inside `main` itself lands in the unhandledRejection
     collector, the process exits 0, and the check reports nothing at all —
     which is the one outcome worse than a false failure. */
  console.error(`the render check itself failed: ${failure.stack || failure}`);
  process.exit(1);
});
