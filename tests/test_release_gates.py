"""tools/release_gates.py: the publish decisions of release.yml.

Exhaustive truth table: every combination of success / failure / skipped for the
eight jobs publish needs (check, test-win, build-win, verify-full-win, delta-win,
verify-delta-win, test-mac, build-mac) x noManifest true / false = 2 x 3^8 = 13,122 rows, against an oracle written here from the plan's
rules, not from the tool. Also the CLI: NEEDS_JSON / NO_MANIFEST parsing (fail
closed), tools/release_flags.json and the publishable-name check.
"""
import importlib.util
import io
import itertools
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gates_mod = _load('release_gates_under_test', os.path.join(ROOT, 'tools', 'release_gates.py'))

# the eight jobs publish needs
JOBS = ['check', 'test-win', 'build-win', 'verify-full-win', 'delta-win', 'verify-delta-win',
        'test-mac', 'build-mac']
STATES = ('success', 'failure', 'skipped')


def oracle(r, noManifest):
    """The plan's rules, stated independently of the tool."""
    s = {job: state == 'success' for job, state in r.items()}
    windows = s['check'] and s['test-win'] and s['build-win'] and s['verify-full-win']
    return {
        'publish_win': windows,
        'publish_manifest': windows and not noManifest,
        'publish_delta': (windows and not noManifest and s['delta-win']
                          and s['verify-delta-win']),
        'publish_mac': s['check'] and s['test-mac'] and s['build-mac'],
    }


class TruthTableTest(unittest.TestCase):

    def test_every_row(self):
        rows = 0
        for states in itertools.product(STATES, repeat=len(JOBS)):
            r = dict(zip(JOBS, states))
            needs = {job: {'result': state, 'outputs': {}} for job, state in r.items()}
            for noManifest in (True, False):
                rows += 1
                got = gates_mod.gates(needs, noManifest)
                want = oracle(r, noManifest)
                if got != want:
                    self.fail('row %s noManifest=%s: got %s, want %s' % (r, noManifest, got, want))
                # the properties, spelled out
                if got['publish_delta']:
                    self.assertTrue(got['publish_win'])
                    self.assertFalse(noManifest)
                if got['publish_manifest']:
                    self.assertTrue(got['publish_win'])
        self.assertEqual(rows, 2 * 3 ** 8)

    def test_metamorphic(self):
        """Over every row: flipping noManifest never changes win / mac; flipping either
        delta job never changes win; flipping any Windows job never changes mac."""
        flips = {'success': 'failure', 'failure': 'success', 'skipped': 'success'}
        rows = 0
        for states in itertools.product(STATES, repeat=len(JOBS)):
            r = dict(zip(JOBS, states))
            needs = {job: {'result': state} for job, state in r.items()}
            for noManifest in (True, False):
                rows += 1
                g = gates_mod.gates(needs, noManifest)
                other = gates_mod.gates(needs, not noManifest)
                self.assertEqual((g['publish_win'], g['publish_mac']),
                                 (other['publish_win'], other['publish_mac']), r)
                for job in JOBS[1:]:
                    flipped = dict(needs, **{job: {'result': flips[r[job]]}})
                    f = gates_mod.gates(flipped, noManifest)
                    if job in ('delta-win', 'verify-delta-win'):
                        self.assertEqual(f['publish_win'], g['publish_win'], (r, job))
                    if job in ('test-win', 'build-win', 'verify-full-win', 'delta-win', 'verify-delta-win'):
                        self.assertEqual(f['publish_mac'], g['publish_mac'], (r, job))
        self.assertEqual(rows, 2 * 3 ** 8)

    def test_failed_delta_leg_and_flag_never_block_windows(self):
        base = {job: {'result': 'success'} for job in JOBS}
        for job in ('delta-win', 'verify-delta-win'):
            for state in ('failure', 'skipped', 'cancelled'):
                needs = json.loads(json.dumps(base))
                needs[job]['result'] = state
                for flag in (True, False):
                    g = gates_mod.gates(needs, flag)
                    self.assertTrue(g['publish_win'])
                    self.assertFalse(g['publish_delta'])
        self.assertTrue(gates_mod.gates(base, True)['publish_win'])
        self.assertFalse(gates_mod.gates(base, True)['publish_manifest'])

    def test_mac_ignores_every_windows_job(self):
        for states in itertools.product(('success', 'failure', 'skipped', 'cancelled'), repeat=5):
            needs = {j: {'result': s} for j, s in zip(JOBS[1:6], states)}
            needs.update({'check': {'result': 'success'}, 'test-mac': {'result': 'success'},
                          'build-mac': {'result': 'success'}})
            for flag in (True, False):
                self.assertTrue(gates_mod.gates(needs, flag)['publish_mac'])

    def test_missing_or_odd_entries_block(self):
        full = {job: {'result': 'success'} for job in JOBS}
        for job in ('test-win', 'build-win', 'verify-full-win'):
            needs = dict(full)
            del needs[job]
            self.assertFalse(gates_mod.gates(needs, False)['publish_win'], job)
        for odd in ('Success', 'cancelled', '', None, 1):
            needs = dict(full, **{'verify-full-win': {'result': odd}})
            self.assertFalse(gates_mod.gates(needs, False)['publish_win'], odd)
        needs = dict(full, **{'verify-full-win': 'success'})
        self.assertFalse(gates_mod.gates(needs, False)['publish_win'])
        with self.assertRaises(gates_mod.GateError):
            gates_mod.gates([], False)
        with self.assertRaises(gates_mod.GateError):
            gates_mod.gates(full, 'false')


