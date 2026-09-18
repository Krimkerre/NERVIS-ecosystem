#!/usr/bin/env bash
# Install the NERVIS ecosystem on this machine:   ./install.sh
#
# Run it from a checkout of NERVIS-ecosystem. It works out which system it is on, installs what
# the ecosystem needs, puts Clarvis into code-server, and adds NERVIS to the desktop — then stops.
# It starts nothing: open NERVIS from the applications menu (or ./start-linux.sh) when you want it.
#
#   macOS                     Homebrew for the packages; builds the menu bar app
#   Linux: apt, dnf, pacman   Ubuntu, Debian, Fedora, Arch and their relatives; adds the tray app
#   Windows                   run it inside WSL — it is then the Linux install, without the tray
#
# Safe to run again: every step looks first and skips what is already there. Nothing is removed.
#
# Options:
#   --yes               don't ask; take the default answer to every question
#   --dry-run           say what would be done, and do none of it
#   --no-ollama         skip Ollama (NERVIS's memory search then has no embedding model)
#   --no-models         install Ollama but download no model into it
#   --with-pdf-model    also download qwen2.5vl:3b (about 3 GB), which checks a PDF's layout
#   --no-code-server    skip code-server, and with it Clarvis
#   --no-clarvis        skip building and installing the Clarvis extension
#   --no-desktop        skip the applications-menu entry and the tray (Linux) or app (macOS)
#
# Environment:
#   CLARVIS_REPO        where to clone Clarvis from when ../clarvis doesn't exist
#                       (default: the clarvis repository beside this one's own origin)

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT="$(dirname "$REPO")"
CLARVIS_DIR="$PARENT/clarvis"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
PRIVATE_NODE="$DATA_DIR/nervis/node"

ASSUME_YES=0 DRY_RUN=0 WANT_OLLAMA=1 WANT_MODELS=1 WANT_PDF_MODEL=0
WANT_CODE_SERVER=1 WANT_CLARVIS=1 WANT_DESKTOP=1

for argument in "$@"; do
  case "$argument" in
    --yes|-y) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-ollama) WANT_OLLAMA=0; WANT_MODELS=0 ;;
    --no-models) WANT_MODELS=0 ;;
    --with-pdf-model) WANT_PDF_MODEL=1 ;;
    --no-code-server) WANT_CODE_SERVER=0; WANT_CLARVIS=0 ;;
    --no-clarvis) WANT_CLARVIS=0 ;;
    --no-desktop) WANT_DESKTOP=0 ;;
    --help|-h) sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $argument (see ./install.sh --help)" >&2; exit 2 ;;
  esac
done

# ── Saying things ─────────────────────────────────────────────────────────────

if [ -t 1 ]; then BOLD=$'\e[1m' DIM=$'\e[2m' GREEN=$'\e[32m' YELLOW=$'\e[33m' RED=$'\e[31m' OFF=$'\e[0m'
else BOLD="" DIM="" GREEN="" YELLOW="" RED="" OFF=""; fi

DONE_LINES=() SKIPPED_LINES=() LATER_LINES=()
step()    { printf '\n%s▸ %s%s\n' "$BOLD" "$1" "$OFF"; }
say()     { printf '  %s\n' "$1"; }
good()    { printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$1"; DONE_LINES+=("$1"); }
skipped() { printf '  %s–%s %s\n' "$DIM" "$OFF" "$1"; SKIPPED_LINES+=("$1"); }
later()   { LATER_LINES+=("$1"); }
warn()    { printf '  %s!%s %s\n' "$YELLOW" "$OFF" "$1"; }
fail()    { printf '\n%s✗ %s%s\n' "$RED" "$1" "$OFF" >&2; exit 1; }
have()    { command -v "$1" >/dev/null 2>&1; }

# Runs a command, or only says it under --dry-run.
run() {
  # To stderr: a command's own output may be going to the install log, and the plan must not.
  if [ "$DRY_RUN" = 1 ]; then printf '  %swould run:%s %s\n' "$DIM" "$OFF" "$*" >&2; return 0; fi
  "$@"
}

ask() {  # ask "Question?" default(y|n) → 0 for yes
  local question="$1" default="$2" answer
  if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; then [ "$default" = y ]; return; fi
  local hint="[y/N]"; [ "$default" = y ] && hint="[Y/n]"
  read -r -p "  $question $hint " answer || answer=""
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy] ]]
}

