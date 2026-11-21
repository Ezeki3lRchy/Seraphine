"""Settings page: no push button is narrower than its content needs.

A fixed 100 px width clipped the label of the "Check now" button (the stylesheet's
horizontal padding makes its size hint far wider than 100 px). The page is built and laid
out on the offscreen platform (no window is shown on screen, no client or network needed),
in English and with the zh_CN translators installed the way main.py does.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, QTranslator
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton

from app.common.qfluentwidgets import FluentTranslator
from app.view.setting_interface import SettingInterface

CJK_FONT = r"C:\Windows\Fonts\msyh.ttc"


class SettingButtonsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.fontId = -1
        if os.path.exists(CJK_FONT):
            cls.fontId = QFontDatabase.addApplicationFont(CJK_FONT)

    @classmethod
    def tearDownClass(cls):
        # registration is process-wide: do not leave the font to later test modules
        if cls.fontId >= 0:
            QFontDatabase.removeApplicationFont(cls.fontId)

    def buildPage(self, locale=None):
        translators = []
        if locale is not None:
            fluent = FluentTranslator(locale)
            seraphine = QTranslator()
            self.assertTrue(seraphine.load(locale, "Seraphine", ".", "./app/resource/i18n"))
            translators = [fluent, seraphine]
        for t in translators:
            self.app.installTranslator(t)
        page = SettingInterface()
        for t in translators:
            self.app.removeTranslator(t)
        page.resize(1000, 800)
        self.assertEqual(self.app.platformName(), "offscreen")
        page.show()
        self.app.processEvents()
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close)
        self._translators = translators  # keep alive until the page is measured
        return page

    def assertButtonsFit(self, page):
        buttons = page.findChildren(QPushButton)
        self.assertGreater(len(buttons), 5)
        for b in buttons:
            with self.subTest(text=b.text(), owner=type(b.parent()).__name__):
                self.assertGreaterEqual(b.width(), b.sizeHint().width())
                self.assertGreaterEqual(b.width(), b.minimumSizeHint().width())

    def test_english(self):
        page = self.buildPage()
        self.assertEqual(page.manualCheckUpdateCard.button.text(), "Check now")
        self.assertButtonsFit(page)

    def test_chinese(self):
        page = self.buildPage(QLocale("zh_CN"))
        self.assertNotEqual(page.manualCheckUpdateCard.button.text(), "Check now")
        self.assertButtonsFit(page)

    def test_card_buttons_are_at_least_100_px_wide(self):
        page = self.buildPage()
        for card in (page.manualCheckUpdateCard, page.lolFolderCard,
                     page.viewLogCard, page.deleteResourceCard, page.feedbackCard):
            with self.subTest(card=card.titleLabel.text()):
                self.assertGreaterEqual(card.button.width(), 100)

    def test_check_now_button_still_fits_while_a_check_is_running(self):
        # The update check only disables the button; its text is unchanged
        page = self.buildPage()
        button = page.manualCheckUpdateCard.button
        button.setEnabled(False)
        self.app.processEvents()
        self.assertGreaterEqual(button.width(), button.sizeHint().width())


if __name__ == "__main__":
    unittest.main()
