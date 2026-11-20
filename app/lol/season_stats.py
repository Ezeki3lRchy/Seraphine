"""
生涯页 "Performance" 面板的数据: 近期对局 vs 本赛季对局的胜率 / KDA / 参团率 / 平均时长.

赛季样本从战绩列表翻页取 (最新在前, 最多 SEASON_CAP 局), 只读原始字段, 不解析图标:
- 赛季边界: 今年 1 月 1 日 (本地时间) 起, 且游戏版本主号与窗口内最新一局相同
  (原始战绩里有 gameVersion 时); 取不到版本就只按日期.
- 参团率需要全队击杀: 国服 SGP 战绩自带 10 人数据; LCU 战绩列表只有自己, 只能用近期对局已有的
  参团率, 再为少量 (KP_DETAIL_CAP) 赛季对局补拉完整对局, 样本数单独记 (kpGames).
- Non-CN servers first try Riot's match server player history (same SUMMARY shape as CN,
  all ten players), falling back to the LCU list when it fails.
- Full-game sources ('match' / 'sgp') also give the Champions view the lane opponent, the
  ahead-after-laning flag and the ranked model score; the LCU list has none of those.
数据层, 不引用 view.
"""
import asyncio
import time
from datetime import datetime

from app.common.logger import logger
from app.lol import opscore
from app.lol.connector import connector

TAG = "SeasonStats"

SEASON_CAP = 150
PAGE_SIZE = 20
KP_DETAIL_CAP = 30
# Non-CN Riot match server (source 'match'): pages of full games (~120 KB each, ~2 s per
# page of 50), so at most MATCH_SEASON_CAP / MATCH_PAGE_SIZE = 4 requests per season sample
MATCH_SEASON_CAP = 200
MATCH_PAGE_SIZE = 50


def seasonCap(source):
    """the cap fetchSeasonGames used for this source (for the 'capped' note)"""
    return MATCH_SEASON_CAP if source == 'match' else SEASON_CAP


def aggregate(games):
    """
    games: parseGameData 结果或 rawGameStats 结果 (需 kills / deaths / assists / win / remake /
    durationSecs, 可有 kp). 去掉重开局; KDA 按总数算 (k + a) / max(d, 1), 与概况卡一致.
    @return: {'games', 'winRate', 'kda', 'kp', 'kpGames', 'duration'}; 没有可统计的局为 None
    """
    counted = [g for g in games or [] if g and not g.get('remake')]
    if not counted:
        return None

    n = len(counted)
    k = sum(g['kills'] for g in counted)
    d = sum(g['deaths'] for g in counted)
    a = sum(g['assists'] for g in counted)
    kps = [g['kp'] for g in counted if g.get('kp') is not None]
    secs = [g['durationSecs'] for g in counted if g.get('durationSecs')]

    return {
        'games': n,
        'winRate': sum(1 for g in counted if g['win']) / n,
        'kda': (k + a) / max(d, 1),
        'kp': sum(kps) / len(kps) if kps else None,
        'kpGames': len(kps),
        'duration': sum(secs) / len(secs) if secs else None,
    }


def _versionMajor(version):
    try:
        return int(str(version).split('.')[0])
    except (ValueError, TypeError):
        return None


def _playStyleFields(me, stats):
    """
    Play style / Champions fields shared by both payloads (match-v4 stats / match-v5
    participant); a field the payload lacks is None
    """
    return {
        'championId': me.get('championId'),
        'teamId': me.get('teamId'),
        'damage': stats.get('totalDamageDealtToChampions'),
        'gold': stats.get('goldEarned'),
        'cs': (stats.get('totalMinionsKilled') or 0) + (stats.get('neutralMinionsKilled') or 0)
        if 'totalMinionsKilled' in stats else None,
        'vision': stats.get('visionScore'),
        'doubleKills': stats.get('doubleKills'),
        'tripleKills': stats.get('tripleKills'),
        'quadraKills': stats.get('quadraKills'),
        'pentaKills': stats.get('pentaKills'),
        'wardsPlaced': stats.get('wardsPlaced'),
        'wardsKilled': stats.get('wardsKilled'),
        'controlWards': stats.get('visionWardsBoughtInGame'),
    }