# ── Which system is this ──────────────────────────────────────────────────────

OS="" DISTRO="" PM="" WSL=0 ARCH="$(uname -m)"
case "$(uname -s)" in
  Darwin) OS=macos; PM=brew ;;
  Linux)
    OS=linux
    if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then WSL=1; fi
    if [ -r /etc/os-release ]; then
      # shellcheck disable=SC1091
      . /etc/os-release
      DISTRO="${PRETTY_NAME:-$ID}"
      case " ${ID:-} ${ID_LIKE:-} " in
        *" debian "*|*" ubuntu "*) PM=apt ;;
        *" fedora "*|*" rhel "*|*" centos "*) PM=dnf ;;
        *" arch "*) PM=pacman ;;
      esac
    fi
    ;;
  MINGW*|MSYS*|CYGWIN*)
    fail "This is Windows. Install WSL (in PowerShell: wsl --install), open Ubuntu, clone
  NERVIS-ecosystem there and run ./install.sh inside it." ;;
  *) fail "$(uname -s) isn't a system this installer knows. NERVIS runs on macOS and Linux." ;;
esac

printf '%sNERVIS installer%s — %s\n' "$BOLD" "$OFF" "$REPO"
if [ "$OS" = macos ]; then say "macOS $(sw_vers -productVersion) on $ARCH"
else say "${DISTRO:-Linux} on $ARCH$([ "$WSL" = 1 ] && echo ' (inside WSL)')"; fi
[ "$DRY_RUN" = 1 ] && say "${YELLOW}Dry run: nothing will be changed.${OFF}"

[ -f "$REPO/tools/run.py" ] || fail "Run this from a checkout of NERVIS-ecosystem: $REPO/tools/run.py isn't there."
[ "$(id -u)" != 0 ] || fail "Run this as yourself, not as root. It asks for your password
  when it needs to install system packages, and everything else belongs in your home folder."
if [ "$OS" = linux ] && [ -z "$PM" ]; then
  fail "This Linux uses a package manager the installer doesn't know. It knows apt (Debian,
  Ubuntu), dnf (Fedora) and pacman (Arch). Install git, Python 3.11+ with venv, and
  curl yourself, then run ./install.sh --no-desktop again."
fi

# ── System packages ───────────────────────────────────────────────────────────

SUDO=()
need_root() {
  [ ${#SUDO[@]} -gt 0 ] && return 0
  have sudo || fail "Installing system packages needs sudo, which isn't installed."
  SUDO=(sudo)
  if [ "$DRY_RUN" = 0 ] && ! sudo -n true 2>/dev/null; then
    say "System packages are installed with sudo, which may ask for your password."
  fi
}

missing_packages() {  # prints the packages from "$@" that aren't installed
  local package
  for package in "$@"; do
    case "$PM" in
      apt) dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed" || echo "$package" ;;
      dnf) rpm -q "$package" >/dev/null 2>&1 || echo "$package" ;;
      pacman) pacman -Q "$package" >/dev/null 2>&1 || echo "$package" ;;
      brew) brew list --formula "$package" >/dev/null 2>&1 || echo "$package" ;;
    esac
  done
}

