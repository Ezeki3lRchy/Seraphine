import itertools
import time
import ctypes
from copy import deepcopy

import asyncio
from PySide6.QtCore import QObject, QLocale

from .exceptions import SummonerRankInfoNotFound
from .chat_reveal import mergeHiddenTeammates, revealStats, isHiddenSlot
from ..common.config import cfg, Language, isChineseText
from ..common.logger import logger
from ..lol.connector import connector
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


async def getRecentTeammates(games, puuid):
    summoners = {}

    for game in games:
        gameId = game['gameId']
        game = await connector.getGameDetailByGameId(gameId)
        teammates = getTeammates(game, puuid)

        for p in teammates['summoners']:
            if p['summonerId'] == 0:
                continue

            if p['puuid'] not in summoners:
                summonerIcon = await connector.getProfileIcon(p['icon'])
                summoners[p['puuid']] = {
                    "name": p['name'], 'icon': summonerIcon,
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

    return ret


async def parseSummonerData(summoner, rankTask, gameTask):
    iconId = summoner['profileIconId']
    icon = await connector.getProfileIcon(iconId)
    level = summoner['summonerLevel']
    xpSinceLastLevel = summoner['xpSinceLastLevel']
    xpUntilNextLevel = summoner['xpUntilNextLevel']

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

    for participant in game['participantIdentities']:
        participantId = participant['participantId']
        summonerName = participant['player'].get(
            'gameName') or participant['player'].get('summonerName')  # 兼容外服
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

                getRankInfo = cfg.get(cfg.showTierInGameInfo)

                tier = division = lp = rankIcon = ""
                if getRankInfo:
                    try:
                        rank = await connector.getRankedStatsByPuuid(
                            summonerPuuid)
                    except SummonerRankInfoNotFound:
                        ...
                    else:
                        rank = rank['queueMap']

                        if queueId == 1700 and 'CHERRY' in rank:
                            rankInfo = rank["CHERRY"]
                            lp = rankInfo['ratedRating']
                        else:
                            rankInfo = rank[
                                'RANKED_FLEX_SR'] if queueId == 440 else rank['RANKED_SOLO_5x5']

                            tier = rankInfo['tier']
                            division = rankInfo['division']
                            lp = rankInfo['leaguePoints']

                            if tier == '':
                                rankIcon = 'app/resource/images/unranked.png'
                            else:
                                rankIcon = f'app/resource/images/{tier.lower()}.png'
                                tier = translateTier(tier, True)

                            if division == 'NA':
                                division = ''

                item = {
                    'summonerName': summonerName,
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
                    'isPublic': isPublic
                }
                # 同 teams[tid] 聚合守卫: 异常 placement 的 tid 不在 teams 内则跳过
                if tid in teams:
                    teams[tid]['summoners'].append(item)

                break

    mapIcon = connector.manager.getMapIconByMapId(mapId, win)

    if win == None:
        return None

    return {
        'gameId': game['gameId'],
        'mapIcon': mapIcon,
        'gameCreation': timeStampToStr(game['gameCreation']),
        'gameDuration': secsToStr(game['gameDuration']),
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

        if cmp == tid:
            if s['puuid'] != targetPuuid:
                res['summoners'].append(
                    {'summonerId': s['summonerId'], 'name': s['summonerName'], 'puuid': s['puuid'], 'icon': s['profileIcon']})
            else:
                # 当前召唤师在该对局使用的英雄, 自定义对局没有该字段
                res["championId"] = player.get('championId', -1)
        else:
            res['enemies'].append(
                {'summonerId': s['summonerId'], 'name': s['summonerName'], 'puuid': s['puuid'],
                 'icon': s['profileIcon']})

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


# 选手评分权重 (Seraphine 自己的评分, 非 OP.GG OP Score; Riot 的 totalPlayerScore 等字段实测全为 0)
# 每项都按本局 10 人里的最大值归一化
GAME_SCORE_WEIGHTS = {
    'kp': 3,           # 参团率 (击杀 + 助攻) / 本队击杀
    'damage': 2,       # 对英雄伤害
    'kda': 2,          # (击杀 + 助攻) / max(死亡, 1), 封顶 10
    'damageTaken': 1,  # 承受伤害
    'gold': 1,         # 金币
    'vision': 1,       # 视野得分
}


def scoreGameParticipants(players):
    """
    给一局里的所有玩家打分并排名, 选出 MVP (胜方最高分) 与 SVP (败方最高分)

    @param players: [{'id', 'teamId', 'win', 'kills', 'deaths', 'assists',
                      'damage', 'damageTaken', 'gold', 'vision'}], id 为 participantId
    @return: {id: {'score', 'scoreRank', 'kp', 'badge'}}; badge 为 'MVP' / 'SVP' / None.
             不是恰好两队且一胜一负时不评徽章 (仍有排名)
    """
    if not players:
        return {}

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

    maxes = {k: max(r[k] for r in raw.values()) for k in GAME_SCORE_WEIGHTS}

    scores = {}
    for pid, r in raw.items():
        scores[pid] = sum(w * (r[k] / maxes[k] if maxes[k] else 0)
                          for k, w in GAME_SCORE_WEIGHTS.items())

    byId = {p['id']: p for p in players}
    # 同分时按伤害高者优先, 再按 participantId, 保证同一局在不同玩家的战绩里结果一致
    order = sorted(scores, key=lambda pid: (-scores[pid], -byId[pid]['damage'], pid))

    result = {pid: {'score': scores[pid], 'scoreRank': i + 1,
                    'kp': raw[pid]['kp'], 'badge': None}
              for i, pid in enumerate(order)}

    teams = {p['teamId'] for p in players}
    winners = [pid for pid in order if byId[pid]['win']]
    losers = [pid for pid in order if not byId[pid]['win']]

    if len(teams) == 2 and winners and losers:
        result[winners[0]]['badge'] = 'MVP'
        result[losers[0]]['badge'] = 'SVP'

    return result


def _pickScore(players, myId, remake):
    """scoreGameParticipants 的结果里取出自己那一项, 转成 gamesInfo 用的字段"""
    if myId is None or len(players) < 2:
        return None

    result = scoreGameParticipants(players).get(myId)
    if not result:
        return None

    return {
        'badge': None if remake else result['badge'],
        'scoreRank': result['scoreRank'],
        'scorePlayers': len(players),
        'kp': result['kp'],
    }


def getGameScoreFromLcuDetail(detail, puuid):
    """
    由 LCU `/lol-match-history/v1/games/{gameId}` 的完整对局算出 puuid 这名玩家的
    MVP / SVP 徽章, 评分排名与参团率. 斗魂竞技场等多小队模式 / 数据不全时返回 None
    """
    try:
        if not detail or detail.get('queueId') in (1700, 1710):
            return None

        identities = {
            pi['participantId']: pi.get('player', {}).get('puuid')
            for pi in detail.get('participantIdentities', [])}

        players, myId, remake = [], None, False
        for p in detail['participants']:
            st = p['stats']
            if st.get('playerSubteamId'):
                return None

            players.append({
                'id': p['participantId'], 'teamId': p['teamId'],
                'win': bool(st['win']), 'kills': st['kills'],
                'deaths': st['deaths'], 'assists': st['assists'],
                'damage': st.get('totalDamageDealtToChampions', 0),
                'damageTaken': st.get('totalDamageTaken', 0),
                'gold': st.get('goldEarned', 0),
                'vision': st.get('visionScore', 0),
            })

            if identities.get(p['participantId']) == puuid:
                myId = p['participantId']
                remake = bool(st.get('gameEndedInEarlySurrender'))

        return _pickScore(players, myId, remake)
    except (KeyError, TypeError, AttributeError):
        return None


def getGameScoreFromSgpGame(game, puuid):
    """与 getGameScoreFromLcuDetail 相同, 数据来自 SGP 战绩 (game 为其中的 'json')"""
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

        return _pickScore(players, myId, remake)
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
            'puuid': player.get('puuid'),
        })

    players.sort(key=lambda x: x['teamId'])
    return players


