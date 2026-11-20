import itertools
import time
import ctypes
from copy import deepcopy

import asyncio
from PySide6.QtCore import QObject, QLocale

from .exceptions import SummonerRankInfoNotFound
from .chat_reveal import mergeHiddenTeammates, revealStats, isHiddenSlot
from .match_build import (parseGameBuilds, buildItemIds, hasBuildEvents,
                          remapTimelineParticipants, statPerksFromSummary,
                          parseTimelineSeries)
from ..common.config import cfg, Language, isChineseText
from ..common.logger import logger
from ..lol.connector import connector
from . import opscore as opscoreModel
from ..common.signals import signalBus
from ..common import platform

TAG = "Tools"


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


async def getRecentTeammates(games, puuid, details=None):
    """
    @param details: 与 games 对齐的完整对局 (attachGameScores 的返回值, 失败处为 None);
        有就直接复用, 只为缺的局补拉
    """
    if details is None or len(details) != len(games):
        details = [None] * len(games)
    details = list(details)

    missing = [i for i, detail in enumerate(details) if detail is None]
    fetched = await asyncio.gather(
        *[connector.getGameDetailByGameId(games[i]['gameId']) for i in missing])
    for i, detail in zip(missing, fetched):
        details[i] = detail

    summoners = {}
    iconIds = {}

    for game in details:
        teammates = getTeammates(game, puuid)

        for p in teammates['summoners']:
            if p['summonerId'] == 0:
                continue

            if p['puuid'] not in summoners:
                iconIds[p['puuid']] = p['icon']
                summoners[p['puuid']] = {
                    "name": p['name'], 'tagLine': p.get('tagLine', ''), 'icon': None,
                    "total": 0, "wins": 0, "losses": 0, "puuid": p["puuid"]}

            summoners[p['puuid']]['total'] += 1

            if not teammates['remake']:
                if teammates['win']:
                    summoners[p['puuid']]['wins'] += 1
                else:
                    summoners[p['puuid']]['losses'] += 1

    ret = {"puuid": puuid, "summoners": [
        item for item in summoners.values()]}

    ret['summoners'] = sorted(ret['summoners'],
                              key=lambda x: x['total'], reverse=True)[:5]

    # 只为显示的人取头像, 同一头像只取一次 (getProfileIcon 并发写同一文件会冲突)
    uniqueIconIds = list(dict.fromkeys(iconIds[s['puuid']] for s in ret['summoners']))
    icons = dict(zip(uniqueIconIds, await asyncio.gather(
        *[connector.getProfileIcon(iconId) for iconId in uniqueIconIds])))
    for s in ret['summoners']:
        s['icon'] = icons[iconIds[s['puuid']]]

    return ret


async def parseSummonerData(summoner, rankTask, gameTask):
    iconId = summoner['profileIconId']
    icon = await connector.getProfileIcon(iconId)
    level = summoner['summonerLevel']
    xpSinceLastLevel = summoner['xpSinceLastLevel']
    xpUntilNextLevel = summoner['xpUntilNextLevel']
    details = None

    try:
        gamesInfo = await gameTask
    except:
        champions = []
        games = {}
    else:
        games = {
            "gameCount": gamesInfo["gameCount"],
            "wins": 0,
            "losses": 0,
            "kills": 0,
            "deaths": 0,
            "assists": 0,
            "games": [],
        }
        for game in gamesInfo["games"]:
            info = await parseGameData(game)
            if time.time() - info["timeStamp"] / 1000 > 60 * 60 * 24 * 365:
                continue
            if not info["remake"] and info["queueId"] != 0:
                games["kills"] += info["kills"]
                games["deaths"] += info["deaths"]
                games["assists"] += info["assists"]
                if info["win"]:
                    games["wins"] += 1
                else:
                    games["losses"] += 1
            games["games"].append(info)

        # OP.GG 风格战绩行: MVP / SVP / 名次 / 参团率与每局 10 名玩家都来自完整对局
        details = await attachGameScores(games['games'], summoner['puuid'])
        participants = await asyncio.gather(
            *[gameParticipantsFromLcuDetail(detail) for detail in details])
        for game, players in zip(games['games'], participants):
            game['participants'] = players

        champions = getRecentChampions(games['games'])

    try:
        rankInfo = await rankTask
    except SummonerRankInfoNotFound:
        rankInfo = {}

    return {
        'name': summoner.get("gameName") or summoner['displayName'],
        'icon': icon,
        'level': level,
        'xpSinceLastLevel': xpSinceLastLevel,
        'xpUntilNextLevel': xpUntilNextLevel,
        'puuid': summoner['puuid'],
        'rankInfo': rankInfo,
        'games': games,
        'gameDetails': details,  # 与 games['games'] 对齐, 近期队友复用
        'champions': champions,
        'isPublic': summoner['privacy'] == "PUBLIC",
        'tagLine': summoner.get("tagLine"),
    }


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


async def parseGameData(game):
    timeStamp = game["gameCreation"]  # 毫秒级时间戳
    time = timeStampToStr(game['gameCreation'])
    shortTime = timeStampToShortStr(game['gameCreation'])
    gameId = game['gameId']
    duration = secsToStr(game['gameDuration'])
    queueId = game['queueId']

    nameAndMap = connector.manager.getNameMapByQueueId(queueId)
    modeName = nameAndMap['name']

    if queueId != 0:
        mapName = nameAndMap['map']
    else:
        mapName = connector.manager.getMapNameById(game['mapId'])

    participant = game['participants'][0]
    championId = participant['championId']
    championIcon = await connector.getChampionIcon(championId)
    spell1Id = participant['spell1Id']
    spell2Id = participant['spell2Id']
    spell1Icon = await connector.getSummonerSpellIcon(spell1Id)
    spell2Icon = await connector.getSummonerSpellIcon(spell2Id)
    stats = participant['stats']

    champLevel = stats['champLevel']
    kills = stats['kills']
    deaths = stats['deaths']
    assists = stats['assists']
    itemIds = [
        stats['item0'],
        stats['item1'],
        stats['item2'],
        stats['item3'],
        stats['item4'],
        stats['item5'],
        stats['item6'],
    ]

    itemIcons = [await connector.getItemIcon(itemId) for itemId in itemIds]
    runeId = stats['perk0']
    runeIcon = await connector.getRuneIcon(runeId)

    cs = stats['totalMinionsKilled'] + stats['neutralMinionsKilled']
    gold = stats['goldEarned']
    damage = stats.get('totalDamageDealtToChampions', 0)
    visionScore = stats.get('visionScore', 0)
    multiKill = stats.get('largestMultiKill', 0)
    remake = stats['gameEndedInEarlySurrender']
    win = stats['win']

    timeline = participant['timeline']
    positionKey = getPositionKey(queueId, timeline['lane'], timeline['role'])
    position = translatePositionKey(positionKey)

    return {
        'queueId': queueId,
        'gameId': gameId,
        'time': time,
        'shortTime': shortTime,
        'name': modeName,
        'map': mapName,
        'duration': duration,
        'durationSecs': game['gameDuration'],
        'remake': remake,
        'win': win,
        'championId': championId,
        'championIcon': championIcon,
        'spell1Icon': spell1Icon,
        'spell2Icon': spell2Icon,
        'champLevel': champLevel,
        'kills': kills,
        'deaths': deaths,
        'assists': assists,
        'itemIcons': itemIcons,
        'itemIds': itemIds,
        'runeIcon': runeIcon,
        'cs': cs,
        'gold': gold,
        'damage': damage,
        'visionScore': visionScore,
        'multiKill': multiKill,
        'gameMode': game.get('gameMode'),
        'timeStamp': timeStamp,
        'position': position,
        'positionKey': positionKey,
    }


def detailRankInfo(rankStats, queueId):
    """
    对局详情表一名玩家的段位 (tierKey, tier, division, lp, rankIcon), 按本局队列取对应段位;
    数据异常时返回 None (该玩家不显示段位, 不影响整张表)
    """
    try:
        rank = rankStats.get('queueMap') or {}

        if queueId == 1700 and 'CHERRY' in rank:
            return "", "", "", rank['CHERRY'].get('ratedRating', ''), ""

        rankInfo = rank.get({440: 'RANKED_FLEX_SR', 710: 'RANKED_PREMADE_5x5'}.get(
            queueId, 'RANKED_SOLO_5x5')) or {}
        tier = rankInfo.get('tier') or ''
        division = rankInfo.get('division') or ''
        lp = rankInfo.get('leaguePoints', '')

        if tier in ('', 'NONE', 'UNRANKED'):
            return "", "", "", lp, 'app/resource/images/unranked.png'

        return (tier, translateTier(tier, True), '' if division == 'NA' else division, lp,
                f'app/resource/images/{tier.lower()}.png')
    except Exception:
        return None