class CliTest(unittest.TestCase):

    def run_main(self, argv, env):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = gates_mod.main(argv, environ=env)
        return rc, out.getvalue(), err.getvalue()

    def test_outputs_for_github_output(self):
        needs = {job: {'result': 'success', 'outputs': {}} for job in JOBS}
        needs['verify-delta-win']['result'] = 'failure'
        rc, out, err = self.run_main([], {'NEEDS_JSON': json.dumps(needs), 'NO_MANIFEST': 'false'})
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.splitlines(), ['publish_win=true', 'publish_manifest=true',
                                            'publish_delta=false', 'publish_mac=true'])
        self.assertIn('verify-delta-win: failure', err)

    def test_bad_input_prints_no_decision(self):
        good = json.dumps({'check': {'result': 'success'}})
        for env in ({'NEEDS_JSON': 'not json', 'NO_MANIFEST': 'false'},
                    {'NO_MANIFEST': 'false'},
                    {'NEEDS_JSON': '[]', 'NO_MANIFEST': 'false'},
                    {'NEEDS_JSON': good, 'NO_MANIFEST': ''},
                    {'NEEDS_JSON': good, 'NO_MANIFEST': 'True'},
                    {'NEEDS_JSON': good}):
            with self.subTest(env=env):
                rc, out, err = self.run_main([], env)
                self.assertEqual(rc, 1)
                self.assertEqual(out, '')

    def test_flags_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'flags.json')
            for text, rc, want in (('{"noManifest": false}', 0, 'no_manifest=false'),
                                   ('{"noManifest": true}', 0, 'no_manifest=true'),
                                   ('{}', 0, 'no_manifest=false'),
                                   ('{"noManifest": "true"}', 1, ''),
                                   ('{"noManifest": 1}', 1, ''),
                                   ('[]', 1, ''),
                                   ('{', 1, '')):
                with self.subTest(text=text):
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(text)
                    got = self.run_main(['--flags', path], {})
                    self.assertEqual(got[0], rc, got[2])
                    self.assertEqual(got[1].strip(), want)
            self.assertEqual(self.run_main(['--flags', os.path.join(d, 'none.json')], {})[0], 1)

    def test_repo_flags_file_has_the_schema(self):
        # only the schema: the reviewed escape-hatch commit sets noManifest to true
        path = os.path.join(ROOT, 'tools', 'release_flags.json')
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data.get('noManifest'), bool)
        rc, out, err = self.run_main(['--flags', path], {})
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), 'no_manifest=%s' % ('true' if data['noManifest'] else 'false'))

    def test_check_full_names(self):
        self.assertEqual(self.run_main(['--check-full-names', 'out/Seraphine.7z', 'out/Seraphine.dmg'], {})[0], 0)
        for bad in ('out/Seraphine.filemanifest.txt', 'out/Seraphine-1.4.10-to-1.4.11.delta', 'out/x.7z'):
            with self.subTest(name=bad):
                rc, out, err = self.run_main(['--check-full-names', 'out/Seraphine.7z', bad], {})
                self.assertEqual(rc, 1)
                self.assertIn(bad, err)

    def test_check_names(self):
        ok = ['out/Seraphine.7z', 'out/Seraphine.dmg', 'out/Seraphine.filemanifest.txt',
              'out/Seraphine-1.4.10-to-1.4.11.delta']
        self.assertEqual(self.run_main(['--check-names'] + ok, {})[0], 0)
        for bad in ('out/x.7z', 'out/Seraphine-win.7z', 'out/a.dmg',
                    'out/Seraphine-1.4.10-to-1.4.11.7z', 'out/notes.md'):
            with self.subTest(name=bad):
                rc, out, err = self.run_main(['--check-names', ok[0], bad], {})
                self.assertEqual(rc, 1)
                self.assertIn(bad, err)


if __name__ == '__main__':
    unittest.main()
