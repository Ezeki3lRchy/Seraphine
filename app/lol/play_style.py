"""
Career page "Play style" view: season play-style statistics, pure aggregation (no Qt).

Input is the season sample from season_stats.fetchSeasonGames (raw per-game stats, newest
first); nothing here sends requests except championIcons. Remakes and custom games
(practice tool, custom lobbies) are left out of every section.

- Positions: SGP teamPosition; LCU only for draft queues (400 / 420 / 440), where the
  participantId order is Top / Jungle / Mid / Bot / Support. Other LCU games have no position.
- Sides / per-minute rates / positions skip Arena (no blue / red sides, no lanes).
- Activity uses the local time of the game creation timestamp; toLocal is injectable so
  tests do not depend on the machine's timezone.
"""
from collections import Counter
from datetime import datetime

POSITIONS = ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')
CLASSES = ('marksman', 'mage', 'fighter', 'tank', 'assassin', 'support')
QUEUE_FILTERS = (None, 420, 440)
ARENA_QUEUES = (1700, 1710)
BLUE, RED = 100, 200
# buckets (weekday / hour / side ...) with fewer games are drawn muted
MIN_BUCKET_GAMES = 3


def countedGames(games, queueId=None):
    """games without remakes / custom games, optionally only one queue"""
    return [g for g in games or [] if g and not g.get('remake') and not g.get('custom')
            and (queueId is None or g.get('queueId') == queueId)]


def _record(games):
    n = len(games)
    wins = sum(1 for g in games if g['win'])
    return {'games': n, 'wins': wins, 'losses': n - wins,
            'winRate': wins / n if n else None}


def summary(games):
    rec = _record(games)
    n = rec['games']
    k = sum(g['kills'] for g in games)
    d = sum(g['deaths'] for g in games)
    a = sum(g['assists'] for g in games)
    secs = [g['durationSecs'] for g in games if g.get('durationSecs')]
    return {
        **rec,
        'kills': k / n if n else 0, 'deaths': d / n if n else 0, 'assists': a / n if n else 0,
        'kda': (k + a) / max(d, 1),
        'totalSecs': sum(secs),
        'avgSecs': sum(secs) / len(secs) if secs else None,
        'champions': len({g.get('championId') for g in games if g.get('championId')}),
    }


def positionShare(games):
    """{'known': games with a position, 'rows': [{'position', games, wins, winRate, share}]}"""
    known = [g for g in games if g.get('position') in POSITIONS
             and g.get('queueId') not in ARENA_QUEUES]
    rows = []
    for pos in POSITIONS:
        rec = _record([g for g in known if g['position'] == pos])
        rec['position'] = pos
        rec['share'] = rec['games'] / len(known) if known else 0
        rows.append(rec)
    return {'known': len(known), 'rows': rows}


def championFocus(games, top=5):
    """main / top-3 / top-5 share of games, plus the top champions' records"""
    picked = [g for g in games if g.get('championId')]
    n = len(picked)
    counts = Counter(g['championId'] for g in picked)
    # most played first; ties: the champion played most recently first (games are newest first)
    order = {}
    for i, g in enumerate(picked):
        order.setdefault(g['championId'], i)
    ranked = sorted(counts, key=lambda c: (-counts[c], order[c]))

    def share(k):
        return sum(counts[c] for c in ranked[:k]) / n if n else 0

    champions = []
    for cid in ranked[:top]:
        rec = _record([g for g in picked if g['championId'] == cid])
        rec['championId'] = cid
        rec['share'] = rec['games'] / n
        champions.append(rec)
    return {'games': n, 'distinct': len(counts), 'main': share(1), 'top3': share(3),
            'top5': share(5), 'champions': champions}


def sideStats(games):
    sided = [g for g in games if g.get('teamId') in (BLUE, RED)
             and g.get('queueId') not in ARENA_QUEUES]
    blue = _record([g for g in sided if g['teamId'] == BLUE])
    red = _record([g for g in sided if g['teamId'] == RED])
    n = len(sided)
    blue['share'] = blue['games'] / n if n else 0
    red['share'] = red['games'] / n if n else 0
    diff = None
    if blue['winRate'] is not None and red['winRate'] is not None:
        diff = blue['winRate'] - red['winRate']
    return {'games': n, 'blue': blue, 'red': red, 'diff': diff}


def _localTime(ms):
    return datetime.fromtimestamp(ms / 1000)


def activity(games, toLocal=None):
    """
    toLocal(ms) -> datetime (default: local time). weekday 0 = Monday.
    @return: {'weekdays': [7 records], 'hours': [24 records]}
    """
    toLocal = toLocal or _localTime
    days = [[] for _ in range(7)]
    hours = [[] for _ in range(24)]
    for g in games:
        if not g.get('timeStamp'):
            continue
        t = toLocal(g['timeStamp'])
        days[t.weekday()].append(g)
        hours[t.hour].append(g)
    return {'weekdays': [_record(d) for d in days], 'hours': [_record(h) for h in hours]}


