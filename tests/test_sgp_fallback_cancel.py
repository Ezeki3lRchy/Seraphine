"""SGP-first orchestrators in tools: cancellation vs. the LCU fallback.

parseGameInfoByGameflowSession and parseAllyGameInfo try SGP first and fall back to
LCU on failure. Cancelling the load while the SGP attempt is running must stop it
(CancelledError propagates, no fallback), while an ordinary error - including the
app's own exceptions, which subclass BaseException - still falls back. Only the
per-summoner fetchers (or the connector calls under them) and
connector.isInTencent are replaced; the orchestrators, gatherProgressively and
build run for real.
"""
import asyncio
import logging
import unittest
from unittest import mock

from tests import LOG_RECORDS
from app.lol import tools
from app.lol.exceptions import RetryMaximumAttempts, SummonerGamesNotFound

MARKER = "SECRET-MARKER-123"


class LeakyError(Exception):
    """repr/str carry data that must never reach the log (like aiohttp's RequestInfo)"""
    status = 401

    def __repr__(self):
        return f"LeakyError(headers={{'Authorization': '{MARKER}'}})"

    __str__ = __repr__


def member(i):
    return {"cellId": i, "puuid": f"p{i}", "summonerId": 10 + i, "championId": 0}


def result(item):
    return {"puuid": item["puuid"], "summonerId": item["summonerId"],
            "championId": 0, "cellId": item["cellId"]}


def debugLogging(test):
    """the Seraphine logger level comes from the local config (default ERROR)"""
    seraphine = logging.getLogger("Seraphine")
    test.addCleanup(seraphine.setLevel, seraphine.level)
    seraphine.setLevel(logging.DEBUG)


def patchAll(test, patches):
    for target, name, value in patches:
        p = mock.patch.object(target, name, value)
        p.start()
        test.addCleanup(p.stop)


class _FallbackMixin:

    def setUp(self):
        debugLogging(self)
        LOG_RECORDS.clear()
        self.calls = []
        self.sgpError = None

        async def sgp(item, queueId, currentSummonerId):
            self.calls.append(("sgp", item["puuid"]))
            if self.sgpError is not None:
                raise self.sgpError
            await asyncio.sleep(3600)  # stays "in flight" until cancelled

        async def lcu(item, queueId, currentSummonerId):
            self.calls.append(("lcu", item["puuid"]))
            return result(item)

        patchAll(self, self.patches(sgp, lcu))

    def patches(self, sgp, lcu):
        return [(tools, "getSummonerGamesInfoViaSGP", sgp),
                (tools, "parseSummonerGameInfo", lcu),
                (tools.connector, "isInTencent", lambda: True)]

    def run_cancelled(self):
        async def main():
            task = asyncio.ensure_future(self.orchestrate())
            while not any(kind == "sgp" for kind, _ in self.calls):
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        asyncio.run(main())

    def assert_cancel_stops(self):
        self.run_cancelled()
        self.assertTrue(self.calls)
        self.assertFalse([c for c in self.calls if c[0] == "lcu"])
        self.assertFalse([r for r in LOG_RECORDS if "falling back" in r])

    def assert_error_falls_back(self, error, logged):
        self.sgpError = error
        out = asyncio.run(self.orchestrate())
        self.assertEqual(sorted(s["puuid"] for s in out["summoners"]),
                         sorted(m["puuid"] for m in self.team))
        self.assertEqual(sorted(p for kind, p in self.calls if kind == "lcu"),
                         sorted(m["puuid"] for m in self.team))
        lines = [r for r in LOG_RECORDS if "falling back" in r]
        self.assertEqual(len(lines), 1)
        self.assertIn("WARNING - [Tools]", lines[0])
        self.assertIn(logged, lines[0])
        return lines[0]

    def test_cancel_during_sgp_propagates_without_fallback(self):
        self.assert_cancel_stops()

    def test_error_during_sgp_still_falls_back_to_lcu(self):
        self.assert_error_falls_back(RuntimeError("sgp down"), "RuntimeError, status=None")

    def test_retry_exhausted_during_sgp_still_falls_back_to_lcu(self):
        # RetryMaximumAttempts subclasses BaseException, not Exception
        self.assert_error_falls_back(RetryMaximumAttempts("x"), "RetryMaximumAttempts")

    def test_log_line_carries_only_type_and_status(self):
        line = self.assert_error_falls_back(LeakyError(), "LeakyError, status=401")
        self.assertNotIn(MARKER, line)
        self.assertFalse([r for r in LOG_RECORDS if MARKER in r])