def _laneFields(game, me, index):
    """
    match-v5 info: the lane opponent's championId (the one other-team player with the same
    teamPosition, None when there is no position or it is not unique), the ahead-after-laning
    flag (challenges.laningPhaseGoldExpAdvantage, optional) and the 0-10 model score (ranked
    only, see opscore.scoreSummary)
    """
    opponent = None
    pos = me.get('teamPosition')
    if pos and not me.get('playerSubteamId'):
        foes = [p for p in game['participants']
                if p.get('teamId') != me.get('teamId') and p.get('teamPosition') == pos]
        if len(foes) == 1:
            opponent = foes[0].get('championId')
    lead = (me.get('challenges') or {}).get('laningPhaseGoldExpAdvantage')
    scores = opscore.scoreSummary(game, only=me.get('participantId') or index + 1)
    return {
        'opponentId': opponent,
        'laneLead': bool(lead) if lead is not None and opponent is not None else None,
        'score': next(iter(scores.values())) if scores else None,
    }


def rawLcuGameStats(game):
    """LCU 战绩列表的一局 (只有自己一个 participant) -> 统计字段; 数据不全返回 None"""
    try:
        me = game['participants'][0]
        stats = me['stats']
        # Legacy timeline lane / role are unreliable; in Summoner's Rift draft queues
        # participantId 1-5 (blue) / 6-10 (red) follow Top / Jungle / Mid / Bot / Support
        # (see laning.py), so use that order there - only when the id agrees with the side.
        position = None
        pid = me.get('participantId')
        if (game.get('queueId') in (400, 420, 440) and game.get('mapId', 11) == 11
                and isinstance(pid, int) and 1 <= pid <= 10
                and me.get('teamId') in (100, 200) and (pid <= 5) == (me['teamId'] == 100)):
            position = ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')[(pid - 1) % 5]
        return {
            **_playStyleFields(me, stats),
            'position': position,
            'custom': game.get('gameType') == 'CUSTOM_GAME',
            'mapId': game.get('mapId'),
            # only the viewed player is in the LCU list: no lane opponent / lane flag / score
            'opponentId': None,
            'laneLead': None,
            'score': None,
            'damageShare': None,
            'gameId': game['gameId'],
            'queueId': game.get('queueId'),
            'timeStamp': game['gameCreation'],
            'version': _versionMajor(game.get('gameVersion')),
            'kills': stats['kills'],
            'deaths': stats['deaths'],
            'assists': stats['assists'],
            'win': bool(stats['win']),
            'remake': bool(stats.get('gameEndedInEarlySurrender')),
            'durationSecs': game.get('gameDuration') or 0,
            'kp': None,
        }
    except (KeyError, IndexError, TypeError):
        return None


def rawSgpGameStats(game, puuid):
    """SGP SUMMARY 战绩的一局 (含 'json', 全部 10 人) -> 统计字段, 参团率直接算出"""
    try:
        game = game['json']
        index, me = next((i, p) for i, p in enumerate(game['participants'])
                         if p.get('puuid') == puuid)
        kp = None
        if not me.get('playerSubteamId') and game.get('queueId') not in (1700, 1710):
            teamKills = sum(p['kills'] for p in game['participants']
                            if p['teamId'] == me['teamId'])
            kp = (me['kills'] + me['assists']) / max(teamKills, 1)
        damageShare = None
        if not me.get('playerSubteamId') and me.get('totalDamageDealtToChampions') is not None:
            teamDamage = sum(p.get('totalDamageDealtToChampions') or 0
                             for p in game['participants'] if p['teamId'] == me['teamId'])
            damageShare = me['totalDamageDealtToChampions'] / teamDamage if teamDamage else None
        return {
            **_playStyleFields(me, me),
            'position': me.get('teamPosition') or None,
            'custom': game.get('gameType') == 'CUSTOM_GAME',
            'mapId': game.get('mapId'),
            **_laneFields(game, me, index),
            'damageShare': damageShare,
            'gameId': game['gameId'],
            'queueId': game.get('queueId'),
            'timeStamp': game['gameCreation'],
            'version': _versionMajor(game.get('gameVersion')),
            'kills': me['kills'],
            'deaths': me['deaths'],
            'assists': me['assists'],
            'win': bool(me['win']),
            'remake': bool(me.get('gameEndedInEarlySurrender')),
            'durationSecs': game.get('gameDuration') or 0,
            'kp': kp,
        }
    except (KeyError, StopIteration, TypeError):
        return None


