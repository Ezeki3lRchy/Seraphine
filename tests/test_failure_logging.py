"""Fallbacks that used to swallow an exception without a trace now log one line.

Each line names what failed and only the exception's type: aiohttp errors carry request
headers (tokens) and messages can carry player names, so repr/str never reach the log.
The fallback behaviour itself is unchanged. Network-facing calls are stand-ins.
"""
import asyncio
import logging
import os
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests import LOG_RECORDS
from app.lol import tools, season_stats, play_style
from app.lol.live_client import LiveClient

MARKER = "SECRET-MARKER-456"


class LeakyError(Exception):
    status = 401

    def __repr__(self):
        return f"LeakyError({MARKER})"

    __str__ = __repr__


def raiser(*args, **kwargs):
    raise LeakyError()


async def asyncRaiser(*args, **kwargs):
    raise LeakyError()


class _LogMixin:

    def setUp(self):
        seraphine = logging.getLogger("Seraphine")
        self.addCleanup(seraphine.setLevel, seraphine.level)
        seraphine.setLevel(logging.DEBUG)
        LOG_RECORDS.clear()

    def patch(self, target, name, value):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

    def assertLogged(self, text, level="WARNING", tag="Tools"):
        lines = [r for r in LOG_RECORDS if text in r]
        self.assertEqual(len(lines), 1, LOG_RECORDS)
        self.assertIn(f"{level} - [{tag}]", lines[0])
        self.assertIn("LeakyError", lines[0])
        self.assertFalse([r for r in LOG_RECORDS if MARKER in r])


class ToolsFailureLoggingTest(_LogMixin, unittest.TestCase):

    def test_apply_last_game_relation(self):
        self.patch(tools, "getTeammates", raiser)
        summoner = {"puuid": "p"}
        self.assertIsNone(tools.applyLastGameRelation(summoner, {"x": 1}, 1))
        self.assertEqual(summoner, {"puuid": "p"})
        self.assertLogged("applyLastGameRelation failed")

    def test_detail_rank_info(self):
        class Bad:
            def get(self, key):
                raise LeakyError()
        self.assertIsNone(tools.detailRankInfo(Bad(), 420))
        self.assertLogged("detailRankInfo")

    def test_build_item_icons_log_at_debug(self):
        async def none(*args):
            return None
        c = tools.connector
        for name in ("getGameSummaryViaSgpQuietly", "getGameDetailByGameIdQuietly",
                     "getGameTimelineQuietly", "getGameTimelineViaSgpQuietly"):
            self.patch(c, name, none)
        self.patch(c, "getItemIcon", asyncRaiser)
        self.patch(tools, "parseGameBuilds", lambda *a: {})
        self.patch(tools, "buildItemIds", lambda builds: [3031])

        out = asyncio.run(tools.loadGameBuildData(1))
        self.assertEqual(out["itemIcons"], {})
        self.assertLogged("item icon failed", level="DEBUG")


class CareerFailureLoggingTest(_LogMixin, unittest.TestCase):
    """CareerInterface background fills, run on a stand-in self"""

    def setUp(self):
        super().setUp()
        from app.view.career_interface import CareerInterface
        self.cls = CareerInterface
        self.view = types.SimpleNamespace(
            puuid="p", performancePanel=mock.Mock(), playStyleView=mock.Mock(),
            championsView=mock.Mock(profiles=[]), championIconTasks=[],
            championIconIds=set(), gameInfoLayout=mock.Mock())

    def method(self, name):
        return getattr(self.cls, f"_CareerInterface__{name}")

    def test_play_style_icons(self):
        self.patch(play_style, "championIcons", asyncRaiser)
        asyncio.run(self.method("loadPlayStyleIcons")(self.view, "p", []))
        self.view.playStyleView.setChampionIcons.assert_not_called()
        self.assertLogged("play style icons failed", tag="CareerInterface")

    def test_champion_view_icons(self):
        self.patch(play_style, "championIcons", asyncRaiser)
        asyncio.run(self.method("loadChampionIcons")(self.view, "p", [1]))
        self.view.championsView.setChampionIcons.assert_not_called()
        self.assertLogged("champion view icons failed", tag="CareerInterface")

    def test_laning_scores(self):
        import app.view.career_interface as career
        self.patch(career, "attachLaningScores", asyncRaiser)
        asyncio.run(self.method("updateLaningScores")(self.view, "p", []))
        self.assertLogged("laning scores failed", tag="CareerInterface")

    def test_kill_participation_fill(self):
        games = [{"gameId": 1}]
        self.patch(season_stats, "fetchSeasonGames",
                   mock.AsyncMock(return_value=(games, "lcu", {})))
        self.patch(season_stats, "aggregate", lambda g: {"games": 1})
        self.patch(season_stats, "fillLcuKp", asyncRaiser)
        self.patch(play_style, "topChampionIds", lambda g: [])
        self.patch(play_style, "championIcons", mock.AsyncMock(return_value={}))

        async def run():
            await self.method("updateSeasonPerformance")(self.view, "p", {}, games)
            await asyncio.sleep(0)  # let the icon task finish

        self.view._CareerInterface__loadPlayStyleIcons = \
            lambda puuid, g: self.method("loadPlayStyleIcons")(self.view, puuid, g)
        self.view._CareerInterface__loadChampionIconIds = lambda ids: None
        asyncio.run(run())
        # the fill failed: the season panel is not set a second time
        self.assertEqual(self.view.performancePanel.setSeason.call_count, 1)
        self.assertLogged("kill participation fill failed", tag="CareerInterface")


