"""
对线得分 (OP.GG 的 "Laning 30 : 70"): 开局前 14 分钟与同位置对手相比的对线优势.

公式 (laningScore):
    取第 14 分钟的时间线帧 (按 timestamp 取最接近 14:00 的一帧), 对每项指标算
    份额 = 自己 / (自己 + 对手); 得分 = round(100 * Σ 权重 * 份额), 对手得分 = 100 - 得分.
        权重: 金币 50%, 经验 30%, 补刀 (小兵 + 野怪) 20%;
        辅助 (UTILITY) 不算补刀: 金币 60%, 经验 40%.
    双方某项都是 0 时去掉该项, 其余权重按比例放大.

对手判定:
    - 自选征召 / 单双排 / 灵活排位 (400 / 420 / 440): 两队各 5 人时 participantId 1-5 与 6-10
      按 上 / 野 / 中 / 下 / 辅 排列 (实测与 Riot 对局服务器的 teamPosition 一致),
      对手 = participantId ± 5. 不另发请求.
    - 其他召唤师峡谷模式: 用 Riot 对局服务器的对局结果 (teamPosition) 找同位置对手.
    - 不足 15 分钟 / 重开 / 非召唤师峡谷 (大乱斗, 斗魂竞技场 ...) / 找不到唯一对手: 不显示.

LCU 对局详情里的 timeline.lane / role 不可靠 (常见整队 NONE / SUPPORT 或两个 JUNGLE), 不用.
"""
import asyncio

from ..common.logger import logger
from .connector import connector

TAG = "laning"

LANING_MINUTE = 14
MIN_DURATION_SECS = 15 * 60
ORDERED_QUEUES = (400, 420, 440)
POSITIONS = ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')

WEIGHTS = {'gold': 0.5, 'xp': 0.3, 'cs': 0.2}
SUPPORT_WEIGHTS = {'gold': 0.6, 'xp': 0.4}

# gameId -> {participantId: {'gold', 'xp', 'cs'}} (第 14 分钟帧); 失败不缓存
_snapshots = {}
# gameId -> {puuid: teamPosition} (Riot 对局服务器的对局结果)
_positions = {}
_semaphore = None


def laningScore(mine, theirs, position=None):
    """
    mine / theirs: {'gold', 'xp', 'cs'} (第 14 分钟). 返回 (自己, 对手), 两数和为 100;
    所有指标双方都是 0 时返回 None. 公式见模块说明
    """
    weights = SUPPORT_WEIGHTS if position == 'UTILITY' else WEIGHTS
    total = share = 0.0
    for metric, weight in weights.items():
        a, b = max(mine.get(metric) or 0, 0), max(theirs.get(metric) or 0, 0)
        if a + b <= 0:
            continue
        total += weight
        share += weight * a / (a + b)

    if total <= 0:
        return None

    score = round(100 * share / total)
    return score, 100 - score


def laningSnapshot(timeline, minute=LANING_MINUTE):
    """时间线 -> 最接近第 minute 分钟那一帧的 {participantId: {'gold', 'xp', 'cs'}}, 没有帧返回 None"""
    frames = [frame for frame in (timeline or {}).get('frames') or []
              if isinstance(frame, dict) and isinstance(frame.get('participantFrames'), dict)
              and frame['participantFrames']]
    if not frames:
        return None

    target = minute * 60000
    frame = min(frames, key=lambda f: abs((f.get('timestamp') or 0) - target))
    snapshot = {}
    for key, data in frame['participantFrames'].items():
        try:
            pid = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            snapshot[pid] = {
                'gold': data.get('totalGold') or 0,
                'xp': data.get('xp') or 0,
                'cs': (data.get('minionsKilled') or 0) + (data.get('jungleMinionsKilled') or 0)}
    return snapshot or None


