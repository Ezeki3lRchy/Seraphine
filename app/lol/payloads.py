"""Pure parsing of game-client payloads (LCU / SGP ranks, games, teammates, lobby and
champ-select teams). No connector calls and no config reads: each function maps the data
it is given."""
from .formatting import ToolsTranslator, translateTier


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


def visibleEnemies(session):
    """
    选人阶段能看到身份的敌方 (自定义 / 锦标赛征召等模式); 排位里 Riot 隐藏敌方,
    theirTeam 的 puuid / summonerId 为空, 这时返回 [] (敌方战绩要等进游戏后才能查)
    """
    return [m for m in (session or {}).get('theirTeam') or []
            if isinstance(m, dict) and m.get('playerType') != 'BOT'
            and m.get('nameVisibilityType') != 'HIDDEN'
            and (m.get('summonerId') or m.get('puuid'))]


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


def parseSummonerOrder(team):
    summoners = [{
        'summonerId': s['summonerId'],
        'cellId': s['cellId']
    } for s in team]

    summoners.sort(key=lambda x: x['cellId'])
    return [s['summonerId'] for s in summoners if s['summonerId'] != 0]


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


def getNameTagLineFromGame(game, puuid):
    for player in game['json']['participants']:
        if player['puuid'] == puuid:
            return player['riotIdGameName'], player['riotIdTagline']

    return None