async def parseGameDetailData(puuid, game):
    queueId = game['queueId']
    mapId = game['mapId']

    names = connector.manager.getNameMapByQueueId(queueId)
    modeName = names['name']
    if queueId != 0:
        mapName = names['map']
    else:
        mapName = connector.manager.getMapNameById(mapId)

    def origTeam(teamId):
        return {
            'win': None,
            'bans': [],
            'baronKills': 0,
            'baronIcon': f"app/resource/images/baron-{teamId}.png",
            'dragonKills': 0,
            'dragonIcon': f'app/resource/images/dragon-{teamId}.png',
            'riftHeraldKills': 0,
            'riftHeraldIcon': f'app/resource/images/herald-{teamId}.png',
            'inhibitorKills': 0,
            'inhibitorIcon': f'app/resource/images/inhibitor-{teamId}.png',
            'hordeKills': 0,
            'towerKills': 0,
            'towerIcon': f'app/resource/images/tower-{teamId}.png',
            'kills': 0,
            'deaths': 0,
            'assists': 0,
            'gold': 0,
            'summoners': []
        }

    teams = {
        100: origTeam("100"),
        200: origTeam("200"),
        300: origTeam("100"),
        400: origTeam("200"),
        500: origTeam("100"),
        600: origTeam("200"),
        700: origTeam("100"),
        800: origTeam("200"),
    }

    cherryResult = None
    win = None

    for team in game['teams']:
        teamId = team['teamId']

        if teamId == 0:
            teamId = 200

        teams[teamId]['win'] = team['win']
        teams[teamId]['bans'] = [
            await connector.getChampionIcon(item['championId'])
            for item in team['bans']
        ]
        teams[teamId]['baronKills'] = team['baronKills']
        teams[teamId]['dragonKills'] = team['dragonKills']
        teams[teamId]['riftHeraldKills'] = team['riftHeraldKills']
        teams[teamId]['hordeKills'] = team['hordeKills']
        teams[teamId]['towerKills'] = team['towerKills']
        teams[teamId]['inhibitorKills'] = team['inhibitorKills']

    # 详情表的段位: 所有玩家并发取 (安静接口, 失败不重试不弹窗), AI 没有段位
    rankPuuids = [p['player']['puuid'] for p in game['participantIdentities']
                  if p['player']['puuid'] != '00000000-0000-0000-0000-000000000000']
    rankResults = await asyncio.gather(
        *[connector.getRankedStatsByPuuidQuietly(p) for p in rankPuuids])
    ranks = {p: detailRankInfo(r, queueId) for p, r in zip(rankPuuids, rankResults) if r}

    for participant in game['participantIdentities']:
        participantId = participant['participantId']
        summonerName = participant['player'].get(
            'gameName') or participant['player'].get('summonerName')  # 兼容外服
        tagLine = participant['player'].get('tagLine') or ''
        summonerPuuid = participant['player']['puuid']
        isCurrent = (summonerPuuid == puuid)

        if summonerPuuid == '00000000-0000-0000-0000-000000000000':  # AI
            isPublic = True
        else:
            t = await connector.getSummonerByPuuid(summonerPuuid)
            isPublic = t.get("privacy") == "PUBLIC"

        for summoner in game['participants']:
            if summoner['participantId'] == participantId:
                stats = summoner['stats']

                if queueId != 1700:
                    subteamPlacement = None
                    tid = summoner['teamId']
                else:
                    subteamPlacement = stats['subteamPlacement']
                    tid = subteamPlacement * 100

                if isCurrent:
                    remake = stats['gameEndedInEarlySurrender']
                    win = stats['win']

                    if queueId == 1700:
                        cherryResult = subteamPlacement

                championId = summoner['championId']
                championIcon = await connector.getChampionIcon(championId)

                spell1Id = summoner['spell1Id']
                spell1Icon = await connector.getSummonerSpellIcon(spell1Id)
                spell2Id = summoner['spell2Id']
                spell2Icon = await connector.getSummonerSpellIcon(spell2Id)

                kills = stats['kills']
                deaths = stats['deaths']
                assists = stats['assists']
                gold = stats['goldEarned']

                # 斗魂竞技场(1700) tid = subteamPlacement*100; 远端给出 0 或 >8 的
                # 异常 placement 时 tid 落在 teams(100..800) 之外, 跳过聚合避免 KeyError
                if tid in teams:
                    teams[tid]['kills'] += kills
                    teams[tid]['deaths'] += deaths
                    teams[tid]['assists'] += assists
                    teams[tid]['gold'] += gold

                runeIcon = await connector.getRuneIcon(stats['perk0'])

                # 副系符文 (8000 / 8100 ... 系别图标), 取不到就不显示
                try:
                    subStyleIcon = await connector.getRuneIcon(stats.get('perkSubStyle') or 0)
                except Exception:
                    subStyleIcon = None

                itemIds = [
                    stats['item0'],
                    stats['item1'],
                    stats['item2'],
                    stats['item3'],
                    stats['item4'],
                    stats['item5'],
                    stats['item6'],
                ]

                itemIcons = [
                    await connector.getItemIcon(itemId) for itemId in itemIds
                ]

                # 详情表总是显示段位 (段位已在循环前并发取好, 取不到就不显示)
                rankInfo = ranks.get(summonerPuuid)
                getRankInfo = rankInfo is not None
                tierKey, tier, division, lp, rankIcon = rankInfo or ("", "", "", "", "")

                item = {
                    'summonerName': summonerName,
                    'tagLine': tagLine,
                    'puuid': summonerPuuid,
                    'isCurrent': isCurrent,
                    'championIcon': championIcon,
                    'rankInfo': getRankInfo,
                    'tier': tier,
                    'division': division,
                    'lp': lp,
                    'rankIcon': rankIcon,
                    'spell1Icon': spell1Icon,
                    'spell2Icon': spell2Icon,
                    'itemIcons': itemIcons,
                    'itemIds': itemIds,
                    'kills': kills,
                    'deaths': deaths,
                    'assists': assists,
                    'cs': stats['totalMinionsKilled'] + stats['neutralMinionsKilled'],
                    'gold': gold,
                    'runeIcon': runeIcon,
                    'champLevel': stats['champLevel'],
                    'demage': stats['totalDamageDealtToChampions'],
                    'subteamPlacement': subteamPlacement,
                    'isPublic': isPublic,
                    # OP.GG 风格详情表用
                    'participantId': participantId,
                    'tierKey': tierKey,
                    'subStyleIcon': subStyleIcon,
                    'damageTaken': stats.get('totalDamageTaken', 0),
                    'controlWards': stats.get('visionWardsBoughtInGame', 0),
                    'wardsPlaced': stats.get('wardsPlaced', 0),
                    'wardsKilled': stats.get('wardsKilled', 0),
                    'visionScore': stats.get('visionScore', 0),
                    'multiKill': stats.get('largestMultiKill', 0),
                }
                # 同 teams[tid] 聚合守卫: 异常 placement 的 tid 不在 teams 内则跳过
                if tid in teams:
                    teams[tid]['summoners'].append(item)

                break

    mapIcon = connector.manager.getMapIconByMapId(mapId, win)

    if win == None:
        return None

    # 评分 / 名次 / MVP SVP / 参团率 (与战绩页同一套评分); 斗魂竞技场不评
    players = lcuDetailPlayers(game)
    if players and queueId not in (1700, 1710):
        try:
            modelScores = await opscoreModel.scoresForLcuDetail(game)
        except Exception:
            modelScores = None
        scores = scoreGameParticipants(players, modelScores)
        for team in teams.values():
            for summoner in team['summoners']:
                result = scores.get(summoner['participantId'])
                if result:
                    summoner['score'] = result['score']
                    summoner['opScore'] = None if remake else result['opScore']
                    summoner['scoreRank'] = result['scoreRank']
                    summoner['kp'] = result['kp']
                    summoner['badge'] = None if remake else result['badge']

    return {
        'gameId': game['gameId'],
        'mapIcon': mapIcon,
        'gameCreation': timeStampToStr(game['gameCreation']),
        'gameDuration': secsToStr(game['gameDuration']),
        'durationSecs': game['gameDuration'],
        'modeName': modeName,
        'mapName': mapName,
        'queueId': queueId,
        'win': win,
        'cherryResult': cherryResult,
        'remake': remake,
        'teams': teams,
    }


def getTeammates(game, targetPuuid):
    """
    通过 game 信息获取目标召唤师的队友

    @param game: @see connector.getGameDetailByGameId
    @param targetPuuid: 目标召唤师 puuid
    @return: @see res
    """
    targetParticipantId = None

    for participant in game['participantIdentities']:
        puuid = participant['player']['puuid']

        if puuid == targetPuuid:
            targetParticipantId = participant['participantId']
            break

    assert targetParticipantId is not None

    for player in game['participants']:
        if player['participantId'] == targetParticipantId:
            if game['queueId'] != 1700:
                tid = player['teamId']
            else:  # 斗魂竞技场
                tid = player['stats']['subteamPlacement']

            win = player['stats']['win']
            remake = player['stats']['teamEarlySurrendered']

            break

    res = {
        'queueId': game['queueId'],
        'win': win,
        'remake': remake,
        'summoners': [],  # 队友召唤师 (由于兼容性, 未修改字段名)
        'enemies': []  # 对面召唤师, 若有多个队伍会全放这里面
    }

    for player in game['participants']:

        if game['queueId'] != 1700:
            cmp = player['teamId']
        else:
            cmp = player['stats']['subteamPlacement']

        p = player['participantId']
        s = game['participantIdentities'][p - 1]['player']

        # Riot ID 账号的 summonerName 为空, 名字在 gameName (+ tagLine)
        entry = {'summonerId': s['summonerId'],
                 'name': s.get('gameName') or s.get('summonerName') or '',
                 'tagLine': s.get('tagLine') or '',
                 'puuid': s['puuid'], 'icon': s['profileIcon']}

        if cmp == tid:
            if s['puuid'] != targetPuuid:
                res['summoners'].append(entry)
            else:
                # 当前召唤师在该对局使用的英雄, 自定义对局没有该字段
                res["championId"] = player.get('championId', -1)
        else:
            res['enemies'].append(entry)

    return res


def getRecentChampions(games):
    champions = {}

    for game in games:
        if game['queueId'] == 0:
            continue

        championId = game['championId']

        if championId not in champions:
            champions[championId] = {
                'icon': game['championIcon'], 'wins': 0, 'losses': 0, 'total': 0}

        champions[championId]['total'] += 1

        if not game['remake']:
            if game['win']:
                champions[championId]['wins'] += 1
            else:
                champions[championId]['losses'] += 1

    ret = [item for item in champions.values()]
    ret.sort(key=lambda x: x['total'], reverse=True)

    maxLen = 10

    return ret if len(ret) < maxLen else ret[:maxLen]


def parseRankInfo(info):
    """
    解析 `connector.getRankedStatsByPuuid()` 的数据。


    api: `/lol-ranked/v1/ranked-stats/{puuid}`

    :param info: 接口返回值, 允许为空（接口异常时抛出 `SummonerRankInfoNotFound`, 需要捕获置空）

    """
    tt = ToolsTranslator()

    soloIcon = flexIcon = "app/resource/images/UNRANKED.svg"
    soloTier = flexTier = tt.unknown
    soloDivision = flexDivision = ""
    soloRankInfo = flexRankInfo = {"leaguePoints": ""}

    if info:
        soloRankInfo = info["queueMap"]["RANKED_SOLO_5x5"]
        flexRankInfo = info["queueMap"]["RANKED_FLEX_SR"]

        soloTier = soloRankInfo["tier"]
        soloDivision = soloRankInfo["division"]

        if soloTier == "":
            soloIcon = "app/resource/images/UNRANKED.svg"
            soloTier = tt.unranked
        else:
            soloIcon = f"app/resource/images/{soloTier}.svg"
            soloTier = translateTier(soloTier, True)
        if soloDivision == "NA":
            soloDivision = ""

        flexTier = flexRankInfo["tier"]
        flexDivision = flexRankInfo["division"]

        if flexTier == "":
            flexIcon = "app/resource/images/UNRANKED.svg"
            flexTier = tt.unranked
        else:
            flexIcon = f"app/resource/images/{flexTier}.svg"
            flexTier = translateTier(flexTier, True)
        if flexDivision == "NA":
            flexDivision = ""

    return {
        "solo": {
            "tier": soloTier,
            "icon": soloIcon,
            "division": soloDivision,
            "lp": soloRankInfo.get("leaguePoints", ""),
            "wins": soloRankInfo.get("wins", 0),
            "losses": soloRankInfo.get("losses", 0),
            # 原始英文段位 (徽章图用) / 赛季最高 / 上赛季结算; 取不到为 ""
            "tierKey": soloRankInfo.get("tier", "") or "",
            "highestTier": soloRankInfo.get("highestTier", "") or "",
            "highestDivision": soloRankInfo.get("highestDivision", "") or "",
            "previousTier": soloRankInfo.get("previousSeasonEndTier", "") or "",
            "previousDivision": soloRankInfo.get("previousSeasonEndDivision", "") or "",
        },
        "flex": {
            "tier": flexTier,
            "icon": flexIcon,
            "division": flexDivision,
            "lp": flexRankInfo.get("leaguePoints", ""),
            "wins": flexRankInfo.get("wins", 0),
            "losses": flexRankInfo.get("losses", 0),
            # 原始英文段位 (徽章图用) / 赛季最高 / 上赛季结算; 取不到为 ""
            "tierKey": flexRankInfo.get("tier", "") or "",
            "highestTier": flexRankInfo.get("highestTier", "") or "",
            "highestDivision": flexRankInfo.get("highestDivision", "") or "",
            "previousTier": flexRankInfo.get("previousSeasonEndTier", "") or "",
            "previousDivision": flexRankInfo.get("previousSeasonEndDivision", "") or "",
        },
    }