APT_UPDATED=0
install_packages() {  # install_packages "what they're for" package…
  local purpose="$1"; shift
  local wanted=() package
  while IFS= read -r package; do [ -n "$package" ] && wanted+=("$package"); done < <(missing_packages "$@")
  if [ ${#wanted[@]} -eq 0 ]; then good "$purpose: already installed"; return 0; fi
  if [ "$DRY_RUN" = 1 ]; then say "$purpose: would install ${wanted[*]}"; return 0; fi
  say "$purpose: installing ${wanted[*]}"
  # The package managers' own progress goes to a log rather than the screen: a few hundred
  # "Setting up …" lines hide the one line that says what went wrong. Errors still show.
  mkdir -p "$REPO/.run"
  local log="$REPO/.run/install.log"
  case "$PM" in
    apt)
      need_root
      if [ "$APT_UPDATED" = 0 ]; then run "${SUDO[@]}" apt-get update -qq >>"$log"; APT_UPDATED=1; fi
      run "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${wanted[@]}" >>"$log" ;;
    dnf) need_root; run "${SUDO[@]}" dnf install -y -q "${wanted[@]}" >>"$log" ;;
    # Never -Sy with a package list: on Arch that is a partial upgrade, which the Arch wiki warns
    # can leave a system that no longer boots. The existing package database is used, and a stale
    # one is a sentence below rather than a system upgrade nobody asked for.
    pacman) need_root; run "${SUDO[@]}" pacman -S --needed --noconfirm "${wanted[@]}" >>"$log" \
              || fail "$purpose didn't install. If pacman couldn't find a package, update the system
  first (sudo pacman -Syu), then run ./install.sh again. Its output is in $log." ;;
    brew) run brew install "${wanted[@]}" >>"$log" ;;
  esac || fail "$purpose didn't install. The package manager's output is in $log."
  good "$purpose: installed ${wanted[*]}"
}

GNOME=0
case "${XDG_CURRENT_DESKTOP:-}${DESKTOP_SESSION:-}" in *GNOME*|*gnome*|*ubuntu*) GNOME=1 ;; esac

step "System packages"
if [ "$OS" = macos ]; then
  have brew || fail "Homebrew is needed and isn't installed. Install it from https://brew.sh
  (one command), then run ./install.sh again."
  have xcode-select && xcode-select -p >/dev/null 2>&1 || fail "The Xcode command line tools are
  needed (for git and for building the menu bar app). Run: xcode-select --install"
  # macOS ships curl, and the command line tools ship git; Homebrew's Python only when no python3
  # new enough is already on PATH (pyenv's, python.org's and Homebrew's all count).
  if python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    good "Git, Python and curl: already there"
  else
    install_packages "Python 3.12" python@3.12
  fi
else
  case "$PM" in
    # No system pip: the services' environment is made with `python3 -m venv`, which brings its own.
    apt) install_packages "Git, Python and tools" git curl ca-certificates python3 python3-venv xz-utils procps ;;
    dnf) install_packages "Git, Python and tools" git curl ca-certificates python3 xz procps-ng ;;
    pacman) install_packages "Git, Python and tools" git curl ca-certificates python xz procps-ng ;;
  esac
fi

# The Python every service runs on. The launcher itself runs on whatever `python3` is, and makes
# the services' environment with it, so it is the one that has to be new enough.
PYTHON=python3
if [ "$OS" = macos ] && ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
   && [ -x "$(brew --prefix)/bin/python3.12" ]; then PYTHON="$(brew --prefix)/bin/python3.12"; fi
if [ "$DRY_RUN" = 0 ] && ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  fail "NERVIS needs Python 3.11 or newer, and $("$PYTHON" --version 2>&1 || echo 'no python3') is
  what this system has. Ubuntu 24.04, Debian 12, Fedora 38 and current Arch all ship one."
fi
good "Python: $("$PYTHON" --version 2>&1)"

# ── The services' own environment ─────────────────────────────────────────────

step "NERVIS, RAVIS, SIRVIS and their shared protocol"
if [ -x "$REPO/ravis/.venv/bin/python" ]; then
  good "Virtual environment: already there (ravis/.venv)"
else
  say "Creating the virtual environment and installing the four packages (a minute or two)…"
  run "$PYTHON" "$REPO/tools/run.py" setup
  good "Virtual environment: created (ravis/.venv)"
