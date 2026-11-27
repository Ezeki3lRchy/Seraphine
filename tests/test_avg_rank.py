"""Average rank of a game (app/lol/avg_rank.py, app/lol/opgg_history.py) and its line in the
career match row. Sources in order: OP.GG's at-the-time value, the ranks recorded when the
game started, an estimate from the players' current ranks."""
import asyncio
import datetime
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.lol import avg_rank, opgg_history

BOT = avg_rank.BOT_PUUID
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "opgg_mcp_matches.json"


def stats(solo=None, flex=None):
    """a ranked-stats response; solo / flex are (tier, division) or None (unranked)"""
    def entry(rank):
        tier, division = rank or ('', 'NA')
        return {'tier': tier, 'division': division, 'leaguePoints': 0}
    return {'queueMap': {'RANKED_SOLO_5x5': entry(solo), 'RANKED_FLEX_SR': entry(flex)}}


def game(puuids, queueId=420, gameId=1, **extra):
    players = [{'puuid': p, 'teamId': 100 if i < (len(puuids) + 1) // 2 else 200}
               for i, p in enumerate(puuids)]
    return {'gameId': gameId, 'queueId': queueId, 'gameMode': 'CLASSIC', 'remake': False,
            'participants': players, **extra}


def opggText():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))['result']['content'][0]['text']


def stamp(iso):
    """an OP.GG created_at -> the gameCreation (ms) of the same game"""
    return int(datetime.datetime.fromisoformat(iso).timestamp() * 1000)


class FakeConnector:
    def __init__(self, table, server=None):
        self.table = table
        self.server = server
        self.calls = []

    async def getRankedStatsByPuuidQuietly(self, puuid):
        self.calls.append(puuid)
        await asyncio.sleep(0)
        value = self.table.get(puuid)
        if isinstance(value, Exception):
            raise value
        return value


class RankValueTest(unittest.TestCase):

    def test_scale(self):
        self.assertEqual(avg_rank.rankValue('IRON', 'IV'), 0)
        self.assertEqual(avg_rank.rankValue('IRON', 'I'), 3)
        self.assertEqual(avg_rank.rankValue('gold', 'iv'), 12)
        self.assertEqual(avg_rank.rankValue('DIAMOND', 'I'), 27)
        self.assertEqual(avg_rank.rankValue('MASTER', 'NA'), 28)
        self.assertEqual(avg_rank.rankValue('GRANDMASTER', 'I'), 32)
        self.assertEqual(avg_rank.rankValue('CHALLENGER', ''), 36)

    def test_unranked_and_unknown(self):
        for tier in ('', None, 'NONE', 'UNRANKED', 'MYTHIC'):
            self.assertIsNone(avg_rank.rankValue(tier, 'I'), tier)
        # a tier without a usable division counts as its lowest division
        self.assertEqual(avg_rank.rankValue('GOLD', 'NA'), 12)

    def test_round_trip(self):
        for tier in avg_rank.TIERS:
            for division in avg_rank.DIVISIONS:
                value = avg_rank.rankValue(tier, division)
                self.assertEqual(avg_rank.rankFromValue(value), (tier, division))
        for tier in avg_rank.APEX:
            self.assertEqual(avg_rank.rankFromValue(avg_rank.rankValue(tier, 'NA')), (tier, ''))
        self.assertEqual(avg_rank.rankFromValue(30), ('MASTER', ''))
        self.assertEqual(avg_rank.rankFromValue(99), ('CHALLENGER', ''))
        self.assertEqual(avg_rank.rankFromValue(-3), ('IRON', 'IV'))

    def test_queue_key(self):
        self.assertEqual(avg_rank.queueRankKey(440), 'RANKED_FLEX_SR')
        for queueId in (420, 400, 430, 450, 1700, None):
            self.assertEqual(avg_rank.queueRankKey(queueId), 'RANKED_SOLO_5x5')

    def test_rank_values_of_bad_payloads(self):
        empty = {'RANKED_SOLO_5x5': None, 'RANKED_FLEX_SR': None}
        for payload in (None, {}, {'queueMap': None}, {'queueMap': []},
                        {'queueMap': {'RANKED_SOLO_5x5': None}}):
            self.assertEqual(avg_rank.rankValues(payload), empty)
        self.assertEqual(avg_rank.rankValues(stats(('GOLD', 'II'), ('MASTER', 'NA'))),
                         {'RANKED_SOLO_5x5': 14, 'RANKED_FLEX_SR': 28})


