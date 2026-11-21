"""Item value overlay lane rows (app/components/gold_overlay.py) and the in-app panel's
difference texts: card size, column fit at real font metrics (English first, Chinese too),
the full-or-compact number rule, no "Even" anywhere, and a lane with a missing side.
Offscreen only: nothing is shown, the widgets are painted into images."""
import os
import unittest

from PySide6.QtCore import QCoreApplication, QTranslator
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.common.signals import signalBus
from app.components import gold_overlay as G
from app.components.design_tokens import resultColor
from app.components.item_gold_panel import CONTENT_H, ItemGoldPanel, diffColor, diffText
from app.lol import live_client as L

I18N = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "app", "resource", "i18n")
FONT_DIR = r"C:\Windows\Fonts"
FONT_FILES = ("bahnschrift.ttf", "segoeui.ttf", "segoeuib.ttf", "msyh.ttc", "msyhbd.ttc")

# fonts the column widths were measured with; elsewhere (macOS: DIN Alternate / PingFang SC)
# the strict fit check is skipped, the full-or-compact rule is still checked
MEASURED_NUMBER = {"Bahnschrift", "Segoe UI"}
MEASURED_TEXT = {"Segoe UI"}

_fontIds = []


def setUpModule():
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "offscreen":
        raise unittest.SkipTest("overlay checks run with QT_QPA_PLATFORM=offscreen only")
    # the offscreen platform has no fonts of its own: load the ones the paint code asks for.
    # Registration is process-wide, so tearDownModule removes them again
    for name in FONT_FILES:
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path):
            fontId = QFontDatabase.addApplicationFont(path)
            if fontId >= 0:
                _fontIds.append(fontId)


def tearDownModule():
    while _fontIds:
        QFontDatabase.removeApplicationFont(_fontIds.pop())


def player(team, position, value, name):
    return {"team": team, "position": position, "riotId": name, "summonerName": name,
            "championName": "Annie", "items": [{"itemID": 1, "price": value}] if value else []}


def game(pairs, local=True):
    """allgamedata with one lane per (ally value, enemy value) pair, in LANES order; a None
    value leaves that side's player out (uneven teams)"""
    players = []
    for i, (a, e) in enumerate(pairs):
        if a is not None:
            players.append(player(L.ORDER, L.LANES[i], a, f"Ally{i}"))
        if e is not None:
            players.append(player(L.CHAOS, L.LANES[i], e, f"Enemy{i}"))
    data = {"allPlayers": players, "gameData": {"gameMode": "CLASSIC", "gameTime": 754.0}}
    if local:
        data["activePlayer"] = {"riotId": "Ally0", "currentGold": 300}
    return data


def summaryOf(pairs, local=True):
    return L.summarizeGameData(game(pairs, local))


# the inputs the lane rows must fit
INPUTS = {
    "mockup": [(3200, 2750), (2100, 2400), (2900, 2900), (500, 725), (1400, 1300)],
    "five_digits": [(12345, 9876)] * 5,
    "single_lane": [(500, 725)],
    "tiny_difference": [(1400, 1300)],
    "zero_difference": [(2900, 2900)],
    "big_ahead": [(28888, 500)],
    "big_behind": [(500, 28888)],
    "four_digit_minus": [(1000, 9888), (1111, 9999)],
    "missing_sides": [(3200, 2750), (2100, None), (None, 2900)],
}
# totals and headline differences the top rows must fit
TOTALS = (500, 10100, 99999, 123456)
HEADLINES = (0, 25, -2469, -12345, -123456)


class RecordingTranslator(QTranslator):
    """Records every (context, source) looked up; translates nothing"""

    def __init__(self):
        super().__init__()
        self.sources = []

    def translate(self, context, source, disambiguation=None, n=-1):
        self.sources.append((context, source))
        return ""

    def isEmpty(self):
        return False


def resolvedFamilies():
    """(measured, description): measured when the number and text fonts resolve to the
    families the widths were measured with. Exact strings and pixel fits are asserted only
    then; elsewhere (macOS: DIN Alternate / PingFang SC) only the full-or-compact rule is"""
    number = QFontInfo(G.laneFonts()["diff"]).family()
    text = QFontInfo(G.laneFonts()["label"]).family()
    measured = number in MEASURED_NUMBER and text in MEASURED_TEXT
    return measured, f"number font {number!r}, text font {text!r}"


class GoldOverlayLaneTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        cls.overlay = G.GoldOverlay()

    @classmethod
    def tearDownClass(cls):
        # The overlay stays alive for the rest of the run (the class keeps it): its three
        # cfg lambdas cannot be disconnected one by one, and a later test that changes those
        # settings must not reach a deleted C++ object. Offscreen it can never be shown
        signalBus.liveGameDataChanged.disconnect(cls.overlay.setSummary)
        cls.overlay.setSummary(None)

    def summary(self, pairs, local=True):
        s = summaryOf(pairs, local)
        self.assertIsNotNone(s)
        return s

    def paint(self, summary):
        self.overlay.setSummary(summary)
        self.assertFalse(self.overlay.isVisible())
        image = QImage(self.overlay.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        p = QPainter(image)
        try:
            self.overlay.drawContent(p)  # raises here instead of being logged by paintEvent
        finally:
            p.end()
        return image

    def cells(self, pairs, i=0):
        return G.laneCells(self.summary(pairs)["lanes"][i])

    def assertFormatted(self, text, full, compact, font, width):
        """With the measured fonts the full form is expected; elsewhere whichever form the
        full-or-compact rule picks for the font that resolved"""
        measured, _ = resolvedFamilies()
        if measured:
            self.assertEqual(text, full)
        else:
            self.assertEqual(text, full if G.textWidth(font, full) <= width else compact)

    # ---------------- size and geometry (font-independent) ----------------

    def test_size_for_one_and_five_lanes(self):
        self.assertEqual(G.WIDTH, 220)
        self.assertEqual(G.LANES_TOP, G.TOP_H + G.LANES_GAP + G.LANE_MARKS_H)
        one, five = self.summary(INPUTS["single_lane"]), self.summary(INPUTS["mockup"])
        self.assertTrue(one["hasLanes"] and five["hasLanes"])
        self.assertEqual(G.overlayHeight(one), G.LANES_TOP + G.LANE_H + G.PAD)
        self.assertEqual(G.overlayHeight(five), G.LANES_TOP + 5 * G.LANE_H + G.PAD)
        for s in (one, five):
            self.overlay.setSummary(s)
            self.assertEqual((self.overlay.width(), self.overlay.height()),
                             (G.WIDTH, G.overlayHeight(s)))

    def test_no_lanes_gives_the_short_card(self):
        data = game(INPUTS["mockup"])
        for p in data["allPlayers"]:
            p["position"] = ""
        s = L.summarizeGameData(data)
        self.assertFalse(s["hasLanes"])
        image = self.paint(s)
        self.assertEqual(self.overlay.height(), G.TOP_H + G.PAD)
        # the column squares belong to the lane rows: none on the short card
        marks = G.laneMarks()
        for key, kind in (("ally", "win"), ("enemy", "lose")):
            x = int(marks[key].center().x())
            for y in range(int(marks[key].top()), min(int(marks[key].bottom()), image.height())):
                self.assertNotEqual(image.pixelColor(x, y).rgb(), resultColor(kind).rgb(),
                                    (key, y))

    def test_columns_line_up_and_stay_inside_the_card(self):
        cols = G.laneColumns(0)
        order = [cols[k] for k in ("label", "ally", "diff", "enemy")]
        self.assertEqual(order[0].left(), G.INNER_X)
        self.assertEqual(order[-1].right(), G.WIDTH - G.INNER_X)
        for a, b in zip(order, order[1:]):
            self.assertEqual(b.left() - a.right(), G.COL_GAP)
        # the difference is centred between the two value columns
        self.assertEqual(cols["diff"].center().x() - cols["ally"].right(),
                         cols["enemy"].left() - cols["diff"].center().x())
        self.assertEqual(G.laneColumns(3)["ally"].top(), G.LANES_TOP + 3 * G.LANE_H)

    def test_column_squares_sit_over_the_value_columns(self):
        cols, marks = G.laneColumns(0), G.laneMarks()
        hairline = G.TOP_H + G.LANES_GAP / 2
        for key in ("ally", "enemy"):
            mark = marks[key]
            self.assertEqual((mark.width(), mark.height()), (G.LEGEND_MARK, G.LEGEND_MARK))
            self.assertEqual(mark.center().x(), cols[key].center().x())
            self.assertGreater(mark.top(), hairline)
            self.assertLessEqual(mark.bottom(), cols[key].top())
        # painted in the legend's colours, for a player and a spectator alike
        for local in (True, False):
            image = self.paint(self.summary(INPUTS["mockup"], local))
            for key, kind in (("ally", "win"), ("enemy", "lose")):
                centre = marks[key].center().toPoint()
                self.assertEqual(image.pixelColor(centre).rgb(), resultColor(kind).rgb(),
                                 (key, local))

    # ---------------- fit ----------------

    def assertCellsFit(self, summary):
        measured, families = resolvedFamilies()
        if not measured:
            self.skipTest(f"column widths were measured with Bahnschrift / Segoe UI; this "
                          f"platform resolves {families} (the full-or-compact rule is "
                          f"covered by test_number_falls_back_to_compact)")
        fonts = G.laneFonts()
        for i, lane in enumerate(summary["lanes"]):
            cols = G.laneColumns(i)
            for key, (text, _) in G.laneCells(lane).items():
                self.assertLessEqual(G.textWidth(fonts[key], text), cols[key].width(),
                                     (key, text, families))
                ink = QFontMetrics(fonts[key]).tightBoundingRect(text)
                self.assertLessEqual(ink.height(), cols[key].height(), (key, text))

    def assertTopFits(self, labels):
        measured, families = resolvedFamilies()
        if not measured:
            self.skipTest(f"totals / headline fit measured with Bahnschrift / Segoe UI; "
                          f"this platform resolves {families}")
        fonts = G.topFonts()
        for value in TOTALS:
            for label in labels:
                text = G.totalText(value, label)
                self.assertLessEqual(G.textWidth(fonts["total"], text), G.totalSpace(label),
                                     (label, text))
        for diff in HEADLINES:
            text = G.headlineText(diff)
            self.assertLessEqual(G.textWidth(fonts["headline"], text), G.INNER_W, text)

    def checkAllInputs(self):
        for name, pairs in INPUTS.items():
            for local in (True, False):
                with self.subTest(name, local=local):
                    s = self.summary(pairs, local)
                    self.paint(s)
                    self.assertCellsFit(s)
        labels = [QCoreApplication.translate("GoldOverlay", label)
                  for label in ("You", "Enemy", "Blue", "Red")]
        with self.subTest("totals and headline"):
            self.assertTopFits(labels)

    def test_cells_fit_their_columns_in_english(self):
        self.checkAllInputs()

    def test_cells_fit_their_columns_in_chinese(self):
        translator = QTranslator()
        self.assertTrue(translator.load("Seraphine.zh_CN", I18N))
        self.app.installTranslator(translator)
        try:
            self.assertEqual(self.cells([(1, 1)])["label"][0], "上路")
            self.checkAllInputs()
        finally:
            self.app.removeTranslator(translator)

    # ---------------- number format ----------------

    def test_numbers_are_written_in_full_when_they_fit(self):
        value, diff = G.laneFonts()["ally"], G.laneFonts()["diff"]
        cells = self.cells(INPUTS["five_digits"])
        self.assertFormatted(cells["ally"][0], "12,345", "12.3k", value, G.LANE_VALUE_W)
        self.assertFormatted(cells["diff"][0], "+2,469", "+2.5k", diff, G.LANE_DIFF_W)
        self.assertFormatted(cells["enemy"][0], "9,876", "9.9k", value, G.LANE_VALUE_W)
        self.assertFormatted(self.cells([(1000, 9888)])["diff"][0], "−8,888", "−8.9k", diff,
                             G.LANE_DIFF_W)
        self.assertEqual(self.cells([(500, 725)])["ally"][0], "500")  # no compact form

    def test_totals_and_headline_are_written_in_full_when_they_fit(self):
        top = G.topFonts()
        for value, full, compact in ((10100, "10,100", "10.1k"), (99999, "99,999", "100.0k"),
                                     (123456, "123,456", "123.5k")):
            for label in ("You", "Enemy"):
                self.assertFormatted(G.totalText(value, label), full, compact, top["total"],
                                     G.totalSpace(label))
        for diff, full, compact in ((25, "+25", "+25"), (2469, "+2,469", "+2.5k"),
                                    (-12345, "−12,345", "−12.3k"),
                                    (-123456, "−123,456", "−123.5k")):
            self.assertFormatted(G.headlineText(diff), full, compact, top["headline"],
                                 G.INNER_W)

    def test_number_falls_back_to_compact(self):
        font = G.laneFonts()["ally"]
        self.assertEqual(G.fitNumber("12,345", "12.3k", font, 1), "12.3k")  # never elided
        self.assertEqual(G.fitNumber("12,345", "12.3k", font, 1000), "12,345")
        # a five-digit difference in the difference column: compact with the measured fonts
        diff = G.laneFonts()["diff"]
        for pairs, full, compact in (((28888, 500), "+28,388", "+28.4k"),
                                     ((500, 28888), "−28,388", "−28.4k")):
            text = self.cells([pairs])["diff"][0]
            if resolvedFamilies()[0]:
                self.assertEqual(text, compact)
            else:
                fits = G.textWidth(diff, full) <= G.LANE_DIFF_W
                self.assertEqual(text, full if fits else compact)
        # a wider fallback font: Microsoft YaHei writes "28,888" wider than the value column
        wide = QFont(["Microsoft YaHei"])
        wide.setPixelSize(13)
        if QFontInfo(wide).family() == "Microsoft YaHei":
            self.assertEqual(G.fitNumber("28,888", "28.9k", wide, G.LANE_VALUE_W), "28.9k")

    # ---------------- no "Even" ----------------

    def test_zero_and_tiny_differences_are_numbers(self):
        # under 1,000 the full and compact forms are the same: font-independent
        ink, win = G.COLORS["ink"], resultColor("win")
        self.assertEqual(self.cells([(2900, 2900)])["diff"], ("0", ink))
        self.assertEqual(self.cells([(1400, 1300)])["diff"], ("+100", win))
        self.assertEqual(self.cells([(1300, 1400)])["diff"], ("−100", resultColor("lose")))
        self.assertEqual(G.headlineText(0), "0")
        self.assertEqual(diffColor(0, ink), ink)
        self.assertEqual(diffColor(1, ink), win)
        self.assertEqual(diffText(0, True), "0")

    def test_overlay_and_panel_never_ask_for_even(self):
        recorder = RecordingTranslator()
        self.app.installTranslator(recorder)
        panel = ItemGoldPanel()
        try:
            panel.content.resize(1100, CONTENT_H)
            for pairs in (INPUTS["zero_difference"], INPUTS["tiny_difference"],
                          INPUTS["mockup"]):
                s = self.summary(pairs)
                self.paint(s)
                panel.content.setSummary(s)
                self.assertFalse(panel.content.grab().isNull())
            self.assertTrue(any(src == "Item value" for _, src in recorder.sources))
            self.assertNotIn("Even", [src for _, src in recorder.sources])
        finally:
            self.app.removeTranslator(recorder)
            signalBus.liveGameDataChanged.disconnect(panel.setSummary)
            panel.deleteLater()

    # ---------------- missing side ----------------

    def test_missing_side_draws_dashes(self):
        muted, value = G.COLORS["muted"], G.laneFonts()["ally"]
        s = self.summary(INPUTS["missing_sides"])
        self.assertTrue(s["hasLanes"])
        self.assertIsNone(s["lanes"][1]["enemy"])
        self.assertIsNone(s["lanes"][2]["ally"])
        enemyMissing, allyMissing = G.laneCells(s["lanes"][1]), G.laneCells(s["lanes"][2])
        self.assertFormatted(enemyMissing["ally"][0], "2,100", "2.1k", value, G.LANE_VALUE_W)
        self.assertEqual(enemyMissing["enemy"], (G.MISSING, muted))
        self.assertEqual(enemyMissing["diff"], (G.MISSING, muted))
        self.assertEqual(allyMissing["ally"], (G.MISSING, muted))
        self.assertEqual(allyMissing["diff"], (G.MISSING, muted))
        self.assertFormatted(allyMissing["enemy"][0], "2,900", "2.9k", value, G.LANE_VALUE_W)
        self.paint(s)

    def test_saved_position_near_the_right_edge_stays_on_screen(self):
        from app.common.config import cfg
        area = QApplication.primaryScreen().availableGeometry()
        old = cfg.goldOverlayPos.value
        try:
            cfg.goldOverlayPos.value = [area.right() - 20, area.y() + 40]  # in memory only
            self.overlay.setSummary(self.summary(INPUTS["mockup"]))
            self.overlay._GoldOverlay__placeOnScreen()
            self.assertTrue(area.contains(self.overlay.frameGeometry()))
            self.assertEqual(self.overlay.x(), area.x() + area.width() - G.WIDTH)
        finally:
            cfg.goldOverlayPos.value = old


if __name__ == "__main__":
    unittest.main()
