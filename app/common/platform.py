"""Cross-platform shim centralizing all win32/winreg coupling.

Stage-0 (macOS startup unblock) contract: every Windows-only native feature is
funneled through a lazy/guarded accessor here so that importing or referencing it
on a non-Windows host never raises ImportError or NameError. The win32api/win32gui
modules are imported ONLY when IS_WINDOWS, lazily, inside each accessor.

The macOS VALUE/behavior of several of these (real client version, lockfile-based
discovery, overlay docking) is refined in later port stages; Stage-0 only guarantees
sane no-ops/None so nothing crashes on a Mac-reachable path.

GOLDEN RULE: this shim ADDS non-Windows branches; the Windows path is preserved
byte-for-byte (it still calls the exact same win32 APIs it always did).
"""

import sys

IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'


def get_system_metrics(index):
    """Windows: win32api.GetSystemMetrics(index). Non-Windows: QScreen fallback.

    index 0 -> screen width, 1 -> screen height (the only two indices used in the
    tree). On Windows this returns physical pixels (GetSystemMetrics semantics);
    on macOS we approximate with the primary screen geometry. Returns 0 if no
    screen is resolvable so callers degrade gracefully rather than crash.
    """
    if IS_WINDOWS:
        import win32api
        return win32api.GetSystemMetrics(index)

    # Non-Windows: approximate via Qt's primary screen.
    try:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return 0
        geo = screen.geometry()
        if index == 0:
            return geo.width()
        if index == 1:
            return geo.height()
        return 0
    except Exception:
        return 0


def find_lol_client_window_rect():
    """Windows: locate the live LoL client window and return (left, top, right,
    bottom) via win32gui. Non-Windows: return None (no portable HWND equivalent).

    Returning None lets the OPGG overlay fall back to its screen-edge placement
    (__moveRightCenter), which is the documented macOS degradation.
    """
    if not IS_WINDOWS:
        return None

    import win32gui
    hwnd = win32gui.FindWindow("RCLIENT", "League of Legends")
    if not hwnd:
        return None
    return win32gui.GetWindowRect(hwnd)


def set_window_no_activate(win_id):
    """Windows: add WS_EX_NOACTIVATE to a top-level window so clicking it never takes
    focus from the foreground (game) window. Qt.WindowDoesNotAcceptFocus already asks Qt
    for this; this re-applies it after Qt recreated the native window. Non-Windows: no-op.
    Never raises.
    """
    if not IS_WINDOWS:
        return

    try:
        import win32con
        import win32gui
        hwnd = int(win_id)
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                               style | win32con.WS_EX_NOACTIVATE)
    except Exception:
        pass


def get_file_version_info(fname, sub_block):
    """Windows: win32api.GetFileVersionInfo(fname, sub_block). Non-Windows: raise.

    There is no portable PE-version reader; on macOS the real client version is
    resolved by other means in a later stage. Raising here (rather than returning a
    sentinel) preserves the existing failure semantics: getFileProperties wraps this
    in a bare ``except: return {}`` so callers' force-update path fires correctly.
    """
    if IS_WINDOWS:
        import win32api
        return win32api.GetFileVersionInfo(fname, sub_block)

    raise OSError("GetFileVersionInfo is only available on Windows")


def config_dir():
    """Per-platform directory for Seraphine's persisted config.

    Windows: ``%APPDATA%\\Seraphine`` (byte-identical to the legacy hard-coded
    path, so existing Windows users' config is found unchanged).
    macOS: ``~/Library/Application Support/Seraphine``.
    Other (e.g. Linux): ``~/.config/Seraphine``.

    Override: when the ``SERAPHINE_CONFIG_DIR`` environment variable is set to a
    non-empty ABSOLUTE path it is used as-is on every platform (no ``Seraphine``
    subfolder is appended). A relative value is ignored (the default applies): in
    a frozen build it would resolve against the working directory, inside the
    install tree the updater merges into. The test suite uses it to keep runs
    away from the real config.

    Uses only ``os``/``sys`` (no Qt, no logger, no config import) so it is safe
    to call at config-module import time without a circular import.
    """
    import os

    override = os.environ.get('SERAPHINE_CONFIG_DIR')
    if override and os.path.isabs(override):
        return override

    if IS_WINDOWS:
        return os.path.join(os.getenv('APPDATA'), 'Seraphine')

    if IS_MACOS:
        return os.path.expanduser('~/Library/Application Support/Seraphine')

    return os.path.expanduser('~/.config/Seraphine')


