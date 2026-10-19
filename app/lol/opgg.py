import aiohttp
from async_lru import alru_cache

from app.lol.connector import connector

TAG = "opgg"


class Opgg:
    def __init__(self):
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession("https://lol-api-champion.op.gg")

    async def close(self):
        if self.session:
            await self.session.close()

    @alru_cache(maxsize=512)
    async def __fetchTierList(self, region, mode, tier):
        url = f"/api/{region}/champions/{mode}"
        params = {"tier": tier}

        return await self.__get(url, params)

    @alru_cache(maxsize=512)
    async def __fetchChampionBuild(self, region, mode, championId, position, tier):
        if mode != 'arena':
            url = f"/api/{region}/champions/{mode}/{championId}/{position}"
        else:
            url = f"/api/{region}/champions/{mode}/{championId}"

        params = {"tier": tier}

        return await self.__get(url, params)

    @alru_cache(maxsize=512)
    async def getChampionBuild(self, region, mode, championId, position, tier):
        positions = await self.getChampionPositions(region, championId, tier)
        if positions and position not in positions and mode == 'ranked':
            position = positions[0]

        raw = await self.__fetchChampionBuild(region, mode, championId, position, tier)

        if mode != 'arena':
            res = await OpggDataParser.parseOtherChampionBuild(raw, position)
        else:
            res = await OpggDataParser.parseArenaChampionBuild(raw)

        return {
            'data': res,
            'version': raw['meta']['version'],
            'mode': mode,
        }

    @alru_cache(maxsize=512)
    async def getTierList(self, region, mode, tier):
        raw = await self.__fetchTierList(region, mode, tier)

        version = raw['meta']['version']

        if mode == 'ranked':
            res = await OpggDataParser.parseRankedTierList(raw)
        else:
            res = await OpggDataParser.parseOtherTierList(raw)

        return {
            'data': res,
            'version': version
        }

    @alru_cache(maxsize=512)
    async def getChampionPositions(self, region, championId, tier):
        # 这个调用因为有 cache，所以还是挺快的
        data = await self.__fetchTierList(region, "ranked", tier)

        for item in data['data']:
            if item['id'] == championId:
                return [p['name'] for p in item['positions']]

        return []

    async def __get(self, url, params=None):
        res = await self.session.get(url, params=params, proxy=None)
        return await res.json()