def parseRankInfoFromSGP(info):
    '''解析来自 `connector.getRankedStatsByPuuidViaSGP()` 的数据'''

    tt = ToolsTranslator()

    soloIcon = flexIcon = "app/resource/images/UNRANKED.svg"
    soloTier = flexTier = tt.unknown
    soloDivision = flexDivision = ""
    soloRankInfo = flexRankInfo = {"leaguePoints": ""}

    if info:
        for queue in info['queues']:
            type = queue['queueType']
            if type == 'RANKED_FLEX_SR':
                flexRankInfo = queue
            elif type == 'RANKED_SOLO_5x5':
                soloRankInfo = queue

        soloTier = soloRankInfo.get("tier", "")
        soloDivision = soloRankInfo.get("rank", "NA")

        if soloTier == "":
            soloIcon = "app/resource/images/UNRANKED.svg"
            soloTier = tt.unranked
        else:
            soloIcon = f"app/resource/images/{soloTier}.svg"
            soloTier = translateTier(soloTier, True)
        if soloDivision == "NA":
            soloDivision = ""

        flexTier = flexRankInfo.get("tier", "")
        flexDivision = flexRankInfo.get("rank", "NA")

        if flexTier == "":
            flexIcon = "app/resource/images/UNRANKED.svg"
            flexTier = tt.unranked
        else:
            flexIcon = f"app/resource/images/{flexTier}.svg"
            flexTier = translateTier(flexTier, True)
        if flexDivision == "NA":
            flexDivision = ""

    return {
        "solo": {
            "tier": soloTier,
            "icon": soloIcon,
            "division": soloDivision,
            "lp": soloRankInfo.get("leaguePoints", ""),
            "wins": soloRankInfo.get("wins", 0),
            "losses": soloRankInfo.get("losses", 0),
            # 原始英文段位 (徽章图用) / 赛季最高 / 上赛季结算; 取不到为 ""
            "tierKey": soloRankInfo.get("tier", "") or "",
            "highestTier": soloRankInfo.get("highestTier", "") or "",
            "highestDivision": soloRankInfo.get("highestDivision", "") or "",
            "previousTier": soloRankInfo.get("previousSeasonEndTier", "") or "",
            "previousDivision": soloRankInfo.get("previousSeasonEndDivision", "") or "",
        },
        "flex": {
            "tier": flexTier,
            "icon": flexIcon,
            "division": flexDivision,
            "lp": flexRankInfo.get("leaguePoints", ""),
            "wins": flexRankInfo.get("wins", 0),
            "losses": flexRankInfo.get("losses", 0),
            # 原始英文段位 (徽章图用) / 赛季最高 / 上赛季结算; 取不到为 ""
            "tierKey": flexRankInfo.get("tier", "") or "",
            "highestTier": flexRankInfo.get("highestTier", "") or "",
            "highestDivision": flexRankInfo.get("highestDivision", "") or "",
            "previousTier": flexRankInfo.get("previousSeasonEndTier", "") or "",
            "previousDivision": flexRankInfo.get("previousSeasonEndDivision", "") or "",
        },
    }


def parseDetailRankInfo(rankInfo):
    soloRankInfo = rankInfo['queueMap']['RANKED_SOLO_5x5']
    soloTier = translateTier(soloRankInfo['tier'])
    soloDivision = soloRankInfo['division']
    if soloTier == '--' or soloDivision == 'NA':
        soloDivision = ""

    soloHighestTier = translateTier(soloRankInfo['highestTier'])
    soloHighestDivision = soloRankInfo['highestDivision']
    if soloHighestTier == '--' or soloHighestDivision == 'NA':
        soloHighestDivision = ""

    solxPreviousSeasonEndTier = translateTier(
        soloRankInfo['previousSeasonEndTier'])
    soloPreviousSeasonDivision = soloRankInfo[
        'previousSeasonEndDivision']
    if solxPreviousSeasonEndTier == '--' or soloPreviousSeasonDivision == 'NA':
        soloPreviousSeasonDivision = ""

    soloWins = soloRankInfo['wins']
    soloLosses = soloRankInfo['losses']
    soloTotal = soloWins + soloLosses
    soloWinRate = soloWins * 100 // soloTotal if soloTotal != 0 else 0
    soloLp = soloRankInfo['leaguePoints']

    flexRankInfo = rankInfo['queueMap']['RANKED_FLEX_SR']
    flexTier = translateTier(flexRankInfo['tier'])
    flexDivision = flexRankInfo['division']
    if flexTier == '--' or flexDivision == 'NA':
        flexDivision = ""

    flexHighestTier = translateTier(flexRankInfo['highestTier'])
    flexHighestDivision = flexRankInfo['highestDivision']
    if flexHighestTier == '--' or flexHighestDivision == 'NA':
        flexHighestDivision = ""

    flexPreviousSeasonEndTier = translateTier(
        flexRankInfo['previousSeasonEndTier'])
    flexPreviousSeasonEndDivision = flexRankInfo[
        'previousSeasonEndDivision']

    if flexPreviousSeasonEndTier == '--' or flexPreviousSeasonEndDivision == 'NA':
        flexPreviousSeasonEndDivision = ""

    flexWins = flexRankInfo['wins']
    flexLosses = flexRankInfo['losses']
    flexTotal = flexWins + flexLosses
    flexWinRate = flexWins * 100 // flexTotal if flexTotal != 0 else 0
    flexLp = flexRankInfo['leaguePoints']

    pt = ToolsTranslator()

    return [
        [
            pt.rankedSolo,
            str(soloTotal),
            str(soloWinRate) + ' %' if soloTotal != 0 else '--',
            str(soloWins),
            str(soloLosses),
            f'{soloTier} {soloDivision}',
            str(soloLp),
            f'{soloHighestTier} {soloHighestDivision}',
            f'{solxPreviousSeasonEndTier} {soloPreviousSeasonDivision}',
        ],
        [
            pt.rankedFlex,
            str(flexTotal),
            str(flexWinRate) + ' %' if flexTotal != 0 else '--',
            str(flexWins),
            str(flexLosses),
            f'{flexTier} {flexDivision}',
            str(flexLp),
            f'{flexHighestTier} {flexHighestDivision}',
            f'{flexPreviousSeasonEndTier} {flexPreviousSeasonEndDivision}',
        ],
    ]


def parseGames(games, targetId=0):
    f"""
    解析 Games 数据

    @param targetId: 需要查询的游戏模式, 不传则收集所有模式的数据
    @param games: 由 @see: {parseGameData} 获取到的games数据
    @return: hitGame, K, D, A, win, loss
    @rtype: tuple[list, int, int, int, int, int, int]
    """

    kills, deaths, assists, wins, losses = 0, 0, 0, 0, 0
    hitGames = []

    for game in games:
        if not targetId or game['queueId'] == targetId:
            hitGames.append(game)

            if not game['remake']:
                kills += game['kills']
                deaths += game['deaths']
                assists += game['assists']

                if game['win']:
                    wins += 1
                else:
                    losses += 1

    return hitGames, kills, deaths, assists, wins, losses


# 选手评分权重: 非排位局 (以及排位局取不到 Riot 对局服务器 SUMMARY 时) 用的手工公式;
# 排位局优先用拟合 OP.GG 的模型 (见 opscore.py). Riot 的 totalPlayerScore 等字段实测全为 0.
# 每项都按本局 10 人里的最大值归一化
GAME_SCORE_WEIGHTS = {
    'kp': 3,           # 参团率 (击杀 + 助攻) / 本队击杀
    'damage': 2,       # 对英雄伤害
    'kda': 2,          # (击杀 + 助攻) / max(死亡, 1), 封顶 10
    'damageTaken': 1,  # 承受伤害
    'gold': 1,         # 金币
    'vision': 1,       # 视野得分
}

# OP Score 换算 (见 opScore)
OP_SCORE_SLOPE = 1.5
OP_SCORE_OFFSET = -3.5


def opScore(raw):
    """
    原始评分 -> OP.GG 风格的 0.0 ~ 10.0 分 (一位小数), 战绩行 / 对局详情显示用.

    原始评分 = Σ 权重 × (本项 / 本局 10 人里该项最大值), 权重和为 10, 所以理论上在 0 ~ 10,
    但因为每项都按本局最大值归一化, 实际很挤: 模拟 3000 局 (五个分路的典型数据加随机波动)
    得到 均值 ≈ 6.0, 5% ~ 95% 分位 ≈ 4.2 ~ 7.9, 第 1 名平均 ≈ 7.8, 第 10 名平均 ≈ 4.4.
    直接显示的话谁都是 "5 点几 ~ 7 点几", 看不出差别, 于是做一次线性拉伸:

        OP Score = clamp(1.5 × raw − 3.5, 0, 10), 保留一位小数

    - 普通一局 (raw ≈ 6) -> 5.5; 第 1 名平均 -> 8.2, 第 10 名平均 -> 3.1
    - 几项都是全场最高的大爹局 (raw ≈ 8.5 ~ 9) -> 9.3 ~ 10
    - 单调不减, 名次 / MVP / SVP 仍按原始评分排, 换算不影响排名 (两端截断处可能同分显示)
    """
    return round(min(max(OP_SCORE_SLOPE * raw + OP_SCORE_OFFSET, 0.0), 10.0), 1)


def scoreGameParticipants(players, modelScores=None):
    """
    给一局里的所有玩家打分并排名, 选出 MVP (胜方最高分) 与 SVP (败方最高分)

    @param players: [{'id', 'teamId', 'win', 'kills', 'deaths', 'assists',
                      'damage', 'damageTaken', 'gold', 'vision'}], id 为 participantId
    @param modelScores: {id: 0 ~ 10 分} 拟合 OP.GG 的模型评分 (opscore.py); 覆盖全部玩家时
                        直接用它评分与排名, 否则用手工公式
    @return: {id: {'score', 'opScore', 'scoreRank', 'kp', 'badge'}}; score 为原始评分,
             opScore 为显示的 0 ~ 10 分 (手工公式时见 opScore); badge 为 'MVP' / 'SVP' / None.
             不是恰好两队且一胜一负时不评徽章 (仍有排名)
    """
    if not players:
        return {}

    useModel = bool(modelScores) and all(p['id'] in modelScores for p in players)

    teamKills = {}
    for p in players:
        teamKills[p['teamId']] = teamKills.get(p['teamId'], 0) + p['kills']

    raw = {}
    for p in players:
        raw[p['id']] = {
            'kp': (p['kills'] + p['assists']) / max(teamKills[p['teamId']], 1),
            'damage': p['damage'],
            'kda': min((p['kills'] + p['assists']) / max(p['deaths'], 1), 10),
            'damageTaken': p['damageTaken'],
            'gold': p['gold'],
            'vision': p['vision'],
        }

    if useModel:
        scores = {p['id']: modelScores[p['id']] for p in players}
    else:
        maxes = {k: max(r[k] for r in raw.values()) for k in GAME_SCORE_WEIGHTS}

        scores = {}
        for pid, r in raw.items():
            scores[pid] = sum(w * (r[k] / maxes[k] if maxes[k] else 0)
                              for k, w in GAME_SCORE_WEIGHTS.items())

    def shown(score):
        return round(min(max(score, 0.0), 10.0), 1) if useModel else opScore(score)

    byId = {p['id']: p for p in players}
    # 同分时按伤害高者优先, 再按 participantId, 保证同一局在不同玩家的战绩里结果一致
    order = sorted(scores, key=lambda pid: (-scores[pid], -byId[pid]['damage'], pid))

    result = {pid: {'score': scores[pid], 'opScore': shown(scores[pid]), 'scoreRank': i + 1,
                    'kp': raw[pid]['kp'], 'badge': None}
              for i, pid in enumerate(order)}

    teams = {p['teamId'] for p in players}
    winners = [pid for pid in order if byId[pid]['win']]
    losers = [pid for pid in order if not byId[pid]['win']]

    if len(teams) == 2 and winners and losers:
        result[winners[0]]['badge'] = 'MVP'
        result[losers[0]]['badge'] = 'SVP'

    return result