def seasonStart(now=None):
    """赛季起点的毫秒时间戳: 今年 1 月 1 日 0 点 (本地时间)"""
    now = datetime.fromtimestamp(time.time() if now is None else now)
    return datetime(now.year, 1, 1).timestamp() * 1000


def filterSeason(games, start):
    """
    games 最新在前. 只留 start 之后的局; 有版本号时再只留与其中最新一局主版本相同的
    """
    games = [g for g in games if g['timeStamp'] >= start]
    major = next((g['version'] for g in games if g['version'] is not None), None)
    if major is not None:
        games = [g for g in games if g['version'] in (major, None)]
    return games


def mergePage(games, page, seen):
    """去重追加; 返回本页有没有新对局 (LCU 翻过历史尽头会重复返回旧局)"""
    fresh = False
    for g in page:
        if g is None or g['gameId'] in seen:
            continue
        seen.add(g['gameId'])
        games.append(g)
        fresh = True
    return fresh


async def _fetchPages(fetch, start, cap, pageSize=PAGE_SIZE, shortPageEnds=False):
    """
    fetch(begIdx, endIdx) -> 统计字段列表或 None (失败); 翻到赛季前 / cap / 尽头为止.
    shortPageEnds: a page shorter than pageSize is the end of the history (match server)
    A failed page is asked for once more; when it fails again paging stops there.
    @return: (games, complete): complete is False when paging stopped at a failed page, so
             the games are only the newest part of what was there
    """
    games, seen = [], set()
    beg = 0
    while len(games) < cap:
        page = await fetch(beg, beg + pageSize - 1)
        if page is None:
            page = await fetch(beg, beg + pageSize - 1)
            if page is None:
                return games[:cap], False
        if not page:
            break
        if not mergePage(games, page, seen):
            break
        if min((g['timeStamp'] for g in page if g), default=0) < start:
            break
        if shortPageEnds and len(page) < pageSize:
            break
        beg += pageSize
    return games[:cap], True


def _sample(games, start, cap, complete):
    """
    (season games, info): info['capped'] is decided on the fetched games before the season
    filter (one filtered-out game must not hide the cap); info['partial'] = a page failed
    """
    return filterSeason(games, start), {'capped': len(games) >= cap, 'partial': not complete}


async def _extractSgpPage(page, puuid):
    """
    rawSgpGameStats for a page of full games, off the Qt event loop: the model score is
    ~1 ms per game (~60 ms per page of 50). Only the extracted fields are kept, not the json
    """
    return await asyncio.to_thread(lambda: [rawSgpGameStats(g, puuid) for g in page])


