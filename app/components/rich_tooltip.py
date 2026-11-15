"""
自绘悬浮提示: 圆角 + 真透明边角 + 柔和阴影, 跟随深 / 浅色主题.

原生 QToolTip 在 Windows 上只能用 QSS 改成方角白底 + 系统硬阴影, 和深色界面格格不入.
RichToolTip 提供与 QToolTip 相同的静态接口 (showText / hideText),
自绘控件的 event() 里直接替换调用即可:

    RichToolTip.showText(e.globalPos(), textOrHtml, self, rect)
    RichToolTip.hideText()

行为与 QToolTip 一致: 光标离开 rect (或离开 owner) / 鼠标按下 / 滚轮 / 按键 / 窗口失活 /
owner 隐藏或销毁时消失; 同一文本重复 show 不重排不闪烁; 贴近屏幕边缘时翻到光标另一侧.
窗口是 Qt.ToolTip 类型, 不抢焦点, 鼠标事件穿透.
"""
import html
import re

from PySide6 import QtGui
from PySide6.QtCore import Qt, QEvent, QObject, QPoint, QRect, QRectF, QTimer, QPropertyAnimation
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QPainter, QPainterPath,
                           QPalette, QTextDocument, QAbstractTextDocumentLayout)
from PySide6.QtWidgets import QApplication, QWidget

from app.common.qfluentwidgets import isDarkTheme
from app.components.design_tokens import textFont

MAX_WIDTH = 320       # 文本区最大宽度, 超出自动换行
PADDING_X = 12
PADDING_Y = 9
RADIUS = 8
SHADOW = 12           # 阴影外扩 (窗口比卡片四周大这么多)
CURSOR_OFFSET = QPoint(4, 18)


def tooltipColors(dark=None) -> dict:
    """提示框配色 (十六进制字符串, 富文本里直接用)"""
    if dark is None:
        dark = isDarkTheme()

    if dark:
        return {
            'bg': '#2B313B', 'border': '#3B4350', 'shadow': (0, 0, 0, 110),
            'ink': '#EEF0F3', 'muted': '#A7AEBB', 'faint': '#7E8694',
            'divider': '#3B4350', 'accent': '#7FA3F0', 'gold': '#D8B978',
        }
    return {
        'bg': '#FFFFFF', 'border': '#DCE0E7', 'shadow': (20, 30, 50, 46),
        'ink': '#1C2230', 'muted': '#5F6878', 'faint': '#8A92A0',
        'divider': '#E3E6EC', 'accent': '#3B6FD8', 'gold': '#A8802E',
    }


def plainTextHtml(text: str, colors: dict) -> str:
    """纯文本 -> 富文本: 每行一段, 段间留出行距, 保留连续空格"""
    lines = text.split('\n')
    paras = []
    for i, line in enumerate(lines):
        top = 0 if i == 0 else 2
        paras.append(f"<p style='margin:0; margin-top:{top}px; line-height:125%; "
                     f"white-space:pre-wrap;'>"
                     f"{html.escape(line) or '&nbsp;'}</p>")
    return "".join(paras)


# ---------------- 物品卡片 ----------------

# 属性名关键字 -> 数值颜色 (深色主题, 浅色主题); 中英文客户端都认, 按顺序匹配
_STAT_COLORS = [
    (('penetration', 'lethality', '穿甲', '穿透'), ('#F08A7A', '#C9412E')),
    (('critical', '暴击'), ('#F08A7A', '#C9412E')),
    (('attack speed', '攻击速度'), ('#EBCB6B', '#A47E12')),
    (('attack damage', '攻击力'), ('#F0A35E', '#C2651A')),
    (('ability power', '法术强度'), ('#B794F6', '#7C4DDB')),
    (('ability haste', '技能急速'), ('#8CCBF2', '#2A82BC')),
    (('magic resist', '魔法抗性'), ('#7FB2F0', '#2F6FC4')),
    (('armor', '护甲'), ('#E8C46A', '#A07A16')),
    (('mana', '法力'), ('#6FA8FF', '#2F6FD0')),
    (('life steal', 'omnivamp', 'heal', 'health', '生命', '吸血', '治疗'), ('#6FCF8E', '#2E9455')),
    (('move', '移动速度', '移速'), ('#D5DAE2', '#4A5263')),
]

