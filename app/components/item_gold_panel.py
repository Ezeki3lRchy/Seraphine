"""
Live item value panel for the game info page: both teams' item value and the difference,
plus the five lanes (ally vs enemy) with the signed difference in the middle. Modes without
lanes (no positions from the game, e.g. ARAM) list each team's players sorted by item value
instead, without a per-row difference. Fed by signalBus.liveGameDataChanged
(app/lol/live_client.py); hidden while there is no live data, slides open / closed.
"""
import asyncio

from PySide6.QtCore import Qt, QRectF, QEvent, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPixmap, QPen
from PySide6.QtWidgets import QWidget, QSizePolicy

from app.common.signals import signalBus
from app.common.logger import logger
from app.components.design_tokens import numberFont, textFont, resultColor, tint
from app.components.rich_tooltip import RichToolTip
from app.components.paint import drawText, drawChampion
from app.components.scoreboard import cachedContent
from app.lol.live_client import resolveChampionId
from app.lol.tools import translatePositionKey

TAG = "ItemGoldPanel"

PAD = 10
ROW_H = 24
ROWS = 5
SUMMARY_W = 230
CONTENT_H = 2 * PAD + ROWS * ROW_H
ANIMATION_MS = 180
MIN_BAR_W = 7  # a lane bar for any non-zero difference reads as a bar, not a speck


def goldText(value, compact=False):
    value = int(round(value or 0))
    if compact and abs(value) >= 1000:
        return f"{value / 1000:.1f}k"
    return f"{value:,}"


def diffText(diff, compact=False):
    diff = int(round(diff or 0))
    sign = "+" if diff > 0 else "−" if diff < 0 else ""  # real minus sign
    return sign + goldText(abs(diff), compact)


def diffColor(diff, neutral):
    """The one colour rule: zero -> neutral, ahead -> win colour, behind -> lose colour"""
    diff = int(round(diff or 0))
    if diff == 0:
        return QColor(neutral)
    return resultColor('win' if diff > 0 else 'lose')


def tipOwnedBy(widget):
    tip = RichToolTip._instance
    return tip is not None and tip.isVisible() and tip._owner is widget


