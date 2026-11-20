"""Seraphine test package.

Side effect on import (unittest discover imports this before any test module):
the app logger's file handler is swapped for an in-memory list, so test runs
never write into log/Seraphine_<date>_ERROR.log. That real log is what the
task-4 acceptance greps for 'reveal-hidden', so it must only ever contain
lines from actual client sessions. Tests read LOG_RECORDS to assert on output.
"""
import atexit
import logging
import os
import shutil
import sys
import tempfile

# The app reads its config from %APPDATA%\Seraphine at import time. Point that at
# an empty temp dir before any app module is imported, so a test run never reads
# (or writes) the developer's real config and behaves the same as on a CI runner,
# where no config exists. Windows only: on macOS the location derives from HOME,
# which is not safe to redirect for a whole process.
if sys.platform == 'win32':
    _appdata = tempfile.mkdtemp(prefix='seraphine-tests-appdata-')
    os.environ['APPDATA'] = _appdata
    atexit.register(shutil.rmtree, _appdata, ignore_errors=True)

import app.common.logger  # noqa: E402,F401 — instantiates the "Seraphine" logger

LOG_RECORDS = []


class _ListHandler(logging.Handler):
    def emit(self, record):
        LOG_RECORDS.append(self.format(record))


_seraphine = logging.getLogger("Seraphine")
for _h in list(_seraphine.handlers):
    _seraphine.removeHandler(_h)
    _h.close()

_handler = _ListHandler()
_handler.setFormatter(logging.Formatter('%(levelname)s - [%(TAG)s] %(message)s'))
_seraphine.addHandler(_handler)