class AverageRankTest(unittest.TestCase):

    def test_average(self):
        # Gold IV (12) x 5 + Gold II (14) x 5 -> 13 = Gold III
        self.assertEqual(avg_rank.averageRank([12] * 5 + [14] * 5),
                         {'tier': 'GOLD', 'division': 'III', 'players': 10, 'total': 10})

    def test_unranked_players_are_left_out(self):
        self.assertEqual(avg_rank.averageRank([None, 16, None, 20]),
                         {'tier': 'PLATINUM', 'division': 'II', 'players': 2, 'total': 4})

    def test_one_ranked_player_is_enough(self):
        self.assertEqual(avg_rank.averageRank([None] * 9 + [27]),
                         {'tier': 'DIAMOND', 'division': 'I', 'players': 1, 'total': 10})

    def test_nobody_ranked(self):
        self.assertIsNone(avg_rank.averageRank([None] * 10))
        self.assertIsNone(avg_rank.averageRank([]))

    def test_apex(self):
        self.assertEqual(avg_rank.averageRank([28, 32, 36])['tier'], 'GRANDMASTER')
        self.assertEqual(avg_rank.averageRank([27, 28])['tier'], 'MASTER')  # 27.5 rounds to 28


class CandidateTest(unittest.TestCase):
    P = [f"p{i}" for i in range(10)]

    def test_matchmade_queues(self):
        for queueId in (420, 440, 400, 430, 450, 490, 1700, 900):
            self.assertEqual(avg_rank.gamePuuids(game(self.P, queueId)), self.P, queueId)

    def test_not_shown(self):
        self.assertEqual(avg_rank.gamePuuids(None), [])
        self.assertEqual(avg_rank.gamePuuids(game(self.P, remake=True)), [])
        for queueId in (0, 3100, 3140, 2000, 830, 840, 850, 870, 880, 890, None):
            self.assertEqual(avg_rank.gamePuuids(game(self.P, queueId)), [], queueId)
        self.assertEqual(avg_rank.gamePuuids(game(self.P, 9999, gameMode='PRACTICETOOL')), [])
        self.assertEqual(avg_rank.gamePuuids(game([], 420)), [])
        self.assertEqual(avg_rank.gamePuuids(game(self.P, participants=None)), [])
        # the practice tool row of the screenshot: one player, one team
        self.assertEqual(avg_rank.gamePuuids(game(['me'], 9999)), [])

    def test_bots_and_missing_puuids(self):
        self.assertEqual(avg_rank.gamePuuids(game(self.P[:9] + [BOT])), [])
        self.assertEqual(avg_rank.gamePuuids(game(self.P[:9] + [None])), [])
        self.assertEqual(avg_rank.gamePuuids(game(self.P[:9] + [''])), [])


