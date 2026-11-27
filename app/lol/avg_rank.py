"""
对局平均段位 (OP.GG 战绩行里的 "Gold 4"): 一局所有玩家段位的平均. 对局数据 (LCU / SGP)
本身不带段位, 按下面的顺序找, 结果写在 game['avgRank'] = {'tier', 'division', 'source', ...}:

1. 'opgg': OP.GG 记下的打那局时的平均段位 (opgg_history.py, 每名玩家一次请求, 最近 20 局).
   OP.GG 没有的服务器 (国服) / 没收录的对局 / 请求失败时没有.
2. 'snapshot': Seraphine 自己在对局开始时记下的 10 人段位 (snapshotGame, 存在
   <config_dir()>/game_ranks.json, 只存段位数值, 不存玩家). 只有开着 Seraphine 打的局才有.
3. 'estimate': 玩家**当前**段位的平均 (LCU ranked-stats), 不是打那局时的段位, 界面上标 "≈".

2 / 3 的算法 (averageRank):
    段位 -> 数值: 黑铁 IV = 0, 每个小段 +1 (黑铁 I = 3, 黄铜 IV = 4 ... 钻石 I = 27),
    大师 = 28, 宗师 = 32, 王者 = 36 (每档按 4 个小段算). 未定级 / 取不到的玩家不计入;
    平均值四舍五入后换回段位. 一名有段位的玩家也算 (用户的选择).
    灵活排位 (440) 用灵活组排段位, 其他一律用单双排段位 (与 OP.GG 相同).
    不算: 重开, 自定义 / 训练模式 / 人机 / 新手教程, 只有一方的对局, 以及带机器人的对局.
    (OP.GG 给了数值的局照 OP.GG 显示, 它连重开的局也给.)

请求量: 每名玩家每个会话只查一次 (_ranks 缓存 RANK_TTL 秒, 同时进行的重复查询合并);
一页 18 局 x 10 人全走估算时约 160 次本机 LCU 请求, 自带并发上限, 不占满 connector 的信号量.
"""
import asyncio
import json
import os
import time

from ..common.logger import logger
from ..common.platform import config_dir
from . import opgg_history
from .connector import connector

TAG = "avgRank"

TIERS = ('IRON', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'EMERALD', 'DIAMOND')
DIVISIONS = ('IV', 'III', 'II', 'I')
APEX = {'MASTER': 28, 'GRANDMASTER': 32, 'CHALLENGER': 36}
QUEUE_KEYS = ('RANKED_SOLO_5x5', 'RANKED_FLEX_SR')

FLEX_QUEUE = 440
# 自定义 (0 / 3100), 训练模式 (3140), 新手教程 (2000 ~ 2020)
EXCLUDED_QUEUES = frozenset((0, 3100, 3140, 2000, 2010, 2020))
EXCLUDED_MODES = frozenset(('PRACTICETOOL', 'TUTORIAL', 'TUTORIAL_MODULE_1',
                            'TUTORIAL_MODULE_2', 'TUTORIAL_MODULE_3'))
BOT_PUUID = '00000000-0000-0000-0000-000000000000'

RANK_TTL = 15 * 60
CONCURRENCY = 6

# puuid -> (monotonic 时间, {'RANKED_SOLO_5x5': 数值或 None, 'RANKED_FLEX_SR': ...}); 失败不缓存
_ranks = {}
# puuid -> 进行中的查询 (同一名玩家出现在多局里只查一次)
_pending = {}
_semaphore = None

# 对局开始时的段位记录; 可在测试里改成别的路径
storePath = os.path.join(config_dir(), 'game_ranks.json')
MAX_SNAPSHOTS = 500
_store = None
_storeLoadedFrom = None


def rankValue(tier, division):
    """LCU 原始段位 -> 数值 (见模块说明); 未定级 / 不认识的段位返回 None"""
    tier = (tier or '').upper()
    if tier in APEX:
        return APEX[tier]
    if tier not in TIERS:
        return None
    division = (division or '').upper()
    # 有段位但小段缺失 / 异常: 按该段位最低的小段算
    step = DIVISIONS.index(division) if division in DIVISIONS else 0
    return TIERS.index(tier) * 4 + step


