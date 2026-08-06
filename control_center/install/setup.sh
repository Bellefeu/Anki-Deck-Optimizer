#!/usr/bin/env bash
#
# One-command setup for macOS and Linux.
#
#   bash control_center/install/setup.sh              install and check
#   bash control_center/install/setup.sh --dry-run    show changes only
#   bash control_center/install/setup.sh --yes        do not ask before installing
#
# Installs: Python 3.10+, poppler, tesseract, Node.js - but only the ones you are
# actually missing. Safe to re-run; it skips whatever is already there.
#
# It does NOT install Anki. You have that already.
#
# On sudo: installing system packages needs an administrator password. This script
# prints every command before running it and never runs sudo silently. If you would
# rather do it by hand, --dry-run gives you the exact list to copy.
#
# PORTABILITY NOTE - do not "modernise" this file.
# macOS ships bash 3.2.57 (2007; Apple froze it over GPLv3), so this script uses
# NO associative arrays (bash 4.0+) and NO arrays at all. Package lists are plain
# space-separated strings, and the unquoted expansions below are deliberate word
# splitting, not a mistake. Under `set -u`, bash 3.2 also errors on "${arr[@]}"
# when arr is empty, which is a second reason arrays are avoided entirely.
# It is tested under dash, so it is POSIX-clean and will run anywhere.

set -eu

DRY=0
ASSUME_YES=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --yes|-y)  ASSUME_YES=1 ;;
    --help|-h) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

PROJECT_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$PROJECT_ROOT"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
miss() { printf '  \033[33mmiss\033[0m  %s\n' "$*"; }
err()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; }

run() {
  printf '  \033[36m$\033[0m %s\n' "$*"
  if [ "$DRY" = "1" ]; then return 0; fi
  "$@"
}

confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  [ "$DRY" = "1" ] && return 0
  printf '\n%s [Y/n] ' "$1"
  if [ -r /dev/tty ]; then read -r reply < /dev/tty || reply=y; else read -r reply || reply=y; fi
  case "$reply" in ""|y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# which package provides a given command
provider() {
  case "$1" in
    pdftotext|pdftoppm|pdfinfo|pdfimages) echo poppler ;;
    tesseract)                            echo tesseract ;;
    node)                                 echo node ;;
    *)                                    echo "$1" ;;
  esac
}

NEED=""
add_need() {
  case " $NEED " in
    *" $1 "*) ;;                # already listed
    *) NEED="$NEED $1" ;;
  esac
}

# ---------------------------------------------------------------- detect
OS=${SETUP_FAKE_OS:-$(uname -s)}
PM=""
case "$OS" in
  Darwin) PM="brew" ;;
  Linux)
    for m in apt-get dnf pacman zypper; do
      if command -v "$m" >/dev/null 2>&1; then PM="$m"; break; fi
    done
    ;;
  *) err "Unsupported OS: $OS. On Windows use the Prism launcher (or WSL, then re-run this)."; exit 1 ;;
esac
[ -n "${SETUP_FAKE_PM:-}" ] && PM="$SETUP_FAKE_PM"

bold "=== ANKI DECK OPTIMIZATION - SETUP ==="
echo "  os: $OS    package manager: ${PM:-none found}"
[ "$DRY" = "1" ] && echo "  DRY RUN - nothing will be installed"
echo

