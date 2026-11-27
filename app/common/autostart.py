"""Launch Seraphine when the user logs in ("Launch at startup" on the Settings page).

The system entry is the only state: nothing is stored in the app config, so the
switch always shows what is really registered.

Windows: the per-user value ``Seraphine`` under
``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` holding the quoted path of
the installed ``Seraphine.exe`` (no admin rights). Task Manager's "Startup" page
switches an entry off by writing an odd first byte to the value of the same name under
``...\\Explorer\\StartupApproved\\Run``; such an entry counts as off here, and switching
it on in the app removes that value.

macOS: the per-user LaunchAgent ``~/Library/LaunchAgents/<LABEL>.plist`` that runs
``/usr/bin/open <Seraphine.app>`` at login (works for an unsigned app; the login-items
API needs a signed bundle). The file is only written or removed: launchd reads it at
the next login, so switching it on never starts a second instance now.

Only a packaged build registers itself (``backend()`` is None from source, on other
platforms, and for a macOS app running from an App Translocation path, which changes
on every launch). The registry and the LaunchAgents directory are passed in, so the
tests use a fake registry and a temp directory.

No Qt import here.
"""
import ntpath
import os
import plistlib
import posixpath
import sys

from app.common import platform

TAG = "Autostart"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = "Seraphine"

# the bundle identifier of Seraphine.spec
LABEL = "com.ezekielrchy.seraphine"
OPEN = "/usr/bin/open"


class _HkcuRegistry:
    """The real registry (HKEY_CURRENT_USER), through the platform shim"""

    get = staticmethod(platform.hkcu_get_value)
    setString = staticmethod(platform.hkcu_set_string)
    delete = staticmethod(platform.hkcu_delete_value)


def commandPath(command):
    """The program of a Run-key command line: the quoted part, or the text up to the
    first argument of an unquoted one. '' when there is none."""
    command = (command or '').strip()
    if command.startswith('"'):
        end = command.find('"', 1)
        return command[1:end] if end > 0 else command[1:]

    lower = command.lower()
    end = lower.find('.exe')
    return command[:end + 4] if end >= 0 else command


class WindowsAutostart:
    def __init__(self, exePath, registry=None, exists=os.path.exists):
        self.exePath = exePath
        self.registry = registry or _HkcuRegistry
        self.exists = exists

    @staticmethod
    def _same(a, b):
        return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))

    def registeredPath(self):
        """The program the Run entry starts, or None without an entry"""
        got = self.registry.get(RUN_KEY, VALUE_NAME)
        if got is None or not isinstance(got[0], str):
            return None

        return commandPath(got[0]) or None

    def _switchedOff(self):
        # Task Manager: first byte odd (03 / 07) = disabled, even (02 / 06) = enabled
        got = self.registry.get(APPROVED_KEY, VALUE_NAME)
        if got is None or not isinstance(got[0], (bytes, bytearray)) or not got[0]:
            return False

        return bool(got[0][0] & 1)

    def isEnabled(self):
        path = self.registeredPath()
        return bool(path) and self._same(path, self.exePath) and not self._switchedOff()

    def enable(self):
        self.registry.setString(RUN_KEY, VALUE_NAME, f'"{self.exePath}"')
        self.registry.delete(APPROVED_KEY, VALUE_NAME)

    def disable(self):
        self.registry.delete(RUN_KEY, VALUE_NAME)
        self.registry.delete(APPROVED_KEY, VALUE_NAME)

    def repair(self):
        """The install was moved: an entry whose program no longer exists is pointed
        at this install. An entry of another install that still exists, and Task
        Manager's on/off choice, are left alone. True when the entry was rewritten."""
        path = self.registeredPath()
        if not path or self._same(path, self.exePath) or self.exists(path):
            return False

        self.registry.setString(RUN_KEY, VALUE_NAME, f'"{self.exePath}"')
        return True


