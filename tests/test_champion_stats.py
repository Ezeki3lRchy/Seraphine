"""Champions view: per-champion / matchup aggregation, extraction from the real fixture, widget"""
import json
import os
import unittest

from app.lol import champion_stats as cs
from app.lol.season_stats import rawLcuGameStats, rawSgpGameStats

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'sgp_match_history.json')
_ids = iter(range(1, 10 ** 6))


def g(champ=1, win=True, opp=None, queue=420, mapId=11, secs=1800, remake=False,
      custom=False, **extra):
    game = {'gameId': next(_ids), 'queueId': queue, 'mapId': mapId, 'win': win,
            'remake': remake, 'custom': custom, 'championId': champ, 'opponentId': opp,
            'durationSecs': secs, 'kills': 6, 'deaths': 3, 'assists': 6, 'kp': 0.5,
            'damage': 30000, 'damageShare': 0.25, 'gold': 15000, 'cs': 240, 'vision': 30,
            'wardsPlaced': 12, 'wardsKilled': 4, 'controlWards': 2, 'doubleKills': 1,
            'tripleKills': 0, 'quadraKills': 0, 'pentaKills': 0, 'laneLead': True,
            'score': 6.0, 'timeStamp': 1}
    game.update(extra)
    return game


class AggregationTest(unittest.TestCase):
    def sample(self):
        return [g(1, True, 10), g(1, False, 10, kills=2, deaths=6), g(1, True, 11),
                g(2, False, 20, queue=440), g(2, True, None, queue=450, mapId=12),
                g(3, True, 30, remake=True), g(4, True, 40, custom=True),
                g(5, True, None, queue=1700, mapId=30)]

    def test_totals_match_champion_sums(self):
        prof = cs.build(self.sample())
        total, champs = prof['total'], prof['champions']
        # remake, custom and Arena are out; champions 3 / 4 / 5 have no counted games
        self.assertEqual([c['championId'] for c in champs], [1, 2])
        self.assertEqual(total['games'], 5)
        self.assertEqual(total['champions'], 2)
        for key in ('games', 'wins', 'losses', 'doubleKills'):
            self.assertEqual(total[key], sum(c[key] for c in champs), key)
        self.assertEqual(total['wins'], 3)
        self.assertAlmostEqual(total['kills'], (6 * 4 + 2) / 5)
        self.assertAlmostEqual(total['kda'], (26 + 30) / (3 * 4 + 6))

    def test_matchups(self):
        champ = cs.build(self.sample())['champions'][0]
        self.assertEqual([(m['opponentId'], m['games']) for m in champ['matchups']],
                         [(10, 2), (11, 1)])
        self.assertEqual(champ['unknownOpponent'], 0)
        aram = cs.build(self.sample())['champions'][1]
        # the ARAM game has no lane opponent: champion row only
        self.assertEqual([m['opponentId'] for m in aram['matchups']], [20])
        self.assertEqual(aram['unknownOpponent'], 1)
        self.assertEqual(cs.opponentIds(cs.buildAll(self.sample()), 1), [10, 11])

    def test_rift_only_fields(self):
        champ = cs.build(self.sample())['champions'][1]  # one Flex game, one ARAM game
        self.assertEqual(champ['games'], 2)
        self.assertEqual((champ['csGames'], champ['goldGames'], champ['visionGames'],
                          champ['wardsPlacedGames']), (1, 1, 1, 1))
        self.assertAlmostEqual(champ['csPerMin'], 240 / 30)
        self.assertEqual(champ['cs'], 240)
        # the Damage column (per minute and team share) is Rift only
        self.assertEqual((champ['damageGames'], champ['damageShareGames']), (1, 1))
        # record / KDA / multi-kills include ARAM
        self.assertEqual(champ['doubleKills'], 2)
        self.assertEqual(champ['multiGames'], 2)
        # no mapId: the queue decides
        self.assertFalse(cs.isRift({'queueId': 450}))
        self.assertTrue(cs.isRift({'queueId': 420}))

    def test_missing_optional_fields(self):
        games = [g(1, score=7.0, laneLead=True, kp=0.6),
                 g(1, score=None, laneLead=None, kp=None, damageShare=None,
                   doubleKills=None, tripleKills=None, quadraKills=None, pentaKills=None),
                 g(1, score=5.0, laneLead=False, kp=0.4, secs=0)]
        row = cs.build(games)['champions'][0]
        self.assertEqual((row['scoreGames'], row['score']), (2, 6.0))
        self.assertEqual((row['laneLeadGames'], row['laneLead']), (2, 0.5))
        self.assertEqual((row['kpGames'], row['kp']), (2, 0.5))
        self.assertEqual(row['damageShareGames'], 2)
        self.assertEqual((row['multiGames'], row['doubleKills']), (2, 2))
        # the zero-length game is not in per-minute figures, numerator nor denominator
        self.assertEqual(row['damageGames'], 2)
        self.assertAlmostEqual(row['damagePerMin'], 60000 / 60)
        self.assertEqual(row['damage'], 30000)

    def test_queue_filter_and_empty(self):
        games = self.sample()
        self.assertEqual(cs.build(games, 420)['total']['games'], 3)
        self.assertEqual(cs.build(games, 440)['total']['games'], 1)
        self.assertIsNone(cs.build([g(remake=True)]))
        self.assertIsNone(cs.build(None))
        self.assertEqual(set(cs.buildAll(games)), set(cs.QUEUE_FILTERS))

    def test_single_game(self):
        prof = cs.build([g(7, False, 8, kills=0, deaths=0, assists=3)])
        row = prof['champions'][0]
        self.assertEqual((row['games'], row['wins'], row['winRate']), (1, 0, 0))
        self.assertEqual(row['kda'], 3)
        self.assertEqual(len(row['matchups']), 1)

    def test_sorting(self):
        rows = cs.build([g(1), g(1, False), g(2), g(2), g(3, False, kills=20)])['champions']
        # games desc, then win rate
        self.assertEqual([r['championId'] for r in rows], [2, 1, 3])
        self.assertEqual([r['championId'] for r in cs.sortRows(rows, 'kda')], [3, 2, 1])
        self.assertEqual([r['championId'] for r in cs.sortRows(rows, 'winRate', False)],
                         [3, 1, 2])
        rows[0]['score'] = None
        self.assertEqual(cs.sortRows(rows, 'score')[-1]['championId'], 2)  # None last
        self.assertEqual(cs.sortRows(rows, 'score', False)[-1]['championId'], 2)

    def test_small_samples_sort_last(self):
        games = [g(1, kills=20)] + [g(2, kills=1)] * 3 + [g(3, kills=3)] * 4 + \
            [g(4, kills=30), g(4, kills=30)]
        rows = cs.build(games)['champions']
        # a 1-game 26.0 KDA never leads: rows with >= 3 games first, the rest after
        self.assertEqual([r['championId'] for r in cs.sortRows(rows, 'kda')], [3, 2, 4, 1])
        self.assertEqual([r['championId'] for r in cs.sortRows(rows, 'kda', False)],
                         [2, 3, 1, 4])
        self.assertEqual([r['championId'] for r in cs.sortRows(rows, 'winRate')][:2], [3, 2])
        # the Games sort is unaffected
        self.assertEqual([r['championId'] for r in cs.sortRows(rows, 'games')], [3, 2, 4, 1])

    def test_lcu_shaped_sample(self):
        lcu = {'gameId': 1, 'queueId': 420, 'mapId': 11, 'gameCreation': 1,
               'gameVersion': '16.23.1', 'gameDuration': 1500, 'gameType': 'MATCHED_GAME',
               'participants': [{'participantId': 4, 'teamId': 100, 'championId': 429,
                                 'stats': {'kills': 5, 'deaths': 2, 'assists': 4, 'win': True,
                                           'totalDamageDealtToChampions': 20000,
                                           'goldEarned': 11000, 'totalMinionsKilled': 180,
                                           'neutralMinionsKilled': 4, 'visionScore': 20,
                                           'doubleKills': 1, 'tripleKills': 0,
                                           'quadraKills': 0, 'pentaKills': 0,
                                           'wardsPlaced': 9, 'wardsKilled': 2,
                                           'visionWardsBoughtInGame': 1}}]}
        r = rawLcuGameStats(lcu)
        self.assertEqual((r['opponentId'], r['laneLead'], r['score']), (None, None, None))
        self.assertEqual((r['doubleKills'], r['wardsPlaced'], r['controlWards'], r['mapId']),
                         (1, 9, 1, 11))
        prof = cs.build([r])
        row = prof['champions'][0]
        self.assertEqual(row['matchups'], [])
        self.assertEqual(row['unknownOpponent'], 1)
        self.assertEqual((row['scoreGames'], row['laneLeadGames'], row['kpGames']), (0, 0, 0))
        self.assertIsNone(row['score'])
        self.assertAlmostEqual(row['csPerMin'], 184 / 25)
        self.assertEqual(prof['opponentGames'], 0)


