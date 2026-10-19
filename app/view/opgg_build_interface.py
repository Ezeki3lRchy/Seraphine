from PySide6.QtWidgets import QHBoxLayout, QWidget, QFrame, QVBoxLayout
from PySide6.QtCore import Qt
from qasync import asyncSlot

from app.components.transparent_button import PrimaryButton
from app.components.opgg_build_widgets import (PaintedCard, ChampionHeaderCard, CountersCard,
                                               OptionTableCard, RunePageTable, RuneTreeView,
                                               SkillsCard, LateItemsCard, TrendsCard,
                                               AugmentsCard, DuoPartnersCard, NoticeCard,
                                               trText, PAD)
from app.common.style_sheet import StyleSheet
from app.common.qfluentwidgets import SmoothScrollArea
from app.common.signals import signalBus
from app.lol.connector import connector


POSITION_NAMES = {'TOP': "Top", 'JUNGLE': "Jungle", 'MID': "Mid", 'ADC': "Bottom",
                  'SUPPORT': "Support"}


def optionRows(entries, limit, minPickRate=0.0, chain=False, spells=False):
    """解析后的出装 / 召唤师技能数据 -> OptionTableCard 的行; 选择率过低的方案 (第一个除外) 不展示"""
    rows = []
    for i, entry in enumerate(entries or []):
        if len(rows) >= limit:
            break
        if i and (entry.get('pickRate') or 0) < minPickRate:
            break

        ids = entry.get('ids') or [None] * len(entry['icons'])
        icons = [{'icon': icon, 'itemId': None if spells else id}
                 for icon, id in zip(entry['icons'], ids)]
        rows.append({'icons': icons, 'chain': chain, 'pickRate': entry.get('pickRate') or 0,
                     'win': entry.get('win') or 0, 'play': entry.get('play') or 0})

    return rows


