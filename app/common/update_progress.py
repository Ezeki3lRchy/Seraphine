"""Update download progress: the text shown for a state and a UI throttle.

No widgets: used by MainWindow (background download), UpdateMessageBox (dialog) and the
settings card, which all show the same wording. States are those of
BackgroundUpdater's ``onProgress(state, version, cur, total, kind)``; the signal
payload may also carry 'idle' (the card shows its normal text again).
"""
import time

from PySide6.QtCore import QCoreApplication

STATES = ('downloading', 'verifying', 'extracting', 'waiting', 'ready', 'failed')

# failure reasons (update_stage.failureReason) -> what the user is told
REASONS = ('network', 'server', 'damaged', 'disk', 'busy', 'other')

MB = 1024 * 1024


def _mb(n, total):
    # whole MB for a full package; one decimal for a small (delta) download
    return f"{n / MB:.0f}" if total >= 10 * MB else f"{n / MB:.1f}"


def reasonText(reason):
    """Plain-English cause of a failure for a reason code of REASONS."""
    if reason == 'network':
        return QCoreApplication.translate("UpdateProgress", "Couldn't reach GitHub")
    if reason == 'server':
        return QCoreApplication.translate("UpdateProgress", "GitHub returned an error")
    if reason == 'damaged':
        return QCoreApplication.translate("UpdateProgress", "The download was damaged")
    if reason == 'disk':
        return QCoreApplication.translate("UpdateProgress", "Couldn't save the update files")
    if reason == 'busy':
        return QCoreApplication.translate("UpdateProgress", "Another update is already in progress")
    return QCoreApplication.translate("UpdateProgress", "Something went wrong")


def nextStepText(reason):
    """What the user can do after a failure with that reason code."""
    if reason == 'disk':
        return QCoreApplication.translate("UpdateProgress", "Free up disk space, then press Check now.")
    if reason == 'busy':
        return QCoreApplication.translate("UpdateProgress", "Try again after it finishes.")
    return QCoreApplication.translate("UpdateProgress", "Press Check now to try again.")


def progressText(state, version, cur=0, total=0, kind='full', reason=None):
    """One line for the settings card / info bar, e.g.
    ``Downloading 1.4.11 - 42% (63 of 150 MB)``. ``reason``: a code of REASONS
    (failed only; None when the cause is not known here)."""
    percent = min(100, cur * 100 // total) if total > 0 else None
    if state == 'downloading':
        if percent is not None:
            text = QCoreApplication.translate(
                "UpdateProgress", "Downloading {version} - {percent}% ({cur} of {total} MB)").format(
                version=version, percent=percent,
                cur=_mb(min(cur, total), total), total=_mb(total, total))
        else:
            # content-length missing (chunked): only the size so far
            text = QCoreApplication.translate(
                "UpdateProgress", "Downloading {version} - {cur} MB").format(
                version=version, cur=f"{cur / MB:.1f}")
        if kind == 'delta':
            text += QCoreApplication.translate("UpdateProgress", " (changes only)")
        return text
    if state == 'verifying':
        return QCoreApplication.translate(
            "UpdateProgress", "Verifying {version}...").format(version=version)
    if state == 'extracting':
        return QCoreApplication.translate(
            "UpdateProgress", "Unpacking {version}...").format(version=version)
    if state == 'waiting':
        if percent is not None:
            return QCoreApplication.translate(
                "UpdateProgress",
                "Update {version} paused while you're in a game ({percent}%); "
                "it resumes afterwards").format(version=version, percent=percent)
        return QCoreApplication.translate(
            "UpdateProgress",
            "Update {version} paused while you're in a game; it resumes afterwards").format(
            version=version)
    if state == 'ready':
        return QCoreApplication.translate(
            "UpdateProgress", "Update {version} is ready; restart to install it").format(
            version=version)
    if state == 'failed':
        if reason:
            return QCoreApplication.translate(
                "UpdateProgress", "Update {version} failed: {reason}. {next}").format(
                version=version, reason=reasonText(reason), next=nextStepText(reason))
        return QCoreApplication.translate(
            "UpdateProgress", "Update {version} failed. Press Check now to try again.").format(
            version=version)
    return ''


def progressPayload(state, version, cur=0, total=0, kind='full', reason=None, source='background'):
    """What ``signalBus.updateProgressChanged`` carries. ``source``: 'background'
    (BackgroundUpdater) or 'dialog' (UpdateMessageBox)."""
    return {'state': state, 'version': version, 'cur': cur, 'total': total,
            'kind': kind, 'reason': reason, 'source': source}


class Throttle:
    """At most one update per ``interval`` seconds within a state; the first update
    of a state (or of another version) and the last one of a download (cur reached
    total) always pass, so the final value is never dropped."""

    def __init__(self, interval=0.25, clock=time.monotonic):
        self.interval = interval
        self.clock = clock
        self._key = None
        self._last = None

    def allow(self, state, version, cur=0, total=0):
        now = self.clock()
        key = (state, version)
        final = state == 'downloading' and total > 0 and cur >= total
        if key != self._key or final or self._last is None \
                or now - self._last >= self.interval:
            self._key, self._last = key, now
            return True
        return False
