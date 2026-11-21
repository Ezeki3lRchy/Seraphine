"""
OP.GG 风格战绩页共用的小控件: 带详情悬浮提示的物品图标, 标签胶囊 (名次 / MVP / SVP / 多杀),
段位徽章大图. 对局信息页与生涯页共用.
"""
import re

from PySide6.QtCore import Qt, QRect, QCoreApplication, QT_TRANSLATE_NOOP
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QLabel

from app.common.qfluentwidgets import ToolTipFilter, ToolTipPosition
from app.components.champion_icon_widget import RoundedLabel
from app.lol.connector import connector


# 物品描述里 LoL 自定义标签 -> Qt 富文本. 强调类标签加粗, 其余标签去掉只留文字
_BOLD_TAGS = {'attention', 'passive', 'active', 'raritymythic', 'raritylegendary',
              'raritygeneric', 'keywordmajor', 'keywordstealth', 'unique', 'rules',
              'flavortext', 'buffedstat', 'nerfedstat', 'ornnbonus'}
_TAG_RE = re.compile(r'<(/?)([A-Za-z]+)[^>]*?(/?)>')

TOOLTIP_WIDTH = 280


def itemDescriptionToHtml(description: str) -> str:
    """把 items.json 的 description 转成 QLabel 能显示的富文本 (只保留 b / br)"""
    if not description:
        return ""

    def convert(m):
        closing, tag = m.group(1), m.group(2).lower()
        if tag == 'br':
            return '<br>'
        if tag == 'li':
            return '' if closing else '<br>• '
        if tag in _BOLD_TAGS:
            return '</b>' if closing else '<b>'
        return ''

    text = _TAG_RE.sub(convert, description)

    # 去掉开头 / 结尾 / 连续多余的换行
    text = re.sub(r'(\s*<br>\s*){3,}', '<br><br>', text)
    text = re.sub(r'^(\s*<br>\s*)+|(\s*<br>\s*)+$', '', text)
    return text.strip()


def itemTooltipHtml(itemId) -> str:
    """物品悬浮提示卡片 (名称 + 金色总价 / 属性 / 被动说明, 跟随主题); 未知物品返回空串"""
    if not itemId:
        return ""

    manager = getattr(connector, 'manager', None)
    info = manager.getItemInfo(itemId) if manager else None
    if not info:
        return ""

    from app.components.rich_tooltip import itemCardHtml

    # 定宽表格让长描述换行 (RichToolTip 与 Fluent ToolTip 的 QLabel 都适用)
    return itemCardHtml(info.get('name') or str(itemId), info.get('priceTotal') or 0,
                        info.get('description', ''), TOOLTIP_WIDTH)


class ItemIcon(RoundedLabel):
    """物品图标, 悬浮时显示物品名称 / 价格 / 属性"""

    def __init__(self, icon, itemId=0, size=22, radius=3, borderWidth=0, parent=None):
        super().__init__(icon, radius=radius, borderWidth=borderWidth, parent=parent)
        self.itemId = itemId
        self.setFixedSize(size, size)

        tip = itemTooltipHtml(itemId)
        if tip:
            self.setToolTip(tip)
            self.installEventFilter(
                ToolTipFilter(self, 150, ToolTipPosition.TOP))


# 标签胶囊颜色
TAG_COLORS = {
    "MVP": "#d4a017",
    "SVP": "#8e6bd8",
    "multiKill": "#d13b4b",
    "rank": "#6b7280",
}

# English source texts; translated where they are shown (gameTags), not at import time
MULTI_KILL_TAGS = {2: QT_TRANSLATE_NOOP("GameTags", "Double kill"),
                   3: QT_TRANSLATE_NOOP("GameTags", "Triple kill"),
                   4: QT_TRANSLATE_NOOP("GameTags", "Quadra kill"),
                   5: QT_TRANSLATE_NOOP("GameTags", "Penta kill")}

