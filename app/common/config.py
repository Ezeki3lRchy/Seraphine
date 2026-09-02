from enum import Enum
import os
import sys

from PySide6.QtCore import QLocale, QSize


from .qfluentwidgets import (qconfig, QConfig, ConfigItem, FolderValidator, BoolValidator,
                             OptionsConfigItem, OptionsValidator, ConfigSerializer,
                             RangeConfigItem, RangeValidator, EnumSerializer, ColorConfigItem)
from app.common import platform


class Language(Enum):
    CHINESE_SIMPLIFIED = QLocale(QLocale.Chinese, QLocale.China)
    ENGLISH = QLocale(QLocale.English)
    AUTO = QLocale()


class LanguageSerializer(ConfigSerializer):
    def serialize(self, language: Language):
        return language.value.name() if language != Language.AUTO else "Auto"

    def deserialize(self, value: str):
        return Language(QLocale(value)) if value != 'Auto' else Language.AUTO


class QSizeSerializer(ConfigSerializer):
    def serialize(self, size: QSize) -> str:
        return f"{size.width()}, {size.height()}"

    def deserialize(self, value: str) -> QSize:
        width, height = map(int, value.split(', '))
        return QSize(width, height)


def isWin11():
    return sys.platform == 'win32' and sys.getwindowsversion().build >= 22000


