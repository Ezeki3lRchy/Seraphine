from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget


class SignalBus(QObject):
    # listener:
    tasklistNotFound = Signal()

    lolClientStarted = Signal(int)
    lolClientEnded = Signal()
    lolClientChanged = Signal(int)

    terminateListeners = Signal()

    # connector:
    lcuApiExceptionRaised = Signal(str, BaseException)
    currentSummonerProfileChanged = Signal(dict)
    gameStatusChanged = Signal(str)
    champSelectChanged = Signal(dict)
    lobbyChanged = Signal(dict)
    getCmdlineError = Signal()

    # career_interface
    careerGameBarClicked = Signal(str)

    # game_info_interface: (puuid, name, gameId)
    gameInfoBarClicked = Signal(str, str, str)

    # search_interface:
    gameTabClicked = Signal(QWidget)

    # jumps:
    toCareerInterface = Signal(str)
    toSearchInterface = Signal(str)

    # style:
    customColorChanged = Signal(str)

    # OPGG:
    toOpggBuildInterface = Signal(int, str, str)


signalBus = SignalBus()