def rates(games):
    """per-minute rates over the games that have the field and a non-zero duration"""
    timed = [g for g in games if (g.get('durationSecs') or 0) > 0
             and g.get('queueId') not in ARENA_QUEUES]
    result = {}
    for key in ('damage', 'cs', 'gold', 'vision'):
        have = [g for g in timed if g.get(key) is not None]
        minutes = sum(g['durationSecs'] for g in have) / 60
        total = sum(g[key] for g in have)
        result[key] = total / minutes if minutes > 0 else None
        # per-game average over the same games as the rate (not the whole sample)
        result[key + 'PerGame'] = total / len(have) if have and minutes > 0 else None
        result[key + 'Games'] = len(have)
    kps = [g['kp'] for g in games if g.get('kp') is not None]
    result['kp'] = sum(kps) / len(kps) if kps else None
    result['kpGames'] = len(kps)
    shares = [g['damageShare'] for g in games if g.get('damageShare') is not None]
    result['damageShare'] = sum(shares) / len(shares) if shares else None
    result['damageShareGames'] = len(shares)
    return result


def classShare(games, roles):
    """roles: {championId: ['mage', 'support']}; each game counts its champion's first class"""
    if not roles:
        return None
    counts = Counter()
    wins = Counter()
    for g in games:
        champRoles = roles.get(g.get('championId')) or []
        if champRoles and champRoles[0] in CLASSES:
            counts[champRoles[0]] += 1
            wins[champRoles[0]] += 1 if g['win'] else 0
    n = sum(counts.values())
    if not n:
        return None
    rows = [{'class': c, 'games': counts[c], 'wins': wins[c], 'losses': counts[c] - wins[c],
             'winRate': wins[c] / counts[c], 'share': counts[c] / n}
            for c in sorted(counts, key=lambda c: (-counts[c], CLASSES.index(c)))]
    return {'games': n, 'rows': rows}


def recentScores(recentGames, queueId=None):
    """
    recent games already on the page (parseGameData + attachGameScores): score / MVP / SVP.
    Remakes and custom games (queueId 0 there) are left out like everywhere else.
    'scores' is newest first.
    """
    games = [g for g in recentGames or [] if g and not g.get('remake') and not g.get('custom')
             and g.get('queueId') != 0 and (queueId is None or g.get('queueId') == queueId)]
    scored = [g for g in games if g.get('opScore') is not None]
    if not scored:
        return None
    scores = [g['opScore'] for g in scored]
    return {'games': len(scores), 'avg': sum(scores) / len(scores),
            'best': max(scores), 'worst': min(scores), 'scores': scores,
            'mvp': sum(1 for g in scored if g.get('badge') == 'MVP'),
            'svp': sum(1 for g in scored if g.get('badge') == 'SVP')}


HEADLINE_SHARE = 0.5
HEADLINE_MIN_GAMES = 5
HEADLINE_CHAMPION_SHARE = 0.2
HEADLINE_CHAMPION_GAMES = 3


def headline(profile):
    """
    The clear-cut parts of a one-line summary, e.g. Bot · Marksman · Kalista 32%:
    - position: >= 50% of the games with a known position (at least 5 such games)
    - class: >= 50% of the games with a known class (at least 5)
    - champion: the most played champion when it has >= 20% of the games (at least 3)
    @return: {'position', 'class', 'championId', 'championShare'} (missing parts None),
             or None when no part is clear-cut
    """
    if not profile:
        return None
    result = {'position': None, 'class': None, 'championId': None, 'championShare': None}

    pos = profile['positions']
    if pos['known'] >= HEADLINE_MIN_GAMES:
        top = max(pos['rows'], key=lambda r: r['games'])
        if top['share'] >= HEADLINE_SHARE:
            result['position'] = top['position']

    classes = profile.get('classes')
    if (classes and classes['games'] >= HEADLINE_MIN_GAMES
            and classes['rows'][0]['share'] >= HEADLINE_SHARE):
        result['class'] = classes['rows'][0]['class']

    champs = profile['focus']['champions']
    if (champs and champs[0]['games'] >= HEADLINE_CHAMPION_GAMES
            and champs[0]['share'] >= HEADLINE_CHAMPION_SHARE):
        result['championId'] = champs[0]['championId']
        result['championShare'] = champs[0]['share']

    return result if any(v is not None for v in result.values()) else None


def buildProfile(games, queueId=None, roles=None, recentGames=None, toLocal=None):
    """every section for one queue filter; None when the filter leaves no games"""
    counted = countedGames(games, queueId)
    if not counted:
        return None
    return {
        'queueId': queueId,
        'summary': summary(counted),
        'positions': positionShare(counted),
        'focus': championFocus(counted),
        'sides': sideStats(counted),
        'activity': activity(counted, toLocal),
        'rates': rates(counted),
        'classes': classShare(counted, roles),
        'recent': recentScores(recentGames, queueId),
    }


def topChampionIds(games, top=5):
    """champions whose icons any queue filter's focus section may show"""
    ids = []
    for queueId in QUEUE_FILTERS:
        for c in championFocus(countedGames(games, queueId), top)['champions']:
            if c['championId'] not in ids:
                ids.append(c['championId'])
    return ids


async def championIcons(championIds):
    """{championId: (icon path, name)} via the connector's on-disk icon cache"""
    from app.lol.connector import connector

    result = {}
    for cid in championIds:
        try:
            path = await connector.getChampionIcon(cid)
        except Exception:
            continue
        name = (getattr(connector.manager, 'champs', None) or {}).get(cid, "")
        result[cid] = (path, name)
    return result
