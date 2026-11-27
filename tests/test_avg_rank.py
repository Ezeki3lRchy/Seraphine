"""Average rank of a game (app/lol/avg_rank.py) and its line in the career match row."""
import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.lol import avg_rank

BOT = avg_rank.BOT_PUUID


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


class FakeConnector:
    def __init__(self, table):
        self.table = table
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


class LookupTest(unittest.TestCase):

    def setUp(self):
        for name, value in (('_ranks', {}), ('_pending', {}), ('_semaphore', None)):
            patcher = mock.patch.object(avg_rank, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def connect(self, table):
        fake = FakeConnector(table)
        patcher = mock.patch.object(avg_rank, 'connector', fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_queue_decides_which_rank_is_used(self):
        self.connect({'a': stats(('GOLD', 'IV'), ('DIAMOND', 'I')),
                      'b': stats(('GOLD', 'II'), None)})
        solo, flex, normal = game(['a', 'b'], 420, 1), game(['a', 'b'], 440, 2), \
            game(['a', 'b'], 430, 3)
        asyncio.run(avg_rank.attachAverageRanks([solo, flex, normal]))
        self.assertEqual(solo['avgRank'],
                         {'tier': 'GOLD', 'division': 'III', 'players': 2, 'total': 2})
        self.assertEqual(flex['avgRank'],
                         {'tier': 'DIAMOND', 'division': 'I', 'players': 1, 'total': 2})
        self.assertEqual(normal['avgRank'], solo['avgRank'])

    def test_each_player_is_looked_up_once(self):
        fake = self.connect({p: stats(('SILVER', 'I')) for p in 'abcd'})
        games = [game(['a', 'b', 'c'], gameId=1), game(['a', 'b', 'd'], gameId=2)]
        seen = []
        asyncio.run(avg_rank.attachAverageRanks(games, seen.append))
        self.assertEqual(sorted(fake.calls), ['a', 'b', 'c', 'd'])
        self.assertEqual([g['gameId'] for g in seen], [1, 2])
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
        self.assertEqual(g['avgRank'],
                         {'tier': 'GOLD', 'division': 'I', 'players': 1, 'total': 2})
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
                                   mock.Mock(getRankedStatsByPuuidQuietly=slow)):
                first = asyncio.ensure_future(avg_rank.attachAverageRanks([game(['a', 'b'])]))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                second = game(['a', 'b'], gameId=2)
                later = asyncio.ensure_future(avg_rank.attachAverageRanks([second]))
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
        row.game['avgRank'] = {'tier': 'GOLD', 'division': 'IV', 'players': 9, 'total': 10}
        row.refreshAvgRank()
        after = self.paint(row).toImage()
        self.assertEqual(row.avgRankText, "Gold IV")
        self.assertFalse(row.avgRankPixmap.isNull())
        self.assertFalse(row.avgRankRect.isNull())
        self.assertIn("Gold IV", row.avgRankTip)
        self.assertIn("9 of 10", row.avgRankTip)
        self.assertNotEqual(before, after)

    def test_every_line_fits_the_row(self):
        row = self.row(laning={'mine': 53, 'theirs': 47, 'position': 'MIDDLE'},
                       avgRank={'tier': 'GRANDMASTER', 'division': '', 'players': 10,
                                'total': 10})
        self.paint(row)
        self.assertEqual(row.avgRankText, "Grandmaster")
        self.assertGreaterEqual(row.statsRect.top(), 0)
        self.assertGreater(row.avgRankRect.top(), row.laningRect.top())
        self.assertLessEqual(row.avgRankRect.bottom(), row.height())


if __name__ == "__main__":
    unittest.main()
