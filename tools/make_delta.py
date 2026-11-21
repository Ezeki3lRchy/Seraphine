"""Build the Windows delta of a release and prove it rebuilds the new package.

Usage:
  python tools/make_delta.py <prev> <new> <out>
  python tools/make_delta.py --check <prev> <new> <delta>
  python tools/make_delta.py --verify-base <digest> <file>

<prev> / <new>: the previous and the new release, each a directory (an extracted
package or a build) or a .7z package (extracted here with py7zr, like the app).
<out> must be named ``Seraphine-<base>-to-<new>.delta`` (app/common/release_names.py).

The delta (C5) holds every file of the new filelist.txt whose sha256 or size
differs from <prev> or that <prev> lacks, plus always Seraphine.exe, filelist.txt
and filemanifest.txt (the new, complete lists). It is packed with py7zr at the
archive root. Then the equivalence check: copy <prev>, overlay the files of the
PACKED delta (extracted again), delete what the previous filelist lists and the new
one does not, and compare the result with <new> file by file (same file set incl.
filelist.txt / filemanifest.txt, same sha256); finally tools/check_archive.py's
check on the delta (--partial, manifest required). Any mismatch: exit 1 and no
output file (an existing <out> is removed first). <new> must carry a usable
filemanifest.txt that matches it: there is no delta without a manifest.

--check: the same equivalence check (and check_archive's) on an existing delta,
e.g. the release workflow's artifact before it is installed; exit 0 / 1.
--verify-base: exit 0 when <file>'s sha256 equals <digest> (``sha256:<hex>``, as
the GitHub release API reports an asset's digest), else 1 (also for a missing or
non-sha256 digest).
--fetch-base X.Y.Z <dir>: download Seraphine.7z of release vX.Y.Z into <dir>
(read-only `gh api repos/<repo>/releases/tags/vX.Y.Z` + `gh release download`) and verify it
as --verify-base does against that asset's API digest; a missing digest fails
before anything is downloaded, a mismatch removes the file. Exit 0 / 1. The release
jobs and release_win.ps1 use it for the previous package before it serves as the
install or the delta base.

Stdlib + py7zr only; never imports the app (release_names.py and check_archive.py
are loaded by path). When GITHUB_STEP_SUMMARY is set, a summary line is appended.
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import py7zr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = 'Ezeki3lRchy/Seraphine'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_names = _load('release_names', os.path.join(ROOT, 'app', 'common', 'release_names.py'))
check_archive = _load('check_archive', os.path.join(HERE, 'check_archive.py'))

LIST = check_archive.LIST
MANIFEST = check_archive.MANIFEST
EXE = 'Seraphine.exe'
Mismatch = check_archive.Mismatch
_DIGEST_RE = re.compile(r'sha256:([0-9a-fA-F]{64})', re.ASCII)


def sha256(path):
    return check_archive.sha256(path)


def treeFiles(root):
    """{relative backslash path: absolute path} of every file under root."""
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            full = os.path.join(d, n)
            out[os.path.relpath(full, root).replace(os.sep, '\\')] = full
    return out


def native(root, rel):
    return os.path.join(root, *rel.split('\\'))


def extract(archive, dest):
    try:
        with py7zr.SevenZipFile(archive, 'r') as z:
            z.extractall(path=dest)
    except Exception as e:
        raise Mismatch('py7zr cannot extract %s: %s' % (os.path.basename(archive), type(e).__name__)) from None
    return dest


def loadTree(path, scratch):
    """A directory as is; a package file extracted into ``scratch``."""
    if os.path.isdir(path):
        return path
    if os.path.isfile(path):
        os.makedirs(scratch, exist_ok=True)
        return extract(path, scratch)
    raise Mismatch('no tree or package at %s' % path)


def pack(srcDir, out):
    """py7zr archive of srcDir with its files at the archive root."""
    with py7zr.SevenZipFile(out, 'w') as z:
        for rel, full in sorted(treeFiles(srcDir).items()):
            z.write(full, arcname=rel.replace('\\', '/'))


def selectFiles(prevFiles, newFiles, entries):
    """The new filelist entries the delta must carry (changed or new against prev),
    plus Seraphine.exe and the two lists, sorted."""
    chosen = {EXE, LIST, MANIFEST}
    for e in entries:
        mine = prevFiles.get(e)
        if mine is None or os.path.getsize(mine) != os.path.getsize(newFiles[e]) \
                or sha256(mine) != sha256(newFiles[e]):
            chosen.add(e)
    return sorted(chosen, key=str.lower)


def staleEntries(prevEntries, newEntries):
    """Previous filelist entries the new one no longer has (case-insensitive, as
    the updater compares them)."""
    keep = {e.lower() for e in newEntries}
    return [e for e in prevEntries if e.lower() not in keep]


def _copyIntoDelta(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


def rebuild(prevDir, deltaDir, stale, outDir):
    """prevDir + the delta's files - stale entries, written to outDir."""
    shutil.copytree(prevDir, outDir)
    for rel, full in treeFiles(deltaDir).items():
        dst = native(outDir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(full, dst)
    for rel in stale:
        path = native(outDir, rel)
        if os.path.isfile(path):
            os.remove(path)


def compareTrees(got, want):
    """Mismatch with the first difference between two trees (file set, sha256)."""
    a, b = treeFiles(got), treeFiles(want)
    for rel in sorted(set(a) | set(b), key=str.lower):
        if rel not in a:
            raise Mismatch('previous + delta lacks %s' % rel)
        if rel not in b:
            raise Mismatch('previous + delta has %s, the new release does not' % rel)
        if sha256(a[rel]) != sha256(b[rel]):
            raise Mismatch('previous + delta differs from the new release in %s' % rel)
    return len(a)


def newLists(newDir, newFiles):
    """Entries of the new filelist; the new tree must be exactly the listed files
    plus the two lists, described by a usable manifest."""
    if LIST not in newFiles:
        raise Mismatch('the new release has no %s' % LIST)
    if MANIFEST not in newFiles:
        raise Mismatch('the new release has no %s (no delta without a manifest)' % MANIFEST)
    entries = check_archive.checkList(newFiles[LIST])
    if EXE not in entries:
        raise Mismatch('the new filelist does not list %s' % EXE)
    expected = set(entries) | {LIST, MANIFEST}
    for rel in sorted(set(newFiles) | expected, key=str.lower):
        if rel not in newFiles:
            raise Mismatch('the new filelist lists %s, the new release does not hold it' % rel)
        if rel not in expected:
            raise Mismatch('the new filelist does not list %s' % rel)
    hashes = {rel: sha256(p) for rel, p in newFiles.items()}
    sizes = {rel: os.path.getsize(p) for rel, p in newFiles.items()}
    check_archive.checkManifest(newFiles[MANIFEST], entries, hashes, sizes)
    return entries


def verifyDelta(prevDir, newDir, archive, scratch):
    """(files compared, stale entries) when prev + the archive's files - the stale
    entries == newDir; Mismatch otherwise. Also the delta's own checks
    (check_archive, --partial, manifest required)."""
    prevFiles, newFiles = treeFiles(prevDir), treeFiles(newDir)
    entries = newLists(newDir, newFiles)
    if LIST not in prevFiles:
        raise Mismatch('the previous release has no %s' % LIST)
    prevEntries = check_archive.checkList(prevFiles[LIST])
    unpacked = extract(archive, os.path.join(scratch, 'unpacked'))
    for required in (EXE, LIST, MANIFEST):
        if not os.path.isfile(native(unpacked, required)):
            raise Mismatch('the delta has no %s' % required)
    rebuilt = os.path.join(scratch, 'rebuilt')
    stale = staleEntries(prevEntries, entries)
    rebuild(prevDir, unpacked, stale, rebuilt)
    count = compareTrees(rebuilt, newDir)
    check_archive.check(archive, unpacked, requireManifest=True, partial=True)
    return count, stale


def checkExisting(prev, new, delta):
    """One-line description when an existing delta rebuilds the new release from
    the previous one; Mismatch otherwise."""
    name = os.path.basename(delta)
    if release_names.parseDelta(name) is None:
        raise Mismatch('%s is not named %s' % (name, 'Seraphine-<base>-to-<new>.delta'))
    if not os.path.isfile(delta):
        raise Mismatch('no delta at %s' % delta)
    scratch = tempfile.mkdtemp(prefix='seraphine_delta_check_')
    try:
        prevDir = loadTree(prev, os.path.join(scratch, 'prev'))
        newDir = loadTree(new, os.path.join(scratch, 'new'))
        count, stale = verifyDelta(prevDir, newDir, delta, os.path.join(scratch, 'verify'))
        return '%s: previous + delta == new release (%d files, %d stale removed)' % (name, count, len(stale))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def build(prev, new, out):
    """Write the delta to ``out``; return a one-line description. Mismatch (and no
    ``out``) on any problem."""
    name = os.path.basename(out)
    versions = release_names.parseDelta(name)
    if versions is None:
        raise Mismatch('output %s is not named %s' % (name, 'Seraphine-<base>-to-<new>.delta'))
    if os.path.lexists(out):
        os.remove(out)
    scratch = tempfile.mkdtemp(prefix='seraphine_delta_')
    try:
        prevDir = loadTree(prev, os.path.join(scratch, 'prev'))
        newDir = loadTree(new, os.path.join(scratch, 'new'))
        prevFiles, newFiles = treeFiles(prevDir), treeFiles(newDir)
        entries = newLists(newDir, newFiles)
        if LIST not in prevFiles:
            raise Mismatch('the previous release has no %s' % LIST)

        chosen = selectFiles(prevFiles, newFiles, entries)
        stage = os.path.join(scratch, 'delta')
        for rel in chosen:
            _copyIntoDelta(newFiles[rel], native(stage, rel))
        archive = os.path.join(scratch, name)
        pack(stage, archive)

        # the equivalence check runs on what was packed, extracted again
        count, stale = verifyDelta(prevDir, newDir, archive, os.path.join(scratch, 'verify'))
        check_archive.check(archive, stage, requireManifest=True, partial=True)

        size = os.path.getsize(archive)
        fullBytes = sum(os.path.getsize(p) for p in newFiles.values())
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        shutil.move(archive, out)
        changed = sum(1 for e in chosen if e in prevFiles and e not in (LIST, MANIFEST))
        added = sum(1 for e in chosen if e not in prevFiles and e not in (LIST, MANIFEST))
        return ('%s (%s -> %s): %d files in the delta (%d changed or always shipped, %d new, '
                '%d stale removed), %d B packed; the new release has %d files, %d B; '
                'previous + delta == new release (%d files)'
                % (name, versions[0], versions[1], len(chosen), changed, added, len(stale),
                   size, len(newFiles), fullBytes, count))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def verifyBase(digest, path):
    """None when path's sha256 equals the ``sha256:<hex>`` digest, else the reason."""
    m = _DIGEST_RE.fullmatch((digest or '').strip())
    if not m:
        return 'no sha256 digest given (%r)' % (digest or '')[:20]
    if not os.path.isfile(path):
        return 'no file at %s' % path
    if sha256(path) != m.group(1).lower():
        return 'sha256 of %s differs from the digest' % os.path.basename(path)
    return None


def _gh(args):
    """stdout of `gh <args>`; Mismatch on a missing gh or a non-zero exit."""
    try:
        r = subprocess.run(['gh'] + args, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Mismatch('gh %s: %s' % (args[0], type(e).__name__)) from None
    if r.returncode != 0:
        raise Mismatch('gh %s failed (exit %d)' % (' '.join(args[:2]), r.returncode))
    return r.stdout


def fetchBase(version, dest, repo=REPO, gh=_gh):
    """Download Seraphine.7z of release v<version> into dest and verify it against
    the asset's API digest; (path, digest). Mismatch otherwise (no file left)."""
    tag = 'v' + version
    try:
        # the REST API carries each asset's digest (`gh release view --json` may not)
        view = json.loads(gh(['api', 'repos/%s/releases/tags/%s' % (repo, tag)]))
        assets = view.get('assets') or []
    except (ValueError, AttributeError):
        raise Mismatch('unexpected gh output for %s' % tag) from None
    full = release_names.FULL_WIN
    found = [x for x in assets if isinstance(x, dict) and x.get('name') == full]
    if not found:
        raise Mismatch('%s has no %s' % (tag, full))
    digest = str(found[0].get('digest') or '')
    if not _DIGEST_RE.fullmatch(digest.strip()):
        raise Mismatch('%s %s has no sha256 digest in the release API' % (tag, full))
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, full)
    if os.path.lexists(path):
        os.remove(path)
    gh(['release', 'download', tag, '--repo', repo, '--pattern', full, '--dir', dest, '--clobber'])
    why = verifyBase(digest, path)
    if why:
        if os.path.lexists(path):
            os.remove(path)
        raise Mismatch('%s %s: %s' % (tag, full, why))
    return path, digest


def _summary(line):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass


def main(argv=None, gh=_gh):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('paths', nargs='*', metavar='prev new out')
    p.add_argument('--verify-base', nargs=2, metavar=('DIGEST', 'FILE'))
    p.add_argument('--fetch-base', nargs=2, metavar=('VERSION', 'DIR'))
    p.add_argument('--check', action='store_true',
                   help='<prev> <new> <delta>: check an existing delta instead of building one')
    p.add_argument('--repo', default=REPO)
    a = p.parse_args(argv)
    if a.fetch_base:
        if a.paths or a.verify_base:
            p.error('--fetch-base takes no other arguments')
        try:
            path, digest = fetchBase(a.fetch_base[0], a.fetch_base[1], a.repo, gh)
        except Mismatch as e:
            print('make_delta: base FAILED: %s' % e, file=sys.stderr)
            return 1
        print('make_delta: base OK: %s matches its API digest %s' % (path, digest))
        return 0
    if a.verify_base:
        if a.paths:
            p.error('--verify-base takes no other arguments')
        why = verifyBase(*a.verify_base)
        if why:
            print('make_delta: base FAILED: %s' % why, file=sys.stderr)
            return 1
        print('make_delta: base OK: %s matches its digest' % os.path.basename(a.verify_base[1]))
        return 0
    if len(a.paths) != 3:
        p.error('expected <prev> <new> <out>')
    prev, new, out = a.paths
    if a.check:
        try:
            what = checkExisting(prev, new, out)
        except Mismatch as e:
            print('make_delta: check FAILED: %s' % e, file=sys.stderr)
            return 1
        print('make_delta: check OK %s' % what)
        return 0
    try:
        what = build(prev, new, out)
    except Mismatch as e:
        if os.path.lexists(out):
            os.remove(out)
        print('make_delta: FAILED: %s' % e, file=sys.stderr)
        _summary('- delta: **not built** (%s)' % e)
        return 1
    print('make_delta: OK %s' % what)
    _summary('- delta: %s' % what)
    return 0


if __name__ == '__main__':
    sys.exit(main())