class BuildInterface(QFrame):
    """
    英雄详情 (出装) 页, 从上到下:

    概览 → 对线克制 → 符文 → 召唤师技能 → 技能加点 → 出门装 → 鞋子 → 核心出装 → 后期装备 → 趋势;
    斗魂竞技场另有强化符文与双人搭档. 各卡片在没有数据时自动隐藏
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.championId = None
        self.vBoxLayout = QVBoxLayout(self)

        self.scrollArea = SmoothScrollArea()
        self.scrollWidget = QWidget()
        self.scrollLayout = QVBoxLayout()

        self.titleBar = ChampionHeaderCard()
        self.noticeCard = NoticeCard()
        self.championCounters = CountersCard()
        self.championPerks = ChampionPerksWidget()
        self.summonerSpells = OptionTableCard()
        self.championSkills = SkillsCard()
        self.starterItems = OptionTableCard()
        self.bootsItems = OptionTableCard()
        self.coreItems = OptionTableCard()
        self.lateItems = LateItemsCard()
        self.championAugments = AugmentsCard()
        self.championSynergies = DuoPartnersCard()
        self.championTrends = TrendsCard()

        self.sections = [self.championCounters, self.championPerks, self.summonerSpells,
                         self.championSkills, self.starterItems, self.bootsItems,
                         self.coreItems, self.lateItems, self.championAugments,
                         self.championSynergies, self.championTrends]

        self.__initWidget()
        self.__initLayout()

        StyleSheet.OPGG_BUILD_INTERFACE.apply(self)

    def __initWidget(self):
        # 点击克制关系 / 双人搭档里的英雄, 跳到它的出装
        for card in (self.championCounters, self.championSynergies):
            card.championClicked.connect(
                lambda id: signalBus.toOpggBuildInterface.emit(id, "", ""))

        self.noticeCard.setText(self.tr("OP.GG has no build data for this mode yet"))

    def __initLayout(self):
        self.scrollArea.setObjectName("scrollArea")
        self.scrollWidget.setObjectName("scrollWidget")

        self.scrollLayout.setAlignment(Qt.AlignTop)
        self.scrollWidget.setLayout(self.scrollLayout)
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setViewportMargins(0, 0, 15, 0)
        self.scrollWidget.setContentsMargins(0, 0, 0, 0)

        self.scrollLayout.addWidget(self.titleBar)
        self.scrollLayout.addWidget(self.noticeCard)
        for widget in self.sections:
            self.scrollLayout.addWidget(widget)

        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.scrollLayout.setContentsMargins(0, 0, 0, 8)
        self.scrollLayout.setSpacing(8)
        self.vBoxLayout.addWidget(self.scrollArea)

    def setCurrentChampionId(self, id):
        self.championId = id

    def getCurrentChampionId(self):
        return self.championId

    def updateInterface(self, data: dict):
        summary = data.get('summary') or {}
        position = summary.get('position')
        positionText = trText("OpggWindow", POSITION_NAMES[position]) \
            if position in POSITION_NAMES else ""

        items = data.get('items') or {}

        self.titleBar.setData(summary, positionText)
        self.championCounters.setData(data.get('counters'))
        self.championPerks.updateWidget(data.get('perks'), summary)
        self.summonerSpells.setData(
            optionRows(data.get('summonerSpells'), 2, 0.01, spells=True),
            trText("SummonerSpellsWidget", "Summoner Spells", "Summoner spells"))
        self.championSkills.setData(data.get('championSkills'))
        self.starterItems.setData(optionRows(items.get('startItems'), 3, 0.01),
                                  self.tr("Starter items"))
        self.bootsItems.setData(optionRows(items.get('boots'), 3, 0.01), self.tr("Boots"))
        self.coreItems.setData(optionRows(items.get('coreItems'), 5, chain=True),
                               self.tr("Core builds"))
        self.lateItems.setData(items.get('lateItems'))
        self.championAugments.setData(data.get('augments'))
        self.championSynergies.setData(data.get('synergies'))
        self.championTrends.setData(data.get('trends'))

        # 该模式没有任何出装数据 (如极限闪击下线期间) 时给个提示, 而不是一片空白
        self.noticeCard.setVisible(all(w.isHidden() for w in self.sections))

        self.scrollArea.delegate.vScrollBar.resetValue(0)
        self.scrollArea.verticalScrollBar().setSliderPosition(0)


class ChampionPerksWidget(PaintedCard):
    """
    符文: 标题行 (右侧 "设为当前符文页") + 符文页候选表 + 选中页的符文树 (含属性碎片)
    """

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.headerLayout = QHBoxLayout()
        self.setRuneButton = PrimaryButton(
            trText("ChampionPerksWidget", "Set Rune Page", "Set rune page"))
        self.pageTable = RunePageTable()
        self.perksView = RuneTreeView()

        self.data = None
        self.summary = None
        self.selectedIndex = 0

        self.__initWidget()
        self.__initLayout()

        self.setVisible(False)

    def __initWidget(self):
        self.pageTable.pageSelected.connect(self.__onPageSelected)
        self.setRuneButton.clicked.connect(self.__onSetRunePageButtonClicked)

    def __initLayout(self):
        self.headerLayout.setContentsMargins(0, 0, PAD, 0)
        self.headerLayout.addStretch(1)
        self.headerLayout.addWidget(self.setRuneButton)

        self.vBoxLayout.setContentsMargins(0, 8, 0, 14)
        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.addLayout(self.headerLayout)
        self.vBoxLayout.addSpacing(6)
        self.vBoxLayout.addWidget(self.pageTable)
        self.vBoxLayout.addSpacing(12)
        self.vBoxLayout.addWidget(self.perksView)

    def drawContent(self, p, pal, dark):
        self.drawTitle(p, pal, self.tr("Runes"))

    def updateWidget(self, data: list, summary: dict):
        if not data:
            self.setVisible(False)
            return

        self.data = data[:3]
        self.summary = summary
        self.selectedIndex = 0

        self.pageTable.setData([self.__pageRow(d) for d in self.data])
        self.__showPage(0)

        self.setVisible(True)

    @staticmethod
    def __runeName(id):
        try:
            return connector.manager.getRuneName(id)
        except Exception:
            return ""

    def __pageRow(self, d):
        perks, icons = d['perks'], d['icons']
        # perks: 主系 4 个 (基石 + 3) + 副系 2 个 + 属性碎片 3 个
        minors = [{'icon': icon, 'tip': self.__runeName(id)}
                  for id, icon in zip(perks[1:6], icons[1:6])]
        keystone = self.__runeName(perks[0]) if perks else ""
        return {
            'keystoneIcon': icons[0] if icons else None,
            'keystoneTip': keystone,
            'minorIcons': minors[:3],
            'secondaryIcons': minors[3:5],
            'title': keystone,
            'pickRate': d['pickRate'],
            'win': d['win'],
            'play': d['play'],
        }

    def __showPage(self, index):
        data = self.data[index]
        styles = connector.manager.getPerkStyles() or {}
        self.perksView.setData(styles.get(data['primaryId']), styles.get(data['secondaryId']),
                               data['perks'])

    def __onPageSelected(self, index):
        self.selectedIndex = index
        self.__showPage(index)

    @asyncSlot(bool)
    async def __onSetRunePageButtonClicked(self, _):
        data = self.data[self.selectedIndex]
        name = "Seraphine" + self.tr(": ") + self.summary['name']

        await connector.deleteCurrentRunePage()
        await connector.createRunePage(
            name, data['primaryId'], data['secondaryId'], data['perks'])
