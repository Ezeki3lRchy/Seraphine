"""Saved players page and star button, built on the offscreen platform (no window on screen,
no client): rows follow the store, the filter hides rows and turns off reordering, notes
and removals are stored, a drag release is not a click, and rows only open with a client."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from app.common.signals import signalBus
from app.components.save_star_button import SaveStarButton
from app.lol import saved_players as sp
from app.view import saved_players_interface as spi


def destroy(widget):
    """delete now: a page left for deleteLater stays alive (no event loop runs here) and
    rebuilds, even loads icons, on later tests' savedPlayersChanged"""
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class SavedPlayersPageTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.mkdtemp(prefix='seraphine-saved-page-')
        for name, value in (('storePath', os.path.join(folder, 'saved_players.json')),
                            ('_data', None), ('_dataPath', None)):
            p = mock.patch.object(sp, name, value)
            p.start()
            self.addCleanup(p.stop)
        sp.add('a', 'Alpha#NA1', None, rank=('GOLD', 'II'), now=100)
        sp.add('b', 'Bravo#NA1', None, now=200)
        sp.setNote('a', 'duo')
        self.page = spi.SavedPlayersInterface()
        self.addCleanup(destroy, self.page)
        self.opened = []
        signalBus.toCareerInterface.connect(self.opened.append)
        self.addCleanup(signalBus.toCareerInterface.disconnect, self.opened.append)

    def puuids(self):
        return [row.puuid for row in self.page.rows()]

    def test_rows_follow_the_store(self):
        self.assertEqual(self.puuids(), ['b', 'a'])
        self.assertEqual(self.page.countLabel.text(), f"2 / {sp.MAX_PLAYERS}")
        rowA = self.page.rows()[1]
        self.assertEqual(rowA.noteButton.text(), 'duo')
        self.assertEqual(rowA.rankLabel.text(), 'Gold II')
        # saved from Search: rank not known yet
        self.assertEqual(self.page.rows()[0].rankLabel.text(), '--')
        sp.refresh('b', 'Bravo#NA1', None, ())
        signalBus.savedPlayersChanged.emit()
        self.assertEqual(self.page.rows()[0].rankLabel.text(), 'Unranked')

    def test_filter_hides_rows_and_stops_reordering(self):
        self.page.filterEdit.setText('DUO')
        self.assertEqual(self.puuids(), ['a'])
        self.assertFalse(self.page.list.reorderEnabled)
        self.page.filterEdit.setText('nobody')
        self.assertEqual(self.puuids(), [])
        self.assertFalse(self.page.emptyLabel.isHidden())
        self.page.filterEdit.setText('')
        self.assertTrue(self.page.list.reorderEnabled)
        self.assertEqual(self.puuids(), ['b', 'a'])

    def test_remove_and_note_are_stored(self):
        rowB = self.page.rows()[0]
        rowB.noteChanged.emit('b', 'smurf?')
        self.assertEqual(sp.entries()[0]['note'], 'smurf?')
        self.page.rows()[1].removeButton.click()
        self.assertEqual([e['puuid'] for e in sp.entries()], ['b'])
        self.assertEqual(self.puuids(), ['b'])

    def test_drag_order_is_stored(self):
        self.page.list.orderChanged.emit(['a', 'b'])
        self.assertEqual([e['puuid'] for e in sp.entries()], ['a', 'b'])

    def test_row_opens_career_only_with_a_client(self):
        row = self.page.rows()[0]
        row.openRequested.emit(row.puuid)
        self.assertEqual(self.opened, [])
        self.page.setClientConnected(True)
        row.openRequested.emit(row.puuid)
        self.assertEqual(self.opened, ['b'])

    def mouse(self, widget, kind, y, buttons, button=Qt.LeftButton, x=20):
        local = QPointF(x, y)
        QApplication.sendEvent(widget, QMouseEvent(
            kind, local, QPointF(widget.mapToGlobal(local.toPoint())),
            button, buttons, Qt.NoModifier))

    def showPage(self):
        self.page.resize(900, 400)
        self.page.show()
        QApplication.processEvents()     # lay the rows out before dragging
        self.page.setClientConnected(True)

    def test_small_wobble_is_still_a_click(self):
        self.showPage()
        label = self.page.rows()[0].nameLabel
        self.mouse(label, QEvent.MouseButtonPress, 10, Qt.LeftButton)
        self.mouse(label, QEvent.MouseMove, 11, Qt.LeftButton)
        self.mouse(label, QEvent.MouseButtonRelease, 11, Qt.NoButton)
        self.assertEqual(self.opened, ['b'])

    def test_right_click_does_not_open(self):
        self.showPage()
        label = self.page.rows()[0].nameLabel
        self.mouse(label, QEvent.MouseButtonPress, 10, Qt.RightButton, Qt.RightButton)
        self.mouse(label, QEvent.MouseButtonRelease, 10, Qt.NoButton, Qt.RightButton)
        self.assertEqual(self.opened, [])

    def test_press_outside_a_row_after_a_click_drags_nothing(self):
        """a click used to leave its row picked, so a later press in the gap between
        rows dragged that row (or an index past the end after a removal)"""
        self.showPage()
        last = self.page.rows()[-1]
        self.mouse(last.nameLabel, QEvent.MouseButtonPress, 10, Qt.LeftButton)
        self.mouse(last.nameLabel, QEvent.MouseButtonRelease, 10, Qt.NoButton)
        self.page.rows()[0].removeButton.click()
        QApplication.processEvents()
        lst = self.page.list
        self.assertEqual(lst.getCurrentIndex(), -1)
        self.mouse(lst, QEvent.MouseButtonPress, 2, Qt.LeftButton)
        for y in range(5, 60, 5):
            self.mouse(lst, QEvent.MouseMove, y, Qt.LeftButton)
        self.mouse(lst, QEvent.MouseButtonRelease, 60, Qt.NoButton)
        self.assertFalse(lst.isDragging)
        self.assertEqual([e['puuid'] for e in sp.entries()], ['a'])

    def test_note_being_typed_is_kept_when_the_page_hides(self):
        self.showPage()
        row = self.page.rows()[0]
        row.noteButton.click()
        row.noteEdit.setText('premade')
        self.page.hide()
        self.assertEqual(sp.entries()[0]['note'], 'premade')

    def test_note_being_typed_is_kept_across_a_rebuild(self):
        row = self.page.rows()[0]
        row.noteButton.click()
        row.noteEdit.setText('premade')
        signalBus.savedPlayersChanged.emit()
        self.assertEqual(sp.entries()[0]['note'], 'premade')
        self.assertEqual(self.page.rows()[0].noteButton.text(), 'premade')

    def test_mouse_drag_reorders_and_is_not_a_click(self):
        """press on a row's name, move down past the next row, release: the order changes
        on screen and on disk, and nothing opens (the drag widget used sender(), which is
        None under PySide6, so no row was ever picked up)"""
        self.showPage()
        label = self.page.rows()[0].nameLabel
        self.mouse(label, QEvent.MouseButtonPress, 10, Qt.LeftButton)
        for y in range(15, 100, 5):
            self.mouse(label, QEvent.MouseMove, y, Qt.LeftButton)
        self.mouse(label, QEvent.MouseButtonRelease, 100, Qt.NoButton)
        self.assertEqual(self.puuids(), ['a', 'b'])
        self.assertEqual([e['puuid'] for e in sp.entries()], ['a', 'b'])
        self.assertEqual(self.opened, [])

        # a press and release without moving is a click
        label = self.page.rows()[0].nameLabel
        self.mouse(label, QEvent.MouseButtonPress, 10, Qt.LeftButton)
        self.mouse(label, QEvent.MouseButtonRelease, 10, Qt.NoButton)
        self.assertEqual(self.opened, ['a'])

    def test_release_after_a_drag_is_not_a_click(self):
        self.page.setClientConnected(True)
        self.page.list.isDragging = True
        row = self.page.rows()[0]
        row.openRequested.emit(row.puuid)
        self.page.list.isDragging = False
        self.assertEqual(self.opened, [])

    def test_star_button_saves_and_removes(self):
        star = SaveStarButton()
        self.addCleanup(destroy, star)
        self.assertFalse(star.isEnabled())
        star.setPlayer('c', 'Charlie#EUW', 12, ('SILVER', 'I'))
        self.assertTrue(star.isEnabled())
        star.click()
        self.assertTrue(sp.isSaved('c'))
        self.assertEqual(self.puuids()[0], 'c')      # the page followed the signal
        star.click()
        self.assertFalse(sp.isSaved('c'))
        self.assertNotIn('c', self.puuids())

    def test_star_button_on_a_full_list_saves_nothing(self):
        star = SaveStarButton()
        self.addCleanup(destroy, star)
        star.setPlayer('new', 'New#1', 1)
        with mock.patch.object(sp, 'MAX_PLAYERS', 2):
            star.click()
        self.assertFalse(sp.isSaved('new'))


if __name__ == '__main__':
    unittest.main()