def _pickScore(players, myId, remake, modelScores=None):
    """scoreGameParticipants 的结果里取出自己那一项, 转成 gamesInfo 用的字段"""
    if myId is None or len(players) < 2:
        return None

    result = scoreGameParticipants(players, modelScores).get(myId)
    if not result:
        return None

    return {
        'badge': None if remake else result['badge'],
        'opScore': None if remake else result['opScore'],  # 重开局不显示分数
        'scoreRank': result['scoreRank'],
        'scorePlayers': len(players),
        'kp': result['kp'],
    }


def lcuDetailPlayers(detail):
    """
    LCU 完整对局 -> scoreGameParticipants 的输入; 多小队模式 (斗魂竞技场) 或数据不全返回 []
    """
    try:
        players = []
        for p in detail['participants']:
            st = p['stats']
            if st.get('playerSubteamId'):
                return []

            players.append({
                'id': p['participantId'], 'teamId': p['teamId'],
                'win': bool(st['win']), 'kills': st['kills'],
                'deaths': st['deaths'], 'assists': st['assists'],
                'damage': st.get('totalDamageDealtToChampions', 0),
                'damageTaken': st.get('totalDamageTaken', 0),
                'gold': st.get('goldEarned', 0),
                'vision': st.get('visionScore', 0),
            })
        return players
    except (KeyError, TypeError, AttributeError):
        return []


def getGameScoreFromLcuDetail(detail, puuid, modelScores=None):
    """
    由 LCU `/lol-match-history/v1/games/{gameId}` 的完整对局算出 puuid 这名玩家的
    MVP / SVP 徽章, 评分排名与参团率. 斗魂竞技场等多小队模式 / 数据不全时返回 None

    @param modelScores: opscore.scoresForLcuDetail 的结果 (排位局的模型评分), 没有为 None
    """
    try:
        if not detail or detail.get('queueId') in (1700, 1710):
            return None

        identities = {
            pi['participantId']: pi.get('player', {}).get('puuid')
            for pi in detail.get('participantIdentities', [])}

        players = lcuDetailPlayers(detail)
        if not players:
            return None

        myId, remake = None, False
        for p in detail['participants']:
            if identities.get(p['participantId']) == puuid:
                myId = p['participantId']
                remake = bool(p['stats'].get('gameEndedInEarlySurrender'))

        return _pickScore(players, myId, remake, modelScores)
    except (KeyError, TypeError, AttributeError):
        return None


def getGameScoreFromSgpGame(game, puuid):
    """
    与 getGameScoreFromLcuDetail 相同, 数据来自 SGP 战绩 (game 为其中的 'json'). SGP 战绩是
    match-v5 格式, 排位局字段齐全时直接用模型评分, 不另发请求
    """
    try:
        if not game or game.get('queueId') in (1700, 1710):
            return None

        players, myId, remake = [], None, False
        for p in game['participants']:
            if p.get('playerSubteamId'):
                return None

            players.append({
                'id': p['participantId'], 'teamId': p['teamId'],
                'win': bool(p['win']), 'kills': p['kills'],
                'deaths': p['deaths'], 'assists': p['assists'],
                'damage': p.get('totalDamageDealtToChampions', 0),
                'damageTaken': p.get('totalDamageTaken', 0),
                'gold': p.get('goldEarned', 0),
                'vision': p.get('visionScore', 0),
            })

            if p.get('puuid') == puuid:
                myId = p['participantId']
                remake = bool(p.get('gameEndedInEarlySurrender'))

        return _pickScore(players, myId, remake, opscoreModel.scoreSummary(game))
    except (KeyError, TypeError, AttributeError):
        return None


async def gameParticipantsFromLcuDetail(detail):
    """
    生涯页每局右侧的玩家列表 [{'teamId', 'championIcon', 'name', 'puuid'}], 按队伍排 (100 在前).
    完整对局取不到时为 []
    """
    if not detail:
        return []

    identities = {pi['participantId']: pi.get('player', {})
                  for pi in detail.get('participantIdentities', [])}

    players = []
    for p in detail.get('participants', []):
        player = identities.get(p.get('participantId'), {})
        players.append({
            'teamId': p.get('teamId') or 0,
            'championIcon': await connector.getChampionIcon(p.get('championId') or 0),
            'name': player.get('gameName') or player.get('summonerName') or '',
            'tagLine': player.get('tagLine') or '',
            'puuid': player.get('puuid'),
            'participantId': p.get('participantId'),  # 对线得分 (laning.py) 用
        })

    players.sort(key=lambda x: x['teamId'])
    return players


async def attachGameScores(gamesInfo, puuid):
    """
    对局信息页用: 为 parseGameData 得到的每局补上 MVP / SVP 徽章, 评分排名与参团率
    (LCU 战绩列表只有自己的数据, 需逐局拉完整对局). 拉取失败的局不显示徽章.

    @return: 与 gamesInfo 对齐的完整对局列表 (失败处为 None), 供调用方复用
    """
    # 排位局的模型评分要 Riot 对局服务器的 SUMMARY (LCU 详情缺字段), 与完整对局同时拉
    # (结果进 opscore 的缓存); 其他局不发请求
    summaries = asyncio.gather(
        *[opscoreModel.getSummary(game['gameId']) for game in gamesInfo
          if game.get('queueId') in opscoreModel.MODEL_QUEUES],
        return_exceptions=True)
    details = await asyncio.gather(
        *[connector.getGameDetailByGameIdQuietly(game['gameId'])
          for game in gamesInfo],
        return_exceptions=True)
    details = [d if isinstance(d, dict) else None for d in details]
    await summaries

    modelScores = await asyncio.gather(
        *[opscoreModel.scoresForLcuDetail(detail) for detail in details],
        return_exceptions=True)

    for game, detail, model in zip(gamesInfo, details, modelScores):
        score = getGameScoreFromLcuDetail(
            detail, puuid, model if isinstance(model, dict) else None)
        if score:
            game.update(score)

    return details


async def gatherProgressively(coros, onPartial=None, build=None):
    """
    与 asyncio.gather 相同 (按原顺序返回结果), 但每完成一个就把目前的结果交给 onPartial
    (未完成的位置为 None; 先经 build 加工). 冷启动时客户端逐个向服务器拉战绩, 一个人要
    两秒多, 这样面板可以每到一个人就先显示一个, 不用等最慢的那个.
    """
    results = [None] * len(coros)

    async def run(index, coro):
        results[index] = await coro
        if onPartial is not None:
            try:
                onPartial(build(list(results)) if build else list(results))
            except Exception as e:
                logger.error(f"partial update failed: {e!r}", TAG)

    await asyncio.gather(*[run(i, c) for i, c in enumerate(coros)])
    return results


def applyLastGameRelation(summoner, detail, currentSummonerId):
    """由该玩家上一局的完整对局判断上把和当前召唤师是队友还是对手 (fateFlag, 名字着色),
    以及上把用的英雄 (recentlyChampionName, 名字悬浮提示)"""
    if not detail:
        return

    try:
        teammates = getTeammates(detail, summoner['puuid'])
    except Exception:
        return

    if currentSummonerId in [t['summonerId'] for t in teammates['summoners']]:
        summoner['fateFlag'] = "ally"
    elif currentSummonerId in [t['summonerId'] for t in teammates['enemies']]:
        summoner['fateFlag'] = "enemy"

    championId = max(teammates.get('championId', -1), 0)  # 自定义对局等取不到时是 -1
    summoner['recentlyChampionName'] = connector.manager.champs.get(championId) or ""


def needsScores(summoner):
    """该玩家的近期对局里还有没算过 MVP / SVP 的 (LCU 战绩; SGP 战绩解析时已算好)
    最近一局没算过时, 上一局关系 (fateFlag) 也还没判断"""
    return any(game and 'scoreRank' not in game and not game.get('scoreChecked')
               for game in (summoner or {}).get('gamesInfo') or [])


async def attachSummonerScores(summoners):
    """
    对局信息页第二阶段: 面板先按基础战绩显示, 再为每名玩家近期对局补上 MVP / SVP / 名次 /
    参团率 (每局一个完整对局请求). 直接修改 gamesInfo 里的对局字典, 界面随后重画即可.
    取不到的局标记为已检查, 不会反复请求.
    """
    async def one(summoner):
        allGames = summoner.get('gamesInfo') or []
        games = [game for game in allGames
                 if game and 'scoreRank' not in game and not game.get('scoreChecked')]
        if not games:
            return

        details = await attachGameScores(games, summoner['puuid'])
        for game in games:
            game['scoreChecked'] = True

        # 最近一局的完整对局顺便判断上把是不是队友 / 对手
        if allGames and games[0] is allGames[0] and summoner.get('relationTo'):
            applyLastGameRelation(summoner, details[0], summoner['relationTo'])

    await asyncio.gather(*[one(s) for s in summoners if s])


def summarizeGames(games, championId=0, limit=3):
    """
    对局信息页左侧概览用的近期战绩汇总 (OP.GG 风格), 只用已拉取的 games, 不发请求.

    @param games: parseGameData / parseGamesDataFromSGP 的结果 (允许含 None)
    @param championId: 当前所选英雄, 为 0 时不统计 currentChampion
    @return: {
        'topChampions': 最常用的至多 limit 个英雄
            [{'championId', 'icon', 'games', 'wins', 'losses', 'kda'}],
        'positions': 排位分路按场次降序 [(positionKey, count)],
        'streak': 最近连续的非重开局 ('win' | 'lose', n), 没有则 None,
        'currentChampion': 当前英雄的 {'games', 'wins', 'losses', 'kda'}, 没有则 None,
        'mvp' / 'svp': 拿到 MVP / SVP 的局数,
        'games': 非重开局总数,
    }
    重开局不计入胜负与 KDA.
    """
    games = [g for g in games or [] if g and not g.get('remake')]

    champions = {}
    positions = {}

    for game in games:
        cid = game['championId']
        item = champions.setdefault(cid, {
            'championId': cid, 'icon': game.get('championIcon'),
            'games': 0, 'wins': 0, 'losses': 0,
            'kills': 0, 'deaths': 0, 'assists': 0})

        item['games'] += 1
        item['wins' if game['win'] else 'losses'] += 1
        item['kills'] += game['kills']
        item['deaths'] += game['deaths']
        item['assists'] += game['assists']

        key = game.get('positionKey')
        if key:
            positions[key] = positions.get(key, 0) + 1

    def finish(item):
        ret = {k: item[k] for k in
               ('championId', 'icon', 'games', 'wins', 'losses')}
        ret['kda'] = (item['kills'] + item['assists']) / max(item['deaths'], 1)
        return ret

    # 场次相同的英雄保持最近出场的顺序 (dict 按首次出现排序, sort 稳定)
    topChampions = sorted(champions.values(),
                          key=lambda x: x['games'], reverse=True)[:limit]

    streak = None
    if games:
        result = games[0]['win']
        n = 0
        for game in games:
            if game['win'] != result:
                break
            n += 1
        streak = ('win' if result else 'lose', n)

    current = champions.get(championId) if championId else None

    return {
        'games': len(games),
        'mvp': sum(1 for g in games if g.get('badge') == 'MVP'),
        'svp': sum(1 for g in games if g.get('badge') == 'SVP'),
        'topChampions': [finish(item) for item in topChampions],
        'positions': sorted(positions.items(), key=lambda x: x[1], reverse=True),
        'streak': streak,
        'currentChampion': finish(current) if current else None,
    }


