"""
战绩页视觉规范 (生涯页 OP.GG 风格改版): 配色与字体.

- 颜色: 纸面白 / 墨色 / 次要灰 / 发丝线 + 海克斯金 (仅用于英雄描边 / 等级 / MVP / 主分路);
  胜负色沿用用户在设置里配置的卡片颜色, 以实色边条 + 浅色底呈现.
- 字体: 数字统一用 Bahnschrift (Windows 自带的 DIN 风格字体, 记分牌感), 文字用 Segoe UI.
"""
from PySide6.QtGui import QColor, QFont

from app.common.qfluentwidgets import isDarkTheme

NUMBER_FAMILIES = ['Bahnschrift', 'DIN Alternate', 'Segoe UI']
TEXT_FAMILIES = ['Segoe UI', 'Microsoft YaHei', 'PingFang SC']

GOLD_LIGHT = QColor('#B8913F')
GOLD_DARK = QColor('#C8AA6E')


def palette(dark=None):
    """当前主题的颜色表"""
    if dark is None:
        dark = isDarkTheme()

    if dark:
        return {
            'sheet': QColor('#23272E'),
            'ink': QColor('#EEF0F3'),
            'muted': QColor('#9AA1AE'),
            'hairline': QColor('#343A44'),
            'gold': GOLD_DARK,
        }

    return {
        'sheet': QColor('#FFFFFF'),
        'ink': QColor('#1C2230'),
        'muted': QColor('#6A7284'),
        'hairline': QColor('#E4E7EC'),
        'gold': GOLD_LIGHT,
    }


def numberFont(size, weight=QFont.DemiBold):
    """weight 可传 QFont.Weight 或 100..900 的整数"""
    font = QFont(NUMBER_FAMILIES)
    font.setPixelSize(size)
    font.setWeight(weight if isinstance(weight, QFont.Weight) else QFont.Weight(int(weight)))
    return font


def textFont(size, bold=False):
    font = QFont(TEXT_FAMILIES)
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def resultColor(type):
    """胜 / 负 / 重开的实色 (用户配置的卡片颜色去掉透明度)"""
    from app.common.style_sheet import colorManager

    c = QColor(colorManager.getColor(type)[0])
    c.setAlpha(255)
    return c


def tint(color, alpha):
    c = QColor(color)
    c.setAlpha(alpha)
    return c
