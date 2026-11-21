"""Cooldown ledger panel (app/components/ledger_panel.py): geometry, text fit at real font
metrics (English and the zh_CN UI font), the click-vs-drag state machine with synthetic
mouse events, the Summoner's Rift gate, window flags in both lock states, icon loaders that
fail, and the wording rule (no "~", no "estimate"). Real LedgerTimers (fake clock) and real
live summaries; the database is a small fixture in the ledger_baseline.json shape.
Offscreen only: nothing is shown. With SERAPHINE_RENDER_DIR set, test_renders writes a PNG
of every cell state there (English, then zh_CN); nothing is written otherwise."""
import asyncio
import copy
import json
import os
import re
import tempfile
import unittest

from PySide6.QtCore import QEvent, QPointF, Qt, QTranslator
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QImage, QMouseEvent, \
    QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from app.common.config import cfg
from app.components import ledger_panel as P
from app.lol import live_client as L
from app.lol import ledger_model as M

I18N = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "app", "resource", "i18n")
ICON_DIRS = {'spell': os.path.join("app", "resource", "game", "summoner spell icons"),
             'item': os.path.join("app", "resource", "game", "item icons")}
FONT_DIR = r"C:\Windows\Fonts"
FONT_FILES = ("bahnschrift.ttf", "segoeui.ttf", "segoeuib.ttf", "msyh.ttc", "msyhbd.ttc")
ZH_FAMILY = "Microsoft YaHei"  # the zh_CN UI font

_fontIds = []


def setUpModule():
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "offscreen":
        # never put a real window on screen: this module needs QT_QPA_PLATFORM=offscreen
        raise RuntimeError("run the ledger panel tests with QT_QPA_PLATFORM=offscreen")
    # the offscreen platform has no fonts of its own: load the ones the paint code asks
    # for. Registration is process-wide, so tearDownModule removes them again
    for name in FONT_FILES:
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path):
            fontId = QFontDatabase.addApplicationFont(path)
            if fontId >= 0:
                _fontIds.append(fontId)


def tearDownModule():
    while _fontIds:
        QFontDatabase.removeApplicationFont(_fontIds.pop())


# ---------------- fixtures ----------------

def cds(*values):
    return {'cooldown': list(values)}


def champion(q=(8, 7, 6, 5, 4), w=(12, 11, 10, 9, 8), e=(16, 15, 14, 13, 12),
             r=(120, 100, 80), priority="QEW"):
    return {'spells': {'Q': cds(*q) if q is not None else {'cooldown': None},
                       'W': cds(*w), 'E': cds(*e), 'R': cds(*r)},
            'maxRank': {'Q': 5, 'W': 5, 'E': 5, 'R': 3}, 'rLevels': [6, 11, 16],
            'priority': priority}


DB = {
    'schema': 1, 'patch': '15.23',
    'champions': {
        'Garen': champion(q=(120, 120, 120, 120, 120)),
        'Lee Sin': champion(),
        'Ahri': champion(),
        'Jinx': champion(),
        'Thresh': champion(),
        'Singed': champion(q=None),          # a toggle: inestimable
    },
    'summonerSpells': {
        'SummonerFlash': {'id': 4, 'cooldown': 300},
        'SummonerDot': {'id': 14, 'cooldown': 180},
        'SummonerTeleport': {'id': 12, 'cooldown': 360},
        'S12_SummonerTeleportUpgrade': {'id': 32, 'cooldown': 240},
        'SummonerSmite': {'id': 11, 'cooldown': 15},
        'S5_SummonerSmitePlayerGanker': {'id': 2201, 'cooldown': None},
        'SummonerHeal': {'id': 7, 'cooldown': 240},
        'SummonerExhaust': {'id': 3, 'cooldown': 240},
    },
    'items': {
        '3157': {'active': 120, 'haste': 15, 'ultHaste': 0, 'spellHaste': 0},  # Zhonya's
        '3140': {'active': 90, 'haste': 0, 'ultHaste': 0, 'spellHaste': 0},    # QSS
        '2420': {'active': 120, 'haste': 0, 'ultHaste': 0, 'spellHaste': 0},   # Stopwatch
        '6631': {'active': 15, 'haste': 20, 'ultHaste': 0, 'spellHaste': 0},   # Stridebreaker
        '3006': {'active': None, 'haste': 0, 'ultHaste': 0, 'spellHaste': 0},  # no active
    },
}

SPELL_RAW = "GeneratedTip_SummonerSpell_{}_DisplayName"

ENEMIES = [  # (position, champion, spells, items)
    ("TOP", "Garen", ("SummonerTeleport", "SummonerFlash"), ()),
    ("JUNGLE", "Lee Sin", ("SummonerSmite", "SummonerFlash"), ()),
    ("MIDDLE", "Ahri", ("SummonerFlash", "SummonerDot"), (3157,)),
    ("BOTTOM", "Jinx", ("SummonerHeal", "SummonerFlash"), ()),
    ("UTILITY", "Thresh", ("SummonerFlash", "SummonerExhaust"), ()),
]


RAW_NAMES = {"Wukong": "MonkeyKing"}  # displayed name -> the payload's English raw name


def player(team, position, champ, spells, items, name, level=7):
    raw = RAW_NAMES.get(champ, champ.replace(" ", ""))
    return {"team": team, "position": position, "riotId": name, "summonerName": name,
            "championName": champ, "level": level,
            "rawChampionName": f"game_character_displayname_{raw}",
            "summonerSpells": {"summonerSpellOne": {"rawDisplayName": SPELL_RAW.format(spells[0])},
                               "summonerSpellTwo": {"rawDisplayName": SPELL_RAW.format(spells[1])}},
            "items": [{"itemID": i, "slot": s, "price": 1000} for s, i in enumerate(items)]}


def payload(enemies=ENEMIES, gameTime=600.0, gameMode="CLASSIC", mapNumber=11):
    players = [player(L.ORDER, pos, "Annie", ("SummonerFlash", "SummonerDot"), (), f"Ally{i}")
               for i, (pos, *_rest) in enumerate(ENEMIES)]
    players += [player(L.CHAOS, pos, champ, spells, items, f"Enemy{i}")
                for i, (pos, champ, spells, items) in enumerate(enemies)]
    return {"allPlayers": players, "activePlayer": {"riotId": "Ally0", "currentGold": 300},
            "gameData": {"gameMode": gameMode, "gameTime": gameTime, "mapNumber": mapNumber}}


