"""Saved players page (sidebar): the players the user starred on the Career or Search page.

Click a row to open that player's Career page (needs the LoL client), drag rows to reorder,
click the tag to edit the note, x to remove. The list itself is local and works without the
client (app/lol/saved_players.py).
"""
import asyncio
import os

from PySide6.QtCore import Qt, Signal, QSize, QCoreApplication
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget

from app.common.qfluentwidgets import (SmoothScrollArea, LineEdit, TransparentToolButton,
                                       TransparentPushButton, IconWidget, FluentIcon,
                                       InfoBar, InfoBarPosition, ToolTipFilter,
                                       ToolTipPosition)
from app.common.signals import signalBus
from app.common.style_sheet import StyleSheet
from app.components.career_widgets import timeAgo
from app.components.champion_icon_widget import RoundIcon
from app.components.draggable_widget import DraggableItem, ItemsDraggableWidget
from app.components.match_widgets import tierText
from app.components.seraphine_interface import SeraphineInterface
from app.lol import saved_players
from app.lol.connector import connector

DEFAULT_ICON = 'app/resource/images/champion-0.png'
ROW_HEIGHT = 64


def profileIconPath(iconId):
    """The profile icon file the connector caches, or None when it is not on disk"""
    if iconId is None:
        return None
    path = f"app/resource/game/profile icons/{iconId}.jpg"
    # the connector creates the file before writing it: an empty one is still loading
    return path if os.path.exists(path) and os.path.getsize(path) > 0 else None


def rankText(entry):
    if entry.get('unreachable') or entry.get('rank') is None:
        return '--'
    rank = entry['rank']
    return (tierText(rank[0], rank[1]) if rank else "") or \
        QCoreApplication.translate("SavedPlayerRow", "Unranked")


class SavedPlayerRow(DraggableItem):
    openRequested = Signal(str)
    removeRequested = Signal(str)
    noteChanged = Signal(str, str)

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.puuid = entry['puuid']
        unreachable = bool(entry.get('unreachable'))

        self.hBoxLayout = QHBoxLayout(self)
        self.handle = IconWidget(FluentIcon.MENU)
        self.icon = RoundIcon(profileIconPath(entry.get('icon')) or DEFAULT_ICON, 40, 4, 2)
        self.textLayout = QVBoxLayout()
        name = entry.get('name') or '?'
        if unreachable:
            name = self.tr("%1 (cached name)").replace("%1", name)
        self.nameLabel = QLabel(name)
        if unreachable:
            sub = self.tr("Couldn't load from this server")
        else:
            sub = self.tr("Saved %1").replace("%1", timeAgo(entry.get('added', 0) * 1000))
        self.subLabel = QLabel(sub)
        self.noteButton = TransparentPushButton(FluentIcon.TAG, '')
        self.noteEdit = LineEdit()
        self.editing = False
        self.rankLabel = QLabel(rankText(entry))
        self.removeButton = TransparentToolButton(FluentIcon.CLOSE)

        self.__initWidget(unreachable)
        self.__initLayout()

    def __initWidget(self, unreachable):
        self.setFixedHeight(ROW_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)
        self.setCursor(Qt.PointingHandCursor)

        self.handle.setFixedSize(16, 16)
        self.handle.setToolTip(self.tr("Drag to reorder"))
        self.handle.installEventFilter(ToolTipFilter(self.handle, 500, ToolTipPosition.TOP))
        self.nameLabel.setObjectName("savedNameLabel")
        self.subLabel.setObjectName("savedSubLabel")
        self.subLabel.setProperty("unreachable", unreachable)
        self.rankLabel.setObjectName("savedRankLabel")
        # a minimum, not a fixed width: macOS fonts are wider than Windows' (no clipping)
        self.rankLabel.setMinimumWidth(120)
        self.rankLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.__showNote()
        self.noteButton.setToolTip(self.tr("Edit note"))
        self.noteButton.installEventFilter(
            ToolTipFilter(self.noteButton, 500, ToolTipPosition.TOP))
        self.noteButton.clicked.connect(self.__startEditNote)
        self.noteEdit.setMaxLength(saved_players.MAX_NOTE)
        self.noteEdit.setPlaceholderText(self.tr("duo, smurf?, toxic..."))
        self.noteEdit.setMinimumWidth(160)
        self.noteEdit.setVisible(False)
        self.noteEdit.editingFinished.connect(self.commitNote)

        self.removeButton.setIconSize(QSize(12, 12))
        self.removeButton.setFixedSize(QSize(30, 30))
        self.removeButton.setToolTip(self.tr("Remove from saved players"))
        self.removeButton.installEventFilter(
            ToolTipFilter(self.removeButton, 500, ToolTipPosition.TOP))
        self.removeButton.clicked.connect(lambda: self.removeRequested.emit(self.puuid))

    def __initLayout(self):
        self.hBoxLayout.setContentsMargins(12, 8, 10, 8)
        self.hBoxLayout.setSpacing(12)
        self.hBoxLayout.addWidget(self.handle)
        self.hBoxLayout.addWidget(self.icon)

        self.textLayout.setContentsMargins(0, 0, 0, 0)
        self.textLayout.setSpacing(2)
        self.textLayout.addWidget(self.nameLabel)
        self.textLayout.addWidget(self.subLabel)
        self.hBoxLayout.addLayout(self.textLayout)
        self.hBoxLayout.addStretch(1)

        self.hBoxLayout.addWidget(self.noteButton)
        self.hBoxLayout.addWidget(self.noteEdit)
        self.hBoxLayout.addWidget(self.rankLabel)
        self.hBoxLayout.addWidget(self.removeButton)

    def sizeHint(self):
        return QSize(700, ROW_HEIGHT)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        # only a left click opens (the list decides whether the release ended a drag)
        if e.button() == Qt.LeftButton and not self.editing:
            self.openRequested.emit(self.puuid)

    def hideEvent(self, e):
        # leaving the page while typing keeps the note
        self.commitNote()
        super().hideEvent(e)

    def setIcon(self, path):
        self.icon.image = QPixmap(path)
        self.icon.havePic = True
        self.icon.update()

    def __showNote(self):
        note = self.entry.get('note', '')
        self.noteButton.setText(note or self.tr("Add note"))

    def __startEditNote(self):
        self.editing = True
        self.noteButton.setVisible(False)
        self.noteEdit.setText(self.entry.get('note', ''))
        self.noteEdit.setVisible(True)
        self.noteEdit.setFocus()
        self.noteEdit.selectAll()

    def commitNote(self):
        """Store the note being edited (editingFinished comes on Return and again on focus
        loss, and the page may also commit before a rebuild: act once)"""
        if not self.editing:
            return
        self.editing = False
        note = self.noteEdit.text().strip()
        self.noteEdit.setVisible(False)
        self.noteButton.setVisible(True)
        if note != self.entry.get('note', ''):
            self.entry['note'] = note
            self.noteChanged.emit(self.puuid, note)
        self.__showNote()