fi

# ── Ollama ────────────────────────────────────────────────────────────────────

step "Ollama — the local runtime for NERVIS's memory search and PDF check"
if [ "$WANT_OLLAMA" = 0 ]; then
  skipped "Ollama: skipped (--no-ollama). Memory search stays off until it's installed."
elif have ollama; then
  good "Ollama: already installed ($(ollama --version 2>/dev/null | head -1 || echo installed))"
elif [ "$OS" = macos ]; then
  install_packages "Ollama" ollama
else
  say "Ollama: installing with its official script (ollama.com/install.sh)…"
  need_root
  if [ "$DRY_RUN" = 1 ]; then run sh -c "curl -fsSL https://ollama.com/install.sh | sh"
  else curl -fsSL https://ollama.com/install.sh | sh; fi
  good "Ollama: installed"
fi

pull_model() {  # pull_model name "why"
  local name="$1" why="$2" started=0
  if [ "$DRY_RUN" = 1 ]; then
    if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$name\(:latest\)\{0,1\}"; then
      good "$name: already downloaded ($why)"
    else
      run ollama pull "$name"
    fi
    return 0
  fi
  if ! curl -fsS -m 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    # Ollama's own service may not be running (WSL without systemd, macOS before first launch):
    # start it for the download only, and stop it again afterwards.
    ollama serve >/dev/null 2>&1 & started=$!
    for _ in $(seq 1 30); do curl -fsS -m 1 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
  fi
  if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$name\(:latest\)\{0,1\}"; then
    good "$name: already downloaded ($why)"
  else
    say "Downloading $name ($why)…"
    ollama pull "$name"
    good "$name: downloaded"
  fi
  [ "$started" != 0 ] && kill "$started" 2>/dev/null || true
}

if [ "$WANT_MODELS" = 1 ] && { have ollama || [ "$DRY_RUN" = 1 ]; }; then
  pull_model nomic-embed-text "about 270 MB — memory and knowledge search"
  if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "qwen2.5vl:3b"; then
    good "qwen2.5vl:3b: already downloaded (the PDF layout check)"
  elif [ "$WANT_PDF_MODEL" = 1 ] || ask "Also download qwen2.5vl:3b (about 3 GB), which checks a saved PDF's layout?" n; then
    pull_model qwen2.5vl:3b "about 3 GB — PDF layout check"
  else
    skipped "qwen2.5vl:3b: not downloaded. PDFs are still read; only their layout check is off."
    later "For the PDF layout check: ollama pull qwen2.5vl:3b"
  fi
elif [ "$WANT_OLLAMA" = 1 ]; then
  skipped "Ollama models: none downloaded (--no-models)"
fi

# ── code-server and Clarvis ───────────────────────────────────────────────────

# code-server writes a default ~/.config/code-server/config.yaml the first time it runs at all,
# even for --version. The launcher treats that file as the owner's own config, which wins outright
# (tools/run.py, code_server_settings), so an installer that merely asked code-server its version
# would swap the launcher's password for one code-server invented. Found on the Ubuntu desktop
# test, 18 September 2026. Every call here gets a config folder of its own, thrown away at exit;
# extensions live under the data folder, which is untouched, so Clarvis lands where it's used.
CODE_SERVER_CONFIG="$(mktemp -d)"
trap 'rm -rf "$CODE_SERVER_CONFIG"' EXIT
cs() { XDG_CONFIG_HOME="$CODE_SERVER_CONFIG" code-server "$@"; }

step "code-server — the editor in NERVIS's Code tab, which runs Clarvis"
if [ "$WANT_CODE_SERVER" = 0 ]; then
  skipped "code-server: skipped (--no-code-server), and with it Clarvis"
elif have code-server; then
  good "code-server: already installed ($(cs --version 2>/dev/null | grep -E '^[0-9]' | head -1 | cut -d' ' -f1))"
elif [ "$OS" = macos ]; then
  install_packages "code-server" code-server
