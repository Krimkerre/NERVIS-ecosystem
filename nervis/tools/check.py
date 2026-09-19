#!/usr/bin/env python3
"""The smallest check that fails if the template is broken.

Three things have actually gone wrong during development, and each is silent in a
browser until you click the right thing:

  1. the inline <script> stopped parsing (a bad string edit, a stray brace)
  2. a CSS rule vanished because an unbalanced brace ate the rule after it
  3. a view read a field the API no longer returns, rendering "undefined"
  4. a region edit defined an API method twice, and the stale copy won

This catches 1, 2 and 4 statically and lists the endpoints for 3.
It does NOT catch anything else — see docs/PITFALLS.md.
Run:  python3 tools/check.py

**A fifth failure has its own check.** `tools/render_check.js` renders every
screen with nothing running and fails if one throws, which is the class that has
actually blanked screens: a `TypeError` while building a template string. Run it
too. There is no CI (GitHub Actions is off); the commit hook in `tools/githooks` and
`tools/check_clean_clone.sh` run both, with every gate in `tools/dashboard_gates.txt`.
Run:  node tools/render_check.js
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
html = (ROOT / "index.html").read_text()
fail = []

# **No path that only exists on one machine, where it will be resolved.**
# `empty_world_check.js` shipped a `require("/Users/…/page_context.js")`, which
# resolved fine for the person who wrote it and nowhere else: CI failed on every
# push for a day and a half with that exact path in the error, while the local
# run stayed green the whole time. The nine sibling checks all used a relative
# require; this catches the tenth.
#
# Matched only inside a call that *resolves* a path. Several suites pass a home
# path as deliberately hostile input — a workspace root, an ssh key, a secret —
# to assert that NERVIS drops or redacts it, and those are the point of their own
# tests rather than a mistake. An allowlist of files would have to grow every
# time somebody wrote another such fixture; this distinguishes the two by what
# the line does with the path.
HOME_PATH = r"(?:/Users/[a-z]|/home/[a-z]|[A-Z]:\\\\Users\\\\)"
RESOLVES = re.compile(
    r"(?:require|open|import|Path|readFile|readFileSync|execFile|spawn|createReadStream)"
    r"\s*\(\s*[\"']" + HOME_PATH,
    re.IGNORECASE,
)

for source in sorted(ROOT.rglob("*.js")) + sorted(ROOT.rglob("*.py")):
    if any(part in {"node_modules", ".venv", "__pycache__"} for part in source.parts):
        continue
    where = source.relative_to(ROOT).as_posix()
    for number, line in enumerate(source.read_text(errors="ignore").splitlines(), 1):
        if RESOLVES.search(line):
            fail.append(f"{where}:{number} resolves a path from one machine: {line.strip()[:90]}")

script = html[html.rindex("<script>") + 8: html.rindex("</script>")]
if shutil.which("node"):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        tmp = f.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    if r.returncode:
        fail.append("JS does not parse:\n" + r.stderr.strip()[:400])
else:
    print("node not found — skipping the JS parse check")

# first pair only: a later <style> string lives inside the JS, and scanning
# past it counts JavaScript braces as CSS ones
style = html[html.index("<style>"): html.index("</style>")]
depth, line = 0, 1
for ch in style:
    if ch == "\n":
        line += 1
    elif ch == "{":
        depth += 1
    elif ch == "}":
        depth -= 1
        if depth < 0:
            fail.append(f"stray closing brace in CSS near line {line} — "
                        "an unbalanced brace silently discards the NEXT rule")
            break
if depth > 0:
    fail.append(f"{depth} unclosed CSS brace(s)")

# 3. "undefined" reaching the DOM. The failure is a template reading a field the
#    API stopped returning, so the *rendered* string says `undefined` — as the
#    Overview's `LOCAL undefined%` did for months, reading `local_pct` from a
#    payload that publishes `local_share`.
#
#    **A bare substring search is not that check.** It also matched `!==
#    undefined`, which is the ordinary way to ask whether an optional field was
#    supplied, and it matched prose in the comments describing this very bug —
#    so the check failed on the code that fixed it and on the note explaining
#    why. Comments and comparisons come out first; whatever `undefined` is left
#    is one somebody is about to concatenate into a string.
_prose = re.sub(r"/\*.*?\*/", "", html, flags=re.DOTALL)
_prose = re.sub(r"(?m)^\s*//.*$", "", _prose)
_code = re.sub(r"[!=]==\s*undefined", "", _prose)
# **Assigning or returning the value is not rendering it.** The failure this
# rule exists for is `undefined` reaching the DOM — the Overview's
# `LOCAL undefined%`, a template reading a field the API stopped returning. An
# assignment (`plan.running = undefined`) clears a field, and a ternary whose
# other branch is `undefined` returns nothing from a promise; neither can put
# the eight characters on a screen. Both were in `index.html` for months while
# this check failed on them, which taught nobody anything except to skip the
# check — and skipping it is how the DOM case comes back.
_code = re.sub(r"[:=]\s*undefined\s*(?=[;,)\]}]|$)", "", _code, flags=re.MULTILINE)
if "undefined" in _code:
    fail.append("the literal string 'undefined' appears in index.html "
                "outside a comment and outside an `=== undefined` comparison")

# 3b. An HTML entity handed to `escapeHtml` is drawn as its own text: `&#9679;` became
#     `&amp;#9679;`, and RAVIS's Providers and Credentials rows read "&#9679; macOS
#     Keychain" where a dot belonged (found 13 September 2026). Markup the page writes
#     itself stays outside the escaper and only the data inside it goes through, so an
#     entity inside an `escapeHtml(...)` call is always this mistake.
for match in re.finditer(r"escapeHtml\([^)]*&#?\w+;", _prose):
    fail.append("an HTML entity is passed through escapeHtml, which draws it as text "
                f"instead of the character: {match.group(0)[:90]}")

# 4. a duplicate key in the API object is legal JavaScript: the LAST one wins and
#    the earlier ones are discarded in silence. A region edit produced exactly
#    that once — four methods defined three times over, with the stale copy
#    winning — and it parsed, rendered and passed every other check here.
#    Methods are indented two spaces, their owning namespace one.
api = html[html.index("const API={"): html.index("const DEMO_API=")]
namespace, seen = None, {}
for line in api.splitlines():
    ns = re.match(r" (\w+):\{", line)
    if ns:
        namespace = ns.group(1)
    method = re.match(r"  async (\w+)\(", line)
    if method and namespace:
        seen.setdefault((namespace, method.group(1)), 0)
        seen[(namespace, method.group(1))] += 1
for (ns, name), n in seen.items():
    if n > 1:
        fail.append(f"API.{ns}.{name}() is defined {n} times — a later duplicate "
                    "silently wins and the earlier one is dead code")

endpoints = sorted(set(re.findall(r"(?:GET|POST) (/[\w/{}\-]+)", html)))

# 5. A cited endpoint that no specification defines is an INVENTED endpoint, which the
#    governing rule forbids — and listing it here without checking made two invented
#    RAVIS paths look sanctioned for as long as this file has existed. Compare each
#    cited path against the spec set, matching on the path with its {placeholders}
#    normalised, since a doc may write {id} where the template writes {decision_id}.
specs = sorted((ROOT.parent).glob("*.md"))
spec_text = "\n".join(p.read_text() for p in specs)


def paths(text):
    """Every API path in text, with {a,b} shorthand expanded and {ids} normalised.

    Both the specs and this file write `/ecosystem/{identity,capabilities,version}`
    and `/providers/{id}/{enable,disable}` as shorthand for several paths. Splitting
    naively on the brace invents a path called `/ecosystem/{identity` and then reports
    it missing, which is how a checker teaches people to ignore it.
    """
    out = set()
    for raw in re.findall(r"(/(?:v1|api/v1|ecosystem)[\w/{},\-]*)", text):
        raw = raw.rstrip("/,")
        branch = re.search(r"\{([\w\-]+(?:,[\w\-]+)+)\}", raw)
        variants = ([raw.replace(branch.group(0), alt) for alt in branch.group(1).split(",")]
                    if branch else [raw])
        for v in variants:
            out.add(re.sub(r"\{[\w]+\}", "{}", v))
    return out


spec_paths = paths(spec_text)


def defined(path):
    """True if the path, or a collection it hangs off, is defined in a spec.

    A template citing `/api/v1/traces/{id}` against a spec that lists `/api/v1/traces`
    is reading the documented resource, not inventing one. The reverse — a path whose
    collection appears nowhere — is the invention this check exists for.
    """
    parts = path.split("/")
    return any("/".join(parts[:n]) in spec_paths for n in range(len(parts), 2, -1))


# Only *citations* count — a provider base_url like ".../v1beta/openai" is data the
# template renders, not a claim that RAVIS serves that path.
cited = set()
for verb, raw in re.findall(r"(GET|POST|PUT|DELETE) (\{?[\w]*\}?/[\w/{},\-]*)", html):
    cited |= paths(re.sub(r"^\{\w+\}", "", raw))
uncited = sorted(e for e in cited if not defined(e))
endpoints = sorted(cited)
if not specs:
    print("spec documents not found beside the template — skipping the endpoint cross-check")
elif uncited:
    fail.append("cited but defined in no specification — invented endpoints:\n    "
                + "\n    ".join(uncited))

# 6. A DEMO_API mutation with no endpoint comment is invisible to the check above,
#    so four of them once sat behind nine buttons uncited. Every member needs a
#    citation on the line before it, in the same form the rest of the file uses.
lines = html[html.index("const DEMO_API={"):].splitlines()
# Stop at whichever comes first: the object's own close, or the next top-level
# declaration. CLARVIS_BRIDGE follows immediately and has members of its own.
lines = lines[1: next(i for i, line in enumerate(lines[1:], 1)
                      if line.rstrip() == "};" or line.startswith("const "))]
for i, line in enumerate(lines):
    m = re.match(r" (\w+)\(", line)
    if not m:
        continue
    # A citation is the comment block directly above; some run to five lines,
    # so the window has to clear the longest one rather than the shortest.
    preceding = "\n".join(lines[max(0, i - 8): i])
    if not re.search(r"(?:GET|POST|PUT|DELETE) [/{]", preceding):
        fail.append(f"DEMO_API.{m.group(1)}() has no endpoint citation — "
                    "an uncited mutation is invisible to the endpoint check")

# Every control names an action that exists, or one the UNBUILT map explains.
#
# `control()` is the rule made mechanical -- "a button for an operation that
# exists, or a disabled one that names what it is waiting for. Never a button
# that looks live and is not." It only helps where it is used, and the SIRVIS
# laboratory's primary button was hand-written around it calling
# `DEMO_API.runBenchmark()`, a method `DEMO_API` does not have. It rendered
# enabled and threw a TypeError on click, on a screen where it was the main
# action. `runBenchmark` had been in `UNBUILT` the whole time.
methods = set(re.findall(r"^\s*(\w+)\s*\([^)]*\)\s*\{",
                         html[html.find("const DEMO_API={"):
                                html.find("const DEMO_API={") + 900], re.M))
unbuilt = set(re.findall(r"^\s*(\w+):\s*'",
                         html[html.find("const UNBUILT="):
                                html.find("const UNBUILT=") + 1200], re.M))
# A handler is a DEMO_API method, a page-local function, or an UNBUILT entry.
# `async function` counts too. The pattern required `function` immediately
# after the indent, so every async top-level handler was invisible to this
# check — which meant a control naming one was reported as dead when it was
# fine, and the first person to hit that would have been tempted to loosen the
# check rather than the pattern.
locals_ = set(re.findall(r"^\s*(?:async\s+)?function (\w+)\s*\(", html, re.M))
locals_ |= set(re.findall(r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", html, re.M))
for action in sorted(set(re.findall(r"control\([^,]+,\s*'(\w+)'", html))):
    if action not in methods and action not in unbuilt and action not in locals_:
        fail.append(f"control(..., '{action}') names neither a DEMO_API method "
                    f"nor an UNBUILT entry — it would throw on click")
for hand in sorted(set(re.findall(r'onclick="DEMO_API\.(\w+)\(', html))):
    if hand not in methods:
        fail.append(f'onclick="DEMO_API.{hand}()" is not a DEMO_API method — '
                    "route it through control(), which renders the disabled form")

# A backtick inside an HTML comment ends the template literal the comment is in.
#
# Four separate breakages in one session, every one of them the same shape: an
# explanatory comment written inside a `$(…).innerHTML = ` … `` string, quoting
# an identifier in backticks the way the surrounding prose does. JavaScript sees
# the string end there, and reports an "Unexpected identifier" naming a token
# from the *following* line, so the error reads as a problem with the code
# rather than with the prose.
#
# `render_check` catches it, but only after a full render of thirty-five
# screens, and the message it prints points at the wrong place. This names the
# comment.
for comment in re.finditer(r"<!--.*?-->", html, re.DOTALL):
    if "`" in comment.group(0):
        line = html[: comment.start()].count("\n") + 1
        fail.append(
            f"index.html:{line}: an HTML comment contains a backtick. Inside a "
            "template literal that ends the string; write the comment without "
            "backticks."
        )

# Which screens gate their tiles on a capability, and which still gate only on
# whether the service is reachable.
#
# Stage 6's exit asks for tiles that are capability-driven. `cell()` consults
# `TILE_CAPABILITY` per screen and falls back to reachability where no entry
# exists — which is the old behaviour, and therefore invisible unless something
# counts it. Reported rather than enforced: several screens genuinely read no
# peer surface (the CLARVIS panel, NERVIS's own Settings), and demanding an
# entry for those would mean inventing a capability to satisfy a tool.
declared = set(re.findall(r"^\s*'([^']+)':\{", html[html.find("const TILE_CAPABILITY"):
                                                     html.find("const requiredCapability")], re.M))
navs = re.findall(r"(\w+):\{name:'[^']*'.*?nav:\[([^\]]*)\]", html, re.S)
screens = {f"{app}/{view.strip().strip(chr(39))}"
           for app, views in navs for view in views.split(",") if view.strip()}
ungated = sorted(s for s in screens if s not in declared and not s.startswith("clarvis/"))
print(f"\n{len(declared)} screen(s) gate tiles on a capability; "
      f"{len(ungated)} still gate on reachability alone:")
for screen in ungated:
    print("  ", screen)

print(f"{len(endpoints)} endpoints cited, all defined in the specs:"
      if not uncited else f"{len(endpoints)} endpoints cited:")
for e in endpoints:
    print("  ", e, "" if defined(e) else "  ← INVENTED")

if fail:
    print("\nFAIL")
    for f in fail:
        print(" •", f)
    sys.exit(1)
print("\nOK")