async def attachGameScores(gamesInfo, puuid):
    """
    对局信息页用: 为 parseGameData 得到的每局补上 MVP / SVP 徽章, 评分排名与参团率
    (LCU 战绩列表只有自己的数据, 需逐局拉完整对局). 拉取失败的局不显示徽章.

    @return: 与 gamesInfo 对齐的完整对局列表 (失败处为 None), 供调用方复用
    """
    details = await asyncio.gather(
        *[connector.getGameDetailByGameIdQuietly(game['gameId'])
          for game in gamesInfo],
        return_exceptions=True)
    details = [d if isinstance(d, dict) else None for d in details]

    for game, detail in zip(gamesInfo, details):
        score = getGameScoreFromLcuDetail(detail, puuid)
        if score:
            game.update(score)

    return details


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
        'mvp': sum(1 for g in games if g.get('badge') == 'MVP'),
        'svp': sum(1 for g in games if g.get('badge') == 'SVP'),
        'topChampions': [finish(item) for item in topChampions],
        'positions': sorted(positions.items(), key=lambda x: x[1], reverse=True),
        'streak': streak,
        'currentChampion': finish(current) if current else None,
    }


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
                            cache=None):
    '''
    @param cache: {puuid: summoner_dict} 同一次选人内复用; 队友陆续进入聊天房后重新解析时
        (见 MainWindow.__refreshHiddenAllies), 只为新揭示的队友发请求
    '''
    if cache is None:
        cache = {}

    # 排位会有预选位
    isRank = bool(session["myTeam"][0]["assignedPosition"])

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

            summoner = await fetch(item, isRank, currentSummonerId)
            if summoner and summoner.get('puuid'):
                cache[summoner['puuid']] = summoner
            return summoner

        return resolve

    if useSGP and connector.isInTencent():
        # 如果是国服就优先尝试 SGP
        try:
            resolve = cached(getSummonerGamesInfoViaSGP)
            summoners = await asyncio.gather(*[resolve(item) for item in myTeam])
        except:
            await fillRevealedSummonerIds(myTeam)
            resolve = cached(parseSummonerGameInfo)
            summoners = await asyncio.gather(*[resolve(item) for item in myTeam])

    else:
        await fillRevealedSummonerIds(myTeam)
        resolve = cached(parseSummonerGameInfo)
        summoners = await asyncio.gather(*[resolve(item) for item in myTeam])

    summoners = [summoner for summoner in summoners if summoner]

    # 按照楼层排序
    summoners = sorted(
        summoners, key=lambda x: x["cellId"])

    champions = {summoner['summonerId']: summoner['championId']
                 for summoner in summoners}
    order = [summoner['summonerId'] for summoner in summoners]

    return {'summoners': summoners, 'champions': champions, 'order': order, "isAram": session.get('benchEnabled', False)}


