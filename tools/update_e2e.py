"""Install a new release over the previous one with the real Windows updater scripts.

Usage:
  python tools/update_e2e.py --prev-package <prev> --new-package <new> [--new-tree <tree>]
      [--delta <delta>] [--leg full|delta|all]
      [--versions 1.4.6,1.4.7,...] [--all-self-updating --version X.Y.Z]
      [--script LABEL=update.py ...] [--new-script app/common/update.py]

The install is <prev> (a .7z package, extracted with py7zr like the app, or a
directory). Each run copies it to a temp dir, puts the package to apply where the
app stages it (<cfg>\\update\\package), and runs a WINDOWS_UPDATER_SCRIPT for real
(hidden PowerShell, -WaitPid 0, -NoRelaunch), like the app does. A run passes when
the script exits 0, result.json says "ok" and the install equals the new tree
(<new-tree>, default: the extracted <new>) file by file: the same files with the
same sha256; a root filemanifest.txt is ignored when it equals the new or the
previous one (the new script never merges it, older scripts copy it). With a
filemanifest.txt in the applied package, every entry must also match the install.
When <prev> has a root filemanifest.txt, every run is repeated on a copy of the
install without it and on one with a manifest that matches nothing.

- full leg: <new> applied by every distinct script of the self-updating versions
  (--versions, or --all-self-updating through tools/prev_release.py; each from
  `git show v<x>:app/common/update.py`, else a read-only
  `gh api repos/<repo>/contents/app/common/update.py?ref=v<x>`; a script that cannot
  be obtained fails the run), of every --script, and of the new script
  (--new-script, default app/common/update.py of this checkout).
- delta leg: <delta> applied by the new script, and by the previous script (the
  last version / --script given) when it carries the C6 line
  `# seraphine-updater: manifest 1`.
--leg defaults to "all" with --delta, else "full". Exit 0 when every run passes,
else 1. Scripts are read with ast, the app is never imported. When
GITHUB_STEP_SUMMARY is set, a table of the runs is appended to it.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = 'Ezeki3lRchy/Seraphine'
MARKER = '# seraphine-updater: manifest 1'
CREATE_NO_WINDOW = 0x08000000


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_delta = _load('make_delta', os.path.join(HERE, 'make_delta.py'))
check_archive = make_delta.check_archive
Mismatch = make_delta.Mismatch
MANIFEST = make_delta.MANIFEST


class E2eError(Exception):
    pass


def updaterScript(source, what):
    """WINDOWS_UPDATER_SCRIPT of an update.py source text (ast, no import)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise E2eError('%s does not parse as Python' % what) from None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'WINDOWS_UPDATER_SCRIPT' for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                value = None
            if isinstance(value, str) and value.strip():
                return value
    raise E2eError('%s has no WINDOWS_UPDATER_SCRIPT' % what)


def _run(args, cwd=ROOT):
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def tagSource(version, repo=REPO):
    """app/common/update.py of tag v<version>: the local tag, else a read-only gh api read."""
    data = _run(['git', 'show', 'v%s:app/common/update.py' % version])
    how = 'git show'
    if not data:
        data = _run(['gh', 'api', 'repos/%s/contents/app/common/update.py?ref=v%s' % (repo, version),
                     '-H', 'Accept: application/vnd.github.raw'])
        how = 'gh api'
    if not data:
        raise E2eError('cannot obtain app/common/update.py of v%s (no local tag, gh api failed)' % version)
    return data.decode('utf-8'), how


def selfUpdating(version):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'prev_release.py'), '--version', version,
                        '--all-self-updating'], capture_output=True, text=True, timeout=600)
    found = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if r.returncode != 0 or not found:
        raise E2eError('prev_release.py --all-self-updating failed: %s' % (r.stderr.strip()[:200],))
    return found


def distinct(named):
    """[(label, script)] with identical scripts merged (labels joined by '/'), in order."""
    order, labels = [], {}
    for label, script in named:
        key = hashlib.sha256(script.encode('utf-8')).hexdigest()
        if key not in labels:
            order.append((key, script))
            labels[key] = []
        labels[key].append(label)
    return [('/'.join(labels[k]), s) for k, s in order]