class ComposedCardLoggingTest(_LogMixin, unittest.TestCase):

    def test_background_update_failure(self):
        from app.view.game_info_interface import ComposedCardMixin
        card = types.SimpleNamespace(_updateBackgroundColor=raiser)
        ComposedCardMixin.setColor(card, 1, 2, 3, 4)
        self.assertEqual((card.normalBackgroundColor, card._borderColor), (1, 4))
        self.assertLogged("card background update failed", tag="GameInfoInterface")


class ExceptionReprNotLoggedTest(_LogMixin, unittest.TestCase):
    """lines that used to log {e!r}: only the type (and status when there is one) now"""

    def test_partial_update(self):
        async def one():
            return 1
        asyncio.run(tools.gatherProgressively([one()], raiser))
        self.assertLogged("partial update failed", level="ERROR")

    def test_reveal_merge(self):
        self.patch(tools.connector, "getChampSelectChatParticipants", asyncRaiser)
        team = [{"cellId": 0, "puuid": "", "summonerId": 0, "nameVisibilityType": "HIDDEN",
                 "playerType": "PLAYER"}]
        self.assertIs(asyncio.run(tools.revealHiddenAllies(team, "me")), team)
        self.assertLogged("merge failed", level="ERROR")
        line = next(r for r in LOG_RECORDS if "merge failed" in r)
        self.assertIn("status=401", line)
        self.assertIn("asyncRaiser", line)  # the stack frames are kept

    def test_reveal_summoner_id_lookup(self):
        self.patch(tools.connector, "getSummonerByPuuid", asyncRaiser)
        team = [{"puuid": "secret-puuid", "summonerId": 0, "revealed": True}]
        asyncio.run(tools.fillRevealedSummonerIds(team))
        self.assertLogged("summonerId lookup failed", level="ERROR")
        self.assertFalse([r for r in LOG_RECORDS if "secret-puuid" in r])

    def test_live_poll_and_update(self):
        from app.lol.live_client import LiveClientPoller

        async def run(poller):
            poller.start()
            await asyncio.sleep(0.02)
            poller.stop()

        asyncio.run(run(LiveClientPoller(fetch=asyncRaiser, onData=lambda s: None,
                                         interval=0.001)))
        lines = [r for r in LOG_RECORDS if "poll failed" in r]
        self.assertTrue(lines)
        self.assertIn("LeakyError, status=401", lines[0])
        self.assertFalse([r for r in LOG_RECORDS if MARKER in r])

        LOG_RECORDS.clear()
        poller = LiveClientPoller(fetch=asyncRaiser, onData=raiser)
        poller._LiveClientPoller__publish({"gameTime": 1.0})
        self.assertLogged("live client update failed", level="ERROR", tag="LiveClient")

    def test_fill_scores(self):
        import app.view.game_info_interface as gi
        self.patch(gi, "needsScores", lambda s: True)
        self.patch(gi, "attachSummonerScores", asyncRaiser)
        owner = types.SimpleNamespace(_scoring=set())
        view = mock.Mock()

        async def run():
            gi.GameInfoInterface._GameInfoInterface__fillScoresLater(
                owner, view, [{"puuid": "p"}])
            await asyncio.sleep(0.01)

        asyncio.run(run())
        view.refreshScores.assert_not_called()
        self.assertEqual(owner._scoring, set())
        self.assertLogged("fill scores failed", level="ERROR", tag="GameInfoInterface")


class LiveClientCloseLoggingTest(_LogMixin, unittest.TestCase):

    def test_session_close_failure(self):
        client = LiveClient()
        client.session = types.SimpleNamespace(close=asyncRaiser)
        asyncio.run(client.close())
        self.assertIsNone(client.session)
        self.assertLogged("session close failed", tag="LiveClient")


if __name__ == "__main__":
    unittest.main()
