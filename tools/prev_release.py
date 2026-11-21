"""Print the previous Windows release of this repo (read-only, through gh).

Usage: python tools/prev_release.py [--version X.Y.Z] [--repo OWNER/NAME] [--all-self-updating]

Prints the highest vX.Y.Z release below VERSION (app/common/config.py, or
--version) that has a Seraphine.7z asset, as X.Y.Z on one line. With
--all-self-updating, prints every such release from 1.4.6 on (the first version
that can update itself), lowest first, one per line. Drafts, prereleases and tags
that are not vX.Y.Z are ignored. Prints nothing to stdout and exits 1 when there
is no such release, VERSION / --version is not X.Y.Z, or gh fails; a usage error
(unknown option) exits 2 (argparse).

Only reads: `gh release list` and `gh release view --json assets`. Never imports
the app (app/common/release_names.py and tools/read_version.py are loaded by path).
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = 'Ezeki3lRchy/Seraphine'
FIRST_SELF_UPDATING = (1, 4, 6)
TAG_RE = re.compile(r'v(\d+)\.(\d+)\.(\d+)', re.ASCII)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_names = _load('release_names', os.path.join(ROOT, 'app', 'common', 'release_names.py'))


class GhError(Exception):
    pass


def _gh(args):
    """stdout of `gh <args>`; GhError on a missing gh or a non-zero exit."""
    try:
        r = subprocess.run(['gh'] + args, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GhError('gh %s: %s' % (args[0], type(e).__name__)) from None
    if r.returncode != 0:
        first = (r.stderr or '').strip().splitlines()[:1]
        raise GhError('gh %s failed (exit %d)%s' % (
            ' '.join(args[:2]), r.returncode, (': ' + first[0]) if first else ''))
    return r.stdout


def parseVersion(text):
    m = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', text or '', re.ASCII)
    return tuple(int(x) for x in m.groups()) if m else None


def candidates(releases, below):
    """[(version tuple, tag)] of the published vX.Y.Z releases below `below`, highest first."""
    out = []
    for r in releases:
        if r.get('isDraft') or r.get('isPrerelease'):
            continue
        m = TAG_RE.fullmatch(r.get('tagName') or '')
        if not m:
            continue
        v = tuple(int(x) for x in m.groups())
        if v < below:
            out.append((v, r['tagName']))
    return sorted(set(out), reverse=True)


def previous(releases, below, hasFull):
    """X.Y.Z of the highest candidate with a full Windows package, else None."""
    for v, tag in candidates(releases, below):
        if hasFull(tag):
            return '%d.%d.%d' % v
    return None


def allSelfUpdating(releases, below, hasFull):
    """Every candidate >= 1.4.6 with a full Windows package, lowest first."""
    return ['%d.%d.%d' % v for v, tag in reversed(candidates(releases, below))
            if v >= FIRST_SELF_UPDATING and hasFull(tag)]


def main(argv=None, gh=_gh):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--version', help='X.Y.Z to look below (default: VERSION in app/common/config.py)')
    p.add_argument('--repo', default=REPO)
    p.add_argument('--all-self-updating', action='store_true')
    a = p.parse_args(argv)

    version = a.version
    if version is None:
        reader = _load('read_version', os.path.join(HERE, 'read_version.py'))
        try:
            version = reader.read_version()
        except OSError as e:
            version = None
            print('prev_release: cannot read VERSION: %s' % type(e).__name__, file=sys.stderr)
    below = parseVersion(version)
    if below is None:
        print('prev_release: not an X.Y.Z version: %r' % (version,), file=sys.stderr)
        return 1

    try:
        releases = json.loads(gh(['release', 'list', '--repo', a.repo, '--limit', '1000',
                                  '--exclude-drafts', '--json', 'tagName,isDraft,isPrerelease']))

        def hasFull(tag):
            view = json.loads(gh(['release', 'view', tag, '--repo', a.repo, '--json', 'assets']))
            return any(x.get('name') == release_names.FULL_WIN for x in view.get('assets') or [])

        if a.all_self_updating:
            found = allSelfUpdating(releases, below, hasFull)
        else:
            one = previous(releases, below, hasFull)
            found = [one] if one else []
    except GhError as e:
        print('prev_release: %s' % e, file=sys.stderr)
        return 1
    except (ValueError, TypeError, AttributeError, KeyError) as e:
        print('prev_release: unexpected gh output (%s)' % type(e).__name__, file=sys.stderr)
        return 1

    if not found:
        print('prev_release: no release below %s with %s in %s'
              % (version, release_names.FULL_WIN, a.repo), file=sys.stderr)
        return 1
    for v in found:
        print(v)
    return 0


if __name__ == '__main__':
    sys.exit(main())
