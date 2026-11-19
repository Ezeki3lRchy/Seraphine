"""OP.GG-style game-info data helpers in app.lol.tools.

summarizeGames / getPositionKey / translatePositionKey and the season W/L kept by
parseRankInfo / parseRankInfoFromSGP are pure functions over already-fetched
data, so they run for real on hand-built inputs.
"""
import asyncio
import unittest

from app.lol import tools


def game(championId, win, k=1, d=1, a=1, remake=False, positionKey=None):
    return {"championId": championId, "championIcon": f"icon-{championId}",
            "win": win, "remake": remake, "kills": k, "deaths": d,
            "assists": a, "positionKey": positionKey}


class SummarizeGamesTest(unittest.TestCase):

    def test_empty(self):
        s = tools.summarizeGames([])
        self.assertEqual(s["topChampions"], [])
        self.assertEqual(s["positions"], [])
        self.assertIsNone(s["streak"])
        self.assertIsNone(s["currentChampion"])

    def test_none_entries_and_remakes_are_ignored(self):
        games = [None, game(1, False, remake=True), game(1, True)]
        s = tools.summarizeGames(games, championId=1)
        self.assertEqual(s["topChampions"][0]["games"], 1)
        self.assertEqual(s["currentChampion"]["wins"], 1)
        self.assertEqual(s["streak"], ("win", 1))

    def test_top_champions_ordered_by_games_capped_at_three(self):
        games = [game(5, True), game(7, True), game(7, False), game(9, True),
                 game(9, True), game(9, False), game(11, True)]
        top = tools.summarizeGames(games)["topChampions"]
        self.assertEqual([c["championId"] for c in top], [9, 7, 5])
        self.assertEqual((top[0]["wins"], top[0]["losses"]), (2, 1))
        self.assertEqual(top[0]["icon"], "icon-9")

    def test_tie_keeps_most_recent_first(self):
        top = tools.summarizeGames([game(3, True), game(4, True)])["topChampions"]
        self.assertEqual([c["championId"] for c in top], [3, 4])

    def test_kda_uses_one_when_no_deaths(self):
        top = tools.summarizeGames(
            [game(1, True, k=4, d=0, a=2)])["topChampions"]
        self.assertAlmostEqual(top[0]["kda"], 6.0)

    def test_kda_aggregates_totals(self):
        top = tools.summarizeGames([game(1, True, 2, 1, 3),
                                    game(1, False, 0, 3, 1)])["topChampions"]
        self.assertAlmostEqual(top[0]["kda"], (2 + 0 + 3 + 1) / 4)

    def test_streak_counts_leading_results(self):
        games = [game(1, False), game(1, False), game(1, False), game(1, True)]
        self.assertEqual(tools.summarizeGames(games)["streak"], ("lose", 3))

    def test_streak_skips_remakes(self):
        games = [game(1, True), game(1, False, remake=True), game(1, True)]
        self.assertEqual(tools.summarizeGames(games)["streak"], ("win", 2))

    def test_positions_sorted_by_count(self):
        games = [game(1, True, positionKey="MIDDLE"), game(1, True, positionKey="TOP"),
                 game(1, True, positionKey="MIDDLE"), game(1, True)]
        self.assertEqual(tools.summarizeGames(games)["positions"],
                         [("MIDDLE", 2), ("TOP", 1)])

    def test_current_champion_absent(self):
        s = tools.summarizeGames([game(1, True)], championId=2)
        self.assertIsNone(s["currentChampion"])

    def test_current_champion_ignored_when_zero(self):
        s = tools.summarizeGames([game(1, True)], championId=0)
        self.assertIsNone(s["currentChampion"])


class PositionKeyTest(unittest.TestCase):

    def test_ranked_lanes(self):
        cases = [("TOP", "SOLO", "TOP"), ("JUNGLE", "NONE", "JUNGLE"),
                 ("MIDDLE", "SOLO", "MIDDLE"), ("BOTTOM", "CARRY", "BOTTOM"),
                 ("BOTTOM", "SUPPORT", "UTILITY")]
        for lane, role, expected in cases:
            self.assertEqual(tools.getPositionKey(420, lane, role), expected)
            self.assertEqual(tools.getPositionKey(440, lane, role), expected)

    def test_non_ranked_or_unknown(self):
        self.assertIsNone(tools.getPositionKey(450, "MIDDLE", "SOLO"))
        self.assertIsNone(tools.getPositionKey(420, "NONE", "DUO"))

    def test_translate(self):
        self.assertIsNone(tools.translatePositionKey(None))
        self.assertEqual(tools.translatePositionKey("MIDDLE"), "MID")
        self.assertEqual(tools.translatePositionKey("UTILITY"), "SUP")


