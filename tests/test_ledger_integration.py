"""Cooldown ledger wiring in MainWindow (app/view/main_window.py) and its settings card.

MainWindow's ledger methods run on a stand-in object (as the other MainWindow tests do), with
the real bundled baseline, the real LedgerPanel and LedgerDatabase, a fake connector (icon
loaders, spell list) and a fake OP.GG client whose build fetch never resolves. Checks: the
panel is created with the connector's loaders and spell list and the database loads from the
bundled baseline; with the setting off nothing is shown and nothing is fetched; with it on a
game's enemies start one background refresh that never blocks and the panel works on the
baseline meanwhile; the settings switch shows / hides the panel; the panel's lock moves the
item value overlay's lock too; a missing baseline (LedgerDataError) means no panel and no
crash; shutdown disconnects the panel and cancels the refresh.
Offscreen only: no window is shown, no client or network is needed.
"""
import asyncio
import inspect
import os
import re
import shutil
import tempfile
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, Qt, QTranslator
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from app.common.config import cfg
from app.common.qfluentwidgets import FluentTranslator
from app.common.signals import signalBus
from app.components import ledger_panel as P
from app.components.gold_overlay import GoldOverlay
from app.lol import ledger_data as D
from app.lol.ledger_model import championEntry
from app.view import main_window as mw
from app.view.setting_interface import SettingInterface
from tests import test_ledger_panel as LP  # the module only: its tests are not collected here

MW = mw.MainWindow
FONT_DIR = r"C:\Windows\Fonts"
FONT_FILES = ("segoeui.ttf", "msyh.ttc")
_fontIds = []


def setUpModule():
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "offscreen":
        raise RuntimeError("run the ledger integration tests with QT_QPA_PLATFORM=offscreen")
    for name in FONT_FILES:  # the offscreen platform has no system fonts on Windows
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path):
            fontId = QFontDatabase.addApplicationFont(path)
            if fontId >= 0:
                _fontIds.append(fontId)


def tearDownModule():
    while _fontIds:
        QFontDatabase.removeApplicationFont(_fontIds.pop())


class FakeConnector:
    def __init__(self):
        self.manager = types.SimpleNamespace(spells={4: "flash.png", 14: "ignite.png"})
        self.requested = []

    async def getSummonerSpellIcon(self, spellId):
        self.requested.append(('spell', spellId))
        raise KeyError(spellId)  # the cell keeps its two-letter text

    async def getItemIcon(self, itemId):
        self.requested.append(('item', itemId))
        raise KeyError(itemId)


class NeverOpgg:
    """OP.GG client whose build fetch never resolves (a hung connection)"""

    def __init__(self, session=True):
        self.session = types.SimpleNamespace(closed=False) if session else None
        self.calls = []
        self.started = asyncio.Event()

    async def getChampionBuild(self, region, mode, championId, position, tier):
        self.calls.append((region, mode, championId, position, tier))
        self.started.set()
        await asyncio.Event().wait()


def standIn():
    """The MainWindow attributes the ledger methods use, on a plain object"""
    fake = types.SimpleNamespace(_ledgerTasks=set(), ledgerDb=None, ledgerPanel=None)
    fake._MainWindow__onLedgerEnemiesSeen = lambda enemies: MW._MainWindow__onLedgerEnemiesSeen(
        fake, enemies)
    fake._MainWindow__cancelLedgerRefresh = lambda: MW._MainWindow__cancelLedgerRefresh(fake)
    return fake


class LedgerWiringTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.saved = {item: item.value for item in
                      (cfg.enableCooldownLedger, cfg.goldOverlayLocked, cfg.cooldownLedgerPos)}
        cfg.enableCooldownLedger.value = False
        cfg.goldOverlayLocked.value = False
        # a fresh config dir: no ledger cache, so every enemy is fetched
        self.cfgDir = tempfile.mkdtemp(prefix='ledger-integration-')
        self.addCleanup(shutil.rmtree, self.cfgDir, ignore_errors=True)
        env = mock.patch.dict(os.environ, {'SERAPHINE_CONFIG_DIR': self.cfgDir})
        env.start()
        self.addCleanup(env.stop)
        self.connector = FakeConnector()
        self.opgg = NeverOpgg()
        for name, value in (('connector', self.connector), ('opgg', self.opgg)):
            patcher = mock.patch.object(mw, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fake = standIn()

    async def asyncTearDown(self):
        if self.fake.ledgerPanel is not None:
            if not self.fake.ledgerPanel._closed:  # not closed by the test itself
                await MW._MainWindow__closeLedger(self.fake)
            self.fake.ledgerPanel.deleteLater()
        for item, value in self.saved.items():
            item.value = value

    def create(self):
        with self.assertLogs("Seraphine", "INFO") as logs:
            self.fake.ledgerDb, self.fake.ledgerPanel = MW._MainWindow__createLedger(self.fake)
        return [r.getMessage() for r in logs.records]

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)

    # ---------------- creation ----------------

    def test_main_window_creates_and_closes_the_ledger(self):
        init = inspect.getsource(MW.__init__)
        self.assertIn("self.ledgerDb, self.ledgerPanel = self.__createLedger()", init)
        self.assertLess(init.index("GoldOverlay()"), init.index("__createLedger()"))
        close = inspect.getsource(MW.closeEvent)
        self.assertLess(close.index("self.goldOverlay.close()"),
                        close.index("await self.__closeLedger()"))
        # the client ended: the refresh stops before its OP.GG session is closed
        ended = inspect.getsource(MW._MainWindow__onLolClientEnded)
        self.assertLess(ended.index("await self.__cancelLedgerRefresh()"),
                        ended.index("await opgg.close()"))

    def test_panel_gets_the_connector_loaders_and_the_bundled_database(self):
        lines = self.create()
        db, panel = self.fake.ledgerDb, self.fake.ledgerPanel
        self.assertIsInstance(db, D.LedgerDatabase)
        self.assertIsInstance(panel, P.LedgerPanel)
        self.assertIs(panel.db, db)
        self.assertEqual(db.baselinePath, D.BASELINE_PATH)
        self.assertGreater(len(db.data['champions']), 150)
        # the connector's loaders and spell list
        self.assertEqual(panel._spellIcon, self.connector.getSummonerSpellIcon)
        self.assertEqual(panel._itemIcon, self.connector.getItemIcon)
        self.assertTrue(panel._spellKnown(4))
        self.assertFalse(panel._spellKnown(2201))
        self.connector.manager = None  # before the client connects: not known, no exception
        with self.assertRaises(AttributeError):
            panel._spellKnown(4)  # the panel catches this and keeps the two-letter text
        # one start-up line with the patches and the champion count, no path, nothing fetched
        ledgerLines = [m for m in lines if m.startswith("ledger db:")]
        self.assertEqual(len(ledgerLines), 1)
        self.assertRegex(ledgerLines[0], r"^ledger db: baseline \d+\.\d+, cache none, \d+ champions$")
        self.assertTrue(db.cachePath.startswith(self.cfgDir))
        self.assertIn(f"{len(db.data['champions'])} champions", ledgerLines[0])
        self.assertEqual(self.opgg.calls, [])
        self.assertEqual(self.fake._ledgerTasks, set())

    async def test_missing_baseline_means_no_panel_and_no_crash(self):
        missing = os.path.join(tempfile.mkdtemp(), "ledger_baseline.json")
        with mock.patch.object(mw, 'LedgerDatabase',
                               lambda: D.LedgerDatabase(baselinePath=missing)):
            with self.assertLogs("Seraphine", "ERROR") as logs:
                db, panel = MW._MainWindow__createLedger(self.fake)
        self.assertIsNone(db)
        self.assertIsNone(panel)
        messages = [r.getMessage() for r in logs.records]
        self.assertTrue(any("LedgerDataError" in m for m in messages), messages)
        self.assertFalse(any(missing in m for m in messages), messages)  # type name only
        # the rest of the wiring copes with no ledger
        cfg.enableCooldownLedger.value = True
        MW._MainWindow__onLedgerEnemiesSeen(self.fake, [{'championName': 'Garen',
                                                         'position': 'TOP'}])
        self.assertEqual(self.fake._ledgerTasks, set())
        await MW._MainWindow__closeLedger(self.fake)

    def test_any_other_load_error_means_no_panel_and_no_crash(self):
        # MainWindow builds the ledger before the error policy is armed: nothing may escape
        with mock.patch.object(D.LedgerDatabase, 'load', side_effect=ValueError("secret detail")):
            with self.assertLogs("Seraphine", "ERROR") as logs:
                db, panel = MW._MainWindow__createLedger(self.fake)
        self.assertIsNone(db)
        self.assertIsNone(panel)
        messages = [r.getMessage() for r in logs.records]
        self.assertTrue(any("ValueError" in m for m in messages), messages)
        self.assertFalse(any("secret detail" in m for m in messages), messages)

    # ---------------- setting off / on ----------------

    async def test_setting_off_shows_nothing_and_fetches_nothing(self):
        self.create()
        panel = self.fake.ledgerPanel
        signalBus.liveGameDataChanged.emit(LP.summary())
        await self.settle()
        self.assertFalse(panel.wanted)
        self.assertEqual(panel.hiddenReason, "setting off")
        self.assertFalse(panel.isVisible())
        # even if the enemies reached the slot, nothing is fetched while the setting is off
        MW._MainWindow__onLedgerEnemiesSeen(self.fake, [{'championName': 'Garen',
                                                         'position': 'TOP'}])
        await self.settle()
        self.assertEqual(self.opgg.calls, [])
        self.assertEqual(self.fake._ledgerTasks, set())
        self.assertEqual(self.connector.requested, [])

    async def test_refresh_runs_in_the_background_and_never_blocks(self):
        self.create()
        db, panel = self.fake.ledgerDb, self.fake.ledgerPanel
        cfg.enableCooldownLedger.value = True
        signalBus.liveGameDataChanged.emit(LP.summary())  # returns: nothing waits for OP.GG
        self.assertTrue(panel.wanted)
        self.assertEqual(len(self.fake._ledgerTasks), 1)
        task = next(iter(self.fake._ledgerTasks))
        await asyncio.wait_for(self.opgg.started.wait(), 5)
        self.assertFalse(task.done())
        self.assertEqual(sorted(c[2] for c in self.opgg.calls),
                         sorted(championEntry(db.baseline, n)['championId']
                                for n in ("Garen", "LeeSin", "Ahri", "Jinx", "Thresh")))
        self.assertEqual({c[1] for c in self.opgg.calls}, {'ranked'})
        # meanwhile the panel works on the bundled baseline: Garen's R at level 7 = rank 1
        top = panel.rows['TOP']
        row = championEntry(db.baseline, 'Garen')
        rank = sum(1 for level in row['rLevels'] if top['level'] >= level)
        self.assertEqual(panel.duration(top, 'R'), row['spells']['R']['cooldown'][rank - 1])
        panel.clickCell(0, 'R')
        self.assertEqual(panel.timers.state(top['key'], 'R')[0], 'running')
        # the same enemies again: no second refresh
        signalBus.liveGameDataChanged.emit(LP.summary(gameTime=605.0))
        self.assertEqual(len(self.fake._ledgerTasks), 1)
        # shutdown cancels the hung refresh and returns only once it has ended
        await MW._MainWindow__closeLedger(self.fake)
        self.assertTrue(task.cancelled())
        await self.settle()
        self.assertEqual(self.fake._ledgerTasks, set())

    async def test_no_opgg_session_no_refresh(self):
        self.create()
        cfg.enableCooldownLedger.value = True
        for session in (None, types.SimpleNamespace(closed=True)):
            with self.subTest(session=session):
                self.opgg.session = session
                MW._MainWindow__onLedgerEnemiesSeen(self.fake, [{'championName': 'Garen',
                                                                 'position': 'TOP'}])
                await self.settle()
                self.assertEqual(self.fake._ledgerTasks, set())
                self.assertEqual(self.opgg.calls, [])
        # nothing was marked as tried: a later game with a session still fetches Garen
        self.assertEqual(self.fake.ledgerDb._attempted, set())

    # ---------------- shutdown ----------------

    async def test_shutdown_disconnects_the_panel(self):
        self.create()
        panel = self.fake.ledgerPanel
        cfg.enableCooldownLedger.value = True
        signalBus.liveGameDataChanged.emit(LP.summary())
        self.assertTrue(panel.wanted)
        await MW._MainWindow__closeLedger(self.fake)
        before = panel.summary
        signalBus.liveGameDataChanged.emit(LP.summary(gameMode="ARAM", mapNumber=12))
        self.assertIs(panel.summary, before)  # no longer follows the live data
        cfg.set(cfg.enableCooldownLedger, False)
        self.assertTrue(panel.wanted)  # nor the setting
        self.assertTrue(panel._closed)
        self.assertFalse(panel.ticker.isActive())


class LedgerSettingsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.overlay = GoldOverlay()  # kept for the module, as tests/test_gold_overlay.py does

    def setUp(self):
        self.saved = {item: item.value for item in
                      (cfg.enableCooldownLedger, cfg.goldOverlayLocked)}
        cfg.enableCooldownLedger.value = False
        cfg.goldOverlayLocked.value = False

    def tearDown(self):
        for item, value in self.saved.items():
            item.value = value

    def buildPage(self, locale=None):
        translators = []
        if locale is not None:
            seraphine = QTranslator()
            self.assertTrue(seraphine.load(locale, "Seraphine", ".", "./app/resource/i18n"))
            translators = [FluentTranslator(locale), seraphine]
        for t in translators:
            self.app.installTranslator(t)
        page = SettingInterface()
        for t in translators:
            self.app.removeTranslator(t)
        page.resize(1000, 800)  # narrower than the main window's minimum width allows
        page.show()
        self.app.processEvents()
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close)
        return page

    def panel(self):
        panel = P.LedgerPanel(D.LedgerDatabase().load())
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.disconnectSources)
        return panel

    def test_card_follows_the_overlay_cards_and_the_setting(self):
        page = self.buildPage()
        card = page.cooldownLedgerCard
        self.assertIs(card.configItem, cfg.enableCooldownLedger)
        self.assertEqual(card.titleLabel.text(), "Cooldown ledger")
        group = page.functionGroup
        order = sorted((w for w in group.findChildren(QWidget)
                        if w.parent() is group and w.isVisibleTo(group)
                        and hasattr(w, 'titleLabel')), key=lambda w: w.y())
        self.assertEqual(order.index(card), order.index(page.goldOverlayLockCard) + 1)
        self.assertGreaterEqual(card.y(), page.goldOverlayLockCard.geometry().bottom())
        self.assertFalse(card.switchButton.isChecked())  # off by default
        text = card.contentLabel.text()
        for phrase in ("Summoner's Rift", "one row per enemy", "ability, summoner spell or item",
                       "usual cooldown", "You start every timer yourself", "nothing is detected",
                       "approximate", "items you last saw"):
            self.assertIn(phrase, text)

    def test_switch_shows_and_hides_the_panel(self):
        page = self.buildPage()
        panel = self.panel()
        panel.setSummary(LP.summary())
        self.assertFalse(panel.wanted)
        self.assertEqual(panel.hiddenReason, "setting off")
        page.cooldownLedgerCard.switchButton.setChecked(True)
        self.assertTrue(cfg.get(cfg.enableCooldownLedger))
        self.assertTrue(panel.wanted)
        self.assertIsNone(panel.hiddenReason)
        page.cooldownLedgerCard.switchButton.setChecked(False)
        self.assertFalse(panel.wanted)
        self.assertEqual(panel.hiddenReason, "setting off")

    def test_panel_lock_is_the_overlay_lock(self):
        page = self.buildPage()
        panel = self.panel()
        cfg.enableCooldownLedger.value = True
        panel.setSummary(LP.summary())
        for locked in (True, False):
            with self.subTest(locked=locked):
                panel.clickCell(P.LOCK_ROW, P.LOCK_COLUMN)
                self.assertIs(cfg.get(cfg.goldOverlayLocked), locked)
                # the item value overlay follows: mouse-transparent exactly while locked
                self.assertEqual(self.overlay.testAttribute(Qt.WA_TransparentForMouseEvents),
                                 locked)
                self.assertEqual(page.goldOverlayLockCard.switchButton.isChecked(), locked)
                # the ledger itself is never click-through
                self.assertFalse(panel.windowFlags() & Qt.WindowTransparentForInput)

    def assertDescriptionFits(self, page):
        card = page.cooldownLedgerCard
        label = card.contentLabel
        hint = label.sizeHint()
        self.assertGreaterEqual(label.width(), hint.width(), label.text())
        self.assertGreaterEqual(label.height(), hint.height(), label.text())
        self.assertEqual(label.text().count("\n"), 1)  # two lines
        self.assertLessEqual(card.titleLabel.geometry().bottom(), label.geometry().top())
        # the card grew with its second line: the same room above and below the text as the
        # one-line cards have (18 px on the item value overlay card), not squeezed to the edge
        self.assertGreaterEqual(card.titleLabel.y(), 14)
        self.assertGreaterEqual(card.height() - 1 - label.geometry().bottom(), 14)

    def test_description_fits_in_english(self):
        self.assertDescriptionFits(self.buildPage())

    def test_description_fits_in_chinese(self):
        page = self.buildPage(QLocale("zh_CN"))
        self.assertEqual(page.cooldownLedgerCard.titleLabel.text(), "冷却账本")
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", page.cooldownLedgerCard.contentLabel.text()))
        self.assertDescriptionFits(page)


if __name__ == "__main__":
    unittest.main()
