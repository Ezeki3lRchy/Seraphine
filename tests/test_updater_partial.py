"""The Windows updater script with a package manifest (filemanifest.txt, C4):
step 4b (check the package against the install, prune what the install already
has), the post-merge verify, the escape hatch (a package without a manifest takes
the path of the v1.4.8 script) and the legacy script snapshots (C9).

The script runs for real (PowerShell hidden, -NoRelaunch, the PID of a process
that has already exited) against temp trees whose paths contain a space, an
apostrophe and non-ASCII characters.
"""
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from app.common import update
from tests.test_windows_updater_script import (CREATE_NO_WINDOW, _deadPid, _filelist,
                                               _lock, _read, _write)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'legacy')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# C9: raw `git show` bytes (LF) of app/common/update.py
LEGACY_SCRIPTS = {
    'update_v1_4_6.py.txt': 'be01edd0264cca0b937b4a6437a8b92fc6cd735280749cae05fe2c404df34464',
    'update_v1_4_8.py.txt': '1b1aed03114ff366f6d9056b5fc1e25ecaa0c6c278c892478ac3ad51cd19dbcb',
}

MANIFEST_HEADER = '#seraphine-manifest 1'

# the install before the update ("base") and the complete new version ("target")
BASE = {
    'Seraphine.exe': b'exe-1',
    '_internal\\same.dll': b'same-bytes',
    '_internal\\sub\\keep.pyd': b'keep-unchanged',
    '_internal\\changed.dll': b'AAAA',
    '_internal\\gone.dll': b'gone',
}
TARGET = {
    'Seraphine.exe': b'exe-2',
    '_internal\\same.dll': b'same-bytes',
    '_internal\\sub\\keep.pyd': b'keep-unchanged',
    '_internal\\changed.dll': b'AAAB',  # same size, one byte changed
    '_internal\\new\\added.dll': b'added',
}
# in no filelist: never touched
USER = {'_internal\\app\\resource\\game\\cache.png': b'user-cache'}

OLD_MTIME = 1500000000
PKG_MTIME = 1600000000
SAME_MTIME = 1700000000


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _manifestText(files, header=MANIFEST_HEADER, override=None):
    # C4: UTF-8 BOM, CRLF, header, then "<sha256>\t<size>\t<path>" in filelist order
    override = override or {}
    lines = [header] + ['%s\t%d\t%s' % (override.get(rel, _sha(data)), len(data), rel)
                        for rel, data in sorted(files.items())]
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8-sig')


def _tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            out[os.path.relpath(path, root)] = _read(path)
    return out


def _legacyScript(name):
    """WINDOWS_UPDATER_SCRIPT of a legacy snapshot, read with ast (no import)."""
    tree = ast.parse(_read(os.path.join(FIXTURES, name)).decode('utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'WINDOWS_UPDATER_SCRIPT' for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError('no WINDOWS_UPDATER_SCRIPT in ' + name)


def _powershell(args, timeout=180, env=None):
    return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                           '-ExecutionPolicy', 'Bypass'] + list(args),
                          capture_output=True, text=True, timeout=timeout, env=env,
                          creationflags=CREATE_NO_WINDOW)


class Sandbox:
    """An install + stage dir; packages are written into stage\\package."""

    def __init__(self, root):
        self.root = root
        self.install = os.path.join(root, "Sera phine's \u5b89\u88c5 \u00fc")
        self.cfg = os.path.join(root, "cfg dir's \u00e9")
        self.stage = os.path.join(self.cfg, 'update')
        self.source = os.path.join(self.stage, 'package')
        self.script = os.path.join(self.cfg, 'updater.ps1')
        self.log = os.path.join(self.cfg, 'updater.log')
        self.resultPath = os.path.join(self.stage, 'result.json')
        self.backup = os.path.join(self.stage, 'backup')
        self.lockPath = os.path.join(self.stage, 'updater.lock')
        self.logText = ''

    def p(self, rel):
        return os.path.join(self.install, rel)

    def s(self, rel):
        return os.path.join(self.source, rel)

    def writeInstall(self, files=BASE):
        for rel, data in files.items():
            _write(self.p(rel), data)
        for rel, data in USER.items():
            _write(self.p(rel), data)
        _filelist(self.p('filelist.txt'), sorted(files))
        for rel in ('_internal\\same.dll', '_internal\\sub\\keep.pyd'):
            os.utime(self.p(rel), (OLD_MTIME, OLD_MTIME))
        os.utime(self.p('_internal\\changed.dll'), (SAME_MTIME, SAME_MTIME))

    def writePackage(self, files, listed=TARGET, manifest=True, manifestBytes=None):
        for rel, data in files.items():
            _write(self.s(rel), data)
            os.utime(self.s(rel), (PKG_MTIME, PKG_MTIME))
        if '_internal\\changed.dll' in files:
            os.utime(self.s('_internal\\changed.dll'), (SAME_MTIME, SAME_MTIME))
        _filelist(self.s('filelist.txt'), sorted(listed))
        if manifestBytes is not None:
            _write(self.s('filemanifest.txt'), manifestBytes)
        elif manifest:
            _write(self.s('filemanifest.txt'), _manifestText(listed))

    def run(self, scriptText=None, extra=()):
        os.makedirs(self.cfg, exist_ok=True)
        if scriptText is None:
            update.writeWindowsUpdaterScript(self.script)
        else:
            with open(self.script, 'w', encoding='utf-8-sig') as f:
                f.write(scriptText)
        args = update.windowsUpdaterArgs(
            self.script, self.install, self.source, _deadPid(), self.log, noRelaunch=True,
            resultPath=self.resultPath, backupDir=self.backup, targetVersion='9.9.9',
            lockPath=self.lockPath) + list(extra)
        r = subprocess.run(args, capture_output=True, text=True, timeout=180,
                           creationflags=CREATE_NO_WINDOW)
        self.logText = _read(self.log).decode('utf-8-sig', 'replace') \
            if os.path.exists(self.log) else ''
        return r

    def result(self):
        with open(self.resultPath, encoding='utf-8-sig') as f:
            return json.load(f)


