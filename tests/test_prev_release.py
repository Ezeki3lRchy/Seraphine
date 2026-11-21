"""tools/prev_release.py (C8): the previous release with a Windows package, from gh output.

gh is replaced by a fake that answers the two read-only calls the tool makes, so the
test needs no network. The tool is loaded by file path, like CI runs it.
"""
import contextlib
import importlib.util
import io
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'tools', 'prev_release.py')


def _load():
    spec = importlib.util.spec_from_file_location('prev_release_under_test', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pr = _load()

# tag -> (isDraft, isPrerelease, asset names); deliberately unsorted
RELEASES = {
    'v1.4.9': (False, False, ['Seraphine.dmg', 'Seraphine.7z']),
    'v1.4.10': (False, False, ['Seraphine.7z', 'Seraphine.dmg']),
    'v1.4.5': (False, False, ['Seraphine.7z']),
    'v1.4.8': (False, False, ['Seraphine.dmg']),            # Windows shipped nothing
    'v1.4.7': (False, False, ['Seraphine.7z']),
    'v1.4.6': (False, False, ['Seraphine.7z']),
    'v1.4.11': (False, False, ['Seraphine.7z']),            # the version being released
    'v1.5.0': (True, False, ['Seraphine.7z']),              # draft
    'v1.4.12': (False, True, ['Seraphine.7z']),             # prerelease
    'v1.4.9-win': (False, False, ['Seraphine.7z']),         # not vX.Y.Z
    '1.4.9': (False, False, ['Seraphine.7z']),
}


class FakeGh:
    def __init__(self, releases=RELEASES, fail=None):
        self.releases = releases
        self.fail = fail
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if self.fail and args[:2] == self.fail:
            raise pr.GhError('gh %s failed (exit 1)' % ' '.join(args[:2]))
        if args[:2] == ['release', 'list']:
            assertReadOnly(args)
            return json.dumps([{'tagName': t, 'isDraft': d, 'isPrerelease': p}
                               for t, (d, p, _) in self.releases.items()])
        if args[:2] == ['release', 'view']:
            assertReadOnly(args)
            return json.dumps({'assets': [{'name': n} for n in self.releases[args[2]][2]]})
        raise AssertionError('unexpected gh call %r' % (args,))


def assertReadOnly(args):
    assert args[0] == 'release' and args[1] in ('list', 'view'), args


def run(argv, gh=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = pr.main(argv, gh=gh or FakeGh())
    return rc, out.getvalue(), err.getvalue()


class PrevReleaseTest(unittest.TestCase):

    def test_previous_is_the_highest_lower_release_with_the_7z(self):
        self.assertEqual(run(['--version', '1.4.11']), (0, '1.4.10\n', ''))
        self.assertEqual(run(['--version', '1.4.10']), (0, '1.4.9\n', ''))
        # 1.4.8 has no Seraphine.7z: skipped
        self.assertEqual(run(['--version', '1.4.9']), (0, '1.4.7\n', ''))
        # numeric, not string, order: 1.4.10 > 1.4.9
        self.assertEqual(run(['--version', '2.0.0']), (0, '1.4.11\n', ''))

    def test_all_self_updating(self):
        rc, out, err = run(['--version', '1.4.11', '--all-self-updating'])
        self.assertEqual((rc, err), (0, ''))
        self.assertEqual(out.splitlines(), ['1.4.6', '1.4.7', '1.4.9', '1.4.10'])

    def test_none_found_exits_1_with_nothing_on_stdout(self):
        for argv in (['--version', '1.4.5'], ['--version', '1.4.6', '--all-self-updating'],
                     ['--version', '0.0.1']):
            with self.subTest(argv=argv):
                rc, out, err = run(argv)
                self.assertEqual((rc, out), (1, ''))
                self.assertIn('no release below', err)

    def test_gh_failure_and_bad_input_exit_1(self):
        for fail in (['release', 'list'], ['release', 'view']):
            with self.subTest(fail=fail):
                rc, out, err = run(['--version', '1.4.11'], gh=FakeGh(fail=fail))
                self.assertEqual((rc, out), (1, ''))
                self.assertIn('failed', err)
        rc, out, err = run(['--version', '1.4'])
        self.assertEqual((rc, out), (1, ''))
        self.assertIn('not an X.Y.Z version', err)

        def garbage(args):
            return 'not json'
        rc, out, err = run(['--version', '1.4.11'], gh=garbage)
        self.assertEqual((rc, out), (1, ''))
        self.assertIn('unexpected gh output', err)

    def test_default_version_is_config_version(self):
        reader = pr._load('rv', os.path.join(ROOT, 'tools', 'read_version.py'))
        version = reader.read_version()
        gh = FakeGh(releases={'v0.0.1': (False, False, ['Seraphine.7z'])})
        rc, out, _ = run([], gh=gh)
        self.assertEqual((rc, out), (0, '0.0.1\n'))
        self.assertTrue(pr.parseVersion(version))

    def test_only_reads(self):
        gh = FakeGh()
        run(['--version', '1.4.11', '--all-self-updating'], gh=gh)
        self.assertTrue(gh.calls)
        for args in gh.calls:
            self.assertIn(args[:2], (['release', 'list'], ['release', 'view']))
            self.assertIn('--repo', args)
            self.assertEqual(args[args.index('--repo') + 1], 'Ezeki3lRchy/Seraphine')


if __name__ == '__main__':
    unittest.main()