# 对局信息页 Insights 胶囊的阈值
# 绝活: 当前英雄是熟练度第一且分数够高, 或在近期 (非重开) 对局里占比够高
MAIN_CHAMPION_POINTS = 100000
MAIN_CHAMPION_SHARE = 0.4
MAIN_CHAMPION_MIN_GAMES = 4
STREAK_MIN = 3


def playerInsights(summary, mastery, championId):
    """
    对局信息页玩家卡片的 Insights (按重要性排序), 纯数据, 文案和颜色由界面决定.

    @param summary: summarizeGames 的结果
    @param mastery: {championId: {'level', 'points'}} (玩家全部英雄)
    @param championId: 当前所选英雄, 0 表示还没选 (不出绝活标签)
    @return: [
        {'kind': 'main', 'tone': 'good', 'byMastery', 'byShare', 'points', 'games', 'total'},
        {'kind': 'streak', 'tone': 'good' | 'bad', 'result': 'win' | 'lose', 'count'},
    ]
    """
    insights = []
    mastery = mastery or {}

    if championId:
        points = (mastery.get(championId) or {}).get('points') or 0
        top = max(((m or {}).get('points') or 0 for m in mastery.values()), default=0)
        byMastery = points >= MAIN_CHAMPION_POINTS and points >= top

        current = summary.get('currentChampion')
        games = current['games'] if current else 0
        total = summary.get('games', 0)
        byShare = games >= MAIN_CHAMPION_MIN_GAMES and games >= MAIN_CHAMPION_SHARE * total

        if byMastery or byShare:
            insights.append({'kind': 'main', 'tone': 'good', 'byMastery': byMastery,
                             'byShare': byShare, 'points': points, 'games': games,
                             'total': total})

    streak = summary.get('streak')
    if streak and streak[1] >= STREAK_MIN:
        insights.append({'kind': 'streak', 'tone': 'good' if streak[0] == 'win' else 'bad',
                         'result': streak[0], 'count': streak[1]})

    return insights


async def revealHiddenAllies(myTeam, currentPuuid):
    '''
    排位选人里 Riot 把队友标成 HIDDEN (summonerId 0), 原逻辑直接放弃这些槽位.
    这里用 Riot Client 聊天房名单 (connector.getChampSelectChatParticipants) 把
    他们补回来, 合并规则见 chat_reveal.mergeHiddenTeammates. 任何失败都退回原 myTeam.
    '''
    try:
        if not any(isHiddenSlot(item) for item in myTeam):
            return myTeam

        participants = await connector.getChampSelectChatParticipants()
        merged = mergeHiddenTeammates(myTeam, participants, currentPuuid)

        visible, revealed, synthesized, hiddenLeft = revealStats(merged)
        logger.critical(
            f"reveal-hidden: 可见 {visible}/揭示 {revealed}/合成 {synthesized}"
            f" (仍隐藏 {hiddenLeft}, 名单 {len(participants)})", TAG)
        return merged
    except Exception as e:
        logger.exception(
            "reveal-hidden: merge failed, using original myTeam", e, TAG)
        return myTeam


async def fillRevealedSummonerIds(team):
    '''
    LCU 路径 (parseSummonerGameInfo) 按 summonerId 取数, 而揭示出来的槽位只有 puuid;
    这里用 /lol-summoner/v2/summoners/puuid/{puuid} 补上 summonerId. 补不上就保持 0,
    下游会像原来一样返回 None (该槽位不显示), 不影响其他人. 未揭示过的 team 是 no-op.
    '''
    async def fill(item):
        try:
            summoner = await connector.getSummonerByPuuid(item['puuid'])
            item['summonerId'] = summoner.get('summonerId') or 0
        except Exception as e:
            logger.error(
                f"reveal-hidden: summonerId lookup failed for {item.get('puuid')}: {e!r}", TAG)

    tasks = [fill(item) for item in team
             if item.get('revealed') and not item.get('summonerId')]
    if tasks:
        await asyncio.gather(*tasks)


async def parseAllyGameInfo(session, currentSummonerId, useSGP=False, currentPuuid=None,
                            cache=None, onPartial=None, queueId=None):
    '''
    @param queueId: 这次选人的队列 (选人 session 里不一定有, 由调用方从 gameflow 取);
        战绩按它过滤, 见 historyQueueFilter
    @param cache: {puuid: summoner_dict} 同一次选人内复用; 队友陆续进入聊天房后重新解析时
        (见 MainWindow.__refreshHiddenAllies), 只为新揭示的队友发请求
    '''
    if cache is None:
        cache = {}

    queueFilter = historyQueueFilter(queueId)

    myTeam = session['myTeam']

    # 排位里被 Riot 隐藏的队友: 用 Riot Client 聊天房名单补回 (开关关则跳过)
    if cfg.get(cfg.revealHiddenTeammates):
        myTeam = await revealHiddenAllies(myTeam, currentPuuid)

    def cached(fetch):
        async def resolve(item):
            puuid = item.get('puuid')
            if puuid and cache.get(puuid):
                summoner = dict(cache[puuid])
                summoner['cellId'] = item.get('cellId')
                summoner['selectedPosition'] = item.get('selectedPosition')
                championId = item.get('championId') or 0
                if championId != summoner['championId']:
                    summoner['championId'] = championId
                    summoner['icon'] = await connector.getChampionIcon(championId)
                return summoner

            summoner = await fetch(item, queueFilter, currentSummonerId)
            if summoner and summoner.get('puuid'):
                cache[summoner['puuid']] = summoner
            return summoner

        return resolve

    def build(summoners):
        summoners = [summoner for summoner in summoners if summoner]

        # 按照楼层排序
        summoners = sorted(
            summoners, key=lambda x: x["cellId"])

        champions = {summoner['summonerId']: summoner['championId']
                     for summoner in summoners}
        order = [summoner['summonerId'] for summoner in summoners]

        return {'summoners': summoners, 'champions': champions, 'order': order,
                "isAram": session.get('benchEnabled', False)}

    if useSGP and connector.isInTencent():
        # 如果是国服就优先尝试 SGP
        try:
            resolve = cached(getSummonerGamesInfoViaSGP)
            summoners = await gatherProgressively(
                [resolve(item) for item in myTeam], onPartial, build)
        except:
            await fillRevealedSummonerIds(myTeam)
            resolve = cached(parseSummonerGameInfo)
            summoners = await gatherProgressively(
                [resolve(item) for item in myTeam], onPartial, build)

    else:
        await fillRevealedSummonerIds(myTeam)
        resolve = cached(parseSummonerGameInfo)
        summoners = await gatherProgressively(
                [resolve(item) for item in myTeam], onPartial, build)

    return build(summoners)


def visibleEnemies(session):
    """
    选人阶段能看到身份的敌方 (自定义 / 锦标赛征召等模式); 排位里 Riot 隐藏敌方,
    theirTeam 的 puuid / summonerId 为空, 这时返回 [] (敌方战绩要等进游戏后才能查)
    """
    return [m for m in (session or {}).get('theirTeam') or []
            if isinstance(m, dict) and m.get('playerType') != 'BOT'
            and m.get('nameVisibilityType') != 'HIDDEN'
            and (m.get('summonerId') or m.get('puuid'))]


async def parseEnemyChampSelectInfo(session, currentSummonerId, useSGP=False, cache=None,
                                    onPartial=None, queueId=None):
    """
    选人阶段的敌方战绩 (只含能看到身份的敌方, 见 visibleEnemies), 返回值同 parseAllyGameInfo.
    @param cache: {puuid: summoner_dict} 同一次选人内复用, 选人事件反复触发时不重复请求
    @param queueId: 这次选人的队列, 战绩按它过滤 (见 historyQueueFilter)
    """
    if cache is None:
        cache = {}

    queueFilter = historyQueueFilter(queueId)

    async def resolve(item):
        puuid = item.get('puuid')
        if puuid and cache.get(puuid):
            summoner = dict(cache[puuid])
            summoner['cellId'] = item.get('cellId')
            championId = item.get('championId') or 0
            if championId != summoner['championId']:
                summoner['championId'] = championId
                summoner['icon'] = await connector.getChampionIcon(championId)
            return summoner

        if useSGP and connector.isInTencent() and puuid:
            try:
                summoner = await getSummonerGamesInfoViaSGP(item, queueFilter, currentSummonerId)
            except Exception:
                summoner = None
        else:
            if not item.get('summonerId') and puuid:
                # 只有 puuid 时补上 summonerId (LCU 战绩路径按 summonerId 取召唤师)
                try:
                    found = await connector.getSummonerByPuuid(puuid)
                    item = dict(item, summonerId=found.get('summonerId') or 0)
                except Exception:
                    return None
            summoner = await parseSummonerGameInfo(item, queueFilter, currentSummonerId)

        if summoner and summoner.get('puuid'):
            cache[summoner['puuid']] = summoner
        return summoner

    def build(summoners):
        summoners = sorted([s for s in summoners if s], key=lambda x: x["cellId"])
        return {'summoners': summoners,
                'champions': {s['summonerId']: s['championId'] for s in summoners},
                'order': [s['summonerId'] for s in summoners]}

    summoners = await gatherProgressively(
        [resolve(item) for item in visibleEnemies(session)], onPartial, build)

    return build(summoners)


def splitCustomLobbyTeams(lobby, currentSummonerId, currentPuuid=None):
    """
    Split a custom-game lobby into (allyMembers, enemyMembers) by which custom team list
    each member is in: the local player's team is the ally side; a spectator (in neither
    team) sees team 100 as ally and team 200 as enemy. Bots and spectators are dropped,
    list order is kept. Returns None when the lobby is not a custom game.
    """
    config = (lobby or {}).get('gameConfig') or {}
    if not config.get('isCustom'):
        return None

    if not currentPuuid:
        currentPuuid = ((lobby or {}).get('localMember') or {}).get('puuid')

    def humans(team):
        return [m for m in team or []
                if isinstance(m, dict) and m.get('puuid')
                and not m.get('isBot') and not m.get('botId')
                and not m.get('isSpectator')]

    def isMe(m):
        return (bool(currentSummonerId) and m.get('summonerId') == currentSummonerId) \
            or (bool(currentPuuid) and m.get('puuid') == currentPuuid)

    team100 = humans(config.get('customTeam100'))
    team200 = humans(config.get('customTeam200'))

    if any(isMe(m) for m in team200):
        return team200, team100

    return team100, team200


def lobbyHistoryQueueFilter(lobby):
    """historyQueueFilter for a lobby; custom lobbies (queueId 3120 etc.) are never filtered"""
    config = (lobby or {}).get('gameConfig') or {}
    if config.get('isCustom'):
        return None

    return historyQueueFilter(config.get('queueId', -1))