_LOL_TAG_RE = re.compile(r'<(/?)([A-Za-z][\w-]*)[^>]*?(/?)>')
_BR_RE = re.compile(r'<br\s*/?>', re.I)
# 只是容器 / 排版用的标签, 去掉不加样式
_STRIP_TAGS = {'maintext', 'stats', 'font', 'span', 'p', 'div', 'b', 'i', 'u', 'ul', 'ol'}
_ACCENT_TAGS = {'passive', 'active', 'unique', 'keywordmajor', 'raritymythic',
                'raritylegendary', 'raritygeneric'}
_ITALIC_TAGS = {'rules', 'flavortext'}


def _statColor(label: str, dark: bool) -> str:
    low = label.lower()
    for keys, (cDark, cLight) in _STAT_COLORS:
        if any(k in low for k in keys):
            return cDark if dark else cLight
    return tooltipColors(dark)['accent']


def _lolTagsToHtml(text: str, c: dict) -> str:
    """LoL 自定义标签 -> Qt 富文本 span: 被动 / 主动名用强调色, 数值类标签用正文主色"""
    def convert(m):
        closing, tag = m.group(1), m.group(2).lower()
        if tag == 'br':
            return '<br>'
        if tag == 'li':
            return '' if closing else '<br>•&nbsp;'
        if tag in _STRIP_TAGS:
            return ''
        if closing:
            return '</span>'
        if tag in _ACCENT_TAGS:
            return f"<span style='color:{c['accent']}; font-weight:600;'>"
        if tag in _ITALIC_TAGS:
            return f"<span style='color:{c['faint']}; font-style:italic;'>"
        return f"<span style='color:{c['ink']};'>"

    return _LOL_TAG_RE.sub(convert, text)


def _plainOf(fragment: str) -> str:
    return re.sub(r'<[^>]+>', '', fragment).strip()


def itemCardHtml(name: str, price: int, description: str, width: int, dark=None) -> str:
    """
    物品提示卡片: 标题 + 右侧金色总价 / 属性行 (数值按属性着色) / 细分隔线 / 被动与主动说明.
    description 是 items.json 里带 LoL 自定义标签的原文
    """
    if dark is None:
        dark = isDarkTheme()
    c = tooltipColors(dark)
    description = description or ""

    # 属性块: <stats><attention>20</attention> Ability Power<br>...</stats>
    statLines = []
    m = re.search(r'<stats>(.*?)</stats>', description, re.S | re.I)
    if m:
        for line in _BR_RE.split(m.group(1)):
            sm = re.match(r'\s*<(attention|buffedstat|nerfedstat|ornnbonus)[^>]*>(.*?)</\1>(.*)$',
                          line, re.S | re.I)
            if sm:
                value, label = _plainOf(sm.group(2)), _plainOf(sm.group(3))
                statLines.append(
                    f"<span style='color:{_statColor(label, dark)}; font-weight:600;'>"
                    f"{html.escape(value)}</span>&nbsp;"
                    f"<span style='color:{c['ink']};'>{html.escape(label)}</span>")
            elif _plainOf(line):
                statLines.append(f"<span style='color:{c['ink']};'>"
                                 f"{html.escape(_plainOf(line))}</span>")
        description = description[:m.start()] + description[m.end():]

    # 其余说明按空行分段, 段与段之间留小间距
    body = _lolTagsToHtml(description, c)
    body = re.sub(r'^(\s*<br>\s*)+|(\s*<br>\s*)+$', '', body.strip())
    paras = [p.strip() for p in re.split(r'(?:\s*<br>\s*){2,}', body) if _plainOf(p)]
    paras = [re.sub(r'^(\s*<br>\s*)+|(\s*<br>\s*)+$', '', p) for p in paras]

    head = (f"<table width='100%' cellspacing=0 cellpadding=0><tr>"
            f"<td style='color:{c['ink']}; font-size:14px; font-weight:600;'>"
            f"{html.escape(name)}</td>")
    if price:
        head += (f"<td align=right valign=middle style='color:{c['gold']}; font-size:12px; "
                 f"font-weight:600; padding-left:10px;'>{price}&nbsp;g</td>")
    head += "</tr></table>"

    parts = [head]
    if statLines:
        parts.append(f"<p style='margin:0; margin-top:5px; line-height:125%;'>"
                     f"{'<br>'.join(statLines)}</p>")
    if paras:
        parts.append(
            f"<table width='100%' cellspacing=0 cellpadding=0 style='margin-top:8px; "
            f"margin-bottom:7px;'><tr><td height=1 style='background-color:{c['divider']}; "
            f"font-size:1px;'>&nbsp;</td></tr></table>")
        for i, p in enumerate(paras):
            top = 0 if i == 0 else 6
            parts.append(f"<p style='margin:0; margin-top:{top}px; color:{c['muted']}; "
                         f"line-height:120%;'>{p}</p>")

    return (f"<table width={width} cellspacing=0 cellpadding=0><tr>"
            f"<td style='font-size:12px;'>{''.join(parts)}</td></tr></table>")


