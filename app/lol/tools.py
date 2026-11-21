"""Async orchestrators of the data layer: they tie connector calls into per-player and per-game
results (summoner data, ally / enemy / lobby / gameflow game info, SGP history, build data). The
helpers they use live in formatting / payloads / scoring / champ_select and are re-exported here."""
import time
import traceback

import asyncio

from .exceptions import SummonerRankInfoNotFound
from .chat_reveal import mergeHiddenTeammates, revealStats, isHiddenSlot
from .match_build import (parseGameBuilds, buildItemIds, hasBuildEvents,
                          remapTimelineParticipants, statPerksFromSummary,
                          parseTimelineSeries)
from ..common.config import cfg
from ..common.logger import logger
from ..lol.connector import connector
from . import opscore as opscoreModel
from .formatting import (
    SERVERS_NAME as SERVERS_NAME,
    SERVERS_SUBSET as SERVERS_SUBSET,
    ToolsTranslator as ToolsTranslator,
    translateTier as translateTier,
    timeStampToStr as timeStampToStr,
    timeStampToShortStr as timeStampToShortStr,
    secsToStr as secsToStr,
    getPositionKey as getPositionKey,
    translatePositionKey as translatePositionKey,
)
from .payloads import (
    getTeammates as getTeammates,
    getRecentChampions as getRecentChampions,
    parseRankInfo as parseRankInfo,
    parseRankInfoFromSGP as parseRankInfoFromSGP,
    parseDetailRankInfo as parseDetailRankInfo,
    visibleEnemies as visibleEnemies,
    splitCustomLobbyTeams as splitCustomLobbyTeams,
    parseSummonerOrder as parseSummonerOrder,
    sortedSummonersByGameRole as sortedSummonersByGameRole,
    getAllyOrderByGameRole as getAllyOrderByGameRole,
    getTeamColor as getTeamColor,
    separateTeams as separateTeams,
    mergeQueuePage as mergeQueuePage,
    getTeammatesFromSGPGame as getTeammatesFromSGPGame,
    getNameTagLineFromGame as getNameTagLineFromGame,
)
from .scoring import (
    GAME_SCORE_WEIGHTS as GAME_SCORE_WEIGHTS,
    OP_SCORE_SLOPE as OP_SCORE_SLOPE,
    OP_SCORE_OFFSET as OP_SCORE_OFFSET,
    opScore as opScore,
    scoreGameParticipants as scoreGameParticipants,
    _pickScore as _pickScore,
    lcuDetailPlayers as lcuDetailPlayers,
    getGameScoreFromLcuDetail as getGameScoreFromLcuDetail,
    getGameScoreFromSgpGame as getGameScoreFromSgpGame,
    summarizeGames as summarizeGames,
    MAIN_CHAMPION_POINTS as MAIN_CHAMPION_POINTS,
    MAIN_CHAMPION_SHARE as MAIN_CHAMPION_SHARE,
    MAIN_CHAMPION_MIN_GAMES as MAIN_CHAMPION_MIN_GAMES,
    STREAK_MIN as STREAK_MIN,
    playerInsights as playerInsights,
)
from .champ_select import (
    ChampionSelection as ChampionSelection,
    autoSwap as autoSwap,
    autoTrade as autoTrade,
    showOpggBuild as showOpggBuild,
    autoPick as autoPick,
    autoComplete as autoComplete,
    autoBan as autoBan,
    autoShow as autoShow,
    rollAndSwapBack as rollAndSwapBack,
    fixLCUWindowViaExe as fixLCUWindowViaExe,
)

TAG = "Tools"


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
    except asyncio.CancelledError:
        raise
    except BaseException as e:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
        logger.warning(f"parseSummonerData: games unavailable: {type(e).__name__}", TAG)
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
        'iconId': iconId,
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
    except Exception as e:
        logger.warning(f"detailRankInfo: bad rank data: {type(e).__name__}", TAG)
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
                logger.error(f"partial update failed: {type(e).__name__}", TAG)

    await asyncio.gather(*[run(i, c) for i, c in enumerate(coros)])
    return results


def applyLastGameRelation(summoner, detail, currentSummonerId):
    """由该玩家上一局的完整对局判断上把和当前召唤师是队友还是对手 (fateFlag, 名字着色),
    以及上把用的英雄 (recentlyChampionName, 名字悬浮提示)"""
    if not detail:
        return

    try:
        teammates = getTeammates(detail, summoner['puuid'])
    except Exception as e:
        logger.warning(f"applyLastGameRelation failed: {type(e).__name__}", TAG)
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
        # type, status and the stack frames (code only): the message can carry request
        # headers (aiohttp) or player data, so it is left out
        logger.error(
            f"reveal-hidden: merge failed, using original myTeam: {type(e).__name__}, "
            f"status={getattr(e, 'status', None)}\n"
            + "".join(traceback.format_tb(e.__traceback__)), TAG)
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
                f"reveal-hidden: summonerId lookup failed: {type(e).__name__}, "
                f"status={getattr(e, 'status', None)}", TAG)

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
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
            logger.warning(
                f"parseAllyGameInfo: SGP failed, falling back to LCU: {type(e).__name__}, status={getattr(e, 'status', None)}", TAG)
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
                except Exception as e:
                    logger.warning(f"summoner lookup by puuid failed: {type(e).__name__}", TAG)
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

        except asyncio.CancelledError:
            raise
        except BaseException as e:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
            logger.warning(
                f"parseGameInfoByGameflowSession: SGP failed, falling back to LCU: {type(e).__name__}, status={getattr(e, 'status', None)}", TAG)
            summoners = await gatherProgressively(
                [parseSummonerGameInfo(item, queueFilter, currentSummonerId) for item in team],
                onPartial, build)

    else:
        summoners = await gatherProgressively(
            [parseSummonerGameInfo(item, queueFilter, currentSummonerId) for item in team],
            onPartial, build)

    return build(summoners)


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
    except asyncio.CancelledError:
        raise
    except BaseException as e:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
        logger.warning(
            f"parseSummonerGameInfo: games fetch failed: {type(e).__name__}, status={getattr(e, 'status', None)}", TAG)
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
    except asyncio.CancelledError:
        raise
    except BaseException as e:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
        logger.warning(
            f"getSummonerGamesInfoViaSGP: games fetch failed: {type(e).__name__}, status={getattr(e, 'status', None)}", TAG)
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
        except Exception as e:
            # debug: connector's retry already reports it; removed items in old matches have no icon
            logger.debug(f"loadGameBuildData: item icon failed: {type(e).__name__}", TAG)

    return {'builds': builds, 'itemIcons': itemIcons, 'hasTimeline': hasBuildEvents(timeline),
            'series': series}
