"""Names of the files a release publishes (stdlib only).

The single place that says which asset names exist. The app (asset pickers) and
the release tools (packing, publishing gates) both read it; tools load this file by
path with importlib, since importing anything else under app/ pulls in PySide6 and
the user config. Keep it free of any import outside the standard library.

- FULL_WIN / FULL_MAC: the full packages. Every self-updating version in the field
  picks the first asset ending in .7z / .dmg, so no other published name may end
  in either.
- MANIFEST_ASSET: the Windows package's filemanifest.txt, published on its own.
- Delta: ``Seraphine-<base>-to-<new>.delta`` (changed files against <base> only).
"""
import re

FULL_WIN = 'Seraphine.7z'
FULL_MAC = 'Seraphine.dmg'
MANIFEST_ASSET = 'Seraphine.filemanifest.txt'
# The C5 pattern, kept literal. Do not match with it directly: re's '\d' also takes
# non-ASCII digits and '$' accepts a trailing newline. Use parseDelta, or compile it
# with re.ASCII and use fullmatch.
DELTA_RE = r'^Seraphine-(\d+\.\d+\.\d+)-to-(\d+\.\d+\.\d+)\.delta$'

# ASCII digits only, and fullmatch: '$' alone would also accept a trailing newline
_VERSION_RE = re.compile(r'\d+\.\d+\.\d+', re.ASCII)
_DELTA = re.compile(DELTA_RE, re.ASCII)


def deltaName(base, new):
    """Asset name of the delta that updates <base> to <new> (both X.Y.Z)."""
    for v in (base, new):
        if not isinstance(v, str) or not _VERSION_RE.fullmatch(v):
            raise ValueError('not an X.Y.Z version: %r' % (v,))
    return 'Seraphine-%s-to-%s.delta' % (base, new)


def parseDelta(name):
    """(base, new) of a delta asset name, else None."""
    m = _DELTA.fullmatch(name) if isinstance(name, str) else None
    return (m.group(1), m.group(2)) if m else None


def isPublishable(name):
    """True only for the four kinds of names a release may carry."""
    return name in (FULL_WIN, FULL_MAC, MANIFEST_ASSET) or parseDelta(name) is not None