class RichToolTip(QWidget):
    """单例悬浮提示窗口, 通过静态方法使用"""

    _instance = None

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint
                         | Qt.NoDropShadowWindowHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)

        self._text = ""
        self._owner = None
        self._rect = QRect()
        self._colors = tooltipColors()

        self.doc = QTextDocument(self)
        self.doc.setDocumentMargin(0)
        self.doc.setUndoRedoEnabled(False)

        self.hideTimer = QTimer(self)
        self.hideTimer.setSingleShot(True)
        self.hideTimer.timeout.connect(self._hide)

        # 兜底: 光标移出 rect 但没有鼠标事件进来 (比如直接移出窗口) 时也能消失
        self.pollTimer = QTimer(self)
        self.pollTimer.setInterval(120)
        self.pollTimer.timeout.connect(self._checkCursor)

        self.fadeAni = QPropertyAnimation(self, b'windowOpacity', self)
        self.fadeAni.setDuration(110)
        self.fadeAni.setStartValue(0.0)
        self.fadeAni.setEndValue(1.0)

    # ---------------- 静态接口 (同 QToolTip) ----------------

    @staticmethod
    def showText(pos: QPoint, text: str, w: QWidget = None, rect: QRect = None, msecShowTime=-1):
        if not text:
            RichToolTip.hideText()
            return

        if RichToolTip._instance is None:
            RichToolTip._instance = RichToolTip()
        RichToolTip._instance._show(pos, text, w, QRect(rect) if rect else QRect(), msecShowTime)

    @staticmethod
    def hideText():
        if RichToolTip._instance is not None:
            RichToolTip._instance._hide()

    # ---------------- 显示 / 隐藏 ----------------

    def _show(self, pos, text, owner, rect, msecShowTime):
        dark = isDarkTheme()
        colors = tooltipColors(dark)
        same = self.isVisible() and text == self._text and owner is self._owner \
            and colors == self._colors

        self._setOwner(owner)
        self._rect = rect

        if same:
            # 同一提示反复触发 ToolTip 事件: 不重排不移动, 避免闪烁
            self._restartHideTimer(msecShowTime)
            return

        self._text = text
        self._colors = colors
        self._layoutText()
        self._place(pos)
        self._restartHideTimer(msecShowTime)

        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            self.raise_()
            self.fadeAni.start()
            app = QApplication.instance()
            if app:
                app.installEventFilter(self)
            self.pollTimer.start()
        self.update()

    def _hide(self):
        self.hideTimer.stop()
        self.pollTimer.stop()
        self.fadeAni.stop()
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        self._setOwner(None)
        self._rect = QRect()
        self._text = ""
        if self.isVisible():
            self.hide()

    def _restartHideTimer(self, msecShowTime):
        # 同 QToolTip 的默认停留时间: 10s, 长文本每多一个字加 40ms
        if msecShowTime is None or msecShowTime < 0:
            msecShowTime = 10000 + 40 * max(0, self.doc.characterCount() - 100)
        self.hideTimer.start(msecShowTime)

    def _setOwner(self, owner):
        if owner is self._owner:
            return
        if self._owner is not None:
            try:
                self._owner.removeEventFilter(self)
                self._owner.destroyed.disconnect(self._onOwnerDestroyed)
            except (RuntimeError, TypeError):
                pass
        self._owner = owner
        if owner is not None:
            owner.installEventFilter(self)
            owner.destroyed.connect(self._onOwnerDestroyed)

    def _onOwnerDestroyed(self, *args):
        self._owner = None
        self._hide()

    # ---------------- 排版 / 定位 ----------------

    def _layoutText(self):
        c = self._colors
        self.doc.setDefaultFont(textFont(12))
        self.doc.setDefaultStyleSheet(f"body {{ color: {c['ink']}; }}")

        body = self._text if QtGui.Qt.mightBeRichText(self._text) else plainTextHtml(self._text, c)
        self.doc.setTextWidth(-1)
        self.doc.setHtml(body)
        if self.doc.idealWidth() > MAX_WIDTH:
            self.doc.setTextWidth(MAX_WIDTH)
        else:
            self.doc.setTextWidth(self.doc.idealWidth())

        w = int(self.doc.idealWidth() + 0.999) + 2 * PADDING_X + 2 * SHADOW
        h = int(self.doc.size().height() + 0.999) + 2 * PADDING_Y + 2 * SHADOW
        self.resize(w, h)

    def _place(self, pos: QPoint):
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1 << 15, 1 << 15)

        cardW, cardH = self.width() - 2 * SHADOW, self.height() - 2 * SHADOW
        x, y = pos.x() + CURSOR_OFFSET.x(), pos.y() + CURSOR_OFFSET.y()

        if x + cardW > area.right():
            x = max(area.left(), area.right() - cardW)
        if y + cardH > area.bottom():
            # 下方放不下就翻到光标上方
            y = pos.y() - cardH - 6
        y = max(area.top(), y)
        x = max(area.left(), x)

        self.move(x - SHADOW, y - SHADOW)

    # ---------------- 自动消失 ----------------

    def _cursorInside(self):
        owner = self._owner
        if owner is None:
            return True
        try:
            if not owner.isVisible():
                return False
            local = owner.mapFromGlobal(QCursor.pos())
        except RuntimeError:
            return False
        if not owner.rect().contains(local):
            return False
        return self._rect.isNull() or self._rect.contains(local)

    def _checkCursor(self):
        if not self._cursorInside():
            self._hide()

    def eventFilter(self, obj: QObject, e: QEvent):
        t = e.type()
        if obj is self._owner and t in (QEvent.Hide, QEvent.Leave):
            self._hide()
        elif t in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.Wheel,
                   QEvent.KeyPress, QEvent.WindowDeactivate, QEvent.FocusOut) \
                and obj is not self:
            if t != QEvent.FocusOut or obj is self._owner:
                self._hide()
        elif t == QEvent.ApplicationStateChange:
            if QGuiApplication.applicationState() != Qt.ApplicationActive:
                self._hide()
        elif t == QEvent.MouseMove and self.isVisible():
            self._checkCursor()
        return False

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        c = self._colors
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)

        card = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)

        # 柔和阴影: 由内向外一圈圈淡出的圆角矩形, 略向下偏移
        r, g, b, a = c['shadow']
        p.setPen(Qt.NoPen)
        steps = SHADOW
        for i in range(steps, 0, -1):
            k = 1 - i / (steps + 1)
            p.setBrush(QColor(r, g, b, max(1, int(a * k * k / steps * 2.2))))
            p.drawRoundedRect(card.adjusted(-i, -i + 2, i, i + 3), RADIUS + i, RADIUS + i)

        path = QPainterPath()
        path.addRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        p.setBrush(QColor(c['bg']))
        p.setPen(QColor(c['border']))
        p.drawPath(path)

        p.translate(card.left() + PADDING_X, card.top() + PADDING_Y)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        pal = QPalette(self.palette())
        pal.setColor(QPalette.Text, QColor(c['ink']))
        ctx.palette = pal
        self.doc.documentLayout().draw(p, ctx)
        p.end()
