#!/bin/bash
# make_mac.sh — build Seraphine.app + Seraphine.dmg on macOS (run from repo root).
# Mirrors make.ps1 for Windows. Produces dist/Seraphine.dmg.
# NOTE: the build is UNSIGNED (no Apple Developer ID). Gatekeeper will quarantine
# it on download; first launch needs:  xattr -cr /Applications/Seraphine.app
set -euo pipefail
cd "$(dirname "$0")"

PY=venv/bin/python
PYINSTALLER=venv/bin/pyinstaller

echo "==> building Seraphine.app (PyInstaller)"
"$PYINSTALLER" --noconfirm Seraphine.spec

echo "==> stripping quarantine on local build"
xattr -cr dist/Seraphine.app || true

echo "==> staging .dmg (with /Applications drag target)"
rm -rf dist/dmg
mkdir -p dist/dmg
cp -R dist/Seraphine.app dist/dmg/Seraphine.app
ln -s /Applications dist/dmg/Applications

echo "==> creating compressed Seraphine.dmg"
rm -f dist/Seraphine.dmg
hdiutil create -volname "Seraphine" -srcfolder dist/dmg -ov -format UDZO dist/Seraphine.dmg
rm -rf dist/dmg

echo "==> done: $(pwd)/dist/Seraphine.dmg"
ls -lh dist/Seraphine.dmg