class FixtureExtractionTest(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, encoding='utf-8') as f:
            self.games = json.load(f)['games']

    def test_ranked_fields(self):
        for game in self.games[:2]:
            ps = game['json']['participants']
            me = next(p for p in ps if p['puuid'] == 'puuid-me')
            opp = next(p for p in ps if p['teamId'] != me['teamId']
                       and p['teamPosition'] == me['teamPosition'])
            r = rawSgpGameStats(game, 'puuid-me')
            self.assertEqual(r['opponentId'], opp['championId'])
            self.assertEqual(r['mapId'], 11)
            for key in ('doubleKills', 'tripleKills', 'quadraKills', 'pentaKills',
                        'wardsPlaced', 'wardsKilled'):
                self.assertEqual(r[key], me[key], key)
            self.assertEqual(r['controlWards'], me['visionWardsBoughtInGame'])
            self.assertEqual(r['laneLead'],
                             bool(me['challenges']['laningPhaseGoldExpAdvantage']))
            self.assertIsNotNone(r['score'])
            self.assertTrue(0 <= r['score'] <= 10)
            self.assertNotIn('json', r)

    def test_score_matches_full_model(self):
        from app.lol import opscore
        game = self.games[0]
        me = next(p for p in game['json']['participants'] if p['puuid'] == 'puuid-me')
        full = opscore.scoreSummary(game['json'])
        self.assertAlmostEqual(rawSgpGameStats(game, 'puuid-me')['score'],
                               full[me['participantId']])
        self.assertEqual(opscore.scoreSummary(game['json'], only=me['participantId']),
                         {me['participantId']: full[me['participantId']]})
        self.assertIsNone(opscore.scoreSummary(game['json'], only=99))

    def test_remake_custom_practice(self):
        remake, custom, practice = (rawSgpGameStats(g, 'puuid-me') for g in self.games[2:5])
        self.assertIsNone(remake['score'])
        # the remake still names its lane opponent; it is left out of every row anyway
        self.assertIsNotNone(remake['opponentId'])
        self.assertIsNone(remake['laneLead'])  # no laning-phase stat in a remake
        self.assertIsNone(custom['score'])
        self.assertIsNone(practice['opponentId'])
        self.assertIsNone(practice['score'])
        prof = cs.build([rawSgpGameStats(g, 'puuid-me') for g in self.games])
        self.assertEqual(prof['total']['games'], 2)
        self.assertEqual(prof['total']['scoreGames'], 2)


class ChampionStatsViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def sample(self):
        games = []
        for champ, n in ((1, 6), (2, 3), (3, 1)):
            for i in range(n):
                games.append(g(champ, i % 2 == 0, 100 + i, queue=420 if i % 3 else 440))
        return games

    def test_states(self):
        from app.components.champion_stats_widget import ChampionStatsView

        v = ChampionStatsView()
        v.resize(1000, 100)
        v.setLoading()
        self.assertEqual((v.state, v.height()), ('loading', v.STATE_H))
        v.grab()
        v.setSample([], 'match')
        self.assertEqual((v.state, v.height()), ('empty', v.STATE_H))
        v.grab()
        v.setSample(self.sample(), 'match', names={1: 'Ahri'})
        self.assertEqual(v.state, 'ready')
        collapsed = v.height()
        self.assertEqual(collapsed, v.layoutHeight(1000))
        self.assertEqual([r[2] for r in v.rows()], ['header', 'total', 'champ', 'champ', 'champ'])
        v.grab()
        v.setEmpty()
        self.assertEqual(v.state, 'empty')

    def test_expand_sort_filter(self):
        from app.components.champion_stats_widget import ChampionStatsView

        v = ChampionStatsView()
        v.resize(1000, 100)
        v.setLoading()
        v.setSample(self.sample(), 'match')
        wanted = []
        v.opponentsWanted.connect(wanted.append)
        before = v.height()
        v.toggleExpanded(1)
        kinds = [r[2] for r in v.rows()]
        # six matchups: the first five, then a "show more" row
        self.assertEqual(kinds.count('match'), v.MATCHUP_LIMIT)
        self.assertEqual(kinds.count('foot'), 1)
        self.assertGreater(v.height(), before)
        v.toggleShowAll(1)
        self.assertEqual([r[2] for r in v.rows()].count('match'), 6)
        v.grab()
        v.toggleExpanded(1)
        v.toggleExpanded(1)
        self.assertEqual(wanted, [1])  # opponents asked for once
        v.toggleExpanded(1)
        self.assertEqual(v.height(), before)

        v.setSort('kda')
        self.assertEqual((v.sortKey, v.sortDesc), ('kda', True))
        v.setSort('kda')
        self.assertFalse(v.sortDesc)
        v.setSort('champion')
        self.assertEqual((v.sortKey, v.sortDesc), ('champion', False))
        v.grab()

        v.setQueue(440)
        self.assertEqual(v.profile()['total']['games'], 4)
        v.setQueue(999)  # no such filter: ignored
        self.assertEqual(v.queueId, 440)
        # a new summoner resets filter / sort / expanded rows
        v.setLoading()
        self.assertEqual((v.queueId, v.sortKey, v.expanded), (None, 'games', set()))

    def test_columns_reflow_and_tips(self):
        from PySide6.QtCore import QPoint
        from app.components.champion_stats_widget import ChampionStatsView

        v = ChampionStatsView()
        v.resize(1000, 100)
        v.setLoading()
        v.setSample(self.sample(), 'match')
        for width in (1300, 1000, 760, 600):
            cols = v.columns(width)
            right = cols[-1][1] + cols[-1][2]
            self.assertLessEqual(right, width - v.MARGIN + 0.01, width)
        # (all columns fit at 1000 with the app's fonts; the test fonts may be wider)
        self.assertIn('multi', [k for k, _, _ in v.columns(1300)])
        narrow = [k for k, _, _ in v.columns(760)]
        self.assertNotIn('multi', narrow)
        self.assertIn('kda', narrow)
        # every cell of every row has a tooltip text (or none for the plain foot cells)
        v.toggleExpanded(1)
        for i, (top, h, kind, _, _) in enumerate(v.rows()):
            for key, x, w in v.columns(v.width()):
                hit = v.hitTest(QPoint(int(x + w / 2), int(top + h / 2)))
                self.assertEqual(hit, (i, key))
                tip = v.tipText(hit)
                if kind != 'foot':
                    self.assertTrue(tip, (kind, key))

    def manyChampions(self, n=40):
        games = []
        for champ in range(1, n + 1):
            for i in range(4):
                games.append(g(champ, i % 2 == 0, 1000 + champ * 10 + i))
        return games

    def test_window_rendering_is_bounded(self):
        from PySide6.QtCore import QRect
        from app.components import champion_stats_widget as w

        v = w.ChampionStatsView()
        v.resize(1000, 100)
        v.setLoading()
        v.setSample(self.manyChampions(), 'match')
        for cid in range(1, 41):
            v.toggleExpanded(cid)
        v.resize(1000, v.layoutHeight(1000))
        self.assertGreater(v.height(), 8000)
        # a 600 px viewport in the middle: only it and the margins are rendered
        v.ensureWindow(QRect(0, 4000, 1000, 600))
        self.assertEqual((v._cacheTop, v._cacheBottom),
                         (4000 - w.WINDOW_MARGIN, 4600 + w.WINDOW_MARGIN))
        cache = v._cache
        # inside the window: blit only; hover / chip hover never re-render
        v.ensureWindow(QRect(0, 4100, 1000, 200))
        v._chipHover = None
        v._hover = (5, 'kda')
        v.ensureWindow(QRect(0, 4100, 1000, 200))
        self.assertIs(v._cache, cache)
        # icons for rows outside the window do not invalidate it; inside they do
        outside = next(cid for cid in range(1, 41) if cid not in v._cacheIds)
        inside = next(iter(v._cacheIds))
        icon = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'app', 'resource',
                            'images', 'logo.png')
        version = v._version
        v.setChampionIcons({outside: (icon, '')})
        self.assertEqual(v._version, version)
        v.setChampionIcons({inside: (icon, '')})
        self.assertEqual(v._version, version + 1)
        # scrolling past the window renders a new one there
        v.ensureWindow(QRect(0, 7000, 1000, 600))
        self.assertEqual(v._cacheTop, 7000 - w.WINDOW_MARGIN)
        self.assertLess(v._cache.height(), 600 + 2 * w.WINDOW_MARGIN + 2)

    def test_names_arriving_resort_by_name(self):
        from app.components.champion_stats_widget import ChampionStatsView

        v = ChampionStatsView()
        v.resize(1000, 100)
        v.setLoading()
        v.setSample(self.sample(), 'match', names={1: 'Zed', 2: 'Ahri', 3: 'Lux'})
        v.setSort('champion')
        self.assertEqual([r[3]['championId'] for r in v.rows() if r[2] == 'champ'], [2, 3, 1])
        icon = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'app', 'resource',
                            'images', 'logo.png')
        v.setChampionIcons({1: (icon, 'Aatrox')})
        self.assertEqual([r[3]['championId'] for r in v.rows() if r[2] == 'champ'], [1, 2, 3])

    def test_empty_hints(self):
        from app.components.champion_stats_widget import ChampionStatsView

        v = ChampionStatsView()
        v.setSample([], 'match')
        none = v.emptyTexts()
        v.setSample([g(queue=1700, mapId=30), g(remake=True)], 'match')
        self.assertEqual(v.state, 'empty')
        self.assertNotEqual(v.emptyTexts(), none)
        self.assertTrue(all(v.emptyTexts()))

    def test_hidden_columns_note(self):
        from app.components.champion_stats_widget import ChampionStatsView

        v = ChampionStatsView()
        v.resize(700, 100)
        v.setLoading()
        v.setSample(self.sample(), 'match')
        hidden = v.hiddenColumns()
        self.assertIn('multi', hidden)
        for key in hidden:
            self.assertIn(v.columnTitle(key), v.hiddenTip())
        v.resize(1600, 100)
        self.assertEqual(v.hiddenColumns(), [])

    def test_lcu_degrades(self):
        from app.components.champion_stats_widget import ChampionStatsView

        games = [g(1, opp=None, score=None, laneLead=None, kp=None, damageShare=None)
                 for _ in range(4)]
        v = ChampionStatsView()
        v.resize(1000, 100)
        v.setLoading()
        v.setSample(games, 'lcu')
        keys = [k for k, _, _ in v.columns(1000)]
        for missing in ('score', 'laneLead', 'kp'):
            self.assertNotIn(missing, keys)
        self.assertTrue(v.note())
        wanted = []
        v.opponentsWanted.connect(wanted.append)
        v.toggleExpanded(1)  # nothing to expand: no matchup rows
        self.assertEqual([r[2] for r in v.rows()].count('match'), 0)
        v.grab()


if __name__ == '__main__':
    unittest.main()