class _StateMixin:
    """fresh module state: rank cache, snapshot store in a temp dir, OP.GG cache"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for name, value in (('_ranks', {}), ('_pending', {}), ('_semaphore', None),
                            ('_store', None),
                            ('storePath', os.path.join(folder.name, 'game_ranks.json'))):
            patcher = mock.patch.object(avg_rank, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(opgg_history._cache, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def connect(self, table, server=None):
        fake = FakeConnector(table, server)
        patcher = mock.patch.object(avg_rank, 'connector', fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class EstimateTest(_StateMixin, unittest.TestCase):
    """no OP.GG value and no snapshot: the players' current ranks"""

    def test_queue_decides_which_rank_is_used(self):
        self.connect({'a': stats(('GOLD', 'IV'), ('DIAMOND', 'I')),
                      'b': stats(('GOLD', 'II'), None)})
        solo, flex, normal = game(['a', 'b'], 420, 1), game(['a', 'b'], 440, 2), \
            game(['a', 'b'], 430, 3)
        asyncio.run(avg_rank.attachAverageRanks([solo, flex, normal]))
        self.assertEqual(solo['avgRank'], {'tier': 'GOLD', 'division': 'III', 'players': 2,
                                           'total': 2, 'source': 'estimate'})
        self.assertEqual(flex['avgRank'], {'tier': 'DIAMOND', 'division': 'I', 'players': 1,
                                           'total': 2, 'source': 'estimate'})
        self.assertEqual(normal['avgRank'], solo['avgRank'])

    def test_each_player_is_looked_up_once(self):
        fake = self.connect({p: stats(('SILVER', 'I')) for p in 'abcd'})
        games = [game(['a', 'b', 'c'], gameId=1), game(['a', 'b', 'd'], gameId=2)]
        seen = []
        asyncio.run(avg_rank.attachAverageRanks(games, seen.append))
        self.assertEqual(sorted(fake.calls), ['a', 'b', 'c', 'd'])
        self.assertEqual(sorted(g['gameId'] for g in seen), [1, 2])
        # a second page with the same players: served from the cache, checked games skipped
        again = game(['a', 'd'], gameId=3)
        asyncio.run(avg_rank.attachAverageRanks(games + [again]))
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(again['avgRank']['tier'], 'SILVER')

    def test_cache_expires(self):
        fake = self.connect({'a': stats(('SILVER', 'I')), 'b': stats(('SILVER', 'I'))})
        asyncio.run(avg_rank.attachAverageRanks([game(['a', 'b'])]))
        with mock.patch.object(avg_rank, 'RANK_TTL', -1):
            asyncio.run(avg_rank.attachAverageRanks([game(['a', 'b'])]))
        self.assertEqual(len(fake.calls), 4)

    def test_failed_lookups_are_left_out_and_not_cached(self):
        fake = self.connect({'a': stats(('GOLD', 'I')), 'b': None})
        g = game(['a', 'b'])
        asyncio.run(avg_rank.attachAverageRanks([g]))
        self.assertEqual(g['avgRank'], {'tier': 'GOLD', 'division': 'I', 'players': 1,
                                        'total': 2, 'source': 'estimate'})
        self.assertTrue(g['avgRankChecked'])
        asyncio.run(avg_rank.attachAverageRanks([game(['a', 'b'], gameId=2)]))
        self.assertEqual(fake.calls.count('a'), 1)
        self.assertEqual(fake.calls.count('b'), 2)

    def test_every_lookup_failed_is_retried_next_time(self):
        fake = self.connect({})
        g = game(['a', 'b'])
        seen = []
        asyncio.run(avg_rank.attachAverageRanks([g], seen.append))
        self.assertNotIn('avgRank', g)
        self.assertFalse(g['avgRankChecked'])
        self.assertEqual(seen, [])
        fake.table.update(a=stats(('IRON', 'IV')), b=stats())
        asyncio.run(avg_rank.attachAverageRanks([g], seen.append))
        self.assertEqual(g['avgRank']['tier'], 'IRON')
        self.assertEqual(seen, [g])

    def test_nobody_ranked_and_excluded_games(self):
        fake = self.connect({'a': stats(), 'b': stats()})
        unranked, custom, remake = game(['a', 'b'], gameId=1), game(['a', 'b'], 0, 2), \
            game(['a', 'b'], gameId=3, remake=True)
        seen = []
        asyncio.run(avg_rank.attachAverageRanks([unranked, custom, remake, None], seen.append))
        for g in (unranked, custom, remake):
            self.assertNotIn('avgRank', g)
            self.assertTrue(g['avgRankChecked'])
        self.assertEqual(seen, [])
        self.assertEqual(sorted(fake.calls), ['a', 'b'])  # nothing asked for the excluded games

    def test_an_exception_is_logged_by_type_name_only(self):
        self.connect({'a': RuntimeError("token=secret"), 'b': stats(('GOLD', 'I'))})
        g, other = game(['a', 'b'], gameId=1), game(['b', 'c'], gameId=2)
        with mock.patch.object(avg_rank.logger, 'warning') as warning:
            asyncio.run(avg_rank.attachAverageRanks([g, other]))
        self.assertNotIn('avgRank', g)
        self.assertEqual(other['avgRank']['tier'], 'GOLD')
        text = " ".join(str(call.args[0]) for call in warning.call_args_list)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("secret", text)

    def test_cancelling_one_page_keeps_the_shared_lookup(self):
        async def run():
            gate = asyncio.Event()
            calls = []

            async def slow(puuid):
                calls.append(puuid)
                await gate.wait()
                return stats(('GOLD', 'I'))

            with mock.patch.object(avg_rank, 'connector',
                                   mock.Mock(getRankedStatsByPuuidQuietly=slow, server=None)):
                first = asyncio.ensure_future(avg_rank.attachAverageRanks([game(['a', 'b'])]))
                for _ in range(5):
                    await asyncio.sleep(0)
                second = game(['a', 'b'], gameId=2)
                later = asyncio.ensure_future(avg_rank.attachAverageRanks([second]))
                for _ in range(5):
                    await asyncio.sleep(0)
                first.cancel()
                gate.set()
                await later
                with self.assertRaises(asyncio.CancelledError):
                    await first
            return calls, second

        calls, second = asyncio.run(run())
        self.assertEqual(sorted(calls), ['a', 'b'])
        self.assertEqual(second['avgRank']['tier'], 'GOLD')


