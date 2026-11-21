"""Download, verify and stage a release for the in-place updater (no UI).

Layout under ``<config dir>/update`` (``stageBase``):
  Seraphine.7z / Seraphine-update.dmg  the downloaded asset (.7z removed after extraction)
  Seraphine.delta                      Windows: a downloaded delta (removed after extraction)
  package/                             Windows: the extracted release (updater source dir;
                                       after an updater run it may be pruned, i.e. no
                                       longer equal to the asset)
  staged.json                          marker: version, asset digest, staged path, size,
                                       kind ('full' | 'delta'; missing = full)
  backup/, backup.meta, backup.added.txt  Windows updater: install files it replaces
  result.json                          Windows updater: outcome of the last run
Staging only ever replaces the asset, package/ and the marker; the updater's files
are left alone.

Outside the stage dir (survives clearStage and restarts):
  <config dir>/update-fallback.json    {"nodelta": [version], "failed9": {version:
                                       {"count", "lastTime"}}}: no delta for a version
                                       whose delta failed; no automatic staging of a
                                       version after two distinct failed:9 runs

Windows delta (``Seraphine-<base>-to-<new>.delta``, only from exactly the previous
version): staged only when it fits the install (every manifest entry the delta does
not ship matches the installed file), else the full package in the same call.

Used by the background updater (MainWindow) and by UpdateMessageBox, so a release
is never downloaded twice.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
import weakref

import aiohttp

from app.common import platform
from app.common.config import cfg, VERSION
from app.common.logger import logger
from app.common.util import (parseVersion, selectDeltaAsset, selectReleaseAsset,
                             verifyAssetDigest)

TAG = "UpdateStage"

MARKER_NAME = 'staged.json'
RESULT_NAME = 'result.json'
ARCHIVE_NAME = 'Seraphine.7z'
DMG_NAME = 'Seraphine-update.dmg'
DELTA_NAME = 'Seraphine.delta'
PACKAGE_NAME = 'package'
FALLBACK_NAME = 'update-fallback.json'
MANIFEST_NAME = 'filemanifest.txt'
MANIFEST_HEADER = '#seraphine-manifest 1'

# failed:9 runs ("package does not match the install") of one version after which
# that version is no longer staged automatically (the manual-download bar instead)
FAILED9_LIMIT = 2

# update-fallback.json: os.replace attempts and the pause between them
_REPLACE_ATTEMPTS = 3
_REPLACE_DELAY = 0.2

# Gameflow phases during which nothing is downloaded and no restart is offered
GAME_PHASES = ('ChampSelect', 'GameStart', 'InProgress', 'Reconnect')

DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=3600, connect=20, sock_read=60)

# how long "Restart now" waits for the updater's first log line before giving up
# (generous: PowerShell start-up can be slow under antivirus scanning)
UPDATER_START_TIMEOUT = 25.0

# an applyStagedUpdate is in flight (UI thread): a second call is refused
_applying = False
# an updater was confirmed started by this process (sticky: the app is exiting and
# the updater will use the stage; nothing may be re-staged under it)
_applyStarted = False

# one staging at a time per event loop (background updater vs. update dialog)
_stageLocks = weakref.WeakKeyDictionary()


class StageError(Exception):
    pass


class DeltaMisfit(StageError):
    """The delta cannot be applied over this install (precheck, manifest)."""


def stageBase():
    return os.path.join(platform.config_dir(), 'update')


def releaseVersion(info):
    return str(info.get('tag_name') or '').lstrip('vV')


def isNewer(info, currentVersion):
    return parseVersion(releaseVersion(info)) > parseVersion(currentVersion)


def _sha256Digest(asset):
    digest = (asset or {}).get('digest') or ''
    return digest if digest.lower().startswith('sha256:') and digest[7:].strip() else ''


def hasDigest(info):
    """The release's platform asset carries a sha256 digest. Required on every
    path (see verifyAssetDigest): without it nothing is downloaded or staged, and
    the background path leaves the release to the dialog, which reports it."""
    return bool(_sha256Digest(selectReleaseAsset(info.get('assets', []))))


def downloadProxy():
    """The configured HTTP proxy, as the GitHub check uses it (aiohttp needs a scheme)."""
    if not cfg.get(cfg.enableProxy):
        return None
    addr = (cfg.get(cfg.proxyAddr) or '').strip()
    if not addr:
        return None
    return addr if '://' in addr else 'http://' + addr


def _readJson(path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def readMarker(base):
    return _readJson(os.path.join(base, MARKER_NAME))


def readResult(base):
    """Outcome of the last Windows updater run: {'status': 'ok' | 'failed:<code>' |
    'restored' | 'restore-failed', 'code', 'version'} or None."""
    return _readJson(os.path.join(base, RESULT_NAME))


def writeMarker(base, data):
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, MARKER_NAME)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    os.replace(tmp, path)


def clearStage(base):
    """Remove everything under base, including the Windows updater's backup and
    result: only for a run that succeeded or a stage that belongs to the past."""
    shutil.rmtree(base, ignore_errors=True)


# what staging creates (marker first, so a half-cleared stage is never valid);
# the updater's backup/, backup.meta, backup.added.txt and result.json are not here
# (Seraphine.delta*: the delta package, staged by the delta path)
_STAGING_NAMES = (MARKER_NAME, MARKER_NAME + '.tmp',
                  ARCHIVE_NAME, ARCHIVE_NAME + '.part', ARCHIVE_NAME + '.part.json',
                  DMG_NAME, DMG_NAME + '.part', DMG_NAME + '.part.json',
                  DELTA_NAME, DELTA_NAME + '.part', DELTA_NAME + '.part.json',
                  PACKAGE_NAME, PACKAGE_NAME + '.part')


def _clearStaging(base, check=False, keep=()):
    """Remove only the staging artifacts, keeping the rollback data and the last
    result. With ``check``, raise StageError if any of them survived (e.g. locked
    by antivirus): a leftover must never be extracted into / merged with a stage.
    ``keep``: names left in place (a partial download that is resumed)."""
    for name in _STAGING_NAMES:
        if name in keep:
            continue
        path = os.path.join(base, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass
    left = [n for n in _STAGING_NAMES
            if n not in keep and os.path.lexists(os.path.join(base, n))]
    try:
        os.rmdir(base)  # only if nothing else (backup, result) is in it
    except OSError:
        pass
    if check and left:
        raise StageError(f"could not remove an earlier stage: {', '.join(left)}")


def _hasRollback(base):
    """The Windows updater left a backup (an unfinished restore)."""
    return any(os.path.lexists(os.path.join(base, n))
               for n in ('backup', 'backup.meta', 'backup.added.txt'))


def _clearRollback(base):
    """Remove the updater's backup, its manifest and fingerprint, and the result."""
    shutil.rmtree(os.path.join(base, 'backup'), ignore_errors=True)
    for name in ('backup.meta', 'backup.added.txt', RESULT_NAME):
        try:
            os.remove(os.path.join(base, name))
        except OSError:
            pass