def install_dir():
    """Install root of a packaged (PyInstaller-frozen) build, or None from source.

    The install root is the directory holding the executable (``Seraphine.exe``
    on Windows, ``Seraphine.app/Contents/MacOS`` on macOS). It is NOT the
    working directory: main.py chdirs to ``_MEIPASS``, which in the PyInstaller 6
    onedir build is ``<install>\\_internal``.
    """
    import os

    if not getattr(sys, 'frozen', False):
        return None

    return os.path.dirname(os.path.abspath(sys.executable))


def is_packaged_windows_build():
    """Windows: frozen build whose install root holds Seraphine.exe."""
    import os

    root = install_dir()
    return bool(root) and os.path.isfile(os.path.join(root, 'Seraphine.exe'))


def hkcu_get_value(sub_key, name):
    """Windows: ``(value, type)`` of a value under HKEY_CURRENT_USER, or None when the
    key or the value does not exist. Other errors (OSError) reach the caller.
    Non-Windows: None.
    """
    if not IS_WINDOWS:
        return None

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as k:
            return winreg.QueryValueEx(k, name)
    except FileNotFoundError:
        return None


def hkcu_set_string(sub_key, name, value):
    """Windows: write a REG_SZ value under HKEY_CURRENT_USER (the key is created when
    missing). Raises OSError on failure, also on a non-Windows host.
    """
    if not IS_WINDOWS:
        raise OSError("the registry is only available on Windows")

    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, sub_key, 0,
                            winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def hkcu_delete_value(sub_key, name):
    """Windows: delete a value under HKEY_CURRENT_USER; a missing key or value is not
    an error. Raises OSError on any other failure. Non-Windows: no-op.
    """
    if not IS_WINDOWS:
        return

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        pass


def mac_launch_agents_dir():
    """macOS: the per-user LaunchAgents directory (launchd reads it at login)."""
    import os

    return os.path.expanduser('~/Library/LaunchAgents')


def get_lol_path_by_registry():
    """Windows: read the Tencent (CN) LoL install path from the registry.
    Non-Windows: return "" (no registry; caller treats empty as "not found").
    """
    if not IS_WINDOWS:
        return ""

    import winreg
    from pathlib import Path
    from app.common.logger import logger

    TAG = "Platform"
    mainKey = winreg.HKEY_CURRENT_USER
    subKey = "SOFTWARE\\Tencent\\LOL"
    valueName = "InstallPath"

    try:
        with winreg.OpenKey(mainKey, subKey) as k:
            installPath, _ = winreg.QueryValueEx(k, valueName)
            path = str(Path(f"{installPath}\\TCLS").absolute()
                       ).replace("\\", "/")
            return f"{path[:1].upper()}{path[1:]}"
    except FileNotFoundError:
        logger.warning("reg path or val does not exist.", TAG)
    except OSError as e:
        logger.warning(f"occurred while reading the registry: {e}", TAG)
    except Exception as e:
        logger.exception("unknown error reading registry", e, TAG)

    return ""


# Default macOS install locations for the League of Legends client bundle.
# The Game binary / lockfile live under <bundle>/Contents/LoL.
_MAC_LOL_APP_CANDIDATES = (
    "/Applications/League of Legends.app",
    "~/Applications/League of Legends.app",
)


def get_lol_path_by_scan():
    """macOS: scan the standard install locations for ``League of Legends.app``.

    Returns the first existing ``.app`` bundle path (forward-slash, no trailing
    slash), or "" if none is found. On non-macOS this returns "" (callers fall
    back to their existing per-platform discovery: the Tencent registry on
    Windows, manual config elsewhere).

    The returned path mirrors what ``getLoLPathByRegistry`` yields on Windows: a
    directory that ``cfg.lolFolder`` can point at. The macOS in-bundle layout is
    ``<bundle>/Contents/LoL/Game`` (see ``mac_game_dir_from_folder``).
    """
    if not IS_MACOS:
        return ""

    import os

    for candidate in _MAC_LOL_APP_CANDIDATES:
        path = os.path.expanduser(candidate)
        if os.path.isdir(path):
            return path

    return ""


