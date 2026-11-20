"""
Career page "Champions" view: this season's statistics per champion and per lane matchup,
pure aggregation (no Qt).

Input is the season sample from season_stats.fetchSeasonGames (the same one Play style
uses; nothing here sends requests). Rules:

- Counted games: play_style.countedGames (no remakes, no custom games, optional queue
  filter shared with Play style) and no Arena: its "win" is a top-half placement, kill
  participation / damage share are over a two-player team and there are no lanes, so mixed
  into a champion's row it would distort every column.
- Other non-Summoner's-Rift modes (ARAM ...) count toward record, KDA, kill participation,
  score (none outside ranked) and multi-kills, but not toward the Damage column (per minute
  and team share), CS, gold, vision or wards, which do not compare across maps. Rift =
  mapId 11 (or, without a mapId, a queue that is not ARAM).
- Sorting by anything but games puts rows under MIN_GAMES games last (see sortRows).
- Every average uses only the games that have that field: numerator and denominator come
  from the same games, and the row carries how many games that was (xxxGames).
- Matchups: games grouped by the lane opponent's champion (season_stats: the one enemy with
  the same teamPosition); games without a known opponent are only in the champion row.
- laneLead is the share of games ahead in both gold and XP over the lane opponent at the end
  of the laning phase (Riot's challenges.laningPhaseGoldExpAdvantage), not a laning score.
"""
from app.lol import play_style

QUEUE_FILTERS = play_style.QUEUE_FILTERS
ARENA_QUEUES = play_style.ARENA_QUEUES
RIFT_MAP = 11
NON_RIFT_QUEUES = (450, 720, 2400)  # ARAM / ARAM Clash / ARAM: Mayhem, when mapId is missing
MIN_GAMES = play_style.MIN_BUCKET_GAMES
MULTI_KILLS = ('doubleKills', 'tripleKills', 'quadraKills', 'pentaKills')

# sortable columns -> row value (None sorts last either way)
SORT_KEYS = {
    'games': lambda r: r['games'],
    'winRate': lambda r: r['winRate'],
    'kda': lambda r: r['kda'],
    'kp': lambda r: r['kp'],
    'score': lambda r: r['score'],
    'laneLead': lambda r: r['laneLead'],
    'damage': lambda r: r['damagePerMin'],
    'vision': lambda r: r['vision'],
    'cs': lambda r: r['csPerMin'],
    'gold': lambda r: r['goldPerMin'],
    # the best multi-kill first: pentas, then quadras ...
    'multi': lambda r: None if r['doubleKills'] is None else
    tuple(r[k] for k in reversed(MULTI_KILLS)),
}


def countedGames(games, queueId=None):
    """Play style's counted games (no remakes / customs, queue filter) without Arena"""
    return [g for g in play_style.countedGames(games, queueId)
            if g.get('queueId') not in ARENA_QUEUES]


def isRift(game):
    if game.get('mapId') is not None:
        return game['mapId'] == RIFT_MAP
    return game.get('queueId') not in NON_RIFT_QUEUES + ARENA_QUEUES


def _mean(games, key):
    have = [g[key] for g in games if g.get(key) is not None]
    return (sum(have) / len(have) if have else None), len(have)


def _perMinute(games, key):
    """(total / minutes, total / games, games) over Rift games that have key and a duration"""
    have = [g for g in games if g.get(key) is not None and (g.get('durationSecs') or 0) > 0]
    minutes = sum(g['durationSecs'] for g in have) / 60
    if not have or minutes <= 0:
        return None, None, 0
    total = sum(g[key] for g in have)
    return total / minutes, total / len(have), len(have)


