"""Print VERSION from app/common/config.py (single source for scripts and CI).

Usage: python tools/read_version.py [path-to-config.py]

Prints the value of the first ``VERSION = "..."`` line: exactly one line, nothing
else. If the file cannot be read or has no such line it prints nothing to stdout,
writes a message to stderr and exits with status 1. Reads the file as text; it
never imports the app.
"""
import os
import re
import sys

DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app', 'common', 'config.py')
PATTERN = re.compile(r'^VERSION = "([^"]+)"', re.M)


def read_version(path=DEFAULT):
    with open(path, encoding='utf-8') as f:
        m = PATTERN.search(f.read())
    return m.group(1) if m else None


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    try:
        version = read_version(target)
    except OSError as e:
        sys.exit('read_version: cannot read %s: %s' % (target, e.strerror or e))
    if not version:
        sys.exit('read_version: no VERSION = "..." line in ' + target)
    print(version)