def mac_lockfile_candidates(lol_folder=None):
    """macOS: ordered list of candidate lockfile paths to probe (first readable
    wins). Empty list on non-macOS.

    The Riot client writes its LCU lockfile (``name:pid:port:password:protocol``)
    inside the app bundle and, for some installs, under Application Support.
    Because we skipped the on-Mac spike, we probe MULTIPLE locations rather than
    assume one:

    1. a path derived from the configured ``cfg.lolFolder`` (``lol_folder`` arg),
       handling both a passed-in ``.app`` bundle and a ``Contents/LoL`` dir;
    2. the default ``/Applications`` and ``~/Applications`` bundle locations;
    3. ``~/Library/Application Support/Riot Games`` variants.
    """
    if not IS_MACOS:
        return []

    import os

    candidates = []

    def _add(path):
        if path and path not in candidates:
            candidates.append(path)

    # (1) cfg.lolFolder-derived. The configured folder may be the .app bundle,
    # the Contents/LoL dir, or already point at the lockfile's directory.
    if lol_folder:
        folder = os.path.expanduser(str(lol_folder).replace("\\", "/"))
        _add(os.path.join(folder, "lockfile"))
        _add(os.path.join(folder, "Contents", "LoL", "lockfile"))

    # (2) default bundle locations.
    for candidate in _MAC_LOL_APP_CANDIDATES:
        bundle = os.path.expanduser(candidate)
        _add(os.path.join(bundle, "Contents", "LoL", "lockfile"))

    # (3) Application Support variants.
    appsup = os.path.expanduser(
        "~/Library/Application Support/Riot Games")
    _add(os.path.join(appsup, "Metadata", "league_of_legends.live", "lockfile"))
    _add(os.path.join(appsup, "League of Legends", "lockfile"))
    _add(os.path.join(appsup, "lockfile"))

    return candidates


def mac_lol_app_path(lol_folder=None):
    """macOS: resolve the ``League of Legends.app`` bundle to launch via ``open``.

    Resolution order:
      1. the configured ``cfg.lolFolder`` (``lol_folder`` arg) if it points at a
         ``.app`` bundle (or its parent does);
      2. the standard install-location scan (``get_lol_path_by_scan``);
      3. the canonical ``/Applications/League of Legends.app`` default.

    Returns the bundle path, or "" on non-macOS / when nothing resolves. Used by
    the client-launch paths (``__startLolClient`` etc.) so all macOS bundle-layout
    knowledge lives in this shim.
    """
    if not IS_MACOS:
        return ""

    import os

    if lol_folder:
        folder = os.path.expanduser(str(lol_folder).replace("\\", "/"))
        # configured folder is the bundle itself
        if folder.endswith(".app") and os.path.isdir(folder):
            return folder
        # configured folder is inside the bundle (e.g. Contents/LoL) -> climb up
        probe = folder
        for _ in range(4):
            probe = os.path.dirname(probe)
            if not probe or probe == os.path.dirname(probe):
                break
            if probe.endswith(".app") and os.path.isdir(probe):
                return probe

    scanned = get_lol_path_by_scan()
    if scanned:
        return scanned

    default = os.path.expanduser(_MAC_LOL_APP_CANDIDATES[0])
    if os.path.isdir(default):
        return default

    return ""


def mac_game_dir_from_folder(lol_folder):
    """macOS: resolve the in-bundle Game directory from a configured lolFolder.

    Returns ``<bundle>/Contents/LoL/Game`` for a ``.app`` bundle, or the folder
    joined with ``Game`` otherwise. Used by version-read / spectate paths in
    later stages; kept here so all macOS bundle-layout knowledge lives in the
    shim. Empty input yields "".
    """
    if not lol_folder:
        return ""

    import os

    folder = os.path.expanduser(str(lol_folder).replace("\\", "/"))

    if folder.endswith(".app"):
        return os.path.join(folder, "Contents", "LoL", "Game")
    if folder.endswith(os.path.join("Contents", "LoL")):
        return os.path.join(folder, "Game")

    return os.path.join(folder, "Game")
