"""
排位局 (单双 / 灵活) 的 OP Score: 拟合 OP.GG 真实评分的小神经网络.

模型与特征移植自 ignaciomigliaro/lol-friends-ladder (MIT, src/opscore.js 与
src/opscore-model.json, PR #5 及之后的重训). 原作者用约 200 局排位里 OP.GG 给每名玩家的
每分钟评分训练; 留出局上平均误差约 0.3, 最差约 1.6. 训练数据来自一个拉美北服好友圈的
排位局, 其他段位 / 大区 / 版本的误差没有测过.

    输入: Riot 对局服务器 SUMMARY (match-v5 info) 里全部 10 人的结算数据, 每人相对本队 /
          同位置对手 / 全场的 44 个特征 (见 features)
    模型: 5 个 44 -> 32 (ReLU) -> 1 的网络取平均, 截断到 0 ~ 10

只用于 420 / 440: 模型只见过排位局, 依赖 teamPosition 找对位. 其他模式, 或 SUMMARY 取不到 /
缺字段时调用方退回 tools.scoreGameParticipants 的原公式.
LCU 对局详情没有经验值 / 镀层 / 推塔参与 / 个人小龙男爵 / teamPosition, 所以要另取 SUMMARY.
"""
import asyncio
import json
import math
import os
from collections import OrderedDict

from ..common.logger import logger
from .connector import connector

TAG = "opscore"

MODEL_QUEUES = (420, 440)
MODEL_PATH = os.path.join('app', 'resource', 'opscore', 'opscore-model.json')
POSITIONS = ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')

# 这些字段 LCU 对局详情没有; SUMMARY 也缺时不用模型
REQUIRED_FIELDS = ('champExperience', 'turretTakedowns', 'dragonKills', 'baronKills',
                   'teamPosition')

_model = None
# gameId -> SUMMARY (成功的才缓存; 同一局常出现在车队几个人的战绩里)
_summaries = OrderedDict()
_SUMMARY_CACHE_SIZE = 600
_semaphore = None


def model():
    global _model
    if _model is None:
        with open(MODEL_PATH, encoding='utf-8') as f:
            _model = json.load(f)
    return _model


def summaryStates(info):
    """
    SUMMARY (match-v5 info) -> {participantId: 结算状态}; 不是两队各 5 人 / 缺字段时返回 None.
    participantId 缺失时按出现顺序编号 (从 1 开始)
    """
    try:
        participants = info['participants']
        teams = {t['teamId']: t.get('objectives') or {} for t in info.get('teams') or []}
        if len(participants) != 10:
            return None

        states = {}
        for index, p in enumerate(participants):
            if any(p.get(k) is None for k in REQUIRED_FIELDS):
                return None
            plates = (p.get('challenges') or {}).get('turretPlatesTaken')
            if plates is None:
                return None

            objectives = teams.get(p['teamId'], {})

            def kills(name):
                return (objectives.get(name) or {}).get('kills', 0)

            states[p.get('participantId') or index + 1] = {
                'teamId': p['teamId'],
                'pos': p['teamPosition'] or 'MIDDLE',
                'k': p['kills'], 'd': p['deaths'], 'a': p['assists'],
                'gold': p['goldEarned'],
                'xp': p['champExperience'],
                'level': p['champLevel'],
                'cs': p['totalMinionsKilled'] + p['neutralMinionsKilled'],
                'dmg': p['totalDamageDealtToChampions'],
                'taken': p['totalDamageTaken'],
                'wards': p.get('wardsPlaced', 0),
                'wardKills': p.get('wardsKilled', 0),
                'towers': p['turretTakedowns'],
                'plates': plates,
                'obj': p['dragonKills'] + 2 * p['baronKills'],
                'tTowers': kills('tower'),
                'tObj': kills('dragon') + 2 * kills('baron') + kills('riftHerald'),
                'win': 1 if p['win'] else 0,
            }

        if sorted(s['teamId'] for s in states.values()).count(100) != 5:
            return None
        return states
    except (KeyError, TypeError, AttributeError):
        return None


