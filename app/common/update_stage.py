"""Download, verify and stage a release for the in-place updater (no UI).

Layout under ``<config dir>/update`` (``stageBase``):
  Seraphine.7z / Seraphine-update.dmg  the downloaded asset (.7z removed after extraction)
  package/                             Windows: the extracted release (updater source dir)
  staged.json                          marker: version, asset digest, staged path, size
  backup/                              Windows updater: install files it replaces
  result.json                          Windows updater: outcome of the last run

Used by the background updater (MainWindow) and by UpdateMessageBox, so a release
is never downloaded twice.
"""
import asyncio
import json
import os
import shutil
import sys
import time
import weakref

import aiohttp

from app.common import platform
from app.common.config import cfg, VERSION
from app.common.logger import logger
from app.common.util import parseVersion, selectReleaseAsset, verifyAssetDigest

TAG = "UpdateStage"

MARKER_NAME = 'staged.json'
RESULT_NAME = 'result.json'
ARCHIVE_NAME = 'Seraphine.7z'
DMG_NAME = 'Seraphine-update.dmg'
PACKAGE_NAME = 'package'

# Gameflow phases during which nothing is downloaded and no restart is offered
GAME_PHASES = ('ChampSelect', 'GameStart', 'InProgress', 'Reconnect')

DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=3600, connect=20, sock_read=60)

# how long "Restart now" waits for the updater's first log line before giving up
# (generous: PowerShell start-up can be slow under antivirus scanning)
UPDATER_START_TIMEOUT = 25.0

# an applyStagedUpdate is in flight (UI thread): a second call is refused
_applying = False

# one staging at a time per event loop (background updater vs. update dialog)
_stageLocks = weakref.WeakKeyDictionary()


class StageError(Exception):
    pass


def stageBase():
    return os.path.join(platform.config_dir(), 'update')


def releaseVersion(info):
    return str(info.get('tag_name') or '').lstrip('vV')


def isNewer(info, currentVersion):
    return parseVersion(releaseVersion(info)) > parseVersion(currentVersion)


def _sha256Digest(asset):
    digest = (asset or {}).get('digest') or ''
    return digest if digest.lower().startswith('sha256:') and len(digest) > 7 else ''


def hasDigest(info):
    """The release's platform asset carries a sha256 digest (required for the
    silent background path)."""
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
    shutil.rmtree(base, ignore_errors=True)


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
    path = marker.get('path') or ''
    expected = _expectedPath(base, marker.get('platform'))
    if not path or not expected or not _samePath(path, expected):
        return False
    if marker.get('platform') == 'win32':
        return os.path.isfile(os.path.join(path, 'Seraphine.exe')) and \
            os.path.isfile(os.path.join(path, 'filelist.txt'))
    return os.path.isfile(path) and os.path.getsize(path) == marker.get('size', -1)


def stagedUpdate(currentVersion, base=None):
    """The staged update to offer on start-up, or None.

    After a successful updater run everything is cleared. After a failed run the
    stage is kept (even if the versions look equal: a half-applied install may
    report the new version) and returned with ``lastResult`` so the user can retry.
    Otherwise stale data is removed: another platform, files gone, a path outside
    the stage dir, or a version that is not newer than the running one.
    """
    base = base or stageBase()
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

    valid = _markerValid(marker, base)
    if result is not None and valid:
        if parseVersion(marker.get('version')) < parseVersion(currentVersion):
            # something newer was installed since (e.g. by hand): the failed
            # stage, its result and its backup belong to the past -> drop them
            logger.info(f"failed update {marker.get('version')} is older than the running "
                        f"{currentVersion}; discarding its stage and backup", TAG)
            clearStage(base)
            return None
        logger.warning(f"last update run did not complete: {result}", TAG)
        return dict(marker, lastResult=result)

    if not valid or parseVersion(marker.get('version')) <= parseVersion(currentVersion):
        logger.info(f"removing stale staged update: {marker}", TAG)
        clearStage(base)
        return None

    return marker


def stagedFor(info, base=None):
    """The marker if exactly this release (version + sha256 digest) is staged."""
    base = base or stageBase()
    marker = readMarker(base)
    digest = _sha256Digest(selectReleaseAsset(info.get('assets', [])))
    if not digest or not _markerValid(marker, base) \
            or marker.get('version') != releaseVersion(info) \
            or marker.get('digest', '') != digest:
        return None
    return marker


def _updaterRunning(base):
    from app.common.update import LOCK_NAME, updaterRunning
    return updaterRunning(os.path.join(base, LOCK_NAME))