def isLaningCandidate(game):
    """这局有没有可能算对线得分 (只看战绩列表里已有的字段, 不发请求)"""
    return bool(game and not game.get('remake')
                and (game.get('durationSecs') or 0) >= MIN_DURATION_SECS
                and game.get('gameMode') == 'CLASSIC'
                and game.get('queueId') not in (1700, 1710)
                and game.get('participants'))


def laneOpponent(game, puuid, positions=None):
    """
    (自己的 participantId, 对手的 participantId, 位置), 判断不了返回 None.
    game['participants'] 来自 gameParticipantsFromLcuDetail (含 participantId / teamId / puuid);
    positions 为 {puuid: teamPosition} (Riot 对局服务器), 只在非 ORDERED_QUEUES 时需要
    """
    players = [p for p in game.get('participants') or [] if p.get('participantId')]
    me = next((p for p in players if p.get('puuid') == puuid), None)
    if not me:
        return None

    if game.get('queueId') in ORDERED_QUEUES:
        ids = {p['participantId']: p.get('teamId') for p in players}
        if sorted(ids) != list(range(1, 11)) or any(
                ids[i] != ids[1] for i in range(1, 6)) or any(
                ids[i] != ids[6] or ids[i] == ids[1] for i in range(6, 11)):
            return None
        myId = me['participantId']
        oppId = myId + 5 if myId <= 5 else myId - 5
        return myId, oppId, POSITIONS[(myId - 1) % 5]

    if not positions:
        return None
    position = positions.get(puuid)
    if position not in POSITIONS:
        return None
    opponents = [p for p in players if p.get('teamId') != me.get('teamId')
                 and positions.get(p.get('puuid')) == position]
    if len(opponents) != 1:
        return None
    return me['participantId'], opponents[0]['participantId'], position


async def _limited(coro):
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(4)
    async with _semaphore:
        return await coro


async def _getPositions(gameId):
    if gameId not in _positions:
        summary = await _limited(connector.getGameSummaryViaSgpQuietly(gameId))
        if not summary or summary.get('mapId') not in (11, None):
            return None
        _positions[gameId] = {p.get('puuid'): p.get('teamPosition') or ''
                              for p in summary.get('participants') or [] if p.get('puuid')}
    return _positions[gameId]


async def _getSnapshot(gameId):
    if gameId not in _snapshots:
        timeline = await _limited(connector.getGameTimelineQuietly(gameId))
        snapshot = laningSnapshot(timeline)
        if not snapshot:
            return None
        _snapshots[gameId] = snapshot
    return _snapshots[gameId]


async def getLaningScore(game, puuid):
    """一局的对线得分 {'mine', 'theirs', 'position'}, 不适用或请求失败返回 None (结果按 gameId 缓存)"""
    if not isLaningCandidate(game):
        return None

    gameId = game['gameId']
    positions = None
    if game.get('queueId') not in ORDERED_QUEUES:
        positions = await _getPositions(gameId)
    lane = laneOpponent(game, puuid, positions)
    if not lane:
        return None

    snapshot = await _getSnapshot(gameId)
    myId, oppId, position = lane
    if not snapshot or myId not in snapshot or oppId not in snapshot:
        return None

    score = laningScore(snapshot[myId], snapshot[oppId], position)
    if not score:
        return None
    return {'mine': score[0], 'theirs': score[1], 'position': position}


async def attachLaningScores(games, puuid, onGame=None):
    """
    生涯页第二阶段: 战绩行显示后再逐局补上 game['laning'] (见 getLaningScore), 每算完一局
    调一次 onGame(game). 已算过 / 已检查过的局跳过; 任何失败都只是不显示
    """
    async def one(game):
        try:
            laning = await getLaningScore(game, puuid)
        except Exception as e:
            logger.warning(f"laning score {game.get('gameId')} failed: {e!r}", TAG)
            laning = None
        game['laningChecked'] = True
        if laning:
            game['laning'] = laning
            if onGame is not None:
                onGame(game)

    await asyncio.gather(*[one(game) for game in games
                           if game and not game.get('laningChecked')])
