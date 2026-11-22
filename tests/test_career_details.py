"""Career page second stage: the match rows paint from the match list alone; the full game of
each row (badges, score, kill participation, the ten players) is fetched afterwards.

- parseSummonerData makes no full-game requests;
- attachGameScores keeps at most `limit` full-game requests waiting, in row order, and reports
  each game as it finishes (the game info page path, limit None, still starts them all);
- attachCareerDetails fills game['participants'] and calls onGame per game;
- connector.getGameDetailByGameIdQuietly fetches a finished game once per connection;
- CareerInterface shows the page before the full games arrive, refreshes each row when its
  game arrives, and starts recent teammates / laning / season only after the last one.

Only the network is replaced (fake connector methods, a fake LCU session); the parsing, the
semaphores, the cache and the widgets are the real ones.
"""
import asyncio
import os
import time
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.lol import connector as connector_module
from app.lol import opscore, rank_history, tools
from app.lol.connector import connector
from app.lol.exceptions import SummonerGamesNotFound
from app.view import career_interface

PUUID = "puuid-me"
ICON = "app/resource/images/champion-0.png"
NOW_MS = int(time.time() * 1000)


def stats(win, kills=5):
    return {'champLevel': 15, 'kills': kills, 'deaths': 3, 'assists': 7,
            **{f'item{i}': 0 for i in range(7)}, 'perk0': 0,
            'totalMinionsKilled': 150, 'neutralMinionsKilled': 10, 'goldEarned': 11000,
            'totalDamageDealtToChampions': 20000, 'totalDamageTaken': 15000,
            'visionScore': 20, 'largestMultiKill': 1,
            'gameEndedInEarlySurrender': False, 'win': win}


def listGame(i, queueId=430):
    """one entry of the LCU match list (only the player's own participant)"""
    return {'gameId': 1000 + i, 'gameCreation': NOW_MS - i * 3600_000, 'gameDuration': 1800,
            'queueId': queueId, 'mapId': 11, 'gameMode': 'CLASSIC',
            'participants': [{'championId': 1, 'spell1Id': 4, 'spell2Id': 14,
                              'stats': stats(i % 2 == 0),
                              'timeline': {'lane': 'MIDDLE', 'role': 'SOLO'}}]}


def fullGame(gameId, queueId=430):
    """LCU /lol-match-history/v1/games/{id}: ten players, PUUID is participant 1"""
    participants, identities = [], []
    for pid in range(1, 11):
        team = 100 if pid <= 5 else 200
        participants.append({'participantId': pid, 'teamId': team, 'championId': pid,
                             'stats': stats(team == 100, pid)})
        identities.append({'participantId': pid, 'player': {
            'puuid': PUUID if pid == 1 else f'p{pid}', 'gameName': f'P{pid}', 'tagLine': 'T'}})
    return {'gameId': gameId, 'queueId': queueId, 'participants': participants,
            'participantIdentities': identities}


async def icon(*args, **kwargs):
    return ICON


async def settle(turns=50):
    """let the background tasks run as far as they can without the held requests"""
    for _ in range(turns):
        await asyncio.sleep(0)


class FakeNetwork:
    """full-game requests of tools.attachGameScores; `gates[gameId]` holds one until set"""

    def __init__(self):
        self.gates = {}
        self.failing = set()
        self.started = []
        self.waiting = 0
        self.peakWaiting = 0

    async def detail(self, gameId):
        self.started.append(gameId)
        self.waiting += 1
        self.peakWaiting = max(self.peakWaiting, self.waiting)
        try:
            gate = self.gates.get(gameId)
            if gate is not None:
                await gate.wait()
            else:
                await asyncio.sleep(0)
        finally:
            self.waiting -= 1
        if gameId in self.failing:
            raise SummonerGamesNotFound()  # one of the app's BaseException subclasses
        return fullGame(gameId)


