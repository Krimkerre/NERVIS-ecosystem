#!/usr/bin/env python3
"""Rebuild index.html's AVATAR_SOURCE_B64 block from avatars/*.html.

The avatar pages are embedded rather than linked because each one runs in its
own iframe via `srcdoc` — that is what keeps four stylesheets, four id spaces and
four scripts from colliding, and it means the template stays a single file you can
open from disk with no server.

Run after editing any avatar:  python3 tools/embed-avatars.py
"""
import base64, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Order matters only for readability; "miku" is the easter-egg avatar and is
# embedded the same way as the four app avatars. Leaving it out of this list
# would silently drop it from index.html the next time this runs.
APPS = ["miku", "nervis", "sirvis", "ravis", "clarvis"]

def main() -> int:
    index = ROOT / "index.html"
    html = index.read_text()

    parts = []
    for app in APPS:
        src = ROOT / "avatars" / f"{app}.html"
        if not src.exists():
            print(f"missing {src}", file=sys.stderr)
            return 1
        b64 = base64.b64encode(src.read_bytes()).decode()
        parts.append(f"{app}:'{b64}'")

    block = "const AVATAR_SOURCE_B64={" + ",".join(parts) + "};"
    new, count = re.subn(r"const AVATAR_SOURCE_B64=\{.*?\};", block, html, count=1, flags=re.S)
    if count != 1:
        print("AVATAR_SOURCE_B64 block not found in index.html", file=sys.stderr)
        return 1

    index.write_text(new)
    print(f"embedded {len(APPS)} avatars · {len(block)//1024} KB")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