class SavedPlayersList(ItemsDraggableWidget):
    orderChanged = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.reorderEnabled = True
        # rows fill the width (the base centres fixed-width items)
        self.vBoxLayout.setAlignment(Qt.AlignTop)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        StyleSheet.SAVED_PLAYERS_INTERFACE.apply(self)

    def addRow(self, row: SavedPlayerRow):
        self._addItem(row)

    def clearRows(self):
        for row in list(self.items):
            self._removeItem(row)

    def wasDragging(self):
        return self.isDragging

    def mouseMoveEvent(self, e):
        if self.reorderEnabled:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        dragged = self.isDragging
        super().mouseReleaseEvent(e)
        if dragged:
            self.orderChanged.emit([row.puuid for row in self.items])


class SavedPlayersInterface(SeraphineInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.clientConnected = False
        self.iconTasks = []

        self.vBoxLayout = QVBoxLayout(self)
        self.headerLayout = QHBoxLayout()
        self.titleLabel = QLabel(self.tr("Saved players"))
        self.countLabel = QLabel()
        self.filterEdit = LineEdit()

        self.scrollArea = SmoothScrollArea()
        self.scrollWidget = QWidget()
        self.scrollLayout = QVBoxLayout(self.scrollWidget)
        self.list = SavedPlayersList()
        self.emptyLabel = QLabel()
        self.hintLabel = QLabel(self.tr(
            "Click a row to open their career · drag to reorder · click the tag to edit the note"))

        self.__initWidget()
        self.__initLayout()

        self.filterEdit.textChanged.connect(self.rebuild)
        self.list.orderChanged.connect(saved_players.reorder)
        signalBus.savedPlayersChanged.connect(self.rebuild)
        self.rebuild()

    def __initWidget(self):
        self.titleLabel.setObjectName("savedTitleLabel")
        self.countLabel.setObjectName("savedCountLabel")
        self.hintLabel.setObjectName("savedHintLabel")
        self.emptyLabel.setObjectName("savedEmptyLabel")
        self.emptyLabel.setAlignment(Qt.AlignCenter)
        self.emptyLabel.setWordWrap(True)

        self.filterEdit.setPlaceholderText(self.tr("Filter by name or note"))
        self.filterEdit.setClearButtonEnabled(True)
        self.filterEdit.setMinimumWidth(260)

        self.scrollArea.setObjectName("savedScrollArea")
        self.scrollWidget.setObjectName("savedScrollWidget")
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        StyleSheet.SAVED_PLAYERS_INTERFACE.apply(self)

    def __initLayout(self):
        self.headerLayout.setContentsMargins(0, 0, 0, 0)
        self.headerLayout.setSpacing(12)
        self.headerLayout.addWidget(self.titleLabel, alignment=Qt.AlignVCenter)
        self.headerLayout.addWidget(self.countLabel, alignment=Qt.AlignBottom)
        self.headerLayout.addStretch(1)
        self.headerLayout.addWidget(self.filterEdit, alignment=Qt.AlignVCenter)

        self.scrollLayout.setContentsMargins(0, 0, 8, 0)
        self.scrollLayout.setSpacing(0)
        self.scrollLayout.addWidget(self.list)
        self.scrollLayout.addWidget(self.emptyLabel)
        self.scrollLayout.addStretch(1)

        self.vBoxLayout.setContentsMargins(30, 32, 30, 20)
        self.vBoxLayout.setSpacing(12)
        self.vBoxLayout.addLayout(self.headerLayout)
        self.vBoxLayout.addWidget(self.scrollArea, 1)
        self.vBoxLayout.addWidget(self.hintLabel)

    def setClientConnected(self, connected):
        self.clientConnected = connected
        if connected:
            self.__loadMissingIcons()

    def rebuild(self):
        for row in self.rows():
            row.commitNote()
        self.__cancelIconTasks()
        self.list.clearRows()

        allEntries = saved_players.entries()
        text = self.filterEdit.text()
        shown = [entry for entry in allEntries if saved_players.matches(entry, text)]
        # reordering a filtered list would mix up the hidden rows' positions
        self.list.reorderEnabled = not text.strip()

        for entry in shown:
            row = SavedPlayerRow(entry)
            row.openRequested.connect(self.__onOpenRequested)
            row.removeRequested.connect(self.__onRemoveRequested)
            row.noteChanged.connect(saved_players.setNote)
            self.list.addRow(row)

        self.countLabel.setText(f"{len(allEntries)} / {saved_players.MAX_PLAYERS}")
        if not allEntries:
            self.emptyLabel.setText(self.tr(
                "No saved players yet. Use the star next to a player's name on the Career or "
                "Search page to save them."))
        elif not shown:
            self.emptyLabel.setText(self.tr("No saved player matches the filter."))
        self.emptyLabel.setVisible(not shown)
        self.list.setVisible(bool(shown))
        self.hintLabel.setVisible(bool(shown))

        if self.clientConnected:
            self.__loadMissingIcons()

    def rows(self):
        return list(self.list.items)

    def __onOpenRequested(self, puuid):
        # the release that ends a drag also reaches the row: that is not a click
        if self.list.wasDragging():
            return
        if not self.clientConnected:
            InfoBar.warning(
                self.tr("LoL client not connected"),
                self.tr("Start the LoL client to open a saved player's career."),
                orient=Qt.Vertical,
                position=InfoBarPosition.BOTTOM_RIGHT,
                duration=5000,
                parent=self.window())
            return
        signalBus.toCareerInterface.emit(puuid)

    def __onRemoveRequested(self, puuid):
        saved_players.remove(puuid)
        signalBus.savedPlayersChanged.emit()

    def __cancelIconTasks(self):
        for task in self.iconTasks:
            task.cancel()
        self.iconTasks = []

    def __loadMissingIcons(self):
        self.__cancelIconTasks()
        missing = {row.entry.get('icon') for row in self.rows()}
        for iconId in missing:
            if iconId is not None and profileIconPath(iconId) is None:
                self.iconTasks.append(asyncio.ensure_future(self.__loadIcon(iconId)))

    async def __loadIcon(self, iconId):
        """one download per icon id, shown on every row that uses it"""
        path = await connector.getProfileIcon(iconId)
        for row in self.rows():
            if row.entry.get('icon') == iconId:
                row.setIcon(path)