async def parseLobbyGameInfo(lobby, currentSummonerId, useSGP=False, cache=None,
                             onPartial=None, members=None):
    '''
    解析组队大厅成员战绩, 复用选人阶段的 per-summoner 取数逻辑。

    @param lobby: connector.getLobby() 的返回值
    @param cache: {puuid: summoner_dict} 复用缓存, 只查询新加入的成员 (避免 SGP 刷爆)
    @param members: members to resolve, in display order (one custom-lobby team, see
                    splitCustomLobbyTeams); defaults to lobby['members']
    @return: 与 @see: parseAllyGameInfo 一致的 {summoners, champions, order, isAram}
    '''
    if cache is None:
        cache = {}

    if members is None:
        members = lobby.get('members') or []
    queueId = (lobby.get('gameConfig') or {}).get('queueId', -1)
    queueFilter = lobbyHistoryQueueFilter(lobby)

    # 国服优先 SGP, 否则走 LCU
    fetch = getSummonerGamesInfoViaSGP if (
        useSGP and connector.isInTencent()) else parseSummonerGameInfo

    async def resolve(idx, member):
        puuid = member.get('puuid')

        # 已查询过的成员直接复用缓存, 仅更新楼层顺序 (只对新成员发起请求)
        if puuid and cache.get(puuid):
            summoner = dict(cache[puuid])
            summoner['cellId'] = idx
            return summoner

        # 用大厅成员合成 champ-select 风格的 item, 复用现成 helper
        item = {
            'puuid': puuid,
            'summonerId': member.get('summonerId'),
            'championId': 0,
            'cellId': idx,
            'selectedPosition': None,
        }
        summoner = await fetch(item, queueFilter, currentSummonerId)
        if summoner and puuid:
            cache[puuid] = summoner
        return summoner

    def build(summoners):
        summoners = [s for s in summoners if s]
        summoners = sorted(summoners, key=lambda x: x["cellId"])

        champions = {s['summonerId']: s['championId'] for s in summoners}
        order = [s['summonerId'] for s in summoners]

        return {'summoners': summoners, 'champions': champions, 'order': order,
                "isAram": queueId == 450}

    summoners = await gatherProgressively(
        [resolve(idx, m) for idx, m in enumerate(members)], onPartial, build)

    return build(summoners)


def parseSummonerOrder(team):
    summoners = [{
        'summonerId': s['summonerId'],
        'cellId': s['cellId']
    } for s in team]

    summoners.sort(key=lambda x: x['cellId'])
    return [s['summonerId'] for s in summoners if s['summonerId'] != 0]


async def parseGameInfoByGameflowSession(session, currentSummonerId, side, useSGP=False,
                                         onPartial=None):
    data = session['gameData']
    queueId = data['queue']['id']

    if queueId in (1700, 1090, 1100, 1110, 1130, 1160):  # 斗魂 云顶匹配 (排位)
        return None

    isRank = queueId in (420, 440)
    queueFilter = historyQueueFilter(queueId)

    if side == 'enemy':
        _, team = separateTeams(data, currentSummonerId)
    else:
        team, _ = separateTeams(data, currentSummonerId)

    def build(summoners):
        summoners = [summoner for summoner in summoners if summoner]

        if isRank:
            s = sortedSummonersByGameRole(summoners)

            if s != None:
                summoners = s

        champions = {summoner['summonerId']: summoner['championId']
                     for summoner in summoners}
        order = [summoner['summonerId'] for summoner in summoners]

        return {'summoners': summoners, 'champions': champions, 'order': order}

    if useSGP and connector.isInTencent():
        # 如果是国服就优先尝试 SGP
        try:
            summoners = await gatherProgressively(
                [getSummonerGamesInfoViaSGP(item, queueFilter, currentSummonerId) for item in team],
                onPartial, build)

        except:
            summoners = await gatherProgressively(
                [parseSummonerGameInfo(item, queueFilter, currentSummonerId) for item in team],
                onPartial, build)

    else:
        summoners = await gatherProgressively(
            [parseSummonerGameInfo(item, queueFilter, currentSummonerId) for item in team],
            onPartial, build)

    return build(summoners)


def sortedSummonersByGameRole(summoners: list):
    position = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]

    if any(x['selectedPosition'] not in position for x in summoners):
        return None

    return sorted(summoners,
                  key=lambda x: position.index(x['selectedPosition']))


def getAllyOrderByGameRole(session, currentSummonerId):
    data = session['gameData']
    queueId = data['queue']['id']

    # 只有排位模式下有返回值
    if queueId not in (420, 440):
        return None

    ally, _ = separateTeams(data, currentSummonerId)
    ally = sortedSummonersByGameRole(ally)

    if ally == None:
        return None

    return [x['summonerId'] for x in ally]


def getTeamColor(session, currentSummonerId):
    '''
    输入 session 以及当前召唤师 id，输出 summonerId -> 颜色的映射
    '''
    data = session['gameData']
    ally, enemy = separateTeams(data, currentSummonerId)

    def makeTeam(team):
        # teamParticipantId => [summonerId]
        tIdToSIds = {}

        for s in team:
            summonerId = s.get('summonerId')
            if not summonerId:
                continue

            teamParticipantId = s.get('teamParticipantId')
            if not teamParticipantId:
                continue

            summoners = tIdToSIds.get(teamParticipantId)

            if not summoners:
                tIdToSIds[teamParticipantId] = [summonerId]
            else:
                tIdToSIds[teamParticipantId].append(summonerId)

        # summonerId => color
        res = {}

        currentColor = 0

        for ids in tIdToSIds.values():
            if len(ids) == 1:
                res[ids[0]] = -1
            else:
                for id in ids:
                    res[id] = currentColor

                currentColor += 1

        return res

    return makeTeam(ally), makeTeam(enemy)


def separateTeams(data, currentSummonerId):
    team1 = data['teamOne']
    team2 = data['teamTwo']
    ally = None
    enemy = None

    for summoner in team1:
        if summoner.get('summonerId') == currentSummonerId:
            ally = team1
            enemy = team2
            break
    else:
        ally = team2
        enemy = team1

    return ally, enemy


async def parseGamesDataConcurrently(games):
    tasks = [parseGameData(game) for game in games]
    return await asyncio.gather(*tasks)


def historyQueueFilter(queueId):
    """
    对局信息页的战绩该按哪个队列过滤: 开关 (cfg.gameInfoFilter) 打开且当前是匹配队列时
    返回该 queueId (单双只看单双, 灵活只看灵活, 大乱斗只看大乱斗 ...), 否则 None (不过滤).
    自定义 / 训练模式 (queueId 0 / -1) 不过滤
    """
    if not cfg.get(cfg.gameInfoFilter):
        return None

    return queueId if isinstance(queueId, int) and queueId > 0 else None


def mergeQueuePage(games, page, seen, key, queueId):
    """
    把一页战绩里属于 queueId 队列的对局追加到 games, 跳过 seen 里已有的 gameId.

    @param seen: 已见过的 gameId 集合 (含其他队列的对局), 会被更新
    @param key: game -> (queueId, gameId)
    @return: 这一页有没有新的对局 (不论哪个队列); 没有说明已翻到历史尽头
    """
    fresh = False
    for game in page:
        gameQueueId, gameId = key(game)
        if gameId in seen:
            continue

        seen.add(gameId)
        fresh = True
        if gameQueueId == queueId:
            games.append(game)

    return fresh


async def collectQueueGames(firstPage, fetchPage, key, queueId, step, want=11):
    """
    只留 queueId 队列的对局: 第一页 (最近 15 局) 不够 want 局就继续往前翻, 最多翻到第 95 局.

    @param fetchPage: async (begIdx, endIdx) -> 一页对局
    @param step: 每页跨度 (LCU 5, SGP 10)
    @return: 该队列的对局. 排位 (420 / 440) 一局都没有就返回空; 其他队列一局都没有时
             (比如刚上线的轮换模式) 退回第一页的全部对局, 免得面板全空
    """
    games, seen = [], set()
    mergeQueuePage(games, firstPage, seen, key, queueId)

    begIdx = 15
    while len(games) < want and begIdx <= 95:
        endIdx = begIdx + step
        page = await fetchPage(begIdx, endIdx)

        # 超出历史范围时 LCU 会重复返回已拿到的对局: 去重, 整页都是旧的就停
        if not mergeQueuePage(games, page, seen, key, queueId):
            break

        begIdx = endIdx + 1

    if not games and queueId not in (420, 440):
        return firstPage

    return games


async def parseSummonerGameInfo(item, queueId, currentSummonerId):
    '''
    @param queueId: 只取这个队列的战绩 (见 historyQueueFilter); None 为不过滤
    '''
    summonerId = item.get('summonerId', None)

    if item.get('nameVisibilityType') == 'HIDDEN':
        return None

    if summonerId == 0 or summonerId == None:
        return None

    summoner = await connector.getSummonerById(summonerId)

    championId = item.get('championId') or 0
    icon = await connector.getChampionIcon(championId)

    puuid = summoner.get("puuid", None)

    if puuid == "00000000-0000-0000-0000-000000000000" or not puuid:
        return None

    # 英雄成就 (熟练度) 与战绩并行拉取, 失败为 {}
    masteryTask = asyncio.ensure_future(connector.getChampionMasteryQuietly(puuid))

    try:
        origRankInfo = await connector.getRankedStatsByPuuid(puuid)
    except SummonerRankInfoNotFound:
        origRankInfo = None

    rankInfo = parseRankInfo(origRankInfo)

    try:
        origGamesInfo = await connector.getSummonerGamesByPuuid(
            puuid, 0, 14)

        if queueId is not None:
            def lcuKey(game):
                return game["queueId"], game["gameId"]

            async def lcuPage(begIdx, endIdx):
                return (await connector.getSummonerGamesByPuuid(puuid, begIdx, endIdx))["games"]

            origGamesInfo["games"] = await collectQueueGames(
                origGamesInfo["games"], lcuPage, lcuKey, queueId, 5)
    except:
        gamesInfo = []
    else:
        tasks = [parseGameData(game)
                 for game in origGamesInfo["games"][:11]]
        gamesInfo = await asyncio.gather(*tasks)

    # 上一局是不是队友 / 对手 (名字着色) 和各局的 MVP / SVP 都要完整对局, 冷启动时每个请求
    # 要几秒; 先返回基础战绩让面板显示, 由界面在后台补 (attachSummonerScores)
    _, kill, deaths, assists, wins, losses = parseGames(gamesInfo)

    recentlyChampionName = ""
    fateFlag = None

    return {
        "name": summoner.get("gameName") or summoner.get("internalName"),
        'tagLine': summoner.get("tagLine"),
        "icon": icon,
        'championId': championId,
        "level": summoner["summonerLevel"],
        "rankInfo": rankInfo,
        "gamesInfo": gamesInfo,
        "xpSinceLastLevel": summoner["xpSinceLastLevel"],
        "xpUntilNextLevel": summoner["xpUntilNextLevel"],
        "puuid": puuid,
        "summonerId": summonerId,
        "kda": [kill, deaths, assists],
        "wins": wins,
        "losses": losses,
        "remakes": sum(1 for g in gamesInfo if g and g.get('remake')),
        "totalGames": sum(1 for g in gamesInfo if g),
        "cellId": item.get("cellId"),
        "selectedPosition": item.get("selectedPosition"),
        "fateFlag": fateFlag,
        # 上一局关系 (fateFlag) 相对于谁; 由 attachSummonerScores 在后台补
        "relationTo": currentSummonerId,
        "isPublic": summoner["privacy"] == "PUBLIC",
        # 最近游戏的英雄 (用于上一局与与同一召唤师游玩之后显示)
        "recentlyChampionName": recentlyChampionName,
        # {championId: {'level', 'points'}}
        "mastery": await masteryTask,
    }


