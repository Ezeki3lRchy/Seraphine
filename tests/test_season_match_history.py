"""
Season sample from Riot's match server on non-CN servers (player history SUMMARY), no network.
The fixture is a sanitized real NA payload: identifiers, names and timestamps replaced,
participants trimmed to the fields the app reads.
"""
import asyncio
import copy
import json
import os
import unittest
from datetime import datetime
from unittest import mock

from app.lol import opscore, season_stats
from app.lol.connector import LolClientConnector
from app.lol.season_stats import fetchSeasonGames, rawSgpGameStats

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'sgp_match_history.json')


def fixture():
    with open(FIXTURE, encoding='utf-8') as f:
        return json.load(f)


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, status, body=b''):
        self.status, self.body = status, body
        self.released = False

    async def read(self):
        return self.body

    def release(self):
        self.released = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return self.response


def matchConnector(response):
    c = LolClientConnector()
    c.inTencent = False
    c.matchSgpHost, c.platformId = 'https://test-red.pp.sgp.pvp.net', 'NA1'
    c.matchSgpSess = FakeSession(response)
    c.getSGPtokenQuietly = mock.AsyncMock(return_value='token')
    return c


class MatchHistoryConnectorTest(unittest.TestCase):
    def test_unwraps_games(self):
        body = json.dumps(fixture()).encode()
        c = matchConnector(FakeResponse(200, body))
        games = run(c.getMatchHistoryViaSgpQuietly('puuid-me', 50, 50))
        self.assertEqual(len(games), 5)
        self.assertEqual(set(games[0]), {'metadata', 'json'})
        path, kwargs = c.matchSgpSess.calls[0]
        self.assertEqual(path, '/match-history-query/v1/products/lol/player/puuid-me/SUMMARY')
        self.assertEqual(kwargs['params'], {'startIndex': 50, 'count': 50})
        self.assertEqual(kwargs['headers'], {'Authorization': 'Bearer token'})

    def test_past_the_end_is_empty(self):
        c = matchConnector(FakeResponse(200, b'{"games": []}'))
        self.assertEqual(run(c.getMatchHistoryViaSgpQuietly('puuid-me', 111, 50)), [])

    def test_failures_are_none(self):
        res = FakeResponse(403, b'{"errorCode": "x"}')
        c = matchConnector(res)
        self.assertIsNone(run(c.getMatchHistoryViaSgpQuietly('puuid-me')))
        self.assertTrue(res.released)
        for body in (b'not json', b'[]', b'{"games": {}}'):
            c = matchConnector(FakeResponse(200, body))
            self.assertIsNone(run(c.getMatchHistoryViaSgpQuietly('puuid-me')))
        # no token (client closed / not logged in): nothing is sent
        c = matchConnector(FakeResponse(200, b'{"games": []}'))
        c.getSGPtokenQuietly = mock.AsyncMock(return_value=None)
        self.assertIsNone(run(c.getMatchHistoryViaSgpQuietly('puuid-me')))
        self.assertEqual(c.matchSgpSess.calls, [])

    def test_cn_is_not_sent(self):
        c = matchConnector(FakeResponse(200, b'{"games": []}'))
        c.inTencent = True
        self.assertIsNone(run(c.getMatchHistoryViaSgpQuietly('puuid-me')))
        self.assertEqual(c.matchSgpSess.calls, [])
        c.getSGPtokenQuietly.assert_not_called()

    def test_401_refreshes_the_token_once(self):
        c = matchConnector(FakeResponse(401))
        tokens = iter(['old', 'new'])
        c.getSGPtokenQuietly = mock.AsyncMock(side_effect=lambda fresh=False: next(tokens))
        self.assertIsNone(run(c.getMatchHistoryViaSgpQuietly('puuid-me')))
        self.assertEqual([kw['headers'] for _, kw in c.matchSgpSess.calls],
                         [{'Authorization': 'Bearer old'}, {'Authorization': 'Bearer new'}])
        self.assertEqual([call.args for call in c.getSGPtokenQuietly.await_args_list],
                         [(False,), (True,)])