class ItemGoldPanel(QWidget):
    """Container that animates its height; the painting is done by `content`"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.content = ItemGoldContent(self)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(CONTENT_H)
        self.setVisible(False)

        self.ani = QVariantAnimation(self)
        self.ani.setDuration(ANIMATION_MS)
        self.ani.setEasingCurve(QEasingCurve.OutCubic)
        self.ani.valueChanged.connect(lambda v: self.setFixedHeight(int(v)))
        self.ani.finished.connect(self.__onAnimationFinished)

        signalBus.liveGameDataChanged.connect(self.setSummary)

    @property
    def summary(self):
        return self.content.summary

    def setSummary(self, summary):
        try:
            self.content.setSummary(summary)
            if summary is not None and (self.isHidden() or self.ani.endValue() == 0):
                if self.isHidden():
                    self.setFixedHeight(0)
                    self.setVisible(True)
                self.__animateTo(CONTENT_H)
            elif summary is None and not self.isHidden():
                self.__animateTo(0)
        except Exception as e:
            logger.error(f"item gold panel update failed: {e!r}", TAG)

    def __animateTo(self, height):
        self.ani.stop()
        self.ani.setStartValue(self.height())
        self.ani.setEndValue(height)
        self.ani.start()

    def __onAnimationFinished(self):
        if self.ani.endValue() == 0 and self.content.summary is None:
            self.setVisible(False)

    def resizeEvent(self, e):
        self.content.setGeometry(0, 0, self.width(), CONTENT_H)
        super().resizeEvent(e)


class ItemGoldContent(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.summary = None
        self.icons = {}          # championName -> QPixmap
        self._requested = set()  # championNames whose icon is loaded or loading
        self._tasks = set()      # keep icon tasks referenced until done
        self._version = 0
        self.tips = []
        self.setFixedHeight(CONTENT_H)

    # ---------------- data ----------------

    def setSummary(self, summary):
        self.summary = summary
        self._version += 1
        if summary is None:
            if tipOwnedBy(self):
                RichToolTip.hideText()
            return
        self.__loadIcons()
        self.update()

    def __loadIcons(self):
        players = self.summary['allyPlayers'] + self.summary['enemyPlayers']
        for name in {p.get('championName') for p in players}:
            if not name or name in self._requested:
                continue
            try:
                task = asyncio.get_running_loop().create_task(self.__loadIcon(name))
            except RuntimeError:  # no event loop (offscreen scripts)
                continue
            self._requested.add(name)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def __loadIcon(self, name):
        try:
            from app.lol.connector import connector
            manager = getattr(connector, 'manager', None)
            championId = resolveChampionId(name, getattr(manager, 'champs', None))
            pixmap = QPixmap(await connector.getChampionIcon(championId)) \
                if championId is not None else None
            if pixmap is None or pixmap.isNull():
                self._requested.discard(name)  # retry on a later update
                return
            self.icons[name] = pixmap
            self._version += 1
            self.update()
        except Exception as e:
            self._requested.discard(name)
            logger.warning(f"champion icon for {name!r} failed: {e!r}", TAG)

    # ---------------- painting ----------------

    def paintEvent(self, e):
        if self.summary is None:
            return
        try:
            p = QPainter(self)
            p.drawPixmap(0, 0, cachedContent(self, self.__draw, (self._version,)))
            p.end()
        except Exception as e:
            logger.error(f"item gold panel paint failed: {e!r}", TAG)

    def __draw(self, p, pal):
        s = self.summary
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(pal['hairline'], 1))
        p.setBrush(pal['sheet'])
        p.drawRoundedRect(rect, 8, 8)
        self.tips = []

        self.__drawSummary(p, pal, s)

        x = PAD + SUMMARY_W
        p.setPen(QPen(pal['hairline'], 1))
        p.drawLine(int(x), PAD, int(x), self.height() - PAD)

        left = x + 14
        geo = self.__rowGeometry(self.width() - PAD - 6 - left)
        if s['hasLanes']:
            scale = max([abs(l['diff']) for l in s['lanes']] + [1500])
            for i, lane in enumerate(s['lanes'][:ROWS]):
                row = QRectF(left, PAD + i * ROW_H, geo['width'], ROW_H)
                self.__drawLane(p, pal, row, geo, lane, scale)
        else:
            self.__drawRanked(p, pal, left, geo, s)

    def __drawSummary(self, p, pal, s):
        x, y, w = PAD + 6, PAD, SUMMARY_W - 22
        ink, muted = pal['ink'], pal['muted']

        drawText(p, QRectF(x, y, w, 16), self.tr("Item value").upper(), textFont(11, True),
                 muted)
        minutes, secs = divmod(int(s['gameTime']), 60)
        drawText(p, QRectF(x, y, w, 16), f"{minutes}:{secs:02d}", numberFont(12, 500), muted,
                 Qt.AlignRight | Qt.AlignVCenter)

        drawText(p, QRectF(x, y + 17, w, 38), diffText(s['diff']), numberFont(30, 600),
                 diffColor(s['diff'], ink))

        win, lose = resultColor('win'), resultColor('lose')
        total = max(s['allyTotal'] + s['enemyTotal'], 1)
        bar = QRectF(x, y + 60, w, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(tint(lose, 200))
        p.drawRoundedRect(bar, 2.5, 2.5)
        if s['allyTotal']:
            p.save()
            p.setClipRect(QRectF(bar.left(), bar.top(), bar.width() * s['allyTotal'] / total,
                                 bar.height()))
            p.setBrush(win)
            p.drawRoundedRect(bar, 2.5, 2.5)
            p.restore()

        if s['localKnown']:
            labels = (self.tr("Your team"), self.tr("Enemy team"))
        else:
            labels = (self.tr("Blue team"), self.tr("Red team"))
        for j, (label, value, color) in enumerate(((labels[0], s['allyTotal'], win),
                                                   (labels[1], s['enemyTotal'], lose))):
            r = QRectF(x, y + 72 + j * 20, w, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QRectF(r.left(), r.center().y() - 4, 8, 8))
            drawText(p, r.adjusted(14, 0, 0, 0), label, textFont(13), ink)
            drawText(p, r, goldText(value), numberFont(15, 600), ink,
                     Qt.AlignRight | Qt.AlignVCenter)

        lines = [self.tr("Item value: total cost of the items each player holds "
                         "(not gold earned)."),
                 self.tr("Enemy items are what the scoreboard shows (last seen). "
                         "Unspent gold of other players is not available.")]
        if s.get('currentGold') is not None:
            lines.append(self.tr("Your unspent gold: {0}").format(goldText(s['currentGold'])))
        if not s['localKnown']:
            lines.append(self.tr("You are not on either team (spectating?): "
                                 "the difference is blue minus red."))
        self.tips.append((QRectF(0, 0, PAD + SUMMARY_W, self.height()).toRect(),
                          "\n".join(lines)))

    def __rowGeometry(self, width):
        labelW, iconS, valueW, diffW = 46, 20, 52, 70
        side = iconS + 6 + 4 + valueW + 10
        nameW = max(min((width - labelW - diffW - 2 * side - 2 * 60) / 2, 120), 56)
        half = max((width - labelW - diffW - 2 * (side + nameW)) / 2, 24)
        return {'labelW': labelW, 'iconS': iconS, 'valueW': valueW, 'diffW': diffW,
                'nameW': nameW, 'half': half,
                'width': labelW + 2 * (side + nameW) + 2 * half + diffW}

    def __drawSide(self, p, pal, row, g, player, x, mirrored):
        """icon + name + value; mirrored puts the value first (enemy side). Returns end x"""
        ink, muted = pal['ink'], pal['muted']
        cy = row.center().y()
        iconS, nameW, valueW = g['iconS'], g['nameW'], g['valueW']
        value = goldText(player['value']) if player else ""
        if mirrored:
            drawText(p, QRectF(x, row.top(), valueW, ROW_H), value, numberFont(14, 600), ink,
                     Qt.AlignLeft | Qt.AlignVCenter)
            x += valueW + 10
        self.__drawIcon(p, pal, QRectF(x, cy - iconS / 2, iconS, iconS), player)
        x += iconS + 6
        self.__drawName(p, QRectF(x, row.top(), nameW, ROW_H), player, ink, muted)
        x += nameW + 4
        if not mirrored:
            drawText(p, QRectF(x, row.top(), valueW, ROW_H), value, numberFont(14, 600), ink,
                     Qt.AlignRight | Qt.AlignVCenter)
            x += valueW + 10
        return x

    def __drawLane(self, p, pal, row, g, lane, scale):
        ink, muted = pal['ink'], pal['muted']
        ally, enemy = lane['ally'], lane['enemy']

        drawText(p, QRectF(row.left(), row.top(), g['labelW'], ROW_H),
                 translatePositionKey(lane['position']) or "", textFont(12), muted)
        x = self.__drawSide(p, pal, row, g, ally, row.left() + g['labelW'], False)

        # centre: bar half towards the ally, signed difference, bar half towards the enemy
        cy = row.center().y()
        half, diffW = g['half'], g['diffW']
        leftBar = QRectF(x, cy - 2.5, half, 5)
        rightBar = QRectF(x + half + diffW, cy - 2.5, half, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(pal['hairline'])
        p.drawRoundedRect(leftBar, 2.5, 2.5)
        p.drawRoundedRect(rightBar, 2.5, 2.5)
        if lane['diff']:
            length = min(max(abs(lane['diff']) / scale * half, MIN_BAR_W), half)
            p.setBrush(diffColor(lane['diff'], muted))
            if lane['diff'] > 0:  # ahead: grows towards the ally
                p.drawRoundedRect(QRectF(leftBar.right() - length, leftBar.top(), length, 5),
                                  2.5, 2.5)
            else:
                p.drawRoundedRect(QRectF(rightBar.left(), rightBar.top(), length, 5), 2.5, 2.5)
        drawText(p, QRectF(x + half, row.top(), diffW, ROW_H), diffText(lane['diff']),
                 numberFont(15, 600), diffColor(lane['diff'], ink), Qt.AlignCenter)

        self.__drawSide(p, pal, row, g, enemy, x + 2 * half + diffW + 10, True)

        tip = [self.__who(ally), self.tr("vs") + " " + self.__who(enemy)]
        tip += self.__deadLines(ally, enemy)
        self.tips.append((row.toRect(), "\n".join(tip)))

    def __drawRanked(self, p, pal, left, g, s):
        """No lanes: each team's players by item value, side by side, no per-row diff"""
        x0 = left + g['labelW']
        centre = None
        for i in range(ROWS):
            row = QRectF(left, PAD + i * ROW_H, g['width'], ROW_H)
            ally = s['allyPlayers'][i] if i < len(s['allyPlayers']) else None
            enemy = s['enemyPlayers'][i] if i < len(s['enemyPlayers']) else None
            x = self.__drawSide(p, pal, row, g, ally, x0, False)
            centre = QRectF(x, PAD, 2 * g['half'] + g['diffW'], ROWS * ROW_H)
            enemyX = x + 2 * g['half'] + g['diffW'] + 10
            self.__drawSide(p, pal, row, g, enemy, enemyX, True)
            for player, rect in ((ally, QRectF(left, row.top(), x - left, ROW_H)),
                                 (enemy, QRectF(enemyX, row.top(), row.right() - enemyX,
                                                ROW_H))):
                if player:
                    self.tips.append((rect.toRect(),
                                      "\n".join([self.__who(player)] + self.__deadLines(player))))

        if centre is not None:
            font = textFont(12)
            fm = QFontMetrics(font)
            lines = [self.tr("No lanes in this mode"), self.tr("Players sorted by item value")]
            for j, line in enumerate(lines):
                r = QRectF(centre.left(), centre.center().y() - 19 + j * 20, centre.width(), 18)
                drawText(p, r, fm.elidedText(line, Qt.ElideRight, int(r.width())), font,
                         pal['muted'], Qt.AlignCenter)

    def __who(self, player):
        if not player:
            return "—"
        return f"{player['championName']} ({player['name']}): {goldText(player['value'])}"

    def __deadLines(self, *players):
        return [self.tr("{0} is dead (dimmed)").format(p['championName'])
                for p in players if p and p.get('isDead')]

    def __drawName(self, p, rect, player, ink, muted):
        if not player:
            return
        font = textFont(13)
        text = QFontMetrics(font).elidedText(player['championName'], Qt.ElideRight,
                                             int(rect.width()))
        drawText(p, rect, text, font, muted if player.get('isDead') else ink,
                 Qt.AlignLeft | Qt.AlignVCenter)

    def __drawIcon(self, p, pal, rect, player):
        if not player:
            return
        pixmap = self.icons.get(player.get('championName'))
        p.save()
        if player.get('isDead'):
            p.setOpacity(0.4)
        if pixmap is None:
            p.setPen(Qt.NoPen)
            p.setBrush(pal['hairline'])
            p.drawEllipse(rect)
            drawText(p, rect, (player.get('championName') or "?")[:1], textFont(11, True),
                     pal['muted'], Qt.AlignCenter)
        else:
            drawChampion(p, pixmap, rect, pal['gold'], ring=1, crop=4)
        p.restore()

    # ---------------- interaction ----------------

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            for rect, text in self.tips:
                if rect.contains(e.pos()) and text:
                    RichToolTip.showText(e.globalPos(), text, self, rect)
                    return True
            if tipOwnedBy(self):
                RichToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)