TIER_NAMES = {'IRON': QT_TRANSLATE_NOOP("RankTier", "Iron"),
              'BRONZE': QT_TRANSLATE_NOOP("RankTier", "Bronze"),
              'SILVER': QT_TRANSLATE_NOOP("RankTier", "Silver"),
              'GOLD': QT_TRANSLATE_NOOP("RankTier", "Gold"),
              'PLATINUM': QT_TRANSLATE_NOOP("RankTier", "Platinum"),
              'EMERALD': QT_TRANSLATE_NOOP("RankTier", "Emerald"),
              'DIAMOND': QT_TRANSLATE_NOOP("RankTier", "Diamond"),
              'MASTER': QT_TRANSLATE_NOOP("RankTier", "Master"),
              'GRANDMASTER': QT_TRANSLATE_NOOP("RankTier", "Grandmaster"),
              'CHALLENGER': QT_TRANSLATE_NOOP("RankTier", "Challenger")}


def tierText(tier, division):
    """LCU 原始段位 -> 'Diamond II' / 'Master' (按当前语言翻译); 取不到为空串"""
    if not tier or tier in ('NONE', 'UNRANKED'):
        return ""

    division = "" if division in ("", "NA", None) else division
    name = TIER_NAMES.get(tier.upper())
    name = QCoreApplication.translate("RankTier", name) if name else tier.title()
    return f"{name} {division}".strip()


def ordinal(n: int) -> str:
    """'1st' / '2nd' / '3rd' / '4th' ...; one translatable pattern per English suffix"""
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    pattern = {'st': QCoreApplication.translate("GameTags", "%1st"),
               'nd': QCoreApplication.translate("GameTags", "%1nd"),
               'rd': QCoreApplication.translate("GameTags", "%1rd"),
               'th': QCoreApplication.translate("GameTags", "%1th")}[suffix]
    return pattern.replace("%1", str(n))


class TagPill(QLabel):
    """OP.GG 风格圆角标签 (MVP / SVP / Double kill / 5th ...)"""

    def __init__(self, text, color, fontSize=11, parent=None):
        super().__init__(text, parent)
        self.setObjectName("tagPill")
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 8px;"
            f"padding: 1px 7px; font: bold {fontSize}px 'Segoe UI', 'Microsoft YaHei';")


def gameTags(game) -> list:
    """
    一局的 OP.GG 风格标签, 按显示顺序 [(text, color)]:
    多杀 (双杀起) -> MVP / SVP 或评分名次 (2nd .. 10th). 重开局没有标签
    """
    if game.get('remake'):
        return []

    tags = []

    multiKill = game.get('multiKill', 0)
    if multiKill >= 2:
        tags.append((QCoreApplication.translate("GameTags", MULTI_KILL_TAGS[min(multiKill, 5)]),
                     TAG_COLORS['multiKill']))

    badge = game.get('badge')
    if badge:
        tags.append((badge, TAG_COLORS[badge]))
    elif game.get('scoreRank'):
        tags.append((ordinal(game['scoreRank']), TAG_COLORS['rank']))

    return tags


def rankEmblemPath(tier: str) -> str:
    """段位大徽章 (500px png, 与 OP.GG 同款); tier 为 LCU 原始英文段位 (DIAMOND ...)"""
    tier = (tier or '').lower()
    if tier in ('', 'none', 'unranked', 'na'):
        tier = 'unranked'
    return f"app/resource/images/{tier}.png"


def rankMiniIconPath(tier: str) -> str:
    """段位小图标 (20px svg, 适合 ~18px 的行内显示); 大徽章缩到这么小只剩一团"""
    tier = (tier or '').upper()
    if tier in ('', 'NONE', 'UNRANKED', 'NA'):
        tier = 'UNRANKED'
    return f"app/resource/images/{tier}.svg"


def rankMiniPixmap(tier: str, size: int = 18) -> QPixmap:
    """rankMiniIconPath 渲染成 2 倍分辨率的 QPixmap (画进 size × size 的矩形里在高分屏也清晰)"""
    # The svgs are not square (IRON is 20x14): centre them on a square canvas so callers
    # that draw the pixmap into a size x size rect do not stretch it.
    side = size * 2
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    QIcon(rankMiniIconPath(tier)).paint(painter, QRect(0, 0, side, side), Qt.AlignCenter)
    painter.end()
    return pixmap


class RankEmblem(QLabel):
    def __init__(self, tier, size=72, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setTier(tier)

    def setTier(self, tier):
        pixmap = QPixmap(rankEmblemPath(tier))
        if not pixmap.isNull():
            ratio = self.devicePixelRatioF()
            pixmap = pixmap.scaled(int(self.width() * ratio), int(self.height() * ratio),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
        self.setPixmap(pixmap)
