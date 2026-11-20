"""The Windows in-place updater script, run for real against a sandbox install.

Windows only. PowerShell runs hidden (CREATE_NO_WINDOW, output captured), with
-NoRelaunch and the PID of a process that has already exited; every path is a
temp dir whose names contain a space, an apostrophe and non-ASCII characters.
"""
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from app.common import update

CREATE_NO_WINDOW = 0x08000000


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def _filelist(path, entries):
    # same encoding as release_win.ps1's Out-File -Encoding UTF8: BOM + CRLF
    with open(path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write('\n'.join(entries) + '\n')


def _sha(data):
    return hashlib.sha256(data).hexdigest().upper()


def _deadPid():
    p = subprocess.Popen([sys.executable, '-c', 'pass'],
                         creationflags=CREATE_NO_WINDOW)
    p.wait()
    return p.pid


def _lock(path, shareRead):
    """Open path so that nobody else may write it (and with shareRead=False, not
    read it either). Returns a handle closer."""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    GENERIC_READ, OPEN_EXISTING = 0x80000000, 3
    h = k32.CreateFileW(path, GENERIC_READ, 1 if shareRead else 0, None,
                        OPEN_EXISTING, 0, None)
    if h in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), 'CreateFileW failed')
    return lambda: k32.CloseHandle(ctypes.c_void_p(h))