def _delta(base, target):
    """C5 delta contents: changed + new files, always Seraphine.exe."""
    files = {rel: data for rel, data in target.items() if base.get(rel) != data}
    files['Seraphine.exe'] = target['Seraphine.exe']
    return files


def _expectedFinal(target=TARGET):
    # the package's filemanifest.txt is never merged into the install
    tree = dict(target)
    tree.update(USER)
    tree['filelist.txt'] = ('\r\n'.join(sorted(target)) + '\r\n').encode('utf-8-sig')
    return tree


class LegacySnapshotTest(unittest.TestCase):
    """C9: the committed snapshots are the exact LF bytes of the tagged files."""

    def test_snapshots_are_the_recorded_bytes(self):
        for name, sha in LEGACY_SCRIPTS.items():
            with self.subTest(name):
                data = _read(os.path.join(FIXTURES, name))
                self.assertNotIn(b'\r', data, 'CRLF checkout: see .gitattributes')
                self.assertEqual(hashlib.sha256(data).hexdigest(), sha)
                script = _legacyScript(name)
                self.assertIn('param(', script)
                # no legacy script knows the manifest
                self.assertNotIn('# seraphine-updater: manifest 1', script)

    def test_gitattributes_keeps_the_snapshots_binary(self):
        self.assertEqual(_read(os.path.join(REPO, '.gitattributes')).strip(),
                         b'tests/fixtures/legacy/* -text')

    def test_new_script_announces_the_manifest(self):
        # C6: how update_e2e.py recognises a manifest-capable script
        self.assertIn('# seraphine-updater: manifest 1',
                      update.WINDOWS_UPDATER_SCRIPT.splitlines())


