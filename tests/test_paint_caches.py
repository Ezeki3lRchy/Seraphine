"""Paint-path caches of the self-drawn career widgets.

- CareerGameRow takes the deaths colour from design_tokens' cache, not from the colour
  manager on every paint.
- Cached pixmaps are keyed on a data version bumped by every setter, not on id() of the
  data (CPython reuses ids of freed objects, so a stale pixmap could match).
"""
import os
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.common.style_sheet import colorManager
from app.components import design_tokens


class _AlwaysEqual:
    def __eq__(self, other):
        return True


class CareerRowDeathsColourTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cache_hit_does_not_ask_the_colour_manager(self):
        from app.components.career_widgets import CareerGameRow
        content = CareerGameRow._CareerGameRow__content
        row = types.SimpleNamespace(width=lambda: 800, height=lambda: 104,
                                    devicePixelRatioF=lambda: 1.0, resultType='win',
                                    _cacheKey=_AlwaysEqual(), _cache="cached")
        design_tokens._invalidate()
        real = colorManager.getColor
        asked = []

        def getColor(type):
            asked.append(type)
            return real(type)

        with mock.patch.object(colorManager, "getColor", getColor):
            for _ in range(3):
                self.assertEqual(content(row), "cached")
        self.assertLessEqual(asked.count('deaths'), 1)


class ChampionStatsDataVersionTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_profile_with_a_reused_id_is_not_served_from_cache(self):
        from app.lol import champion_stats
        from app.components.champion_stats_widget import ChampionStatsView
        from tests.test_champion_stats import g

        v = ChampionStatsView()
        v.resize(1000, 100)
        v.setSample([g(champ) for champ in (1, 1, 2, 3)], 'match')
        self.assertEqual([r[2] for r in v.rows()].count('champ'), 3)
        contentKey = v.contentKey()
        old = v.profile()
        real = champion_stats.buildAll

        def buildAll(games):
            # stands in for CPython handing the freed profile's id to the new one
            out = real(games)
            old.clear()
            old.update(out[None])
            out[None] = old
            return out

        with mock.patch.object(champion_stats, "buildAll", buildAll):
            v.setSample([g(1), g(1)], 'match')
        self.assertIs(v.profile(), old)
        self.assertEqual([r[2] for r in v.rows()].count('champ'), 1)
        self.assertNotEqual(v.contentKey(), contentKey)

    def test_every_data_setter_changes_the_keys(self):
        from app.components.champion_stats_widget import ChampionStatsView
        from tests.test_champion_stats import g

        v = ChampionStatsView()
        v.resize(1000, 100)
        keys = []
        for setter in (v.setLoading, v.setEmpty, lambda: v.setSample([g(1)], 'match'),
                       lambda: v.setSample([g(1)], 'match'), v.setEmpty):
            setter()
            v.rows()
            keys.append((v.contentKey(), v._rowsKey))
        self.assertEqual(len(set(keys)), len(keys))


class FontCacheTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fonts_are_built_once_per_arguments(self):
        from PySide6.QtGui import QFont
        from app.components.design_tokens import textFont, numberFont

        self.assertIs(textFont(12), textFont(12))
        self.assertIs(numberFont(14, 500), numberFont(14, 500))
        self.assertIsNot(textFont(12), textFont(12, True))
        self.assertTrue(textFont(12, True).bold())
        self.assertEqual(textFont(13).pixelSize(), 13)
        self.assertEqual(numberFont(11, 600).weight(), QFont.Weight(600))
        self.assertEqual(numberFont(11).weight(), QFont.DemiBold)

    def test_nothing_is_built_at_import(self):
        import subprocess
        import sys
        code = ("import app.components.design_tokens as t\n"
                "from PySide6.QtWidgets import QApplication\n"
                "assert QApplication.instance() is None\n"
                "assert t.textFont.cache_info().currsize == 0\n"
                "assert t.numberFont.cache_info().currsize == 0\n")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True,
                             text=True, timeout=120,
                             env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
