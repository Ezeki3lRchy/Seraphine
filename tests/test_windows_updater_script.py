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
        # os.environ holds Windows names upper-cased (PSMODULEPATH): replace that
        # entry instead of adding a second spelling next to it
        env = {k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
        env['PSModulePath'] = os.path.dirname(module) + os.pathsep + \
            os.environ.get('PSModulePath', '')

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
        for text in (self.text, self.locked()):
            r = subprocess.run([bash, '-n'], input=text.encode('utf-8'),
                               capture_output=True, timeout=60,
                               creationflags=CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8', 'replace'))

    def locked(self, noRelaunch=False):
        return update.macUpdaterScript(
            '/u/Seraphine-update.dmg', '/Applications/Seraphine.app', 4242, '/c/updater.sh',
            '/c/updater.log', '/u/result.json', '9.9.9', "/u/up date's/updater.lock",
            noRelaunch=noRelaunch)

    def test_lock_is_taken_before_anything_is_touched(self):
        t = self.locked()
        self.assertIn("LOCK='/u/up date'\"'\"'s/updater.lock'", t)
        taken = t.index('take_lock || exit 7')
        self.assertLess(taken, t.index(': > "$LOG"'))
        self.assertLess(t.index('trap release EXIT'), t.index(': > "$LOG"'))
        self.assertLess(taken, t.index('trap release EXIT'))
        # the lock is released only while the record is still this run's
        release = t.split('release() {')[1].split('\n}')[0]
        self.assertIn('[ "$(sed -n 1p "$LOCK/pid" 2>/dev/null)" = "$$" ]', release)
        self.assertLess(taken, t.index('for stale in'))

    def test_detach_retries_before_forcing_and_only_what_it_mounted(self):
        t = self.locked()
        body = t.split('detach_dmg() {')[1].split('\n}')[0]
        self.assertLess(body.index('if [ "$MOUNTED" = 1 ]; then'),
                        body.index('hdiutil detach "$MNT"'))
        self.assertLess(body.index('for try in 1 2 3'), body.index('-force'))
        self.assertIn('sleep 1', body)
        # MOUNTED is set only right after a successful attach
        self.assertEqual(t.count('MOUNTED=1'), 1)
        self.assertLess(t.index('if hdiutil attach'), t.index('MOUNTED=1'))
        self.assertNotIn('hdiutil detach', t.split('detach_dmg() {')[0])
        self.assertNotIn('hdiutil detach', t.split('detach_dmg() {')[1].split('\n}', 1)[1])

    def test_delays_are_parameters(self):
        t = update.macUpdaterScript('/d.dmg', '/A/Seraphine.app', 1, '/s', '/l', lockPath='/x',
                                    lockGrace=2, attachDelay=0, detachDelay=0.5)
        self.assertIn('LOCK_GRACE=2\n', t)
        self.assertIn('|| sleep 0\n', t)
        self.assertIn('sleep 0.5\n', t)
        d = self.locked()
        self.assertIn('LOCK_GRACE=5\n', d)
        self.assertIn('|| sleep 2\n', d)

    def test_no_relaunch_switch(self):
        self.assertIn('NO_RELAUNCH=0', self.locked())
        self.assertIn('NO_RELAUNCH=1', self.locked(noRelaunch=True))
        # every relaunch goes through reopen(), which honours the switch
        opens = [l.strip() for l in self.locked().splitlines() if l.strip().startswith('open ')]
        self.assertEqual(opens, ['open "$1"'])


def _deadPosixPid():
    p = subprocess.Popen([sys.executable, '-c', 'pass'],
                         creationflags=CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    p.wait()
    return p.pid


class MacUpdaterLockTest(unittest.TestCase):
    """The macOS lock directory as Python reads it, and runUpdaterMac refusing to
    start a second updater (sys.platform and the config dir are faked)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_maclock_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.stage = os.path.join(self.root, 'update')
        self.lock = os.path.join(self.stage, update.LOCK_NAME)
        self.dmg = os.path.join(self.stage, 'Seraphine-update.dmg')
        _write(self.dmg, b'dmg')

    def hold(self, pid, ident='', created=None, raw=None):
        """Write a lock record as the script does: pid, identity, created epoch."""
        os.makedirs(self.lock, exist_ok=True)
        if raw is None and pid is not None:
            raw = '%s\n%s\n%d\n' % (pid, ident,
                                    int(time.time()) if created is None else created)
        if raw is not None:
            _write(os.path.join(self.lock, 'pid'), raw.encode('ascii'))

    def held(self, identity='Sat Nov 21 03:25:34 2026'):
        with mock.patch.object(update, '_processIdentity', return_value=identity):
            return update.updaterRunning(self.lock)

    def age(self, seconds):
        old = time.time() - seconds
        os.utime(self.lock, (old, old))

    def test_live_pid_with_its_identity_is_held(self):
        self.hold(os.getpid(), 'Sat Nov 21 03:25:34 2026')
        self.assertTrue(self.held())

    def test_reused_pid_with_another_identity_is_stale(self):
        # the updater was killed; an unrelated process got its PID later
        self.hold(os.getpid(), 'Fri Nov 20 11:00:00 2026')
        self.assertTrue(self.held(identity='Fri Nov 20 11:00:00 2026'))  # control
        self.assertFalse(self.held())
        self.assertFalse(self.held(identity=''))  # the PID is gone for ps

    def test_without_a_recorded_identity_liveness_decides(self):
        self.hold(os.getpid(), '')
        self.assertTrue(self.held())

    def test_dead_or_invalid_pid_is_stale(self):
        for pid in (_deadPosixPid(), 0):
            self.hold(pid, 'Sat Nov 21 03:25:34 2026')
            self.age(60)
            self.assertFalse(self.held(), pid)

    def test_the_age_backstop_applies_only_without_an_identity(self):
        old = (int(time.time()) - update.LOCK_MAX_AGE - 60,   # e.g. the app took
               int(time.time()) + update.LOCK_MAX_AGE + 60)   # long to exit, a clock jump
        for created in old:
            # the live updater with its identity keeps its lock however old it is
            self.hold(os.getpid(), 'Sat Nov 21 03:25:34 2026', created=created)
            self.assertTrue(self.held(), created)
            # without an identity (no usable ps) the backstop decides
            self.hold(os.getpid(), '', created=created)
            self.assertFalse(self.held(), created)
        self.hold(os.getpid(), '', created=int(time.time()) - update.LOCK_MAX_AGE + 60)
        self.assertTrue(self.held())

    def test_an_identity_python_cannot_check_counts_as_held(self):
        # its ps call failed or timed out: the live pid is not disproved
        self.hold(os.getpid(), 'Sat Nov 21 03:25:34 2026')
        self.assertTrue(self.held(identity=None))

    def test_an_identity_python_cannot_check_is_bounded_by_the_backstop(self):
        # ps keeps failing and the PID may have been reused: without the age limit
        # that lock would block updates for good
        for created in (int(time.time()) - update.LOCK_MAX_AGE - 60,
                        int(time.time()) + update.LOCK_MAX_AGE + 60):
            self.hold(os.getpid(), 'Sat Nov 21 03:25:34 2026', created=created)
            self.assertFalse(self.held(identity=None), created)
            # control: with ps answering, the same old lock is still held
            self.assertTrue(self.held(), created)

    @unittest.skipIf(os.name == 'nt', 'ps')
    def test_identity_lookup_failure_is_none(self):
        with mock.patch.object(update.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired('ps', 2)):
            self.assertIsNone(update._processIdentity(os.getpid()))
        with mock.patch.object(update.subprocess, 'run',
                               return_value=mock.Mock(returncode=1, stdout=b'')):
            self.assertIsNone(update._processIdentity(os.getpid()))

    @unittest.skipIf(os.name == 'nt', 'POSIX permission semantics of kill -0')
    def test_another_users_process_does_not_hold_it(self):
        # same rule as the script's kill -0, which fails with EPERM
        self.hold(os.getpid(), 'Sat Nov 21 03:25:34 2026')
        with mock.patch.object(update.os, 'kill', side_effect=PermissionError(1, 'EPERM')):
            self.assertFalse(self.held())

    def test_incomplete_record_is_held_only_during_the_grace_period(self):
        # the script is between mkdir and its write, or was killed right there
        for raw in (None, '', 'garbage\n', '%d\n' % os.getpid(),
                    '%d\nSat Nov 21 03:25:34 2026\n' % os.getpid(), '-5\n\n123\n'):
            shutil.rmtree(self.lock, ignore_errors=True)
            self.hold(None, raw=raw)
            self.assertTrue(self.held(), raw)
            self.age(update.LOCK_GRACE + 30)
            self.assertFalse(self.held(), raw)

    @unittest.skipUnless(sys.platform == 'darwin', 'ps -o lstart')
    def test_identity_is_what_the_script_records(self):
        # the script's proc_id pipeline, run by bash, for a live process
        sleeper = subprocess.Popen(['/bin/sleep', '30'])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        text = update.macUpdaterScript('/x.dmg', '/A/Seraphine.app', 1, '/x.sh', '/x.log')
        procId = [l for l in text.splitlines() if l.startswith('proc_id() {')][0]
        r = subprocess.run(['/bin/bash', '-c', procId + '\nproc_id "$1"', 'bash',
                            str(sleeper.pid)], capture_output=True, timeout=30)
        recorded = r.stdout.decode().strip()
        self.assertTrue(recorded)
        self.assertEqual(update._processIdentity(sleeper.pid), recorded)

    def runMac(self):
        from app.common import config
        cfgDir = os.path.join(self.root, 'cfg')
        os.makedirs(cfgDir, exist_ok=True)
        _write(os.path.join(cfgDir, 'updater.log'), b'first run log')
        _write(os.path.join(self.stage, 'result.json'), b'{}')
        with mock.patch.object(update, 'sys', mock.Mock(platform='darwin')), \
                mock.patch.object(config, 'LOCAL_PATH', cfgDir), \
                mock.patch.object(update.subprocess, 'Popen') as popen:
            launch = update.runUpdaterMac(self.dmg, '/Applications/Seraphine.app', '9.9.9')
        return launch, popen, cfgDir

    def test_run_updater_mac_never_disturbs_a_running_updater(self):
        self.hold(os.getpid(), 'Sat Nov 21 03:25:34 2026')
        with mock.patch.object(update, '_processIdentity',
                               return_value='Sat Nov 21 03:25:34 2026'):
            launch, popen, cfgDir = self.runMac()
        self.assertIsNone(launch)
        popen.assert_not_called()
        self.assertEqual(_read(os.path.join(cfgDir, 'updater.log')), b'first run log')
        self.assertEqual(_read(os.path.join(self.stage, 'result.json')), b'{}')
        self.assertFalse(os.path.exists(os.path.join(cfgDir, 'updater.sh')))

    def test_run_updater_mac_passes_the_lock_and_takes_over_a_stale_one(self):
        self.hold(_deadPosixPid())
        launch, popen, cfgDir = self.runMac()
        self.assertIsNotNone(launch)
        script = os.path.join(cfgDir, 'updater.sh')
        self.assertEqual(popen.call_args.args[0], ['/bin/bash', script])
        with open(script, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('LOCK=' + update.shlex.quote(self.lock) + '\n', text)
        self.assertIn('NO_RELAUNCH=0\n', text)


def _findBash():
    if sys.platform != 'win32':
        return '/bin/bash' if os.path.isfile('/bin/bash') else shutil.which('bash')
    git = r'C:\Program Files\Git\bin\bash.exe'
    return git if os.path.isfile(git) else None


def _bashPath(path):
    """``path`` as the bash from _findBash sees it (Git Bash: /c/...)."""
    if sys.platform != 'win32':
        return path
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return '/' + drive.rstrip(':').lower() + rest.replace('\\', '/')


class MacUpdaterScriptLockRunTest(unittest.TestCase):
    """The script's lock, run for real by bash (Git Bash on Windows, /bin/bash on
    macOS). A fake ``hdiutil`` on PATH records its arguments and always fails, so
    a run that gets the lock stops at the attach with exit 1 and no real hdiutil
    is ever called; a run refused by the lock exits 7 touching nothing. The lock
    record is written by the bash process that starts the script (its own PID is
    alive meanwhile), and a fake ``ps`` prints $FAKE_IDENT as the identity. A short
    grace period and no retry delays keep the runs fast."""

    HELD, RAN = 7, 1
    GRACE = 1

    def setUp(self):
        self.bash = _findBash()
        if not self.bash:
            self.skipTest('no bash available')
        self.root = tempfile.mkdtemp(prefix='seraphine_lockrun_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.stage = os.path.join(self.root, 'update')
        os.makedirs(self.stage)
        self.lock = os.path.join(self.stage, update.LOCK_NAME)
        self.log = os.path.join(self.root, 'updater.log')
        self.calls = os.path.join(self.root, 'hdiutil.calls')
        self.shim = os.path.join(self.root, 'shim')
        self.addShim('ps', b'#!/bin/bash\necho "  $FAKE_IDENT  "\n')
        self.addShim('hdiutil',
                     b'#!/bin/bash\nprintf "%s\\n" "$*" >> "$HDIUTIL_CALLS"\n'
                     b'if [ "$1" = attach ] && [ -n "${STEAL_LOCK:-}" ]; then\n'
                     b'    printf "1\\nother\\n1\\n" > "$STEAL_LOCK/pid"\nfi\nexit 1\n')
        self.script = os.path.join(self.root, 'updater.sh')
        p = _bashPath
        with open(self.script, 'w', encoding='utf-8', newline='\n') as f:
            f.write(update.macUpdaterScript(
                p(os.path.join(self.stage, 'missing.dmg')),
                p(os.path.join(self.root, 'Apps', 'Seraphine.app')), _deadPosixPid(),
                p(self.script), p(self.log), p(os.path.join(self.stage, 'result.json')),
                '9.9.9', p(self.lock), noRelaunch=True, lockGrace=self.GRACE,
                attachDelay=0, detachDelay=0))

    def addShim(self, name, text):
        _write(os.path.join(self.shim, name), text)
        os.chmod(os.path.join(self.shim, name), 0o755)

    def run_(self, setup='', ident='Sat Nov 21 03:25:34 2026', **extraEnv):
        """Run ``setup`` then the script in one bash; returns (exit code, stderr)."""
        cmd = ('export PATH="%s:$PATH"; L=%s; %s\nbash %s; echo "rc=$?"'
               % (_bashPath(self.shim), update.shlex.quote(_bashPath(self.lock)), setup,
                  update.shlex.quote(_bashPath(self.script))))
        env = dict(os.environ, FAKE_IDENT=ident, HDIUTIL_CALLS=_bashPath(self.calls),
                   **extraEnv)
        r = subprocess.run([self.bash, '-c', cmd], capture_output=True, timeout=60, env=env,
                           creationflags=CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        out = r.stdout.decode('utf-8', 'replace')
        self.assertIn('rc=', out, r.stderr)
        return int(out.rsplit('rc=', 1)[1].split()[0]), r.stderr.decode('utf-8', 'replace')

    def hdiutilCalls(self):
        try:
            return _read(self.calls).decode('utf-8', 'replace').splitlines()
        except OSError:
            return []

    # a record of the starting bash itself (alive while the script runs)
    @staticmethod
    def record(ident='Sat Nov 21 03:25:34 2026', ageSec=0):
        return ('mkdir -p "$L"; printf "%%s\\n%%s\\n%%s\\n" "$$" %s "$(( $(date +%%s) - %d ))" '
                '> "$L/pid"' % (update.shlex.quote(ident), ageSec))

    def assertRan(self, code, err):
        self.assertEqual(code, self.RAN, err)
        self.assertTrue(os.path.isfile(self.log))
        self.assertFalse(os.path.lexists(self.lock))  # released on exit
        self.assertEqual([n for n in os.listdir(self.stage) if 'lock' in n], [])

    def assertHeld(self, code, err):
        self.assertEqual(code, self.HELD, err)
        self.assertFalse(os.path.exists(self.log))
        self.assertFalse(os.path.exists(os.path.join(self.stage, 'result.json')))
        self.assertTrue(os.path.isdir(self.lock))
        self.assertEqual(self.hdiutilCalls(), [])

    def test_free_lock_is_taken_and_released(self):
        self.assertRan(*self.run_())

    def test_a_failing_attach_is_retried_then_nothing_is_detached(self):
        self.assertRan(*self.run_())
        log = _read(self.log).decode('utf-8', 'replace')
        for attempt in (1, 2, 3):
            self.assertIn('hdiutil attach failed (attempt %d)' % attempt, log)
        self.assertLess(log.index('(attempt 3)'), log.index('result: failed:1'))
        calls = self.hdiutilCalls()
        self.assertEqual([c.split()[0] for c in calls], ['attach'] * 3, calls)
        # the never-mounted mount directory is removed (as bash sees it)
        mount = calls[0].split('-mountpoint ', 1)[1]
        r = subprocess.run([self.bash, '-c', '[ ! -e "$1" ]', 'bash', mount],
                           capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        self.assertEqual(r.returncode, 0, mount)

    def test_exit_keeps_a_lock_that_is_no_longer_its_own(self):
        # while this run works, another updater takes the lock over (its record
        # replaces ours): leaving, this run must not delete that lock
        code, err = self.run_(STEAL_LOCK=_bashPath(self.lock))
        self.assertEqual(code, self.RAN, err)
        self.assertEqual(_read(os.path.join(self.lock, 'pid')), b'1\nother\n1\n')

    def test_live_holder_with_its_identity_blocks(self):
        self.assertHeld(*self.run_(self.record()))

    def test_same_pid_with_another_identity_is_taken_over(self):
        other = 'Fri Nov 20 11:00:00 2026'
        self.assertHeld(*self.run_(self.record(ident=other), ident=other))  # control
        shutil.rmtree(self.lock)
        self.assertRan(*self.run_(self.record(ident=other)))

    def test_dead_holder_is_taken_over(self):
        self.assertRan(*self.run_(
            '(exit 0) & dead=$!; wait $dead; mkdir -p "$L"; '
            'printf "%s\\n\\n%s\\n" "$dead" "$(date +%s)" > "$L/pid"'))

    def test_the_age_backstop_applies_only_without_an_identity(self):
        old = update.LOCK_MAX_AGE + 60
        # a live updater with its identity keeps its lock however old it is
        self.assertHeld(*self.run_(self.record(ageSec=old)))
        shutil.rmtree(self.lock)
        # without an identity: held inside the limit, stale beyond it
        self.assertHeld(*self.run_(self.record(ident='', ageSec=update.LOCK_MAX_AGE - 60)))
        shutil.rmtree(self.lock)
        self.assertRan(*self.run_(self.record(ident='', ageSec=old)))

    def test_incomplete_record_completed_within_the_grace_period_blocks(self):
        self.assertHeld(*self.run_(
            'mkdir -p "$L"; : > "$L/pid"; (sleep 0.5; %s) &' % self.record()))

    def test_incomplete_record_is_stale_after_the_grace_period(self):
        for setup in ('mkdir -p "$L"; : > "$L/pid"', 'mkdir -p "$L"; echo garbage > "$L/pid"',
                      'mkdir -p "$L"'):
            shutil.rmtree(self.lock, ignore_errors=True)
            _removeQuietly(self.log)
            start = time.monotonic()
            self.assertRan(*self.run_(setup))
            self.assertGreaterEqual(time.monotonic() - start, self.GRACE - 0.5)

    def test_a_lost_record_write_aborts_before_anything_is_touched(self):
        # another updater writes its record into the fresh lock directory first
        # (stale-takeover race): this one must not run without its own record
        self.addShim('mkdir',
                     b'#!/bin/bash\n/bin/mkdir "$@" || exit $?\n'
                     b'case "$1" in *updater.lock) printf "1\\nother\\n1\\n" > "$1/pid";; esac\n')
        code, err = self.run_()
        self.assertHeld(code, err)
        self.assertEqual(_read(os.path.join(self.lock, 'pid')), b'1\nother\n1\n')


def _removeQuietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _bash(*args, **kwargs):
    return subprocess.run(['/bin/bash', *args], capture_output=True, **kwargs)


def _reap(process):
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


@unittest.skipUnless(sys.platform == 'darwin', 'macOS updater (hdiutil, ditto)')
class MacUpdaterRunTest(unittest.TestCase):
    """The macOS updater run for real: a tiny fake Seraphine.app packed into a real
    .dmg, a temporary "Applications" dir, the PID of an exited process and
    relaunching disabled, so no app is ever opened."""

    def setUp(self):
        # $TMPDIR is under /var, a symlink to /private/var: compare real paths
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='seraphine_macupd_'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(self.detachOurImages)
        self.apps = os.path.join(self.root, "Apps dir's")
        self.app = os.path.join(self.apps, 'Seraphine.app')
        self.cfgDir = os.path.join(self.root, 'cfg')
        self.stage = os.path.join(self.cfgDir, 'update')
        self.dmg = os.path.join(self.stage, 'Seraphine-update.dmg')
        self.lock = os.path.join(self.stage, update.LOCK_NAME)
        self.resultPath = os.path.join(self.stage, 'result.json')
        os.makedirs(self.stage)
        self.bundle(self.app, 'old')

    def bundle(self, path, tag, executable=True):
        import plistlib
        os.makedirs(os.path.join(path, 'Contents', 'MacOS'))
        with open(os.path.join(path, 'Contents', 'Info.plist'), 'wb') as f:
            plistlib.dump({'CFBundleExecutable': 'Seraphine',
                           'CFBundleIdentifier': 'test.seraphine.fake',
                           'CFBundlePackageType': 'APPL'}, f)
        exe = os.path.join(path, 'Contents', 'MacOS', 'Seraphine')
        _write(exe, b'#!/bin/sh\necho ' + tag.encode() + b'\n')
        os.chmod(exe, 0o755 if executable else 0o644)
        _write(os.path.join(path, 'Contents', 'Resources', 'tag.txt'), tag.encode())

    def makeDmg(self, tag, executable=True):
        src = os.path.join(self.root, 'dmg-src')
        shutil.rmtree(src, ignore_errors=True)
        self.bundle(os.path.join(src, 'Seraphine.app'), tag, executable)
        for attempt in range(3):  # hdiutil create is known to fail transiently
            r = subprocess.run(['hdiutil', 'create', '-quiet', '-ov', '-volname', 'Seraphine',
                                '-srcfolder', src, '-format', 'UDZO', self.dmg],
                               capture_output=True, timeout=120)
            if r.returncode == 0 and os.path.isfile(self.dmg):
                break
            time.sleep(2)
        else:
            self.fail('hdiutil create failed: ' + r.stderr.decode('utf-8', 'replace'))
        shutil.rmtree(src, ignore_errors=True)

    def attachedImages(self, check=True):
        import plistlib
        r = subprocess.run(['hdiutil', 'info', '-plist'], capture_output=True, timeout=60)
        if check:
            # never let a failed query pass as "nothing attached"
            self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8', 'replace'))
        elif r.returncode != 0:
            return []
        images = plistlib.loads(r.stdout).get('images', [])
        ours = []
        for image in images:
            path = os.path.realpath(image.get('image-path', ''))
            if path.startswith(self.root + os.sep):
                ours.append([e['mount-point'] for e in image.get('system-entities', [])
                             if e.get('mount-point')])
        return ours

    def detachOurImages(self):
        for mounts in self.attachedImages(check=False):
            for mount in mounts:
                subprocess.run(['hdiutil', 'detach', mount, '-force', '-quiet'],
                               capture_output=True, timeout=60)

    def tag(self):
        return _read(os.path.join(self.app, 'Contents', 'Resources', 'tag.txt')).decode()

    def result(self):
        with open(self.resultPath, encoding='utf-8') as f:
            return json.load(f)

    def runAsTheAppDoes(self, path=None):
        """runUpdaterMac itself (config dir faked, the PID of an exited process),
        waited for; returns (exit code, log text)."""
        from app.common import config
        env = {'PATH': path + os.pathsep + os.environ.get('PATH', '')} if path else {}
        with mock.patch.object(config, 'LOCAL_PATH', self.cfgDir), \
                mock.patch.object(update.os, 'getpid', return_value=_deadPosixPid()), \
                mock.patch.dict(os.environ, env):
            launch = update.runUpdaterMac(self.dmg, self.app, '9.9.9', noRelaunch=True)
        self.assertIsNotNone(launch)
        self.addCleanup(_reap, launch.process)  # also after a TimeoutExpired
        code = launch.process.wait(timeout=180)
        return code, _read(launch.logPath).decode('utf-8', 'replace')

    def assertNoLeftovers(self):
        self.assertEqual(os.listdir(self.apps), ['Seraphine.app'])
        self.assertFalse(os.path.lexists(self.lock))
        self.assertEqual([n for n in os.listdir(self.stage) if 'lock' in n], [])
        self.assertEqual(self.attachedImages(), [])

    def test_replaces_the_installed_bundle(self):
        self.makeDmg('new')
        code, log = self.runAsTheAppDoes()
        self.assertEqual(code, 0, log)
        self.assertEqual(self.tag(), 'new')
        self.assertEqual((self.result()['status'], self.result()['version']), ('ok', '9.9.9'))
        self.assertIn('relaunch disabled', log)
        self.assertNoLeftovers()
        self.assertEqual(os.listdir(self.stage), ['result.json'])  # dmg removed
        self.assertFalse(os.path.exists(os.path.join(self.cfgDir, 'updater.sh')))

    def test_failed_swap_restores_the_old_bundle(self):
        # moving the verified copy into place fails: the old bundle (already moved
        # aside) must be put back
        self.makeDmg('new')
        shim = os.path.join(self.root, 'shim')
        _write(os.path.join(shim, 'mv'),
               b'#!/bin/bash\ncase "$1" in *"/.Seraphine.app.new."*) '
               b'echo "forced failure" >&2; exit 1;; esac\nexec /bin/mv "$@"\n')
        os.chmod(os.path.join(shim, 'mv'), 0o755)
        code, log = self.runAsTheAppDoes(path=shim)
        self.assertEqual(code, 1, log)
        self.assertEqual(self.tag(), 'old')
        self.assertEqual(self.result()['status'], 'restored', log)
        self.assertIn('previous app restored', log)
        self.assertNoLeftovers()
        self.assertTrue(os.path.isfile(self.dmg))  # kept for a retry

    def test_incomplete_new_bundle_leaves_the_old_one(self):
        self.makeDmg('new', executable=False)
        code, log = self.runAsTheAppDoes()
        self.assertEqual(code, 1, log)
        self.assertEqual(self.tag(), 'old')
        self.assertEqual(self.result()['status'], 'failed:1')
        self.assertIn('incomplete', log)
        self.assertNoLeftovers()

    def test_lock_blocks_a_second_instance_until_its_owner_is_gone(self):
        self.makeDmg('new')
        # a first updater that holds the lock: it waits for a "Seraphine" that keeps
        # running, so it sits in its wait loop owning the lock
        app = subprocess.Popen(['/bin/sleep', '120'])
        self.addCleanup(_reap, app)
        first = os.path.join(self.root, 'first.sh')
        _write(first, update.macUpdaterScript(
            self.dmg, self.app, app.pid, first, os.path.join(self.root, 'first.log'),
            self.resultPath, '9.9.9', self.lock, noRelaunch=True).encode('utf-8'))
        holder = subprocess.Popen(['/bin/bash', first])
        self.addCleanup(_reap, holder)
        record = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                record = _read(os.path.join(self.lock, 'pid')).decode().split('\n')
            except OSError:
                record = []
            if len(record) >= 3 and record[2]:
                break
            time.sleep(0.2)
        # what bash recorded is what Python reads back for that process
        self.assertEqual(record[0], str(holder.pid))
        self.assertTrue(record[1])
        self.assertEqual(update._processIdentity(holder.pid), record[1])
        self.assertTrue(update.updaterRunning(self.lock))
        recorded = _read(os.path.join(self.lock, 'pid'))

        # the app does not start a second updater
        from app.common import config
        with mock.patch.object(config, 'LOCAL_PATH', self.cfgDir):
            self.assertIsNone(update.runUpdaterMac(self.dmg, self.app, '9.9.9',
                                                   noRelaunch=True))

        # and the script itself, started anyway, exits 7 touching nothing
        script = os.path.join(self.root, 'second.sh')
        log = os.path.join(self.root, 'second.log')
        _write(script, update.macUpdaterScript(
            self.dmg, self.app, _deadPosixPid(), script, log, self.resultPath, '9.9.9',
            self.lock, noRelaunch=True).encode('utf-8'))
        r = _bash(script, timeout=60)
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertFalse(os.path.exists(log))
        self.assertFalse(os.path.exists(self.resultPath))
        self.assertEqual(self.tag(), 'old')
        self.assertEqual(_read(os.path.join(self.lock, 'pid')), recorded)
        self.assertTrue(os.path.isfile(self.dmg))

        # its owner SIGKILLed (as the app does when it never reports "started"),
        # so its trap never ran: the lock is stale and taken over
        holder.kill()
        holder.wait(timeout=30)
        self.assertTrue(os.path.isdir(self.lock))
        self.assertFalse(update.updaterRunning(self.lock))
        code, text = self.runAsTheAppDoes()
        self.assertEqual(code, 0, text)
        self.assertEqual(self.tag(), 'new')
        self.assertNoLeftovers()


if __name__ == '__main__':
    unittest.main()