def installedTree(installDir, newTree, prevTree, manifestOf):
    """None when installDir is the new release, else the first difference."""
    got = make_delta.treeFiles(installDir)
    want = make_delta.treeFiles(newTree)
    want.pop(MANIFEST, None)
    if MANIFEST in got:
        mine = make_delta.sha256(got.pop(MANIFEST))
        allowed = {make_delta.sha256(p) for p in (os.path.join(newTree, MANIFEST), os.path.join(prevTree, MANIFEST))
                   if os.path.isfile(p)}
        if mine not in allowed:
            return 'the install has a %s that is neither the new nor the previous one' % MANIFEST
    for rel in sorted(set(got) | set(want), key=str.lower):
        if rel not in got:
            return 'missing after the update: %s' % rel
        if rel not in want:
            return 'not in the new release: %s' % rel
        if make_delta.sha256(got[rel]) != make_delta.sha256(want[rel]):
            return 'differs from the new release: %s' % rel
    if manifestOf and os.path.isfile(manifestOf):
        try:
            lines = check_archive.readLines(manifestOf, MANIFEST)
        except Mismatch as e:
            return 'the applied package manifest is unreadable: %s' % e
        if lines and lines[0] == check_archive.HEADER:
            for line in lines[1:]:
                m = check_archive.LINE_RE.fullmatch(line)
                if not m:
                    return 'the applied package manifest has a malformed line'
                rel = m.group(3)
                path = got.get(rel)
                if path is None:
                    return 'manifest entry %s is not in the install' % rel
                if os.path.getsize(path) != int(m.group(2)) or make_delta.sha256(path) != m.group(1):
                    return 'the install differs from the package manifest in %s' % rel
    return None


def applyOnce(script, prevTree, packageTree, newTree, version, work):
    """(ok, detail, seconds) of one updater run on copies in ``work``."""
    install = os.path.join(work, "install dir's")
    cfg = os.path.join(work, 'cfg dir')
    stage = os.path.join(cfg, 'update')
    source = os.path.join(stage, 'package')
    shutil.copytree(prevTree, install)
    shutil.copytree(packageTree, source)
    scriptPath = os.path.join(cfg, 'updater.ps1')
    with open(scriptPath, 'w', encoding='utf-8-sig') as f:
        f.write(script)
    log = os.path.join(cfg, 'updater.log')
    resultPath = os.path.join(stage, 'result.json')
    manifestCopy = os.path.join(work, 'applied-' + MANIFEST)
    if os.path.isfile(os.path.join(source, MANIFEST)):
        shutil.copyfile(os.path.join(source, MANIFEST), manifestCopy)
    args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', scriptPath, '-InstallDir', install, '-SourceDir', source, '-WaitPid', '0',
            '-LogPath', log, '-ResultPath', resultPath, '-BackupDir', os.path.join(stage, 'backup'),
            '-LockPath', os.path.join(stage, 'updater.lock'), '-TargetVersion', version, '-NoRelaunch']
    started = time.monotonic()
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=1800,
                           creationflags=CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, 'the script did not run (%s)' % type(e).__name__, time.monotonic() - started
    seconds = time.monotonic() - started
    status = None
    try:
        with open(resultPath, encoding='utf-8-sig') as f:
            status = json.load(f).get('status')
    except (OSError, ValueError, AttributeError):
        pass
    if r.returncode != 0 or status != 'ok':
        tail = ''
        if os.path.isfile(log):
            with open(log, encoding='utf-8-sig', errors='replace') as f:
                tail = ' | '.join(line.strip() for line in f.readlines()[-6:])
        return False, 'exit %d, result %s; log: %s' % (r.returncode, status, tail), seconds
    why = installedTree(install, newTree, prevTree, manifestCopy)
    if why:
        return False, why, seconds
    return True, 'result ok, %d files' % len(make_delta.treeFiles(install)), seconds


def installVariants(prevTree, scratch):
    """[(label suffix, install tree)]: the previous install as given and, when it has
    a root filemanifest.txt (a manual install from a package with one), also a copy
    without it (a self-updated install has none: the updater never merges it) and
    one whose manifest matches nothing (a stale or foreign file)."""
    out = [('', prevTree)]
    if not os.path.isfile(os.path.join(prevTree, MANIFEST)):
        return out
    without = os.path.join(scratch, 'prev without manifest')
    shutil.copytree(prevTree, without)
    os.remove(os.path.join(without, MANIFEST))
    other = os.path.join(scratch, 'prev other manifest')
    shutil.copytree(prevTree, other)
    with open(os.path.join(other, MANIFEST), 'wb') as f:
        f.write(('%s\r\n%s\t0\tSeraphine.exe\r\n' % (check_archive.HEADER, '0' * 64)).encode('utf-8-sig'))
    return out + [(' [install without filemanifest.txt]', without),
                  (' [install with a mismatching filemanifest.txt]', other)]


