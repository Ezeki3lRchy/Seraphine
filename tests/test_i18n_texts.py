"""Display texts that go through the Qt translator: lookups happen at call time and every
(context, source) pair is a literal pyside6-lupdate can extract."""
import pathlib
import re
import time
import unittest

from PySide6.QtCore import QTranslator

from app.components import career_widgets as cw
from app.components import match_widgets as mw
from app.components import scoreboard as sb
from app.components import play_style_widget as psw

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


class FakeTranslator(QTranslator):
    """Translates a fixed table, like a loaded .qm would"""

    def __init__(self, table):
        super().__init__()
        self.table = table

    def translate(self, context, source, disambiguation=None, n=-1):
        return self.table.get((context, source), "")

    def isEmpty(self):
        return False


class EnglishTextTest(unittest.TestCase):
    """no translator installed: the English output is the source text"""

    def test_tier_text(self):
        self.assertEqual(mw.tierText("DIAMOND", "II"), "Diamond II")
        self.assertEqual(mw.tierText("GRANDMASTER", ""), "Grandmaster")
        self.assertEqual(mw.tierText("NONE", "I"), "")
        # a tier the table does not know still shows, title-cased
        self.assertEqual(mw.tierText("MYTHIC", "I"), "Mythic I")

    def test_time_ago_singular_and_plural(self):
        now = time.time() * 1000
        self.assertEqual(cw.timeAgo(now - 60_000 - 1000), "1 minute ago")
        self.assertEqual(cw.timeAgo(now - 2 * 3_600_000 - 1000), "2 hours ago")
        self.assertEqual(cw.timeAgo(now - 86_400_000 - 1000), "1 day ago")

    def test_play_time(self):
        self.assertEqual(psw.playTimeText(20 * 3600), "20h 00m")
        self.assertEqual(psw.playTimeText(3600 + 5 * 60), "1h 05m")
        self.assertEqual(psw.playTimeText(45 * 60), "45m")

    def test_duration_is_shared(self):
        self.assertIs(cw.durationText, sb.durationText)


class CallTimeLookupTest(unittest.TestCase):
    """a translator installed after import is used (nothing is looked up at import time)"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.translator = FakeTranslator({
            ("RankTier", "Diamond"): "DIA",
            ("TimeText", "%1 minutes ago"): "%1 min. ago",
            ("TimeText", "%1m %2s"): "%1:%2",
            ("TimeText", "now"): "NOW",
            ("GameTags", "Triple kill"): "TRIPLE",
            ("GameTags", "%1th"): "#%1",
        })
        self.app.installTranslator(self.translator)

    def tearDown(self):
        self.app.removeTranslator(self.translator)

    def test_lookups(self):
        now = time.time()
        self.assertEqual(mw.tierText("DIAMOND", "II"), "DIA II")
        self.assertEqual(cw.timeAgo(now * 1000 - 3 * 60_000 - 1000), "3 min. ago")
        self.assertEqual(sb.durationText(32 * 60 + 9), "32:09")
        self.assertEqual(sb.compactAgo((now - 20) * 1000, "", now), "NOW")
        tags = mw.gameTags({"remake": False, "multiKill": 3, "badge": None, "scoreRank": 7})
        self.assertEqual([t for t, _ in tags], ["TRIPLE", "#7"])


class ExtractableLiteralTest(unittest.TestCase):
    """trText (opgg build widgets) takes its pair as arguments, which lupdate cannot read;
    every pair it is called with must also be written out as a literal"""

    def sources(self):
        return "\n".join(p.read_text(encoding="utf-8") for p in APP.rglob("*.py"))

    def test_tr_text_pairs_are_literals(self):
        text = self.sources()
        pairs = set(re.findall(r'trText\(\s*"([^"]+)",\s*"([^"]+)"', text))
        self.assertIn(("ChampionTitleBar", "Win Rate"), pairs)
        for context, source in pairs:
            self.assertRegex(
                text, r'(QT_TRANSLATE_NOOP|QCoreApplication\.translate)\(\s*"%s",\s*"%s"\)'
                % (re.escape(context), re.escape(source)), (context, source))

    def test_position_names_are_literals(self):
        from app.view.opgg_build_interface import POSITION_NAMES
        text = self.sources()
        for source in POSITION_NAMES.values():
            self.assertRegex(text, r'QT_TRANSLATE_NOOP\("OpggWindow", "%s"\)' % re.escape(source))


if __name__ == "__main__":
    unittest.main()
