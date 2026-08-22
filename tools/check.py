#!/usr/bin/env python3
"""The smallest check that fails if the template is broken.

Three things have actually gone wrong during development, and each is silent in a
browser until you click the right thing:

  1. the inline <script> stopped parsing (a bad string edit, a stray brace)
  2. a CSS rule vanished because an unbalanced brace ate the rule after it
  3. a view read a field the API no longer returns, rendering "undefined"

This catches 1 and 2 statically and lists the endpoints for 3.
Run:  python3 tools/check.py
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

endpoints = sorted(set(re.findall(r"(?:GET|POST) (/[\w/{}\-]+)", html)))
print(f"{len(endpoints)} endpoints cited:")
for e in endpoints:
    print("  ", e)

if fail:
    print("\nFAIL")
    for f in fail:
        print(" •", f)
    sys.exit(1)
print("\nOK")