@unittest.skipUnless(sys.platform == 'win32', 'Windows updater')
class UpdaterPartialTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_part_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.box = Sandbox(self.root)
        self.box.writeInstall()

    def assertOk(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + self.box.logText)
        self.assertEqual((self.box.result()['status'], self.box.result()['code']), ('ok', 0))

    def assertFailed9(self, r, before, packageBefore):
        self.assertEqual(r.returncode, 9, r.stdout + r.stderr + self.box.logText)
        self.assertEqual((self.box.result()['status'], self.box.result()['code']),
                         ('failed:9', 9))
        self.assertIn('nothing changed', self.box.logText)
        self.assertEqual(_tree(self.box.install), before)
        # nothing pruned, nothing backed up
        self.assertEqual(_tree(self.box.source), packageBefore)
        for left in (self.box.backup, self.box.backup + '.meta', self.box.backup + '.added.txt'):
            self.assertFalse(os.path.exists(left), left)

    def assertPhaseLines(self, log):
        for phase in ('manifest', 'backup', 'merge', 'verify', 'removal'):
            self.assertRegex(log, r'phase %s: \d+\.\d\d s, \d+ ' % phase)
        self.assertRegex(log, r'deadline: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d')
        self.assertLess(log.index('phase manifest'), log.index('deadline:'))
        self.assertLess(log.index('deadline:'), log.index('phase backup'))

    def test_full_package_backs_up_and_copies_only_what_differs(self):
        self.box.writePackage(TARGET)
        r = self.box.run()
        self.assertOk(r)
        self.assertEqual(_tree(self.box.install), _expectedFinal())
        # the manifest describes the package; no copy of it is left in the install
        self.assertFalse(os.path.exists(self.box.p('filemanifest.txt')))
        # unchanged files: not copied (the install keeps its own mtime) ...
        for rel in ('_internal\\same.dll', '_internal\\sub\\keep.pyd'):
            self.assertEqual(int(os.path.getmtime(self.box.p(rel))), OLD_MTIME, rel)
        # ... and not backed up: only the exe, the changed dll and filelist.txt were
        self.assertIn('backed up 3 files', self.box.logText)
        self.assertRegex(self.box.logText, r'phase manifest: \d+\.\d\d s, 5 checked, 2 pruned')
        self.assertRegex(self.box.logText, r'phase removal: \d+\.\d\d s, 1 files')
        self.assertIn('removed stale: _internal\\gone.dll', self.box.logText)
        self.assertPhaseLines(self.box.logText)
        self.assertFalse(os.path.exists(self.box.source))
        self.assertFalse(os.path.exists(self.box.backup))

    def test_same_size_one_byte_change_is_copied(self):
        # same size and mtime as the install's file: a size-only (or robocopy
        # default) shortcut would skip it
        self.assertEqual(len(BASE['_internal\\changed.dll']), len(TARGET['_internal\\changed.dll']))
        self.box.writePackage(_delta(BASE, TARGET))
        r = self.box.run()
        self.assertOk(r)
        self.assertEqual(_read(self.box.p('_internal\\changed.dll')), b'AAAB')

    def test_delta_package_gives_the_full_target(self):
        delta = _delta(BASE, TARGET)
        self.assertEqual(sorted(delta), ['Seraphine.exe', '_internal\\changed.dll',
                                         '_internal\\new\\added.dll'])
        self.box.writePackage(delta)
        r = self.box.run()
        self.assertOk(r)
        self.assertEqual(_tree(self.box.install), _expectedFinal())
        self.assertPhaseLines(self.box.logText)
        self.assertRegex(self.box.logText, r'phase manifest: \d+\.\d\d s, 5 checked, 0 pruned')
        self.assertRegex(self.box.logText, r'phase verify: \d+\.\d\d s, 3 files')

    def test_stale_file_cases(self):
        # old list only -> removed; in both lists, absent from the delta -> kept;
        # unlisted user file -> untouched; behind a reparse point -> skipped
        outsideDir = os.path.join(self.root, 'outside_dir')
        _write(os.path.join(outsideDir, 'victim.txt'), b'victim')
        link = self.box.p('_internal\\link')
        mk = subprocess.run(['cmd', '/c', 'mklink', '/J', link, outsideDir],
                            capture_output=True, creationflags=CREATE_NO_WINDOW)
        self.assertEqual(mk.returncode, 0, mk.stderr)
        self.addCleanup(lambda: os.path.isdir(link) and os.rmdir(link))
        _filelist(self.box.p('filelist.txt'), sorted(BASE) + ['_internal\\link\\victim.txt'])
        self.box.writePackage(_delta(BASE, TARGET))
        r = self.box.run()
        self.assertOk(r)
        self.assertFalse(os.path.exists(self.box.p('_internal\\gone.dll')))
        self.assertEqual(_read(self.box.p('_internal\\sub\\keep.pyd')), b'keep-unchanged')
        self.assertEqual(int(os.path.getmtime(self.box.p('_internal\\sub\\keep.pyd'))), OLD_MTIME)
        self.assertEqual(_read(self.box.p('_internal\\same.dll')), b'same-bytes')
        for rel, data in USER.items():
            self.assertEqual(_read(self.box.p(rel)), data)
        self.assertEqual(_read(os.path.join(outsideDir, 'victim.txt')), b'victim')
        self.assertIn('skip (reparse point)', self.box.logText)

    def test_pruned_unchanged_file_is_kept(self):
        self.box.writePackage(TARGET)
        r = self.box.run()
        self.assertOk(r)
        self.assertIn('2 pruned', self.box.logText)
        self.assertEqual(_read(self.box.p('_internal\\same.dll')), b'same-bytes')
        self.assertEqual(_read(self.box.p('_internal\\sub\\keep.pyd')), b'keep-unchanged')

    def test_base_mismatch_fails_9_and_changes_nothing(self):
        # a file the delta does not ship differs in the install (hand edit, AV)
        _write(self.box.p('_internal\\sub\\keep.pyd'), b'keep-hand-edited')
        self.box.writePackage(_delta(BASE, TARGET))
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        r = self.box.run()
        self.assertFailed9(r, before, pkg)
        self.assertNotIn('phase backup', self.box.logText)

    def test_wrong_hash_for_a_file_absent_from_the_package_fails_9(self):
        manifest = _manifestText(TARGET, override={'_internal\\same.dll': '0' * 64})
        self.box.writePackage(_delta(BASE, TARGET), manifestBytes=manifest)
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        self.assertFailed9(self.box.run(), before, pkg)

    def test_missing_installed_file_absent_from_the_package_fails_9(self):
        os.remove(self.box.p('_internal\\same.dll'))
        self.box.writePackage(_delta(BASE, TARGET))
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        self.assertFailed9(self.box.run(), before, pkg)

    def test_corrupt_package_file_fails_9_and_changes_nothing(self):
        self.box.writePackage(TARGET)
        _write(self.box.s('_internal\\changed.dll'), b'AAAC')  # != its manifest hash
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        r = self.box.run()
        self.assertFailed9(r, before, pkg)
        self.assertIn("package's _internal\\changed.dll differs", self.box.logText)

    def test_corrupt_package_exe_fails_9_even_when_the_install_has_it(self):
        # Seraphine.exe is never pruned, so its package copy is always checked
        target = dict(TARGET, **{'Seraphine.exe': b'exe-1'})
        self.box.writePackage(target, listed=target)
        _write(self.box.s('Seraphine.exe'), b'exe-X')
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        self.assertFailed9(self.box.run(), before, pkg)

    def test_refused_package_is_not_pruned(self):
        # same.dll is a prune candidate (the install has it) and is checked before
        # keep.pyd, which differs in the install and is not in the package: the
        # refusal must leave same.dll in the package (pruning only after every check)
        files = _delta(BASE, TARGET)
        files['_internal\\same.dll'] = TARGET['_internal\\same.dll']
        self.assertLess(sorted(TARGET).index('_internal\\same.dll'),
                        sorted(TARGET).index('_internal\\sub\\keep.pyd'))
        _write(self.box.p('_internal\\sub\\keep.pyd'), b'keep-hand-edited')
        self.box.writePackage(files)
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        r = self.box.run()
        self.assertFailed9(r, before, pkg)
        self.assertEqual(_read(self.box.s('_internal\\same.dll')), b'same-bytes')

    def test_manifest_entry_not_in_the_new_filelist_fails_9(self):
        # gone.dll is in the install (old list) and in the package, so it would be
        # pruned, then removed as stale: the manifest must match the filelist
        listed = dict(TARGET, **{'_internal\\gone.dll': BASE['_internal\\gone.dll']})
        self.box.writePackage(listed, listed=TARGET, manifestBytes=_manifestText(listed))
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        r = self.box.run()
        self.assertFailed9(r, before, pkg)
        self.assertIn('gone.dll is not in the new filelist', self.box.logText)

    def test_duplicate_manifest_entry_fails_9(self):
        # first line: the old hash (the install has it -> prune), second line the
        # new one (the package has it): the install would keep the old bytes
        text = _manifestText(TARGET).decode('utf-8-sig')
        old = '%s\t4\t_internal\\changed.dll' % _sha(BASE['_internal\\changed.dll'])
        text = text.replace('\r\n', '\r\n' + old + '\r\n', 1)
        self.box.writePackage(TARGET, manifestBytes=text.encode('utf-8-sig'))
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        r = self.box.run()
        self.assertFailed9(r, before, pkg)
        self.assertIn('changed.dll listed twice', self.box.logText)

    def test_unreadable_manifest_fails_9(self):
        self.box.writePackage(_delta(BASE, TARGET))
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        unlock = _lock(self.box.s('filemanifest.txt'), shareRead=False)
        try:
            r = self.box.run()
        finally:
            unlock()
        self.assertFailed9(r, before, pkg)
        self.assertIn('malformed (unreadable', self.box.logText)

    def test_manifest_paths_are_trimmed_like_filelist_entries(self):
        lines = _manifestText(TARGET).decode('utf-8-sig').split('\r\n')
        # the header stays exact; every entry's path gets surrounding blanks
        lines = lines[:1] + [ln.replace('\t', '\t ').replace('\t ', '\t', 1) + '  ' if ln else ln
                             for ln in lines[1:]]
        self.assertTrue(all(ln.endswith('  ') for ln in lines[1:] if ln))
        self.assertTrue(all('\t ' in ln for ln in lines[1:] if ln))
        self.box.writePackage(_delta(BASE, TARGET),
                              manifestBytes='\r\n'.join(lines).encode('utf-8-sig'))
        r = self.box.run()
        self.assertOk(r)
        self.assertIn('phase manifest', self.box.logText)
        self.assertEqual(_tree(self.box.install), _expectedFinal())

    def test_malformed_manifest_line_fails_9(self):
        manifest = _manifestText(TARGET)[:-2] + b'\r\nnot-a-hash\t5\t_internal\\x.dll\r\n'
        self.box.writePackage(TARGET, manifestBytes=manifest)
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        r = self.box.run()
        self.assertFailed9(r, before, pkg)
        self.assertIn('malformed', self.box.logText)

    def test_pending_matching_backup_is_restored_before_the_manifest_check(self):
        # an earlier run merged half of the target and its restore did not finish:
        # 4b must see the restored base, not the half-merged install (else it would
        # prune changed.dll against the half-merged copy and the restore would put
        # the old one back)
        b = self.box
        _write(b.p('Seraphine.exe'), TARGET['Seraphine.exe'])
        _write(b.p('_internal\\changed.dll'), TARGET['_internal\\changed.dll'])
        _write(b.p('_internal\\new\\added.dll'), TARGET['_internal\\new\\added.dll'])
        _write(os.path.join(b.backup, 'Seraphine.exe'), BASE['Seraphine.exe'])
        _write(os.path.join(b.backup, '_internal', 'changed.dll'), BASE['_internal\\changed.dll'])
        with open(b.backup + '.added.txt', 'w', encoding='utf-8') as f:
            f.write(b.p('_internal\\new\\added.dll') + '\n')
        with open(b.backup + '.meta', 'w', encoding='utf-8-sig') as f:
            json.dump({'preExe': _sha(BASE['Seraphine.exe']).upper(),
                       'pkgExe': _sha(TARGET['Seraphine.exe']).upper(), 'version': '9.9.9'}, f)
        b.writePackage(_delta(BASE, TARGET))
        r = b.run()
        self.assertOk(r)
        self.assertLess(b.logText.index('earlier restore completed'),
                        b.logText.index('phase manifest'))
        self.assertEqual(_tree(b.install), _expectedFinal())

    def test_merge_failure_after_pruning_restores_and_a_retry_succeeds(self):
        b = self.box
        b.writePackage(TARGET)
        original = _tree(b.install)
        # changed.dll can be read (hashed, backed up) but not written
        unlock = _lock(b.p('_internal\\changed.dll'), shareRead=True)
        try:
            r = b.run(extra=['-MergeAttempts', '2'])
        finally:
            unlock()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr + b.logText)
        self.assertEqual(b.result()['status'], 'restored')
        self.assertEqual(_tree(b.install), original)
        # the stage was pruned: the unchanged files are gone from it
        self.assertFalse(os.path.exists(b.s('_internal\\same.dll')))
        self.assertTrue(os.path.isfile(b.s('Seraphine.exe')))
        # retry of that pruned stage (the app offers it after a restore)
        r = b.run()
        self.assertOk(r)
        self.assertEqual(_tree(b.install), _expectedFinal())

    def test_unknown_manifest_header_takes_todays_path(self):
        # a newer manifest format is "no usable manifest": its (here wrong)
        # hashes are never read and nothing is pruned or verified
        manifest = _manifestText(TARGET, header='#seraphine-manifest 2',
                                 override={rel: '0' * 64 for rel in TARGET})
        self.box.writePackage(TARGET, manifestBytes=manifest)
        r = self.box.run()
        self.assertOk(r)
        self.assertEqual(_tree(self.box.install), _expectedFinal())
        self.assertNotIn('phase manifest', self.box.logText)
        self.assertNotIn('phase verify', self.box.logText)
        self.assertIn('backed up 5 files', self.box.logText)

    def test_new_list_entry_missing_from_a_package_without_manifest_fails_9(self):
        files = dict(TARGET)
        del files['_internal\\new\\added.dll']
        self.box.writePackage(files, manifest=False)
        before, pkg = _tree(self.box.install), _tree(self.box.source)
        r = self.box.run()
        self.assertFailed9(r, before, pkg)
        self.assertIn('added.dll (new filelist) is not in the package', self.box.logText)