def rankFromValue(value):
    """数值 -> (tier, division); 大师及以上 division 为 ''"""
    value = max(0, round(value))
    for tier, base in sorted(APEX.items(), key=lambda item: -item[1]):
        if value >= base:
            return tier, ''
    return TIERS[value // 4], DIVISIONS[value % 4]


def queueRankKey(queueId):
    """这局该看 ranked-stats queueMap 里的哪个队列"""
    return 'RANKED_FLEX_SR' if queueId == FLEX_QUEUE else 'RANKED_SOLO_5x5'


def rankValues(rankStats):
    """ranked-stats 响应 -> {'RANKED_SOLO_5x5': 数值或 None, 'RANKED_FLEX_SR': ...}"""
    queueMap = (rankStats or {}).get('queueMap')
    queueMap = queueMap if isinstance(queueMap, dict) else {}
    values = {}
    for key in QUEUE_KEYS:
        entry = queueMap.get(key)
        entry = entry if isinstance(entry, dict) else {}
        values[key] = rankValue(entry.get('tier'), entry.get('division'))
    return values


def gamePuuids(game):
    """这局要查段位的玩家 (去重, 保持顺序); 不适用的对局返回 [] (只看战绩里已有的字段)"""
    if not game or game.get('remake'):
        return []
    queueId = game.get('queueId')
    # 人机 (830 ~ 899) 没有意义; queueId 缺失时无法判断是不是自定义
    if queueId is None or queueId in EXCLUDED_QUEUES or 830 <= queueId <= 899:
        return []
    if game.get('gameMode') in EXCLUDED_MODES:
        return []

    players = game.get('participants') or []
    if len({player.get('teamId') for player in players}) < 2:
        return []  # 只有一方 (单人训练之类) 不是一场对局

    puuids = []
    for player in players:
        puuid = player.get('puuid')
        if not puuid or puuid == BOT_PUUID:
            return []  # 带机器人 / 匿名玩家的局不算
        if puuid not in puuids:
            puuids.append(puuid)
    return puuids


def averageRank(values):
    """
    values: 每名玩家的段位数值 (None = 未定级 / 没取到). 返回
    {'tier', 'division', 'players' (计入的人数), 'total'}; 没有人有段位时返回 None
    """
    values = list(values)
    ranked = [v for v in values if v is not None]
    if not ranked:
        return None
    tier, division = rankFromValue(sum(ranked) / len(ranked))
    return {'tier': tier, 'division': division, 'players': len(ranked), 'total': len(values)}


async def _fetch(puuid):
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(CONCURRENCY)
    try:
        async with _semaphore:
            stats = await connector.getRankedStatsByPuuidQuietly(puuid)
        if not stats:
            return None
        values = rankValues(stats)
        _ranks[puuid] = (time.monotonic(), values)
        return values
    finally:
        _pending.pop(puuid, None)


async def _playerRanks(puuid):
    """一名玩家各队列的段位数值; 查询失败返回 None"""
    cached = _ranks.get(puuid)
    if cached and time.monotonic() - cached[0] < RANK_TTL:
        return cached[1]

    task = _pending.get(puuid)
    if task is None:
        task = _pending[puuid] = asyncio.ensure_future(_fetch(puuid))
    # shield: 这一页被取消 (换人 / 刷新) 时不连带取消别的对局也在等的同一个查询
    return await asyncio.shield(task)


# ---------------- 对局开始时的段位记录 ----------------

def _loadStore():
    global _store, _storeLoadedFrom
    if _store is not None and _storeLoadedFrom == storePath:
        return _store

    games = {}
    try:
        with open(storePath, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get('games'), dict):
            games = data['games']
    except (OSError, ValueError):
        pass
    _store, _storeLoadedFrom = games, storePath
    return games


def saveSnapshot(gameId, ranks, now=None):
    """
    记下一局开始时各玩家的段位. ranks: 每名玩家的 rankValues 结果 (没查到的为 None).
    只保留最近 MAX_SNAPSHOTS 局; 写盘失败只是这局没有记录
    """
    values = {key: [r.get(key) if r else None for r in ranks] for key in QUEUE_KEYS}
    games = _loadStore()
    games[str(gameId)] = {'t': int(now if now is not None else time.time()), **values}
    for old in sorted(games, key=lambda g: games[g].get('t') or 0)[:-MAX_SNAPSHOTS]:
        del games[old]

    try:
        os.makedirs(os.path.dirname(storePath), exist_ok=True)
        tmp = storePath + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'version': 1, 'games': games}, f, separators=(',', ':'))
        os.replace(tmp, storePath)
    except OSError as e:
        logger.warning(f"game rank snapshot not saved: {type(e).__name__}", TAG)