class NetworkTestCase(unittest.TestCase):

    def setUp(self):
        self.net = FakeNetwork()
        manager = SimpleNamespace(getNameMapByQueueId=lambda q: {'name': 'Draft', 'map': 'SR'},
                                  getMapNameById=lambda m: 'SR', champs={}, champRoles={})
        self.summaries = []

        async def summary(gameId):
            self.summaries.append(gameId)
            return None

        patches = [mock.patch.object(connector, 'manager', manager, create=True),
                   mock.patch.object(connector, 'getGameDetailByGameIdQuietly', self.net.detail),
                   mock.patch.object(connector, 'getGameSummaryViaSgpQuietly', summary),
                   mock.patch.object(opscore, '_summaries', OrderedDict()),
                   mock.patch.object(opscore, '_semaphore', None),
                   *[mock.patch.object(connector, name, icon) for name in
                     ('getProfileIcon', 'getChampionIcon', 'getSummonerSpellIcon',
                      'getItemIcon', 'getRuneIcon')]]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def parsed(self, n, queueId=430):
        async def parse():
            return [await tools.parseGameData(listGame(i, queueId)) for i in range(n)]
        return asyncio.run(parse())


class ParseSummonerDataTest(NetworkTestCase):

    def test_no_full_game_requests_before_the_page_paints(self):
        summoner = {'puuid': PUUID, 'profileIconId': 1, 'summonerLevel': 30,
                    'xpSinceLastLevel': 0, 'xpUntilNextLevel': 1, 'gameName': 'Me',
                    'tagLine': 'T', 'privacy': 'PUBLIC'}

        async def main():
            async def games():
                return {'gameCount': 5, 'games': [listGame(i) for i in range(5)]}

            async def rank():
                return {}
            return await tools.parseSummonerData(
                summoner, asyncio.ensure_future(rank()), asyncio.ensure_future(games()))

        info = asyncio.run(main())

        self.assertEqual(self.net.started, [])
        self.assertEqual(len(info['games']['games']), 5)
        self.assertEqual((info['games']['wins'], info['games']['losses']), (3, 2))
        for game in info['games']['games']:
            self.assertNotIn('participants', game)
            self.assertNotIn('scoreRank', game)


class AttachGameScoresTest(NetworkTestCase):

    def test_limit_bounds_the_waiting_requests_and_keeps_row_order(self):
        games = self.parsed(6)
        finished = []

        async def onDetail(game, detail):
            finished.append(game['gameId'])

        details = asyncio.run(tools.attachGameScores(games, PUUID, onDetail, limit=2))

        self.assertEqual(self.net.peakWaiting, 2)
        self.assertEqual(self.net.started, [g['gameId'] for g in games])
        self.assertEqual(finished, [g['gameId'] for g in games])
        self.assertEqual([d['gameId'] for d in details], [g['gameId'] for g in games])
        for game in games:
            self.assertEqual(game['scorePlayers'], 10)
            self.assertIsNotNone(game['kp'])

    def test_without_limit_every_request_starts_at_once(self):
        """the game info page (attachSummonerScores) passes no limit"""
        games = self.parsed(6)
        asyncio.run(tools.attachGameScores(games, PUUID))
        self.assertEqual(self.net.peakWaiting, 6)

    def test_a_failed_game_or_callback_does_not_stop_the_others(self):
        games = self.parsed(3)
        self.net.failing.add(games[1]['gameId'])
        seen = []

        async def onDetail(game, detail):
            seen.append((game['gameId'], detail is None))
            if game is games[0]:
                raise RuntimeError("row gone")

        details = asyncio.run(tools.attachGameScores(games, PUUID, onDetail, limit=1))

        self.assertIsNone(details[1])
        self.assertIsNotNone(details[0])
        self.assertIsNotNone(details[2])
        self.assertNotIn('scoreRank', games[1])
        self.assertEqual(seen, [(games[0]['gameId'], False), (games[1]['gameId'], True),
                                (games[2]['gameId'], False)])

    def test_ranked_summary_is_requested_once_per_game(self):
        games = self.parsed(3, queueId=420)

        async def summary(gameId):
            self.summaries.append(gameId)
            return {'participants': [{'participantId': 1, 'championId': 1}]}

        with mock.patch.object(connector, 'getGameSummaryViaSgpQuietly', summary):
            asyncio.run(tools.attachGameScores(games, PUUID, limit=1))

        self.assertEqual(sorted(self.summaries), sorted(g['gameId'] for g in games))


    def test_cancelling_stops_the_pending_summaries(self):
        """another summoner opened: the ranked summaries still in flight are cancelled"""
        games = self.parsed(2, queueId=420)
        hold = asyncio.Event()
        cancelled = []

        async def summary(gameId):
            try:
                await hold.wait()
            except asyncio.CancelledError:
                cancelled.append(gameId)
                raise

        async def main():
            task = asyncio.ensure_future(tools.attachGameScores(games, PUUID, limit=1))
            await settle()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await settle()

        with mock.patch.object(connector, 'getGameSummaryViaSgpQuietly', summary):
            asyncio.run(main())
        self.assertEqual(sorted(cancelled), sorted(g['gameId'] for g in games))