@unittest.skipUnless(sys.platform == 'win32', 'Windows updater')
class EscapeHatchTest(unittest.TestCase):
    """P4: a full package without filemanifest.txt, run by the new script and by
    the v1.4.8 script on identical copies, ends identically."""

    def test_package_without_manifest_matches_the_v1_4_8_script(self):
        root = tempfile.mkdtemp(prefix='seraphine_hatch_')
        self.addCleanup(shutil.rmtree, root, True)
        boxes = {}
        for name in ('new', 'v1.4.8'):
            box = Sandbox(os.path.join(root, name))
            box.writeInstall()
            box.writePackage(TARGET, manifest=False)
            boxes[name] = box
        self.assertEqual(_tree(boxes['new'].install), _tree(boxes['v1.4.8'].install))
        self.assertEqual(_tree(boxes['new'].source), _tree(boxes['v1.4.8'].source))

        rNew = boxes['new'].run()
        rOld = boxes['v1.4.8'].run(_legacyScript('update_v1_4_8.py.txt'))
        self.assertEqual(rNew.returncode, rOld.returncode, boxes['new'].logText)
        self.assertEqual(rNew.returncode, 0, boxes['new'].logText)
        new, old = boxes['new'], boxes['v1.4.8']
        self.assertEqual(_tree(new.install), _tree(old.install))
        self.assertEqual(_tree(new.install), _expectedFinal())
        self.assertEqual((new.result()['status'], new.result()['code']),
                         (old.result()['status'], old.result()['code']))
        self.assertEqual(os.path.exists(new.source), os.path.exists(old.source))
        self.assertEqual(os.path.exists(new.backup), os.path.exists(old.backup))
        # neither 4b's manifest pass nor the post-merge verify ran
        self.assertNotIn('phase manifest', new.logText)
        self.assertNotIn('phase verify', new.logText)
        self.assertNotIn('deadline:', new.logText)
        self.assertIn('phase merge', new.logText)


