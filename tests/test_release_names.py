"""app/common/release_names.py (C5): the only asset names a release may carry.

Loaded by file path, the way the release tools load it, and checked to import with
the standard library alone (tools must not pull in PySide6 or the user config).
"""
import importlib.util
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'app', 'common', 'release_names.py')


def _load():
    spec = importlib.util.spec_from_file_location('release_names_under_test', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rn = _load()


class ReleaseNamesTest(unittest.TestCase):

    def test_constants(self):
        self.assertEqual(rn.FULL_WIN, 'Seraphine.7z')
        self.assertEqual(rn.FULL_MAC, 'Seraphine.dmg')
        self.assertEqual(rn.MANIFEST_ASSET, 'Seraphine.filemanifest.txt')
        self.assertEqual(rn.DELTA_RE, r'^Seraphine-(\d+\.\d+\.\d+)-to-(\d+\.\d+\.\d+)\.delta$')

    def test_delta_name_round_trip(self):
        name = rn.deltaName('1.4.10', '1.4.11')
        self.assertEqual(name, 'Seraphine-1.4.10-to-1.4.11.delta')
        self.assertEqual(rn.parseDelta(name), ('1.4.10', '1.4.11'))
        self.assertTrue(re.match(rn.DELTA_RE, name))

    def test_delta_name_rejects_non_versions(self):
        for base, new in (('1.4', '1.4.11'), ('1.4.10', 'v1.4.11'), ('1.4.10', '1.4.11\n'),
                          ('', '1.4.11'), (None, '1.4.11'), ('1.4.10', '1.4.11 ')):
            with self.subTest(base=base, new=new):
                with self.assertRaises(ValueError):
                    rn.deltaName(base, new)

    def test_parse_delta_rejects_other_names(self):
        for name in ('Seraphine-1.4.10-to-1.4.11.7z', 'Seraphine-1.4.10-to-1.4.11.delta\n',
                     'seraphine-1.4.10-to-1.4.11.delta', 'Seraphine-1.4-to-1.4.11.delta',
                     'Seraphine-1.4.10-to-1.4.11.delta.7z', 'xSeraphine-1.4.10-to-1.4.11.delta',
                     'Seraphine-١.4.10-to-1.4.11.delta', 'Seraphine.delta', '', None, 7):
            with self.subTest(name=name):
                self.assertIsNone(rn.parseDelta(name))

    def test_is_publishable(self):
        for name in ('Seraphine.7z', 'Seraphine.dmg', 'Seraphine.filemanifest.txt',
                     'Seraphine-1.4.10-to-1.4.11.delta'):
            with self.subTest(name=name):
                self.assertTrue(rn.isPublishable(name))
        for name in ('x.7z', 'Seraphine-win.7z', 'a.dmg', 'Seraphine.7z.delta', 'seraphine.7z',
                     'Seraphine-1.4.10-to-1.4.11.7z', 'filemanifest.txt', 'Seraphine.delta',
                     'Seraphine.7z ', '', None):
            with self.subTest(name=name):
                self.assertFalse(rn.isPublishable(name))

    def test_no_publishable_name_but_the_full_packages_ends_in_7z_or_dmg(self):
        # every self-updating version in the field takes the first .7z / .dmg asset
        names = [rn.MANIFEST_ASSET, rn.deltaName('1.4.10', '1.4.11')]
        for name in names:
            self.assertFalse(name.endswith('.7z') or name.endswith('.dmg'), name)

    def test_imports_with_the_standard_library_only(self):
        # -I: isolated (no user site, no PYTHONPATH, no script dir); -S: no site-packages
        code = ('import importlib.util, sys\n'
                'spec = importlib.util.spec_from_file_location("rn", sys.argv[1])\n'
                'm = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n'
                'bad = sorted(n for n in sys.modules if n.split(".")[0] in '
                '("PySide6", "qfluentwidgets", "app", "py7zr", "aiohttp"))\n'
                'print(m.deltaName("1.0.0", "1.0.1"), bad)\n')
        r = subprocess.run([sys.executable, '-I', '-S', '-c', code, PATH],
                           capture_output=True, text=True, timeout=60, cwd=os.path.dirname(PATH))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), 'Seraphine-1.0.0-to-1.0.1.delta []')


if __name__ == '__main__':
    unittest.main()