@unittest.skipUnless(sys.platform == 'win32', 'Windows updater')
class WindowsUpdaterScriptTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_upd_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.install = os.path.join(self.root, "Sera phine's \u5b89\u88c5 \u00fc")
        self.stage = os.path.join(self.root, "cfg dir's \u00e9", 'update')
        self.source = os.path.join(self.stage, 'package')
        self.cfg = os.path.dirname(self.stage)
        self.outside = os.path.join(self.root, 'outside.txt')
        _write(self.outside, b'outside')

        # old install
        _write(self.p('Seraphine.exe'), b'old-exe')
        _write(self.p('_internal', 'a.dll'), b'old-a')
        _write(self.p('_internal', 'b.dll'), b'old-b')
        _write(self.p('_internal', 'old_only.dll'), b'old-only')
        # same size and mtime as its package counterpart, different content
        _write(self.p('_internal', 'same.dll'), b'AAAA')
        # user data in no filelist
        _write(self.p('_internal', 'app', 'resource', 'game', 'cache.png'), b'user-cache')
        _filelist(self.p('filelist.txt'), [
            'Seraphine.exe', '_internal\\a.dll', '_internal\\b.dll',
            '_internal\\old_only.dll', '..\\outside.txt', self.outside, '', '   '])

        # new package
        _write(self.s('Seraphine.exe'), b'new-exe')
        _write(self.s('_internal', 'a.dll'), b'new-a')
        _write(self.s('_internal', 'b.dll'), b'new-b')
        _write(self.s('_internal', 'new.dll'), b'new-new')
        _write(self.s('_internal', 'same.dll'), b'BBBB')
        for f in (self.p('_internal', 'same.dll'), self.s('_internal', 'same.dll')):
            os.utime(f, (1700000000, 1700000000))
        _filelist(self.s('filelist.txt'), [
            'Seraphine.exe', '_internal\\a.dll', '_internal\\b.dll', '_internal\\new.dll'])

        self.script = os.path.join(self.cfg, 'updater.ps1')
        self.log = os.path.join(self.cfg, 'updater.log')
        self.resultPath = os.path.join(self.stage, 'result.json')
        self.backup = os.path.join(self.stage, 'backup')
        self.lockPath = os.path.join(self.stage, 'updater.lock')
        update.writeWindowsUpdaterScript(self.script)

    def args(self, source=None, pid=None, extra=(), install=None):
        return update.windowsUpdaterArgs(
            self.script, install or self.install, source or self.source,
            _deadPid() if pid is None else pid, self.log, noRelaunch=True,
            resultPath=self.resultPath, backupDir=self.backup,
            targetVersion='9.9.9', lockPath=self.lockPath) + list(extra)

    def p(self, *parts):
        return os.path.join(self.install, *parts)

    def s(self, *parts):
        return os.path.join(self.source, *parts)

    def readLog(self):
        self.logText = _read(self.log).decode('utf-8-sig', 'replace') \
            if os.path.exists(self.log) else ''
        return self.logText

    def run_script(self, source=None, pid=None, extra=(), install=None, env=None):
        r = subprocess.run(self.args(source, pid, extra, install), capture_output=True,
                           text=True, timeout=180, creationflags=CREATE_NO_WINDOW, env=env)
        self.readLog()
        return r

    def result(self):
        with open(self.resultPath, encoding='utf-8-sig') as f:
            return json.load(f)

    def assertOldInstall(self):
        self.assertEqual(_read(self.p('Seraphine.exe')), b'old-exe')
        self.assertEqual(_read(self.p('_internal', 'a.dll')), b'old-a')
        self.assertEqual(_read(self.p('_internal', 'b.dll')), b'old-b')
        self.assertEqual(_read(self.p('_internal', 'old_only.dll')), b'old-only')
        self.assertEqual(_read(self.p('_internal', 'same.dll')), b'AAAA')
        self.assertFalse(os.path.exists(self.p('_internal', 'new.dll')))
        self.assertEqual(
            _read(self.p('_internal', 'app', 'resource', 'game', 'cache.png')),
            b'user-cache')
        self.assertIn('old_only.dll', _read(self.p('filelist.txt')).decode('utf-8-sig'))
        self.assertEqual(_read(self.outside), b'outside')
        self.assertTrue(os.path.isfile(self.s('Seraphine.exe')))  # kept for a retry

    def test_update_merges_removes_stale_and_keeps_user_files(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + self.logText)

        self.assertEqual(_read(self.p('Seraphine.exe')), b'new-exe')
        self.assertEqual(_read(self.p('_internal', 'a.dll')), b'new-a')
        self.assertEqual(_read(self.p('_internal', 'new.dll')), b'new-new')
        # same size + mtime, different content: still installed (robocopy /IM)
        self.assertEqual(_read(self.p('_internal', 'same.dll')), b'BBBB')
        self.assertFalse(os.path.exists(self.p('_internal', 'old_only.dll')))
        self.assertEqual(
            _read(self.p('_internal', 'app', 'resource', 'game', 'cache.png')),
            b'user-cache')
        self.assertIn('new.dll', _read(self.p('filelist.txt')).decode('utf-8-sig'))
        self.assertFalse(os.path.exists(self.source))
        self.assertFalse(os.path.exists(self.backup))
        self.assertFalse(os.path.exists(self.backup + '.added.txt'))
        self.assertEqual(self.result(), {**self.result(), 'status': 'ok', 'code': 0,
                                         'version': '9.9.9'})

        # escaping entries were refused, nothing outside the install was touched
        self.assertEqual(_read(self.outside), b'outside')
        self.assertEqual(sorted(os.listdir(self.root)),
                         sorted([os.path.basename(self.install), os.path.basename(self.cfg),
                                 'outside.txt']))
        self.assertIn('removed stale: _internal\\old_only.dll', self.logText)
        self.assertIn('skip (parent reference)', self.logText)
        self.assertIn('skip (absolute path)', self.logText)
        self.assertIn('updater started', self.logText)

    def test_missing_source_changes_nothing(self):
        r = self.run_script(source=os.path.join(self.root, 'missing'))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.result()['status'], 'failed:2')
        self.assertOldInstall()
        self.assertIn('nothing changed', self.logText)

    def test_waits_for_a_running_pid_and_times_out_without_changes(self):
        sleeper = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(30)'],
            creationflags=CREATE_NO_WINDOW)
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        r = self.run_script(pid=sleeper.pid, extra=['-WaitTimeoutSec', '1'])
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(self.result()['status'], 'failed:3')
        self.assertOldInstall()

    def test_write_locked_file_restores_old_version(self):
        # b.dll can be read (backed up) but not written: the merge fails after
        # Seraphine.exe / a.dll were already replaced -> everything is restored
        unlock = _lock(self.p('_internal', 'b.dll'), shareRead=True)
        try:
            r = self.run_script(extra=['-MergeAttempts', '2'])
        finally:
            unlock()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr + self.logText)
        self.assertEqual(self.result()['status'], 'restored')
        self.assertEqual(self.result()['version'], '9.9.9')
        self.assertOldInstall()
        self.assertIn('merge pass 2 failed', self.logText)
        self.assertFalse(os.path.exists(os.path.join(self.stage, 'backup')))

    def test_unreadable_locked_file_fails_before_changing_anything(self):
        unlock = _lock(self.p('_internal', 'b.dll'), shareRead=False)
        try:
            r = self.run_script()
        finally:
            unlock()
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr + self.logText)
        self.assertEqual(self.result()['status'], 'failed:5')
        self.assertOldInstall()

    def test_package_without_filelist_deletes_nothing(self):
        os.remove(self.s('filelist.txt'))
        r = self.run_script()
        self.assertEqual(r.returncode, 0, self.logText)
        self.assertEqual(_read(self.p('Seraphine.exe')), b'new-exe')
        self.assertEqual(_read(self.p('_internal', 'a.dll')), b'new-a')
        self.assertEqual(_read(self.p('_internal', 'old_only.dll')), b'old-only')
        self.assertIn('skipping stale-file cleanup', self.logText)

    def test_variant_spellings_never_delete_package_files(self):
        _filelist(self.p('filelist.txt'), [
            'Seraphine.exe.', '.\\_internal\\a.dll', '_internal\\\\b.dll',
            '_INTERNAL\\NEW.DLL', '_internal/a.dll ', '_internal\\old_only.dll'])
        # the new list omits a.dll although the package ships it
        _filelist(self.s('filelist.txt'), ['Seraphine.exe'])
        r = self.run_script()
        self.assertEqual(r.returncode, 0, self.logText)
        self.assertEqual(_read(self.p('Seraphine.exe')), b'new-exe')
        self.assertEqual(_read(self.p('_internal', 'a.dll')), b'new-a')
        self.assertEqual(_read(self.p('_internal', 'b.dll')), b'new-b')
        self.assertEqual(_read(self.p('_internal', 'new.dll')), b'new-new')
        self.assertFalse(os.path.exists(self.p('_internal', 'old_only.dll')))

    def _junction(self, link, target):
        r = subprocess.run(['cmd', '/c', 'mklink', '/J', link, target],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            self.skipTest('cannot create a junction here')
        self.addCleanup(lambda: os.path.isdir(link) and os.rmdir(link))

    def test_junction_inside_install_cannot_delete_outside(self):
        outsideDir = os.path.join(self.root, 'outside_dir')
        _write(os.path.join(outsideDir, 'victim.txt'), b'victim')
        self._junction(self.p('_internal', 'link'), outsideDir)
        _filelist(self.p('filelist.txt'), ['Seraphine.exe', '_internal\\link\\victim.txt'])
        r = self.run_script()
        self.assertEqual(r.returncode, 0, self.logText)
        self.assertEqual(_read(os.path.join(outsideDir, 'victim.txt')), b'victim')
        self.assertIn('skip (reparse point)', self.logText)

    def test_package_writing_through_a_junction_changes_nothing(self):
        outsideDir = os.path.join(self.root, 'outside_dir')
        os.makedirs(outsideDir)
        self._junction(self.p('_internal', 'link'), outsideDir)
        _write(self.s('_internal', 'link', 'evil.txt'), b'evil')
        r = self.run_script()
        self.assertEqual(r.returncode, 6, self.logText)
        self.assertEqual(self.result()['status'], 'failed:6')
        self.assertEqual(os.listdir(outsideDir), [])
        self.assertOldInstall()

    def test_file_vs_directory_mismatch_changes_nothing(self):
        # the install has a directory where the package has a file
        os.makedirs(self.p('_internal', 'thing'))
        _write(self.s('_internal', 'thing'), b'file')
        r = self.run_script()
        self.assertEqual(r.returncode, 6, self.logText)
        self.assertIn('mismatch', self.logText)
        self.assertOldInstall()

    def test_file_in_the_way_of_a_directory_changes_nothing(self):
        _write(self.p('_internal', 'sub'), b'a file')
        _write(self.s('_internal', 'sub', 'x.dll'), b'x')
        r = self.run_script()
        self.assertEqual(r.returncode, 6, self.logText)
        self.assertEqual(_read(self.p('_internal', 'sub')), b'a file')
        self.assertOldInstall()

    def test_two_concurrent_instances_only_one_proceeds(self):
        sleeper = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            creationflags=CREATE_NO_WINDOW)
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        procs = [subprocess.Popen(self.args(pid=sleeper.pid), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
                 for _ in range(2)]
        # the loser exits at once (7) while the winner still waits for the PID
        deadline = 60
        codes = [None, None]
        for _ in range(deadline * 10):
            codes = [p.poll() for p in procs]
            if 7 in codes:
                break
            time.sleep(0.1)
        self.assertIn(7, codes)
        self.assertIn(None, codes)  # the winner is still waiting
        self.assertTrue(os.path.isfile(self.p('_internal', 'old_only.dll')))
        sleeper.kill()
        sleeper.wait()
        final = [p.wait(timeout=180) for p in procs]
        self.assertEqual(sorted(final), [0, 7])
        self.readLog()
        self.assertEqual(self.logText.count('updater started'), 1)
        self.assertEqual(self.result()['status'], 'ok')
        self.assertEqual(_read(self.p('Seraphine.exe')), b'new-exe')
        self.assertEqual(_read(self.p('_internal', 'b.dll')), b'new-b')
        self.assertFalse(os.path.exists(self.p('_internal', 'old_only.dll')))

    def _writeMeta(self, preExe=b'old-exe', pkgExe=b'new-exe', version='9.9.9'):
        with open(self.backup + '.meta', 'w', encoding='utf-8-sig') as f:
            json.dump({'preExe': _sha(preExe), 'pkgExe': _sha(pkgExe),
                       'version': version}, f)

    def _leaveUnfinishedRestore(self, meta=True):
        # as if an earlier run merged half and its restore did not finish
        _write(self.p('Seraphine.exe'), b'new-exe')
        _write(self.p('_internal', 'a.dll'), b'new-a')
        _write(self.p('_internal', 'new.dll'), b'new-new')
        _write(os.path.join(self.backup, 'Seraphine.exe'), b'old-exe')
        _write(os.path.join(self.backup, '_internal', 'a.dll'), b'old-a')
        with open(self.backup + '.added.txt', 'w', encoding='utf-8') as f:
            f.write(self.p('_internal', 'new.dll') + '\n')
        if meta:
            self._writeMeta()

    def _leaveForeignBackup(self, meta=True):
        # install is v1.4.8 installed by hand; backup + manifest are from a failed
        # 1.4.6 -> 1.4.7 run; the package is 1.4.7 (or newer)
        _write(self.p('Seraphine.exe'), b'v148-exe')
        _write(self.p('_internal', 'extra148.dll'), b'v148-extra')
        _write(os.path.join(self.backup, 'Seraphine.exe'), b'v146-exe')
        _write(os.path.join(self.backup, '_internal', 'only146.dll'), b'v146-only')
        with open(self.backup + '.added.txt', 'w', encoding='utf-8') as f:
            f.write(self.p('_internal', 'extra148.dll') + '\n')
        if meta:
            self._writeMeta(preExe=b'v146-exe', pkgExe=b'v147-exe', version='1.4.7')

    def assertForeignBackupIgnored(self, r):
        self.assertEqual(r.returncode, 0, self.logText)
        self.assertIn('FOREIGN BACKUP', self.logText)
        self.assertNotIn('completing its restore first', self.logText)
        # nothing copied from it, nothing in its added-list deleted
        self.assertFalse(os.path.exists(self.p('_internal', 'only146.dll')))
        self.assertEqual(_read(self.p('_internal', 'extra148.dll')), b'v148-extra')
        # discarded, then the update ran per the normal rules
        self.assertEqual(self.result()['status'], 'ok')
        self.assertEqual(_read(self.p('Seraphine.exe')), b'new-exe')
        for leftover in (self.backup, self.backup + '.added.txt', self.backup + '.meta'):
            self.assertFalse(os.path.exists(leftover), leftover)

    def test_foreign_backup_from_an_older_failed_run_is_never_applied(self):
        self._leaveForeignBackup()
        self.assertForeignBackupIgnored(self.run_script())

    def test_backup_without_meta_is_treated_as_foreign(self):
        self._leaveForeignBackup(meta=False)
        self.assertForeignBackupIgnored(self.run_script())

    def test_backup_without_meta_after_a_partial_merge_is_foreign_too(self):
        self._leaveUnfinishedRestore(meta=False)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, self.logText)
        self.assertIn('FOREIGN BACKUP', self.logText)
        self.assertEqual(self.result()['status'], 'ok')

    def test_matching_pre_update_exe_completes_the_restore(self):
        # the earlier merge failed before it reached the exe
        self._leaveUnfinishedRestore()
        _write(self.p('Seraphine.exe'), b'old-exe')
        r = self.run_script()
        self.assertEqual(r.returncode, 0, self.logText)
        self.assertIn('earlier restore completed', self.logText)
        self.assertNotIn('FOREIGN BACKUP', self.logText)
        self.assertEqual(self.result()['status'], 'ok')

    def test_script_hashes_without_get_filehash(self):
        # In Windows PowerShell, Get-FileHash is a script function of the
        # Microsoft.PowerShell.Utility module. Started from a PowerShell 7 process
        # (a pwsh terminal, the CI step that runs these tests) the inherited
        # PSModulePath can leave it unavailable; the script then saw an empty hash
        # and discarded its own backup as foreign. It must hash through .NET.
        code = [line.split('#', 1)[0] for line in
                update.WINDOWS_UPDATER_SCRIPT.splitlines()]
        self.assertFalse([line for line in code if 'Get-FileHash' in line])
        self.assertTrue([line for line in code if 'SHA256CryptoServiceProvider' in line])

    def test_backup_is_recognised_under_a_powershell_7_module_path(self):
        # The same environment, reproduced without PowerShell 7: an unversioned
        # Core-edition Microsoft.PowerShell.Utility ahead in PSModulePath, laid out
        # the way PowerShell 7 ships it (its nested dll has the name of Windows
        # PowerShell's own, so the compiled cmdlets still resolve). Windows
        # PowerShell takes that manifest, and Get-FileHash, which it only has as a
        # script function of its own module, is gone.
        module = os.path.join(self.root, 'ps7 modules', 'Microsoft.PowerShell.Utility')
        _write(os.path.join(module, 'Microsoft.PowerShell.Utility.psd1'),
               b"@{ GUID='1DA87E53-152B-403E-98DC-74D7B4D63D59'; ModuleVersion='7.0.0.0'; "
               b"PowerShellVersion='3.0'; CompatiblePSEditions=@('Core'); "
               b"NestedModules='Microsoft.PowerShell.Commands.Utility.dll'; "
               b"CmdletsToExport=@('Get-FileHash','Format-List','Get-Date',"
               b"'ConvertFrom-Json','New-Object'); FunctionsToExport=@() }\r\n")
        _write(os.path.join(module, 'Microsoft.PowerShell.Commands.Utility.dll'),
               b'not an assembly')
        env = dict(os.environ)
        env['PSModulePath'] = os.path.dirname(module) + os.pathsep + \
            env.get('PSModulePath', '')

        probe = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
             'try { $null = Get-FileHash -LiteralPath $env:ComSpec; "works" } '
             'catch { "broken" }'],
            capture_output=True, text=True, timeout=60, env=env,
            creationflags=CREATE_NO_WINDOW)
        self.assertIn('broken', probe.stdout, 'the stand-in module path no longer '
                      'breaks Get-FileHash, so this test would prove nothing')

        self._leaveUnfinishedRestore()
        _write(self.p('Seraphine.exe'), b'old-exe')
        r = self.run_script(env=env)
        self.assertEqual(r.returncode, 0, self.logText)
        self.assertIn('earlier restore completed', self.logText)
        self.assertNotIn('FOREIGN BACKUP', self.logText)
        self.assertEqual(self.result()['status'], 'ok')

    def test_newer_package_meets_a_matching_backup(self):
        # backup made for package v1.4.7 (install half-merged to it); now 1.4.9
        self._leaveUnfinishedRestore()
        _write(self.p('Seraphine.exe'), b'v147-exe')
        self._writeMeta(preExe=b'v146-exe', pkgExe=b'v147-exe', version='1.4.7')
        _write(self.s('Seraphine.exe'), b'v149-exe')
        r = self.run_script()
        self.assertEqual(r.returncode, 0, self.logText)
        self.assertIn('earlier restore completed', self.logText)
        self.assertLess(self.logText.index('earlier restore completed'),
                        self.logText.index('merge finished'))
        self.assertEqual(_read(self.p('Seraphine.exe')), b'v149-exe')
        self.assertEqual(_read(self.p('_internal', 'a.dll')), b'new-a')

    def test_undeletable_foreign_backup_stops_before_changing_anything(self):
        self._leaveForeignBackup()
        held = os.path.join(self.backup, '_internal', 'only146.dll')
        unlock = _lock(held, shareRead=False)
        try:
            r = self.run_script()
        finally:
            unlock()
        self.assertEqual(r.returncode, 5, self.logText)
        self.assertEqual(self.result()['status'], 'failed:5')
        self.assertIn('could not be removed', self.logText)
        # nothing restored from it, nothing merged, nothing deleted
        self.assertEqual(_read(self.p('Seraphine.exe')), b'v148-exe')
        self.assertEqual(_read(self.p('_internal', 'a.dll')), b'old-a')
        self.assertEqual(_read(self.p('_internal', 'extra148.dll')), b'v148-extra')
        self.assertFalse(os.path.exists(self.p('_internal', 'only146.dll')))
        self.assertTrue(os.path.isfile(self.s('Seraphine.exe')))

    def test_newer_package_meets_a_foreign_backup(self):
        self._leaveForeignBackup()
        _write(self.s('Seraphine.exe'), b'v149-exe')
        r = self.run_script()
        self.assertEqual(r.returncode, 0, self.logText)
        self.assertIn('FOREIGN BACKUP', self.logText)
        self.assertFalse(os.path.exists(self.p('_internal', 'only146.dll')))
        self.assertEqual(_read(self.p('_internal', 'extra148.dll')), b'v148-extra')
        self.assertEqual(_read(self.p('Seraphine.exe')), b'v149-exe')

    def test_meta_is_written_before_the_merge_and_removed_with_the_backup(self):
        unlock = _lock(self.p('_internal', 'b.dll'), shareRead=False)
        try:
            r = self.run_script()  # backup of b.dll fails -> failed:5, nothing changed
        finally:
            unlock()
        self.assertEqual(r.returncode, 5)
        self.assertFalse(os.path.exists(self.backup + '.meta'))
        # a merge failure that cannot be restored keeps backup AND meta together
        self._leaveUnfinishedRestore()
        unlock = _lock(self.p('_internal', 'a.dll'), shareRead=False)
        try:
            r = self.run_script()
        finally:
            unlock()
        self.assertEqual(r.returncode, 8, self.logText)
        self.assertTrue(os.path.isdir(self.backup))
        self.assertTrue(os.path.isfile(self.backup + '.meta'))

    def test_unfinished_restore_is_completed_before_a_retry(self):
        self._leaveUnfinishedRestore()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, self.logText)
        log = self.logText
        self.assertLess(log.index('completing its restore first'),
                        log.index('earlier restore completed'))
        self.assertLess(log.index('earlier restore completed'), log.index('merge finished'))
        self.assertEqual(self.result()['status'], 'ok')
        self.assertEqual(_read(self.p('_internal', 'b.dll')), b'new-b')
        self.assertFalse(os.path.exists(self.backup))

    def test_unfinished_restore_that_fails_again_keeps_backup_and_merges_nothing(self):
        self._leaveUnfinishedRestore()
        unlock = _lock(self.p('_internal', 'a.dll'), shareRead=False)
        try:
            r = self.run_script()
        finally:
            unlock()
        self.assertEqual(r.returncode, 8, self.logText)
        self.assertEqual((self.result()['status'], self.result()['code']),
                         ('restore-failed', 8))
        self.assertEqual(_read(os.path.join(self.backup, '_internal', 'a.dll')), b'old-a')
        self.assertEqual(_read(self.p('_internal', 'b.dll')), b'old-b')  # no merge
        self.assertTrue(os.path.isfile(self.s('Seraphine.exe')))
        self.assertIn('download Seraphine manually', self.logText)

    def test_python_fingerprint_rule_agrees_with_the_script(self):
        # update_stage._backupBelongsToInstall mirrors the script's step 3: the same
        # backup.meta and installed exe give the same verdict in both
        from app.common import update_stage as us

        def meta(pre=b'old-exe', pkg=b'new-exe', lower=False, bom=True, raw=None):
            def write():
                if raw is not None:
                    _write(self.backup + '.meta', raw)
                    return
                obj = {'version': '9.9.8'}
                if pre is not None:
                    obj['preExe'] = _sha(pre).lower() if lower else _sha(pre)
                if pkg is not None:
                    obj['pkgExe'] = _sha(pkg).lower() if lower else _sha(pkg)
                with open(self.backup + '.meta', 'w',
                          encoding='utf-8-sig' if bom else 'utf-8') as f:
                    json.dump(obj, f)
            return write

        cases = {
            'pre-update exe': (meta(), b'old-exe'),
            'package exe, lower-case hashes': (meta(lower=True), b'new-exe'),
            'no BOM': (meta(bom=False), b'new-exe'),
            'foreign exe': (meta(), b'v148-exe'),
            'no meta': (None, b'old-exe'),
            'garbage meta': (meta(raw=b'{not json'), b'old-exe'),
            'meta without pkgExe': (meta(pkg=None), b'old-exe'),
            'empty preExe': (meta(raw=b'{"preExe": "", "pkgExe": "%s"}'
                                  % _sha(b'old-exe').encode()), b'old-exe'),
            'no installed exe': (meta(), None),
        }
        for name, (writeMeta, exe) in cases.items():
            with self.subTest(name):
                self.setUp()  # fresh sandbox per case
                _write(os.path.join(self.backup, '_internal', 'a.dll'), b'old-a')
                if writeMeta:
                    writeMeta()
                if exe is None:
                    os.remove(self.p('Seraphine.exe'))
                else:
                    _write(self.p('Seraphine.exe'), exe)
                python = us._backupBelongsToInstall(self.stage, self.install)
                r = self.run_script()
                self.assertIn(r.returncode, (0, 1), self.logText)
                script = 'completing its restore first' in self.logText
                self.assertEqual(script, 'FOREIGN BACKUP' not in self.logText, self.logText)
                self.assertEqual(python, script)

    def test_drive_root_install_is_refused(self):
        r = self.run_script(install='C:\\')
        self.assertEqual(r.returncode, 2, self.logText)
        self.assertEqual(self.result()['status'], 'failed:2')
        self.assertIn('drive-root', self.logText)
        self.assertOldInstall()


