"""Star toggle that saves / removes the shown player (Career header, Search header)."""
from PySide6.QtCore import Qt

from app.common.icons import Icon
from app.common.qfluentwidgets import (ToolButton, ToolTipFilter, ToolTipPosition, InfoBar,
                                       InfoBarPosition)
from app.common.signals import signalBus
from app.lol import saved_players


class SaveStarButton(ToolButton):
    def __init__(self, parent=None):
        # ToolButton(icon, parent) re-enters __init__ through its overload: pass the parent
        # alone and set the icon afterwards
        super().__init__(parent)
        self.setIcon(Icon.STAR)
        self.puuid = None
        self.name = ''
        self.iconId = None
        self.rank = None

        self.installEventFilter(ToolTipFilter(self, 500, ToolTipPosition.TOP))
        self.clicked.connect(self.__onClicked)
        signalBus.savedPlayersChanged.connect(self.__updateState)
        self.setPlayer(None)

    def setPlayer(self, puuid, name='', iconId=None, rank=None):
        """The player shown next to the button; None disables it"""
        self.puuid = puuid
        self.name = name
        self.iconId = iconId
        self.rank = rank
        self.__updateState()

    def __updateState(self):
        self.setEnabled(self.puuid is not None)
        saved = self.puuid is not None and saved_players.isSaved(self.puuid)
        self.setIcon(Icon.STARFILLED if saved else Icon.STAR)
        self.setToolTip(self.tr("Remove from saved players") if saved
                        else self.tr("Save player"))

    def __onClicked(self):
        if saved_players.isSaved(self.puuid):
            saved_players.remove(self.puuid)
        elif not saved_players.add(self.puuid, self.name, self.iconId, self.rank):
            InfoBar.warning(
                self.tr("Saved players list is full"),
                self.tr("Remove someone from Saved players first (at most %1).").replace(
                    "%1", str(saved_players.MAX_PLAYERS)),
                orient=Qt.Vertical,
                position=InfoBarPosition.BOTTOM_RIGHT,
                duration=5000,
                parent=self.window())
            return

        signalBus.savedPlayersChanged.emit()