class Config(QConfig):
    lolFolder = ConfigItem("General", "LolPath", [])
    enableStartLolWithApp = ConfigItem("General", "EnableStartLolWithApp",
                                       False, BoolValidator())

    micaEnabled = ConfigItem(
        "Personalization", "MicaEnabled", isWin11(), BoolValidator())
    dpiScale = OptionsConfigItem("Personalization",
                                 "DpiScale",
                                 "Auto",
                                 OptionsValidator(
                                     [1, 1.25, 1.5, 1.75, 2, "Auto"]),
                                 restart=True)

    language = OptionsConfigItem("Personalization",
                                 "Language",
                                 Language.AUTO,
                                 OptionsValidator(Language),
                                 LanguageSerializer(),
                                 restart=True)

    winCardColor = ColorConfigItem(
        "Personalization", "WinCardColor", '#2839b01b')
    loseCardColor = ColorConfigItem(
        "Personalization", "LoseCardColor", '#28d3190c')
    remakeCardColor = ColorConfigItem(
        "Personalization", "RemakeCardColor", '#28a2a2a2')

    lightDeathsNumberColor = ColorConfigItem(
        "Personalization", "LightDeathsNumberColor", "#ffb60000")
    darkDeathsNumberColor = ColorConfigItem(
        "Personalization", "DarkDeathsNumberColor", "#ffff8d8d")

    careerGamesNumber = RangeConfigItem("Functions", "CareerGamesNumber", 20,
                                        RangeValidator(10, 100))
    apiConcurrencyNumber = RangeConfigItem("Functions", "ApiConcurrencyNumber", 1,
                                           RangeValidator(1, 100), restart=True)

    gameInfoFilter = ConfigItem(
        "Functions", "GameInfoFilter", False, BoolValidator())

    showTierInGameInfo = ConfigItem("Functions", "ShowTierInGameInfo", False,
                                    BoolValidator())
    autoShowOpgg = ConfigItem("Functions", "AutoShowOpgg", True,
                              BoolValidator())
    enableOpggOnTop = ConfigItem(
        "Functions", "EnableOpggOnTop", False, BoolValidator())
    enableAutoAcceptMatching = ConfigItem("Functions",
                                          "EnableAutoAcceptMatching", False,
                                          BoolValidator())
    enableAutoReconnect = ConfigItem("Functions",
                                     "EnableAutoReconnect", False,
                                     BoolValidator())

    autoAcceptMatchingDelay = RangeConfigItem(
        "Functions", "AutoAcceptMatchingDelay", 0, RangeValidator(0, 11))

    enableAutoSelectTimeoutCompleted = ConfigItem("Functions",
                                                  "EnableAutoSelectTimeoutCompleted", False,
                                                  BoolValidator())

    enableRandomSkin = ConfigItem(
        "Functions", "enableRandomSkin", False, BoolValidator())

    enableAutoSelectChampion = ConfigItem(
        "Functions", "EnableAutoSelectChampion", False, BoolValidator())
    autoSelectChampion = ConfigItem(
        "Functions", "AutoSelectChampion", [])
    autoSelectChampionTop = ConfigItem(
        "Functions", "AutoSelectChampionTop", [])
    autoSelectChampionJug = ConfigItem(
        "Functions", "AutoSelectChampionJug", [])
    autoSelectChampionMid = ConfigItem(
        "Functions", "AutoSelectChampionMid", [])
    autoSelectChampionBot = ConfigItem(
        "Functions", "AutoSelectChampionBot", [])
    autoSelectChampionSup = ConfigItem(
        "Functions", "AutoSelectChampionSup", [])

    enableAutoBanChampion = ConfigItem(
        "Functions", "EnableAutoBanChampion", False, BoolValidator())
    autoBanChampion = ConfigItem(
        "Functions", "AutoBanChampion", [])
    autoBanChampionTop = ConfigItem(
        "Functions", "AutoBanChampionTop", [])
    autoBanChampionJug = ConfigItem(
        "Functions", "AutoBanChampionJug", [])
    autoBanChampionMid = ConfigItem(
        "Functions", "AutoBanChampionMid", [])
    autoBanChampionBot = ConfigItem(
        "Functions", "AutoBanChampionBot", [])
    autoBanChampionSup = ConfigItem(
        "Functions", "AutoBanChampionSup", [])

    autoBanDelay = RangeConfigItem(
        "Functions", "AutoBanDelay", 0, RangeValidator(0, 25))
    pretentBan = ConfigItem("Functions", "PrententBan", False, BoolValidator())
    enableEmptyBan = ConfigItem(
        "Functions", "EnableEmptyBan", False, BoolValidator())

    autoAcceptCeilSwap = ConfigItem(
        "Functions", "AutoAcceptCeilSwap", False, BoolValidator())
    autoAcceptChampTrade = ConfigItem(
        "Functions", "AutoAcceptChampTrade", False, BoolValidator())

    # 排位选人: 用 Riot Client 聊天房名单补出被 Riot 标为 HIDDEN 的队友.
    # 只进 config.json, 无设置界面 / 翻译.
    revealHiddenTeammates = ConfigItem(
        "Functions", "RevealHiddenTeammates", True, BoolValidator())

    lastNoticeSha = ConfigItem("Other", "LastNoticeSha", "")

    enableCloseToTray = ConfigItem(
        "General", "EnableCloseToTray", None, OptionsValidator([None, True, False]))

    searchHistory = ConfigItem(
        "Other", "SearchHistory", ""
    )

    enableGameStartMinimize = ConfigItem("General",
                                         "EnableGameStartMinimize", False,
                                         BoolValidator())

    enableCheckUpdate = ConfigItem("General",
                                   "EnableCheckUpdate", True,
                                   BoolValidator())

    logLevel = OptionsConfigItem(
        "General", "LogLevel", 40, OptionsValidator([10, 20, 30, 40]), restart=True)

    enableProxy = ConfigItem("General", "EnableProxy", False, BoolValidator())
    proxyAddr = ConfigItem("General", "HttpProxy", "")

    opggRegion = ConfigItem("Functions", "OpggRegion", "kr",
                            OptionsValidator(["kr", "na", "global"]), restart=True)
    opggTier = ConfigItem("Functions", "OpggTier", "emerald_plus", OptionsValidator([
        "ibsg", "gold_plus", "platinum_plus", "emerald_plus", "diamond_plus", "master", "master_plus", "grandmaster", "challenger"
    ]))
    opggPosition = ConfigItem("Functions", "OpggPosition", "MID", OptionsValidator([
        "TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]))

    enableReserveGameinfo = ConfigItem(
        "Functions", "EnableReserveGameinfo", False, BoolValidator())

    enableSilent = ConfigItem(
        "General", "EnableSilent", False, BoolValidator())

    windowSize = ConfigItem("Personalization", "WindowSize", QSize(
        1134, 826), None, QSizeSerializer())


YEAR = 2023
AUTHOR = "Ezeki3lRchy"
VERSION = "1.3.0"
BETA = None
FEEDBACK_URL = "https://github.com/Ezeki3lRchy/Seraphine/issues?q=is%3Aissue"
GITHUB_URL = "https://github.com/Ezeki3lRchy/Seraphine"
LOCAL_PATH = platform.config_dir()

cfg = Config()
qconfig.load(os.path.join(LOCAL_PATH, "config.json"), cfg)


def isChineseText() -> bool:
    """是否应使用中文文案: 显式中文, 或 AUTO 且系统 locale 为中文。
    显式英文 / AUTO+非中文系统 -> 英文。"""
    lang = cfg.language.value
    if lang == Language.AUTO:
        return QLocale().language() == QLocale.Chinese
    return lang == Language.CHINESE_SIMPLIFIED
