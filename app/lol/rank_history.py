"""
段位走势 (Tier graph) 的本地快照. Riot / LCU 没有 LP 历史接口, 只能每次读到某人的段位时记一笔:

    rank_history.json (与 config.json 同目录):
    {"version": 1, "players": {puuid: {queue: [[timestamp, tier, division, lp], ...]}}}

- 与上一笔段位 / 小段 / LP 完全相同时不记;
- 每个队列最多保留 MAX_POINTS 笔 (丢最旧的);
- 视图按 日 / 周 / 月 聚合, 每个时间段取最后一笔.
纯数据模块: 不依赖 Qt / 视图层.
"""
import datetime
import json
import os
import time

from app.common.platform import config_dir

QUEUES = ('RANKED_SOLO_5x5', 'RANKED_FLEX_SR')
MAX_POINTS = 2000

TIERS = ('IRON', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'EMERALD', 'DIAMOND')
APEX_TIERS = ('MASTER', 'GRANDMASTER', 'CHALLENGER')
DIVISIONS = {'IV': 4, 'III': 3, 'II': 2, 'I': 1}
TIER_SHORT = {'IRON': 'I', 'BRONZE': 'B', 'SILVER': 'S', 'GOLD': 'G', 'PLATINUM': 'P',
              'EMERALD': 'E', 'DIAMOND': 'D', 'MASTER': 'M', 'GRANDMASTER': 'GM',
              'CHALLENGER': 'C'}
APEX_BASE = len(TIERS) * 400   # 钻石 I 100 LP 的顶

# 可在测试里改成别的路径
storePath = os.path.join(config_dir(), 'rank_history.json')

_data = None
_dataPath = None
_unsaved = False    # 上次写盘失败 (如文件被占用), 下次 record 时重试


def _load():
    global _data, _dataPath
    if _data is not None and _dataPath == storePath:
        return _data

    data = None
    exists = os.path.exists(storePath)
    try:
        with open(storePath, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        pass
    if not isinstance(data, dict) or not isinstance(data.get('players'), dict):
        if exists:
            # 文件坏了: 先备份成 .bak 再从空开始, 不直接覆盖掉历史
            try:
                os.replace(storePath, storePath + '.bak')
            except OSError:
                pass
        data = {'version': 1, 'players': {}}

    _data, _dataPath = data, storePath
    return data


def _save(data):
    os.makedirs(os.path.dirname(storePath), exist_ok=True)
    tmp = storePath + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    os.replace(tmp, storePath)


def isRanked(tier):
    return bool(tier) and (tier in TIERS or tier in APEX_TIERS)


def record(puuid, queueMap, now=None):
    """
    queueMap: LCU ranked-stats 的 queueMap. 记下各队列的当前段位, 与上一笔相同则跳过;
    返回是否有新记录. 读写失败一律吞掉 (不能因为这个让生涯页出错)
    """
    global _unsaved
    try:
        if not puuid or not isinstance(queueMap, dict):
            return False

        now = int(now if now is not None else time.time())
        data = _load()
        changed = False
        for queue in QUEUES:
            entry = queueMap.get(queue) or {}
            tier = entry.get('tier')
            if not isRanked(tier):
                continue
            division = entry.get('division') or ''
            if tier in APEX_TIERS or division not in DIVISIONS:
                division = ''
            lp = int(entry.get('leaguePoints') or 0)

            points = data['players'].setdefault(puuid, {}).setdefault(queue, [])
            if points and points[-1][1:] == [tier, division, lp]:
                continue
            points.append([now, tier, division, lp])
            del points[:-MAX_POINTS]
            changed = True

        if changed or _unsaved:
            try:
                _save(data)
                _unsaved = False
            except OSError:
                _unsaved = True
        return changed
    except Exception:
        return False


def history(puuid, queue='RANKED_SOLO_5x5'):
    """[(timestamp, tier, division, lp)], 按时间排序"""
    try:
        points = (_load()['players'].get(puuid) or {}).get(queue) or []
        return sorted((tuple(p) for p in points if len(p) == 4), key=lambda p: p[0])
    except Exception:
        return []


def rankValue(tier, division, lp):
    """段位 -> 单调的纵轴值: 每个大段 400, 每个小段 100; 大师以上 = 钻石 I 顶 + LP"""
    if tier in APEX_TIERS:
        return APEX_BASE + max(lp, 0)
    if tier not in TIERS:
        return 0
    return (TIERS.index(tier) * 400 + (4 - DIVISIONS.get(division, 4)) * 100
            + min(max(lp, 0), 100))


def shortLabel(tier, division):
    """'DIAMOND', 'III' -> 'D 3'; 大师以上没有小段: 'M' / 'GM' / 'C'"""
    short = TIER_SHORT.get(tier, '?')
    if tier in APEX_TIERS or division not in DIVISIONS:
        return short
    return f"{short} {DIVISIONS[division]}"


def _bucket(ts, period):
    day = datetime.date.fromtimestamp(ts)
    if period == 'week':
        return day - datetime.timedelta(days=day.weekday())
    if period == 'month':
        return day.replace(day=1)
    return day


def aggregate(points, period='day'):
    """
    period: 'day' | 'week' | 'month'. 每个时间段取最后一笔 (本地时区, 周从周一起);
    返回 [{'timestamp', 'tier', 'division', 'lp', 'value', 'label'}], 按时间排序
    """
    buckets = {}
    for point in sorted(points, key=lambda p: p[0]):
        buckets[_bucket(point[0], period)] = point

    out = []
    for key in sorted(buckets):
        ts, tier, division, lp = buckets[key]
        out.append({'timestamp': ts, 'tier': tier, 'division': division, 'lp': lp,
                    'value': rankValue(tier, division, lp),
                    'label': shortLabel(tier, division)})
    return out
