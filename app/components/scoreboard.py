"""
搜索页 ("赛后记分表" 风格) 共用的自绘工具: 文字 / 多段文字 / 圆角图 / 金边英雄头像 / 标签胶囊 /
物品格, 按尺寸 + 主题 + 用户配色缓存的内容 pixmap, 以及对局列表用的简短文字 (模式简称 / 多久前).
颜色与字体见 design_tokens.py.
"""
import time

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics, QPixmap

from app.common.qfluentwidgets import isDarkTheme
from app.components.design_tokens import palette, resultColor, deathsColor
from app.components.match_widgets import TAG_COLORS

# 英文客户端的模式简称; 中文名 / 未收录的队列退回 "去掉 '排位赛 ' 前缀" 的原名
QUEUE_SHORT_NAMES = {
    0: "Custom",
    400: "Draft",
    420: "Solo/Duo",
    430: "Blind",
    440: "Flex",
    450: "ARAM",
    480: "Swiftplay",
    490: "Quickplay",
    700: "Clash",
    720: "ARAM Clash",
    900: "ARURF",
    1700: "Arena",
    1710: "Arena",
    1900: "URF",
    3140: "Practice",
}


def queueShortName(queueId, name):
    name = name or ""
    if not any('一' <= ch <= '鿿' for ch in name) and queueId in QUEUE_SHORT_NAMES:
        return QUEUE_SHORT_NAMES[queueId]
    return name.replace("排位赛 ", "").replace("Ranked ", "")


def compactAgo(timeStamp, dateText="", now=None):
    """毫秒时间戳 -> 'now' / '12m' / '3h' / '5d'; 一周以前用 dateText (如 '10/19')"""
    secs = max((time.time() if now is None else now) - timeStamp / 1000, 0)
    if secs >= 7 * 86400 and dateText:
        return dateText
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit}"
    return "now"


def durationText(secs):
    return f"{secs // 60}m {secs % 60:02d}s" if secs else ""


def resultType(win, remake):
    if remake:
        return 'remake'
    return 'win' if win else 'lose'


def stripeColor(type, pal):
    """结果色边条 / 结果字: 重开局用次要灰"""
    return pal['muted'] if type == 'remake' else resultColor(type)


# ---------------- 绘制 ----------------

def drawText(p, rect, string, font, color, align=Qt.AlignLeft | Qt.AlignVCenter):
    p.setFont(font)
    p.setPen(color)
    p.drawText(rect, align, string)


def runsWidth(runs):
    return sum(QFontMetrics(font).horizontalAdvance(string) for string, font, _ in runs)


def drawRuns(p, x, y, h, runs):
    """一行里多段不同字体 / 颜色的文字, 依次排开; 返回结束的 x"""
    for string, font, color in runs:
        drawText(p, QRectF(x, y, 1000, h), string, font, color)
        x += QFontMetrics(font).horizontalAdvance(string)
    return x


def drawRoundedPixmap(p, pixmap, rect, radius):
    if pixmap is None or pixmap.isNull():
        return
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    p.setClipPath(clip)
    p.drawPixmap(rect.toRect(), pixmap)
    p.restore()


def drawChampion(p, pixmap, rect, gold, ring=1.5, crop=0):
    """圆形英雄头像 + 海克斯金描边"""
    if pixmap is not None and not pixmap.isNull():
        src = pixmap.copy(crop, crop, pixmap.width() - 2 * crop,
                          pixmap.height() - 2 * crop) if crop else pixmap
        p.save()
        clip = QPainterPath()
        clip.addEllipse(rect)
        p.setClipPath(clip)
        p.drawPixmap(rect.toRect(), src)
        p.restore()
    p.setPen(QPen(gold, ring))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(rect.adjusted(ring / 2, ring / 2, -ring / 2, -ring / 2))


def drawItemSlot(p, pixmap, itemId, rect, radius, pal):
    """物品格; 空格只画发丝线框"""
    if itemId:
        drawRoundedPixmap(p, pixmap, rect, radius)
    else:
        p.setPen(QPen(pal['hairline'], 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def drawTag(p, rect, string, tagColor, font, pal):
    """标签胶囊: 名次低调 (发丝线底 + 墨色字), MVP 用海克斯金, 其余 (SVP / 多杀) 用各自颜色"""
    quiet = QColor(tagColor) == QColor(TAG_COLORS['rank'])
    fill = pal['hairline'] if quiet else (pal['gold'] if string == "MVP" else QColor(tagColor))
    p.setPen(Qt.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    drawText(p, rect, string, font, pal['ink'] if quiet else QColor(255, 255, 255),
             Qt.AlignCenter)


def drawResultCard(p, rect, type, pal, radius=8, tintAlpha=15, stripe=4):
    """记分表卡片底: 纸面 + 发丝线 + 结果色浅底 + 左侧实色边条"""
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    p.setPen(QPen(pal['hairline'], 1))
    p.setBrush(pal['sheet'])
    p.drawPath(path)
    color = stripeColor(type, pal)
    if tintAlpha:
        c = QColor(color)
        c.setAlpha(tintAlpha)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(path)
    if stripe:
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(rect.left(), rect.top(), stripe, rect.height()), color)
        p.restore()
    return path


def cachedContent(widget, draw, extra=()):
    """
    widget 的整块内容 pixmap: 尺寸 / dpr / 主题 / 用户配色 / extra 都没变就直接复用,
    否则新建一张透明 pixmap 交给 draw(p, pal) 作画. 清缓存: widget._cacheKey = None
    """
    dark = isDarkTheme()
    dpr = widget.devicePixelRatioF()
    key = (widget.width(), widget.height(), dpr, dark, resultColor('win').rgba(),
           resultColor('lose').rgba(), resultColor('remake').rgba(),
           deathsColor().rgba()) + tuple(extra)
    if getattr(widget, '_cacheKey', None) == key:
        return widget._cache

    out = QPixmap(max(round(widget.width() * dpr), 1), max(round(widget.height() * dpr), 1))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                     | QPainter.TextAntialiasing)
    draw(p, palette(dark))
    p.end()

    widget._cache, widget._cacheKey = out, key
    return out
