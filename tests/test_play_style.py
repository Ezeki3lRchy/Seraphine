import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

from app.lol import play_style
from app.lol.season_stats import rawLcuGameStats, rawSgpGameStats

UTC = timezone.utc


def toUtc(ms):
    return datetime.fromtimestamp(ms / 1000, UTC)


def ms(dt):
    return int(dt.timestamp() * 1000)


# Monday 2026-11-16 00:00 UTC
MONDAY = datetime(2026, 11, 16, tzinfo=UTC)


def g(win=True, champ=1, team=100, pos='TOP', queue=420, secs=1800, remake=False,
      custom=False, when=None, kp=None, **extra):
    game = {'gameId': id(extra) + champ, 'queueId': queue, 'win': win, 'remake': remake,
            'custom': custom, 'championId': champ, 'teamId': team, 'position': pos,
            'durationSecs': secs, 'kills': 5, 'deaths': 2, 'assists': 5,
            'timeStamp': ms(when or MONDAY), 'kp': kp, 'damage': 30000, 'gold': 15000,
            'cs': 270, 'vision': 30, 'damageShare': None}
    game.update(extra)
    return game


class PlayStyleTest(unittest.TestCase):
    def test_empty_sample(self):
        self.assertIsNone(play_style.buildProfile([]))
        self.assertIsNone(play_style.buildProfile(None))
        self.assertIsNone(play_style.buildProfile([g(remake=True), g(custom=True)]))

    def test_remakes_and_customs_excluded(self):
        games = [g(win=True), g(win=False), g(remake=True), g(custom=True, win=True)]
        s = play_style.buildProfile(games)['summary']
        self.assertEqual((s['games'], s['wins'], s['losses']), (2, 1, 1))
        self.assertEqual(s['winRate'], 0.5)
        self.assertEqual(s['totalSecs'], 3600)
        self.assertEqual(s['avgSecs'], 1800)

    def test_focus(self):
        games = [g(champ=1)] * 5 + [g(champ=2)] * 3 + [g(champ=3)] * 1 + [g(champ=4)] \
            + [g(champ=5)] * 2 + [g(champ=6, win=False)] * 8
        f = play_style.championFocus(games)
        self.assertEqual(f['games'], 20)
        self.assertEqual(f['distinct'], 6)
        self.assertEqual([c['championId'] for c in f['champions']], [6, 1, 2, 5, 3])
        self.assertAlmostEqual(f['main'], 8 / 20)
        self.assertAlmostEqual(f['top3'], 16 / 20)
        self.assertAlmostEqual(f['top5'], 19 / 20)
        self.assertEqual(f['champions'][0]['winRate'], 0)

    def test_focus_tie_prefers_recent(self):
        f = play_style.championFocus([g(champ=9), g(champ=7), g(champ=7), g(champ=9)])
        self.assertEqual([c['championId'] for c in f['champions']], [9, 7])

    def test_sides(self):
        games = [g(team=100, win=True)] * 3 + [g(team=100, win=False)] \
            + [g(team=200, win=False)] * 2 + [g(team=200, win=True, queue=1700)]
        s = play_style.sideStats(games)
        self.assertEqual(s['games'], 6)  # arena has no sides
        self.assertEqual(s['blue']['games'], 4)
        self.assertAlmostEqual(s['blue']['share'], 4 / 6)
        self.assertAlmostEqual(s['blue']['winRate'], 0.75)
        self.assertEqual(s['red']['winRate'], 0)
        self.assertAlmostEqual(s['diff'], 0.75)
        self.assertIsNone(play_style.sideStats([g(team=100)])['diff'])

    def test_activity_buckets(self):
        games = [g(when=MONDAY + timedelta(hours=23, minutes=59)),  # Mon 23h
                 g(when=MONDAY + timedelta(days=1)),  # Tue 0h
                 g(when=MONDAY + timedelta(days=6, hours=13), win=False)]  # Sun 13h
        a = play_style.activity(games, toUtc)
        self.assertEqual([d['games'] for d in a['weekdays']], [1, 1, 0, 0, 0, 0, 1])
        self.assertEqual(a['hours'][23]['games'], 1)
        self.assertEqual(a['hours'][0]['games'], 1)
        self.assertEqual(a['hours'][13]['winRate'], 0)
        self.assertIsNone(a['hours'][5]['winRate'])
        # a fixed +8 offset moves the Monday 23:59 game to Tuesday 07:59
        plus8 = timezone(timedelta(hours=8))
        b = play_style.activity(games[:1], lambda t: datetime.fromtimestamp(t / 1000, plus8))
        self.assertEqual(b['weekdays'][1]['games'], 1)
        self.assertEqual(b['hours'][7]['games'], 1)

    def test_rates(self):
        games = [g(secs=1200, damage=24000, cs=200, gold=12000, vision=20),
                 g(secs=0, damage=99999), g(secs=600, damage=None, cs=100, gold=6000,
                                            vision=10, kp=0.5)]
        r = play_style.rates(games)
        self.assertAlmostEqual(r['damage'], 24000 / 20)
        self.assertEqual(r['damageGames'], 1)
        self.assertAlmostEqual(r['cs'], 300 / 30)
        self.assertAlmostEqual(r['gold'], 18000 / 30)
        self.assertAlmostEqual(r['vision'], 1.0)
        self.assertEqual((r['kp'], r['kpGames']), (0.5, 1))
        self.assertIsNone(r['damageShare'])
        none = play_style.rates([g(secs=0)])
        self.assertIsNone(none['damage'])
        self.assertIsNone(none['cs'])

    def test_per_game_uses_rate_games(self):
        # 1 SR game (30 min, 36000 damage) + 3 Arena games: per game must be 36000, not 18000
        games = [g(secs=1800, damage=36000)] + [g(queue=1700, secs=900, damage=20000)] * 3
        r = play_style.rates(games)
        self.assertAlmostEqual(r['damage'], 1200)
        self.assertAlmostEqual(r['damagePerGame'], 36000)
        self.assertEqual(r['damageGames'], 1)
        # a game missing the field does not dilute the per-game figure either
        r = play_style.rates([g(secs=1800, vision=30), g(secs=1800, vision=None)])
        self.assertAlmostEqual(r['visionPerGame'], 30)
        self.assertIsNone(play_style.rates([g(secs=0)])['damagePerGame'])

    def test_headline(self):
        games = [g(champ=1, pos='BOTTOM')] * 6 + [g(champ=2, pos='BOTTOM')] * 2 \
            + [g(champ=3, pos='MIDDLE')] * 2
        roles = {1: ['marksman'], 2: ['marksman'], 3: ['mage']}
        h = play_style.headline(play_style.buildProfile(games, roles=roles))
        self.assertEqual((h['position'], h['class'], h['championId']), ('BOTTOM', 'marksman', 1))
        self.assertAlmostEqual(h['championShare'], 0.6)
        # no clear position / class, spread-out pool: nothing is clear-cut -> None
        spread = [g(champ=c, pos=p) for c, p in zip(range(1, 11), play_style.POSITIONS * 2)]
        self.assertIsNone(play_style.headline(play_style.buildProfile(spread, roles={})))
        # parts are independent: position only
        h = play_style.headline(play_style.buildProfile(
            [g(champ=c, pos='TOP') for c in range(1, 11)]))
        self.assertEqual((h['position'], h['class'], h['championId']), ('TOP', None, None))
        self.assertIsNone(play_style.headline(None))

    def test_positions(self):
        games = [g(pos='TOP')] * 2 + [g(pos='UTILITY', win=False)] + [g(pos=None)] \
            + [g(pos='MIDDLE', queue=1700)]
        p = play_style.positionShare(games)
        self.assertEqual(p['known'], 3)
        rows = {r['position']: r for r in p['rows']}
        self.assertAlmostEqual(rows['TOP']['share'], 2 / 3)
        self.assertEqual(rows['UTILITY']['winRate'], 0)
        self.assertEqual(rows['JUNGLE']['games'], 0)

    def test_queue_filter(self):
        games = [g(queue=420)] * 3 + [g(queue=440, win=False)] * 2 + [g(queue=450)]
        self.assertEqual(play_style.buildProfile(games)['summary']['games'], 6)
        self.assertEqual(play_style.buildProfile(games, 420)['summary']['games'], 3)
        flex = play_style.buildProfile(games, 440)['summary']
        self.assertEqual((flex['games'], flex['winRate']), (2, 0))
        self.assertIsNone(play_style.buildProfile([g(queue=450)], 420))

    def test_classes(self):
        roles = {1: ['marksman'], 2: ['mage', 'support'], 3: []}
        c = play_style.classShare([g(champ=1), g(champ=1, win=False), g(champ=2), g(champ=3)],
                                  roles)
        self.assertEqual(c['games'], 3)
        self.assertEqual(c['rows'][0]['class'], 'marksman')
        self.assertAlmostEqual(c['rows'][0]['share'], 2 / 3)
        self.assertIsNone(play_style.classShare([g()], {}))

    def test_recent_scores(self):
        recent = [{'opScore': 8.0, 'badge': 'MVP', 'queueId': 420},
                  {'opScore': 4.0, 'badge': 'SVP', 'queueId': 440},
                  {'opScore': None, 'badge': None, 'queueId': 420, 'remake': True}]
        r = play_style.recentScores(recent)
        self.assertEqual((r['games'], r['avg'], r['mvp'], r['svp']), (2, 6.0, 1, 1))
        self.assertEqual(play_style.recentScores(recent, 440)['avg'], 4.0)
        self.assertEqual((r['best'], r['worst'], r['scores']), (8.0, 4.0, [8.0, 4.0]))
        self.assertIsNone(play_style.recentScores([]))
        # custom games are left out like in every other section
        custom = recent + [{'opScore': 10.0, 'badge': 'MVP', 'queueId': 0}]
        self.assertEqual(play_style.recentScores(custom)['games'], 2)