class TokenQuietlyTest(unittest.TestCase):
    """the client's access token is cached, never retried / popped up / logged / recorded"""
    SECRET = 'eyJ-secret-access-token'

    def connector(self, body=None, fail=False):
        c = LolClientConnector()
        c.semaphore = asyncio.Semaphore(1)

        class Res:
            async def json(self, content_type=None):
                return body if body is not None else {'accessToken': TokenQuietlyTest.SECRET}

        class Sess:
            calls = 0

            async def get(self, path, params=None, ssl=None):
                Sess.calls += 1
                if fail:
                    raise TypeError("client gone")
                return Res()

        c.lcuSess = Sess()
        return c, Sess

    def patched(self, c):
        """the module-level connector (needLcu / retry read it) is this test's connector"""
        from app.lol import connector as module
        return mock.patch.object(module, 'connector', c)

    def test_cached_until_fresh(self):
        c, sess = self.connector()

        async def go():
            a = await c.getSGPtokenQuietly()
            b = await c.getSGPtokenQuietly()
            fresh = await c.getSGPtokenQuietly(fresh=True)
            return a, b, fresh

        with self.patched(c):
            self.assertEqual(run(go()), (self.SECRET,) * 3)
        self.assertEqual(sess.calls, 2)
        self.assertEqual(len(c.callStack), 0)  # not recorded for the crash report

    def test_failures_are_silent(self):
        from app.common.signals import signalBus
        popups = []

        def onPopup(*a):
            popups.append(a)
        signalBus.lcuApiExceptionRaised.connect(onPopup)
        try:
            for c in (self.connector(fail=True)[0], self.connector(body={'httpStatus': 404})[0],
                      LolClientConnector()):  # the last: semaphore None, client closed
                with self.patched(c):
                    self.assertIsNone(run(c.getSGPtokenQuietly()))
                self.assertEqual(len(c.callStack), 0)
        finally:
            signalBus.lcuApiExceptionRaised.disconnect(onPopup)
        self.assertEqual(popups, [])

    def test_token_not_in_call_stack_or_log(self):
        """getSGPtoken (retry-wrapped) and the token event keep the value out of both"""
        import logging
        from app.lol import connector as module
        from tests import LOG_RECORDS

        c, _ = self.connector()
        seraphine = logging.getLogger("Seraphine")
        level = seraphine.level
        seraphine.setLevel(logging.DEBUG)
        del LOG_RECORDS[:]
        try:
            with self.patched(c):
                self.assertEqual(run(c.getSGPtoken()), self.SECRET)
                ws = module.LcuWebSocket(1, 'x')
                ws.subscribe('e', '/entitlements/v1/token', ('Update',))(mock.AsyncMock())

                async def event():
                    ws.matchUri({'uri': '/entitlements/v1/token', 'eventType': 'Update',
                                 'data': {'accessToken': self.SECRET}})
                    await asyncio.sleep(0)
                run(event())
        finally:
            seraphine.setLevel(level)
        self.assertEqual(len(c.callStack), 1)
        self.assertNotIn(self.SECRET, " ".join(str(r) for r in c.callStack))
        self.assertNotIn(self.SECRET, "\n".join(LOG_RECORDS))
        # the calls are still logged, just not the value
        self.assertTrue(any('getSGPtoken' in line for line in LOG_RECORDS))
        self.assertTrue(any(module.REDACTED in line for line in LOG_RECORDS))


