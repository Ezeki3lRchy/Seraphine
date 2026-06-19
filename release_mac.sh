#!/bin/bash
# release_mac.sh — one-command macOS release.
# Bumps VERSION, builds the .dmg (via make_mac.sh), verifies the frozen build,
# and publishes a GitHub release to the PUBLIC Seraphine-releases repo that the
# in-app auto-updater reads. Uses your local `gh` login — no PAT, no CI minutes.
#
# Usage:
#   ./release_mac.sh <version> [options]
#
# Options:
#   --notes "<md>"       Release notes (markdown). Shown in the in-app updater.
#   --notes-file <path>  Read release notes from a file.
#   --push               After committing the bump, push the current branch.
#   --yes                Skip the confirmation prompt (non-interactive).
#   --allow-dirty        Don't abort on an unclean working tree (not recommended).
#   --dry-run            Print every step without bumping/building/publishing.
#
# Example:
#   ./release_mac.sh 1.2.3 --notes-file NOTES.md --push
set -euo pipefail
cd "$(dirname "$0")"

# ---- config -----------------------------------------------------------------
REPO_RELEASES="Ezeki3lRchy/Seraphine-releases"   # public repo the updater reads
CONFIG="app/common/config.py"
DMG="dist/Seraphine.dmg"
APP_BIN="dist/Seraphine.app/Contents/MacOS/Seraphine"
PY="venv/bin/python"

# ---- args -------------------------------------------------------------------
VERSION=""
NOTES=""
NOTES_FILE=""
DO_PUSH=0
ASSUME_YES=0
ALLOW_DIRTY=0
DRY_RUN=0

die() { echo "error: $*" >&2; exit 1; }
note() { echo "==> $*"; }
run() { if [ "$DRY_RUN" -eq 1 ]; then echo "[dry-run] $*"; else eval "$*"; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    --notes)       NOTES="${2:-}"; shift 2;;
    --notes-file)  NOTES_FILE="${2:-}"; shift 2;;
    --push)        DO_PUSH=1; shift;;
    --yes|-y)      ASSUME_YES=1; shift;;
    --allow-dirty) ALLOW_DIRTY=1; shift;;
    --dry-run)     DRY_RUN=1; shift;;
    -h|--help)     sed -n '2,22p' "$0"; exit 0;;
    -*)            die "unknown option: $1";;
    *)             [ -z "$VERSION" ] && VERSION="$1" || die "unexpected arg: $1"; shift;;
  esac
done

# ---- pre-flight -------------------------------------------------------------
note "pre-flight checks"
[ -n "$VERSION" ] || die "version required, e.g. ./release_mac.sh 1.2.3"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must be X.Y.Z (got '$VERSION')"
[ "$(uname -s)" = "Darwin" ] || die "macOS only (the .dmg build needs a Mac)"
[ -x "$PY" ] || die "$PY not found — create the venv and install requirements first"
[ -x "venv/bin/pyinstaller" ] || die "pyinstaller not in venv — pip install pyinstaller"
command -v gh >/dev/null || die "gh (GitHub CLI) not installed"
gh auth status >/dev/null 2>&1 || die "gh not authenticated — run: gh auth login"

CURRENT="$(grep -E '^VERSION = "' "$CONFIG" | sed -E 's/.*"([^"]+)".*/\1/')"
[ -n "$CURRENT" ] || die "could not read current VERSION from $CONFIG"
[ "$CURRENT" != "$VERSION" ] || die "VERSION is already $VERSION in $CONFIG"
echo "    current VERSION: $CURRENT  ->  new: $VERSION"

if [ -n "$NOTES_FILE" ]; then
  [ -f "$NOTES_FILE" ] || die "notes file not found: $NOTES_FILE"
  NOTES="$(cat "$NOTES_FILE")"
fi
[ -n "$NOTES" ] || NOTES="## v$VERSION (macOS)

Maintenance release. Use **Update and Restart** to install."

if [ -z "$(git status --porcelain)" ]; then
  echo "    working tree: clean"
elif [ "$ALLOW_DIRTY" -eq 1 ]; then
  echo "    working tree: DIRTY (--allow-dirty set, continuing)"
else
  die "working tree not clean — commit your changes first (the release builds from the tree). Use --allow-dirty to override."
fi

if gh release view "v$VERSION" --repo "$REPO_RELEASES" >/dev/null 2>&1; then
  die "release v$VERSION already exists in $REPO_RELEASES"
fi
echo "    tag v$VERSION: free in $REPO_RELEASES"

# ---- bump + commit ----------------------------------------------------------
note "bumping VERSION $CURRENT -> $VERSION in $CONFIG"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "[dry-run] would set VERSION = \"$VERSION\" and commit 'release: bump VERSION to $VERSION'"
else
  "$PY" - "$VERSION" <<'PY'
import re, sys
ver = sys.argv[1]
p = "app/common/config.py"
s = open(p, encoding="utf-8").read()
s2, n = re.subn(r'^VERSION = "[^"]*"', f'VERSION = "{ver}"', s, flags=re.M)
if n != 1:
    sys.exit(f"expected exactly one VERSION line, found {n}")
open(p, "w", encoding="utf-8").write(s2)
PY
  git add "$CONFIG"
  git commit -q -m "release: bump VERSION to $VERSION"
  echo "    committed bump"
fi

# ---- build ------------------------------------------------------------------
note "building .dmg (./make_mac.sh)"
run "./make_mac.sh"

# ---- verify frozen build reports the new version ----------------------------
note "verifying the frozen app reports $VERSION"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "[dry-run] would run: $APP_BIN --version  (expect $VERSION)"
else
  [ -f "$DMG" ] || die "build did not produce $DMG"
  REPORTED="$("$APP_BIN" --version 2>/dev/null | tail -1 | tr -d '[:space:]')"
  [ "$REPORTED" = "$VERSION" ] || die "frozen app reports '$REPORTED', expected '$VERSION' — aborting before publish"
  echo "    frozen app reports: $REPORTED  ✓   ($(du -h "$DMG" | cut -f1) dmg)"
fi

# ---- confirm ----------------------------------------------------------------
if [ "$ASSUME_YES" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
  echo
  echo "About to publish:  v$VERSION  ->  $REPO_RELEASES   (asset: $DMG)"
  read -r -p "Proceed? [y/N] " ans
  case "$ans" in y|Y|yes) ;; *) die "aborted by user (bump commit kept locally; not published)";; esac
fi

# ---- publish ----------------------------------------------------------------
note "publishing release v$VERSION to $REPO_RELEASES"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "[dry-run] would run: gh release create v$VERSION --repo $REPO_RELEASES --title 'v$VERSION (macOS)' --notes <notes> $DMG"
else
  printf '%s' "$NOTES" > /tmp/seraphine_release_notes.md
  gh release create "v$VERSION" \
    --repo "$REPO_RELEASES" \
    --title "v$VERSION (macOS)" \
    --notes-file /tmp/seraphine_release_notes.md \
    "$DMG"
  rm -f /tmp/seraphine_release_notes.md
fi

# ---- optional push ----------------------------------------------------------
if [ "$DO_PUSH" -eq 1 ]; then
  note "pushing current branch"
  run "git push origin HEAD"
else
  echo "==> note: bump commit NOT pushed (run with --push, or push/PR yourself)"
fi

note "done. v$VERSION is live; existing users (< $VERSION) will see Update and Restart."