class RawFieldsTest(unittest.TestCase):
    def lcu(self, queue=420, pid=4):
        return {'gameId': 1, 'queueId': queue, 'gameCreation': 1, 'gameVersion': '16.23.1',
                'gameDuration': 1600, 'gameType': 'MATCHED_GAME',
                'participants': [{'participantId': pid, 'teamId': 100, 'championId': 429,
                                  'timeline': {'lane': 'TOP', 'role': 'DUO'},
                                  'stats': {'kills': 1, 'deaths': 2, 'assists': 3, 'win': True,
                                            'gameEndedInEarlySurrender': False,
                                            'totalDamageDealtToChampions': 40000,
                                            'goldEarned': 18000, 'totalMinionsKilled': 200,
                                            'neutralMinionsKilled': 30, 'visionScore': 36}}]}

    def test_lcu(self):
        r = rawLcuGameStats(self.lcu())
        self.assertEqual((r['championId'], r['teamId'], r['position']), (429, 100, 'BOTTOM'))
        self.assertEqual((r['damage'], r['gold'], r['cs'], r['vision']), (40000, 18000, 230, 36))
        self.assertFalse(r['custom'])
        red = self.lcu(pid=10)
        red['participants'][0]['teamId'] = 200
        self.assertEqual(rawLcuGameStats(red)['position'], 'UTILITY')
        # legacy lane / role are ignored; no position outside draft queues
        self.assertIsNone(rawLcuGameStats(self.lcu(queue=450))['position'])

    def test_lcu_position_guards(self):
        # participantId must agree with the side: 1-5 blue (100), 6-10 red (200)
        game = self.lcu(pid=2)
        game['participants'][0]['teamId'] = 200
        self.assertIsNone(rawLcuGameStats(game)['position'])
        game = self.lcu(pid=7)
        game['participants'][0]['teamId'] = 200
        self.assertEqual(rawLcuGameStats(game)['position'], 'JUNGLE')
        # Summoner's Rift only when the payload names the map
        game = self.lcu(pid=2)
        game['mapId'] = 12
        self.assertIsNone(rawLcuGameStats(game)['position'])
        game['mapId'] = 11
        self.assertEqual(rawLcuGameStats(game)['position'], 'JUNGLE')

    def test_sgp(self):
        def p(puuid, team, dmg, pos):
            return {'puuid': puuid, 'teamId': team, 'championId': 1, 'kills': 2, 'deaths': 1,
                    'assists': 2, 'win': team == 100, 'totalDamageDealtToChampions': dmg,
                    'goldEarned': 10000, 'totalMinionsKilled': 100, 'neutralMinionsKilled': 0,
                    'visionScore': 10, 'teamPosition': pos}
        game = {'json': {'gameId': 2, 'queueId': 420, 'gameCreation': 1, 'gameDuration': 1500,
                         'gameVersion': '16.23', 'gameType': 'MATCHED_GAME',
                         'participants': [p('me', 100, 30000, 'JUNGLE'), p('a', 100, 10000, 'TOP'),
                                          p('b', 200, 50000, 'TOP')]}}
        r = rawSgpGameStats(game, 'me')
        self.assertEqual(r['position'], 'JUNGLE')
        self.assertAlmostEqual(r['damageShare'], 0.75)
        self.assertEqual(r['cs'], 100)


class PlayStyleViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_states_and_filter(self):
        from app.components.play_style_widget import PlayStyleView

        v = PlayStyleView()
        v.resize(1000, 100)
        v.setLoading([])
        self.assertEqual((v.state, v.height()), ('loading', PlayStyleView.STATE_H))
        v.setSample([], 'lcu')
        self.assertEqual(v.state, 'empty')

        games = [g(queue=420, champ=i % 4 + 1, when=MONDAY + timedelta(hours=i))
                 for i in range(12)] + [g(queue=450, team=200, win=False)] * 3
        v.setSample(games, 'lcu', roles={1: ['mage']})
        self.assertEqual(v.state, 'ready')
        self.assertGreater(v.layoutHeight(1000), PlayStyleView.STATE_H)
        # narrower page: tiles reflow into more lines
        self.assertGreater(v.layoutHeight(600), v.layoutHeight(1000))
        v.grab()
        self.assertTrue(v.tips)
        self.assertEqual([q for _, q in v.chipRects], [440, 420, None])
        v.setQueue(440)  # no Flex games: ignored
        self.assertIsNone(v.queueId)
        v.setQueue(420)
        self.assertEqual(v.profile()['summary']['games'], 12)
        # a refresh with the same sample keeps the filter; a new summoner resets it
        v.setSample(games, 'lcu')
        self.assertEqual(v.queueId, 420)
        v.setLoading([])
        self.assertIsNone(v.queueId)

    def test_hover_is_an_overlay_on_the_cached_card(self):
        """moving over a (?) icon or a chip repaints only the highlight: the card is not
        redrawn, and the highlight is drawn inside the hovered element's own pixels"""
        from app.components.play_style_widget import PlayStyleView

        draws = []
        real = PlayStyleView._PlayStyleView__draw

        def draw(self, p, pal):
            draws.append(1)
            return real(self, p, pal)

        games = [g(queue=420, champ=i % 4 + 1, when=MONDAY + timedelta(hours=i), kp=0.5)
                 for i in range(12)]
        v = PlayStyleView()
        v.resize(1000, 100)
        v.setSample(games, 'lcu')
        v.resize(1000, v.layoutHeight(1000))
        with unittest.mock.patch.object(PlayStyleView, '_PlayStyleView__draw', draw):
            v._chipHover = 'none'
            base = v.grab().toImage()
            self.assertEqual(len(draws), 1)
            helps = [(i, rect) for i, (rect, _, isHelp) in enumerate(v.tips) if isHelp]
            chips = [(rect, q) for rect, q in v.chipRects if q != v.queueId]
            self.assertTrue(helps and chips)

            for (index, tipRect), (chipRect, q) in zip(helps, chips):
                for hover, chip, area in ((index, 'none', tipRect), (-1, q, chipRect)):
                    v._hover, v._chipHover = hover, chip
                    img = v.grab().toImage()
                    self.assertEqual(len(draws), 1)  # served from the cache
                    changed = [(x, y) for y in range(img.height()) for x in range(img.width())
                               if img.pixel(x, y) != base.pixel(x, y)]
                    self.assertTrue(changed, (hover, chip))
                    inside = area.adjusted(-1, -1, 1, 1)
                    self.assertTrue(all(inside.contains(x, y) for x, y in changed))
            v._hover, v._chipHover = -1, 'none'
            self.assertEqual(v.grab().toImage(), base)
            self.assertEqual(len(draws), 1)


if __name__ == '__main__':
    unittest.main()
