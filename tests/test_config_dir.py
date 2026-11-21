"""config_dir(): the SERAPHINE_CONFIG_DIR override, the per-platform default, and
the guard that the test run really is isolated from the real config."""
import os
import unittest
from unittest import mock

from app.common import platform


class ConfigDirTest(unittest.TestCase):

    def test_absolute_override_is_used_as_is(self):
        path = os.path.join(os.path.abspath(os.sep), 'x', 'cfg')
        with mock.patch.dict(os.environ, {'SERAPHINE_CONFIG_DIR': path}):
            self.assertEqual(platform.config_dir(), path)

    def test_empty_or_relative_override_is_ignored(self):
        appdata = os.path.join(os.path.abspath(os.sep), 'appdata')
        for value in ('', os.path.join('x', 'cfg'), '.'):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {'SERAPHINE_CONFIG_DIR': value, 'APPDATA': appdata}):
                    with mock.patch.object(platform, 'IS_WINDOWS', True):
                        self.assertEqual(platform.config_dir(), os.path.join(appdata, 'Seraphine'))


class TestRunIsolationTest(unittest.TestCase):
    """The app must have been imported AFTER tests/__init__.py set
    SERAPHINE_CONFIG_DIR. That only happens when the modules are imported as
    ``tests.test_x`` (discovery with ``-t .``); with plain ``discover -s tests``
    they are ``test_x``, the package is skipped, and the app would read (and
    write) the real config."""

    HINT = ('the app was imported before tests/__init__.py redirected the config '
            'dir, so this run would use the real config: run the suite with '
            '`python -m unittest discover -s tests -t .`')

    def test_import_time_config_paths_lie_under_the_test_config_dir(self):
        override = os.environ.get('SERAPHINE_CONFIG_DIR', '')
        self.assertTrue(os.path.isabs(override), self.HINT)
        root = os.path.normcase(os.path.abspath(override))

        from app.common import config
        from app.lol import aram, champions, rank_history
        derived = {
            'config.LOCAL_PATH': config.LOCAL_PATH,
            'rank_history.storePath': rank_history.storePath,
            'AramBuff.ARAM_CFG_PATH': aram.AramBuff.ARAM_CFG_PATH,
            'ChampionAlias.CHAMPION_CFG_PATH': champions.ChampionAlias.CHAMPION_CFG_PATH,
        }
        for name, path in derived.items():
            with self.subTest(path=name):
                p = os.path.normcase(os.path.abspath(str(path)))
                self.assertTrue(p == root or p.startswith(root + os.sep),
                                '%s = %s is outside %s: %s' % (name, path, override, self.HINT))


if __name__ == '__main__':
    unittest.main()