def aggregate(games):
    """one row over counted games (non-empty); see the module notes for each field's games"""
    n = len(games)
    wins = sum(1 for g in games if g['win'])
    k = sum(g['kills'] for g in games)
    d = sum(g['deaths'] for g in games)
    a = sum(g['assists'] for g in games)
    rift = [g for g in games if isRift(g)]
    row = {
        'games': n, 'wins': wins, 'losses': n - wins, 'winRate': wins / n,
        'kills': k / n, 'deaths': d / n, 'assists': a / n,
        'kda': (k + a) / max(d, 1),
    }
    row['kp'], row['kpGames'] = _mean(games, 'kp')
    row['score'], row['scoreGames'] = _mean(games, 'score')
    # Rift only like damage / min: the Damage column then means one set of games
    row['damageShare'], row['damageShareGames'] = _mean(rift, 'damageShare')
    row['vision'], row['visionGames'] = _mean(rift, 'vision')
    for key in ('wardsPlaced', 'wardsKilled', 'controlWards'):
        row[key], row[key + 'Games'] = _mean(rift, key)
    for key, name in (('damage', 'damage'), ('cs', 'cs'), ('gold', 'gold')):
        row[name + 'PerMin'], row[name], row[name + 'Games'] = _perMinute(rift, key)
    multi = [g for g in games if g.get('doubleKills') is not None]
    for key in MULTI_KILLS:
        row[key] = sum(g.get(key) or 0 for g in multi) if multi else None
    row['multiGames'] = len(multi)
    leads = [g['laneLead'] for g in games if g.get('laneLead') is not None]
    row['laneLead'] = sum(1 for x in leads if x) / len(leads) if leads else None
    row['laneLeadGames'] = len(leads)
    return row


def sortRows(rows, column='games', descending=True):
    """
    rows sorted by one column; ties (and the default 'games') fall back to games desc, win
    rate desc, then the original order (most recently played first). None values sort last.
    Any column but 'games': rows with fewer than MIN_GAMES games come after all the others
    (sorted the same way among themselves), so a single 9.50 KDA game never leads
    """
    value = SORT_KEYS[column]
    order = {id(r): i for i, r in enumerate(rows)}

    def tie(r):
        return (-r['games'], -r['winRate'], order[id(r)])

    def byValue(group):
        have = [r for r in group if value(r) is not None]
        missing = sorted((r for r in group if value(r) is None), key=tie)
        have.sort(key=tie)
        have.sort(key=value, reverse=descending)  # stable: ties keep the fallback order
        return have + missing

    if column == 'games':
        return byValue(rows)
    return byValue([r for r in rows if r['games'] >= MIN_GAMES]) + \
        byValue([r for r in rows if r['games'] < MIN_GAMES])


def _group(games, key):
    """{value: [games]} in order of first appearance (games are newest first)"""
    groups = {}
    for g in games:
        if g.get(key) is not None:
            groups.setdefault(g[key], []).append(g)
    return groups


def build(games, queueId=None):
    """
    every row for one queue filter; None when the filter leaves no games.
    @return: {'queueId', 'total': row (+ 'champions' count), 'champions': [row +
             'championId', 'matchups': [row + 'opponentId'], 'unknownOpponent': games
             without a known lane opponent]}, 'opponentGames': games with a known opponent}
    champions and matchups sorted by games desc, then win rate
    """
    counted = countedGames(games, queueId)
    if not counted:
        return None
    total = aggregate(counted)
    champions = []
    for cid, champGames in _group(counted, 'championId').items():
        row = aggregate(champGames)
        row['championId'] = cid
        matchups = []
        for oid, vsGames in _group(champGames, 'opponentId').items():
            vs = aggregate(vsGames)
            vs['opponentId'] = oid
            matchups.append(vs)
        row['matchups'] = sortRows(matchups)
        row['unknownOpponent'] = row['games'] - sum(m['games'] for m in matchups)
        champions.append(row)
    total['champions'] = len(champions)
    return {
        'queueId': queueId,
        'total': total,
        'champions': sortRows(champions),
        'opponentGames': sum(1 for g in counted if g.get('opponentId') is not None),
    }


def buildAll(games):
    """{queueId: build(games, queueId)} for every Play style queue filter"""
    return {q: build(games, q) for q in QUEUE_FILTERS}


def championIds(profiles):
    """champions of the rows (every filter), most played first, for the icon task"""
    ids = []
    for prof in (profiles or {}).values():
        for row in (prof or {}).get('champions') or []:
            if row['championId'] not in ids:
                ids.append(row['championId'])
    return ids


def opponentIds(profiles, championId):
    """lane opponents of one champion over every filter (icons loaded when it is expanded)"""
    ids = []
    for prof in (profiles or {}).values():
        for row in (prof or {}).get('champions') or []:
            if row['championId'] == championId:
                ids += [m['opponentId'] for m in row['matchups'] if m['opponentId'] not in ids]
    return ids