class OpggTextTest(unittest.TestCase):
    """the text format of OP.GG's MCP endpoint (the fixture is a response of the real service)"""

    def test_captured_response(self):
        entries = opgg_history.entriesFromText(opggText())
        self.assertEqual(len(entries), 5)
        first = entries[0]
        self.assertEqual((first['tier'], first['division']), ('GRANDMASTER', ''))
        self.assertEqual(first['championIds'], [3])
        self.assertEqual(first['duration'], 1011)
        self.assertEqual(first['createdAt'] * 1000, stamp("2026-10-17T01:53:14+09:00"))

    def text(self, games):
        return ("class R: data\nclass D: game_history\n"
                "class G: created_at,game_length_second,average_tier_info,participants\n"
                "class A: tier,division\nclass P: champion_id\n\n"
                f"R(D([{games}]))")

    def test_divisions_and_missing_tiers(self):
        entries = opgg_history.entriesFromText(self.text(
            'G("2026-01-02T03:04:05+00:00",1800,A("GOLD",4),[P(1)]),'
            'G("2026-01-02T04:04:05+00:00",900,None,[P(2)]),'
            'G("2026-01-02T05:04:05+00:00",900,A("emerald",1),[]),'
            'G("not a date",900,A("GOLD",2),[P(3)]),'
            'G("2026-01-02T06:04:05+00:00",null,A("WOOD",2),[P(4)]),'
            'G("2026-01-02T07:04:05+00:00",90,A("MASTER",1),[P(5),P(6)])'))
        self.assertEqual([(e['tier'], e['division'], e['championIds']) for e in entries],
                         [('GOLD', 'IV', [1]), ('EMERALD', 'I', []), ('MASTER', '', [5, 6])])

    def test_strings_with_escapes_and_brackets(self):
        parsed = opgg_history.parseText(
            'class A: name,list\n\nA("a \\"quoted\\", (b) [c]",[1,-2.5,true,"x"])')
        self.assertEqual(parsed, {'name': 'a "quoted", (b) [c]', 'list': [1, -2.5, True, 'x']})

    def test_bad_text_raises_value_error(self):
        for text in ("", "class A: x\n", "class A: x\n\nA(1", "class A: x\n\nB(1)",
                     "class A: x\n\nA(1) trailing", "class A: x\n\nA(1,2)",
                     "Summoner not found", "class A: x\n\nA(what)", "class A: x\n\nA(\"open"):
            with self.assertRaises(ValueError, msg=text):
                opgg_history.parseText(text)
        with self.assertRaises(ValueError):
            opgg_history.entriesFromText("class A: x\n\nA(1)")

    def test_regions(self):
        self.assertEqual(opgg_history.opggRegion('NA1'), 'NA')
        self.assertEqual(opgg_history.opggRegion('kr'), 'KR')
        self.assertEqual(opgg_history.opggRegion('EUN1'), 'EUNE')
        for server in ('HN1', 'HN10', 'BGP2', 'TJ100', 'PBE1', '', None):
            self.assertIsNone(opgg_history.opggRegion(server), server)

    def test_match_by_time_and_champion(self):
        entries = opgg_history.entriesFromText(opggText())
        created = stamp("2026-10-17T01:32:01+09:00")
        hit = opgg_history.matchEntry(entries, {'timeStamp': created + 90_000, 'championId': 7})
        self.assertEqual(hit['duration'], 1452)
        # another champion, too far away in time, or no time: not this game
        self.assertIsNone(opgg_history.matchEntry(
            entries, {'timeStamp': created, 'championId': 8}))
        self.assertIsNone(opgg_history.matchEntry(
            entries, {'timeStamp': created + 11 * 60_000, 'championId': 7}))
        self.assertIsNone(opgg_history.matchEntry(entries, {'championId': 7}))
        self.assertIsNone(opgg_history.matchEntry([], {'timeStamp': created, 'championId': 7}))

    def test_the_nearest_game_wins(self):
        entries = [{'createdAt': 1000, 'championIds': [1], 'tier': 'GOLD', 'division': 'I'},
                   {'createdAt': 1400, 'championIds': [1], 'tier': 'IRON', 'division': 'I'}]
        self.assertEqual(opgg_history.matchEntry(
            entries, {'timeStamp': 1300_000, 'championId': 1})['tier'], 'IRON')


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self.body


