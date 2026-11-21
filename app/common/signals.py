from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget


class SignalBus(QObject):
    # listener:
    tasklistNotFound = Signal()

    lolClientStarted = Signal(int)
    lolClientEnded = Signal()
    lolClientChanged = Signal(int)
    # first listener pass found no client: "not in a game" is known (update download)
    lolClientAbsent = Signal()

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

    # manual update check: setting_interface -> main_window, and back when done
    checkUpdateRequested = Signal()
    checkUpdateFinished = Signal()
    # update download progress (dict from update_progress.progressPayload), throttled
    # by the sender: MainWindow (background download) and UpdateMessageBox (dialog)
    updateProgressChanged = Signal(object)
    # the settings card's "Restart to update": setting_interface -> main_window
    restartToUpdateRequested = Signal()

    # OPGG:
    toOpggBuildInterface = Signal(int, str, str)

    # live_client: item gold summary of the running game (dict), None when there is none
    liveGameDataChanged = Signal(object)


signalBus = SignalBus()
