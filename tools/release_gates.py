"""Publish decisions of release.yml (pure; stdlib only).

Usage (in release.yml's publish job):
  NEEDS_JSON='${{ toJSON(needs) }}' NO_MANIFEST=true|false python3 tools/release_gates.py >> "$GITHUB_OUTPUT"
  python3 tools/release_gates.py --flags tools/release_flags.json     (check job: prints no_manifest=...)
  python3 tools/release_gates.py --check-names <file>...             (every name must be publishable)
  python3 tools/release_gates.py --check-full-names <file>...        (only Seraphine.7z / Seraphine.dmg)

Gates (a job counts only when its result is exactly "success"; a missing job,
failure, skipped or cancelled blocks; so does a check job that did not succeed):
  publish_win      = test-win, build-win, verify-full-win
  publish_manifest = publish_win and not noManifest
  publish_delta    = publish_win and not noManifest and delta-win, verify-delta-win
  publish_mac      = test-mac, build-mac (independent of every Windows job and the flag)
A failed delta leg never blocks Windows; noManifest never blocks Windows. Whether
this run uploaded Seraphine.7z (required for the manifest asset and the delta) is
decided by the upload step itself (its `if`, checked by tools/check_release_graph.py).

A malformed NEEDS_JSON or a NO_MANIFEST other than "true" / "false" prints no
decision and exits 1 (the publish job then fails and publishes nothing).
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

WIN_JOBS = ('test-win', 'build-win', 'verify-full-win')
DELTA_JOBS = ('delta-win', 'verify-delta-win')
MAC_JOBS = ('test-mac', 'build-mac')
JOBS = ('check',) + WIN_JOBS + DELTA_JOBS + MAC_JOBS
OUTPUTS = ('publish_win', 'publish_manifest', 'publish_delta', 'publish_mac')


class GateError(Exception):
    pass


def _ok(needs, job):
    entry = needs.get(job)
    return isinstance(entry, dict) and entry.get('result') == 'success'


def gates(needs, noManifest):
    """{output: bool} for the publish step. ``needs``: toJSON(needs) parsed."""
    if not isinstance(needs, dict):
        raise GateError('needs is not a JSON object')
    if not isinstance(noManifest, bool):
        raise GateError('noManifest is not a boolean')
    checked = _ok(needs, 'check')
    win = checked and all(_ok(needs, j) for j in WIN_JOBS)
    delta = win and not noManifest and all(_ok(needs, j) for j in DELTA_JOBS)
    mac = checked and all(_ok(needs, j) for j in MAC_JOBS)
    return {'publish_win': win, 'publish_manifest': win and not noManifest,
            'publish_delta': delta, 'publish_mac': mac}


def parseFlag(text):
    if text == 'true':
        return True
    if text == 'false':
        return False
    raise GateError('NO_MANIFEST must be "true" or "false", got %r' % (text,))


def readFlags(path):
    """noManifest of tools/release_flags.json: a JSON object whose "noManifest" is a
    boolean (missing = false); anything else raises GateError."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise GateError('cannot read %s: %s' % (path, type(e).__name__)) from None
    if not isinstance(data, dict):
        raise GateError('%s is not a JSON object' % path)
    value = data.get('noManifest', False)
    if not isinstance(value, bool):
        raise GateError('%s: noManifest must be true or false' % path)
    return value


def _loadNames():
    spec = importlib.util.spec_from_file_location(
        'release_names', os.path.join(ROOT, 'app', 'common', 'release_names.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unpublishable(paths):
    names = _loadNames()
    return [p for p in paths if not names.isPublishable(os.path.basename(p))]


def notFull(paths):
    """The paths that are not a full package (the first upload carries only those)."""
    names = _loadNames()
    return [p for p in paths if os.path.basename(p) not in (names.FULL_WIN, names.FULL_MAC)]


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--flags', metavar='JSON')
    p.add_argument('--check-names', nargs='+', metavar='FILE')
    p.add_argument('--check-full-names', nargs='+', metavar='FILE')
    a = p.parse_args(argv)
    try:
        if a.check_full_names:
            bad = notFull(a.check_full_names)
            if bad:
                print('release_gates: refusing %s in the full-package upload (only Seraphine.7z / '
                      'Seraphine.dmg)' % ', '.join(bad), file=sys.stderr)
                return 1
            print('release_gates: full packages OK: %s'
                  % ', '.join(os.path.basename(x) for x in a.check_full_names))
            return 0
        if a.check_names:
            bad = unpublishable(a.check_names)
            if bad:
                print('release_gates: refusing to publish %s (not a release name, see '
                      'app/common/release_names.py)' % ', '.join(bad), file=sys.stderr)
                return 1
            print('release_gates: names OK: %s' % ', '.join(os.path.basename(x) for x in a.check_names))
            return 0
        if a.flags:
            print('no_manifest=%s' % ('true' if readFlags(a.flags) else 'false'))
            return 0
        try:
            needs = json.loads(environ.get('NEEDS_JSON', ''))
        except ValueError:
            raise GateError('NEEDS_JSON is not JSON') from None
        result = gates(needs, parseFlag(environ.get('NO_MANIFEST', '')))
    except GateError as e:
        print('release_gates: %s' % e, file=sys.stderr)
        return 1
    for key in OUTPUTS:
        print('%s=%s' % (key, 'true' if result[key] else 'false'))
    for job in JOBS:
        entry = needs.get(job)
        state = entry.get('result') if isinstance(entry, dict) else 'missing'
        print('release_gates: %s: %s' % (job, state), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