class AttachCareerDetailsTest(NetworkTestCase):

    def test_players_are_filled_per_game(self):
        games = self.parsed(3)
        self.net.failing.add(games[2]['gameId'])
        rows = []

        details = asyncio.run(tools.attachCareerDetails(
            games, PUUID, lambda game: rows.append((game['gameId'], len(game['participants']))),
            limit=1))

        self.assertEqual(rows, [(games[0]['gameId'], 10), (games[1]['gameId'], 10),
                                (games[2]['gameId'], 0)])
        self.assertEqual([p['teamId'] for p in games[0]['participants']], [100] * 5 + [200] * 5)
        self.assertEqual(games[0]['participants'][0]['puuid'], PUUID)
        self.assertIsNone(details[2])


class Response:

    def __init__(self, status, data):
        self.status, self.data = status, data

    async def json(self, **kwargs):
        return self.data

    def release(self):
        pass


class GameDetailCacheTest(unittest.TestCase):

    def setUp(self):
        self.requests = []
        self.answers = {}

        test = self

        class Session:
            async def get(self, path, params=None, ssl=None):
                test.requests.append(path)
                gameId = int(path.rsplit('/', 1)[1])
                return test.answers.get(gameId) or Response(200, fullGame(gameId))

        for name, value in (('lcuSess', Session()), ('semaphore', None),
                            ('gameDetails', OrderedDict())):
            p = mock.patch.object(connector, name, value)
            p.start()
            self.addCleanup(p.stop)

    def fetch(self, *gameIds):
        async def main():
            connector.semaphore = asyncio.Semaphore(1)
            return [await connector.getGameDetailByGameIdQuietly(g) for g in gameIds]
        return asyncio.run(main())

    def test_a_game_is_fetched_once(self):
        first, again, asText = self.fetch(7, 7, "7")
        self.assertEqual(self.requests, ["/lol-match-history/v1/games/7"])
        self.assertIs(first, again)
        self.assertIs(first, asText)

    def test_failures_are_not_kept(self):
        self.answers[7] = Response(500, {})
        self.assertEqual(self.fetch(7), [None])
        del self.answers[7]
        self.assertIsNotNone(self.fetch(7)[0])
        self.assertEqual(len(self.requests), 2)

    def test_the_least_recently_used_game_is_dropped(self):
        with mock.patch.object(connector_module, 'GAME_DETAIL_CACHE_SIZE', 2):
            self.fetch(1, 2, 1, 3)  # 1 used again, so 2 is the oldest when 3 arrives
            self.assertEqual(list(connector.gameDetails), ["1", "3"])
            self.fetch(1, 2)
        self.assertEqual(self.requests, [f"/lol-match-history/v1/games/{g}" for g in (1, 2, 3, 2)])

    def test_a_new_connection_starts_empty(self):
        fresh = connector_module.LolClientConnector()
        fresh.gameDetails["1"] = fullGame(1)
        fresh._LolClientConnector__reset()
        self.assertEqual(len(fresh.gameDetails), 0)