async def parseLobbyGameInfo(lobby, currentSummonerId, useSGP=False, cache=None):
    '''
    解析组队大厅成员战绩, 复用选人阶段的 per-summoner 取数逻辑。

    @param lobby: connector.getLobby() 的返回值
    @param cache: {puuid: summoner_dict} 复用缓存, 只查询新加入的成员 (避免 SGP 刷爆)
    @return: 与 @see: parseAllyGameInfo 一致的 {summoners, champions, order, isAram}
    '''
    if cache is None:
        cache = {}

    members = lobby.get('members') or []
    queueId = (lobby.get('gameConfig') or {}).get('queueId', -1)
    isRank = queueId in (420, 440)

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
        summoner = await fetch(item, isRank, currentSummonerId)
        if summoner and puuid:
            cache[puuid] = summoner
        return summoner

    tasks = [resolve(idx, m) for idx, m in enumerate(members)]
    summoners = await asyncio.gather(*tasks)

    summoners = [s for s in summoners if s]
    summoners = sorted(summoners, key=lambda x: x["cellId"])

    champions = {s['summonerId']: s['championId'] for s in summoners}
    order = [s['summonerId'] for s in summoners]

    return {'summoners': summoners, 'champions': champions, 'order': order, "isAram": queueId == 450}


def parseSummonerOrder(team):
    summoners = [{
        'summonerId': s['summonerId'],
        'cellId': s['cellId']
    } for s in team]

    summoners.sort(key=lambda x: x['cellId'])
    return [s['summonerId'] for s in summoners if s['summonerId'] != 0]


async def parseGameInfoByGameflowSession(session, currentSummonerId, side, useSGP=False):
    data = session['gameData']
    queueId = data['queue']['id']

    if queueId in (1700, 1090, 1100, 1110, 1130, 1160):  # 斗魂 云顶匹配 (排位)
        return None

    isRank = queueId in (420, 440)

    if side == 'enemy':
        _, team = separateTeams(data, currentSummonerId)
    else:
        team, _ = separateTeams(data, currentSummonerId)

    if useSGP and connector.isInTencent():
        # 如果是国服就优先尝试 SGP
        try:
            tasks = [getSummonerGamesInfoViaSGP(item, isRank, currentSummonerId)
                     for item in team]
            summoners = await asyncio.gather(*tasks)

        except:
            tasks = [parseSummonerGameInfo(item, isRank, currentSummonerId)
                     for item in team]
            summoners = await asyncio.gather(*tasks)

    else:
        tasks = [parseSummonerGameInfo(item, isRank, currentSummonerId)
                 for item in team]
        summoners = await asyncio.gather(*tasks)

    summoners = [summoner for summoner in summoners if summoner]

    if isRank:
        s = sortedSummonersByGameRole(summoners)

        if s != None:
            summoners = s

    champions = {summoner['summonerId']: summoner['championId']
                 for summoner in summoners}
    order = [summoner['summonerId'] for summoner in summoners]

    return {'summoners': summoners, 'champions': champions, 'order': order}


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


