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
too — CI runs both.
Run:  node tools/render_check.js
"""
import pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
html = (ROOT / "index.html").read_text()
fail = []

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

if "undefined" in html:
    fail.append("the literal string 'undefined' appears in index.html")

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
lines = lines[1: next(i for i, l in enumerate(lines[1:], 1)
                      if l.rstrip() == "};" or l.startswith("const "))]
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
