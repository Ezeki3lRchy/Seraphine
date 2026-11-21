"""
Low-level painting helpers shared by the self-painted widgets (text, text runs, rounded
pixmaps, champion circles, cards). Depends on PySide6 only, so any widget module can import
it without pulling in the rest of the widget layer.
"""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainterPath, QPen, QFontMetrics


def drawCard(p, rect, pal, radius=10):
    """卡片底: 纸面 + 发丝线描边, 不加阴影"""
    p.setPen(QPen(pal['hairline'], 1))
    p.setBrush(pal['sheet'])
    p.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def drawText(p, rect, string, font, color, align=Qt.AlignLeft | Qt.AlignVCenter):
    p.setFont(font)
    p.setPen(color)
    p.drawText(rect, align, string)


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