class OpggDataParser:

    @staticmethod
    async def parseRankedTierList(data):
        '''
        召唤师峡谷模式下的原始梯队数据，是所有英雄所有位置一起返回的

        在此函数内按照分路位置将它们分开
        '''

        data = data['data']
        res = {p: []
               for p in ['TOP', 'JUNGLE', 'MID', 'ADC', 'SUPPORT']}

        for item in data:
            championId = item['id']
            name = connector.manager.getChampionNameById(championId)
            icon = await connector.getChampionIcon(championId)

            for p in item['positions']:
                position = p['name']

                stats = p['stats']
                tier = stats['tier_data']

                counters = [{
                    'championId': c['champion_id'],
                    'icon': await connector.getChampionIcon(c['champion_id'])
                } for c in p['counters']]

                res[position].append({
                    'championId': championId,
                    'name': name,
                    'icon': icon,
                    'winRate': stats.get('win_rate'),
                    'pickRate': stats.get('pick_rate'),
                    'banRate': stats.get('ban_rate'),
                    'kda': stats.get('kda'),
                    'tier': tier.get('tier'),
                    'rank': tier.get('rank'),
                    'position': position,
                    'counters': counters,
                })

        # 排名 / 梯队是乱的，所以排个序
        for tier in res.values():
            tier.sort(key=lambda x: x['rank'])

        return res

    @staticmethod
    async def parseOtherTierList(data):
        '''
        处理其他模式下的原始梯队数据
        '''

        data = data['data']
        res = []

        for item in data:
            stats = item['average_stats']

            if stats == None:
                continue

            if stats.get('rank') == None:
                continue

            championId = item['id']
            name = connector.manager.getChampionNameById(championId)
            icon = await connector.getChampionIcon(championId)

            res.append({
                'championId': championId,
                'name': name,
                'icon': icon,
                'winRate': stats.get('win_rate'),
                'pickRate': stats.get('pick_rate'),
                'banRate': stats.get('ban_rate'),
                'kda': stats.get('kda'),
                'tier': stats.get('tier'),
                'rank': stats.get('rank'),
                "position": None,
                'counters': [],
            })

        return sorted(res, key=lambda x: x['rank'])

    @staticmethod
    async def parseOtherChampionBuild(data, position):
        data = data['data']

        summary = data['summary']
        championId = summary['id']
        icon = await connector.getChampionIcon(championId)
        name = connector.manager.getChampionNameById(championId)

        # 非排位模式 (大乱斗等) 的 positions 为 null; 找不到对应分路时也退回到平均数据
        stats = None
        if position != 'none':
            for p in summary.get('positions') or []:
                if p['name'] == position:
                    stats = p['stats']
                    break

        if stats is not None:
            tierData: dict = stats.get('tier_data') or {}
            tier = tierData.get("tier")
            rank = tierData.get("rank")
        else:
            stats = summary.get('average_stats') or {}
            tier = stats.get("tier")
            rank = stats.get("rank")

        trends = data.get('trends') or {}

        summonerSpells = []
        for s in data.get('summoner_spells') or []:
            icons = [await connector.getSummonerSpellIcon(id)
                     for id in s['ids']]

            summonerSpells.append({
                'ids': s['ids'],
                'icons': icons,
                'win': s['win'],
                'play': s['play'],
                'pickRate': s['pick_rate']
            })

        items = await OpggDataParser.parseItems(data)

        counters = await OpggDataParser.parseCounters(data.get('counters'))

        perks = [{
            'primaryId': (mainId := perk['primary_page_id']),
            "primaryIcon": await connector.getRuneIcon(mainId),
            'secondaryId': (subId := perk['secondary_page_id']),
            "secondaryIcon": await connector.getRuneIcon(subId),
            'perks': (perkIds := perk['primary_rune_ids']+perk['secondary_rune_ids']+perk['stat_mod_ids']),
            "icons": [await connector.getRuneIcon(id) for id in perkIds],
            'play': perk['play'],
            'win': perk['win'],
            'pickRate': perk['pick_rate'],
        } for perk in data.get('runes') or []
        ]

        return {
            "summary": {
                'name': name,
                'championId': championId,
                'icon': icon,
                'position': position,
                'winRate': stats.get('win_rate'),
                'pickRate': stats.get('pick_rate'),
                'banRate': stats.get('ban_rate'),
                'kda': stats.get('kda'),
                'play': stats.get('play'),
                'tier': tier,
                'rank': rank,
                # 同分路 (非排位模式下为全部) 参与排名的英雄数
                'rankTotal': trends.get('total_position_rank'),
            },
            "summonerSpells": summonerSpells,
            "championSkills": OpggDataParser.parseSkills(data),
            "items": items,
            "counters": counters,
            "perks": perks,
            "trends": OpggDataParser.parseTrends(data),
        }

    @staticmethod
    def parseSkills(data):
        '''
        技能加点: 主升顺序 (skill_masteries) 及其最常见的完整 15 级加点顺序

        顶层的 masteries / order / play / win / pickRate 为最热门的主升顺序 (兼容旧结构),
        `options` 为选择率不低于 5% 的主升顺序 (至多 3 个), 每个都带着自己的加点顺序
        '''
        options = []
        for i, m in enumerate(data.get('skill_masteries') or []):
            if len(options) >= 3 or (i and (m.get('pick_rate') or 0) < 0.05):
                break

            builds = m.get('builds') or []
            sequence = builds[0] if builds else {}
            options.append({
                'masteries': m['ids'],
                'order': sequence.get('order') or [],
                'play': m['play'],
                'win': m['win'],
                'pickRate': m['pick_rate'],
                'orderPlay': sequence.get('play') or 0,
                'orderWin': sequence.get('win') or 0,
            })

        # 没有主升数据时, 退回到最热门的加点顺序
        if not options and (skills := data.get('skills')):
            s = skills[0]
            options.append({
                'masteries': [],
                'order': s['order'],
                'play': s['play'],
                'win': s['win'],
                'pickRate': s['pick_rate'],
                'orderPlay': s['play'],
                'orderWin': s['win'],
            })

        if not options:
            return None

        return {**options[0], 'options': options}

    @staticmethod
    async def parseItemSets(entries, limit, arena=False):
        res = []
        for i in (entries or [])[:limit]:
            item = {
                "ids": i['ids'],
                "icons": [await connector.getItemIcon(id) for id in i['ids']],
                "play": i['play'],
                "win": i['win'],
                'pickRate': i['pick_rate'],
            }

            if arena and i['play']:
                item["averatePlace"] = i['total_place'] / i['play']
                item["firstRate"] = i['first_place'] / i['play']

            res.append(item)

        return res

    @staticmethod
    async def parseItems(data, arena=False):
        '''
        出门装 / 鞋子 / 核心三件套, 以及后期装备

        `lateItems`: 终局装备 (last_items, 按选择率排序) 中去掉最热门核心三件套与鞋子后的前 10 件,
        即 "核心装之后还会出什么". OP.GG 接口不区分第 4 / 5 / 6 件, 只给终局出现率
        '''
        boots = await OpggDataParser.parseItemSets(data.get('boots'), 3, arena)
        startItems = await OpggDataParser.parseItemSets(data.get('starter_items'), 3, arena)
        coreItems = await OpggDataParser.parseItemSets(data.get('core_items'), 5, arena)

        lastItems = []
        for i in (data.get('last_items') or [])[:16]:
            lastItems.append(await connector.getItemIcon(i['ids'][0]))

        exclude = set(coreItems[0]['ids']) if coreItems else set()
        for b in data.get('boots') or []:
            exclude.update(b['ids'])

        lateItems = []
        # 斗魂竞技场的 last_items 不是按选择率排好的
        lastByPick = sorted(data.get('last_items') or [],
                            key=lambda i: -(i.get('pick_rate') or 0))
        for i in lastByPick:
            if len(lateItems) >= 10:
                break

            id = i['ids'][0]
            if id in exclude:
                continue

            lateItems.append({
                'id': id,
                'icon': await connector.getItemIcon(id),
                'play': i['play'],
                'win': i['win'],
                'pickRate': i['pick_rate'],
            })

        return {
            "boots": boots,
            "startItems": startItems,
            "coreItems": coreItems,
            "lastItems": lastItems,
            "lateItems": lateItems,
        }

    # 克制关系里, 对局数低于全部对线对局 1% 的英雄样本太少, 不在概览中展示
    COUNTER_MIN_SHARE = 0.01

    @staticmethod
    async def parseCounters(counters):
        '''
        对线克制: `win` 是当前英雄对该英雄的胜场, 胜率 >= 50% 为 "克制", 否则为 "被克制"

        `strongAgainst` / `weakAgainst` 为完整列表 (按胜率排序);
        `minGames` 为概览展示所需的最少对局数 (全部对线对局的 1%, 至少 30 局)
        '''
        strongAgainst = []
        weakAgainst = []

        counters = [c for c in counters or [] if c.get('play')]
        for c in counters:
            rate = c['win'] / c['play']
            arr = strongAgainst if rate >= 0.5 else weakAgainst

            arr.append({
                'championId': (id := c['champion_id']),
                'name': connector.manager.getChampionNameById(id),
                'icon': await connector.getChampionIcon(id),
                'play': c['play'],
                'win': c['win'],
                'winRate': rate
            })

        strongAgainst.sort(key=lambda x: -x['winRate'])
        weakAgainst.sort(key=lambda x: x['winRate'])

        total = sum(c['play'] for c in counters)

        return {
            "strongAgainst": strongAgainst,
            "weakAgainst": weakAgainst,
            "totalGames": total,
            "minGames": max(int(total * OpggDataParser.COUNTER_MIN_SHARE), 30),
        }

    @staticmethod
    def parseTrends(data):
        '''
        近期版本的胜率 / 选择率 / 禁用率 (按版本从旧到新), 以及不同游戏时长下的胜率

        游戏时长分段: game_length 为 0 / 25 / 30 / 35 / 40, 即 <25, 25-30, 30-35, 35-40, 40+ 分钟
        '''
        trends = data.get('trends') or {}

        def series(key):
            return [{'version': t['version'], 'rate': t.get('rate') or 0, 'rank': t.get('rank')}
                    for t in reversed(trends.get(key) or []) if t.get('version')]

        gameLengths = [{
            'minute': g['game_length'],
            'winRate': g.get('rate') or 0,
            'average': g.get('average') or 0.5,
            'rank': g.get('rank'),
        } for g in data.get('game_lengths') or [] if g.get('rate')]

        return {
            'win': series('win'),
            'pick': series('pick'),
            'ban': series('ban'),
            'gameLengths': gameLengths,
            'rankTotal': trends.get('total_position_rank'),
        }

    @staticmethod
    async def parseArenaChampionBuild(data):
        data = data['data']

        summary = data['summary']
        championId = summary['id']
        name = connector.manager.getChampionNameById(championId)
        icon = await connector.getChampionIcon(championId)

        stats = summary['average_stats']
        play = stats['play']
        winRate = stats['win'] / (play or 1)
        firstRate = stats['first_place'] / (play or 1)
        averagePlace = stats['total_place'] / (play or 1)
        pickRate = stats['pick_rate']
        banRate = stats['ban_rate']
        tier = stats['tier']

        items = await OpggDataParser.parseItems(data, arena=True)

        augments = []
        # 按稀有度排: 白银 (1) / 黄金 (4) / 棱彩 (8)
        for item in sorted(data.get('augment_group') or [], key=lambda g: g.get('rarity', 0)):
            arr = [{
                "id": (augId := aug['id']),
                "icon": await connector.getAugmentIcon(augId),
                "name": connector.manager.getAugmentsName(augId),
                "win": aug['win'],
                'play': aug['play'],
                "totalPlace": aug['total_place'],
                "firstPlace": aug['first_place'],
                'pickRate': aug['pick_rate']
            }for aug in item['augments']]

            augments.append(arr)

        synergies = [{
            "championId": (chId := syn['champion_id']),
            'icon': await connector.getChampionIcon(chId),
            "name": connector.manager.getChampionNameById(chId),
            "win": syn['win'],
            'play': syn['play'],
            "totalPlace": syn['total_place'],
            "firstPlace": syn['first_place'],
            'pickRate': syn['pick_rate']
        }for syn in data.get('synergies') or []]

        return {
            "summary": {
                "name": name,
                "icon": icon,
                "championId": championId,
                "play": play,
                "winRate": winRate,
                "firstRate": firstRate,
                "averagePlace": averagePlace,
                "pickRate": pickRate,
                "banRate": banRate,
                "tier": tier,
                "rank": (stats.get('tier_data') or {}).get('rank', stats.get('rank')),
                "position": "none"
            },
            "championSkills": OpggDataParser.parseSkills(data),
            "items": items,
            "augments": augments,
            "synergies": synergies,
        }


opgg = Opgg()