async def getSummonerGamesInfoViaSGP(item, queueId, currentSummonerId):
    '''
    使用 SGP 接口取战绩信息
    @param queueId: 只取这个队列的战绩 (见 historyQueueFilter); None 为不过滤
    '''
    puuid = item.get('puuid')

    if item.get('nameVisibilityType') == 'HIDDEN':
        return None

    if puuid == "00000000-0000-0000-0000-000000000000" or not puuid:
        return None

    # 英雄成就 (熟练度) 与战绩并行拉取, 失败为 {}
    masteryTask = asyncio.ensure_future(connector.getChampionMasteryQuietly(puuid))

    championId = item.get('championId') or 0
    icon = await connector.getChampionIcon(championId)
    summoner = await connector.getSummonerByPuuidViaSGP(puuid)

    try:
        origRankInfo = await connector.getRankedStatsByPuuidViaSGP(puuid)
    except SummonerRankInfoNotFound:
        origRankInfo = None

    rankInfo = parseRankInfoFromSGP(origRankInfo)

    # 默认取 summoner 自身的名字 / tagLine; 若下方能从最近一局解析到更准确的再覆盖.
    # 也保证 except 分支(SGP 拉取失败)有已定义的 summonerName/tagLine, 避免 NameError
    summonerName = summoner.get("gameName")
    tagLine = summoner.get("tagLine")

    try:
        origGamesInfo = await connector.getSummonerGamesByPuuidViaSGP(puuid, 0, 14)

        if queueId is not None:
            def sgpKey(game):
                return game['json']["queueId"], game['json']["gameId"]

            async def sgpPage(begIdx, endIdx):
                return (await connector.getSummonerGamesByPuuidViaSGP(
                    puuid, begIdx, endIdx))["games"]

            origGamesInfo["games"] = await collectQueueGames(
                origGamesInfo["games"], sgpPage, sgpKey, queueId, 10)
    except:
        # SGP 拉取失败: 绑定空 origGamesInfo, 否则下方 teammatesInfo 的
        # origGamesInfo['games'][:1] 会 UnboundLocalError
        origGamesInfo = {"games": []}
        gamesInfo = []
    else:
        # 空战绩(新号 / 被过滤为空)时 games[0] 会 IndexError; getNameTagLineFromGame
        # 也可能返回 None. 两种情况都保留上面的默认名字, 不覆盖
        games = origGamesInfo['games']
        nameTag = getNameTagLineFromGame(games[0], puuid) if games else None
        if nameTag:
            summonerName, tagLine = nameTag

        tasks = [parseGamesDataFromSGP(game, puuid)
                 for game in origGamesInfo["games"][:11]]
        gamesInfo = await asyncio.gather(*tasks)

    _, kill, deaths, assists, wins, losses = parseGames(gamesInfo)

    teammatesInfo = [
        getTeammatesFromSGPGame(
            game,
            puuid
        ) for game in origGamesInfo['games'][:1]  # 避免空报错, 查上一局的队友(对手)
    ]

    recentlyChampionName = ""
    fateFlag = None

    if teammatesInfo:  # 判个空, 避免太久没有打游戏的玩家或新号引发异常
        if currentSummonerId in [t['summonerId'] for t in teammatesInfo[0]['summoners']]:
            # 上把队友
            fateFlag = "ally"
        elif currentSummonerId in [t['summonerId'] for t in teammatesInfo[0]['enemies']]:
            # 上把对面
            fateFlag = "enemy"
        recentlyChampionId = max(
            teammatesInfo and teammatesInfo[0]['championId'], 0)  # 取不到时是-1, 如果-1置为0
        recentlyChampionName = connector.manager.champs.get(
            recentlyChampionId)

    # 适用于 LCU API 返回值
    # return {
    #     "name": summoner.get("gameName"),
    #     'tagLine': summoner.get('tagLine'),
    #     "icon": icon,
    #     'championId': championId,
    #     "level": summoner["summonerLevel"],
    #     "rankInfo": rankInfo,
    #     "gamesInfo": gamesInfo,
    #     "xpSinceLastLevel": summoner["xpSinceLastLevel"],
    #     "xpUntilNextLevel": summoner["xpUntilNextLevel"],
    #     "puuid": puuid,
    #     "summonerId": summoner['summonerId'],
    #     "kda": [kill, deaths, assists],
    #     "cellId": item.get("cellId"),
    #     "selectedPosition": item.get("selectedPosition"),
    #     "fateFlag": fateFlag,
    #     "isPublic": summoner["privacy"] == "PUBLIC",
    #     # 最近游戏的英雄 (用于上一局与与同一召唤师游玩之后显示)
    #     "recentlyChampionName": recentlyChampionName
    # }

    # 适用于 SGP API 返回值
    return {
        "name": summonerName,
        'tagLine': tagLine,
        "icon": icon,
        'championId': championId,
        "level": summoner["level"],
        "rankInfo": rankInfo,
        "gamesInfo": gamesInfo,
        "xpSinceLastLevel": summoner["expPoints"],
        "xpUntilNextLevel": summoner["expToNextLevel"],
        "puuid": puuid,
        "summonerId": summoner['id'],
        "kda": [kill, deaths, assists],
        "wins": wins,
        "losses": losses,
        "remakes": sum(1 for g in gamesInfo if g and g.get('remake')),
        "totalGames": sum(1 for g in gamesInfo if g),
        "cellId": item.get("cellId"),
        "selectedPosition": item.get("selectedPosition"),
        "fateFlag": fateFlag,
        "isPublic": summoner["privacy"] == "PUBLIC",
        # 最近游戏的英雄 (用于上一局与与同一召唤师游玩之后显示)
        "recentlyChampionName": recentlyChampionName,
        # {championId: {'level', 'points'}}
        "mastery": await masteryTask,
    }


def getTeammatesFromSGPGame(game, puuid):
    json = game['json']
    queueId = json['queueId']

    for player in json['participants']:
        if player['puuid'] == puuid:
            if queueId != 1700:
                tid = player['teamId']
            else:  # 斗魂竞技场
                tid = player['subteamPlacement']

            win = player['win']
            remake = player['teamEarlySurrendered']

            break

    res = {
        'queueId': queueId,
        'win': win,
        'remake': remake,
        'summoners': [],  # 队友召唤师 (由于兼容性, 未修改字段名)
        'enemies': []  # 对面召唤师, 若有多个队伍会全放这里面
    }

    for player in json['participants']:
        if queueId != 1700:
            cmp = player['teamId']
        else:
            cmp = player['subteamPlacement']

        # Riot ID 账号的 summonerName 为空, 名字在 riotIdGameName (+ riotIdTagline)
        entry = {
            'summonerId': player['summonerId'],
            'name': player.get('riotIdGameName') or player.get('summonerName') or '',
            'tagLine': player.get('riotIdTagline') or '',
            'puuid': player['puuid'],
            'icon': player['profileIcon']
        }

        if cmp == tid:
            if player['puuid'] != puuid:
                res['summoners'].append(entry)
            else:
                # 当前召唤师在该对局使用的英雄, 自定义对局没有该字段
                res["championId"] = player.get('championId', -1)
        else:
            res['enemies'].append(entry)

    return res


async def parseGamesDataFromSGP(game, puuid):
    """
    解析由 SGP 接口得到的具体到某一局的对局记录信息
    """

    game = game['json']

    timeStamp = game["gameCreation"]  # 毫秒级时间戳
    time = timeStampToStr(game['gameCreation'])
    shortTime = timeStampToShortStr(game['gameCreation'])
    gameId = game['gameId']
    duration = secsToStr(game['gameDuration'])
    queueId = game['queueId']

    nameAndMap = connector.manager.getNameMapByQueueId(queueId)
    modeName = nameAndMap['name']

    if queueId != 0:
        mapName = nameAndMap['map']
    else:
        mapName = connector.manager.getMapNameById(game['mapId'])

    participant = None
    for p in game['participants']:
        if p['puuid'] == puuid:
            participant = p

    championId = participant['championId']
    championIcon = await connector.getChampionIcon(championId)
    spell1Id = participant['spell1Id']
    spell2Id = participant['spell2Id']
    spell1Icon = await connector.getSummonerSpellIcon(spell1Id)
    spell2Icon = await connector.getSummonerSpellIcon(spell2Id)

    champLevel = participant['champLevel']
    kills = participant['kills']
    deaths = participant['deaths']
    assists = participant['assists']

    itemIds = [
        participant['item0'],
        participant['item1'],
        participant['item2'],
        participant['item3'],
        participant['item4'],
        participant['item5'],
        participant['item6'],
    ]

    itemIcons = [await connector.getItemIcon(itemId) for itemId in itemIds]
    runeId = participant['perks']['styles'][0]['selections'][0]['perk']
    runeIcon = await connector.getRuneIcon(runeId)

    cs = participant['totalMinionsKilled'] + \
        participant['neutralMinionsKilled']
    gold = participant['goldEarned']
    damage = participant.get('totalDamageDealtToChampions', 0)
    visionScore = participant.get('visionScore', 0)
    multiKill = participant.get('largestMultiKill', 0)
    remake = participant['gameEndedInEarlySurrender']
    win = participant['win']

    positionKey = getPositionKey(
        queueId, participant['lane'], participant['role'])
    position = translatePositionKey(positionKey)

    # SGP 战绩本身带全部 10 人数据, MVP / SVP 不用额外请求
    score = getGameScoreFromSgpGame(game, puuid) or {}

    return {
        **score,
        'queueId': queueId,
        'gameId': gameId,
        'time': time,
        'shortTime': shortTime,
        'name': modeName,
        'map': mapName,
        'duration': duration,
        'durationSecs': game['gameDuration'],
        'remake': remake,
        'win': win,
        'championId': championId,
        'championIcon': championIcon,
        'spell1Icon': spell1Icon,
        'spell2Icon': spell2Icon,
        'champLevel': champLevel,
        'kills': kills,
        'deaths': deaths,
        'assists': assists,
        'itemIcons': itemIcons,
        'itemIds': itemIds,
        'runeIcon': runeIcon,
        'cs': cs,
        'gold': gold,
        'damage': damage,
        'visionScore': visionScore,
        'multiKill': multiKill,
        'gameMode': game.get('gameMode'),
        'timeStamp': timeStamp,
        'position': position,
        'positionKey': positionKey,
    }


def getNameTagLineFromGame(game, puuid):
    for player in game['json']['participants']:
        if player['puuid'] == puuid:
            return player['riotIdGameName'], player['riotIdTagline']

    return None


class ChampionSelection:
    def __init__(self):
        self.isChampionShowed = False
        self.isChampionBanned = False
        self.isChampionPicked = False
        self.isChampionPickedCompleted = False
        self.isSkinPicked = False
        self.opggShowChampionId = None

    def reset(self):
        self.__init__()


async def autoSwap(data, selection: ChampionSelection):
    """
    选用顺序交换请求发生时，自动接受
    """

    if not cfg.get(cfg.autoAcceptCeilSwap):
        return

    for pickOrderSwap in data['pickOrderSwaps']:
        if 'RECEIVED' == pickOrderSwap['state']:
            await asyncio.sleep(0.5)
            await connector.acceptSwap(pickOrderSwap['id'])

            selection.isChampionPickedCompleted = False
            return True


async def autoTrade(data, selection):
    """
    英雄交换请求发生时，自动接受
    """
    if not cfg.get(cfg.autoAcceptChampTrade):
        return False

    for trade in data['trades']:
        if 'RECEIVED' == trade['state']:
            await asyncio.sleep(0.5)
            await connector.acceptTrade(trade['id'])

            return True

    return False