else
  say "code-server: installing with its official script (code-server.dev/install.sh)…"
  # On Arch the script's own choice is to build code-server from the AUR, which needs Arch's build
  # tools and several minutes; its standalone method unpacks the release into ~/.local instead,
  # with no build and no sudo. Everywhere else its choice is a .deb or .rpm, the better one there.
  method=()
  [ "$PM" = pacman ] && method=(-s -- --method standalone)
  if [ "$DRY_RUN" = 1 ]; then run sh -c "curl -fsSL https://code-server.dev/install.sh | sh ${method[*]}"
  else curl -fsSL https://code-server.dev/install.sh | sh "${method[@]}"; fi
  # The standalone install (Arch) lands in ~/.local/bin, which not every shell puts on PATH. The
  # launcher looks there too, so NERVIS finds it either way; a person typing `code-server` may not.
  if ! have code-server; then
    export PATH="$HOME/.local/bin:$PATH"
    later "code-server is in ~/.local/bin, which your shell's PATH doesn't include; NERVIS finds it anyway"
  fi
  have code-server || fail "code-server's installer finished, but code-server isn't where it said."
  good "code-server: installed"
  # A .deb or .rpm install suggests a systemd service. NERVIS starts and stops code-server itself,
  # with the stack, so that service would be a second copy fighting over the same port.
  [ "$PM" = pacman ] || later "code-server's installer mentions 'systemctl enable code-server' — not needed: NERVIS starts it"
fi

# Node 20 or newer, for building Clarvis's package only. A system Node that is new enough is used
# as it is; otherwise nodejs.org's own build goes into a private folder, checked against the
# checksums nodejs.org publishes, so no package repository is added to the system for it.
NODE_BIN=""
node_ok() { "$1" -e 'process.exit(parseInt(process.versions.node) >= 20 ? 0 : 1)' 2>/dev/null; }
find_node() {
  if have node && node_ok node; then NODE_BIN="$(dirname "$(command -v node)")"; return 0; fi
  if [ -x "$PRIVATE_NODE/bin/node" ] && node_ok "$PRIVATE_NODE/bin/node"; then NODE_BIN="$PRIVATE_NODE/bin"; return 0; fi
  return 1
}
fetch_private_node() {
  local platform arch listing file expected tarball
  case "$OS" in macos) platform=darwin ;; *) platform=linux ;; esac
  case "$ARCH" in x86_64|amd64) arch=x64 ;; aarch64|arm64) arch=arm64 ;; *) fail "No Node build for $ARCH." ;; esac
  listing="$(curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt)"
  file="$(printf '%s\n' "$listing" | awk -v want="-$platform-$arch.tar.xz" '$2 ~ want"$" {print $2; exit}')"
  expected="$(printf '%s\n' "$listing" | awk -v f="$file" '$2 == f {print $1}')"
  [ -n "$file" ] && [ -n "$expected" ] || fail "nodejs.org didn't list a Node 22 build for $platform-$arch."
  tarball="$(mktemp)"
  curl -fsSL "https://nodejs.org/dist/latest-v22.x/$file" -o "$tarball"
  if have sha256sum; then actual="$(sha256sum "$tarball" | cut -d' ' -f1)"; else actual="$(shasum -a 256 "$tarball" | cut -d' ' -f1)"; fi
  [ "$actual" = "$expected" ] || { rm -f "$tarball"; fail "The Node download didn't match nodejs.org's checksum; nothing was installed."; }
  rm -rf "$PRIVATE_NODE" && mkdir -p "$PRIVATE_NODE"
  tar -xJf "$tarball" -C "$PRIVATE_NODE" --strip-components=1
  rm -f "$tarball"
}

step "Clarvis — the coding assistant, installed into code-server"
if [ "$WANT_CLARVIS" = 0 ]; then
  skipped "Clarvis: skipped"