# Loads only the function definitions of a script and calls Test-MergedFiles or
# Test-FileMatches. -Probe: report whether that file is locked right before the
# call; -Marker: written right before the call (the test then releases its lock).
_CALL_FUNCTION = r'''
param([string]$ScriptPath, [string]$Function, [string]$From = '', [string]$To = '',
      [string]$ExpectedJson = '', [string]$Path = '', [long]$Size = 0, [string]$Sha = '',
      [string]$Probe = '', [string]$Marker = '')
$ErrorActionPreference = 'Stop'
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    [System.IO.File]::ReadAllText($ScriptPath), [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'parse errors' }
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($d in $defs) { . ([ScriptBlock]::Create($d.Extent.Text)) }
$expected = @{}
if ($ExpectedJson) {
    foreach ($p in (Get-Content -LiteralPath $ExpectedJson -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties) {
        $expected[$p.Name] = $p.Value
    }
}
if ($Probe) {
    try { ([System.IO.File]::OpenRead($Probe)).Dispose(); 'LOCKED=False' } catch { 'LOCKED=True' }
}
if ($Marker) { [System.IO.File]::WriteAllText($Marker, 'go') }
$watch = [System.Diagnostics.Stopwatch]::StartNew()
if ($Function -eq 'Test-MergedFiles') { $ok = Test-MergedFiles $From $To $expected }
else { $ok = Test-FileMatches $Path $Size $Sha }
"ELAPSED_MS=$([int]$watch.Elapsed.TotalMilliseconds)"
"RESULT=$ok"
'''