class CareerSecondStageTest(NetworkTestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.view = career_interface.CareerInterface()
        self.addCleanup(self.view.deleteLater)
        self.games = self.parsed(3)
        self.teammates = mock.AsyncMock(return_value={'puuid': PUUID, 'summoners': []})
        self.laning = mock.AsyncMock(return_value=None)
        self.season = mock.AsyncMock(return_value=(None, '', {}))

        async def parse(summoner, rankTask, gamesTask):
            await rankTask
            await gamesTask
            games = {'gameCount': 3, 'wins': 2, 'losses': 1, 'kills': 15, 'deaths': 9,
                     'assists': 21, 'games': self.games}
            return {'name': 'Me', 'isPublic': True, 'tagLine': 'T', 'icon': ICON, 'iconId': 1,
                    'level': 30, 'xpSinceLastLevel': 0, 'xpUntilNextLevel': 1, 'puuid': PUUID,
                    'rankInfo': {}, 'games': games, 'champions': []}

        lcu = SimpleNamespace(maxRefCnt=1, getSummonerByPuuid=mock.AsyncMock(return_value={'puuid': PUUID}),
                              getSummonerGamesByPuuid=mock.AsyncMock(return_value={}),
                              getRankedStatsByPuuid=mock.AsyncMock(return_value={}))
        for target, name, value in [
                (career_interface, 'connector', lcu),
                (career_interface, 'parseSummonerData', parse),
                (career_interface, 'getRecentTeammates', self.teammates),
                (career_interface, 'attachLaningScores', self.laning),
                (career_interface.season_stats, 'fetchSeasonGames', self.season),
                (career_interface.saved_players, 'refresh', lambda *a: False),
                (rank_history, 'record', lambda *a, **k: None),
                (rank_history, 'history', lambda *a, **k: [])]:
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def rows(self):
        layout = self.view.gameInfoLayout
        return [w for w in (layout.itemAt(i).widget() for i in range(layout.count()))
                if isinstance(w, career_interface.CareerGameRow)]

    def test_page_shows_before_the_full_games(self):
        gate = asyncio.Event()
        for game in self.games:
            self.net.gates[game['gameId']] = gate

        async def main():
            self.assertIs(await self.view.updateInterface(puuid=PUUID), True)
            await settle()

            # painted from the match list: rows without players, loading page off
            rows = self.rows()
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(not row.teams and row.kpValue == "" for row in rows))
            self.assertFalse(self.view.detailsTask.done())
            # connector.maxRefCnt (1) rows waiting, not all three
            self.assertEqual(self.net.started, [self.games[0]['gameId']])
            # the stages that need the full games have not started
            self.assertIsNone(self.view.teammatesTask)
            self.assertIsNone(self.view.laningTask)
            self.assertIsNone(self.view.seasonTask)

            gate.set()
            await self.view.detailsTask
            return rows

        rows = asyncio.run(main())

        for row in rows:
            self.assertEqual([len(team) for team in row.teams], [5, 5])
            self.assertNotEqual(row.kpValue, "")
            self.assertIn("#", row.scoreTip)
        self.assertEqual(self.net.peakWaiting, 1)
        self.teammates.assert_awaited_once()
        details = self.teammates.await_args.args[2]
        self.assertEqual([d['gameId'] for d in details], [g['gameId'] for g in self.games])
        self.laning.assert_awaited_once()
        self.season.assert_awaited_once()
        self.assertIsNotNone(self.view.summaryCard.summary)

    def test_teammates_written_during_the_load_are_not_shown_for_the_new_summoner(self):
        """the previous summoner's second stage may finish while this one loads"""
        parse = career_interface.parseSummonerData

        async def staleThenParse(summoner, rankTask, gamesTask):
            info = await parse(summoner, rankTask, gamesTask)
            self.view.recentTeammatesInfo = {'puuid': 'puuid-previous', 'summoners': []}
            return info

        gate = asyncio.Event()
        self.net.gates[self.games[0]['gameId']] = gate

        async def main():
            with mock.patch.object(career_interface, 'parseSummonerData', staleThenParse):
                await self.view.updateInterface(puuid=PUUID)
            self.assertIsNone(self.view.recentTeammatesInfo)
            gate.set()
            await self.view.detailsTask
            await self.view.teammatesTask

        asyncio.run(main())
        self.assertEqual(self.view.recentTeammatesInfo['puuid'], PUUID)

    def test_rows_rebuilt_by_the_filter_are_refreshed_too(self):
        """refresh() rebuilds the rows (filter) right after updateInterface returns"""
        gate = asyncio.Event()
        self.net.gates[self.games[0]['gameId']] = gate

        async def main():
            await self.view.updateInterface(puuid=PUUID)
            first = self.rows()
            self.view.filterComboBox.setCurrentIndex(1)  # queue 430: every game
            await settle()
            rebuilt = self.rows()
            self.assertFalse(set(map(id, first)) & set(map(id, rebuilt)))
            gate.set()
            await self.view.detailsTask
            return rebuilt

        rebuilt = asyncio.run(main())
        self.assertEqual(len(rebuilt), 3)
        self.assertTrue(all(len(row.teams) == 2 for row in rebuilt))


if __name__ == "__main__":
    unittest.main()