def features(states, minutes):
    """每名玩家的 44 个模型输入 (与原 src/opscore.js 的 features 一一对应)"""
    players = list(states.values())
    n = len(players)
    m = max(1, minutes)
    rm = math.sqrt(m)

    def total(group, key):
        return sum(v[key] for v in group)

    def lobby(key):
        return max(1, total(players, key) / n)

    out = {}
    for pid, v in states.items():
        mates = [x for x in players if x['teamId'] == v['teamId']]
        foes = [x for x in players if x['teamId'] != v['teamId']]
        opp = next((x for x in foes if x['pos'] == v['pos']), v)
        tk = max(1, total(mates, 'k'))
        ek = total(foes, 'k')
        sup = 1 if v['pos'] == 'UTILITY' else 0
        goldDiff = total(mates, 'gold') - total(foes, 'gold')

        out[pid] = {
            'kda': math.log((v['k'] + v['a'] + 1) / (v['d'] + 1)),
            'k': v['k'] / m * 10, 'd': v['d'] / m * 10, 'a': v['a'] / m * 10,
            'kp': (v['k'] + v['a']) / tk,
            'deathShare': v['d'] / max(1, ek),
            'gold': v['gold'] / lobby('gold'),
            'xp': v['xp'] / lobby('xp'),
            'cs': 0 if sup else v['cs'] / lobby('cs'),
            'dmg': v['dmg'] / lobby('dmg'),
            'taken': v['taken'] / lobby('taken'),
            'goldOpp': (v['gold'] - opp['gold']) / 1000 / rm,
            'xpOpp': (v['xp'] - opp['xp']) / 1000 / rm,
            'csOpp': 0 if sup else (v['cs'] - opp['cs']) / 10 / rm,
            'dmgOpp': (v['dmg'] - opp['dmg']) / 1000 / rm,
            'wards': v['wards'] / m, 'wardKills': v['wardKills'] / m,
            'towers': v['towers'], 'plates': v['plates'], 'obj': v['obj'],
            'teamGold': goldDiff / 1000 / rm,
            'teamKills': (tk - ek) / 5,
            'teamTowers': v['tTowers'],
            'oppKda': math.log((opp['k'] + opp['a'] + 1) / (opp['d'] + 1)),
            'oppD': opp['d'] / m * 10,
            'teamDeaths': total(mates, 'd') / m, 'foeDeaths': total(foes, 'd') / m,
            'goldTeamShare': v['gold'] / max(1, total(mates, 'gold')),
            'dmgTeamShare': v['dmg'] / max(1, total(mates, 'dmg')),
            'takenTeamShare': v['taken'] / max(1, total(mates, 'taken')),
            'levelOpp': v['level'] - opp['level'],
            'teamGoldRaw': goldDiff / 10000,
            'towerDiff': v['tTowers'] - (foes[0]['tTowers'] if foes else 0),
            'objDiff': v['tObj'] - (foes[0]['tObj'] if foes else 0),
            'kRel': v['k'] / lobby('k'), 'dRel': v['d'] / lobby('d'),
            'aRel': v['a'] / lobby('a'),
            'sup': sup, 'jg': int(v['pos'] == 'JUNGLE'), 'top': int(v['pos'] == 'TOP'),
            'adc': int(v['pos'] == 'BOTTOM'),
            'minutes': m / 30,
            'win': v['win'], 'end': 1,
        }
    return out


def runNet(net, x):
    v = [(x[k] - mean) / sd for k, mean, sd in zip(net['keys'], net['mean'], net['sd'])]
    y = net['b2']
    for w, b, w2 in zip(net['W1'], net['b1'], net['W2']):
        h = b + sum(a * c for a, c in zip(w, v))
        if h > 0:
            y += h * w2
    return y


def predict(x):
    nets = model()['nets']
    y = sum(runNet(net, x) for net in nets) / len(nets)
    return min(max(y, 0.0), 10.0)


def scoreSummary(info, queueId=None):
    """
    SUMMARY -> {participantId: 未取整的 0 ~ 10 分}; 不适用 (非排位 / 缺字段 / 重开) 返回 None
    """
    if not isinstance(info, dict):
        return None
    if (queueId if queueId is not None else info.get('queueId')) not in MODEL_QUEUES:
        return None
    states = summaryStates(info)
    if states is None or any(p.get('gameEndedInEarlySurrender') for p in info['participants']):
        return None
    duration = info.get('gameDuration') or 0
    if not states or duration <= 0:
        return None

    try:
        return {pid: predict(x) for pid, x in features(states, duration / 60).items()}
    except Exception as e:
        logger.warning(f"opscore predict failed: {e!r}", TAG)
        return None


def matchesLcuDetail(info, detail):
    """SUMMARY 与 LCU 对局详情的 participantId 是不是同一批人 (逐个比英雄)"""
    try:
        lcu = {p['participantId']: p['championId'] for p in detail['participants']}
        sgp = {p['participantId']: p['championId'] for p in info['participants']}
    except (KeyError, TypeError):
        return False
    return bool(lcu) and lcu == sgp


async def getSummary(gameId):
    """Riot 对局服务器上一局的 SUMMARY, 按 gameId 缓存; 失败返回 None (不缓存)"""
    global _semaphore
    if gameId in _summaries:
        _summaries.move_to_end(gameId)
        return _summaries[gameId]

    if _semaphore is None:
        _semaphore = asyncio.Semaphore(8)
    async with _semaphore:
        summary = await connector.getGameSummaryViaSgpQuietly(gameId)

    if summary:
        _summaries[gameId] = summary
        while len(_summaries) > _SUMMARY_CACHE_SIZE:
            _summaries.popitem(last=False)
    return summary


async def scoresForLcuDetail(detail):
    """
    LCU 对局详情对应的模型评分 {participantId: 分}; 非排位 / SUMMARY 取不到 / 对不上 / 缺字段
    时返回 None (调用方用原公式). 只有排位局才发请求
    """
    if not isinstance(detail, dict) or detail.get('queueId') not in MODEL_QUEUES:
        return None

    summary = await getSummary(detail.get('gameId'))
    if not summary or not matchesLcuDetail(summary, detail):
        return None
    return scoreSummary(summary, detail.get('queueId'))