class FakeSession:
    def __init__(self, status=200, body=None):
        self.response = FakeResponse(status, body)
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.response


class OpggFetchTest(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch.dict(opgg_history._cache, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_request_and_cache(self):
        session = FakeSession(body=json.loads(FIXTURE.read_text(encoding="utf-8")))
        fetch = opgg_history.fetchAverageTiers
        entries = asyncio.run(fetch("Some One", "TAG", "KR", session))
        self.assertEqual(len(entries), 5)
        url, kwargs = session.posts[0]
        self.assertEqual(url, "https://mcp-api.op.gg/mcp")
        arguments = kwargs['json']['params']['arguments']
        self.assertEqual(kwargs['json']['params']['name'], "lol_list_summoner_matches")
        self.assertEqual((arguments['game_name'], arguments['tag_line'], arguments['region']),
                         ("Some One", "TAG", "KR"))
        self.assertIn("data.game_history[].average_tier_info.{division,tier}",
                      arguments['desired_output_fields'])
        # the same player again: no second request; another player: one more
        self.assertIs(asyncio.run(fetch("Some One", "TAG", "KR", session)), entries)
        asyncio.run(fetch("Other", "TAG", "KR", session))
        self.assertEqual(len(session.posts), 2)

    def test_failures_raise_and_are_not_cached(self):
        for session in (FakeSession(status=500, body={}),
                        FakeSession(body={'error': {'code': -32000}}),
                        FakeSession(body={'result': {'isError': True, 'content': []}}),
                        FakeSession(body={'result': {'content': []}}),
                        FakeSession(body={'result': {'content': [
                            {'type': 'text', 'text': 'Summoner not found'}]}}),
                        FakeSession(body=[])):
            with self.assertRaises(ValueError):
                asyncio.run(opgg_history.fetchAverageTiers("A", "B", "KR", session))
        self.assertEqual(opgg_history._cache, {})


class SourceTest(_StateMixin, unittest.TestCase):
    """OP.GG first, then the snapshot taken when the game started, then the estimate"""

    CREATED = "2026-10-17T01:32:01+09:00"

    def opgg(self, result):
        calls = []

        async def fetch(name, tagLine, region):
            calls.append((name, tagLine, region))
            if isinstance(result, Exception):
                raise result
            return result

        patcher = mock.patch.object(opgg_history, 'fetchAverageTiers', fetch)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_opgg_value_is_used_without_any_rank_lookup(self):
        fake = self.connect({'a': stats(('IRON', 'IV')), 'b': stats(('IRON', 'IV'))}, 'NA1')
        calls = self.opgg(opgg_history.entriesFromText(opggText()))
        hit = game(['a', 'b'], timeStamp=stamp(self.CREATED), championId=7, remake=True)
        miss = game(['a', 'b'], gameId=2, timeStamp=stamp(self.CREATED) - 86_400_000,
                    championId=7)
        seen = []
        asyncio.run(avg_rank.attachAverageRanks([hit, miss], seen.append, ("Me", "NA1")))
        self.assertEqual(calls, [("Me", "NA1", "NA")])
        # OP.GG's own value, also for a remake
        self.assertEqual(hit['avgRank'],
                         {'tier': 'GRANDMASTER', 'division': '', 'source': 'opgg'})
        self.assertEqual(miss['avgRank']['source'], 'estimate')
        self.assertEqual(miss['avgRank']['tier'], 'IRON')
        self.assertEqual(sorted(fake.calls), ['a', 'b'])
        self.assertEqual(seen, [hit, miss])

    def test_no_rank_lookup_when_opgg_covers_every_game(self):
        fake = self.connect({}, 'KR')
        self.opgg(opgg_history.entriesFromText(opggText()))
        hit = game(['a', 'b'], timeStamp=stamp(self.CREATED), championId=7)
        asyncio.run(avg_rank.attachAverageRanks([hit], None, ("Me", "KR1")))
        self.assertEqual(hit['avgRank']['source'], 'opgg')
        self.assertEqual(fake.calls, [])

    def test_no_opgg_request_without_region_or_riot_id(self):
        calls = self.opgg([])
        for server, riotId in (('HN1', ("Me", "1234")), (None, ("Me", "1234")),
                               ('NA1', None), ('NA1', ("Me", "")), ('NA1', ("", "NA1"))):
            self.connect({'a': stats(('GOLD', 'I')), 'b': stats()}, server)
            g = game(['a', 'b'])
            asyncio.run(avg_rank.attachAverageRanks([g], None, riotId))
            self.assertEqual(g['avgRank']['source'], 'estimate')
        self.assertEqual(calls, [])

    def test_opgg_failure_falls_back_and_logs_the_type_name_only(self):
        self.connect({'a': stats(('GOLD', 'I')), 'b': stats()}, 'KR')
        self.opgg(RuntimeError("Bearer secret"))
        g = game(['a', 'b'])
        with mock.patch.object(avg_rank.logger, 'warning') as warning:
            asyncio.run(avg_rank.attachAverageRanks([g], None, ("Me", "KR1")))
        self.assertEqual(g['avgRank']['source'], 'estimate')
        text = " ".join(str(call.args[0]) for call in warning.call_args_list)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("secret", text)

    def session(self, gameId=77, queueId=420, one=('a', 'b'), two=('c', 'd')):
        return {'gameData': {'gameId': gameId, 'queue': {'id': queueId},
                             'teamOne': [{'puuid': p} for p in one],
                             'teamTwo': [{'puuid': p} for p in two]}}

    def test_snapshot_taken_at_game_start_beats_the_estimate(self):
        fake = self.connect({'a': stats(('GOLD', 'IV'), ('SILVER', 'I')),
                             'b': stats(('GOLD', 'II')), 'c': stats(), 'd': None})
        asyncio.run(avg_rank.snapshotGame(self.session()))
        stored = json.loads(pathlib.Path(avg_rank.storePath).read_text(encoding="utf-8"))
        # rank values only, no player ids
        self.assertEqual(stored['games']['77']['RANKED_SOLO_5x5'], [12, 14, None, None])
        self.assertEqual(sorted(stored['games']['77']),
                         ['RANKED_FLEX_SR', 'RANKED_SOLO_5x5', 't'])

        # later everybody climbed: the row still shows the ranks of that day
        fake.table.update({p: stats(('DIAMOND', 'I'), ('DIAMOND', 'I')) for p in 'abcd'})
        avg_rank._ranks.clear()
        calls = len(fake.calls)
        solo, flex = game(list('abcd'), gameId=77), game(list('abcd'), 440, gameId=77)
        other = game(list('abcd'), gameId=78)
        with mock.patch.object(avg_rank, '_store', None):   # as after a restart
            asyncio.run(avg_rank.attachAverageRanks([solo]))
            asyncio.run(avg_rank.attachAverageRanks([flex]))
            self.assertEqual(len(fake.calls), calls)
            asyncio.run(avg_rank.attachAverageRanks([other]))
        self.assertEqual(solo['avgRank'], {'tier': 'GOLD', 'division': 'III', 'players': 2,
                                           'total': 4, 'source': 'snapshot'})
        self.assertEqual(flex['avgRank'], {'tier': 'SILVER', 'division': 'I', 'players': 1,
                                           'total': 4, 'source': 'snapshot'})
        self.assertEqual(other['avgRank']['source'], 'estimate')
        self.assertEqual(other['avgRank']['tier'], 'DIAMOND')

    def test_nothing_is_recorded_for_games_that_do_not_count(self):
        fake = self.connect({p: stats(('GOLD', 'I')) for p in 'abcd'})
        for session in (self.session(queueId=0), self.session(queueId=840),
                        self.session(gameId=None), self.session(two=()),
                        self.session(two=('c', BOT)), self.session(two=('c', None)),
                        {}, None, {'gameData': None}):
            asyncio.run(avg_rank.snapshotGame(session))
        self.assertEqual(fake.calls, [])
        self.assertFalse(os.path.exists(avg_rank.storePath))

    def test_failed_lookups_and_a_second_start_do_not_overwrite(self):
        fake = self.connect({})
        asyncio.run(avg_rank.snapshotGame(self.session()))
        self.assertFalse(os.path.exists(avg_rank.storePath))   # every lookup failed
        fake.table.update({p: stats(('GOLD', 'I')) for p in 'abcd'})
        asyncio.run(avg_rank.snapshotGame(self.session()))
        fake.table.update({p: stats(('IRON', 'I')) for p in 'abcd'})
        avg_rank._ranks.clear()
        calls = len(fake.calls)
        asyncio.run(avg_rank.snapshotGame(self.session()))   # reconnect to the same game
        self.assertEqual(len(fake.calls), calls)
        self.assertEqual(avg_rank.snapshotRank({'gameId': 77, 'queueId': 420})['tier'], 'GOLD')

    def test_store_is_capped_and_survives_bad_files(self):
        with mock.patch.object(avg_rank, 'MAX_SNAPSHOTS', 3):
            for gameId in range(5):
                avg_rank.saveSnapshot(gameId, [{'RANKED_SOLO_5x5': 12}], now=gameId)
        self.assertEqual(sorted(avg_rank._loadStore()), ['2', '3', '4'])
        for content in ("not json", "[]", '{"games": 3}', '{"games": {"9": 5}}',
                        '{"games": {"9": {"RANKED_SOLO_5x5": ["x", null]}}}'):
            pathlib.Path(avg_rank.storePath).write_text(content, encoding="utf-8")
            with mock.patch.object(avg_rank, '_store', None):
                self.assertIsNone(avg_rank.snapshotRank({'gameId': 9, 'queueId': 420}), content)

    def test_an_exception_while_recording_is_logged_by_type_name_only(self):
        self.connect({'a': RuntimeError("token=secret"), 'b': stats(), 'c': stats(),
                      'd': stats()})
        with mock.patch.object(avg_rank.logger, 'warning') as warning:
            asyncio.run(avg_rank.snapshotGame(self.session()))
        text = " ".join(str(call.args[0]) for call in warning.call_args_list)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("secret", text)


class RowTest(unittest.TestCase):
    """the extra line of CareerGameRow"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def row(self, **extra):
        from app.components.career_widgets import CareerGameRow
        data = {'gameId': 7, 'queueId': 420, 'name': 'Ranked Solo/Duo', 'remake': False,
                'win': True, 'championIcon': '', 'spell1Icon': '', 'spell2Icon': '',
                'itemIcons': [''] * 7, 'itemIds': [0] * 7, 'timeStamp': 0, 'champLevel': 18,
                'kills': 1, 'deaths': 2, 'assists': 3, 'kp': 0.5, 'cs': 100, 'gold': 9000,
                'durationSecs': 1800, 'participants': [], **extra}
        row = CareerGameRow(data, 'me')
        self.addCleanup(row.deleteLater)
        row.resize(1200, CareerGameRow.HEIGHT)
        return row

    def paint(self, row):
        return row._CareerGameRow__content()

    def test_no_line_without_data(self):
        row = self.row()
        self.paint(row)
        self.assertEqual(row.avgRankText, "")
        self.assertTrue(row.avgRankRect.isNull())

    def test_line_appears_when_the_data_arrives(self):
        row = self.row()
        before = self.paint(row).toImage()
        row.game['avgRank'] = {'tier': 'GOLD', 'division': 'IV', 'players': 9, 'total': 10,
                               'source': 'snapshot'}
        row.refreshAvgRank()
        after = self.paint(row).toImage()
        self.assertEqual(row.avgRankText, "Gold IV")
        self.assertFalse(row.avgRankPixmap.isNull())
        self.assertFalse(row.avgRankRect.isNull())
        self.assertIn("Gold IV", row.avgRankTip)
        self.assertIn("recorded when the game started (9 of 10", row.avgRankTip)
        self.assertNotEqual(before, after)

    def test_an_estimate_is_marked(self):
        row = self.row(avgRank={'tier': 'GOLD', 'division': 'II', 'players': 3, 'total': 10,
                                'source': 'estimate'})
        self.assertEqual(row.avgRankText, "≈ Gold II")
        self.assertIn("Estimate from the players' current ranks (3 of 10", row.avgRankTip)
        self.assertIn("≈ Gold II", row.avgRankTip)
        for source in ('opgg', 'snapshot'):
            exact = self.row(avgRank={'tier': 'GOLD', 'division': 'II', 'players': 3,
                                      'total': 10, 'source': source})
            self.assertEqual(exact.avgRankText, "Gold II", source)
            self.assertNotIn("Estimate", exact.avgRankTip)

    def test_every_line_fits_the_row(self):
        row = self.row(laning={'mine': 53, 'theirs': 47, 'position': 'MIDDLE'},
                       avgRank={'tier': 'GRANDMASTER', 'division': '', 'source': 'opgg'})
        self.paint(row)
        self.assertEqual(row.avgRankText, "Grandmaster")
        self.assertIn("OP.GG", row.avgRankTip)
        self.assertGreaterEqual(row.statsRect.top(), 0)
        self.assertGreater(row.avgRankRect.top(), row.laningRect.top())
        self.assertLessEqual(row.avgRankRect.bottom(), row.height())


if __name__ == "__main__":
    unittest.main()