else
  if [ ! -d "$CLARVIS_DIR/.git" ]; then
    source_url="${CLARVIS_REPO:-}"
    if [ -z "$source_url" ]; then
      origin="$(git -C "$REPO" remote get-url origin 2>/dev/null || true)"
      source_url="${origin%NERVIS-ecosystem.git}clarvis.git"
      [ "$origin" != "$source_url" ] || source_url=""
    fi
    [ -n "$source_url" ] || fail "Clarvis isn't at $CLARVIS_DIR and there's no origin to find it from.
  Clone it there yourself, or set CLARVIS_REPO=<its git URL>."
    say "Clarvis isn't beside NERVIS-ecosystem; cloning it from $source_url…"
    run git clone -q "$source_url" "$CLARVIS_DIR"
  fi
  if ! find_node; then
    if [ "$DRY_RUN" = 1 ]; then run "download Node 22 from nodejs.org into $PRIVATE_NODE"; NODE_BIN="$PRIVATE_NODE/bin"
    else
      say "Building Clarvis needs Node 20 or newer; fetching Node 22 from nodejs.org into $PRIVATE_NODE…"
      fetch_private_node && find_node || fail "Node couldn't be set up for building Clarvis."
      good "Node $("$NODE_BIN/node" --version): in $PRIVATE_NODE, used only for building Clarvis"
    fi
  fi
  version="$(sed -n 's/^  "version": "\(.*\)",$/\1/p' "$CLARVIS_DIR/package.json" 2>/dev/null | head -1)"
  installed="$(cs --list-extensions --show-versions 2>/dev/null | grep -i '\.clarvis@' | sed 's/.*@//' || true)"
  if [ -n "$version" ] && [ "$installed" = "$version" ]; then
    good "Clarvis $version: already installed in code-server"
  else
    say "Building Clarvis ${version:-} (npm ci, then its package)…"
    if [ "$DRY_RUN" = 1 ]; then
      run npm ci; run npx vsce package -o clarvis.vsix; run code-server --install-extension clarvis.vsix
    else
      (cd "$CLARVIS_DIR" && PATH="$NODE_BIN:$PATH" npm ci --no-audit --no-fund --loglevel=error \
        && PATH="$NODE_BIN:$PATH" npx --no-install vsce package --allow-missing-repository -o clarvis.vsix >/dev/null) \
        || fail "Clarvis didn't build. Its output is above."
      cs --install-extension "$CLARVIS_DIR/clarvis.vsix" --force >/dev/null \
        || fail "code-server didn't take the Clarvis package ($CLARVIS_DIR/clarvis.vsix)."
      good "Clarvis ${version:-}: built and installed in code-server"
    fi
  fi
fi

# ── Opening NERVIS from the desktop ───────────────────────────────────────────

if [ "$WANT_DESKTOP" = 0 ]; then
  step "Desktop"; skipped "Applications-menu entry: skipped (--no-desktop)"
elif [ "$OS" = macos ]; then
  step "The menu bar app"
  run "$REPO/nervis/packaging/macos/build_app.sh"
  built="$REPO/nervis/packaging/macos/build/NERVIS.app"
  if [ -d /Applications/NERVIS.app ] && ! ask "Replace /Applications/NERVIS.app with this build?" y; then
    skipped "NERVIS.app: built in $built, not copied to /Applications"
  else
    run rm -rf /Applications/NERVIS.app && run cp -R "$built" /Applications/
    good "NERVIS.app: in /Applications — open it to start the stack"
  fi
elif [ "$WSL" = 1 ]; then
  step "Desktop"
  skipped "Tray and applications-menu entry: not on WSL, which can't put an icon in Windows' tray"
  later "Start with ./start-linux.sh, then open http://127.0.0.1:8790 in your Windows browser"