def fallbackPath():
    """``<config dir>/update-fallback.json``: outside stageBase(), so clearStage and
    restarts keep it."""
    return os.path.join(platform.config_dir(), FALLBACK_NAME)


def _version(text):
    return str(text or '').lstrip('vV')


def readFallback(path=None):
    """``{'nodelta': [version], 'failed9': {version: {'count': n, 'lastTime': t}}}``.
    A missing, unreadable or corrupt file (and any malformed entry) reads as empty;
    never raises."""
    path = path or fallbackPath()
    out = {'nodelta': [], 'failed9': {}}
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
    except FileNotFoundError:
        return out
    except Exception as e:
        logger.warning(f"{FALLBACK_NAME} unreadable ({type(e).__name__}); ignoring it", TAG)
        return out
    if not isinstance(data, dict):
        logger.warning(f"{FALLBACK_NAME} malformed; ignoring it", TAG)
        return out
    nodelta = data.get('nodelta')
    for v in nodelta if isinstance(nodelta, list) else []:
        if isinstance(v, str) and v and v not in out['nodelta']:
            out['nodelta'].append(v)
    failed9 = data.get('failed9')
    for v, entry in (failed9.items() if isinstance(failed9, dict) else ()):
        count = entry.get('count') if isinstance(entry, dict) else None
        if v and isinstance(count, int) and not isinstance(count, bool) and count > 0:
            out['failed9'][v] = {'count': count, 'lastTime': str(entry.get('lastTime') or '')}
    return out


def _writeFallback(data, path=None):
    """Atomic write; os.replace is retried (e.g. antivirus holding the file). A
    failure is logged (type name) and never raised. @return: True when written."""
    path = path or fallbackPath()
    tmp = path + '.tmp'
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return True
            except OSError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_DELAY)
    except OSError as e:
        logger.warning(f"could not write {FALLBACK_NAME} ({type(e).__name__})", TAG)
        _removeQuietly(tmp)
    return False


def pruneFallback(currentVersion, path=None):
    """Drop the entries of versions not newer than ``currentVersion`` (start-up)."""
    data = readFallback(path)
    cur = parseVersion(currentVersion)
    kept = {'nodelta': [v for v in data['nodelta'] if parseVersion(v) > cur],
            'failed9': {v: e for v, e in data['failed9'].items() if parseVersion(v) > cur}}
    if kept != data:
        _writeFallback(kept, path)


def failed9Count(version, path=None):
    entry = readFallback(path)['failed9'].get(_version(version))
    return entry['count'] if entry else 0


def updateBlocked(version, path=None):
    """``version`` failed with failed:9 in FAILED9_LIMIT distinct runs: it is not
    staged automatically again (per version, whatever its digest, until the running
    version moves past it)."""
    return failed9Count(version, path) >= FAILED9_LIMIT


def deltaExcluded(version, path=None):
    """A delta of ``version`` failed before: the full package is used."""
    return _version(version) in readFallback(path)['nodelta']


def _addNoDelta(version, path=None):
    """@return: True when ``version`` is in nodelta afterwards."""
    data = readFallback(path)
    if not version or version in data['nodelta']:
        return bool(version)
    data['nodelta'].append(version)
    return _writeFallback(data, path)


def _recordFailed9(result, path=None):
    """Count a failed:9 run once: only when its ``time`` (written by the updater)
    differs from the stored ``lastTime``.
    @return: 'counted' (now), 'seen' (counted before) or 'unrecorded' (the file
    could not be written: the run is counted at the next read)."""
    version = _version(result.get('version'))
    if not version:
        return 'seen'
    when = str(result.get('time') or '')
    data = readFallback(path)
    entry = data['failed9'].get(version)
    if entry is not None and entry['lastTime'] == when:
        return 'seen'
    count = (entry['count'] if entry else 0) + 1
    data['failed9'][version] = {'count': count, 'lastTime': when}
    if not _writeFallback(data, path):
        return 'unrecorded'
    logger.warning(f"update {version} did not match the install (failed:9, run {count})", TAG)
    return 'counted'


# a delta stage with these results keeps today's handling (the Retry bar, or the
# manual-download bar for restore-failed/8); only its delta is excluded
_KEEP_STAGE_STATUSES = ('restored', 'restore-failed')


def _consumeFailedResult(base, result, marker):
    """The updater results that change what is staged next. A failed:9 ("package
    does not match the install; nothing changed") of any kind is counted once and
    its stage goes, so the next stage downloads fresh. A delta-kind stage's
    failed:9, or any other failed result about it, also excludes the delta for
    that version; its stage goes too, except after restored / restore-failed (kept
    and reported as for a full stage). Rollback rules unchanged: while a restore is
    pending its backup and result stay; when update-fallback.json could not be
    written, result.json stays as well, so the next read records the run.
    @return: True when the stage was cleared."""
    status = str((result or {}).get('status') or '')
    if not status or status == 'ok':
        return False
    version = _version(result.get('version'))
    isDelta = bool(marker) and marker.get('kind') == 'delta' \
        and _version(marker.get('version')) == version
    recorded = True
    if status == 'failed:9':
        outcome = _recordFailed9(result)
        if outcome == 'seen':
            return False  # this run was handled before
        recorded = outcome == 'counted'
    elif not isDelta:
        return False
    if isDelta:
        if not deltaExcluded(version):
            recorded = _addNoDelta(version) and recorded
            logger.info(f"the delta for {version} failed ({status}); "
                        f"the full package is used next", TAG)
        if status in _KEEP_STAGE_STATUSES:
            return False
    if _hasRollback(base) or not recorded:
        _clearStaging(base)
    else:
        clearStage(base)
    return True


def _resultFor(result, marker, base):
    """``result`` reports on ``marker``'s release, or on an unfinished restore that
    the next updater run completes before it merges whatever is staged."""
    return str(result.get('version') or '') == str(marker.get('version') or '') \
        or _hasRollback(base)


