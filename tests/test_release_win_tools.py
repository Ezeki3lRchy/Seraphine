"""Small pieces of the Windows release scripts, run for real (Windows only).

- tools/pack_win.ps1 -CheckMethods accepts a `Method = LZMA2:24` line with trailing
  spaces (7-Zip pads some listings) and still rejects a filter behind them (a
  stand-in 7z prints the canned `7z l -slt` listing, as in test_pack_manifest.py).
- release_win.ps1's Get-TagLagWarning (taken from the script with the PowerShell
  parser, not copied): -DryRun without gh warns when the newest local release tag
  is below the previous release, comparing versions numerically.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

CREATE_NO_WINDOW = 0x08000000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, 'tools', 'pack_win.ps1')
RELEASE = os.path.join(ROOT, 'release_win.ps1')

_CALL = r'''
param([string]$ScriptPath, [string]$CasesJson)
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$null, [ref]$errors)
if ($errors.Count) { Write-Output 'PARSE-ERROR'; exit 3 }
$fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                  $n.Name -eq 'Get-TagLagWarning' }, $true)
if (-not $fn) { Write-Output 'NO-FUNCTION'; exit 4 }
. ([scriptblock]::Create($fn.Extent.Text))
foreach ($c in (ConvertFrom-Json $CasesJson)) {
    $w = Get-TagLagWarning @($c.tags) $c.prev
    if ($null -eq $w) { Write-Output 'NONE' } else { Write-Output ('WARN ' + $w) }
}
'''


def _ps(args, env=None, timeout=120):
    return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass']
                          + list(args), capture_output=True, text=True, timeout=timeout, env=env,
                          creationflags=CREATE_NO_WINDOW)


@unittest.skipUnless(sys.platform == 'win32', 'Windows release scripts')
class ReleaseWinToolsTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_relwin_')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_check_methods_trailing_space(self):
        fake = os.path.join(self.root, 'fake7z')
        os.makedirs(fake)
        listing = os.path.join(self.root, 'listing.txt')
        with open(os.path.join(fake, '7z.cmd'), 'w') as f:
            f.write('@echo off\r\ntype "%SERAPHINE_FAKE_7Z_LISTING%"\r\nexit /b 0\r\n')
        archive = os.path.join(self.root, 'any.7z')
        with open(archive, 'wb') as f:
            f.write(b'7z')
        env = dict(os.environ, PATH=fake + os.pathsep + os.environ.get('PATH', ''),
                   SERAPHINE_FAKE_7Z_LISTING=listing)
        for lines, rc in ((['Method = LZMA2:24   '], 0),
                          (['Method = LZMA2:24\t'], 0),
                          (['Method = LZMA2:24   ', 'Method = BCJ2 LZMA2:24   '], 1),
                          (['Method = BCJ LZMA2:24   '], 1)):
            with self.subTest(lines=lines):
                with open(listing, 'w') as f:
                    f.write('--\r\nPath = any.7z\r\nType = 7z\r\n' + ''.join(x + '\r\n' for x in lines))
                r = _ps(['-File', PACK, '-CheckMethods', archive], env=env)
                self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
                self.assertIn('sevenzip: ' + os.path.join(fake, '7z.cmd'), r.stdout)

    def test_tag_lag_warning(self):
        helper = os.path.join(self.root, 'call.ps1')
        with open(helper, 'w', encoding='utf-8-sig') as f:
            f.write(_CALL)
        cases = [
            ({'tags': ['v1.4.8', 'v1.4.9'], 'prev': '1.4.9'}, None),
            ({'tags': ['v1.4.9', 'v1.4.10'], 'prev': '1.4.10'}, None),   # 10 > 9 numerically
            ({'tags': ['v1.4.10', 'v1.4.9'], 'prev': '1.4.9'}, None),
            ({'tags': ['v1.4.8', 'v1.4.9'], 'prev': '1.4.10'}, 'v1.4.9'),
            ({'tags': ['v1.4.8', 'v2.0.0-beta', 'nightly'], 'prev': '1.4.9'}, 'v1.4.8'),
            ({'tags': [], 'prev': '1.4.9'}, '(none)'),
        ]
        r = _ps(['-File', helper, '-ScriptPath', RELEASE, '-CasesJson', json.dumps([c for c, _ in cases])])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = [line for line in r.stdout.splitlines() if line.strip()]
        self.assertEqual(len(out), len(cases), out)
        for (case, want), line in zip(cases, out):
            with self.subTest(case=case):
                if want is None:
                    self.assertEqual(line, 'NONE')
                else:
                    self.assertTrue(line.startswith('WARN '), line)
                    self.assertIn(want, line)
                    self.assertIn('git fetch --tags', line)
                    self.assertIn('v' + case['prev'], line)


if __name__ == '__main__':
    unittest.main()
