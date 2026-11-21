"""MVP / SVP game scoring and the per-player summaries of the game-info page: the hand-tuned
weight formula, OP Score scaling, the ``opscore`` model hook and the Insights thresholds.
Pure functions over parsed games."""
from . import opscore as opscoreModel


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