def mergeRankedPage(games, page, seen, key):
    """
    把一页战绩里的排位局 (420 / 440) 追加到 games, 跳过 seen 里已有的 gameId.

    @param seen: 已见过的 gameId 集合 (含非排位局), 会被更新
    @param key: game -> (queueId, gameId)
    @return: 这一页有没有新的对局 (不论是否排位); 没有说明已翻到历史尽头
    """
    fresh = False
    for game in page:
        queueId, gameId = key(game)
        if gameId in seen:
            continue

        seen.add(gameId)
        fresh = True
        if queueId in (420, 440):
            games.append(game)

    return fresh


async def parseSummonerGameInfo(item, isRank, currentSummonerId):
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

        if cfg.get(cfg.gameInfoFilter) and isRank:
            def lcuKey(game):
                return game["queueId"], game["gameId"]

            firstPage = origGamesInfo["games"]
            seen = {lcuKey(game)[1] for game in firstPage}
            origGamesInfo["games"] = []
            mergeRankedPage(origGamesInfo["games"], firstPage, set(), lcuKey)

            begIdx = 15
            while len(origGamesInfo["games"]) < 11 and begIdx <= 95:
                endIdx = begIdx + 5
                new = (await connector.getSummonerGamesByPuuid(puuid, begIdx, endIdx))["games"]

                # 超出历史范围时 LCU 会重复返回已拿到的对局: 去重, 整页都是旧的就停
                if not mergeRankedPage(origGamesInfo["games"], new, seen, lcuKey):
                    break

                begIdx = endIdx + 1
    except:
        gamesInfo = []
    else:
        tasks = [parseGameData(game)
                 for game in origGamesInfo["games"][:11]]
        gamesInfo = await asyncio.gather(*tasks)

    # MVP / SVP 等需要每局的完整数据; 顺便复用第一局给下面的队友判断
    details = await attachGameScores(gamesInfo, puuid)

    _, kill, deaths, assists, wins, losses = parseGames(gamesInfo)

    teammatesInfo = [
        getTeammates(
            details[0] or await connector.getGameDetailByGameId(game["gameId"]),
            puuid
        ) for game in gamesInfo[:1]  # 避免空报错, 查上一局的队友(对手)
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
        "isPublic": summoner["privacy"] == "PUBLIC",
        # 最近游戏的英雄 (用于上一局与与同一召唤师游玩之后显示)
        "recentlyChampionName": recentlyChampionName,
        # {championId: {'level', 'points'}}
        "mastery": await masteryTask,
    }


async def getSummonerGamesInfoViaSGP(item, isRank, currentSummonerId):
    '''
    使用 SGP 接口取战绩信息
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

        if cfg.get(cfg.gameInfoFilter) and isRank:
            def sgpKey(game):
                return game['json']["queueId"], game['json']["gameId"]

            firstPage = origGamesInfo["games"]
            seen = {sgpKey(game)[1] for game in firstPage}
            origGamesInfo["games"] = []
            mergeRankedPage(origGamesInfo["games"], firstPage, set(), sgpKey)

            begIdx = 15
            while len(origGamesInfo["games"]) < 11 and begIdx <= 95:
                endIdx = begIdx + 10
                new = (await connector.getSummonerGamesByPuuidViaSGP(puuid, begIdx, endIdx))["games"]

                # 同 LCU: 去重, 整页都是旧的就停
                if not mergeRankedPage(origGamesInfo["games"], new, seen, sgpKey):
                    break

                begIdx = endIdx + 1
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

        if cmp == tid:
            if player['puuid'] != puuid:
                res['summoners'].append({
                    'summonerId': player['summonerId'],
                    'name': player['summonerName'],
                    'puuid': player['puuid'],
                    'icon': player['profileIcon']
                })
            else:
                # 当前召唤师在该对局使用的英雄, 自定义对局没有该字段
                res["championId"] = player.get('championId', -1)
        else:
            res['enemies'].append({
                'summonerId': player['summonerId'],
                'name': player['summonerName'],
                'puuid': player['puuid'],
                'icon': player['profileIcon']
            })

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