def _backupBelongsToInstall(base, installDir):
    """Python mirror of the Windows updater's step 3 (WINDOWS_UPDATER_SCRIPT in
    app/common/update.py, "an earlier run left a backup"): the backup is applied
    only if backup.meta has non-empty preExe and pkgExe and the installed
    Seraphine.exe's SHA-256 equals one of them (case-insensitive); otherwise the
    script discards it as foreign. Without backup/ the script never applies
    backup.meta / backup.added.txt either (it removes them).
    @return: True (the next run completes this restore), False (it discards the
    backup), None (cannot tell: no install dir, i.e. not a packaged build)."""
    if not os.path.exists(os.path.join(base, 'backup')):
        return False
    meta = _readJson(os.path.join(base, 'backup.meta')) or {}
    pre, pkg = meta.get('preExe'), meta.get('pkgExe')
    if not pre or not pkg:
        return False
    if not installDir:
        return None
    try:
        h = hashlib.sha256()
        with open(os.path.join(installDir, 'Seraphine.exe'), 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
    except FileNotFoundError:
        return False  # the script's Get-ExeHash gives $null: no match
    except OSError:
        # e.g. a sharing violation while antivirus scans the exe: the script decides
        # later, once the app has exited, so never drop the backup on a read error
        return None
    current = h.hexdigest().lower()
    return current in (str(pre).lower(), str(pkg).lower())


def repairBlocks(result, version):
    """``result`` is restore-failed/8 (even completing an earlier restore failed),
    or failed:9 of a version blocked by updateBlocked (two distinct runs), and
    ``version`` is not newer than the release that run was for. Such a release
    is not offered as ready again (the run would stop the same way); the user is
    pointed at the manual download. A newer release gets one attempt: its run
    completes the pending restore first, then merges."""
    if not result or parseVersion(version) > parseVersion(result.get('version')):
        return False
    if result.get('status') == 'restore-failed':
        return result.get('code') == 8
    return result.get('status') == 'failed:9' and updateBlocked(result.get('version'))


def failedRepair(offeredVersion, base=None):
    """What the update-ready bar shows instead of offering ``offeredVersion``
    while repairBlocks applies and the backup is still there (the valid marker or
    {}, with ``lastResult``; equal to what the start-up stagedUpdate returns for a
    valid stage), or ``{'lastResult': {'status': 'failed:9', 'code': 9, 'version':
    v}}`` while ``offeredVersion`` is blocked by repeated failed:9 runs (with or
    without a backup), else None. No hashing: cheap enough for the UI thread; a
    foreign backup is dropped by the start-up pass."""
    base = base or stageBase()
    result = readResult(base)
    if repairBlocks(result, offeredVersion) and _hasRollback(base):
        marker = readMarker(base)
        return dict(marker if _markerValid(marker, base) else {}, lastResult=result)
    version = _version(offeredVersion)
    if version and updateBlocked(version):
        return {'lastResult': {'status': 'failed:9', 'code': 9, 'version': version}}
    return None


def _withResult(marker, base):
    """``marker`` with ``lastResult`` when the last failed run reports on it, the
    same dict stagedUpdate returns at start-up (so the bars agree). A failed:9 is
    never reported: it was counted and its stage removed (_consumeFailedResult)."""
    result = readResult(base)
    if result is not None and result.get('status') not in ('ok', 'failed:9') \
            and _resultFor(result, marker, base):
        return dict(marker, lastResult=result)
    return marker


def _expectedPath(base, platformName):
    if platformName == 'win32':
        return os.path.join(base, PACKAGE_NAME)
    if platformName == 'darwin':
        return os.path.join(base, DMG_NAME)
    return None


def _samePath(a, b):
    return os.path.normcase(os.path.normpath(os.path.abspath(a))) == \
        os.path.normcase(os.path.normpath(os.path.abspath(b)))


def _markerValid(marker, base):
    """Marker is for this platform, points at the expected staged file inside base
    (a tampered path elsewhere is refused), and the staged files are in place."""
    if not marker or marker.get('platform') != sys.platform:
        return False
    if not _sha256Digest(marker):
        # staged without a digest (an earlier build allowed it): never applied
        return False
    path = marker.get('path') or ''
    expected = _expectedPath(base, marker.get('platform'))
    if not path or not expected or not _samePath(path, expected):
        return False
    if marker.get('platform') == 'win32':
        return os.path.isfile(os.path.join(path, 'Seraphine.exe')) and \
            os.path.isfile(os.path.join(path, 'filelist.txt'))
    return os.path.isfile(path) and os.path.getsize(path) == marker.get('size', -1)


def _installKey(installDir):
    """How a delta-kind marker records the install it was checked against."""
    return os.path.normcase(os.path.normpath(os.path.abspath(installDir))) if installDir else ''


def _deltaMarkerCurrent(marker, currentVersion, installDir):
    """A delta-kind marker was staged on exactly this install (``installDir``) by
    exactly the running version, its base (or the running version already reports
    the staged one: a half-applied run being retried). Other markers: True."""
    if not marker or marker.get('kind') != 'delta':
        return True
    running = _version(currentVersion)
    return bool(installDir) and marker.get('installDir') == _installKey(installDir) \
        and running in (_version(marker.get('base')), _version(marker.get('version')))


def stagedUpdate(currentVersion, base=None, installDir=None):
    """The staged update to offer on start-up, or None.

    After a successful updater run everything is cleared. After a failed run the
    stage is kept (even if the versions look equal: a half-applied install may
    report the new version) and returned with ``lastResult`` so the user can retry,
    if the result is about that release or about an unfinished restore. A backup
    the updater would discard as foreign (``installDir``: default the packaged
    install; hashes its Seraphine.exe, so call this off the UI thread) is dropped
    with its result; without a backup, a failed run older than the running
    version is dropped.
    Otherwise stale data is removed: another platform, files gone, a path outside
    the stage dir, or a version that is not newer than the running one (the
    backup and result of an unfinished restore are kept then).
    A failed:9 run (any kind) is counted once in update-fallback.json and its stage
    removed, as is a delta-kind stage after any other failed:<n> run, so the next
    check downloads fresh; after restored / restore-failed a delta stage is kept
    and reported like a full one. Any failed delta run puts its version in nodelta.
    A delta staged by another version or for another install is stale (removed,
    nothing counted). Entries of update-fallback.json not newer than
    ``currentVersion`` are dropped.
    """
    base = base or stageBase()
    pruneFallback(currentVersion)  # start-up: versions not newer than this one
    if not os.path.exists(base):
        return None
    if _updaterRunning(base):
        # an updater owns the stage right now: never clean anything under it
        return None

    result = readResult(base)
    marker = readMarker(base)
    if result and result.get('status') == 'ok':
        logger.info(f"update {result.get('version')} applied; clearing stage", TAG)
        clearStage(base)
        return None

    if _hasRollback(base):
        # whose backup it is decides by the updater's own fingerprint rule, never
        # by version order (a failed downgrade is older than the running version
        # and still needs its backup): a backup the next run would discard as
        # foreign (e.g. a version installed by hand since) goes with its result
        if _backupBelongsToInstall(base, installDir or platform.install_dir()) is False:
            logger.info(f"the updater's backup does not belong to this install; "
                        f"discarding it and the result {result}", TAG)
            _clearRollback(base)
            result = None
    elif result is not None and \
            parseVersion(result.get('version')) < parseVersion(currentVersion):
        # no backup: a failed run older than the running version belongs to the past
        logger.info(f"failed update {result.get('version')} is older than the running "
                    f"{currentVersion}; discarding its result", TAG)
        _clearRollback(base)
        result = None

    if not _deltaMarkerCurrent(marker, currentVersion, installDir or platform.install_dir()):
        # staged by another version or for another install (a shared config dir, a
        # reinstall): stale. A failed:9 run of it is still a real run of that
        # version: counted (once), then its result goes; nothing else is recorded
        logger.info("removing a staged delta made for another version or install", TAG)
        recorded = True
        if result is not None and result.get('status') == 'failed:9':
            recorded = _recordFailed9(result) != 'unrecorded'
            if recorded:
                _removeQuietly(os.path.join(base, RESULT_NAME))  # it changed nothing
        if _hasRollback(base) or not recorded:
            _clearStaging(base)
        else:
            clearStage(base)
        return None

    # failed:9 (any kind) and a failed delta: counted / recorded, the stage goes
    # and the next check downloads fresh (the full package after a failed delta)
    if _consumeFailedResult(base, result, marker):
        return None
    if result is not None and result.get('status') == 'failed:9':
        result = None  # a run counted before: nothing to retry or report

    valid = _markerValid(marker, base)
    # a result about another release with nothing left to complete is ignored
    if result is not None and valid and _resultFor(result, marker, base):
        logger.warning(f"last update run did not complete: {result}", TAG)
        return dict(marker, lastResult=result)

    if not valid or parseVersion(marker.get('version')) <= parseVersion(currentVersion):
        logger.info(f"removing stale staged update: {marker}", TAG)
        if _hasRollback(base):
            # an unfinished restore: keep its backup and result for the next
            # updater run (it completes the restore first, or discards a foreign one)
            _clearStaging(base)
        else:
            clearStage(base)
        return None

    return marker


def stagedFor(info, base=None, installDir=None):
    """The marker if exactly this release (version + sha256 digest) is staged: the
    full package, or (kind 'delta') its delta, only while the release still
    publishes that delta, does not say noDelta, the version is not in nodelta, and
    the marker was staged by the running version on this install (``installDir``,
    default the packaged install). A staged delta the kill switches turn off is so
    replaced by the full package at the next update check."""
    base = base or stageBase()
    marker = readMarker(base)
    digest = _sha256Digest(selectReleaseAsset(info.get('assets', [])))
    if not digest or not _markerValid(marker, base) \
            or marker.get('version') != releaseVersion(info):
        return None
    if marker.get('kind') == 'delta':
        usable = not info.get('noDelta') and not deltaExcluded(releaseVersion(info)) \
            and _deltaMarkerCurrent(marker, VERSION, installDir or platform.install_dir())
        delta = selectDeltaAsset(info.get('assets', []), VERSION, releaseVersion(info)) \
            if usable else None
        digest = _sha256Digest(delta)
    if not digest or marker.get('digest', '') != digest:
        return None
    return marker


def _updaterRunning(base):
    from app.common.update import LOCK_NAME, updaterRunning
    return updaterRunning(os.path.join(base, LOCK_NAME))


def markerApplicable(marker, base=None, currentVersion=None, allowDowngrade=False,
                     installDir=None):
    """``marker`` (e.g. the one a "Restart now" bar was created for) is exactly what
    is staged on disk now, that stage is valid, and applying it cannot downgrade:
    its version is newer than the running one, or equal only for the Retry of a
    failed run (a half-applied install already reports the new version). A delta
    must have been staged by the running version on this install (``installDir``,
    default the packaged install).

    ``allowDowngrade`` (only the update dialog, only for a forbidden running
    version): any version other than the running one is accepted."""
    base = base or stageBase()
    disk = readMarker(base)
    if not marker or not disk:
        return False

    def strip(m):
        return {k: v for k, v in m.items() if k != 'lastResult'}

    if strip(disk) != strip(marker) or not _markerValid(disk, base):
        return False
    current = VERSION if currentVersion is None else currentVersion
    if not _deltaMarkerCurrent(disk, current, installDir or platform.install_dir()):
        return False

    staged = parseVersion(disk.get('version'))
    running = parseVersion(current)
    if staged > running or (allowDowngrade and staged != running):
        return True
    result = readResult(base)
    return staged == running and bool(marker.get('lastResult')) \
        and result is not None and result.get('status') != 'ok' \
        and _resultFor(result, disk, base)


def _probeWritable(directory):
    probe = os.path.join(directory, f'.seraphine-write-probe-{os.getpid()}')
    try:
        with open(probe, 'wb') as f:
            f.write(b'x')
        os.remove(probe)
        return True
    except OSError:
        return False


def _macAppPath(executable=None):
    """The .app bundle that runs ``executable`` (default sys.executable): the path
    up to the component ending in .app directly above Contents/MacOS/<executable>,
    or None when the executable is not inside a bundle (e.g. a source run)."""
    parts = (executable or sys.executable).split('/')
    if len(parts) >= 4 and parts[-3] == 'Contents' and parts[-2] == 'MacOS' \
            and parts[-1] and parts[-4].endswith('.app') and parts[-4] != '.app':
        return '/'.join(parts[:-3])
    return None


def installWritable():
    """The updater can write where it has to (Windows: the install dir and its
    _internal; macOS: the folder holding the .app). False from source and for a
    drive-root install (the updater refuses those)."""
    root = platform.install_dir()
    if not root or os.path.dirname(os.path.abspath(root)) == os.path.abspath(root):
        return False
    if platform.IS_MACOS:
        app = _macAppPath()
        return bool(app) and _probeWritable(os.path.dirname(app))
    dirs = [root]
    internal = os.path.join(root, '_internal')
    if os.path.isdir(internal):
        dirs.append(internal)
    return all(_probeWritable(d) for d in dirs)


async def _blocking(func, *args):
    """Run func in a worker thread. If cancelled meanwhile, wait for the thread to
    finish (it cannot be interrupted) before re-raising, so cleanup is safe."""
    fut = asyncio.get_running_loop().run_in_executor(None, func, *args)
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        try:
            await fut
        except Exception:
            pass
        raise


def resumeKey(asset):
    """What a kept partial download must belong to: the asset's stable download URL
    (never the signed CDN URL it redirects to), sha256 digest and size. None when
    the asset has no size or digest (such a download is never resumed)."""
    size = (asset or {}).get('size')
    if not isinstance(size, int) or size <= 0 or not _sha256Digest(asset) \
            or not asset.get('browser_download_url'):
        return None
    return {'browser_download_url': asset['browser_download_url'],
            'digest': asset['digest'], 'size': size}


def _partMatches(part, key):
    """``part`` + ``part.json`` were written for exactly ``key``."""
    return key is not None and os.path.isfile(part) \
        and _readJson(part + '.json') == key


def _contentRangeOk(value, start, size):
    """A 206 answer covers exactly bytes start..size-1 of a size-byte file."""
    m = re.fullmatch(r'\s*bytes\s+(\d+)-(\d+)/(\d+)\s*', value or '', re.IGNORECASE)
    return bool(m) and (int(m[1]), int(m[2]), int(m[3])) == (start, size - 1, size)


def _removeQuietly(*paths):
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


async def downloadAsset(url, dest, onProgress=None, session=None, proxy=None, resume=None):
    """Stream url to dest (via dest + '.part'). Raises on HTTP errors / timeouts.

    ``resume`` (resumeKey of the asset): a cancelled download keeps ``.part`` and
    ``.part.json`` (= the key); a matching pair is continued with ``Range: bytes=N-``.
    Only a 206 whose Content-Range is exactly ``bytes N-(size-1)/size`` is appended;
    a 200 is taken from byte 0, a 416 or any other 206 restarts from 0. Any failure
    other than a cancellation removes both files; after success ``.part.json`` stays
    (the caller removes it once the file is verified and staged, so a pause during
    verification can still resume). The caller verifies the digest of the whole
    file. Without ``resume`` a partial file is never kept or reused.
    @return: (bytes received now, offset resumed from)
    """
    part = dest + '.part'
    meta = part + '.json'
    total = resume['size'] if resume else 0
    ownSession = session is None
    sess = session or aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT)
    try:
        offset = os.path.getsize(part) if _partMatches(part, resume) else 0
        if offset > total:
            offset = 0
        if resume and not offset:
            _removeQuietly(part)
            with open(meta, 'w', encoding='utf-8') as f:
                json.dump(resume, f)
        resumedFrom = offset
        if resume and offset == total:
            # complete before (paused while verifying / unpacking): the digest decides
            os.replace(part, dest)
            return 0, resumedFrom

        while True:
            kwargs = {'timeout': DOWNLOAD_TIMEOUT, 'proxy': proxy}
            if offset:
                kwargs['headers'] = {'Range': f'bytes={offset}-'}
            async with sess.get(url, **kwargs) as resp:
                mode = 'wb'
                if offset:
                    if resp.status == 206 and _contentRangeOk(
                            resp.headers.get('content-range'), offset, total):
                        mode = 'ab'
                    elif resp.status == 200:
                        logger.info("download: the server ignored the range; restarting", TAG)
                        offset = resumedFrom = 0
                    else:
                        logger.info(f"download: resume refused (HTTP {resp.status}); "
                                    f"restarting", TAG)
                        offset = resumedFrom = 0
                        _removeQuietly(part)
                        continue
                elif resp.status != 200:
                    raise StageError(f"HTTP {resp.status}")
                # content-length may be missing (chunked) or 0
                length = total or int(resp.headers.get('content-length') or 0)
                cur = 0
                with open(part, mode) as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
                        cur += len(chunk)
                        if total and offset + cur > total:
                            raise StageError("download is larger than the release asset")
                        if onProgress:
                            onProgress('download', offset + cur, length)
            break
        if length and offset + cur != length:
            raise StageError(f"incomplete download ({offset + cur} of {length} bytes)")
        os.replace(part, dest)
        return cur, resumedFrom
    except asyncio.CancelledError:
        if not resume:
            _removeQuietly(part)
        raise
    except BaseException:
        _removeQuietly(part, meta)
        raise
    finally:
        if ownSession:
            await sess.close()


