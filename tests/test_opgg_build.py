"""
OP.GG champion build parsing (app/lol/opgg.py) and the build-page widgets.

Fixtures are trimmed real responses of lol-api-champion.op.gg (Yunara bottom / ARAM / Arena,
and an empty Nexus Blitz build). The LCU connector is replaced by a fake, so no client or
network is needed.
"""
import asyncio
import json
import os
import unittest
from unittest import mock

from app.lol import opgg as opggModule
from app.lol.opgg import OpggDataParser

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class FakeManager:
    def getChampionNameById(self, id):
        return f"Champion {id}"

    def getAugmentsName(self, id):
        return f"Augment {id}"


class FakeConnector:
    manager = FakeManager()

    async def getChampionIcon(self, id):
        return f"champion/{id}.png"

    async def getItemIcon(self, id):
        return f"item/{id}.png"

    async def getRuneIcon(self, id):
        return f"rune/{id}.png"

    async def getSummonerSpellIcon(self, id):
        return f"spell/{id}.png"

    async def getAugmentIcon(self, id):
        return f"augment/{id}.png"


def parse(raw, position):
    with mock.patch.object(opggModule, "connector", FakeConnector()):
        return asyncio.run(OpggDataParser.parseOtherChampionBuild(raw, position))


def parseArena(raw):
    with mock.patch.object(opggModule, "connector", FakeConnector()):
        return asyncio.run(OpggDataParser.parseArenaChampionBuild(raw))


class RankedBuildTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.raw = load("opgg_build_ranked_adc.json")
        cls.res = parse(cls.raw, "ADC")

    def test_summary_uses_position_stats(self):
        # 回归: 之前对线克制的循环覆盖了 winRate, 概览显示的是最后一个对位英雄的胜率
        stats = self.raw['data']['summary']['positions'][0]['stats']
        summary = self.res['summary']
        self.assertAlmostEqual(summary['winRate'], stats['win_rate'])
        self.assertAlmostEqual(summary['pickRate'], stats['pick_rate'])
        self.assertAlmostEqual(summary['banRate'], stats['ban_rate'])
        self.assertEqual(summary['play'], stats['play'])
        self.assertEqual((summary['tier'], summary['rank']), (2, 11))
        self.assertEqual(summary['rankTotal'], 40)

    def test_skill_priority_paired_with_its_own_sequence(self):
        masteries = self.raw['data']['skill_masteries']
        skills = self.res['championSkills']

        # 顶层: 最热门的主升顺序, 统计与加点顺序都来自它自己
        self.assertEqual(skills['masteries'], masteries[0]['ids'])
        self.assertEqual(skills['play'], masteries[0]['play'])
        self.assertEqual(skills['order'], masteries[0]['builds'][0]['order'])
        self.assertEqual(skills['orderPlay'], masteries[0]['builds'][0]['play'])
        self.assertEqual(len(skills['order']), 15)

        # 选择率 < 5% 的主升顺序不作为候选
        self.assertEqual([o['masteries'] for o in skills['options']],
                         [m['ids'] for m in masteries if m['pick_rate'] >= 0.05])
        for option, mastery in zip(skills['options'], masteries):
            self.assertEqual(option['order'], mastery['builds'][0]['order'])

    def test_counters_split_sorted_and_sample_floor(self):
        raw = self.raw['data']['counters']
        counters = self.res['counters']
        strong, weak = counters['strongAgainst'], counters['weakAgainst']

        self.assertEqual(len(strong) + len(weak), len(raw))
        self.assertTrue(all(c['winRate'] >= 0.5 for c in strong))
        self.assertTrue(all(c['winRate'] < 0.5 for c in weak))
        self.assertEqual([c['winRate'] for c in strong],
                         sorted((c['winRate'] for c in strong), reverse=True))
        self.assertEqual([c['winRate'] for c in weak], sorted(c['winRate'] for c in weak))

        total = sum(c['play'] for c in raw)
        self.assertEqual(counters['totalGames'], total)
        self.assertEqual(counters['minGames'], max(int(total * 0.01), 30))

    def test_late_items_skip_top_core_and_boots(self):
        items = self.res['items']
        late = items['lateItems']
        excluded = set(self.raw['data']['core_items'][0]['ids'])
        excluded |= {id for b in self.raw['data']['boots'] for id in b['ids']}

        self.assertLessEqual(len(late), 10)
        self.assertTrue(late)
        self.assertFalse({i['id'] for i in late} & excluded)
        self.assertEqual([i['pickRate'] for i in late],
                         sorted((i['pickRate'] for i in late), reverse=True))
        self.assertTrue(all({'icon', 'play', 'win', 'pickRate'} <= set(i) for i in late))

    def test_item_sets_keep_ids_and_legacy_keys(self):
        items = self.res['items']
        self.assertEqual(items['coreItems'][0]['ids'], self.raw['data']['core_items'][0]['ids'])
        self.assertEqual(len(items['coreItems']), 5)
        self.assertEqual(len(items['boots']), 3)
        self.assertEqual(len(items['startItems']), 3)
        self.assertTrue(all(isinstance(icon, str) for icon in items['lastItems']))

    def test_trends_oldest_first(self):
        trends = self.res['trends']
        raw = self.raw['data']['trends']['win']
        self.assertEqual([t['version'] for t in trends['win']],
                         [t['version'] for t in reversed(raw)])
        self.assertEqual(trends['win'][-1]['rate'], raw[0]['rate'])
        self.assertEqual([g['minute'] for g in trends['gameLengths']], [0, 25, 30, 35, 40])

    def test_rune_pages(self):
        perks = self.res['perks']
        self.assertEqual(len(perks[0]['perks']), 9)
        self.assertEqual(len(perks[0]['icons']), 9)