def summary(**kw):
    s = L.summarizeGameData(payload(**kw))
    assert s is not None
    return s


def withEnemy(index, **changes):
    enemies = [list(e) for e in ENEMIES]
    for key, value in changes.items():
        enemies[index][('position', 'champion', 'spells', 'items').index(key)] = value
    return [tuple(e) for e in enemies]


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


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


def mouse(kind, panel, local, button=Qt.LeftButton):
    local = QPointF(*local)
    globalPos = local + QPointF(panel.pos())
    buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else button
    if kind == QEvent.MouseMove:
        buttons = button
    return QMouseEvent(kind, local, globalPos, button if kind != QEvent.MouseMove
                       else Qt.NoButton, buttons, Qt.NoModifier)


def centre(column, row):
    c = P.columnRect(column, row).center()
    return c.x(), c.y()


def timerState(panel, row, cell):
    return panel.timers.state(P.LANES[row], cell)[0]


class LedgerPanelTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()

    def setUp(self):
        self.saved = {item: item.value for item in
                      (cfg.enableCooldownLedger, cfg.goldOverlayLocked, cfg.cooldownLedgerPos,
                       cfg.goldOverlayOpacity, cfg.cooldownLedgerScale)}
        cfg.enableCooldownLedger.value = True  # in memory only
        cfg.goldOverlayLocked.value = False
        self.clock = FakeClock()
        self.panel = P.LedgerPanel(copy.deepcopy(DB), clock=self.clock)

    def tearDown(self):
        self.panel.disconnectSources()
        self.panel.deleteLater()
        for item, value in self.saved.items():
            item.value = value

    def show(self, s=None):
        self.panel.setSummary(summary() if s is None else s)
        self.assertFalse(self.panel.isVisible())  # offscreen: never on screen

    def press(self, local, button=Qt.LeftButton):
        self.panel.mousePressEvent(mouse(QEvent.MouseButtonPress, self.panel, local, button))

    def move(self, local):
        self.panel.mouseMoveEvent(mouse(QEvent.MouseMove, self.panel, local))

    def release(self, local, button=Qt.LeftButton):
        self.panel.mouseReleaseEvent(mouse(QEvent.MouseButtonRelease, self.panel, local,
                                           button))

    def click(self, column, row, jitter=(0, 0)):
        x, y = centre(column, row)
        self.press((x, y))
        if jitter != (0, 0):
            self.move((x + jitter[0], y + jitter[1]))
        self.release((x + jitter[0], y + jitter[1]))

    # ---------------- geometry ----------------

    def test_grid_has_no_gaps_and_the_agreed_cell_sizes(self):
        keys = [k for k, _, _ in P.COLUMNS]
        self.assertEqual(keys, ['lane', 'Q', 'W', 'E', 'R', 'D', 'F', 'item0', 'item1',
                                'item2', 'TP'])
        for (_, x, w), (_, nx, _) in zip(P.COLUMNS, P.COLUMNS[1:]):
            self.assertEqual(x + w, nx)  # no spacing between cells
        for key in 'QWER':
            r = P.columnRect(key, 0)
            self.assertEqual((r.width(), r.height()), (36, 28))
        for key in ('D', 'F', 'item0', 'item1', 'item2', 'TP'):
            r = P.columnRect(key, 0)
            self.assertEqual((r.width(), r.height()), (30, 28))
        # the client's rule: Q W E R larger than the rarely clicked spell / item cells
        self.assertGreater(P.ABILITY_W, P.SLOT_W)
        self.assertEqual((P.WIDTH, P.HEIGHT), (360, 148))
        self.assertLessEqual(P.LANE_ICON, P.ROW_H)
        self.assertLessEqual(P.LANE_ICON, P.LANE_W)
        self.assertEqual(P.columnRect('Q', 4).bottom() + P.PAD, P.HEIGHT)
        self.assertEqual(P.columnRect('TP', 0).right() + P.PAD, P.WIDTH)
        self.assertEqual((self.panel.width(), self.panel.height()), (P.WIDTH, P.HEIGHT))

        # every point of the grid hits exactly the cell it is drawn in
        for row in range(5):
            for key, x, w in P.COLUMNS:
                r = P.columnRect(key, row)
                for px, py in ((r.left(), r.top()), (r.right() - 0.5, r.bottom() - 0.5)):
                    self.assertEqual(P.LedgerPanel.gridAt(QPointF(px, py)), (row, key))
        self.assertIsNone(P.LedgerPanel.gridAt(QPointF(1, 1)))  # padding

    # ---------------- size setting ----------------

    def test_size_setting_scales_the_window_at_once(self):
        self.assertEqual(cfg.cooldownLedgerScale.value, 100)       # the default: normal size
        for percent, size in ((150, (540, 222)), (80, (288, 119)), (200, (720, 296)),
                              (100, (P.WIDTH, P.HEIGHT))):
            with self.subTest(percent=percent):
                cfg.set(cfg.cooldownLedgerScale, percent)
                self.assertEqual((self.panel.width(), self.panel.height()), size)
        self.assertEqual(cfg.cooldownLedgerScale.range, (80, 200))

    def test_clicks_hit_the_same_cell_at_any_size(self):
        self.show()
        for percent in (150, 80, 200):
            cfg.set(cfg.cooldownLedgerScale, percent)
            s = percent / 100
            for column, row in (('Q', 0), ('R', 3), ('D', 2), ('F', 4)):
                with self.subTest(percent=percent, cell=(column, row)):
                    x, y = centre(column, row)
                    self.press((x * s, y * s))
                    self.release((x * s, y * s))
                    self.assertEqual(timerState(self.panel, row, column), 'running')
                    self.press((x * s, y * s))
                    self.release((x * s, y * s))
                    self.assertEqual(timerState(self.panel, row, column), 'idle')
        # a point that is inside the scaled panel but past the unscaled width hits the last
        # column, not nothing
        cfg.set(cfg.cooldownLedgerScale, 200)
        x, y = centre('TP', 0)
        self.assertGreater(x * 2, P.WIDTH)
        self.assertEqual(P.LedgerPanel.gridAt(self.panel.unscaled(QPointF(x * 2, y * 2))), (0, 'TP'))

    def test_scaled_panel_renders_icons_at_the_scaled_density(self):
        self.show()
        cfg.set(cfg.cooldownLedgerScale, 150)
        P._tinted.clear()
        image = QImage(self.panel.width() * 2, self.panel.height() * 2, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(2)
        image.fill(QColor('#3A4A3C'))
        p = QPainter(image)
        try:
            p.scale(self.panel.scale(), self.panel.scale())
            self.panel.drawContent(p)
        finally:
            p.end()
        # lane icons drawn at 2 (device) x 1.5 (size setting) pixels per layout unit
        self.assertTrue(P._tinted)
        self.assertEqual({key[2] for key in P._tinted}, {3.0})
        # densities go in steps of 0.5: sizes 101 % to 125 % share one cache entry
        for percent in range(101, 126):
            cfg.set(cfg.cooldownLedgerScale, percent)
            image = QImage(self.panel.width(), self.panel.height(), QImage.Format_ARGB32_Premultiplied)
            image.fill(QColor('#3A4A3C'))
            p = QPainter(image)
            try:
                p.scale(self.panel.scale(), self.panel.scale())
                self.panel.drawContent(p)
            finally:
                p.end()
        self.assertEqual({key[2] for key in P._tinted}, {3.0, 1.5})

    def test_window_paint_uses_the_size_setting(self):
        # the real paintEvent: a running Q cell, painted at 150 %, is blue where the scaled
        # cell is, and the right border sits inside the window
        self.show()
        self.panel.clickCell(0, 'Q')
        cfg.set(cfg.cooldownLedgerScale, 150)
        image = self.panel.grab().toImage()
        self.assertEqual((image.width(), image.height()), (540, 222))
        q = P.columnRect('Q', 0)
        inside = image.pixelColor(int((q.right() - 4) * 1.5), int((q.bottom() - 4) * 1.5))
        self.assertGreater(inside.blue(), inside.red() + 40, inside.name())
        # unscaled, that point lies in another cell, which is not running
        w = P.columnRect(*P.LedgerPanel.gridAt(QPointF((q.right() - 4) * 1.5, (q.bottom() - 4) * 1.5))[::-1])
        self.assertNotEqual(w, q)
        self.assertGreater(image.pixelColor(539, 111).alpha(), 0)   # the right border is painted

    def test_rows_are_top_jungle_mid_bottom_support_with_flash_left(self):
        enemies = list(reversed(ENEMIES))  # API order must not matter
        self.show(summary(enemies=enemies))
        self.assertEqual([self.panel.rows[lane]['championName'] for lane in P.LANES],
                         ["Garen", "Lee Sin", "Ahri", "Jinx", "Thresh"])
        for row in range(5):
            self.assertEqual(self.panel.cellView(row, 'lane')['icon'],
                             ('top', 'jng', 'mid', 'bot', 'sup')[row])
            self.assertEqual(self.panel.cellView(row, 'D')['text'], 'Fl')  # Flash left
        self.assertEqual([self.panel.cellView(row, 'F')['text'] for row in range(5)],
                         ['TP', 'Sm', 'Ig', 'He', 'Ex'])

    # ---------------- text fit ----------------

    def fonts(self, zh):
        """The cell fonts; zh: the text fonts (letters, labels) in the zh_CN UI font. The
        numbers keep their number font in every language (numberFont's families do not
        depend on the language), so they are measured as drawn"""
        fonts = {'ability': P.abilityFont(), 'corner': P.cornerFont(),
                 'seconds': P.secondsFont(), 'return': P.returnFont(),
                 'label': P.slotLabelFont()}
        if zh:
            for key in ('ability', 'corner', 'label'):
                fonts[key] = QFont(fonts[key])
                fonts[key].setFamilies([ZH_FAMILY])
        return fonts

    def assertFits(self, font, text, width):
        self.assertLessEqual(P.textWidth(font, text), width, (text, font.family()))
        ink = QFontMetrics(font).tightBoundingRect(text)
        self.assertLessEqual(ink.height(), P.ROW_H, (text, font.family()))

    def test_text_fits_every_state_in_both_ui_fonts(self):
        labels = sorted(set(P.SPELL_LABELS.values()) | {P.ITEM_LABEL,
                                                         P.spellLabel('SummonerSnowball')})
        # every countdown a Q W E R cell can show (0-999 s) and every return time of a game
        # of up to 99:59, as the model formats them
        countdowns = {M.cellText('R', ('running', n - 0.5, 0.0)) for n in range(1000)}
        clocks = {M.cellText('D', ('running', 1.0, float(t))) for t in range(6000)}
        self.assertEqual(len(countdowns), 1000)
        self.assertIn('999', countdowns)
        self.assertIn('59:59', clocks)
        self.assertIn('99:59', clocks)
        number = P.abilityNumberRect(P.columnRect('Q', 0))
        for zh in (False, True):
            with self.subTest(zh=zh):
                f = self.fonts(zh)
                widest = max(countdowns, key=lambda t: P.textWidth(f['seconds'], t))
                self.assertFits(f['seconds'], widest, number.width() - 4)
                for text in 'QWER':
                    self.assertFits(f['ability'], text, P.ABILITY_W - 4)
                # the corner letter's ink ends above the seconds' ink
                corner = f['corner']
                cell = P.columnRect('Q', 0)
                for letter in 'QWER':
                    cornerBottom = (P.cornerRect(cell).top() + QFontMetrics(corner).ascent()
                                    + QFontMetrics(corner).tightBoundingRect(letter).bottom())
                    for text in countdowns:
                        fm = QFontMetrics(f['seconds'])
                        baseline = number.top() + (number.height() - fm.height()) / 2 \
                            + fm.ascent()
                        ink = fm.tightBoundingRect(text)
                        self.assertLess(cornerBottom, baseline + ink.top(), (letter, text))
                        self.assertLessEqual(baseline + ink.bottom(), cell.bottom(), text)
                widest = max(clocks, key=lambda t: P.textWidth(f['return'], t))
                self.assertFits(f['return'], widest, P.SLOT_W - 2)  # 28 px
                for text in labels:
                    self.assertEqual(len(text), 2, text)
                    self.assertFits(f['label'], text, P.SLOT_W - 4)
        self.assertEqual(len(set(P.SPELL_LABELS[k] for k in ('SummonerBoost', 'SummonerMana'))),
                         2)  # Cleanse and Clarity are told apart
        # the texts the cells really draw for those values
        self.assertEqual(M.cellText('R', ('running', 119.2, 700.0)), '120')
        self.assertEqual(M.cellText('R', ('running', 998.2, 1500.0)), '999')
        self.assertEqual(M.cellText('D', ('running', 10.0, 850.0)), '14:10')
        self.assertEqual(M.cellText('D', ('running', 10.0, 3599.0)), '59:59')

    # ---------------- click vs drag ----------------

    def test_click_starts_and_second_click_cancels(self):
        self.show()
        self.click('Q', 0)
        state = self.panel.timers.state('TOP', 'Q')
        self.assertEqual(state[0], 'running')
        self.assertEqual(self.panel.cellView(0, 'Q')['text'], M.cellText('Q', state))
        self.click('Q', 0)
        self.assertEqual(timerState(self.panel, 0, 'Q'), 'idle')
        self.assertEqual(self.panel.cellView(0, 'Q')['text'], 'Q')

    def test_four_px_jitter_is_a_click(self):
        self.show()
        pos = self.panel.pos()
        cfg.cooldownLedgerPos.value = []
        self.click('W', 1, jitter=(4, 0))
        self.assertEqual(timerState(self.panel, 1, 'W'), 'running')
        self.assertEqual(self.panel.pos(), pos)
        self.assertEqual(cfg.cooldownLedgerPos.value, [])  # no drag, nothing saved
        self.click('W', 1, jitter=(3, 3))  # diagonal, 4.2 px
        self.assertEqual(timerState(self.panel, 1, 'W'), 'idle')

    def test_six_px_unlocked_is_a_drag_and_saves_the_position(self):
        self.show()
        self.panel.move(100, 200)
        before = list(cfg.cooldownLedgerPos.value)
        self.click('E', 2, jitter=(6, 0))
        self.assertEqual(timerState(self.panel, 2, 'E'), 'idle')
        self.assertEqual((self.panel.x(), self.panel.y()), (106, 200))
        self.assertEqual(cfg.cooldownLedgerPos.value, [106, 200])
        self.assertNotEqual(cfg.cooldownLedgerPos.value, before)

    def test_six_px_locked_is_a_click_and_nothing_moves(self):
        self.show()
        cfg.goldOverlayLocked.value = True
        self.panel.move(100, 200)
        cfg.cooldownLedgerPos.value = []
        self.click('E', 2, jitter=(6, 0))
        self.assertEqual(timerState(self.panel, 2, 'E'), 'running')
        self.assertEqual((self.panel.x(), self.panel.y()), (100, 200))
        self.assertEqual(cfg.cooldownLedgerPos.value, [])

    def test_press_inside_release_outside_does_nothing(self):
        self.show()
        cfg.goldOverlayLocked.value = True  # no drag either
        x, y = centre('R', 3)
        self.press((x, y))
        self.move((x, P.HEIGHT + 30))
        self.release((x, P.HEIGHT + 30))
        self.assertEqual(timerState(self.panel, 3, 'R'), 'idle')
        # a press in one cell and release in another (locked, far apart): no click
        self.press(centre('Q', 3))
        self.release(centre('F', 3))
        self.assertEqual(timerState(self.panel, 3, 'Q'), 'idle')
        self.assertEqual(timerState(self.panel, 3, 'F'), 'idle')

    def test_right_button_and_padding_do_nothing(self):
        self.show()
        x, y = centre('Q', 0)
        self.press((x, y), Qt.RightButton)
        self.release((x, y), Qt.RightButton)
        self.assertEqual(timerState(self.panel, 0, 'Q'), 'idle')
        self.press((1, 1))
        self.release((1, 1))
        self.click('lane', 0)
        self.click('item2', 0)  # empty item slot
        self.assertFalse(self.panel.timers.active())

    def test_lock_cell_toggles_the_shared_lock(self):
        self.show()
        self.assertEqual(self.panel.cellView(P.LOCK_ROW, P.LOCK_COLUMN)['kind'], 'lock')
        self.click(P.LOCK_COLUMN, P.LOCK_ROW)
        self.assertTrue(cfg.get(cfg.goldOverlayLocked))
        self.click(P.LOCK_COLUMN, P.LOCK_ROW)
        self.assertFalse(cfg.get(cfg.goldOverlayLocked))
        # the whole cell is the target, not just the small padlock: each corner toggles
        r = P.columnRect(P.LOCK_COLUMN, P.LOCK_ROW)
        for x, y in ((r.left() + 0.5, r.top() + 0.5), (r.right() - 0.5, r.top() + 0.5),
                     (r.left() + 0.5, r.bottom() - 0.5), (r.right() - 0.5, r.bottom() - 0.5)):
            before = cfg.get(cfg.goldOverlayLocked)
            self.press((x, y))
            self.release((x, y))
            self.assertNotEqual(cfg.get(cfg.goldOverlayLocked), before, (x, y))

    def test_inestimable_cell_does_nothing(self):
        self.show(summary(enemies=withEnemy(0, champion="Singed")))
        self.assertIsNone(self.panel.duration(self.panel.rows['TOP'], 'Q'))
        self.assertFalse(self.panel.cellView(0, 'Q')['active'])
        self.click('Q', 0)
        self.assertEqual(timerState(self.panel, 0, 'Q'), 'idle')
        self.assertFalse(self.panel.ticker.isActive())

    # ---------------- timers on the panel ----------------

    def test_countdown_for_abilities_return_time_for_the_rest(self):
        self.show(summary(gameTime=600.0))
        self.click('Q', 0)  # Garen Q: 120 s
        self.assertEqual(self.panel.cellView(0, 'Q')['text'], '120')
        self.click('D', 0)  # Flash
        state = self.panel.timers.state('TOP', 'D')
        self.assertEqual(state[0], 'running')
        self.assertEqual(self.panel.cellView(0, 'D')['text'], M.cellText('D', state))
        self.assertEqual(self.panel.cellView(0, 'D')['text'], '15:00')  # 600 + Flash 300
        self.assertTrue(self.panel.ticker.isActive())
        self.assertEqual(self.panel.ticker.interval(), 500)
        # the countdown follows the clock between polls
        self.clock.t += 2
        self.assertEqual(self.panel.cellView(0, 'Q')['text'], '118')

    def test_ticker_runs_only_while_a_timer_runs(self):
        self.show()
        self.assertFalse(self.panel.ticker.isActive())
        length = self.panel.duration(self.panel.rows['JUNGLE'], 'Q')
        self.assertGreater(length, 2)
        self.click('Q', 1)
        self.assertTrue(self.panel.ticker.isActive())
        self.clock.t += 1
        self.panel.setSummary(summary(gameTime=601.0))
        self.assertTrue(self.panel.ticker.isActive())
        self.clock.t += 1
        self.panel.setSummary(summary(gameTime=600.0 + length + 1))
        self.panel._LedgerPanel__onTick()
        self.assertEqual(timerState(self.panel, 1, 'Q'), 'idle')
        self.assertFalse(self.panel.ticker.isActive())

    def test_spell_upgrade_in_place_keeps_the_timer(self):
        self.show()
        self.click('F', 0)  # Garen: Teleport is the non-Flash spell -> F column
        self.assertEqual(timerState(self.panel, 0, 'F'), 'running')
        up = withEnemy(0, spells=("S12_SummonerTeleportUpgrade", "SummonerFlash"))
        self.panel.setSummary(summary(enemies=up, gameTime=601.0))
        self.assertEqual(timerState(self.panel, 0, 'F'), 'running')
        self.assertEqual(self.panel.cellView(0, 'D')['text'], 'Fl')

    def test_item_cell_appears_and_tp_only_on_top_without_teleport(self):
        self.show()
        self.assertEqual(self.panel.cellIdAt(2, 'item0'), 'item:3157')
        self.assertIsNone(self.panel.cellIdAt(2, 'item1'))
        self.assertIsNone(self.panel.cellIdAt(0, 'TP'))  # Garen carries Teleport
        noTp = withEnemy(0, spells=("SummonerFlash", "SummonerDot"))
        self.panel.setSummary(summary(enemies=noTp))
        self.assertEqual(self.panel.cellIdAt(0, 'TP'), 'TP')
        self.assertEqual(self.panel.cellView(0, 'TP')['text'], 'TP')
        for row in (1, 2, 3):
            self.assertIsNone(self.panel.cellIdAt(row, 'TP'))
        more = withEnemy(1, items=(3140,))
        self.panel.setSummary(summary(enemies=more))
        self.assertEqual(self.panel.cellIdAt(1, 'item0'), 'item:3140')
        self.click('item0', 1)
        self.assertEqual(timerState(self.panel, 1, 'item:3140'), 'running')

    def test_item_slots_never_move_under_the_cursor(self):
        self.show(summary(enemies=withEnemy(1, items=(3157, 3140))))
        self.assertEqual((self.panel.cellIdAt(1, 'item0'), self.panel.cellIdAt(1, 'item1')),
                         ('item:3157', 'item:3140'))
        self.panel.setSummary(summary(enemies=withEnemy(1, items=(3140,)), gameTime=601.0))
        # the sold item leaves a hole: QSS stays in the second slot, the first is blank
        self.assertIsNone(self.panel.cellIdAt(1, 'item0'))
        self.assertIsNone(self.panel.cellView(1, 'item0'))
        self.assertEqual(self.panel.cellIdAt(1, 'item1'), 'item:3140')
        self.click('item0', 1)
        self.assertFalse(self.panel.timers.active())
        self.click('item1', 1)
        self.assertEqual(timerState(self.panel, 1, 'item:3140'), 'running')

    def test_works_on_a_loaded_ledger_database(self):
        from app.lol.ledger_data import LedgerDatabase
        with tempfile.TemporaryDirectory() as tmp:
            baseline = os.path.join(tmp, "ledger_baseline.json")
            with open(baseline, "w", encoding="utf-8") as f:
                json.dump(DB, f)
            db = LedgerDatabase(baselinePath=baseline, configDir=os.path.join(tmp, "cfg")).load()
            panel = P.LedgerPanel(db, clock=self.clock)
            try:
                panel.setSummary(summary())
                self.assertEqual(panel.cellIdAt(2, 'item0'), 'item:3157')
                panel.clickCell(0, 'Q')
                self.assertEqual(panel.cellView(0, 'Q')['text'], '120')
                self.assertEqual(panel.cellView(0, 'F')['text'], 'TP')
            finally:
                panel.disconnectSources()
                panel.deleteLater()

    # ---------------- visibility ----------------

    def test_hidden_outside_summoners_rift(self):
        noPositions = [("", c, s, i) for _, c, s, i in ENEMIES]
        duplicate = withEnemy(4, position="BOTTOM")
        cases = {
            "URF": summary(gameMode="URF"),
            "ONEFORALL": summary(gameMode="ONEFORALL"),
            "ARAM": summary(gameMode="ARAM", mapNumber=12),
            "map 12": summary(mapNumber=12),
            "positions missing": summary(enemies=noPositions),
            "duplicate positions": summary(enemies=duplicate),
            "four enemies": summary(enemies=ENEMIES[:4]),
        }
        for name, s in cases.items():
            with self.subTest(name):
                self.show(summary())
                self.assertTrue(self.panel.wanted)
                self.panel.setSummary(s)
                self.assertFalse(self.panel.wanted)
                self.assertEqual(self.panel.hiddenReason, "not Summoner's Rift with five lanes")
        self.panel.setSummary(None)
        self.assertFalse(self.panel.wanted)
        self.assertEqual(self.panel.hiddenReason, "no game")
        self.show(summary())
        self.assertTrue(self.panel.wanted)

    def test_recorded_game_shows_and_paints(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                            "liveclient_allgamedata.json")
        with open(path, encoding="utf-8") as f:
            s = L.summarizeGameData(json.load(f))
        self.show(s)
        self.assertTrue(self.panel.wanted)
        self.assertEqual(sorted(self.panel.rows), sorted(P.LANES))
        # the recording has no spell names: the spell cells stay blank and do nothing
        self.assertIsNone(self.panel.cellView(0, 'D'))
        self.click('D', 0)
        self.assertFalse(self.panel.timers.active())
        self.assertFalse(self.render(self.panel).isNull())

    def test_setting_off_hides_and_logs_only_changes(self):
        with self.assertLogs("Seraphine", "INFO") as logs:
            self.show()
            self.show()  # same state again: no second line
            cfg.set(cfg.enableCooldownLedger, False)
            self.assertFalse(self.panel.wanted)
            self.assertEqual(self.panel.hiddenReason, "setting off")
            cfg.set(cfg.enableCooldownLedger, True)
            self.assertTrue(self.panel.wanted)
        lines = [r.getMessage() for r in logs.records if r.getMessage().startswith("ledger panel")]
        self.assertEqual(lines, ["ledger panel shown", "ledger panel hidden (setting off)",
                                 "ledger panel shown"])

    def test_default_is_off(self):
        self.assertIs(cfg.enableCooldownLedger.defaultValue, False)
        self.assertEqual(cfg.cooldownLedgerPos.defaultValue, [])
        self.assertEqual((cfg.enableCooldownLedger.group, cfg.enableCooldownLedger.name),
                         ("Functions", "EnableCooldownLedger"))
        self.assertEqual((cfg.cooldownLedgerPos.group, cfg.cooldownLedgerPos.name),
                         ("Other", "CooldownLedgerPos"))

    def test_enemies_seen_once_per_set_of_enemies(self):
        seen = []
        self.panel.enemiesSeen.connect(seen.append)
        self.show()
        self.show(summary(gameTime=605.0))
        self.assertEqual(seen, [[{'championName': self.panel.championOf(self.panel.rows[p]),
                                  'position': p} for p, _, _, _ in ENEMIES]])
        self.assertEqual([e['championName'] for e in seen[0]],
                         ["Garen", "LeeSin", "Ahri", "Jinx", "Thresh"])  # raw names
        cfg.enableCooldownLedger.value = False
        self.panel.setSummary(summary(enemies=withEnemy(0, champion="Singed")))
        self.assertEqual(len(seen), 1)  # setting off: nothing to refresh for

    # ---------------- window flags ----------------

    def test_flags_never_click_through_and_lock_recreates_nothing(self):
        calls = []
        original = self.panel.setWindowFlags
        self.panel.setWindowFlags = lambda flags: (calls.append(flags), original(flags))
        for locked in (False, True, False):
            cfg.set(cfg.goldOverlayLocked, locked)
            flags = self.panel.windowFlags()
            self.assertFalse(flags & Qt.WindowTransparentForInput, locked)
            self.assertTrue(flags & Qt.WindowDoesNotAcceptFocus, locked)
            self.assertTrue(flags & Qt.WindowStaysOnTopHint, locked)
            self.assertTrue(flags & Qt.FramelessWindowHint, locked)
            self.assertEqual(flags & Qt.Tool, Qt.Tool, locked)
            self.assertFalse(self.panel.testAttribute(Qt.WA_TransparentForMouseEvents), locked)
        self.assertEqual(calls, [])
        for attribute in (Qt.WA_MacAlwaysShowToolWindow, Qt.WA_TranslucentBackground,
                          Qt.WA_ShowWithoutActivating):
            self.assertTrue(self.panel.testAttribute(attribute), attribute)
        self.assertEqual(self.panel.focusPolicy(), Qt.NoFocus)

    # ---------------- icons ----------------

    def test_icon_loader_key_error_keeps_the_text(self):
        async def failing(_):
            raise KeyError(4)

        panel = P.LedgerPanel(copy.deepcopy(DB), spellIcon=failing, itemIcon=failing,
                              clock=self.clock)
        try:
            async def run():
                panel.setSummary(summary())
                self.assertTrue(panel._tasks)
                await asyncio.gather(*panel._tasks)  # loadIcon never raises

            asyncio.run(run())
            self.assertEqual(panel.icons, {})
            self.assertEqual(panel.cellView(0, 'D')['text'], 'Fl')
            self.assertEqual(panel.cellView(2, 'item0')['text'], P.ITEM_LABEL)
            panel.setSummary(summary())  # failed icons are not retried
            self.assertEqual(panel._tasks, set())
        finally:
            panel.disconnectSources()
            panel.deleteLater()

    def test_loaded_icon_replaces_the_text(self):
        path = os.path.join("app", "resource", "images", "item-0.png")
        self.assertTrue(os.path.exists(path))

        async def loader(_):
            return path

        panel = P.LedgerPanel(copy.deepcopy(DB), spellIcon=loader, itemIcon=loader,
                              clock=self.clock)
        try:
            panel.setSummary(summary())
            asyncio.run(panel.loadIcon(('spell', 4)))
            self.assertEqual(panel.cellView(0, 'D')['text'], '')
            self.assertIsNotNone(panel.cellView(0, 'D')['icon'])
            panel.clickCell(0, 'D')
            self.assertRegex(panel.cellView(0, 'D')['text'], r'^\d\d:\d\d$')
        finally:
            panel.disconnectSources()
            panel.deleteLater()

    def test_spell_ids_the_client_does_not_list_are_never_requested(self):
        asked = []

        async def loader(i):
            asked.append(i)
            return os.path.join("app", "resource", "images", "item-0.png")

        panel = P.LedgerPanel(copy.deepcopy(DB), spellIcon=loader, clock=self.clock,
                              spellKnown=lambda i: i != 4)  # Flash (4) unknown to the client
        try:
            async def run():
                panel.setSummary(summary())
                await asyncio.gather(*panel._tasks)

            asyncio.run(run())
            self.assertNotIn(4, asked)
            self.assertIn(14, asked)  # Ignite
            self.assertEqual(panel.cellView(2, 'D')['text'], 'Fl')
            self.assertEqual(panel.cellView(2, 'F')['text'], '')  # Ignite's icon loaded
        finally:
            panel.disconnectSources()
            panel.deleteLater()

    def test_disconnect_cancels_pending_icon_loads(self):
        async def never(_):
            await asyncio.Event().wait()

        panel = P.LedgerPanel(copy.deepcopy(DB), spellIcon=never, itemIcon=never,
                              clock=self.clock)
        try:
            async def run():
                panel.setSummary(summary())
                tasks = list(panel._tasks)
                self.assertTrue(tasks)
                await asyncio.sleep(0)
                panel.disconnectSources()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                self.assertTrue(all(isinstance(r, asyncio.CancelledError) for r in results))

            asyncio.run(run())
            self.assertEqual(panel.icons, {})
        finally:
            panel.deleteLater()

    def test_icon_arriving_after_disconnect_is_dropped(self):
        gate = []

        async def slow(_):
            while not gate:
                await asyncio.sleep(0)
            return os.path.join("app", "resource", "images", "item-0.png")

        panel = P.LedgerPanel(copy.deepcopy(DB), spellIcon=slow, clock=self.clock)
        try:
            panel.setSummary(summary())

            async def run():
                task = asyncio.ensure_future(panel.loadIcon(('spell', 4)))
                await asyncio.sleep(0)
                panel._closed = True  # as disconnectSources, without cancelling this load
                gate.append(1)
                await task

            asyncio.run(run())
            self.assertEqual(panel.icons, {})
        finally:
            panel.disconnectSources()
            panel.deleteLater()

    def test_champion_found_by_raw_name_first(self):
        db = copy.deepcopy(DB)
        db['champions']['MonkeyKing'] = champion(q=(10, 10, 10, 10, 10))
        panel = P.LedgerPanel(db, clock=self.clock)
        seen = []
        panel.enemiesSeen.connect(seen.append)
        try:
            panel.setSummary(summary(enemies=withEnemy(0, champion="Wukong")))
            entry = panel.rows['TOP']
            self.assertEqual((entry['championName'], entry['rawChampionName']),
                             ("Wukong", "MonkeyKing"))
            self.assertEqual(panel.championOf(entry), "MonkeyKing")
            self.assertEqual(panel.duration(entry, 'Q'), 10)
            self.assertEqual(seen[0][0], {'championName': 'MonkeyKing', 'position': 'TOP'})
            # a raw name the database lacks falls back to the displayed name
            self.assertEqual(panel.championOf({'championName': 'Garen',
                                               'rawChampionName': 'Nope'}), 'Garen')
        finally:
            panel.disconnectSources()
            panel.deleteLater()

    def test_cooldown_failure_is_logged_once(self):
        original = P.cooldown

        def broken(*args, **kw):
            raise ValueError("bad row")

        P.cooldown = broken
        try:
            self.show()
            with self.assertLogs("Seraphine", "WARNING") as logs:
                for _ in range(3):  # three frames
                    self.render(self.panel)
        finally:
            P.cooldown = original
        failures = [r for r in logs.records if "ledger cooldown failed" in r.getMessage()]
        self.assertEqual(len(failures), len({(c, k) for c, k in self.panel._failed}))
        self.assertTrue(all("ValueError" in r.getMessage() for r in failures))
        self.assertFalse(any("bad row" in r.getMessage() for r in logs.records))

    def test_double_click_toggles_once(self):
        self.show()
        x, y = centre('Q', 0)
        self.press((x, y))
        self.release((x, y))
        self.panel.mouseDoubleClickEvent(mouse(QEvent.MouseButtonDblClick, self.panel, (x, y)))
        self.release((x, y))
        self.assertEqual(timerState(self.panel, 0, 'Q'), 'running')

    def test_background_follows_the_overlay_opacity(self):
        # Order independent: cfg is process-wide and other modules keep objects connected to
        # this setting (the overlay test keeps its GoldOverlay alive). Start from a known value
        # that differs from every value set below (cfg.set does nothing for an equal value),
        # restore the caller's value whatever happens, and never write config.json
        item = cfg.goldOverlayOpacity
        saved = item.value
        self.addCleanup(setattr, item, 'value', saved)
        item.value = 77
        repaints = []
        self.panel.update = lambda: repaints.append(item.value)  # spy on this panel only
        self.addCleanup(self.panel.__dict__.pop, 'update', None)
        for opacity in (30, 50, 100):
            before = len(repaints)
            cfg.set(item, opacity, save=False)  # the settings path: emits valueChanged
            # the change repaints the panel (other receivers may cause more repaints; each
            # one paints the new value)
            self.assertGreater(len(repaints), before, opacity)
            self.assertEqual(set(repaints[before:]), {opacity})
            self.assertEqual(self.panel.backgroundColor().alpha(),
                             round(P.BG_ALPHA * opacity / 100))
        self.panel.disconnectSources()
        count = len(repaints)
        cfg.set(item, 40, save=False)  # disconnected: no repaint any more
        self.assertEqual(len(repaints), count)

    def test_outlined_time_is_built_once_and_reused(self):
        self.show()
        for key, _, _ in P.COLUMNS:
            self.panel.clickCell(0, key)
        P._labels.clear()
        self.render(self.panel)
        built = dict(P._labels)
        self.assertTrue(built)  # the running slots' times
        self.render(self.panel)
        self.assertEqual(set(P._labels), set(built))
        for key, pixmap in built.items():
            self.assertIs(P._labels[key], pixmap)  # reused, not rebuilt
        texts = {k[0] for k in built}
        self.assertTrue(all(re.fullmatch(r'\d\d:\d\d', t) for t in texts), texts)
        # bounded: a full cache starts over
        for i in range(P.LABEL_CACHE_MAX + 5):
            P.outlinedLabel(str(i), P.returnFont(), QColor('white'), QColor('black'), 1.0)
        self.assertLessEqual(len(P._labels), P.LABEL_CACHE_MAX)

    def test_inestimable_icon_is_greyed_like_its_text(self):
        pixmap = QPixmap(4, 4)
        pixmap.fill(QColor(200, 40, 40))
        grey = P.greyPixmap(pixmap).toImage().pixelColor(1, 1)
        self.assertEqual(grey.red(), grey.green())
        self.assertEqual(grey.green(), grey.blue())
        db = copy.deepcopy(DB)
        db['summonerSpells']['SummonerFlash']['cooldown'] = None
        panel = P.LedgerPanel(db, clock=self.clock)
        try:
            panel.setSummary(summary())
            panel.icons[('spell', 4)] = pixmap
            view = panel.cellView(0, 'D')
            self.assertFalse(view['active'])
            self.assertIs(view['icon'], pixmap)
            self.assertFalse(self.render(panel).isNull())
        finally:
            panel.disconnectSources()
            panel.deleteLater()

    # ---------------- show path (nothing real is shown) ----------------

    def fakeShowable(self, panel):
        """overlayAllowed -> True and the window calls recorded: nothing reaches a screen"""
        state = {'visible': False, 'shown': 0, 'noActivate': 0}
        panel.isVisible = lambda: state['visible']
        panel.show = lambda: state.update(visible=True, shown=state['shown'] + 1)
        panel.hide = lambda: state.update(visible=False)
        return state

    def test_show_path_places_and_applies_no_activate_on_every_show(self):
        state = self.fakeShowable(self.panel)
        originalAllowed, originalNoActivate = P.overlayAllowed, P.platform.set_window_no_activate
        P.overlayAllowed = lambda: True
        P.platform.set_window_no_activate = lambda winId: state.update(
            noActivate=state['noActivate'] + 1)
        try:
            self.panel.setSummary(summary())
            self.panel.setSummary(summary(gameTime=601.0))  # already shown: no second call
            self.assertEqual((state['shown'], state['noActivate']), (1, 1))
            self.panel.setSummary(None)
            self.assertFalse(state['visible'])
            self.assertEqual(state['noActivate'], 1)  # hide: none
            self.panel.setSummary(summary())
            self.assertEqual((state['shown'], state['noActivate']), (2, 2))
        finally:
            P.overlayAllowed, P.platform.set_window_no_activate = originalAllowed, \
                originalNoActivate

    def test_saved_position_is_kept_on_screen(self):
        area = QApplication.primaryScreen().availableGeometry()
        for saved, expected in (
                ([99999, -99999], (area.x() + area.width() - P.WIDTH, area.y())),
                ([-99999, 99999], (area.x(), area.y() + area.height() - P.HEIGHT)),
                ([], (area.x() + 24, area.y() + 160)),
                (["x", None], (area.x() + 24, area.y() + 160))):
            with self.subTest(saved=saved):
                cfg.cooldownLedgerPos.value = saved
                self.panel.placeOnScreen()
                self.assertEqual((self.panel.x(), self.panel.y()), expected)
                self.assertTrue(area.contains(self.panel.geometry()))

    # ---------------- wording ----------------

    def test_no_tilde_and_no_estimate_wording(self):
        recorder = RecordingTranslator()
        self.app.installTranslator(recorder)
        try:
            panel = P.LedgerPanel(copy.deepcopy(DB), clock=self.clock)
            try:
                texts = [panel.windowTitle()]
                noTp = withEnemy(0, spells=("SummonerFlash", "SummonerDot"))
                panel.setSummary(summary(enemies=noTp))
                for cell in ('Q', 'W', 'E', 'R', 'D', 'F', 'TP'):
                    panel.clickCell(0, cell)
                panel.clickCell(2, 'item0')
                for row in range(5):
                    for key, _, _ in P.COLUMNS:
                        view = panel.cellView(row, key)
                        if view is not None:
                            texts.append(view['text'])
                self.render(panel)
                texts += [src for _, src in recorder.sources]
            finally:
                panel.disconnectSources()
                panel.deleteLater()
        finally:
            self.app.removeTranslator(recorder)
        self.assertIn("Cooldown ledger", texts)
        for text in texts:
            self.assertNotIn("~", text)
            self.assertNotIn("estimat", text.lower())

    # ---------------- renders ----------------

    def render(self, panel, scale=2):
        image = QImage(P.WIDTH * scale, P.HEIGHT * scale, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(scale)
        image.fill(QColor('#3A4A3C'))  # roughly the colour of the map under the panel
        p = QPainter(image)
        try:
            p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                             | QPainter.SmoothPixmapTransform)
            panel.drawContent(p)  # raises here instead of being logged by paintEvent
        finally:
            p.end()
        return image

    def renderStates(self, outDir, suffix):
        """Every cell state, each as one image: {name: QImage}"""
        async def icon(kind, iconId):
            path = os.path.join(ICON_DIRS[kind], f"{iconId}.png")
            if not os.path.exists(path):  # not cached on this machine: two-letter text
                raise KeyError(iconId)
            return path

        async def spell(i):
            return await icon('spell', i)

        async def item(i):
            return await icon('item', i)

        images = {}
        for withIcons in (False, True):
            clock = FakeClock()
            panel = P.LedgerPanel(copy.deepcopy(DB), spellIcon=spell if withIcons else None,
                                  itemIcon=item if withIcons else None, clock=clock)
            tag = "icons" if withIcons else "text"
            try:
                noTp = withEnemy(0, spells=("SummonerFlash", "SummonerDot"))
                inactive = [list(e) for e in noTp]
                inactive[1][1] = "Singed"  # Q cannot be estimated: drawn inactive
                inactive[1][2] = ("S5_SummonerSmitePlayerGanker", "SummonerFlash")
                inactive = [tuple(e) for e in inactive]

                async def load(s):
                    panel.setSummary(s)
                    if panel._tasks:
                        await asyncio.gather(*panel._tasks)

                asyncio.run(load(summary(enemies=inactive, gameTime=600.0)))
                images[f"{tag}_1_idle"] = self.render(panel)
                panel.clickCell(0, 'Q')                           # Garen Q: 120
                images[f"{tag}_2_q_120"] = self.render(panel)
                panel.setSummary(summary(enemies=inactive, gameTime=550.0))
                panel.timers.clear()
                panel.setSummary(summary(enemies=inactive, gameTime=550.0))
                panel.clickCell(0, 'D')                           # Flash 300 s -> 14:10
                images[f"{tag}_3_spell_14_10"] = self.render(panel)
                more = [list(e) for e in inactive]
                more[3][3] = (3140, 6631)
                more = [tuple(e) for e in more]
                asyncio.run(load(summary(enemies=more, gameTime=551.0)))
                images[f"{tag}_4_item_added"] = self.render(panel)
                panel.clickCell(2, 'item0')
                panel.clickCell(3, 'item1')
                panel.clickCell(0, 'TP')
                panel.clickCell(2, 'R')
                panel.clickCell(4, 'W')
                images[f"{tag}_5_many_running"] = self.render(panel)
                cfg.goldOverlayLocked.value = True
                panel.update()
                images[f"{tag}_6_locked"] = self.render(panel)
                cfg.goldOverlayLocked.value = False
            finally:
                panel.disconnectSources()
                panel.deleteLater()
        written = []
        if outDir:
            os.makedirs(outDir, exist_ok=True)
            for name, image in images.items():
                path = os.path.join(outDir, f"ledger_{suffix}_{name}.png")
                self.assertTrue(image.save(path), path)
                written.append(os.path.abspath(path))
        return images, written

    def test_renders(self):
        outDir = os.environ.get("SERAPHINE_RENDER_DIR") or None
        images, written = self.renderStates(outDir, "1_en")
        self.assertEqual(len(images), 12)
        for name, image in images.items():
            self.assertFalse(image.isNull(), name)
        translator = QTranslator()
        self.assertTrue(translator.load("Seraphine.zh_CN", I18N))
        self.app.installTranslator(translator)
        try:
            _, writtenZh = self.renderStates(outDir, "2_zh_CN")
        finally:
            self.app.removeTranslator(translator)
        if outDir is None:
            self.assertEqual(written + writtenZh, [])
        else:
            self.assertEqual(len(written + writtenZh), 24)
            for path in written + writtenZh:
                print(path)