def _extract(archive, dest):
    import py7zr  # only the updater needs it; keeps start-up fast

    with py7zr.SevenZipFile(archive, mode='r') as z:
        z.extractall(dest)


# The in-app delta precheck: a Python mirror of the Windows updater's step 4b
# (WINDOWS_UPDATER_SCRIPT in app/common/update.py: Read-Manifest, Read-FileList,
# Resolve-Entry, Test-FileMatches), run on the extracted delta before it is staged.
# The install has no filemanifest.txt (the updater never merges it): the manifest
# is the one inside the downloaded, digest-verified delta.

_SHA256_RE = re.compile(r'[0-9a-fA-F]{64}', re.ASCII)
_SIZE_RE = re.compile(r'\s*\+?(\d+)\s*', re.ASCII)
# the files 4b never prunes from the package
_KEEP_IN_PACKAGE = ('seraphine.exe', 'filelist.txt', 'filemanifest.txt')


def _lines(path):
    with open(path, 'rb') as f:
        text = f.read().decode('utf-8', errors='replace')
    return re.split(r'\r\n|\n|\r', text)


def readManifest(path):
    """filemanifest.txt (``#seraphine-manifest 1``, then ``sha256 TAB size TAB
    path``) -> [(sha, size, path)]; None without a usable manifest (missing, or any
    other first line); DeltaMisfit when it is unreadable or malformed."""
    try:
        lines = _lines(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise DeltaMisfit(f"the manifest is unreadable ({type(e).__name__})")
    if lines[0].lstrip('\ufeff') != MANIFEST_HEADER:
        return None
    entries = []
    for number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = line.split('\t', 2)
        size = _SIZE_RE.fullmatch(parts[1]) if len(parts) == 3 else None
        if size is None or not _SHA256_RE.fullmatch(parts[0]) or not parts[2].strip():
            raise DeltaMisfit(f"the manifest is malformed (line {number})")
        entries.append((parts[0], int(size.group(1)), parts[2].strip()))
    return entries


def _readFileList(path):
    entries = []
    for raw in _lines(path):
        entry = raw.strip().lstrip('\ufeff').strip()
        if entry:
            entries.append(entry)
    return entries


def _entryParts(entry):
    """A filelist / manifest entry -> its path components under the install dir,
    or None (absolute, parent reference, the install dir itself, or a segment
    ending in '.' or a space: Windows would trim it, so the updater's GetFullPath
    could name another file; such a delta falls back to the full package)."""
    rel = entry.replace('/', '\\')
    if ':' in rel or rel.startswith('\\'):
        return None
    segments = rel.split('\\')
    if any(s != '.' and s.endswith(('.', ' ')) for s in segments):
        return None
    return [s for s in segments if s not in ('', '.')] or None


def _trimmedByWindows(entry):
    """``entry`` is refused by _entryParts only for a segment ending in '.' or a
    space (the updater would resolve it, trimmed, instead of skipping it like an
    absolute path or a parent reference)."""
    rel = entry.replace('/', '\\')
    if ':' in rel or rel.startswith('\\'):
        return False
    segments = rel.split('\\')
    if any(s and s != '.' and not s.strip('. ') for s in segments):
        return False  # a parent reference: the updater skips it too
    return any(s != '.' and s.endswith(('.', ' ')) for s in segments)


def _sha256File(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _fileMatches(path, size, sha):
    """The file exists with this size and SHA-256 (case-insensitive); a read error
    counts as no."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) == size \
            and _sha256File(path) == sha.lower()
    except OSError:
        return False


def deltaFits(packageDir, installDir):
    """The extracted delta ``packageDir`` can be applied over ``installDir`` (what
    the updater's step 4b requires, so it will not refuse with failed:9): a usable
    manifest that lists exactly the new filelist's files, once each; every package
    file matches its entry (or the installed file does, then 4b prunes it); every
    entry the package does not ship matches the installed file by size + SHA-256;
    every filelist entry without a manifest entry is in the package.
    @return: the number of manifest entries checked. Raises DeltaMisfit on a misfit."""
    manifest = readManifest(os.path.join(packageDir, MANIFEST_NAME))
    if manifest is None:
        raise DeltaMisfit(f"the delta has no usable {MANIFEST_NAME}")
    fileList = _readFileList(os.path.join(packageDir, 'filelist.txt'))
    listed = set()
    for entry in fileList:
        parts = _entryParts(entry)
        if parts:
            listed.add('\\'.join(parts).lower())
        elif _trimmedByWindows(entry):
            # the updater resolves it with GetFullPath (trimmed) and refuses with 9
            raise DeltaMisfit(f"the new filelist has an entry Windows would trim ({entry})")
    covered = set()
    for sha, size, entry in manifest:
        parts = _entryParts(entry)
        rel = '\\'.join(parts) if parts else entry
        key = rel.lower()
        if not parts:
            raise DeltaMisfit(f"the manifest is malformed (invalid path {entry})")
        if key in covered:
            raise DeltaMisfit(f"the manifest is malformed ({rel} listed twice)")
        if key not in listed:
            raise DeltaMisfit(f"the manifest is malformed ({rel} is not in the new filelist)")
        covered.add(key)
        installed = os.path.join(installDir, *parts)
        shipped = os.path.join(packageDir, *parts)
        if os.path.isfile(shipped):
            if not _fileMatches(shipped, size, sha) and \
                    (key in _KEEP_IN_PACKAGE or not _fileMatches(installed, size, sha)):
                raise DeltaMisfit(f"the delta's {rel} differs from its manifest")
        elif not _fileMatches(installed, size, sha):
            raise DeltaMisfit(f"the install does not match the delta ({rel} differs)")
    for entry in fileList:
        parts = _entryParts(entry)
        rel = '\\'.join(parts) if parts else ''
        if parts and rel.lower() not in covered \
                and not os.path.isfile(os.path.join(packageDir, *parts)):
            raise DeltaMisfit(f"the delta lacks {rel}")
    return len(covered)


def _stageLock():
    loop = asyncio.get_running_loop()
    lock = _stageLocks.get(loop)
    if lock is None:
        lock = _stageLocks[loop] = asyncio.Lock()
    return lock


async def stagedUpdateAsync(currentVersion, base=None):
    """stagedUpdate off the UI thread, serialised with staging: its clean-up can
    never remove a stage the dialog / background updater is creating right now."""
    async with _stageLock():
        return await asyncio.get_running_loop().run_in_executor(
            None, stagedUpdate, currentVersion, base)


async def stageRelease(info, base=None, onProgress=None, session=None, resumable=False,
                       installDir=None):
    """Download, verify and (Windows) extract the release's platform asset.

    Short-circuits when exactly this release is already staged. On any failure or
    cancellation nothing is left staged and partial files are removed; except with
    ``resumable``, where a cancellation (a game started) keeps the partial download
    (``<asset>.part`` + ``.part.json``) and the next call for the same asset (stable
    URL, digest, size) continues it with an HTTP range. The digest is always checked
    on the whole file; a mismatch removes everything. ``info['noResume']`` (ver.json
    field switch): never resumed, an earlier partial download is discarded.
    Windows: the release's delta from exactly the running version is tried first
    (unless ``info['noDelta']``, no install, a pending restore, or an earlier delta
    of this version failed) and staged only if it fits ``installDir`` (default the
    packaged install); any delta failure stages the full package in the same call
    (a deterministic one also puts the version in nodelta). A paused download of
    either package is kept while it matches its resume key. A version blocked by repeated failed:9 runs raises StageError before anything
    is downloaded.
    ``onProgress(phase, cur, total)``: phase 'download' | 'verify' | 'extract'; a
    callback with a true ``takesKind`` attribute also gets the kind ('full' |
    'delta') as a fourth argument.
    @return: the marker dict, with ``lastResult`` when the last failed run reports on
    it (as stagedUpdate returns it). Raises StageError / network errors /
    CancelledError.
    """
    async with _stageLock():
        return await _stageRelease(info, base or stageBase(), onProgress, session,
                                   resumable, installDir)


def _kindReporter(onProgress, kind):
    """onProgress for one kind of package: the update dialog's three-argument
    callback as before, BackgroundUpdater's (``takesKind``) with the kind."""
    if onProgress is None or not getattr(onProgress, 'takesKind', False):
        return onProgress
    return lambda phase, cur, total: onProgress(phase, cur, total, kind)


def _deltaFor(info, base, installDir):
    """The delta asset to try for this release, or None (the reason is logged when
    the release has one that is not used)."""
    if sys.platform != 'win32':
        return None
    version = releaseVersion(info)
    delta = selectDeltaAsset(info.get('assets', []), VERSION, version)
    if delta is None:
        return None
    if info.get('noDelta'):
        why = 'switched off for this version'
    elif not installDir:
        why = 'no packaged install'
    elif _hasRollback(base):
        why = 'a restore is pending'
    elif deltaExcluded(version):
        why = 'an earlier delta of this version failed'
    else:
        return delta
    logger.info(f"delta not used ({why}); staging the full package", TAG)
    return None


def _partialNames(assetPath):
    name = os.path.basename(assetPath)
    return name + '.part', name + '.part.json'


def _deltaFailedForGood(e):
    """The delta failed for a reason another download would not change (it does
    not fit this install, has no usable manifest, a wrong digest, an archive that
    does not unpack or lacks its files): its version goes into nodelta. Not for
    HTTP / network / disk errors (the delta is tried again next time)."""
    if isinstance(e, DeltaMisfit):
        return True
    if isinstance(e, StageError):
        return str(e).startswith(('verification failed', 'package has no'))
    return type(e).__module__.split('.')[0] == 'py7zr'


async def _stageRelease(info, base, onProgress, session, resumable=False, installDir=None):
    asset = selectReleaseAsset(info.get('assets', []))
    if asset is None:
        raise StageError("no install asset for this platform")
    if not _sha256Digest(asset):
        # refused before anything is downloaded or cleared (see verifyAssetDigest)
        raise StageError("verification failed: the release asset has no sha256 digest")

    # never touch the stage an updater is (about to be) working from
    if _applying or _applyStarted or _updaterRunning(base):
        raise StageError("an update is being applied")

    result = readResult(base)
    if result and result.get('status') == 'ok':
        # a finished run the start-up pass has not cleaned yet: its 'ok' branch
        # would later remove everything, including the stage created now
        clearStage(base)
    else:
        # a failed:9 / failed delta the start-up pass has not seen yet (first, so
        # that a run counted now already blocks below)
        _consumeFailedResult(base, result, readMarker(base))
    if updateBlocked(releaseVersion(info)):
        # two distinct failed:9 runs of this version: the manual download instead
        raise StageError("update blocked after repeated failures")

    installDir = installDir or platform.install_dir()
    marker = stagedFor(info, base, installDir)
    if marker:
        logger.info(f"update {marker['version']} already staged", TAG)
        return _withResult(marker, base)

    # Replace only the staging artifacts. The updater's backup and result.json are
    # kept, also after an unresolved 'restore-failed': the next updater run (for
    # whatever release is staged then) first completes that restore from the
    # backup, or stops with restore-failed/8 merging nothing, so a newer release
    # can still be staged and offered without losing the way back. A failure report
    # about another release that left no backup is obsolete and goes with the stage.
    isWindows = sys.platform == 'win32'
    resumable = resumable and not info.get('noResume')
    delta = _deltaFor(info, base, installDir)
    assetPath = os.path.join(base, ARCHIVE_NAME if isWindows else DMG_NAME)
    deltaPath = os.path.join(base, DELTA_NAME)
    key = resumeKey(asset) if resumable else None
    deltaKey = resumeKey(delta) if resumable and delta is not None else None

    def kept(path, k):
        # a partial download is kept while it matches its own resume key
        return _partialNames(path) if _partMatches(path + '.part', k) else ()

    _clearStaging(base, check=True, keep=kept(assetPath, key) + kept(deltaPath, deltaKey))
    result = readResult(base)
    if result is not None and not _hasRollback(base) \
            and str(result.get('version') or '') != releaseVersion(info):
        try:
            os.remove(os.path.join(base, RESULT_NAME))
        except OSError:
            pass
    os.makedirs(base, exist_ok=True)

    if delta is not None:
        try:
            marker = await _stagePackage(info, delta, deltaPath, 'delta', base,
                                         onProgress, session, deltaKey, installDir,
                                         preserve=kept(assetPath, key))
            return _withResult(marker, base)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # our own messages carry no URL; anything else: the type name only
            why = str(e) if isinstance(e, StageError) else type(e).__name__
            logger.info(f"delta not used ({why}); staging the full package", TAG)
            if _deltaFailedForGood(e):
                _addNoDelta(releaseVersion(info))
        # may remove an empty base; a paused full download stays
        _clearStaging(base, check=True, keep=kept(assetPath, key))
        os.makedirs(base, exist_ok=True)

    marker = await _stagePackage(info, asset, assetPath, 'full', base, onProgress,
                                 session, key, installDir)
    return _withResult(marker, base)


async def _stagePackage(info, asset, assetPath, kind, base, onProgress, session, key,
                        installDir, preserve=()):
    """Download ``asset`` to ``assetPath``, verify it and (Windows) extract it into
    package/; a delta must also carry a usable manifest and fit ``installDir``.
    Writes the marker. Failure / cancellation: see stageRelease; ``preserve``:
    staging names (the other package's partial download) never removed here."""
    isWindows = sys.platform == 'win32'
    package = os.path.join(base, PACKAGE_NAME)
    partial = _partialNames(assetPath)
    report = _kindReporter(onProgress, kind)
    try:
        started = time.monotonic()
        received, resumedFrom = await downloadAsset(
            asset['browser_download_url'], assetPath, report, session,
            downloadProxy(), resume=key)
        logger.info(f"download: {received} B in {time.monotonic() - started:.1f} s "
                    f"(resumed from {resumedFrom})", TAG)

        if report:
            report('verify', 0, 0)
        started = time.monotonic()
        ok, detail = await _blocking(verifyAssetDigest, assetPath, asset)
        logger.info(f"verify: {time.monotonic() - started:.1f} s", TAG)
        if not ok:
            raise StageError(f"verification failed: {detail}")

        if isWindows:
            if report:
                report('extract', 0, 0)
            started = time.monotonic()
            await _blocking(_extract, assetPath, package + '.part')
            logger.info(f"extract: {time.monotonic() - started:.1f} s", TAG)
            for required in ('Seraphine.exe', 'filelist.txt'):
                if not os.path.isfile(os.path.join(package + '.part', required)):
                    raise StageError(f"package has no {required}")
            if kind == 'delta':
                started = time.monotonic()
                checked = await _blocking(deltaFits, package + '.part', installDir)
                logger.info(f"delta precheck: {checked} files in "
                            f"{time.monotonic() - started:.1f} s", TAG)
            os.replace(package + '.part', package)
            os.remove(assetPath)
            path, size = package, 0
        else:
            path, size = assetPath, os.path.getsize(assetPath)

        marker = {
            'version': releaseVersion(info),
            'digest': asset.get('digest') or '',
            'platform': sys.platform,
            'path': path,
            'size': size,
        }
        if kind == 'delta':
            # what it was checked against: only this version on this install
            marker.update(kind='delta', base=VERSION, installDir=_installKey(installDir))
        _removeQuietly(assetPath + '.part.json')
        writeMarker(base, marker)
        if kind == 'delta':
            logger.info(f"delta staged: {asset.get('name')}", TAG)
        logger.info(f"update {marker['version']} staged at {path}", TAG)
        return marker
    except asyncio.CancelledError:
        if key is None:
            _clearStaging(base, keep=preserve)
            raise
        # paused: keep the download for the next run (a complete but unverified
        # file goes back to .part; the next run verifies it without downloading)
        if os.path.isfile(assetPath) and os.path.isfile(assetPath + '.part.json'):
            try:
                os.replace(assetPath, assetPath + '.part')
            except OSError:
                pass
        own = partial if _partMatches(assetPath + '.part', key) else ()
        _clearStaging(base, keep=own + tuple(preserve))
        raise
    except BaseException:
        _clearStaging(base, keep=preserve)
        raise


async def applyStagedUpdate(marker, base=None, startTimeout=UPDATER_START_TIMEOUT,
                            allowDowngrade=False):
    """Start the platform updater for ``marker`` if it is still exactly what is
    staged, and wait until the updater reports it is running.

    ``allowDowngrade``: see markerApplicable (update dialog, forbidden version only).
    @return: True when the updater runs; the caller then exits the app. False when
    the stage changed / is invalid, or the updater never started (it is killed so
    it cannot act later) - the app should stay running.
    """
    global _applying, _applyStarted
    if _applying:
        logger.warning("an update is already being applied", TAG)
        return False
    _applying = True
    try:
        started = await _applyStagedUpdate(marker, base or stageBase(), startTimeout,
                                           allowDowngrade)
        if started:
            _applyStarted = True
        return started
    finally:
        _applying = False


async def _applyStagedUpdate(marker, base, startTimeout, allowDowngrade):
    from app.common.update import runUpdater, runUpdaterMac, updaterStarted

    if not markerApplicable(marker, base, allowDowngrade=allowDowngrade):
        logger.warning(f"refusing to apply a stale staged update: {marker}", TAG)
        return False

    if sys.platform == 'win32':
        launch = runUpdater(marker['path'], targetVersion=marker.get('version', ''),
                            stageDir=base)
    elif sys.platform == 'darwin' and _macAppPath():
        launch = runUpdaterMac(marker['path'], _macAppPath(),
                               version=marker.get('version', ''))
    else:
        launch = None
    if not launch:
        logger.warning("updater not started (not packaged, bad package, or already running)",
                       TAG)
        return False

    deadline = time.monotonic() + startTimeout
    while time.monotonic() < deadline:
        if updaterStarted(launch.logPath):
            return True
        if launch.process.poll() is not None:
            break
        await asyncio.sleep(0.1)

    if updaterStarted(launch.logPath):
        return True
    logger.error(f"updater did not start (log: {launch.logPath})", TAG)
    try:
        launch.process.kill()
    except OSError:
        pass
    return False


def failureReason(e):
    """What went wrong, as a code of update_progress.REASONS (the UI words it):
    'network' (GitHub / the CDN not reachable, a timeout, a cut connection),
    'server' (an HTTP error status), 'damaged' (digest mismatch, short / oversized
    download, an archive that does not unpack or lacks its files), 'disk' (files
    could not be written or removed), 'busy' (an update is being applied), 'other'.
    Never the exception's text (an aiohttp error's text can carry the URL)."""
    if isinstance(e, StageError):
        text = str(e)
        if text.startswith('HTTP '):
            return 'server'
        if text.startswith(('verification failed', 'incomplete download',
                            'download is larger', 'package has no')):
            return 'damaged'
        if text.startswith('an update is being applied'):
            return 'busy'
        if text.startswith('could not remove'):
            return 'disk'
        return 'other'
    if isinstance(e, (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError)):
        return 'network'
    if type(e).__module__.split('.')[0] == 'py7zr':
        return 'damaged'
    if isinstance(e, OSError):
        return 'disk'
    return 'other'


class BackgroundUpdater:
    """Stages updates in the background, never during a game.

    Lives on the UI thread (qasync loop). The game state is tri-state: unknown
    (``gaming is None``, at start-up / while a client connects), gaming, or not.
    Nothing is downloaded and no "ready" is reported unless it is known that no
    game is in progress. ``onReady(marker)`` reports a staged update; one found
    while gaming/unknown is held. ``onStarting(info)`` is called right before a
    download starts. Failures are logged and dropped; the next update check
    offers the release again.

    ``onProgress(state, version, cur, total, kind)`` (state: downloading |
    verifying | extracting | waiting | ready | failed; kind: full | delta) reports
    the staging; after 'failed', ``lastError`` holds the failureReason code. ``resumable``:
    a download paused by a game continues from the partial file (stageRelease).
    With neither set, the stage is called as ``stage(info, base)``.
    """

    def __init__(self, onReady, base=None, stage=None, onStarting=None,
                 onProgress=None, resumable=False):
        self.onReady = onReady
        self.onStarting = onStarting
        self.onProgress = onProgress
        self.resumable = resumable
        self.base = base
        self.stage = stage or stageRelease
        self.task = None
        self.pendingInfo = None
        self.heldMarker = None
        self.gaming = None
        self.pausedByGame = False
        self.kind = 'full'
        self.lastError = None
        self._lastBytes = (0, 0)

    def __report(self, state, version, cur=0, total=0):
        if self.onProgress:
            self.onProgress(state, version, cur, total, self.kind)

    @staticmethod
    def __offerable(marker):
        # a stage the user can restart into (not a repair that keeps failing)
        version = marker.get('version')
        return bool(version) and not repairBlocks(marker.get('lastResult'), version)

    def __deliver(self, marker):
        self.kind = marker.get('kind') or 'full'
        self.onReady(marker)
        if self.__offerable(marker):
            self.__report('ready', marker.get('version'))

    def setGameStatusUnknown(self):
        self.gaming = None

    def setGameStatus(self, status):
        """status: a gameflow phase, or None for "no client running"."""
        self.gaming = status in GAME_PHASES
        if self.gaming:
            if self.task is not None and not self.task.done():
                logger.info("game started; pausing update download", TAG)
                self.pausedByGame = True
                self.task.cancel()
            return

        if self.heldMarker is not None:
            marker, self.heldMarker = self.heldMarker, None
            self.__deliver(marker)
        self.__maybeStart()

    def offer(self, info):
        self.pendingInfo = info
        self.__maybeStart()
        if self.onProgress and self.gaming is True and not self.isBusy() \
                and not self.__alreadyStaged(info):
            self.__report('waiting', releaseVersion(info))

    def __alreadyStaged(self, info):
        # held for the end of the game, or on disk: nothing waits to be downloaded
        held = self.heldMarker
        if held is not None and held.get('version') == releaseVersion(info):
            return True
        return stagedFor(info, self.base) is not None

    def notifyReady(self, marker):
        if self.gaming is False:
            self.__deliver(marker)
        else:
            # held until no game is known to run: the card says so (not "Unpacking...")
            self.heldMarker = marker
            self.kind = marker.get('kind') or 'full'
            if self.__offerable(marker):
                self.__report('waiting', marker.get('version'))

    def isBusy(self):
        return self.task is not None and not self.task.done()

    def __maybeStart(self):
        if self.gaming is not False or self.pendingInfo is None or self.isBusy():
            return
        self.pausedByGame = False
        if self.onStarting:
            self.onStarting(self.pendingInfo)
        self.task = asyncio.ensure_future(self.__run(self.pendingInfo))

    def __stageProgress(self, version, phase, cur, total, *rest):
        if rest:
            self.kind = rest[0]  # 'delta' while a delta is staged, 'full' otherwise
        if phase == 'download':
            self._lastBytes = (cur, total)
            self.__report('downloading', version, cur, total)
        elif phase == 'verify':
            self.__report('verifying', version)
        elif phase == 'extract':
            self.__report('extracting', version)

    async def __run(self, info):
        marker = None
        version = releaseVersion(info)
        try:
            if self.onProgress is None and not self.resumable:
                marker = await self.stage(info, self.base)
            else:
                self._lastBytes = (0, 0)
                self.kind = 'full'

                def progress(*a):
                    self.__stageProgress(version, *a)
                progress.takesKind = True  # stageRelease passes the kind
                marker = await self.stage(info, self.base, onProgress=progress,
                                          resumable=self.resumable)
        except asyncio.CancelledError:
            logger.info("update download cancelled", TAG)
            if not self.pausedByGame:
                self.pendingInfo = None  # cancelled for another reason (exit)
            else:
                self.__report('waiting', version, *self._lastBytes)
        except Exception as e:
            # the type name only: an aiohttp error's text can carry the URL
            self.lastError = failureReason(e)
            logger.warning(f"background update failed: {type(e).__name__} "
                           f"({self.lastError})", TAG)
            if self.pendingInfo is info:
                self.pendingInfo = None
            self.__report('failed', version)
        finally:
            self.task = None

        if marker is None:
            # paused by a game: pendingInfo is kept and resumed once the game ends,
            # which may already be the case if a cancelled extraction just finished
            self.__maybeStart()
            return

        if self.pendingInfo is info:
            self.pendingInfo = None
        self.notifyReady(marker)