class GameflowSessionFallbackTest(_FallbackMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.team = [member(i) for i in range(3)]
        self.session = {"gameData": {"queue": {"id": 430},
                                     "teamOne": self.team,
                                     "teamTwo": [member(i) for i in range(5, 8)]}}

    def orchestrate(self):
        return tools.parseGameInfoByGameflowSession(
            self.session, self.team[0]["summonerId"], "ally", useSGP=True)


class AllyGameInfoFallbackTest(_FallbackMixin, unittest.TestCase):

    def patches(self, sgp, lcu):
        async def fill(team):
            self.calls.append(("fill", None))

        async def reveal(team, currentPuuid):
            return team

        return super().patches(sgp, lcu) + [
            (tools, "fillRevealedSummonerIds", fill),
            (tools, "revealHiddenAllies", reveal)]

    def setUp(self):
        super().setUp()
        self.team = [member(i) for i in range(3)]
        self.session = {"myTeam": self.team}

    def orchestrate(self):
        return tools.parseAllyGameInfo(self.session, self.team[0]["summonerId"],
                                       useSGP=True, currentPuuid="p0")

    def test_cancel_during_sgp_propagates_without_fallback(self):
        super().test_cancel_during_sgp_propagates_without_fallback()
        self.assertNotIn(("fill", None), self.calls)

    def test_error_during_sgp_still_falls_back_to_lcu(self):
        super().test_error_during_sgp_still_falls_back_to_lcu()
        self.assertIn(("fill", None), self.calls)


class LcuGamesNotFoundTest(unittest.TestCase):
    """the real parseSummonerGameInfo: one player's history failing empties only that player"""

    def setUp(self):
        debugLogging(self)
        LOG_RECORDS.clear()
        c = tools.connector

        async def summonerById(summonerId):
            return {"puuid": f"p{summonerId - 10}", "gameName": f"n{summonerId}",
                    "tagLine": "T", "summonerLevel": 1, "xpSinceLastLevel": 0,
                    "xpUntilNextLevel": 1, "privacy": "PUBLIC"}

        async def icon(championId):
            return "icon"

        async def mastery(puuid):
            return {}

        async def ranked(puuid):
            return None

        async def games(puuid, begIdx, endIdx):
            if puuid == "p1":
                raise SummonerGamesNotFound()
            return {"games": []}

        patchAll(self, [(c, "getSummonerById", summonerById),
                        (c, "getChampionIcon", icon),
                        (c, "getChampionMasteryQuietly", mastery),
                        (c, "getRankedStatsByPuuid", ranked),
                        (c, "getSummonerGamesByPuuid", games),
                        (c, "isInTencent", lambda: False),
                        (tools, "historyQueueFilter", lambda queueId: None)])

    def test_games_not_found_for_one_player_keeps_the_team(self):
        team = [member(i) for i in range(3)]
        session = {"gameData": {"queue": {"id": 430}, "teamOne": team,
                                "teamTwo": [member(i) for i in range(5, 8)]}}

        out = asyncio.run(tools.parseGameInfoByGameflowSession(
            session, team[0]["summonerId"], "ally", useSGP=False))

        self.assertEqual([s["puuid"] for s in out["summoners"]], ["p0", "p1", "p2"])
        failed = next(s for s in out["summoners"] if s["puuid"] == "p1")
        self.assertEqual(failed["gamesInfo"], [])
        self.assertEqual(failed["totalGames"], 0)
        lines = [r for r in LOG_RECORDS if "games fetch failed" in r]
        self.assertEqual(len(lines), 1)
        self.assertIn("SummonerGamesNotFound", lines[0])


class ParseSummonerDataGamesTest(unittest.TestCase):
    """parseSummonerData (career page): a failing games task falls back to an empty
    history, a cancelled one propagates"""

    def setUp(self):
        debugLogging(self)
        LOG_RECORDS.clear()

        async def profileIcon(iconId):
            return "icon"

        patchAll(self, [(tools.connector, "getProfileIcon", profileIcon)])
        self.summoner = {"profileIconId": 1, "summonerLevel": 30, "xpSinceLastLevel": 0,
                         "xpUntilNextLevel": 1, "gameName": "n", "puuid": "p",
                         "privacy": "PUBLIC", "tagLine": "t"}

    def parse(self, gameTask):
        async def rank():
            return {}

        async def main():
            return await tools.parseSummonerData(
                self.summoner, asyncio.ensure_future(rank()), asyncio.ensure_future(gameTask()))
        return asyncio.run(main())

    def test_error_falls_back_to_empty_games(self):
        for error in (LeakyError(), SummonerGamesNotFound()):
            LOG_RECORDS.clear()

            async def games():
                raise error

            out = self.parse(games)
            self.assertEqual(out["games"], {})
            self.assertEqual(out["champions"], [])
            lines = [r for r in LOG_RECORDS if "parseSummonerData" in r]
            self.assertEqual(len(lines), 1)
            self.assertIn("WARNING - [Tools]", lines[0])
            self.assertIn(type(error).__name__, lines[0])
            self.assertNotIn(MARKER, lines[0])

    def test_cancelled_games_task_propagates(self):
        async def games():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            self.parse(games)


if __name__ == "__main__":
    unittest.main()