@unittest.skipUnless(sys.platform == 'win32', 'Windows updater')
class RunUpdaterTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_upd_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cfgDir = os.path.join(self.root, "cfg dir's \u00e9")
        self.stage = os.path.join(self.cfgDir, 'update')
        self.pkg = os.path.join(self.stage, 'package')
        self.inst = os.path.join(self.root, "inst \u5b89\u88c5's")
        _write(os.path.join(self.pkg, 'Seraphine.exe'), b'new-exe')
        _filelist(os.path.join(self.pkg, 'filelist.txt'), ['Seraphine.exe'])
        _write(os.path.join(self.inst, 'Seraphine.exe'), b'old-exe')

    def test_launches_hidden_detached_with_parameters(self):
        with mock.patch.object(update.subprocess, 'Popen') as popen, \
                mock.patch.dict(os.environ, {'QT_SCALE_FACTOR': '1.5',
                                             'QT_ENABLE_HIGHDPI_SCALING': '0'}):
            launch = update.runUpdater(self.pkg, installDir=self.inst,
                                       configDir=self.cfgDir, targetVersion='9.9.9')
        self.assertIsNotNone(launch)
        self.assertEqual(launch.logPath, os.path.join(self.cfgDir, 'updater.log'))
        self.assertTrue(os.path.isfile(os.path.join(self.cfgDir, 'updater.ps1')))
        args = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        opt = lambda name: args[args.index(name) + 1]
        self.assertEqual(opt('-File'), os.path.join(self.cfgDir, 'updater.ps1'))
        self.assertEqual(opt('-InstallDir'), self.inst)
        self.assertEqual(opt('-SourceDir'), self.pkg)
        self.assertEqual(opt('-WaitPid'), str(os.getpid()))
        self.assertEqual(opt('-ResultPath'), os.path.join(self.stage, 'result.json'))
        self.assertEqual(opt('-BackupDir'), os.path.join(self.stage, 'backup'))
        self.assertEqual(opt('-TargetVersion'), '9.9.9')
        self.assertEqual(opt('-LockPath'), os.path.join(self.stage, 'updater.lock'))
        self.assertNotIn('-NoRelaunch', args)
        self.assertTrue(kwargs['creationflags'] & CREATE_NO_WINDOW)
        self.assertTrue(kwargs['creationflags'] & update._CREATE_BREAKAWAY_FROM_JOB)
        self.assertEqual(kwargs['cwd'], self.cfgDir)
        self.assertNotIn('QT_SCALE_FACTOR', kwargs['env'])
        self.assertNotIn('QT_ENABLE_HIGHDPI_SCALING', kwargs['env'])
        self.assertEqual(kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_breakaway_denied_falls_back(self):
        ok = mock.Mock()
        with mock.patch.object(update.subprocess, 'Popen',
                               side_effect=[PermissionError(5, 'denied'), ok]) as popen:
            launch = update.runUpdater(self.pkg, installDir=self.inst, configDir=self.cfgDir)
        self.assertIs(launch.process, ok)
        flags = [c.kwargs['creationflags'] for c in popen.call_args_list]
        self.assertTrue(flags[0] & update._CREATE_BREAKAWAY_FROM_JOB)
        self.assertFalse(flags[1] & update._CREATE_BREAKAWAY_FROM_JOB)

    def test_running_updater_is_never_disturbed(self):
        lock = os.path.join(self.stage, 'updater.lock')
        log = os.path.join(self.cfgDir, 'updater.log')
        result = os.path.join(self.stage, 'result.json')
        _write(lock, b'')
        _write(log, b'first run log')
        _write(result, b'{}')
        unlock = _lock(lock, shareRead=False)
        try:
            self.assertTrue(update.updaterRunning(lock))
            with mock.patch.object(update.subprocess, 'Popen') as popen:
                self.assertIsNone(update.runUpdater(self.pkg, installDir=self.inst,
                                                    configDir=self.cfgDir))
        finally:
            unlock()
        popen.assert_not_called()
        self.assertEqual(_read(log), b'first run log')
        self.assertEqual(_read(result), b'{}')
        self.assertFalse(os.path.exists(os.path.join(self.cfgDir, 'updater.ps1')))
        self.assertFalse(update.updaterRunning(lock))

    def test_source_without_exe_starts_nothing(self):
        os.remove(os.path.join(self.pkg, 'Seraphine.exe'))
        with mock.patch.object(update.subprocess, 'Popen') as popen:
            self.assertIsNone(update.runUpdater(self.pkg, installDir=self.inst,
                                                configDir=self.cfgDir))
        popen.assert_not_called()

    def test_not_packaged_does_nothing(self):
        with mock.patch.object(update.platform, 'install_dir', return_value=None), \
                mock.patch.object(update.subprocess, 'Popen') as popen:
            self.assertIsNone(update.runUpdater(self.pkg))
        popen.assert_not_called()

    def test_real_hidden_launch_through_special_character_paths(self):
        # the real command-line builder + a real hidden, detached Popen; the PID is
        # a dead one and relaunch is off so nothing is started afterwards
        dead = _deadPid()
        with mock.patch.object(update.os, 'getpid', return_value=dead):
            launch = update.runUpdater(self.pkg, installDir=self.inst,
                                       configDir=self.cfgDir, targetVersion='9.9.9',
                                       noRelaunch=True)
        self.assertEqual(launch.process.wait(timeout=120), 0)
        self.assertTrue(update.updaterStarted(launch.logPath))
        self.assertEqual(_read(os.path.join(self.inst, 'Seraphine.exe')), b'new-exe')
        with open(os.path.join(self.stage, 'result.json'), encoding='utf-8-sig') as f:
            result = json.load(f)
        self.assertEqual((result['status'], result['version']), ('ok', '9.9.9'))


class MacUpdaterScriptTest(unittest.TestCase):
    """Text-level checks only: this path cannot be run here (no macOS)."""

    def setUp(self):
        self.text = update.macUpdaterScript(
            "/Users/x/Down loads/Sera'phine-update.dmg",
            "/Applications/Sera phine's.app", 4242,
            '/Users/x/Library/Application Support/Seraphine/updater.sh',
            '/Users/x/Library/Application Support/Seraphine/updater.log',
            '/Users/x/Library/Application Support/Seraphine/update/result.json',
            '9.9.9')

    def test_never_removes_the_installed_bundle_before_the_new_copy_is_verified(self):
        t = self.text
        self.assertNotIn('rm -rf "$APP"', t)
        verified = t.index('[ ! -x "$NEW/Contents/MacOS/$EXE" ]')
        self.assertLess(t.index('ditto "$MNT/Seraphine.app" "$NEW"'), verified)
        moveAside = t.index('mv "$APP" "$BACKUP"')
        self.assertLess(verified, moveAside)
        moveIn = t.index('mv "$NEW" "$APP"')
        self.assertLess(moveAside, moveIn)
        self.assertLess(moveIn, t.index('rm -rf "$BACKUP"'))
        # failure branch after moving aside restores the backup
        self.assertLess(moveIn, t.index('mv "$BACKUP" "$APP"'))
        self.assertLess(t.index('mv "$BACKUP" "$APP"'), t.index('rm -rf "$BACKUP"'))

    def test_logs_detaches_quotes_and_reopens(self):
        t = self.text
        self.assertIn('set -u', t)
        self.assertIn('updater started', t)
        self.assertIn('hdiutil detach', t)
        self.assertIn("APP='/Applications/Sera phine'\"'\"'s.app'", t)
        self.assertIn('while kill -0 "$PID"', t)
        self.assertIn('open "$APP"', t.split('fail() {')[1].split('\n}')[0])

    def test_stale_new_copies_are_cleaned_never_old_ones(self):
        t = self.text
        cleanup = t.index('for stale in "$PARENT/.$NAME.new."*; do')
        self.assertLess(cleanup, t.index('ditto "$MNT/Seraphine.app" "$NEW"'))
        self.assertNotIn('.old."*', t)
        self.assertNotIn('rm -rf "$PARENT', t)

    def test_result_file_on_every_outcome(self):
        t = self.text
        self.assertIn("RESULT='/Users/x/Library/Application Support/Seraphine/update/result.json'", t)
        self.assertIn('write_result "${2:-failed:1}" 1', t.split('fail() {')[1].split('\n}')[0])
        self.assertIn('fail "previous app restored" restored', t)
        self.assertLess(t.index('mv "$NEW" "$APP"'), t.index('write_result ok 0'))
        self.assertLess(t.index('rm -rf "$BACKUP"'), t.index('write_result ok 0'))

    def test_restore_failed_branch_logs_and_reopens_the_backup(self):
        branch = self.text.split('log "RESTORE FAILED: the previous app is at: $BACKUP"')[1]
        branch = branch.split('exit 2')[0]
        self.assertIn('write_result restore-failed 2', branch)
        self.assertIn('open "$BACKUP"', branch)
        self.assertNotIn('rm -rf "$BACKUP"', branch)

    def test_bash_syntax(self):
        bash = shutil.which('bash')
        if not bash or 'system32' in bash.lower() or 'windowsapps' in bash.lower():
            git = r'C:\Program Files\Git\bin\bash.exe'
            bash = git if os.path.isfile(git) else None
        if not bash:
            self.skipTest('no bash available')
        r = subprocess.run([bash, '-n'], input=self.text.encode('utf-8'),
                           capture_output=True, timeout=60,
                           creationflags=CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8', 'replace'))


if __name__ == '__main__':
    unittest.main()
