"""Launch at startup (app.common.autostart): the Windows Run entry on a fake registry,
the macOS LaunchAgent in a temp directory, and the Settings card on a fake backend.
Nothing here touches the real registry or ~/Library/LaunchAgents: every test replaces
the registry / directory, and SystemUntouchedTest fails if a default leaks in."""
import logging
import os
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.common import autostart, platform
from app.common.autostart import (APPROVED_KEY, LABEL, RUN_KEY, VALUE_NAME, MacAutostart,
                                  WindowsAutostart, commandPath, macAppPath)
from tests import LOG_RECORDS

EXE = r"C:\Apps\Seraphine\Seraphine.exe"
APP = "/Applications/Seraphine.app"

REG_SZ, REG_BINARY = 1, 3


class FakeRegistry:
    def __init__(self):
        self.values = {}
        self.failWrites = False

    def get(self, key, name):
        return self.values.get((key, name))

    def setString(self, key, name, value):
        if self.failWrites:
            raise PermissionError(13, r"denied C:\Users\someone")
        self.values[(key, name)] = (value, REG_SZ)

    def delete(self, key, name):
        if self.failWrites:
            raise PermissionError(13, r"denied C:\Users\someone")
        self.values.pop((key, name), None)


class CommandPathTest(unittest.TestCase):

    def test_forms(self):
        self.assertEqual(commandPath(f'"{EXE}"'), EXE)
        self.assertEqual(commandPath(f'"{EXE}" --minimized'), EXE)
        self.assertEqual(commandPath(r'C:\Program Files\S\Seraphine.exe --x'),
                         r'C:\Program Files\S\Seraphine.exe')
        self.assertEqual(commandPath(EXE), EXE)
        self.assertEqual(commandPath(''), '')
        self.assertEqual(commandPath(None), '')


class WindowsAutostartTest(unittest.TestCase):

    def setUp(self):
        self.reg = FakeRegistry()
        self.present = set()
        self.entry = WindowsAutostart(EXE, self.reg, exists=lambda p: p in self.present)

    def test_off_by_default(self):
        self.assertFalse(self.entry.isEnabled())
        self.assertIsNone(self.entry.registeredPath())

    def test_enable_writes_quoted_path_then_disable_removes_it(self):
        self.entry.enable()
        self.assertEqual(self.reg.values, {(RUN_KEY, VALUE_NAME): (f'"{EXE}"', REG_SZ)})
        self.assertTrue(self.entry.isEnabled())

        self.entry.disable()
        self.assertEqual(self.reg.values, {})
        self.assertFalse(self.entry.isEnabled())

    def test_disable_without_entry_is_fine(self):
        self.entry.disable()
        self.assertEqual(self.reg.values, {})

    def test_path_compare_ignores_case_and_slashes(self):
        self.reg.values[(RUN_KEY, VALUE_NAME)] = ('"c:/apps/SERAPHINE/seraphine.exe"', REG_SZ)
        self.assertTrue(self.entry.isEnabled())

    def test_other_install_is_not_this_one(self):
        other = r"D:\Old\Seraphine.exe"
        self.reg.values[(RUN_KEY, VALUE_NAME)] = (f'"{other}"', REG_SZ)
        self.assertFalse(self.entry.isEnabled())
        self.assertEqual(self.entry.registeredPath(), other)

    def test_task_manager_switch(self):
        self.entry.enable()
        for first, off in ((0x03, True), (0x07, True), (0x02, False), (0x06, False)):
            self.reg.values[(APPROVED_KEY, VALUE_NAME)] = (bytes([first]) + bytes(11), REG_BINARY)
            self.assertEqual(self.entry.isEnabled(), not off, hex(first))

        # switching it on in the app clears Task Manager's "disabled"
        self.reg.values[(APPROVED_KEY, VALUE_NAME)] = (bytes([3]) + bytes(11), REG_BINARY)
        self.entry.enable()
        self.assertNotIn((APPROVED_KEY, VALUE_NAME), self.reg.values)
        self.assertTrue(self.entry.isEnabled())

    def test_non_string_value_is_no_entry(self):
        self.reg.values[(RUN_KEY, VALUE_NAME)] = (b'\x00', REG_BINARY)
        self.assertFalse(self.entry.isEnabled())

    def test_repair_follows_a_moved_install(self):
        old = r"D:\Old\Seraphine.exe"
        self.reg.values[(RUN_KEY, VALUE_NAME)] = (f'"{old}"', REG_SZ)
        self.assertTrue(self.entry.repair())  # D:\Old is gone
        self.assertEqual(self.reg.values[(RUN_KEY, VALUE_NAME)][0], f'"{EXE}"')
        self.assertFalse(self.entry.repair())  # nothing left to do

    def test_repair_leaves_another_existing_install(self):
        old = r"D:\Old\Seraphine.exe"
        self.present.add(old)
        self.reg.values[(RUN_KEY, VALUE_NAME)] = (f'"{old}"', REG_SZ)
        self.assertFalse(self.entry.repair())
        self.assertEqual(self.reg.values[(RUN_KEY, VALUE_NAME)][0], f'"{old}"')

    def test_repair_never_creates_an_entry(self):
        self.assertFalse(self.entry.repair())
        self.assertEqual(self.reg.values, {})

    def test_repair_keeps_task_manager_choice(self):
        self.reg.values[(RUN_KEY, VALUE_NAME)] = (r'"D:\Old\Seraphine.exe"', REG_SZ)
        self.reg.values[(APPROVED_KEY, VALUE_NAME)] = (bytes([3]) + bytes(11), REG_BINARY)
        self.assertTrue(self.entry.repair())
        self.assertFalse(self.entry.isEnabled())


