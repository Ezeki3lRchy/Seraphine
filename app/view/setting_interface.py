# coding:utf-8
import os

from app.common.qfluentwidgets import (SettingCardGroup, SwitchSettingCard, ComboBoxSettingCard,
                                       PushSettingCard, ExpandLayout, InfoBar,
                                       setTheme, PrimaryPushSettingCard, HyperlinkCard,
                                       TeachingTip, TeachingTipTailPosition, TeachingTipView, PushButton,
                                       RangeSettingCard, ProgressBar, IndeterminateProgressBar,
                                       FluentIcon)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QSizePolicy

from app.common.icons import Icon
from app.common.config import (cfg, YEAR, AUTHOR, VERSION, FEEDBACK_URL, GITHUB_URL, isWin11,
                               BETA)
from app.common.signals import signalBus
from app.common.update_progress import progressText
from app.common.style_sheet import StyleSheet
from app.components.seraphine_interface import SeraphineInterface
from app.components.setting_cards import (LineEditSettingCard, GameTabColorSettingCard,
                                          LooseSwitchSettingCard, ProxySettingCard,
                                          DeathsNumberColorSettingCard, ThemeColorSettingCard)
from app.components.message_box import MultiPathSettingMsgBox


class SettingInterface(SeraphineInterface):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.settingLabel = QLabel(self.tr("Settings"), self)

        self.functionGroup = SettingCardGroup(self.tr("Functions"),
                                              self.scrollWidget)

        self.apiConcurrencyCount = LineEditSettingCard(
            cfg.apiConcurrencyNumber,
            self.tr("LCU API concurrency number"),
            self.tr("Number of concurrency:"),
            1, 1, 20,
            Icon.APPLIST,
            self.tr("Setting the maximum number of API concurrency."),
            self.functionGroup)

        self.careerGamesCount = LineEditSettingCard(
            cfg.careerGamesNumber,
            self.tr("Default games number"),
            self.tr("Number of games:"),
            10, 10, 100,
            Icon.SLIDESEARCH,
            self.tr(
                "Setting the maximum number of games shows in the career interface"),
            self.functionGroup)

        self.gameInfoFilterCard = SwitchSettingCard(
            Icon.FILTER, self.tr("Filter history by mode"),
            self.tr(
                "On the Game Information interface, only show games of the mode you are in (Solo/Duo, Flex, ARAM, ...)"),
            cfg.gameInfoFilter
        )

        self.gameInfoShowTierCard = SwitchSettingCard(
            Icon.TROPHY, self.tr("Show tier in game information"),
            self.tr(
                "Show tier icon in game information interface. Enabling this option affects APP's performance"),
            cfg.showTierInGameInfo)

        self.autoClearGameinfoCard = SwitchSettingCard(
            Icon.ATTACHTEXT, self.tr("Reserve Game Information interface"),
            self.tr(
                "Reserve Game Information interface until the next champion selection starts"),
            cfg.enableReserveGameinfo
        )

        self.goldOverlayCard = SwitchSettingCard(
            Icon.DESKTOPRIGHT, self.tr("Item value overlay"),
            self.tr("While in game, show the difference in item value (total cost of the items "
                    "each player holds) in a small window on top of the game "
                    "(borderless or windowed mode only)"),
            cfg.enableGoldOverlay)
        self.goldOverlayOpacityCard = RangeSettingCard(
            cfg.goldOverlayOpacity, Icon.BLUR, self.tr("Item value overlay opacity"),
            self.tr("Opacity of the item value overlay, in percent"))
        self.goldOverlayLockCard = SwitchSettingCard(
            Icon.LOCK, self.tr("Lock item value overlay"),
            self.tr("Lock the overlay's position and let clicks pass through it to the game"),
            cfg.goldOverlayLocked)
        self.cooldownLedgerCard = SwitchSettingCard(
            FluentIcon.STOP_WATCH, self.tr("Cooldown ledger"),
            self.tr("On Summoner's Rift, shows one row per enemy. Click an ability, "
                    "summoner spell or item you see used to time its usual cooldown.\n"
                    "You start every timer yourself; nothing is detected. Times are approximate: "
                    "they use the level and items you last saw."),
            cfg.enableCooldownLedger)
        # two explicit lines (a setting card's description does not wrap): one line taller,
        # measured with the card's stylesheet font
        ledgerText = self.cooldownLedgerCard.contentLabel
        ledgerText.ensurePolished()
        self.cooldownLedgerCard.setFixedHeight(
            self.cooldownLedgerCard.height() + ledgerText.fontMetrics().lineSpacing())

        self.opggGroup = SettingCardGroup(self.tr("OP.GG"),
                                          self.scrollWidget)

        self.autoShowOpggCard = SwitchSettingCard(
            Icon.WINDOW, self.tr("Show OP.GG window automatically"),
            self.tr("Show OP.GG window automatically when champion selection starts"),
            cfg.autoShowOpgg)
        self.opggOnTopCard = SwitchSettingCard(
            Icon.PADDINGTOP, self.tr(
                "Show OP.GG window on top"),
            self.tr(
                "Show OP.GG window in front of other windows while selecting champions"),
            cfg.enableOpggOnTop)

        self.generalGroup = SettingCardGroup(self.tr("General"),
                                             self.scrollWidget)

        self.lolFolderCard = PushSettingCard(self.tr("Choose folder"),
                                             Icon.FOLDER,
                                             self.tr("Client Path"),
                                             self.tr(
                                                 "Set client path and order"),
                                             self.generalGroup)
        self.lolFolderCard.button.setMinimumWidth(100)
        self.lolFolderCard.button.setStyleSheet(
            "QPushButton {padding-left: 0; padding-right: 0;}")

        self.silentCard = SwitchSettingCard(
            Icon.SNOOZE, self.tr("Silently start"),
            self.tr(
                "Show Seraphine window minimized when it starts"),
            cfg.enableSilent
        )

        self.logGroup = SettingCardGroup(self.tr("Log"), self.scrollWidget)

        self.logLevelCard = ComboBoxSettingCard(
            cfg.logLevel,
            Icon.LOG,
            self.tr('Log Level'),
            self.tr('The level of logging for Seraphine (take effect after restart)'),
            texts=["Debug", "Info", "Warning", "Error"],
            parent=self.logGroup)
        self.viewLogCard = PushSettingCard(
            self.tr("Open"), Icon.DOCUMENT, self.tr("Log file"),
            self.
            tr("Open log directory"),
            self.logGroup)
        self.viewLogCard.button.setMinimumWidth(100)

        # 这玩意左右 padding 大的离谱，手动给它改了
        self.viewLogCard.button.setStyleSheet(
            "QPushButton {padding-left: 0; padding-right: 0;}")

        self.enableStartLolWithApp = SwitchSettingCard(
            Icon.CIRCLERIGHT,
            self.tr("Auto-start LOL"),
            self.tr("Launch LOL client upon opening Seraphine automatically"),
            configItem=cfg.enableStartLolWithApp,
            parent=self.generalGroup)
        self.deleteResourceCard = PushSettingCard(
            self.tr("Delete"), Icon.DELETE, self.tr("Delete cache"),
            self.
            tr("Delete all game resources (Apply it when game resources update)"
               ), self.generalGroup)

        self.deleteResourceCard.button.setMinimumWidth(100)
        self.deleteResourceCard.button.setStyleSheet(
            "QPushButton {padding-left: 0; padding-right: 0;}")

        self.enableCloseToTray = LooseSwitchSettingCard(
            Icon.EXIT,
            self.tr("Minimize to tray on close"),
            self.tr("Minimize to system tray when clicking close"),
            configItem=cfg.enableCloseToTray,
            parent=self.generalGroup)

        self.personalizationGroup = SettingCardGroup(
            self.tr("Personalization"), self.scrollWidget)

        self.micaCard = SwitchSettingCard(
            Icon.BLUR,
            self.tr('Mica effect'),
            self.tr(
                'Apply semi transparent to windows and surfaces (only available on Win11)'),
            cfg.micaEnabled,
            self.personalizationGroup
        )
        self.themeCard = ComboBoxSettingCard(
            cfg.themeMode,
            Icon.BRUSH,
            self.tr("Application theme"),
            self.tr("Change the appearance of Seraphine"),
            texts=[
                self.tr("Light"),
                self.tr("Dark"),
                self.tr("Use system setting")
            ],
            parent=self.personalizationGroup)
        # self.themeColorCard = CustomColorSettingCard(
        #     cfg.themeColor, Icon.PALETTE, self.tr("Theme color"),
        #     self.tr("Change the theme color of Seraphine"),
        #     self.personalizationGroup)
        self.themeColorCard = ThemeColorSettingCard(
            self.tr("Theme color"), self.tr(
                "Change the theme color of Seraphine"),
            cfg.themeColor, self.personalizationGroup)
        self.gameTabColorSettingCard = GameTabColorSettingCard(
            self.tr("Game tabs color"),
            self.tr("Change the color of game tabs"),
            cfg.winCardColor, cfg.loseCardColor, cfg.remakeCardColor,
            self.personalizationGroup
        )
        self.deathNumberColorSettingCard = DeathsNumberColorSettingCard(
            self.tr("Deaths number color"),
            self.tr("Change the color of Deaths number of KDA"),
            cfg.lightDeathsNumberColor, cfg.darkDeathsNumberColor,
            self.personalizationGroup
        )
        self.zoomCard = ComboBoxSettingCard(
            cfg.dpiScale,
            Icon.ZOOMFIT,
            self.tr("Interface zoom"),
            self.tr("Change the size of widgets and fonts"),
            texts=[
                "100%", "125%", "150%", "175%", "200%",
                self.tr("Use system setting")
            ],
            parent=self.personalizationGroup)
        self.languageCard = ComboBoxSettingCard(
            cfg.language,
            Icon.LANGUAGE,
            self.tr('Language'),
            self.tr('Set your preferred language for Seraphine'),
            texts=['简体中文', 'English',
                   self.tr('Use system setting')],
            parent=self.personalizationGroup)

        self.updateGroup = SettingCardGroup(
            self.tr("Update"), self.scrollWidget)

        self.checkUpdateCard = SwitchSettingCard(
            Icon.UPDATE, self.tr("Check for updates"),
            self.tr(
                "Automatically check for updates when software starts"),
            cfg.enableCheckUpdate
        )
        self.autoDownloadUpdateCard = SwitchSettingCard(
            Icon.UPDATE, self.tr("Download updates automatically"),
            self.tr("Download new versions in the background, then only ask to restart"),
            cfg.autoDownloadUpdate
        )
        self.manualCheckUpdateCard = PushSettingCard(
            self.tr("Check now"), Icon.UPDATE,
            self.tr("Check for updates now"),
            self.tr("Current version: {}").format(BETA or VERSION),
            self.updateGroup)
        self._updateIdleText = self.manualCheckUpdateCard.contentLabel.text()
        self._updateState = None  # last update-progress state shown on the card
        # Minimum, not fixed: the stylesheet's horizontal padding makes the
        # button wider than 100 px, and a fixed width clipped the label
        self.manualCheckUpdateCard.button.setMinimumWidth(100)
        # thin bar under the subtitle (signalBus.updateProgressChanged): how far the
        # download is, or a moving bar while verifying / unpacking. Its box has the
        # gap above it, so a hidden bar takes no space; it spans the text above it
        self.updateBarBox = QWidget(self.manualCheckUpdateCard)
        self.updateBarBox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        barLayout = QVBoxLayout(self.updateBarBox)
        barLayout.setContentsMargins(0, 4, 0, 0)
        barLayout.setSpacing(0)
        self.updateProgressBar = ProgressBar(self.updateBarBox)
        self.updateProgressBar.setRange(0, 1000)
        self.updateBusyBar = IndeterminateProgressBar(self.updateBarBox, start=False)
        self.updateBusyBar.setVisible(False)
        barLayout.addWidget(self.updateProgressBar)
        barLayout.addWidget(self.updateBusyBar)
        self.updateBarBox.setVisible(False)
        self.manualCheckUpdateCard.vBoxLayout.addWidget(self.updateBarBox)
        self.httpProxyCard = ProxySettingCard(
            self.tr("HTTP proxy"), self.tr(
                "Using a proxy when connecting to GitHub"),
            cfg.enableProxy, cfg.proxyAddr, self.updateGroup)

        self.aboutGroup = SettingCardGroup(self.tr("About"), self.scrollWidget)

        self.feedbackCard = PrimaryPushSettingCard(
            self.tr('Provide feedback'), Icon.FEEDBACK,
            self.tr('Provide feedback'),
            self.tr('Help us improve Seraphine by providing feedback'),
            self.aboutGroup)

        # 至少和上面的按钮一样宽；英文 "Provide feedback" 比 100px 长，
        # 用最小宽度（而非固定宽度）让按钮自适应文字，避免被截断
        self.feedbackCard.button.setMinimumWidth(100)

        self.aboutCard = HyperlinkCard(
            GITHUB_URL, self.tr("View GitHub"), Icon.INFO, self.tr('About'),
            self.tr('Copyright') + ' © ' + f"{YEAR}, {AUTHOR}. " +
            self.tr('Version') + f" {BETA or VERSION}", self.aboutGroup)
        self.aboutCard.linkButton.setIcon(Icon.GITHUB)

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 90, 0, 20)
        # self.scrollDelagate.vScrollBar.setContentsMargins(0, 50, 0, 0)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

        self.micaCard.switchButton.setEnabled(isWin11())

        # initialize style sheet
        self.scrollWidget.setObjectName('scrollWidget')
        self.settingLabel.setObjectName('settingLabel')
        StyleSheet.SETTING_INTERFACE.apply(self)

        # initialize layout
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.settingLabel.move(36, 30)

        # add cards to group
        self.functionGroup.addSettingCard(self.apiConcurrencyCount)
        self.functionGroup.addSettingCard(self.careerGamesCount)
        self.functionGroup.addSettingCard(self.gameInfoFilterCard)
        self.functionGroup.addSettingCard(self.autoClearGameinfoCard)
        self.functionGroup.addSettingCard(self.gameInfoShowTierCard)
        self.functionGroup.addSettingCard(self.goldOverlayCard)
        self.functionGroup.addSettingCard(self.goldOverlayOpacityCard)
        self.functionGroup.addSettingCard(self.goldOverlayLockCard)
        self.functionGroup.addSettingCard(self.cooldownLedgerCard)

        self.opggGroup.addSettingCard(self.autoShowOpggCard)
        self.opggGroup.addSettingCard(self.opggOnTopCard)

        self.generalGroup.addSettingCard(self.lolFolderCard)
        self.generalGroup.addSettingCard(self.enableStartLolWithApp)
        self.generalGroup.addSettingCard(self.deleteResourceCard)
        self.generalGroup.addSettingCard(self.enableCloseToTray)
        self.generalGroup.addSettingCard(self.silentCard)

        self.logGroup.addSettingCard(self.logLevelCard)
        self.logGroup.addSettingCard(self.viewLogCard)

        self.personalizationGroup.addSettingCard(self.micaCard)
        self.personalizationGroup.addSettingCard(self.themeCard)
        self.personalizationGroup.addSettingCard(self.themeColorCard)
        self.personalizationGroup.addSettingCard(self.gameTabColorSettingCard)
        self.personalizationGroup.addSettingCard(
            self.deathNumberColorSettingCard)
        self.personalizationGroup.addSettingCard(self.zoomCard)
        self.personalizationGroup.addSettingCard(self.languageCard)

        self.updateGroup.addSettingCard(self.checkUpdateCard)
        self.updateGroup.addSettingCard(self.autoDownloadUpdateCard)
        self.updateGroup.addSettingCard(self.manualCheckUpdateCard)
        self.updateGroup.addSettingCard(self.httpProxyCard)

        self.aboutGroup.addSettingCard(self.feedbackCard)
        self.aboutGroup.addSettingCard(self.aboutCard)

        # add setting card group to layout
        self.expandLayout.setSpacing(30)
        self.expandLayout.setContentsMargins(36, 0, 36, 0)
        self.expandLayout.addWidget(self.functionGroup)
        self.expandLayout.addWidget(self.opggGroup)
        self.expandLayout.addWidget(self.generalGroup)
        self.expandLayout.addWidget(self.logGroup)
        self.expandLayout.addWidget(self.personalizationGroup)
        self.expandLayout.addWidget(self.updateGroup)
        self.expandLayout.addWidget(self.aboutGroup)

    def __connectSignalToSlot(self):
        self.lolFolderCard.clicked.connect(self.__onLolFolderCardClicked)

        self.themeCard.comboBox.currentIndexChanged.connect(
            lambda: setTheme(cfg.get(cfg.themeMode)))

        cfg.appRestartSig.connect(self.__showRestartToolTip)
        self.careerGamesCount.pushButton.clicked.connect(
            self.__showUpdatedSuccessfullyToolTip)
        self.feedbackCard.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(FEEDBACK_URL)))
        self.deleteResourceCard.clicked.connect(self.__showFlyout)
        self.manualCheckUpdateCard.clicked.connect(
            self.__onManualCheckUpdateClicked)
        signalBus.checkUpdateFinished.connect(self.__onCheckUpdateFinished)
        signalBus.updateProgressChanged.connect(self.__onUpdateProgressChanged)
        self.viewLogCard.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.join(os.getcwd(), 'log')))
        )

    def __onManualCheckUpdateClicked(self):
        if self._updateState == 'ready':
            # "Restart to update": MainWindow runs the same restart as the ready bar
            signalBus.restartToUpdateRequested.emit()
            return
        self.manualCheckUpdateCard.button.setEnabled(False)
        signalBus.checkUpdateRequested.emit()

    def __onCheckUpdateFinished(self):
        # a failure stays on the card until the next check has run
        if self._updateState == 'failed':
            self.__showUpdateState({'state': 'idle'})
        self.manualCheckUpdateCard.button.setEnabled(self._updateState not in self.UPDATE_BUSY)

    UPDATE_BUSY = ('downloading', 'verifying', 'extracting')

    def __onUpdateProgressChanged(self, payload):
        # 'idle' (MainWindow: a check found nothing newer) never interrupts a download
        if payload['state'] == 'idle' and self._updateState in self.UPDATE_BUSY + ('waiting',):
            return
        self.__showUpdateState(payload)

    def __showUpdateState(self, payload):
        # subtitle: what the update is doing; bar: how far it is; button: "Check now",
        # disabled while a download runs, or "Restart to update" once it is ready
        card, state = self.manualCheckUpdateCard, payload['state']
        self._updateState = None if state == 'idle' else state
        if state == 'idle':
            card.setContent(self._updateIdleText)
        else:
            card.setContent(progressText(state, payload['version'], payload['cur'],
                                         payload['total'], payload['kind'], payload.get('reason')))

        bar, busy = self.updateProgressBar, self.updateBusyBar
        cur, total = payload.get('cur', 0), payload.get('total', 0)
        moving = state in ('verifying', 'extracting')
        showBar = (state == 'downloading' and total > 0) or \
            (state == 'waiting' and total > 0 and cur > 0)
        if showBar:
            bar.setValue(min(1000, cur * 1000 // total))
            bar.setPaused(state == 'waiting')
        bar.setVisible(showBar)
        busy.setVisible(moving)
        if moving:
            busy.start()
        else:
            busy.stop()
        self.updateBarBox.setVisible(showBar or moving)

        button = card.button
        button.setText(self.tr("Restart to update") if state == 'ready' else self.tr("Check now"))
        if state in self.UPDATE_BUSY:
            button.setEnabled(False)
        elif state != 'idle':
            button.setEnabled(True)

    def __onLolFolderCardClicked(self):
        current = cfg.get(cfg.lolFolder)

        msgBox = MultiPathSettingMsgBox(current, self.window())
        msgBox.exec()

    def __showRestartToolTip(self):
        InfoBar.success(self.tr("Updated successfully"),
                        self.tr("Configuration takes effect after restart"),
                        duration=2000,
                        parent=self)

    def __showUpdatedSuccessfullyToolTip(self):
        InfoBar.success(self.tr("Updated successfully"),
                        self.tr("Settings have been applied"),
                        duration=2000,
                        parent=self)

    def __onDeleteButtonClicked(self):
        folders = [
            'champion icons', 'item icons', 'profile icons', 'rune icons',
            'summoner spell icons', "augment icons"
        ]

        for folder in folders:
            path = f'app/resource/game/{folder}'

            if not os.path.exists(path):
                continue

            for file in os.listdir(path):
                filePath = f"{path}/{file}"

                if not os.path.exists(filePath):
                    continue

                os.remove(filePath)

    def __showFlyout(self):
        view = TeachingTipView(
            title=self.tr("Really?"),
            content=self.
            tr("Game resources will be downloaded again\nwhen they are used by Seraphine, which will cost more time"
               ),
            isClosable=True,
            tailPosition=TeachingTipTailPosition.RIGHT)

        applyButton = PushButton(self.tr('Confirm delete'))

        view.widgetLayout.insertSpacing(1, 10)
        view.widgetLayout.addSpacing(10)
        view.addWidget(applyButton, align=Qt.AlignRight)

        t = TeachingTip.make(
            view,
            self.deleteResourceCard.button,
            -1,
            TeachingTipTailPosition.RIGHT,
            self,
        )

        applyButton.clicked.connect(self.__onDeleteButtonClicked)
        view.closed.connect(t.close)
