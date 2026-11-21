"""P1: the asset pickers of every self-updating version in the field never take a
delta or the manifest asset.

v1.4.6/v1.4.7 and v1.4.8-v1.4.10 pick the first asset ending in .7z / .dmg
(``selectReleaseAsset`` + ``platformAssetSuffix`` of their app/common/util.py). Their
code is read from the committed snapshots (tests/fixtures/legacy, kept LF by the root
.gitattributes; never ``git show``: CI checks out shallow), extracted with ``ast`` (no
import) and run with a stub platform on the new release's asset list in all 24
orders. The current picker must give the same answer. The release gate is
tools/update_e2e.py with the real tags; these snapshots serve the unit tests only.
"""
import ast
import hashlib
import itertools
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import importlib.util

from app.common import platform
from app.common import util
from app.common.release_names import FULL_MAC, FULL_WIN, MANIFEST_ASSET, deltaName

LEGACY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'legacy')

# C9: sha256 of the LF bytes `git show <ref>:app/common/util.py` prints
SNAPSHOTS = {
    'util_v1_4_6.py.txt': '8d8a306baba8e0f0a76bb959683e00de2e16ba8509bceda2f9170f6d0f4b38d5',
    'util_v1_4_8.py.txt': '963a6dc7a97b8a798128f1488c96a98f7b2590944c7bd51c449a2a20b32122ad',
}

DELTA = deltaName('1.4.10', '1.4.11')
NAMES = (DELTA, MANIFEST_ASSET, FULL_WIN, FULL_MAC)

_GATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'release_gates.py')


def _releaseGates():
    spec = importlib.util.spec_from_file_location('release_gates_for_legacy_pick', _GATES)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _asset(name):
    return {'name': name, 'browser_download_url': 'https://example.invalid/' + name,
            'digest': 'sha256:' + hashlib.sha256(name.encode()).hexdigest()}


def _legacyPicker(fileName, isMac):
    """selectReleaseAsset of a snapshot, bound to a stub platform module."""
    with open(os.path.join(LEGACY, fileName), 'rb') as f:
        source = f.read().decode('utf-8')
    tree = ast.parse(source)
    wanted = {'platformAssetSuffix', 'selectReleaseAsset'}
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    if {f.name for f in funcs} != wanted:
        raise AssertionError(f'{fileName}: picker functions not found')
    namespace = {'platform': SimpleNamespace(IS_MACOS=isMac)}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), fileName, 'exec'), namespace)
    return namespace['selectReleaseAsset']


class SnapshotBytesTest(unittest.TestCase):

    def test_snapshots_are_the_recorded_lf_bytes(self):
        for name, sha in SNAPSHOTS.items():
            with self.subTest(snapshot=name):
                with open(os.path.join(LEGACY, name), 'rb') as f:
                    data = f.read()
                self.assertNotIn(b'\r', data)
                self.assertEqual(hashlib.sha256(data).hexdigest(), sha)


class LegacyPickTest(unittest.TestCase):

    def test_legacy_pickers_take_the_full_package_in_every_order(self):
        for snapshot in SNAPSHOTS:
            for isMac, expected in ((False, FULL_WIN), (True, FULL_MAC)):
                pick = _legacyPicker(snapshot, isMac)
                orders = list(itertools.permutations(NAMES))
                self.assertEqual(len(orders), 24)
                for order in orders:
                    with self.subTest(snapshot=snapshot, mac=isMac, order=order):
                        chosen = pick([_asset(n) for n in order])
                        self.assertIsNotNone(chosen)
                        self.assertEqual(chosen['name'], expected)

    def test_publish_check_accepts_exactly_what_the_legacy_pickers_handle(self):
        # tools/release_gates.py --check-first-packages (release.yml's last publish
        # step) against the pickers themselves: every order of every subset of a
        # release's names plus two names that must never be published
        problems = _releaseGates().firstPackageProblems
        pickers = [(_legacyPicker(snapshot, isMac), full)
                   for snapshot in SNAPSHOTS for isMac, full in ((False, FULL_WIN), (True, FULL_MAC))]
        pool = NAMES + ('Seraphine-win.7z', 'old.dmg')
        count = 0
        for size in range(len(pool) + 1):
            for order in itertools.permutations(pool, size):
                picked = [(pick([_asset(n) for n in order]), full) for pick, full in pickers]
                fine = any(p is not None for p, _ in picked) \
                    and all(p is None or p['name'] == full for p, full in picked)
                count += 1
                if (not problems(list(order))) != fine:
                    self.fail('order %r: check says %r, the legacy pickers say %s'
                              % (order, problems(list(order)), 'fine' if fine else 'wrong'))
        self.assertEqual(count, 1957)

    def test_current_picker_agrees_in_every_order(self):
        for isMac, expected in ((False, FULL_WIN), (True, FULL_MAC)):
            with mock.patch.object(platform, 'IS_MACOS', isMac):
                for order in itertools.permutations(NAMES):
                    with self.subTest(mac=isMac, order=order):
                        chosen = util.selectReleaseAsset([_asset(n) for n in order])
                        self.assertEqual(chosen['name'], expected)

    def test_current_picker_never_takes_a_delta_or_the_manifest(self):
        # even when nothing else is there
        with mock.patch.object(platform, 'IS_MACOS', False):
            self.assertIsNone(util.selectReleaseAsset([_asset(DELTA), _asset(MANIFEST_ASSET)]))
            self.assertIsNone(util.selectReleaseAsset([_asset(MANIFEST_ASSET)]))
            # the full name wins over an earlier other .7z (today's suffix rule as fallback)
            chosen = util.selectReleaseAsset([_asset('Other.7z'), _asset(FULL_WIN)])
            self.assertEqual(chosen['name'], FULL_WIN)
            self.assertEqual(util.selectReleaseAsset([_asset('Other.7z')])['name'], 'Other.7z')


if __name__ == '__main__':
    unittest.main()
