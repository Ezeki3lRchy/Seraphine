"""Check that a Windows package extracts with py7zr to exactly the tree it was packed from.

Usage: python tools/check_archive.py <archive.7z> <tree> [--no-manifest] [--partial]

The installed app extracts updates with py7zr (app/common/update_stage.py `_extract`),
so an archive that py7zr cannot read breaks self-update for everyone. Run this right
after packing, with the py7zr pin of every self-updating version in the field (the
script needs only the standard library and py7zr). It extracts into a temp dir and
requires:

- the same relative file set as <tree>, and the same sha256 for every file;
- a root filelist.txt in the updater's format (UTF-8 BOM, CRLF, relative backslash
  paths) that lists every other file of the archive, and nothing else unless
  --partial (a delta carries only some of the listed files);
- a root filemanifest.txt (`#seraphine-manifest 1`, then `<sha256>\\t<size>\\t<path>`
  per filelist entry, in filelist order) whose size and sha256 match every listed
  file the archive holds. --no-manifest: not required (a package built without one);
  checked the same way if present.

Prints one OK line and exits 0, or prints the first difference and exits 1.
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile

import py7zr

LIST = 'filelist.txt'
MANIFEST = 'filemanifest.txt'
HEADER = '#seraphine-manifest 1'
BOM = b'\xef\xbb\xbf'
LINE_RE = re.compile(r'([0-9a-f]{64})\t(0|[1-9][0-9]*)\t(.+)', re.ASCII | re.S)


class Mismatch(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def files(root):
    """{relative backslash path: absolute path} of every file under root."""
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            full = os.path.join(d, n)
            out[os.path.relpath(full, root).replace(os.sep, '\\')] = full
    return out


def readLines(path, what):
    """Lines of a UTF-8 BOM + CRLF file (trailing CRLF after the last line)."""
    with open(path, 'rb') as f:
        raw = f.read()
    if not raw.startswith(BOM):
        raise Mismatch('%s: no UTF-8 BOM' % what)
    try:
        text = raw[len(BOM):].decode('utf-8')
    except UnicodeDecodeError:
        raise Mismatch('%s: not UTF-8' % what) from None
    if not text.endswith('\r\n'):
        raise Mismatch('%s: does not end with CRLF' % what)
    lines = text[:-2].split('\r\n')
    for i, line in enumerate(lines, 1):
        if '\r' in line or '\n' in line or chr(0xFEFF) in line:
            raise Mismatch('%s line %d: not CRLF-separated' % (what, i))
    return lines


def checkList(path):
    entries = readLines(path, LIST)
    seen = set()
    for e in entries:
        if not e or e.startswith('\\') or ':' in e or '/' in e:
            raise Mismatch('%s: not a relative backslash path: %r' % (LIST, e))
        if e.lower() in (LIST, MANIFEST):
            raise Mismatch('%s lists %s' % (LIST, e))
        if e.lower() in seen:
            raise Mismatch('%s lists %s twice' % (LIST, e))
        seen.add(e.lower())
    return entries


def checkManifest(path, entries, hashes, sizes):
    lines = readLines(path, MANIFEST)
    if lines[0] != HEADER:
        raise Mismatch('%s: first line is %r, expected %r' % (MANIFEST, lines[0][:40], HEADER))
    body = lines[1:]
    if len(body) != len(entries):
        raise Mismatch('%s: %d entries, %s has %d' % (MANIFEST, len(body), LIST, len(entries)))
    for i, (line, entry) in enumerate(zip(body, entries), 2):
        m = LINE_RE.fullmatch(line)
        if not m:
            raise Mismatch('%s line %d: not <sha256>\\t<size>\\t<path>' % (MANIFEST, i))
        digest, size, rel = m.group(1), int(m.group(2)), m.group(3)
        if rel != entry:
            raise Mismatch('%s line %d: %s, %s has %s at that place' % (MANIFEST, i, rel, LIST, entry))
        if rel in hashes:
            if size != sizes[rel]:
                raise Mismatch('%s: size of %s is %d, the file has %d' % (MANIFEST, rel, size, sizes[rel]))
            if digest != hashes[rel]:
                raise Mismatch('%s: sha256 of %s differs from the file' % (MANIFEST, rel))
    return len(body)


def check(archive, tree, requireManifest=True, partial=False):
    """Description of a matching archive; Mismatch with the first difference otherwise."""
    if not os.path.isfile(archive):
        raise Mismatch('no archive at %s' % archive)
    if not os.path.isdir(tree):
        raise Mismatch('no tree at %s' % tree)
    temp = tempfile.mkdtemp(prefix='seraphine_check_')
    try:
        try:
            with py7zr.SevenZipFile(archive, 'r') as z:
                z.extractall(path=temp)
        except Exception as e:
            raise Mismatch('py7zr %s cannot extract %s: %s: %s'
                           % (py7zr.__version__, os.path.basename(archive), type(e).__name__, e)) from None
        got, want = files(temp), files(tree)
        for rel in sorted(set(want) | set(got), key=str.lower):
            if rel not in got:
                raise Mismatch('missing from the archive: %s' % rel)
            if rel not in want:
                raise Mismatch('not in the tree: %s' % rel)
        hashes, sizes = {}, {}
        for rel in sorted(got, key=str.lower):
            hashes[rel] = sha256(got[rel])
            sizes[rel] = os.path.getsize(got[rel])
            if hashes[rel] != sha256(want[rel]):
                raise Mismatch('content differs: %s' % rel)

        if LIST not in got:
            raise Mismatch('no %s at the archive root' % LIST)
        entries = checkList(got[LIST])
        listed = set(entries)
        for rel in sorted(got, key=str.lower):
            if rel not in (LIST, MANIFEST) and rel not in listed:
                raise Mismatch('%s does not list %s' % (LIST, rel))
        if not partial:
            for e in entries:
                if e not in got:
                    raise Mismatch('%s lists %s, the archive does not hold it' % (LIST, e))

        if MANIFEST in got:
            checkManifest(got[MANIFEST], entries, hashes, sizes)
            manifest = 'manifest checked'
        elif requireManifest:
            raise Mismatch('no %s at the archive root' % MANIFEST)
        else:
            manifest = 'no manifest'
        return '%d files, %s %d entries, %s (py7zr %s)' % (
            len(got), LIST, len(entries), manifest, py7zr.__version__)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('archive')
    p.add_argument('tree')
    p.add_argument('--no-manifest', action='store_true', help='do not require filemanifest.txt')
    p.add_argument('--partial', action='store_true',
                   help='filelist.txt entries may be absent from the archive (a delta)')
    a = p.parse_args(argv)
    try:
        what = check(a.archive, a.tree, requireManifest=not a.no_manifest, partial=a.partial)
    except Mismatch as e:
        print('check_archive: FAILED %s: %s' % (a.archive, e), file=sys.stderr)
        return 1
    print('check_archive: OK %s: %s' % (a.archive, what))
    return 0


if __name__ == '__main__':
    sys.exit(main())
