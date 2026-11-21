"""Display formatting and translation for the data layer: server names, ``ToolsTranslator``,
tier / position translation and time formatters. The only Qt dependency of the former
``tools`` helpers lives here."""
import time

from PySide6.QtCore import QObject

from ..common.config import isChineseText


SERVERS_NAME = {
    "NJ100": "联盟一区", "GZ100": "联盟二区", "CQ100": "联盟三区", "TJ100": "联盟四区", "TJ101": "联盟五区",
    "HN10": "黑色玫瑰", "HN1": "艾欧尼亚", "BGP2": "峡谷之巅"
}

SERVERS_SUBSET = {
    "NJ100": ["祖安", "皮尔特沃夫", "巨神峰", "教育网", "男爵领域", "均衡教派", "影流", "守望之海"],
    "GZ100": ["卡拉曼达", "暗影岛", "征服之海", "诺克萨斯", "战争学院", "雷瑟守备"],
    "CQ100": ["班德尔城", "裁决之地", "水晶之痕", "钢铁烈阳", "皮城警备"],
    "TJ100": ["比尔吉沃特", "弗雷尔卓德", "扭曲丛林"],
    "TJ101": ["德玛西亚", "无畏先锋", "恕瑞玛", "巨龙之巢"]
}


class ToolsTranslator(QObject):
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.top = self.tr("TOP")
        self.jungle = self.tr("JUG")
        self.middle = self.tr("MID")
        self.bottom = self.tr("BOT")
        self.support = self.tr("SUP")

        self.positionMap = {
            "TOP": self.top,
            "JUNGLE": self.jungle,
            "MID": self.middle,
            "ADC": self.bottom,
            "SUPPORT": self.support
        }

        self.rankedSolo = self.tr('Ranked Solo')
        self.rankedFlex = self.tr("Ranked Flex")

        self.unranked = self.tr("Unranked")
        self.unknown = self.tr("Unknown")


def translateTier(orig: str, short=False) -> str:
    if orig == '':
        return "--"

    maps = {
        'Iron': ['坚韧黑铁', '黑铁'],
        'Bronze': ['英勇黄铜', '黄铜'],
        'Silver': ['不屈白银', '白银'],
        'Gold': ['荣耀黄金', '黄金'],
        'Platinum': ['华贵铂金', '铂金'],
        'Emerald': ['流光翡翠', '翡翠'],
        'Diamond': ['璀璨钻石', '钻石'],
        'Master': ['超凡大师', '大师'],
        'Grandmaster': ['傲世宗师', '宗师'],
        'Challenger': ['最强王者', '王者'],
    }

    index = 1 if short else 0

    return maps[orig.capitalize()][index] if isChineseText() else orig.capitalize()


def timeStampToStr(stamp):
    """
    @param stamp: Millisecond timestamp
    """
    timeArray = time.localtime(stamp / 1000)
    return time.strftime("%Y/%m/%d %H:%M", timeArray)


def timeStampToShortStr(stamp):
    timeArray = time.localtime(stamp / 1000)
    return time.strftime("%m/%d", timeArray)


def secsToStr(secs):
    return time.strftime("%M:%S", time.gmtime(secs))


def getPositionKey(queueId, lane, role):
    """
    排位 (420 / 440) 对局里的分路, 返回 'TOP' / 'JUNGLE' / 'MIDDLE' / 'BOTTOM' /
    'UTILITY' 之一; 非排位或无法判断时返回 None
    """
    if queueId not in [420, 440]:
        return None

    if lane == 'TOP':
        return 'TOP'
    elif lane == "JUNGLE":
        return 'JUNGLE'
    elif lane == 'MIDDLE':
        return 'MIDDLE'
    elif role == 'SUPPORT':
        return 'UTILITY'
    elif lane == 'BOTTOM' and role == 'CARRY':
        return 'BOTTOM'

    return None


def translatePositionKey(positionKey):
    if positionKey is None:
        return None

    tt = ToolsTranslator()

    return {
        'TOP': tt.top,
        'JUNGLE': tt.jungle,
        'MIDDLE': tt.middle,
        'BOTTOM': tt.bottom,
        'UTILITY': tt.support,
    }.get(positionKey)