# Where the post-merge verify sits in the script (PowerShell AST), as JSON.
_STRUCTURE = r'''
param([string]$ScriptPath)
$ErrorActionPreference = 'Stop'
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    [System.IO.File]::ReadAllText($ScriptPath), [ref]$null, [ref]$errors)
$commands = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true))
$assigns = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true))
$fors = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.ForStatementAst] }, $true))
function Get-Assign([string]$Left, [string]$Right) {
    @($assigns | Where-Object { $_.Left.Extent.Text -eq $Left -and $_.Right.Extent.Text -eq $Right } |
      ForEach-Object { $_.Extent.StartOffset })
}
$loopEnds = @($fors | Where-Object {
    $body = $_.Body
    @($commands | Where-Object { $_.GetCommandName() -eq 'Invoke-Robocopy' -and
        $_.Extent.StartOffset -ge $body.Extent.StartOffset -and $_.Extent.EndOffset -le $body.Extent.EndOffset }).Count
} | ForEach-Object { $_.Extent.EndOffset })
$calls = @()
foreach ($c in @($commands | Where-Object { $_.GetCommandName() -eq 'Test-MergedFiles' })) {
    $inFunction = $false
    $restores = $false
    $tries = 0
    $p = $c.Parent
    while ($p) {
        if ($p -is [System.Management.Automation.Language.FunctionDefinitionAst]) { $inFunction = $true }
        if ($p -is [System.Management.Automation.Language.TryStatementAst]) { $tries++ }
        if ($p -is [System.Management.Automation.Language.IfStatementAst] -and -not $restores) {
            foreach ($clause in $p.Clauses) {
                $cond = $clause.Item1
                if ($c.Extent.StartOffset -ge $cond.Extent.StartOffset -and $c.Extent.EndOffset -le $cond.Extent.EndOffset -and
                    $cond.Extent.Text.TrimStart().StartsWith('-not')) {
                    $body = $clause.Item2
                    if (@($commands | Where-Object { $_.GetCommandName() -eq 'Restore-Install' -and
                            $_.Extent.StartOffset -ge $body.Extent.StartOffset -and
                            $_.Extent.EndOffset -le $body.Extent.EndOffset }).Count) { $restores = $true }
                }
            }
        }
        $p = $p.Parent
    }
    $calls += @{ start = $c.Extent.StartOffset; end = $c.Extent.EndOffset; inFunction = $inFunction;
                 restores = $restores; tries = $tries }
}
$params = @()
if ($ast.ParamBlock) { $params = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath }) }
$out = [ordered]@{
    parseErrors = @($errors).Count
    calls = $calls
    loopEnds = $loopEnds
    mergeStartedTrue = @(Get-Assign '$script:mergeStarted' '$true')
    mergeStartedFalse = @(Get-Assign '$script:mergeStarted' '$false')
    mergeDoneTrue = @(Get-Assign '$script:mergeDone' '$true')
    params = $params
}
$out | ConvertTo-Json -Depth 5 -Compress
'''


