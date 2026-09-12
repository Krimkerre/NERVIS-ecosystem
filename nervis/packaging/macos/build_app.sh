#!/bin/bash
# Builds NERVIS.app, the menu bar launcher, into build/ beside this file.
#
# The app carries no service code. It holds this repository's path and runs
# tools/run.py, so changing the services never needs a rebuild — only moving the
# repository does, because the path is written into Info.plist here. The header of
# NERVISMenu.swift says why it is built that way.
#
# Needs the Xcode command line tools (swiftc, codesign, plutil). The Finder icon is
# NERVIS.icns from build_icon.sh; the menu bar icon is drawn in code, so there is no
# second picture to keep in step with the first.
#
#   nervis/packaging/macos/build_app.sh
#   open nervis/packaging/macos/build/NERVIS.app
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
app="$here/build/NERVIS.app"
[ -f "$repo/tools/run.py" ] || { echo "no tools/run.py under $repo" >&2; exit 1; }
version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$repo/nervis/pyproject.toml" | head -n 1)"

mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
# Swift 5 language mode: the file is written for it, and the stricter Swift 6
# concurrency checking would reject AppKit's delegate callbacks as written.
swiftc -O -swift-version 5 -target "$(uname -m)-apple-macos14.0" \
  -o "$app/Contents/MacOS/NERVIS" "$here/NERVISMenu.swift"
cp "$here/NERVIS.icns" "$app/Contents/Resources/NERVIS.icns"

# The path goes into XML, so the three characters XML reserves are escaped.
escaped="$(printf '%s' "$repo" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')"
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>NERVIS</string>
  <key>CFBundleDisplayName</key><string>NERVIS</string>
  <key>CFBundleIdentifier</key><string>local.nervis.menubar</string>
  <key>CFBundleExecutable</key><string>NERVIS</string>
  <key>CFBundleIconFile</key><string>NERVIS</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <!-- No Dock icon: the menu bar icon is the whole app. -->
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NERVISRepository</key><string>$escaped</string>
</dict>
</plist>
PLIST
plutil -lint "$app/Contents/Info.plist" >/dev/null
# Ad hoc: enough for a Mac to run an app built on itself. Another Mac would need a
# Developer ID signature, which this does not attempt.
codesign --force --sign - "$app"

echo "Built $app"
echo "  starts the stack in $repo"