# ---------------------------------------------------------------- python
# Chicken-and-egg: this script exists because bootstrap.py cannot install the
# Python it needs in order to run. Everything after Python is handled here too,
# so the user only ever types one command.
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0.0)
    maj=${v%%.*}; min=${v##*.}
    if [ "$maj" = "3" ] && [ "$min" -ge 10 ] 2>/dev/null; then PY="$c"; break; fi
  fi
done

NEED_PY=0
bold "1. Python 3.10+"
if [ -n "$PY" ]; then
  ok "$PY ($("$PY" -V 2>&1))"
else
  miss "not found (macOS ships 3.9, which is too old)"
  NEED_PY=1
fi

# ---------------------------------------------------------------- tools
bold "2. System tools"
for t in pdftotext pdftoppm pdfinfo pdfimages tesseract node; do
  p=$(provider "$t")
  if command -v "$t" >/dev/null 2>&1; then
    ok "$t"
  else
    miss "$t  (from $p)"
    add_need "$p"
  fi
done

# ---------------------------------------------------------------- install
if [ "$NEED_PY" = "0" ] && [ -z "$NEED" ]; then
  echo
  ok "everything is already installed"
else
  echo
  bold "3. Installing what is missing"

  if [ "$PM" = "brew" ] && ! command -v brew >/dev/null 2>&1 && [ "$DRY" != "1" ]; then
    miss "Homebrew is not installed - it is how macOS gets these packages"
    if confirm "Install Homebrew now? (asks for your Mac password)"; then
      run /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      for p in /opt/homebrew/bin /usr/local/bin; do
        if [ -x "$p/brew" ]; then eval "$("$p/brew" shellenv)"; fi
      done
    else
      err "Cannot continue without a package manager."
      echo "     Install Homebrew yourself: https://brew.sh, then open Prism again."
      exit 1
    fi
  fi

  PKGS=""
  case "$PM" in
    brew)
      [ "$NEED_PY" = "1" ] && PKGS="$PKGS python@3.12"
      for n in $NEED; do case "$n" in
        poppler)   PKGS="$PKGS poppler" ;;
        tesseract) PKGS="$PKGS tesseract" ;;
        node)      PKGS="$PKGS node" ;;
      esac; done
      confirm "Install with Homebrew:$PKGS" || { err "declined"; exit 1; }
      run brew install $PKGS
      ;;
    apt-get)
      [ "$NEED_PY" = "1" ] && PKGS="$PKGS python3 python3-pip python3-venv"
      for n in $NEED; do case "$n" in
        poppler)   PKGS="$PKGS poppler-utils" ;;
        tesseract) PKGS="$PKGS tesseract-ocr tesseract-ocr-eng" ;;
        node)      PKGS="$PKGS nodejs npm" ;;
      esac; done
      confirm "Install with apt (needs your password):$PKGS" || { err "declined"; exit 1; }
      run sudo apt-get update -qq
      run sudo apt-get install -y $PKGS
      ;;
    dnf|zypper)
      [ "$NEED_PY" = "1" ] && PKGS="$PKGS python3 python3-pip"
      for n in $NEED; do case "$n" in
        poppler)   PKGS="$PKGS poppler-utils" ;;
        tesseract) PKGS="$PKGS tesseract tesseract-langpack-eng" ;;
        node)      PKGS="$PKGS nodejs" ;;
      esac; done
      confirm "Install with $PM (needs your password):$PKGS" || { err "declined"; exit 1; }
      run sudo "$PM" install -y $PKGS
      ;;
    pacman)
      [ "$NEED_PY" = "1" ] && PKGS="$PKGS python python-pip"
      for n in $NEED; do case "$n" in
        poppler)   PKGS="$PKGS poppler" ;;
        tesseract) PKGS="$PKGS tesseract tesseract-data-eng" ;;
        node)      PKGS="$PKGS nodejs npm" ;;
      esac; done
      confirm "Install with pacman (needs your password):$PKGS" || { err "declined"; exit 1; }
      run sudo pacman -S --needed --noconfirm $PKGS
      ;;
    *)
      err "No supported package manager found on this system."
      echo "     Install these yourself, then re-run: python 3.10+, poppler, tesseract, node"
      exit 1
      ;;
  esac

  # re-detect python after a possible install
  if [ -z "$PY" ]; then
    for c in python3.13 python3.12 python3.11 python3.10 python3; do
      if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
    done
  fi
fi

# ---------------------------------------------------------------- hand off
echo
bold "4. Handing over to bootstrap.py"
if [ "$DRY" = "1" ]; then
  echo "  \$ ${PY:-python3} scripts/bootstrap.py"
  echo
  echo "  (dry run - stopping here)"
  exit 0
fi
if [ -z "$PY" ]; then
  err "Python 3.10+ still not found after install."
  echo "     Close this terminal, then open Prism again."
  exit 1
fi
echo
exec "$PY" scripts/bootstrap.py