class MacAutostartTest(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # the directory does not exist yet on a fresh account
        self.dir = os.path.join(tmp.name, "LaunchAgents")
        self.present = set()
        self.entry = MacAutostart(APP, self.dir, exists=lambda p: p in self.present)

    def plist(self):
        with open(self.entry.plistPath, 'rb') as f:
            return plistlib.load(f)

    def test_off_by_default(self):
        self.assertFalse(self.entry.isEnabled())
        self.assertFalse(os.path.exists(self.dir))

    def test_enable_writes_agent_then_disable_removes_it(self):
        self.entry.enable()
        self.assertEqual(os.listdir(self.dir), [LABEL + ".plist"])
        data = self.plist()
        self.assertEqual(data["Label"], LABEL)
        self.assertEqual(data["ProgramArguments"], ["/usr/bin/open", APP])
        self.assertIs(data["RunAtLoad"], True)
        self.assertTrue(self.entry.isEnabled())

        self.entry.disable()
        self.assertEqual(os.listdir(self.dir), [])
        self.assertFalse(self.entry.isEnabled())
        self.entry.disable()  # again: no error

    def test_other_app_or_disabled_is_off(self):
        self.entry.enable()
        data = self.plist()
        for change in ({"ProgramArguments": ["/usr/bin/open", "/Old/Seraphine.app"]},
                       {"Disabled": True}, {"RunAtLoad": False}, {"ProgramArguments": []},
                       {"ProgramArguments": "x"}):
            with open(self.entry.plistPath, 'wb') as f:
                plistlib.dump({**data, **change}, f)
            self.assertFalse(self.entry.isEnabled(), change)

    def test_broken_plist_is_off_and_enable_replaces_it(self):
        os.makedirs(self.dir)
        with open(self.entry.plistPath, 'wb') as f:
            f.write(b"not a plist")
        self.assertFalse(self.entry.isEnabled())
        self.entry.enable()
        self.assertTrue(self.entry.isEnabled())

    def test_repair(self):
        self.assertFalse(self.entry.repair())  # never creates one
        self.assertFalse(os.path.exists(self.dir))

        MacAutostart("/Old/Seraphine.app", self.dir).enable()
        self.present.add("/Old/Seraphine.app")
        self.assertFalse(self.entry.repair())  # the other copy still exists
        self.present.clear()
        self.assertTrue(self.entry.repair())
        self.assertTrue(self.entry.isEnabled())

    def test_app_path(self):
        self.assertEqual(macAppPath("/Applications/Seraphine.app/Contents/MacOS/Seraphine"), APP)
        self.assertIsNone(macAppPath("/usr/local/bin/python3"))
        self.assertIsNone(macAppPath(
            "/private/var/folders/ab/T/AppTranslocation/1-2/d/Seraphine.app/Contents/MacOS/Seraphine"))


class ModuleFunctionsTest(unittest.TestCase):

    def test_from_source_nothing_is_available(self):
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertIsNone(autostart.backend())
        self.assertFalse(autostart.isAvailable())
        self.assertFalse(autostart.isEnabled())
        self.assertFalse(autostart.setEnabled(True))
        self.assertFalse(autostart.repair())

    def test_set_enabled(self):
        entry = WindowsAutostart(EXE, FakeRegistry(), exists=lambda p: False)
        with mock.patch.object(autostart, "backend", return_value=entry):
            self.assertTrue(autostart.isAvailable())
            self.assertTrue(autostart.setEnabled(True))
            self.assertTrue(autostart.isEnabled())
            self.assertTrue(autostart.setEnabled(False))
            self.assertFalse(autostart.isEnabled())

    def test_failure_is_reported_and_logged_by_type_name_only(self):
        reg = FakeRegistry()
        reg.failWrites = True
        entry = WindowsAutostart(EXE, reg, exists=lambda p: False)
        seraphine = logging.getLogger("Seraphine")
        self.addCleanup(seraphine.setLevel, seraphine.level)
        seraphine.setLevel(logging.DEBUG)
        del LOG_RECORDS[:]
        with mock.patch.object(autostart, "backend", return_value=entry):
            self.assertFalse(autostart.setEnabled(True))
        lines = [r for r in LOG_RECORDS if "[Autostart]" in r]
        self.assertEqual(len(lines), 1, LOG_RECORDS)
        self.assertIn("PermissionError", lines[0])
        self.assertNotIn("someone", lines[0])


class SystemUntouchedTest(unittest.TestCase):
    """the defaults that reach the real system are only used by backend(), which is
    None in a test run"""

    def test_real_accessors_are_not_called(self):
        names = ("hkcu_get_value", "hkcu_set_string", "hkcu_delete_value", "mac_launch_agents_dir")
        with mock.patch.multiple(platform, **{n: mock.DEFAULT for n in names}) as mocks:
            autostart.isEnabled()
            autostart.setEnabled(True)
            autostart.setEnabled(False)
            autostart.repair()
        for name, m in mocks.items():
            m.assert_not_called()

    def test_non_windows_shim(self):
        with mock.patch.object(platform, "IS_WINDOWS", False):
            self.assertIsNone(platform.hkcu_get_value(RUN_KEY, VALUE_NAME))
            platform.hkcu_delete_value(RUN_KEY, VALUE_NAME)
            with self.assertRaises(OSError):
                platform.hkcu_set_string(RUN_KEY, VALUE_NAME, "x")


class SettingCardTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def page(self):
        from app.view.setting_interface import SettingInterface
        page = SettingInterface()
        self.addCleanup(page.deleteLater)
        return page

    def test_from_source_the_switch_is_off_and_disabled(self):
        page = self.page()
        self.assertFalse(page.autostartCard.switchButton.isEnabled())
        self.assertFalse(page.autostartCard.isChecked())
        self.assertIn("Not available", page.autostartCard.contentLabel.text())

    def test_switch_shows_and_changes_the_system_entry(self):
        reg = FakeRegistry()
        entry = WindowsAutostart(EXE, reg, exists=lambda p: False)
        entry.enable()
        with mock.patch.object(autostart, "backend", return_value=entry):
            page = self.page()
            card = page.autostartCard
            self.assertTrue(card.switchButton.isEnabled())
            self.assertTrue(card.isChecked())  # what the system has, not a config value

            card.switchButton.setChecked(False)
            self.assertNotIn((RUN_KEY, VALUE_NAME), reg.values)
            card.switchButton.setChecked(True)
            self.assertIn((RUN_KEY, VALUE_NAME), reg.values)

            # changed outside the app: picked up when the page is shown again
            entry.disable()
            page.show()
            self.app.processEvents()
            self.assertFalse(card.isChecked())
            page.close()

    def test_failed_change_puts_the_switch_back(self):
        reg = FakeRegistry()
        reg.failWrites = True
        entry = WindowsAutostart(EXE, reg, exists=lambda p: False)
        with mock.patch.object(autostart, "backend", return_value=entry):
            page = self.page()
            page.autostartCard.switchButton.setChecked(True)
            self.assertFalse(page.autostartCard.isChecked())
            self.assertEqual(reg.values, {})


if __name__ == "__main__":
    unittest.main()