async def showOpggBuild(data, selection: ChampionSelection):
    cellId = data['localPlayerCellId']

    # 只有在英雄已经选定后才会尝试刷新 OPGG 界面
    for actionGroup in data['actions']:
        # 这里必须遍历完所有的 actorCellId == cellId 的所有 action
        for action in actionGroup:
            if not action['actorCellId'] == cellId:
                continue

            if action['type'] != 'pick':
                continue

            if not action['completed']:
                return False

    # 拿一下位置和英雄 ID
    for player in data['myTeam']:
        if player['cellId'] == cellId:
            position = player.get('assignedPosition', "")
            championId = player['championId'] or player['championPickIntent']
            break

    # 大乱斗模式下，即使锁定了也可能会换英雄，这里判断一下
    if championId == selection.opggShowChampionId:
        return False

    map = {
        'TOP': "TOP",
        'JUNGLE': "JUNGLE",
        'MIDDLE': "MID",
        'BOTTOM': "ADC",
        'UTILITY': "SUPPORT",
    }

    position = map.get(position, "")

    if championId == 0:
        return False

    # 判断一下是不是大乱斗
    if data.get('benchEnabled'):
        mode = "aram"
    # 不知道怎么判断斗魂竞技场，用这种方式判断一下吧
    elif len(data['myTeam']) == 2:
        mode = 'arena'
    else:
        mode = ""

    selection.opggShowChampionId = championId

    signalBus.toOpggBuildInterface.emit(championId, mode, position)

    return True


async def autoPick(data, selection: ChampionSelection):
    """
    自动选用英雄
    """

    if not cfg.get(cfg.enableAutoSelectChampion) or selection.isChampionPicked:
        return

    localPlayerCellId = data['localPlayerCellId']

    for player in data['myTeam']:
        if player["cellId"] != localPlayerCellId:
            continue

        if bool(player['championId']) or bool(player['championPickIntent']):
            selection.isChampionPicked = True
            return

        break

    bans = itertools.chain(data["bans"]['myTeamBans'],
                           data["bans"]['theirTeamBans'])

    pos = next(filter(lambda x: x['cellId'] ==
               localPlayerCellId, data['myTeam']), None)
    pos = pos.get('assignedPosition')

    if pos == 'top':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionTop))
    elif pos == 'jungle':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionJug))
    elif pos == 'middle':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionMid))
    elif pos == 'bottom':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionBot))
    elif pos == 'utility':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionSup))
    else:
        candidates = []

    candidates.extend(cfg.get(cfg.autoSelectChampion))

    candidates = [x for x in candidates if x not in bans]

    if not candidates:
        selection.isChampionPicked = True
        return

    championId = candidates[0]

    for actionGroup in reversed(data['actions']):
        for action in actionGroup:
            if (action["actorCellId"] == localPlayerCellId
                    and action['type'] == "pick"):

                await connector.selectChampion(action['id'], championId)
                selection.isChampionPicked = True
                return True


async def autoComplete(data, selection: ChampionSelection):
    """
    超时自动选定（当前选中英雄）
    """
    isAutoCompleted = cfg.get(cfg.enableAutoSelectTimeoutCompleted)
    if not isAutoCompleted or selection.isChampionPickedCompleted:
        return

    if not (localPlayerCellId := data.get('localPlayerCellId', None)):
        return

    for actionGroup in reversed(data['actions']):
        for action in actionGroup:
            if action['actorCellId'] != localPlayerCellId:
                continue

            if action['type'] != 'pick':
                continue

            if not action['isInProgress']:
                return False

            if action['completed']:
                selection.isChampionPickedCompleted = True
                return False

            break

    selection.isChampionPickedCompleted = True

    sleepTime = int(data['timer']['adjustedTimeLeftInPhase'] / 1000) - 2
    await asyncio.sleep(sleepTime)

    data = await connector.getChampSelectSession()

    if not data:
        return

    # 双方选过的英雄
    cantSelect = []

    # 双方 ban 掉的英雄
    bans = itertools.chain(data["bans"]['myTeamBans'],
                           data["bans"]['theirTeamBans'])

    championIntent = 0
    for actionGroup in data['actions']:
        for action in actionGroup:
            if (action['type'] == 'pick' and action['completed']
                    and action['actorCellId'] != localPlayerCellId):
                cantSelect.append(action['championId'])

            if action['actorCellId'] != localPlayerCellId:
                continue

            if action['type'] != 'pick':
                continue

            if action['completed']:
                return

            # 现在亮着的英雄
            championIntent = action['championId']
            actionId = action['id']

    if not championIntent:
        return

    cantSelect.extend(bans)

    if championIntent not in cantSelect:
        await connector.selectChampion(actionId, championIntent, True)
        return True

    pos = next(filter(lambda x: x['cellId'] ==
               localPlayerCellId, data['myTeam']), None)
    pos = pos.get('assignedPosition')

    if pos == 'top':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionTop))
    elif pos == 'jungle':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionJug))
    elif pos == 'middle':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionMid))
    elif pos == 'bottom':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionBot))
    elif pos == 'utility':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionSup))
    else:
        candidates = []

    candidates.extend(cfg.get(cfg.autoSelectChampion))

    candidates = [x for x in candidates if x not in cantSelect]

    if len(candidates) == 0:
        return

    await connector.selectChampion(actionId, candidates[0], True)

    return True


async def autoBan(data, selection: ChampionSelection):
    """
    自动禁用英雄
    """
    isAutoBan = cfg.get(cfg.enableAutoBanChampion)
    isEmptyBan = cfg.get(cfg.enableEmptyBan)

    if (not isAutoBan and not isEmptyBan) or selection.isChampionBanned:
        return

    localPlayerCellId = data['localPlayerCellId']
    for actionGroup in data['actions']:
        for action in actionGroup:
            if (action["actorCellId"] == localPlayerCellId
                    and action['type'] == 'ban'
                    and action["isInProgress"]):

                # 空ban：到自己的 ban 位时，延迟后选 "None"(championId=-1) 完成 ban
                # championId=0 的 PATCH 与 /complete 均被客户端拒绝，故改用 -1 并打日志确认
                # 优先级高于按英雄自动 ban
                if isEmptyBan:
                    await asyncio.sleep(cfg.get(cfg.autoBanDelay))
                    await connector.emptyBan(action['id'])
                    selection.isChampionBanned = True
                    return True

                pos = next(
                    filter(lambda x: x['cellId'] == localPlayerCellId, data['myTeam']), None)
                pos = pos.get('assignedPosition')

                if pos == 'top':
                    candidates = deepcopy(cfg.get(cfg.autoBanChampionTop))
                elif pos == 'jungle':
                    candidates = deepcopy(cfg.get(cfg.autoBanChampionJug))
                elif pos == 'middle':
                    candidates = deepcopy(cfg.get(cfg.autoBanChampionMid))
                elif pos == 'bottom':
                    candidates = deepcopy(cfg.get(cfg.autoBanChampionBot))
                elif pos == 'utility':
                    candidates = deepcopy(cfg.get(cfg.autoBanChampionSup))
                else:
                    candidates = []

                candidates.extend(cfg.get(cfg.autoBanChampion))

                bans = itertools.chain(data["bans"]['myTeamBans'],
                                       data["bans"]['theirTeamBans'])
                candidates = [x for x in candidates if x not in bans]

                # 给队友一点预选的时间
                await asyncio.sleep(cfg.get(cfg.autoBanDelay))

                isFriendly = cfg.get(cfg.pretentBan)
                if isFriendly:
                    myTeam = (await connector.getChampSelectSession()).get("myTeam")

                    if not myTeam:
                        return

                    intents = [player["championPickIntent"]
                               for player in myTeam]
                    candidates = [x for x in candidates if x not in intents]

                if not candidates:
                    return

                championId = candidates[0]
                await connector.banChampion(action['id'], championId, True)
                selection.isChampionBanned = True

                return True


async def autoShow(data, selection: ChampionSelection):
    '''在 B/P 前展示英雄'''
    if selection.isChampionShowed:
        return

    if not cfg.get(cfg.enableAutoSelectChampion):
        return

    cellId = data['localPlayerCellId']

    for player in data['myTeam']:
        if player['cellId'] != cellId:
            continue

        if (player['championId'] != 0
                or player['championPickIntent'] != 0):
            selection.isChampionShowed = True
            return

        pos = player.get("assignedPosition", None)

        break

    if pos == 'top':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionTop))
    elif pos == 'jungle':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionJug))
    elif pos == 'middle':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionMid))
    elif pos == 'bottom':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionBot))
    elif pos == 'utility':
        candidates = deepcopy(cfg.get(cfg.autoSelectChampionSup))
    else:
        candidates = []

    default = deepcopy(cfg.get(cfg.autoSelectChampion))
    candidates.extend(default)

    if len(candidates) == 0:
        selection.isChampionShowed = True
        return

    championId = candidates[0]
    for actionGroup in reversed(data['actions']):
        for action in actionGroup:
            if (action['actorCellId'] == cellId and
                    action['type'] == 'pick'):

                await connector.selectChampion(action['id'], championId)
                selection.isChampionShowed = True

                return True


async def rollAndSwapBack():
    """
    摇骰子并切换回之前的英雄
    todo: 界面
    """
    championId = await connector.getCurrentChampion()

    await connector.reroll()
    await connector.benchSwap(championId)


async def fixLCUWindowViaExe():
    # 仅 Windows 可用: 调用 fix_lcu_window.exe (ctypes.windll 在非 Windows 不存在).
    # macOS 上作为 no-op 优雅降级 (documented degradation #1, spec:48). 调用方不消费返回值.
    if not platform.IS_WINDOWS:
        return

    zoom = await connector.getClientZoom()

    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "app\\resource\\bin\\fix_lcu_window.exe", f"{zoom}", None, 0)


async def loadGameBuildData(gameId):
    """
    对局详情 "出装" / "图表" 视图的数据, 从不抛异常:
        {'builds': {participantId: {'itemGroups', 'skills', 'runes'}},
         'itemIcons': {itemId: 本地图标路径}, 'hasTimeline': 时间线里有出装 / 加点事件,
         'series': match_build.parseTimelineSeries 的结果 (每分钟的经济 / 经验 / 补刀) 或 None}
    符文来自对局详情的 perk0..5 (总能拿到), 属性碎片来自 Riot 对局服务器的 SUMMARY (拿不到就不显示),
    出装 / 加点来自时间线 (LCU 的没有时用 Riot 对局服务器的)
    """
    # SUMMARY 与下面的请求并行, 不额外增加等待
    summaryTask = asyncio.ensure_future(connector.getGameSummaryViaSgpQuietly(gameId))
    detail, localTimeline = await asyncio.gather(
        connector.getGameDetailByGameIdQuietly(gameId),
        connector.getGameTimelineQuietly(gameId))

    # LCU 时间线没有购买 / 加点事件时, 改用 Riot 对局服务器的完整时间线
    timeline = localTimeline
    if not hasBuildEvents(timeline):
        sgpTimeline = await connector.getGameTimelineViaSgpQuietly(gameId)
        if hasBuildEvents(sgpTimeline):
            timeline = remapTimelineParticipants(sgpTimeline, detail)

    summary = await summaryTask

    # 图表: Riot 对局服务器的帧多了对英雄伤害; 它的帧没能对上全部玩家时用本地时间线
    # (本地帧的键本来就是对局详情的 participantId)
    series = parseTimelineSeries(timeline) if timeline is not localTimeline else None
    playerIds = {p.get('participantId') for p in (detail or {}).get('participants') or []}
    if not series or (playerIds and set(series['series']) != playerIds):
        series = parseTimelineSeries(localTimeline) or series

    manager = connector.manager
    builds = parseGameBuilds(detail, timeline, manager.getPerkStyles() if manager else None,
                             statPerksFromSummary(summary, detail))

    itemIcons = {}
    for itemId in buildItemIds(builds):
        try:
            itemIcons[itemId] = await connector.getItemIcon(itemId)
        except Exception:
            pass

    return {'builds': builds, 'itemIcons': itemIcons, 'hasTimeline': hasBuildEvents(timeline),
            'series': series}
