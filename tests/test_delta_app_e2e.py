"""The Windows delta end to end in the app: a real delta built by tools/make_delta.py,
served by a fake session, staged by update_stage.stageRelease (delta kind, the
in-app precheck passes) into a temp stage dir for an injected temp install, then
applied by the real new updater script (hidden PowerShell, -NoRelaunch): the install
equals the new release. With one install file changed, the precheck refuses the
delta, the full package is staged in the same call, and it installs the same tree.

Windows only (runs PowerShell). No network, no windows; every path is a temp path.
"""
import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from app.common import platform, update
from app.common import update_stage as us
from app.common.config import VERSION
from app.common.release_names import FULL_WIN, deltaName
from tests.test_delta_staging import Server, asset, digestOf
from tests.test_make_delta import NEW, PREV, make_delta, writeTree
from tests.test_windows_updater_script import CREATE_NO_WINDOW, _deadPid

_MAJOR, _MINOR, _PATCH = (int(n) for n in VERSION.split('.'))
RELEASE = f'{_MAJOR}.{_MINOR}.{_PATCH + 1}'
FULL_URL = 'https://example.invalid/' + FULL_WIN
DELTA_URL = 'https://example.invalid/' + deltaName(VERSION, RELEASE)


def _tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for name in files:
            full = os.path.join(d, name)
            with open(full, 'rb') as f:
                out[os.path.relpath(full, root)] = f.read()
    return out


@unittest.skipUnless(sys.platform == 'win32', 'runs the PowerShell updater script')
class DeltaAppE2eTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="seraphine app e2e's ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.install = writeTree(os.path.join(self.tmp, 'install dir'), PREV, withManifest=False)
        self.newTree = writeTree(os.path.join(self.tmp, 'new'), NEW)
        self.base = os.path.join(self.tmp, 'cfg', 'update')
        deltaPath = os.path.join(self.tmp, deltaName(VERSION, RELEASE))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(make_delta.main([self.install, self.newTree, deltaPath]), 0)
        fullPath = os.path.join(self.tmp, FULL_WIN)
        make_delta.pack(self.newTree, fullPath)
        with open(deltaPath, 'rb') as f:
            self.deltaBytes = f.read()
        with open(fullPath, 'rb') as f:
            self.fullBytes = f.read()
        fallback = os.path.join(self.tmp, 'cfg', us.FALLBACK_NAME)
        for target, name, value in ((us, 'fallbackPath', lambda: fallback),
                                    (platform, 'install_dir', lambda: None),
                                    (us, 'downloadProxy', lambda: None)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def info(self):
        return {'tag_name': 'v' + RELEASE, 'forbidden': False,
                'assets': [asset(FULL_WIN, FULL_URL, self.fullBytes),
                           asset(deltaName(VERSION, RELEASE), DELTA_URL, self.deltaBytes)]}

    def server(self):
        return Server({FULL_URL: self.fullBytes, DELTA_URL: self.deltaBytes})

    def apply(self, marker):
        cfg = os.path.dirname(self.base)
        script = os.path.join(cfg, 'updater.ps1')
        update.writeWindowsUpdaterScript(script)
        resultPath = os.path.join(self.base, 'result.json')
        args = update.windowsUpdaterArgs(
            script, self.install, marker['path'], _deadPid(), os.path.join(cfg, 'updater.log'),
            noRelaunch=True, resultPath=resultPath, backupDir=os.path.join(self.base, 'backup'),
            targetVersion=marker['version'], lockPath=os.path.join(self.base, 'updater.lock'))
        r = subprocess.run(args, capture_output=True, text=True, timeout=300,
                           creationflags=CREATE_NO_WINDOW)
        with open(resultPath, encoding='utf-8-sig') as f:
            result = json.load(f)
        return r.returncode, result

    def expected(self):
        want = _tree(self.newTree)
        del want['filemanifest.txt']  # the updater never merges it into the install
        return want

    async def test_delta_staged_and_applied(self):
        sess = self.server()
        marker = await us.stageRelease(self.info(), self.base, session=sess, installDir=self.install)
        self.assertEqual(sess.urls, [DELTA_URL])
        self.assertEqual(marker['kind'], 'delta')
        self.assertEqual(marker['digest'], digestOf(self.deltaBytes))
        self.assertEqual(sorted(_tree(marker['path'])),
                         sorted(['Seraphine.exe', '_internal\\changed.dll', '_internal\\new\\added.dll',
                                 'filelist.txt', 'filemanifest.txt']))
        rc, result = await asyncio.to_thread(self.apply, marker)
        self.assertEqual((rc, result['status']), (0, 'ok'))
        self.assertEqual(_tree(self.install), self.expected())

    async def test_changed_install_file_takes_the_full_package(self):
        with open(os.path.join(self.install, '_internal', 'same.dll'), 'wb') as f:
            f.write(b'same-bytez')  # not in the delta, differs from the manifest
        sess = self.server()
        with self.assertLogs('Seraphine', level='INFO') as cm:
            marker = await us.stageRelease(self.info(), self.base, session=sess, installDir=self.install)
        self.assertEqual(sess.urls, [DELTA_URL, FULL_URL])
        self.assertNotIn('kind', marker)
        self.assertEqual(marker['digest'], digestOf(self.fullBytes))
        self.assertIn('staging the full package', '\n'.join(cm.output))
        rc, result = await asyncio.to_thread(self.apply, marker)
        self.assertEqual((rc, result['status']), (0, 'ok'))
        self.assertEqual(_tree(self.install), self.expected())


if __name__ == '__main__':
    unittest.main()