def _structureProblems(info):
    """Every way the post-merge verify could be swallowed or skipped."""
    problems = []
    if info['parseErrors']:
        problems.append('the script does not parse')
    # the initialisation at the top ($script:mergeStarted = $false) is not the
    # end of the window
    if len(info['mergeStartedTrue']) == 1:
        info = dict(info, mergeStartedFalse=[
            o for o in info['mergeStartedFalse'] if o > info['mergeStartedTrue'][0]])
    for key in ('loopEnds', 'mergeStartedTrue', 'mergeStartedFalse', 'mergeDoneTrue'):
        if len(info[key]) != 1:
            problems.append('%s: %r' % (key, info[key]))
    if any(p.lower() == 'aftermergehook' for p in info['params']):
        problems.append('the script has an AfterMergeHook parameter')
    calls = info['calls']
    if len(calls) != 1:
        problems.append('Test-MergedFiles is called %d times' % len(calls))
    if problems:
        return problems
    c = calls[0]
    if c['inFunction']:
        problems.append('called inside a function')
    if not info['loopEnds'][0] <= c['start']:
        problems.append('not after the robocopy merge loop')
    if not c['end'] <= info['mergeDoneTrue'][0]:
        problems.append('not before $script:mergeDone = $true')
    if not info['mergeStartedTrue'][0] < c['start'] or not c['end'] <= info['mergeStartedFalse'][0]:
        problems.append('outside the mergeStarted window')
    if not c['restores']:
        problems.append('a failed verify does not call Restore-Install')
    # only the script's top-level try: a nested try/catch could swallow the
    # verify (or Restore-Install's exit) and carry on to "merge done"
    if c['tries'] != 1:
        problems.append('inside %d try statements, not only the top-level one' % c['tries'])
    return problems