class RealPayloadStatsTest(unittest.TestCase):
    def setUp(self):
        self.games = fixture()['games']

    def test_ranked(self):
        game = self.games[0]
        r = rawSgpGameStats(game, 'puuid-me')
        me = next(p for p in game['json']['participants'] if p['puuid'] == 'puuid-me')
        team = [p for p in game['json']['participants'] if p['teamId'] == me['teamId']]
        self.assertEqual(r['position'], me['teamPosition'])
        self.assertIn(r['position'], ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY'))
        self.assertEqual((r['championId'], r['teamId'], r['queueId']),
                         (me['championId'], me['teamId'], 420))
        self.assertEqual(r['cs'], me['totalMinionsKilled'] + me['neutralMinionsKilled'])
        self.assertEqual((r['damage'], r['gold'], r['vision']),
                         (me['totalDamageDealtToChampions'], me['goldEarned'], me['visionScore']))
        self.assertAlmostEqual(r['kp'], (me['kills'] + me['assists']) /
                               sum(p['kills'] for p in team))
        self.assertAlmostEqual(r['damageShare'], me['totalDamageDealtToChampions'] /
                               sum(p['totalDamageDealtToChampions'] for p in team))
        # the server's own challenges.killParticipation agrees (rounded there)
        self.assertAlmostEqual(r['kp'], me['challenges']['killParticipation'], places=2)
        self.assertEqual(r['version'], 16)
        self.assertEqual(r['durationSecs'], game['json']['gameDuration'])
        self.assertFalse(r['custom'] or r['remake'])

    def test_remake_custom_practice(self):
        remake, custom, practice = (rawSgpGameStats(g, 'puuid-me') for g in self.games[2:5])
        self.assertTrue(remake['remake'])
        self.assertLess(remake['durationSecs'], 300)
        self.assertTrue(custom['custom'])
        self.assertFalse(custom['remake'])
        # practice tool: one participant, no team position
        self.assertTrue(practice['custom'])
        self.assertIsNone(practice['position'])

    def test_lane_opponents_and_score(self):
        for game in self.games[:2]:
            ps = game['json']['participants']
            for p in ps:
                opp = [q for q in ps if q['teamId'] != p['teamId']
                       and q['teamPosition'] == p['teamPosition']]
                self.assertEqual(len(opp), 1)
            scores = opscore.scoreSummary(game['json'])
            self.assertEqual(len(scores), 10)
        self.assertIsNone(opscore.scoreSummary(self.games[2]['json']))  # remake


def page(games, beg, count):
    return [copy.deepcopy(g) for g in games[beg:beg + count]]


class FetchSeasonTest(unittest.TestCase):
    NOW = datetime(2026, 7, 2).timestamp()

    def setUp(self):
        base = fixture()['games'][0]
        self.start = season_stats.seasonStart(self.NOW)

        def game(i, when):
            g = copy.deepcopy(base)
            g['json']['gameId'] = 7000 + i
            g['json']['gameCreation'] = when
            return g

        day = 86400000
        newest = int(datetime(2026, 7, 1).timestamp() * 1000)
        # 120 games this season (one per day-ish), then 30 from last year
        self.history = [game(i, newest - i * day // 2) for i in range(120)] + \
                       [game(120 + i, self.start - (i + 1) * day) for i in range(30)]

    def fake(self, inTencent=False, history=None, fail=False):
        history = self.history if history is None else history
        c = mock.MagicMock()
        c.inTencent = inTencent

        async def matchPage(puuid, beg, count):
            return None if fail else page(history, beg, count)

        c.getMatchHistoryViaSgpQuietly = mock.AsyncMock(side_effect=matchPage)
        c.getSummonerGamesByPuuidQuietly = mock.AsyncMock(return_value=None)
        return c

    def fetch(self, c, **kwargs):
        with mock.patch.object(season_stats, 'connector', c):
            return run(fetchSeasonGames('puuid-me', now=self.NOW, **kwargs))[:2]

    def fetchInfo(self, c, **kwargs):
        with mock.patch.object(season_stats, 'connector', c):
            return run(fetchSeasonGames('puuid-me', now=self.NOW, **kwargs))

    def test_pages_until_season_start(self):
        c = self.fake()
        games, source = self.fetch(c)
        self.assertEqual(source, 'match')
        self.assertEqual(len(games), 120)
        self.assertEqual([g['gameId'] for g in games], list(range(7000, 7120)))
        # 0-49, 50-99, 100-149 (crosses the season start) -> 3 requests
        self.assertEqual([call.args[1:] for call in c.getMatchHistoryViaSgpQuietly.await_args_list],
                         [(0, 50), (50, 50), (100, 50)])
        c.getSummonerGamesByPuuidQuietly.assert_not_awaited()

    def test_cap_and_short_page(self):
        c = self.fake(history=self.history[:60])
        games, _ = self.fetch(c)
        self.assertEqual(len(games), 60)
        # the second page is short: end of history, no third request
        self.assertEqual(c.getMatchHistoryViaSgpQuietly.await_count, 2)

        c = self.fake()
        games, _ = self.fetch(c, cap=70)
        self.assertEqual(len(games), 70)
        self.assertEqual(c.getMatchHistoryViaSgpQuietly.await_count, 2)
        self.assertEqual(season_stats.seasonCap('match'), season_stats.MATCH_SEASON_CAP)
        self.assertEqual(season_stats.seasonCap('lcu'), season_stats.SEASON_CAP)

    def test_repeated_page_stops(self):
        c = self.fake()

        async def same(puuid, beg, count):
            return page(self.history, 0, count)

        c.getMatchHistoryViaSgpQuietly = mock.AsyncMock(side_effect=same)
        games, _ = self.fetch(c)
        self.assertEqual(len(games), 50)
        self.assertEqual(c.getMatchHistoryViaSgpQuietly.await_count, 2)

    def test_failure_falls_back_to_lcu(self):
        c = self.fake(fail=True)
        lcuGame = {'gameId': 1, 'queueId': 420, 'gameCreation': self.start + 1000,
                   'gameVersion': '16.1', 'gameDuration': 1500,
                   'participants': [{'participantId': 1, 'teamId': 100, 'championId': 1,
                                     'stats': {'kills': 1, 'deaths': 1, 'assists': 1,
                                               'win': True}}]}
        c.getSummonerGamesByPuuidQuietly = mock.AsyncMock(side_effect=[[lcuGame], [lcuGame]])
        games, source = self.fetch(c)
        self.assertEqual((source, len(games)), ('lcu', 1))

        # empty match history -> LCU too (here also empty)
        c = self.fake(history=[])
        games, source = self.fetch(c)
        self.assertEqual((games, source), (None, 'lcu'))

    def test_cn_unchanged(self):
        c = self.fake(inTencent=True)
        c.sgpSess, c.sgpToken = object(), 'token'

        async def sgp(puuid, beg, end):
            return {'games': page(self.history, beg, end - beg + 1)}

        c.getSummonerGamesByPuuidViaSGP = mock.AsyncMock(side_effect=sgp)
        games, source = self.fetch(c)
        self.assertEqual((source, len(games)), ('sgp', 120))
        c.getMatchHistoryViaSgpQuietly.assert_not_awaited()
        calls = [call.args[1:] for call in c.getSummonerGamesByPuuidViaSGP.await_args_list]
        self.assertEqual(calls[:2], [(0, 19), (20, 39)])
        self.assertEqual(len(calls), 7)  # pages of 20 up to the season start

    def flaky(self, c, failures):
        """match pages fail for the listed start indexes (each entry = one failed call)"""
        failures = list(failures)

        async def matchPage(puuid, beg, count):
            if beg in failures:
                failures.remove(beg)
                return None
            return page(self.history, beg, count)

        c.getMatchHistoryViaSgpQuietly = mock.AsyncMock(side_effect=matchPage)
        return c

    def calls(self, c):
        return [call.args[1] for call in c.getMatchHistoryViaSgpQuietly.await_args_list]

    def test_complete_sample_is_not_partial(self):
        games, source, info = self.fetchInfo(self.fake())
        self.assertEqual((source, len(games), info), ('match', 120,
                                                       {'capped': False, 'partial': False}))

    def test_failed_page_is_retried_once(self):
        c = self.flaky(self.fake(), [50])
        games, source, info = self.fetchInfo(c)
        self.assertEqual((source, len(games), info['partial']), ('match', 120, False))
        self.assertEqual(self.calls(c), [0, 50, 50, 100])

    def test_later_page_failing_twice_is_partial(self):
        c = self.flaky(self.fake(), [100, 100])
        games, source, info = self.fetchInfo(c)
        self.assertEqual((source, len(games)), ('match', 100))
        self.assertEqual(info, {'capped': False, 'partial': True})
        self.assertEqual(self.calls(c), [0, 50, 100, 100])
        # more than one page came back: the LCU list is not tried
        c.getSummonerGamesByPuuidQuietly.assert_not_awaited()

    def lcuGames(self, n):
        return [{'gameId': 500 + i, 'queueId': 420, 'gameCreation': self.start + 1000 + i,
                 'gameVersion': '16.1', 'gameDuration': 1500,
                 'participants': [{'participantId': 1, 'teamId': 100, 'championId': 1,
                                   'stats': {'kills': 1, 'deaths': 1, 'assists': 1,
                                             'win': True}}]} for i in range(n)][::-1]

    def test_only_first_page_tries_the_lcu_list(self):
        # LCU has fewer season games: keep the match sample, marked partial
        c = self.flaky(self.fake(), [50, 50])
        lcu = self.lcuGames(20)
        c.getSummonerGamesByPuuidQuietly = mock.AsyncMock(side_effect=[lcu[:20], []])
        games, source, info = self.fetchInfo(c)
        self.assertEqual((source, len(games), info['partial']), ('match', 50, True))
        c.getSummonerGamesByPuuidQuietly.assert_awaited()

        # LCU has more: it is used (own stats only)
        c = self.flaky(self.fake(), [50, 50])
        lcu = self.lcuGames(70)

        async def lcuPage(puuid, beg, end):
            return lcu[beg:end + 1]

        c.getSummonerGamesByPuuidQuietly = mock.AsyncMock(side_effect=lcuPage)
        games, source, info = self.fetchInfo(c)
        self.assertEqual((source, len(games), info['partial']), ('lcu', 70, False))

    def test_capped_before_the_season_filter(self):
        # 70 fetched = the cap; one of them is on an older patch and filtered out
        self.history[10]['json']['gameVersion'] = '15.24.1'
        games, source, info = self.fetchInfo(self.fake(), cap=70)
        self.assertEqual(len(games), 69)
        self.assertTrue(info['capped'])

    def test_first_page_failing_falls_back(self):
        c = self.flaky(self.fake(), [0, 0])
        c.getSummonerGamesByPuuidQuietly = mock.AsyncMock(return_value=None)
        games, source, info = self.fetchInfo(c)
        self.assertEqual((games, source, info['partial']), (None, 'lcu', True))
        self.assertEqual(self.calls(c), [0, 0])


if __name__ == '__main__':
    unittest.main()