else
  step "The applications-menu entry and the tray"
  case "$PM" in
    apt) tray_packages=(python3-gi gir1.2-ayatanaappindicator3-0.1 libnotify-bin)
         [ "$GNOME" = 1 ] && tray_packages+=(gnome-shell-extension-appindicator) ;;
    dnf) tray_packages=(python3-gobject libayatana-appindicator-gtk3 libnotify)
         [ "$GNOME" = 1 ] && tray_packages+=(gnome-shell-extension-appindicator) ;;
    pacman) tray_packages=(python-gobject libayatana-appindicator libnotify)
         [ "$GNOME" = 1 ] && tray_packages+=(gnome-shell-extension-appindicator) ;;
  esac
  install_packages "The tray's libraries" "${tray_packages[@]}"
  if [ "$GNOME" = 1 ] && have gnome-extensions; then
    # Ubuntu ships the extension under its own name; everyone else under upstream's.
    extension=""
    for candidate in ubuntu-appindicators@ubuntu.com appindicatorsupport@rgcjonas.gmail.com; do
      if gnome-extensions list 2>/dev/null | grep -qx "$candidate"; then extension="$candidate"; break; fi
    done
    if [ -z "$extension" ]; then
      warn "GNOME's tray-icon extension is installed but GNOME hasn't seen it yet."
      later "Log out and back in once, so GNOME can show NERVIS's tray icon"
    elif gnome-extensions list --enabled 2>/dev/null | grep -qx "$extension"; then
      good "GNOME's tray-icon extension: already on"
    else
      run gnome-extensions enable "$extension" 2>/dev/null || true
      good "GNOME's tray-icon extension: switched on"
      # GNOME acts on it at the next unlock or log-in: extensions are off on the lock screen,
      # and a Wayland session reads newly installed ones only when it starts.
      later "If NERVIS's tray icon doesn't appear, log out and back in once"
    fi
  fi

  applications="$DATA_DIR/applications"
  icons="$DATA_DIR/icons/hicolor/scalable/apps"
  run mkdir -p "$applications" "$icons"
  run cp "$REPO/nervis/packaging/macos/NERVIS-icon.svg" "$icons/nervis.svg"
  entry="$applications/nervis.desktop"
  if [ "$DRY_RUN" = 1 ]; then run "write $entry"
  else
    # The entry holds the path to this checkout, as the Mac app's Info.plist does: moving the
    # checkout means running the installer again from its new place.
    cat > "$entry" <<DESKTOP
[Desktop Entry]
Type=Application
Name=NERVIS
GenericName=AI ecosystem
Comment=Start the NERVIS stack and keep an eye on it from the tray
Exec="$REPO/nervis/packaging/linux/nervis-tray"
Icon=nervis
Terminal=false
Categories=Development;Utility;
StartupNotify=false
DESKTOP
    chmod 0644 "$entry"
  fi
  run chmod +x "$REPO/nervis/packaging/linux/nervis-tray"
  have update-desktop-database && run update-desktop-database -q "$applications" 2>/dev/null || true
  have gtk-update-icon-cache && run gtk-update-icon-cache -q -t "$DATA_DIR/icons/hicolor" 2>/dev/null || true
  good "NERVIS: in the applications menu — open it to start the stack and the tray"
fi

# ── What happened ─────────────────────────────────────────────────────────────

printf '\n%sDone.%s\n' "$BOLD" "$OFF"
if [ ${#SKIPPED_LINES[@]} -gt 0 ]; then
  printf '\n  Left out:\n'; printf '    – %s\n' "${SKIPPED_LINES[@]}"
fi
printf '\n  To start NERVIS:\n'
if [ "$OS" = macos ]; then say "  open /Applications/NERVIS.app   (or ./start-macos.command)"
elif [ "$WSL" = 1 ] || [ "$WANT_DESKTOP" = 0 ]; then say "  ./start-linux.sh"
else say "  open NERVIS from the applications menu   (or ./start-linux.sh)"; fi
say "  the dashboard is at http://127.0.0.1:8790"
if [ ${#LATER_LINES[@]} -gt 0 ]; then
  printf '\n  Worth knowing:\n'; printf '    · %s\n' "${LATER_LINES[@]}"
fi