class MacAutostart:
    def __init__(self, appPath, agentsDir=None, exists=os.path.exists):
        self.appPath = appPath
        self.agentsDir = agentsDir or platform.mac_launch_agents_dir()
        self.exists = exists

    @property
    def plistPath(self):
        return os.path.join(self.agentsDir, LABEL + '.plist')

    @staticmethod
    def _same(a, b):
        return posixpath.normpath(a) == posixpath.normpath(b)

    def _read(self):
        """The agent's plist as a dict; None when there is no file or it is not a
        usable plist (enable() then replaces it)"""
        try:
            with open(self.plistPath, 'rb') as f:
                data = plistlib.load(f)
        except FileNotFoundError:
            return None
        except Exception:  # ExpatError / InvalidFileException / ValueError: not a plist
            return None

        return data if isinstance(data, dict) else None

    def registeredPath(self):
        data = self._read()
        args = (data or {}).get('ProgramArguments')
        if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
            return None

        return args[-1] or None

    def isEnabled(self):
        data = self._read()
        path = self.registeredPath()
        return bool(path) and self._same(path, self.appPath) \
            and data.get('RunAtLoad') is True and data.get('Disabled') is not True

    def _write(self):
        os.makedirs(self.agentsDir, exist_ok=True)
        tmp = self.plistPath + '.tmp'
        with open(tmp, 'wb') as f:
            plistlib.dump({
                'Label': LABEL,
                'ProgramArguments': [OPEN, self.appPath],
                'RunAtLoad': True,
                'ProcessType': 'Interactive',
            }, f)
        os.replace(tmp, self.plistPath)

    def enable(self):
        self._write()

    def disable(self):
        try:
            os.remove(self.plistPath)
        except FileNotFoundError:
            pass

    def repair(self):
        """Same rule as on Windows: only an entry whose app no longer exists"""
        path = self.registeredPath()
        if not path or self._same(path, self.appPath) or self.exists(path):
            return False

        self._write()
        return True


def macAppPath(executable):
    """``/x/Seraphine.app`` for ``/x/Seraphine.app/Contents/MacOS/Seraphine``; None for
    anything else, and for an App Translocation path (a quarantined app runs from a
    random read-only path that is gone at the next launch)."""
    marker = '.app/Contents/MacOS/'
    at = executable.find(marker)
    if at < 0 or '/AppTranslocation/' in executable:
        return None

    return executable[:at + len('.app')]


def backend():
    """The autostart entry of this packaged build, or None when it cannot register
    itself (running from source, unsupported platform, translocated macOS app)."""
    root = platform.install_dir()
    if not root:
        return None

    if platform.IS_WINDOWS:
        exe = os.path.join(root, 'Seraphine.exe')
        return WindowsAutostart(exe) if os.path.isfile(exe) else None

    if platform.IS_MACOS:
        app = macAppPath(os.path.abspath(sys.executable))
        return MacAutostart(app) if app else None

    return None


def _logFailure(what, e):
    # type name only: an OSError's text carries the user's paths
    from app.common.logger import logger
    logger.error(f"{what} failed: {type(e).__name__}", TAG)


def isAvailable():
    return backend() is not None


def isEnabled():
    """What the system has registered for this install; False when it cannot be read"""
    entry = backend()
    if entry is None:
        return False

    try:
        return entry.isEnabled()
    except OSError as e:
        _logFailure("reading the startup entry", e)
        return False


def setEnabled(enabled):
    """Register or remove the entry. True when the system now has what was asked for."""
    entry = backend()
    if entry is None:
        return False

    try:
        if enabled:
            entry.enable()
        else:
            entry.disable()
        return entry.isEnabled() == bool(enabled)
    except OSError as e:
        _logFailure("enabling the startup entry" if enabled else "removing the startup entry", e)
        return False


def repair():
    """Start-up: keep an existing entry pointing at this install after it was moved"""
    entry = backend()
    if entry is None:
        return False

    try:
        fixed = entry.repair()
    except OSError as e:
        _logFailure("repairing the startup entry", e)
        return False

    if fixed:
        from app.common.logger import logger
        logger.info("startup entry pointed at the moved install", TAG)
    return fixed