class SeasonRecordTest(unittest.TestCase):

    def test_lcu_rank_keeps_wins_losses(self):
        info = {"queueMap": {
            "RANKED_SOLO_5x5": {"tier": "DIAMOND", "division": "II",
                                "leaguePoints": 95, "wins": 52, "losses": 40},
            "RANKED_FLEX_SR": {"tier": "", "division": "NA",
                               "leaguePoints": 0, "wins": 0, "losses": 0},
        }}
        r = tools.parseRankInfo(info)
        self.assertEqual((r["solo"]["wins"], r["solo"]["losses"]), (52, 40))
        self.assertEqual((r["flex"]["wins"], r["flex"]["losses"]), (0, 0))

    def test_sgp_rank_keeps_wins_losses(self):
        info = {"queues": [
            {"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "I",
             "leaguePoints": 10, "wins": 7, "losses": 3},
        ]}
        r = tools.parseRankInfoFromSGP(info)
        self.assertEqual((r["solo"]["wins"], r["solo"]["losses"]), (7, 3))
        self.assertEqual((r["flex"]["wins"], r["flex"]["losses"]), (0, 0))

    def test_no_rank_info_defaults_to_zero(self):
        r = tools.parseRankInfo(None)
        self.assertEqual((r["solo"]["wins"], r["solo"]["losses"]), (0, 0))




class MergeQueuePageTest(unittest.TestCase):
    """Queue-filter paging: the LCU repeats already-returned games past the end
    of a player's history; those must not be appended again."""

    @staticmethod
    def key(game):
        return game["q"], game["id"]

    def test_skips_seen_and_other_queues(self):
        games, seen = [], {1}
        page = [{"q": 420, "id": 1}, {"q": 450, "id": 2}, {"q": 440, "id": 3},
                {"q": 420, "id": 4}]
        self.assertTrue(tools.mergeQueuePage(games, page, seen, self.key, 420))
        self.assertEqual([g["id"] for g in games], [4])
        self.assertEqual(seen, {1, 2, 3, 4})

    def test_page_with_only_old_games_reports_no_progress(self):
        games, seen = [], {1, 2}
        page = [{"q": 420, "id": 1}, {"q": 420, "id": 2}]
        self.assertFalse(tools.mergeQueuePage(games, page, seen, self.key, 420))
        self.assertEqual(games, [])

    def test_new_page_of_other_queues_still_counts_as_progress(self):
        games, seen = [], set()
        self.assertTrue(tools.mergeQueuePage(
            games, [{"q": 450, "id": 9}], seen, self.key, 420))
        self.assertEqual(games, [])


class CollectQueueGamesTest(unittest.TestCase):
    """Only the current queue's games are kept (Solo/Duo must not show Flex)."""

    key = staticmethod(MergeQueuePageTest.key)

    def collect(self, firstPage, pages, queueId, want=11):
        calls = []

        async def fetchPage(begIdx, endIdx):
            calls.append((begIdx, endIdx))
            return pages.pop(0) if pages else []

        games = asyncio.run(tools.collectQueueGames(
            firstPage, fetchPage, self.key, queueId, 5, want))
        return games, calls

    def test_solo_drops_flex_and_pages_until_enough(self):
        first = [{"q": 420, "id": 1}, {"q": 440, "id": 2}, {"q": 420, "id": 3}]
        pages = [[{"q": 440, "id": 4}, {"q": 420, "id": 5}]]
        games, calls = self.collect(first, pages, 420, want=3)
        self.assertEqual([g["id"] for g in games], [1, 3, 5])
        self.assertEqual(calls, [(15, 20)])

    def test_enough_on_first_page_fetches_nothing(self):
        first = [{"q": 440, "id": 1}, {"q": 440, "id": 2}]
        games, calls = self.collect(first, [], 440, want=2)
        self.assertEqual([g["id"] for g in games], [1, 2])
        self.assertEqual(calls, [])

    def test_stops_when_history_runs_out(self):
        first = [{"q": 420, "id": 1}]
        games, calls = self.collect(first, [[{"q": 420, "id": 1}]], 420)
        self.assertEqual([g["id"] for g in games], [1])
        self.assertEqual(len(calls), 1)

    def test_ranked_with_no_games_stays_empty(self):
        first = [{"q": 440, "id": 1}, {"q": 450, "id": 2}]
        games, _ = self.collect(first, [], 420)
        self.assertEqual(games, [])

    def test_other_queue_with_no_games_falls_back_to_recent(self):
        first = [{"q": 420, "id": 1}, {"q": 440, "id": 2}]
        games, _ = self.collect(first, [], 450)
        self.assertEqual(games, first)


class HistoryQueueFilterTest(unittest.TestCase):

    def setUp(self):
        self._orig = tools.cfg.get(tools.cfg.gameInfoFilter)

    def tearDown(self):
        tools.cfg.gameInfoFilter.value = self._orig

    def test_on_filters_matchmade_queues_only(self):
        tools.cfg.gameInfoFilter.value = True
        self.assertEqual(tools.historyQueueFilter(420), 420)
        self.assertEqual(tools.historyQueueFilter(450), 450)
        for queueId in (0, -1, None):
            self.assertIsNone(tools.historyQueueFilter(queueId))

    def test_off_never_filters(self):
        tools.cfg.gameInfoFilter.value = False
        self.assertIsNone(tools.historyQueueFilter(420))


if __name__ == "__main__":
    unittest.main()
