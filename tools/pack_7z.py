"""Pack a directory into a .7z with its files at the ARCHIVE ROOT (py7zr).

Usage: python tools/pack_7z.py <dist-dir> <out.7z>

No nested top folder: this is the in-place updater's archive contract. Used by
tools/pack_win.ps1.
"""
import os
import sys

import py7zr

dist = sys.argv[1]
out = sys.argv[2]
if os.path.exists(out):
    os.remove(out)
with py7zr.SevenZipFile(out, "w") as z:
    for root, dirs, files in os.walk(dist):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, dist)
            z.write(full, arcname=rel)
print("packed " + out)
