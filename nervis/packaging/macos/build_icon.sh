#!/bin/bash
# Regenerates NERVIS.icns from NERVIS-icon.svg.
#
# The geometry is not independent art: it is pdf.py's _mark() and the
# dashboard's own .brand i mark, redrawn at icon scale — same diamond, same
# LOGO_EDGE/LOGO_CORE colours (nervis/src/nervis/layout.py), same "glow as a
# blur behind a solid stroke" idea the PDF fakes with concentric strokes and
# this gets for free from SVG's own feGaussianBlur.
#
# The squircle behind it is a real superellipse (|x/a|^n + |y/a|^n = 1, n=5),
# not a rounded-rect. A hand-built .app with no Xcode asset catalog gets no
# automatic corner masking from the OS, so the shape has to be drawn — see
# the comment in generate_squircle.py for why n=5 rather than a CSS-style
# border-radius approximation.
#
# Needs Chrome (for exact-pixel SVG rasterisation — sips/qlmanage can render
# an SVG but neither gives the same control over viewport size and filter
# fidelity) and iconutil/sips, both stock on macOS.
set -euo pipefail
cd "$(dirname "$0")"

python3 generate_icon.py

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ ! -x "$CHROME" ]; then
  echo "Google Chrome not found at the expected path — needed to rasterise the SVG." >&2
  exit 1
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

"$CHROME" --headless --disable-gpu --screenshot="$WORK/master.png" \
  --window-size=1024,1024 --default-background-color=00000000 \
  "$(pwd)/NERVIS-icon.svg" >/dev/null 2>&1

ICONSET="$WORK/NERVIS.iconset"
mkdir -p "$ICONSET"
# name:size pairs Apple's iconutil expects. @2x is the same nominal size at
# double the pixels, for Retina — not a separate design.
for pair in "16:icon_16x16" "32:icon_16x16@2x" "32:icon_32x32" "64:icon_32x32@2x" \
            "128:icon_128x128" "256:icon_128x128@2x" "256:icon_256x256" \
            "512:icon_256x256@2x" "512:icon_512x512" "1024:icon_512x512@2x"; do
  size="${pair%%:*}"; name="${pair#*:}"
  sips -z "$size" "$size" "$WORK/master.png" --out "$ICONSET/$name.png" >/dev/null
done

iconutil -c icns "$ICONSET" -o "$(pwd)/NERVIS.icns"
cp "$WORK/master.png" "$(pwd)/NERVIS-icon-1024.png"
echo "NERVIS.icns and NERVIS-icon-1024.png regenerated."