def _summary(rows):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    lines = ['', '| leg | script | result | seconds |', '|---|---|---|---|']
    lines += ['| %s | %s | %s | %.1f |' % (leg, label, ('OK: ' if ok else 'FAILED: ') + detail.replace('|', '/'), s)
              for leg, label, ok, detail, s in rows]
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    except OSError:
        pass


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--prev-package', required=True)
    p.add_argument('--new-package')
    p.add_argument('--new-tree')
    p.add_argument('--delta')
    p.add_argument('--leg', choices=('full', 'delta', 'all'))
    p.add_argument('--versions', default='', help='comma-separated X.Y.Z of the self-updating releases')
    p.add_argument('--all-self-updating', action='store_true')
    p.add_argument('--version', help='the new release X.Y.Z (TargetVersion; --all-self-updating looks below it)')
    p.add_argument('--script', action='append', default=[], metavar='LABEL=PATH')
    p.add_argument('--new-script', default=os.path.join(ROOT, 'app', 'common', 'update.py'))
    p.add_argument('--repo', default=REPO)
    a = p.parse_args(argv)
    leg = a.leg or ('all' if a.delta else 'full')
    legs = ['full', 'delta'] if leg == 'all' else [leg]
    if 'delta' in legs and not a.delta:
        p.error('the delta leg needs --delta')
    if 'full' in legs and not a.new_package:
        p.error('the full leg needs --new-package')
    if not (a.new_tree or a.new_package):
        p.error('give --new-package or --new-tree')

    scratch = tempfile.mkdtemp(prefix='seraphine_e2e_')
    rows = []
    try:
        legacy = []
        versions = [v.strip() for v in a.versions.split(',') if v.strip()]
        if a.all_self_updating:
            if not a.version:
                raise E2eError('--all-self-updating needs --version')
            versions = selfUpdating(a.version)
        for v in versions:
            text, how = tagSource(v, a.repo)
            legacy.append(('v' + v, updaterScript(text, 'v%s update.py (%s)' % (v, how))))
        for spec in a.script:
            label, sep, path = spec.partition('=')
            if not sep or not label or not path:
                raise E2eError('--script wants LABEL=PATH, got %r' % spec)
            try:
                with open(path, encoding='utf-8') as f:
                    text = f.read()
            except OSError as e:
                raise E2eError('cannot read script %s: %s' % (path, type(e).__name__)) from None
            legacy.append((label, updaterScript(text, path)))
        with open(a.new_script, encoding='utf-8') as f:
            newScript = updaterScript(f.read(), a.new_script)
        if MARKER not in newScript.splitlines():
            raise E2eError('the new script lacks the line %r' % MARKER)

        prevTree = make_delta.loadTree(a.prev_package, os.path.join(scratch, 'prev'))
        newPackage = make_delta.loadTree(a.new_package, os.path.join(scratch, 'new')) if a.new_package else None
        newTree = make_delta.loadTree(a.new_tree, os.path.join(scratch, 'tree')) if a.new_tree else newPackage
        target = a.version or '0.0.0'

        plan = []
        if 'full' in legs:
            for label, script in distinct(legacy + [('new', newScript)]):
                plan.append(('full', label, script, newPackage))
        if 'delta' in legs:
            delta = make_delta.loadTree(a.delta, os.path.join(scratch, 'delta'))
            scripts = [('new', newScript)]
            if legacy and MARKER in legacy[-1][1].splitlines():
                scripts.append(legacy[-1])
            elif legacy:
                print('update_e2e: delta leg: %s has no %r line; only the new script applies the delta'
                      % (legacy[-1][0], MARKER))
            for label, script in distinct(scripts):
                plan.append(('delta', label, script, delta))

        failed = 0
        variants = installVariants(prevTree, scratch)
        runs = [(legName, label + suffix, script, package, prev)
                for legName, label, script, package in plan for suffix, prev in variants]
        for i, (legName, label, script, package, prev) in enumerate(runs):
            ok, detail, seconds = applyOnce(script, prev, package, newTree, target,
                                            os.path.join(scratch, 'run%d' % i))
            rows.append((legName, label, ok, detail, seconds))
            print('update_e2e: %-5s %s: %s %s (%.1f s)' % (legName, label, 'OK' if ok else 'FAILED',
                                                          detail, seconds))
            failed += not ok
            shutil.rmtree(os.path.join(scratch, 'run%d' % i), ignore_errors=True)
        if failed:
            print('update_e2e: FAILED: %d of %d runs' % (failed, len(runs)), file=sys.stderr)
            return 1
        print('update_e2e: OK: %d runs (%s)' % (len(runs), ', '.join(sorted(set(r[0] for r in rows)))))
        return 0
    except (E2eError, Mismatch) as e:
        print('update_e2e: FAILED: %s' % e, file=sys.stderr)
        return 1
    finally:
        _summary(rows)
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