@unittest.skipUnless(sys.platform == 'win32', 'Windows updater')
class PostMergeVerifyTest(unittest.TestCase):
    """The post-merge verify has no test hook in the production script; it is
    proven at function level (wrong hash, read error) and by its place in the
    script (it can only fail into Restore-Install, never into the outer catch's
    "merge done" success)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_verify_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.script = os.path.join(self.root, "updater's \u00e9.ps1")
        update.writeWindowsUpdaterScript(self.script)

    def helper(self, name, text):
        path = os.path.join(self.root, name)
        with open(path, 'w', encoding='utf-8-sig') as f:
            f.write(text)
        return path

    def call(self, args, hold=None, brief=None):
        """Run _CALL_FUNCTION. hold: a file locked (FileShare.None) for the whole
        call; brief: a file locked until 0.3 s after the function starts (inside
        the retry window of 3 attempts 0.5 s apart). Returns the KEY=value lines."""
        args = ['-File', self.helper('call.ps1', _CALL_FUNCTION), '-ScriptPath', self.script] + args
        locked = hold or brief
        unlock = _lock(locked, shareRead=False) if locked else None
        try:
            if brief:
                marker = os.path.join(self.root, 'started-%d' % len(os.listdir(self.root)))
                p = subprocess.Popen(
                    ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass']
                    + args + ['-Probe', brief, '-Marker', marker],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    creationflags=CREATE_NO_WINDOW)
                deadline = time.monotonic() + 120
                while not os.path.exists(marker) and p.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                time.sleep(0.3)
                unlock()
                unlock = None
                out, err = p.communicate(timeout=120)
                code = p.returncode
            else:
                r = _powershell(args)
                out, err, code = r.stdout, r.stderr, r.returncode
        finally:
            if unlock:
                unlock()
        self.assertEqual(code, 0, out + err)
        values = dict(ln.split('=', 1) for ln in out.splitlines() if '=' in ln)
        self.assertIn('RESULT', values, out + err)
        return values

    def verify(self, fromFiles, toFiles, expected, hold=None, brief=None):
        base = tempfile.mkdtemp(prefix='case ', dir=self.root)
        src, dst = os.path.join(base, 'pack age'), os.path.join(base, "inst'all")
        for rel, data in fromFiles.items():
            _write(os.path.join(src, rel), data)
        for rel, data in toFiles.items():
            _write(os.path.join(dst, rel), data)
        exp = os.path.join(base, 'expected.json')
        with open(exp, 'w', encoding='utf-8') as f:
            json.dump({rel.lower(): sha for rel, sha in expected.items()}, f)
        return self.call(['-Function', 'Test-MergedFiles', '-From', src, '-To', dst,
                          '-ExpectedJson', exp],
                         hold=hold and os.path.join(dst, hold),
                         brief=brief and os.path.join(dst, brief))

    def test_function_level(self):
        files = {'a.dll': b'new-a', 'sub\\b.dll': b'new-b', 'extra.txt': b'no entry'}
        expected = {'a.dll': _sha(b'new-a').upper(), 'sub\\b.dll': _sha(b'new-b')}
        merged = dict(files, **{'extra.txt': b'differs, but has no manifest entry'})
        self.assertEqual(self.verify(files, merged, expected)['RESULT'], 'True')
        # a wrong hash
        self.assertEqual(self.verify(files, dict(merged, **{'sub\\b.dll': b'new-B'}),
                                     expected)['RESULT'], 'False')
        # a merged file that is missing
        gone = dict(merged)
        del gone['a.dll']
        self.assertEqual(self.verify(files, gone, expected)['RESULT'], 'False')
        # a read error that lasts (file held open with FileShare.None, e.g. by
        # antivirus): every retry fails
        v = self.verify(files, merged, expected, hold='a.dll')
        self.assertEqual(v['RESULT'], 'False')
        self.assertGreaterEqual(int(v['ELAPSED_MS']), 900)  # 3 attempts, 0.5 s apart
        # a brief one (released 0.3 s after the first attempt) is retried
        v = self.verify(files, merged, expected, brief='a.dll')
        self.assertEqual(v['LOCKED'], 'True')
        self.assertEqual(v['RESULT'], 'True')
        self.assertGreaterEqual(int(v['ELAPSED_MS']), 400)

    def test_package_side_read_retry(self):
        path = os.path.join(self.root, "pack age's", 'b.dll')
        _write(path, b'new-b')
        args = ['-Function', 'Test-FileMatches', '-Path', path, '-Size', '5']
        self.assertEqual(self.call(args + ['-Sha', _sha(b'new-b')])['RESULT'], 'True')
        # a hash mismatch is final at once (no retry)
        v = self.call(args + ['-Sha', _sha(b'new-B')])
        self.assertEqual(v['RESULT'], 'False')
        self.assertLess(int(v['ELAPSED_MS']), 400)
        # a lasting read error: False after the retries
        v = self.call(args + ['-Sha', _sha(b'new-b')], hold=path)
        self.assertEqual(v['RESULT'], 'False')
        self.assertGreaterEqual(int(v['ELAPSED_MS']), 900)
        # a brief one: True
        v = self.call(args + ['-Sha', _sha(b'new-b')], brief=path)
        self.assertEqual(v['LOCKED'], 'True')
        self.assertEqual(v['RESULT'], 'True')
        self.assertGreaterEqual(int(v['ELAPSED_MS']), 400)

    def structure(self, text):
        path = self.helper('candidate.ps1', text)
        r = _powershell(['-File', self.helper('structure.ps1', _STRUCTURE), '-ScriptPath', path])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_structure(self):
        text = update.WINDOWS_UPDATER_SCRIPT
        self.assertEqual(_structureProblems(self.structure(text)), [])

        # reverse: the verify moved after "$script:mergeDone = $true" (where the
        # outer catch would turn a failure into "ok") must be caught
        start = text.index('    if ($null -ne $script:expected) {')
        end = text.index('    $script:mergeStarted = $false')
        block = text[start:end]
        self.assertIn('Test-MergedFiles', block)
        rest = text[:start] + text[end:]
        done = rest.index('    $script:mergeDone = $true\n') + len('    $script:mergeDone = $true\n')
        moved = rest[:done] + block + rest[done:]
        problems = _structureProblems(self.structure(moved))
        self.assertIn('not before $script:mergeDone = $true', problems)
        self.assertIn('outside the mergeStarted window', problems)

        # reverse: a failed verify that does not restore
        noRestore = text[:start] + block.replace('Restore-Install', "Write-Log 'x'") + text[end:]
        self.assertEqual(_structureProblems(self.structure(noRestore)),
                         ['a failed verify does not call Restore-Install'])

        # reverse: the verify inside its own try/catch
        wrapped = text[:start] + '    try {\n' + block + '    } catch { }\n' + text[end:]
        self.assertEqual(_structureProblems(self.structure(wrapped)),
                         ['inside 2 try statements, not only the top-level one'])

        # reverse: a test hook parameter
        hooked = text.replace('[switch]$NoRelaunch', '[switch]$NoRelaunch,\n    [string]$AfterMergeHook = \'\'', 1)
        self.assertIn('the script has an AfterMergeHook parameter',
                      _structureProblems(self.structure(hooked)))


if __name__ == '__main__':
    unittest.main()
