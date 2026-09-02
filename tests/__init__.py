"""Seraphine test package.

Side effect on import (unittest discover imports this before any test module):
the app logger's file handler is swapped for an in-memory list, so test runs
never write into log/Seraphine_<date>_ERROR.log. That real log is what the
task-4 acceptance greps for 'reveal-hidden', so it must only ever contain
lines from actual client sessions. Tests read LOG_RECORDS to assert on output.
"""
import logging

import app.common.logger  # noqa: F401 — instantiates the "Seraphine" logger

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