def markerApplicable(marker, base=None, currentVersion=None, allowDowngrade=False):
    """``marker`` (e.g. the one a "Restart now" bar was created for) is exactly what
    is staged on disk now, that stage is valid, and applying it cannot downgrade:
    its version is newer than the running one, or equal only for the Retry of a
    failed run (a half-applied install already reports the new version).

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

    staged = parseVersion(disk.get('version'))
    running = parseVersion(VERSION if currentVersion is None else currentVersion)
    if staged > running or (allowDowngrade and staged != running):
        return True
    result = readResult(base)
    return staged == running and bool(marker.get('lastResult')) \
        and result is not None and result.get('status') != 'ok'


def _probeWritable(directory):
    probe = os.path.join(directory, f'.seraphine-write-probe-{os.getpid()}')
    try:
        with open(probe, 'wb') as f:
            f.write(b'x')
        os.remove(probe)
        return True
    except OSError:
        return False


def _macAppPath():
    executable = sys.executable
    idx = executable.find('.app')
    return executable[:idx + len('.app')] if idx != -1 else executable


def installWritable():
    """The updater can write where it has to (Windows: the install dir and its
    _internal; macOS: the folder holding the .app). False from source and for a
    drive-root install (the updater refuses those)."""
    root = platform.install_dir()
    if not root or os.path.dirname(os.path.abspath(root)) == os.path.abspath(root):
        return False
    if platform.IS_MACOS:
        return _probeWritable(os.path.dirname(_macAppPath()))
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


async def downloadAsset(url, dest, onProgress=None, session=None, proxy=None):
    """Stream url to dest (via dest + '.part'). Raises on HTTP errors / timeouts."""
    part = dest + '.part'
    ownSession = session is None
    sess = session or aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT)
    try:
        async with sess.get(url, timeout=DOWNLOAD_TIMEOUT, proxy=proxy) as resp:
            if resp.status != 200:
                raise StageError(f"HTTP {resp.status}")
            # content-length may be missing (chunked) or 0
            length = int(resp.headers.get('content-length') or 0)
            cur = 0
            with open(part, 'wb') as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    f.write(chunk)
                    cur += len(chunk)
                    if onProgress:
                        onProgress('download', cur, length)
        if length and cur != length:
            raise StageError(f"incomplete download ({cur} of {length} bytes)")
        os.replace(part, dest)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    finally:
        if ownSession:
            await sess.close()


def _extract(archive, dest):
    import py7zr  # only the updater needs it; keeps start-up fast

    with py7zr.SevenZipFile(archive, mode='r') as z:
        z.extractall(dest)


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


async def stageRelease(info, base=None, onProgress=None, session=None):
    """Download, verify and (Windows) extract the release's platform asset.

    Short-circuits when exactly this release is already staged. On any failure or
    cancellation nothing is left staged and partial files are removed.
    @return: the marker dict. Raises StageError / network errors / CancelledError.
    """
    async with _stageLock():
        return await _stageRelease(info, base or stageBase(), onProgress, session)


async def _stageRelease(info, base, onProgress, session):
    asset = selectReleaseAsset(info.get('assets', []))
    if asset is None:
        raise StageError("no install asset for this platform")

    marker = stagedFor(info, base)
    if marker:
        logger.info(f"update {marker['version']} already staged", TAG)
        return marker

    clearStage(base)
    os.makedirs(base, exist_ok=True)

    isWindows = sys.platform == 'win32'
    assetPath = os.path.join(base, ARCHIVE_NAME if isWindows else DMG_NAME)
    package = os.path.join(base, PACKAGE_NAME)

    try:
        await downloadAsset(asset['browser_download_url'], assetPath,
                            onProgress, session, downloadProxy())

        if onProgress:
            onProgress('verify', 0, 0)
        ok, detail = await _blocking(verifyAssetDigest, assetPath, asset)
        if not ok:
            raise StageError(f"verification failed: {detail}")

        if isWindows:
            if onProgress:
                onProgress('extract', 0, 0)
            await _blocking(_extract, assetPath, package + '.part')
            for required in ('Seraphine.exe', 'filelist.txt'):
                if not os.path.isfile(os.path.join(package + '.part', required)):
                    raise StageError(f"package has no {required}")
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
        writeMarker(base, marker)
        logger.info(f"update {marker['version']} staged at {path}", TAG)
        return marker
    except BaseException:
        clearStage(base)
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
    global _applying
    if _applying:
        logger.warning("an update is already being applied", TAG)
        return False
    _applying = True
    try:
        return await _applyStagedUpdate(marker, base or stageBase(), startTimeout,
                                        allowDowngrade)
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
    elif sys.platform == 'darwin':
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


class BackgroundUpdater:
    """Stages updates in the background, never during a game.

    Lives on the UI thread (qasync loop). The game state is tri-state: unknown
    (``gaming is None``, at start-up / while a client connects), gaming, or not.
    Nothing is downloaded and no "ready" is reported unless it is known that no
    game is in progress. ``onReady(marker)`` reports a staged update; one found
    while gaming/unknown is held. ``onStarting(info)`` is called right before a
    download starts. Failures are logged and dropped; the next update check
    offers the release again.
    """

    def __init__(self, onReady, base=None, stage=None, onStarting=None):
        self.onReady = onReady
        self.onStarting = onStarting
        self.base = base
        self.stage = stage or stageRelease
        self.task = None
        self.pendingInfo = None
        self.heldMarker = None
        self.gaming = None
        self.pausedByGame = False

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
            self.onReady(marker)
        self.__maybeStart()

    def offer(self, info):
        self.pendingInfo = info
        self.__maybeStart()

    def notifyReady(self, marker):
        if self.gaming is False:
            self.onReady(marker)
        else:
            self.heldMarker = marker

    def isBusy(self):
        return self.task is not None and not self.task.done()

    def __maybeStart(self):
        if self.gaming is not False or self.pendingInfo is None or self.isBusy():
            return
        self.pausedByGame = False
        if self.onStarting:
            self.onStarting(self.pendingInfo)
        self.task = asyncio.ensure_future(self.__run(self.pendingInfo))

    async def __run(self, info):
        marker = None
        try:
            marker = await self.stage(info, self.base)
        except asyncio.CancelledError:
            logger.info("update download cancelled", TAG)
            if not self.pausedByGame:
                self.pendingInfo = None  # cancelled for another reason (exit)
        except Exception as e:
            logger.warning(f"background update failed: {e!r}", TAG)
            if self.pendingInfo is info:
                self.pendingInfo = None
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