def snapshotRank(game):
    """这局开始时记下的平均段位 (见 averageRank), 没有记录返回 None"""
    try:
        entry = _loadStore().get(str(game.get('gameId')))
        values = entry.get(queueRankKey(game.get('queueId'))) if entry else None
        if not isinstance(values, list):
            return None
        return averageRank(v if isinstance(v, int) else None for v in values)
    except Exception as e:
        logger.warning(f"game rank snapshot unreadable: {type(e).__name__}", TAG)
        return None


async def snapshotGame(session):
    """
    对局开始时 (gameflow session 里已有双方玩家) 查一遍所有玩家的段位并记下来, 之后这局的
    平均段位就是打这局时的. 任何失败都只是没有记录; 自定义 / 人机等不记
    """
    try:
        data = (session or {}).get('gameData') or {}
        gameId = data.get('gameId')
        queueId = (data.get('queue') or {}).get('id')
        teams = [data.get('teamOne') or [], data.get('teamTwo') or []]
        game = {'queueId': queueId, 'participants': [
            {'puuid': player.get('puuid'), 'teamId': index}
            for index, team in enumerate(teams) for player in team]}
        puuids = gamePuuids(game)
        if not gameId or not puuids or str(gameId) in _loadStore():
            return

        ranks = await asyncio.gather(*[_playerRanks(p) for p in puuids])
        if all(r is None for r in ranks):
            return
        saveSnapshot(gameId, ranks)
    except Exception as e:
        logger.warning(f"game rank snapshot failed: {type(e).__name__}", TAG)


# ---------------- 战绩行 ----------------

async def estimateRank(game):
    """
    按玩家当前段位估算的平均段位 (见 averageRank). 返回 (结果或 None, 是否已有定论):
    不适用的对局 / 查到了但没人有段位 -> (None, True); 所有查询都失败 -> (None, False), 下次再试
    """
    puuids = gamePuuids(game)
    if not puuids:
        return None, True

    ranks = await asyncio.gather(*[_playerRanks(p) for p in puuids])
    if all(r is None for r in ranks):
        return None, False

    key = queueRankKey(game.get('queueId'))
    return averageRank([r.get(key) if r else None for r in ranks]), True


async def _opggEntries(riotId):
    """被查看玩家在 OP.GG 上最近对局的平均段位; 不适用 / 失败返回 []"""
    name, tagLine = riotId or (None, None)
    region = opgg_history.opggRegion(getattr(connector, 'server', None))
    if not name or not tagLine or not region:
        return []
    try:
        return await opgg_history.fetchAverageTiers(name, tagLine, region)
    except Exception as e:
        logger.warning(f"OP.GG average tiers failed: {type(e).__name__}", TAG)
        return []


async def attachAverageRanks(games, onGame=None, riotId=None):
    """
    生涯页第二阶段: 战绩行显示后再逐局补上 game['avgRank'] (来源顺序见模块说明), 每补上一局
    调一次 onGame(game). 已有定论的局跳过; 任何失败都只是不显示.
    riotId: 被查看玩家的 (gameName, tagLine), 给了才问 OP.GG
    """
    games = [game for game in games if game and not game.get('avgRankChecked')]
    if not games:
        return

    def done(game, rank, source):
        game['avgRankChecked'] = True
        if rank:
            game['avgRank'] = {**rank, 'source': source}
            if onGame is not None:
                onGame(game)

    entries = await _opggEntries(riotId)
    rest = []
    for game in games:
        entry = opgg_history.matchEntry(entries, game)
        if entry:
            done(game, {'tier': entry['tier'], 'division': entry['division']}, 'opgg')
            continue
        rank = snapshotRank(game)
        if rank:
            done(game, rank, 'snapshot')
        else:
            rest.append(game)

    async def one(game):
        try:
            rank, settled = await estimateRank(game)
        except Exception as e:
            logger.warning(
                f"average rank {game.get('gameId')} failed: {type(e).__name__}", TAG)
            return
        if settled:
            done(game, rank, 'estimate')
        else:
            game['avgRankChecked'] = False

    await asyncio.gather(*[one(game) for game in rest])