async def fetchSeasonGames(puuid, cap=None, now=None):
    """
    本赛季对局的统计字段 (最新在前, 至多 cap 局, 默认 seasonCap(source)); 一页都拿不到返回 None.
    @return: (games, source, info), source 为 'sgp' (CN) / 'match' (non-CN Riot match server,
    all ten players like 'sgp') / 'lcu' (own stats only); info {'capped', 'partial'}: the
    sample is only the newest games (cap reached / a later page failed twice)
    """
    start = seasonStart(now)
    lcuCap = cap or SEASON_CAP

    async def lcuPage(beg, end):
        page = await connector.getSummonerGamesByPuuidQuietly(puuid, beg, end)
        return None if page is None else [rawLcuGameStats(g) for g in page]

    if not connector.inTencent:
        async def matchPage(beg, end):
            page = await connector.getMatchHistoryViaSgpQuietly(puuid, beg, end - beg + 1)
            return None if page is None else await _extractSgpPage(page, puuid)

        matchCap = cap or MATCH_SEASON_CAP
        games, complete = await _fetchPages(matchPage, start, matchCap, MATCH_PAGE_SIZE,
                                            shortPageEnds=True)
        if games:
            match = _sample(games, start, matchCap, complete)
            if complete or len(games) > MATCH_PAGE_SIZE:
                return match[0], 'match', match[1]
            # only the first page came back: the client's own list may reach further back
            # (own stats only); used only when it really has more season games
            lcu, lcuComplete = await _fetchPages(lcuPage, start, lcuCap)
            if lcu:
                fallback = _sample(lcu, start, lcuCap, lcuComplete)
                if len(fallback[0]) > len(match[0]):
                    return fallback[0], 'lcu', fallback[1]
            return match[0], 'match', match[1]

    cap = lcuCap

    if connector.inTencent and getattr(connector, 'sgpSess', None) \
            and getattr(connector, 'sgpToken', None):
        async def sgpPage(beg, end):
            try:
                res = await connector.getSummonerGamesByPuuidViaSGP(puuid, beg, end)
                return await _extractSgpPage(res['games'], puuid)
            except Exception as e:
                logger.warning(f"SGP season page {beg}-{end} failed: {e!r}", TAG)
                return None

        games, complete = await _fetchPages(sgpPage, start, cap)
        if games:
            season, info = _sample(games, start, cap, complete)
            return season, 'sgp', info

    games, complete = await _fetchPages(lcuPage, start, cap)
    if not games:
        return None, 'lcu', {'capped': False, 'partial': not complete}
    season, info = _sample(games, start, cap, complete)
    return season, 'lcu', info


async def fillLcuKp(seasonGames, recentGames, puuid, cap=KP_DETAIL_CAP):
    """
    LCU 赛季样本补参团率: 先用近期对局已有的 kp (attachGameScores 算过), 再为至多 cap 局
    其余赛季对局拉完整对局 (走 connector.semaphore, 逐局, 后台); 原地写入 seasonGames
    """
    from app.lol.tools import getGameScoreFromLcuDetail

    known = {g['gameId']: g.get('kp') for g in recentGames or [] if g}
    for g in seasonGames:
        if g['kp'] is None and known.get(g['gameId']) is not None:
            g['kp'] = known[g['gameId']]

    todo = [g for g in seasonGames
            if g['kp'] is None and not g['remake'] and g['queueId'] not in (1700, 1710)][:cap]
    if not todo:
        return

    # 逐局 await (不 gather): 每局之间让出信号量, 不把对局信息页等前台请求排在 30 个之后
    for g in todo:
        detail = await connector.getGameDetailByGameIdQuietly(g['gameId'])
        score = getGameScoreFromLcuDetail(detail, puuid)
        if score and score.get('kp') is not None:
            g['kp'] = score['kp']


def rankedSeasonFallback(rankInfo):
    """战绩拿不到时的赛季胜率: 单双 + 灵活的 wins / losses (他人 losses 常为 0, 那时不用)"""
    queueMap = (rankInfo or {}).get('queueMap') or {}
    wins = losses = 0
    for queue in ('RANKED_SOLO_5x5', 'RANKED_FLEX_SR'):
        entry = queueMap.get(queue) or {}
        if entry.get('losses'):
            wins += entry.get('wins', 0) or 0
            losses += entry['losses']
    if not losses:
        return None
    return {'games': wins + losses, 'winRate': wins / (wins + losses), 'kda': None,
            'kp': None, 'kpGames': 0, 'duration': None, 'rankedOnly': True}