class OtherModesTest(unittest.TestCase):

    def test_aram_nulls(self):
        res = parse(load("opgg_build_aram.json"), "none")
        summary = res['summary']
        self.assertIsNone(summary['banRate'])
        self.assertEqual(summary['tier'], 3)
        self.assertEqual(summary['rankTotal'], 173)
        self.assertEqual(res['counters']['strongAgainst'], [])
        self.assertEqual(res['counters']['weakAgainst'], [])
        self.assertTrue(res['championSkills']['options'])
        # 大乱斗没有禁用, 版本趋势里的禁用率全是 0
        self.assertTrue(all(t['rate'] == 0 for t in res['trends']['ban']))

    def test_empty_mode_does_not_raise(self):
        res = parse(load("opgg_build_nexus_blitz_empty.json"), "none")
        self.assertIsNone(res['championSkills'])
        self.assertEqual(res['perks'], [])
        self.assertEqual(res['summonerSpells'], [])
        self.assertEqual(res['items']['coreItems'], [])
        self.assertEqual(res['items']['lateItems'], [])
        self.assertIsNone(res['summary']['winRate'])

    def test_arena(self):
        raw = load("opgg_build_arena.json")
        res = parseArena(raw)
        groups = sorted(raw['data']['augment_group'], key=lambda g: g['rarity'])
        self.assertEqual([[a['id'] for a in g] for g in res['augments']],
                         [[a['id'] for a in g['augments']] for g in groups])
        late = res['items']['lateItems']
        self.assertEqual([i['pickRate'] for i in late],
                         sorted((i['pickRate'] for i in late), reverse=True))
        self.assertIsNotNone(res['summary']['firstRate'])
        self.assertEqual(res['championSkills']['masteries'],
                         raw['data']['skill_masteries'][0]['ids'])


class BuildWidgetsTest(unittest.TestCase):
    """自绘卡片: 数据过滤 / 显隐 / 命中区域 (离屏渲染)"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.res = parse(load("opgg_build_ranked_adc.json"), "ADC")

    def render(self, widget, width=603):
        widget.resize(width, widget.height())
        widget.grab()

    def test_option_rows(self):
        from app.view.opgg_build_interface import optionRows

        items = self.res['items']
        rows = optionRows(items['startItems'], 3, 0.01)
        # 第三个出门装选择率 < 1%, 不展示
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['icons'][0]['itemId'], items['startItems'][0]['ids'][0])

        spells = optionRows(self.res['summonerSpells'], 2, 0.01, spells=True)
        self.assertEqual(len(spells), 2)
        self.assertTrue(all(icon['itemId'] is None for row in spells for icon in row['icons']))

        core = optionRows(items['coreItems'], 5, chain=True)
        self.assertEqual(len(core), 5)
        self.assertTrue(all(row['chain'] for row in core))

    def test_counters_card_filters_small_samples(self):
        from app.components.opgg_build_widgets import CountersCard

        card = CountersCard()
        card.setData(self.res['counters'])
        minGames = self.res['counters']['minGames']
        self.assertFalse(card.isHidden())
        self.assertTrue(all(c['play'] >= minGames for c in card.strong + card.weak))
        self.assertLessEqual(len(card.strong), 5)

        clicked = []
        card.championClicked.connect(clicked.append)
        self.render(card)
        self.assertEqual(len(card._links), len(card.strong) + len(card.weak))
        card._links[0][1]()
        self.assertEqual(clicked, [card.strong[0]['championId']])

        card.setData({'strongAgainst': [], 'weakAgainst': [], 'minGames': 30})
        self.assertTrue(card.isHidden())

    def test_header_hides_missing_stats(self):
        from app.components.opgg_build_widgets import ChampionHeaderCard

        card = ChampionHeaderCard()
        card.setData(self.res['summary'], "Bottom")
        self.assertEqual(len(card.stats), 5)
        self.render(card)

        aram = parse(load("opgg_build_aram.json"), "none")['summary']
        card.setData(aram)
        self.assertEqual(len(card.stats), 4)   # 无禁用率

        empty = parse(load("opgg_build_nexus_blitz_empty.json"), "none")['summary']
        card.setData(empty)
        self.assertEqual(card.stats, [])
        self.render(card)

    def test_skills_card_selection(self):
        from app.components.opgg_build_widgets import SkillsCard

        card = SkillsCard()
        card.setData(self.res['championSkills'])
        self.render(card)
        self.assertEqual(len(card._links), len(self.res['championSkills']['options']))
        card._links[-1][1]()
        self.assertEqual(card.selected, len(self.res['championSkills']['options']) - 1)

        card.setData(None)
        self.assertTrue(card.isHidden())

    def test_trends_and_late_items_render(self):
        from app.components.opgg_build_widgets import TrendsCard, LateItemsCard

        trends = TrendsCard()
        trends.setData(self.res['trends'])
        self.render(trends)
        self.assertFalse(trends.isHidden())
        self.assertTrue(trends._tips)

        late = LateItemsCard()
        late.setData(self.res['items']['lateItems'])
        self.render(late)
        self.assertEqual(len(late._tips), len(self.res['items']['lateItems']))


if __name__ == '__main__':
    unittest.main()
