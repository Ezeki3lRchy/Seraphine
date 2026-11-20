"""Custom lobbies load both teams (tools.splitCustomLobbyTeams / parseLobbyGameInfo).

The local player's custom team is the ally side, the other team the enemy side; a
spectator sees team 100 as ally. Bots and spectators are skipped. Only the
network-facing calls are replaced.
"""
import asyncio
import unittest

from app.lol import tools
from app.lol.connector import connector

ME = 1


def member(puuid, summonerId, bot=False, spectator=False):
    return {"puuid": puuid, "summonerId": summonerId, "teamId": 0,
            "isBot": bot, "botId": "Bot_1" if bot else "", "botChampionId": 0,
            "isSpectator": spectator, "isLeader": False}


def lobby(team100=None, team200=None, spectators=None, custom=True, queueId=3120):
    config = {"isCustom": custom, "queueId": queueId,
              "customTeam100": team100, "customTeam200": team200,
              "customSpectators": spectators}
    members = (team100 or []) + (team200 or []) + (spectators or [])
    return {"gameConfig": config, "members": members,
            "localMember": {"puuid": "me", "summonerId": ME}}


def puuids(team):
    return [m["puuid"] for m in team]


class SplitCustomLobbyTeamsTest(unittest.TestCase):

    def test_me_in_team_100(self):
        ally, enemy = tools.splitCustomLobbyTeams(
            lobby([member("me", ME), member("a", 2)], [member("x", 3), member("y", 4)]), ME)
        self.assertEqual((puuids(ally), puuids(enemy)), (["me", "a"], ["x", "y"]))

    def test_me_in_team_200_swaps_sides(self):
        ally, enemy = tools.splitCustomLobbyTeams(
            lobby([member("a", 2), member("b", 5)], [member("x", 3), member("me", ME)]), ME)
        self.assertEqual((puuids(ally), puuids(enemy)), (["x", "me"], ["a", "b"]))

    def test_match_by_puuid(self):
        ally, _ = tools.splitCustomLobbyTeams(
            lobby([member("a", 2)], [member("me", 0)]), 0, "me")
        self.assertEqual(puuids(ally), ["me"])

    def test_spectator_sees_team_100_as_ally(self):
        ally, enemy = tools.splitCustomLobbyTeams(
            lobby([member("a", 2)], [member("x", 3)], [member("me", ME, spectator=True)]), ME)
        self.assertEqual((puuids(ally), puuids(enemy)), (["a"], ["x"]))

    def test_bots_and_spectators_skipped(self):
        team200 = [member("", 0, bot=True), member("x", 3), member("bot", 9, bot=True),
                   member("s", 7, spectator=True), {"puuid": "", "summonerId": 0}, None]
        ally, enemy = tools.splitCustomLobbyTeams(lobby([member("me", ME)], team200), ME)
        self.assertEqual((puuids(ally), puuids(enemy)), (["me"], ["x"]))

    def test_empty_teams(self):
        self.assertEqual(tools.splitCustomLobbyTeams(lobby([member("me", ME)], []), ME),
                         ([member("me", ME)], []))
        self.assertEqual(tools.splitCustomLobbyTeams(lobby([], []), ME), ([], []))

    def test_missing_keys_and_none_lists(self):
        self.assertEqual(tools.splitCustomLobbyTeams(lobby(None, None, None), ME), ([], []))
        self.assertEqual(tools.splitCustomLobbyTeams(
            {"gameConfig": {"isCustom": True}}, ME), ([], []))

    def test_non_custom_lobby_untouched(self):
        self.assertIsNone(tools.splitCustomLobbyTeams(
            lobby([member("me", ME)], [member("x", 3)], custom=False, queueId=420), ME))
        self.assertIsNone(tools.splitCustomLobbyTeams({"members": [member("me", ME)]}, ME))
        self.assertIsNone(tools.splitCustomLobbyTeams({"gameConfig": None}, ME))
        self.assertIsNone(tools.splitCustomLobbyTeams(None, ME))


class LobbyHistoryQueueFilterTest(unittest.TestCase):

    def setUp(self):
        self._orig = tools.cfg.get(tools.cfg.gameInfoFilter)
        tools.cfg.gameInfoFilter.value = True

    def tearDown(self):
        tools.cfg.gameInfoFilter.value = self._orig

    def test_custom_lobby_is_not_filtered(self):
        self.assertEqual(tools.historyQueueFilter(3120), 3120)
        self.assertIsNone(tools.lobbyHistoryQueueFilter(lobby()))

    def test_queue_lobby_filters_as_before(self):
        self.assertEqual(tools.lobbyHistoryQueueFilter(lobby(custom=False, queueId=420)), 420)
        self.assertIsNone(tools.lobbyHistoryQueueFilter({}))


class ParseCustomLobbyTest(unittest.TestCase):

    def setUp(self):
        self._orig = (tools.parseSummonerGameInfo, connector.isInTencent)
        self.fetched = []
        self.queueFilters = []

        async def fetch(item, queueFilter, currentSummonerId):
            self.fetched.append(item["puuid"])
            self.queueFilters.append(queueFilter)
            await asyncio.sleep(0)
            return {"puuid": item["puuid"], "summonerId": item["summonerId"],
                    "championId": 0, "icon": "x", "cellId": item["cellId"]}

        tools.parseSummonerGameInfo = fetch
        connector.isInTencent = lambda: False

    def tearDown(self):
        tools.parseSummonerGameInfo, connector.isInTencent = self._orig

    def refresh(self, data, cache, partials=None):
        ally, enemy = tools.splitCustomLobbyTeams(data, ME)

        def parse(members, side):
            onPartial = (lambda info: partials.append((side, len(info["summoners"])))) \
                if partials is not None else None
            return tools.parseLobbyGameInfo(data, ME, cache=cache, onPartial=onPartial,
                                            members=members)

        async def both():
            return await asyncio.gather(parse(ally, "ally"), parse(enemy, "enemy"))

        return asyncio.run(both())

    def test_both_sides_load_progressively_in_list_order(self):
        data = lobby([member("me", ME), member("a", 2)],
                     [member("y", 4), member("x", 3), member("bot", 0, bot=True)])
        partials = []
        ally, enemy = self.refresh(data, {}, partials)
        self.assertEqual(puuids(ally["summoners"]), ["me", "a"])
        self.assertEqual(ally["order"], [ME, 2])
        self.assertEqual(puuids(enemy["summoners"]), ["y", "x"])
        self.assertEqual(enemy["champions"], {4: 0, 3: 0})
        self.assertEqual(sorted(self.fetched), ["a", "me", "x", "y"])
        self.assertEqual(sorted(partials),
                         [("ally", 1), ("ally", 2), ("enemy", 1), ("enemy", 2)])
        self.assertEqual(set(self.queueFilters), {None})

    def test_team_switch_reuses_cache(self):
        cache = {}
        self.refresh(lobby([member("me", ME), member("a", 2)], [member("x", 3)]), cache)
        self.fetched.clear()

        # a switches to team 200, z joins team 100
        ally, enemy = self.refresh(
            lobby([member("me", ME), member("z", 5)], [member("x", 3), member("a", 2)]), cache)
        self.assertEqual(self.fetched, ["z"])
        self.assertEqual(puuids(ally["summoners"]), ["me", "z"])
        self.assertEqual(puuids(enemy["summoners"]), ["x", "a"])
        self.assertEqual([s["cellId"] for s in enemy["summoners"]], [0, 1])

    def test_last_enemy_left_gives_empty_side(self):
        cache = {}
        self.refresh(lobby([member("me", ME)], [member("x", 3)]), cache)
        _, enemy = self.refresh(lobby([member("me", ME)], []), cache)
        self.assertEqual(enemy, {"summoners": [], "champions": {}, "order": [],
                                 "isAram": False})

    def test_non_custom_lobby_still_uses_all_members(self):
        data = lobby([member("me", ME)], [member("x", 3)], custom=False, queueId=420)
        info = asyncio.run(tools.parseLobbyGameInfo(data, ME, cache={}))
        self.assertEqual(puuids(info["summoners"]), ["me", "x"])


class MainWindowLobbyRefreshTest(unittest.TestCase):
    """MainWindow.__doLobbyRefresh wiring on a fake self (network calls replaced)."""

    def setUp(self):
        from types import SimpleNamespace
        from unittest import mock
        from app.view import main_window

        self.mw = main_window
        MW = main_window.MainWindow
        self.refreshFn = MW._MainWindow__doLobbyRefresh.__wrapped__

        fake = SimpleNamespace(
            isGaming=False, _lobbyRefreshSeq=0, _champSelectEpoch=0, _gameStatus="Lobby",
            _lobbyEnemyShown=False, _lobbyQueueFilter=None, _lobbyGameInfoCache={},
            currentSummoner={"summonerId": ME, "puuid": "me"},
            gameInfoInterface=mock.Mock(), stackedWidget=mock.Mock(),
            careerInterface=mock.Mock(), switchTo=mock.Mock(), checkAndSwitchTo=mock.Mock())
        fake._MainWindow__dropLobbyEnemies = lambda: MW._MainWindow__dropLobbyEnemies(fake)
        fake._MainWindow__refreshCustomLobby = \
            lambda *a, **k: MW._MainWindow__refreshCustomLobby(fake, *a, **k)
        self.fake = fake
        self.ui = fake.gameInfoInterface

        self.lobbies = []
        self.gates = {}

        async def getLobby():
            return self.lobbies.pop(0)

        async def parse(lobby, currentSummonerId, useSGP=False, cache=None,
                        onPartial=None, members=None):
            if members is None:
                members = lobby.get("members") or []
            gate = self.gates.get(lobby["tag"])
            if gate is not None:
                await gate.wait()
            info = {"summoners": [dict(m, tag=lobby["tag"]) for m in members],
                    "champions": {}, "order": [], "isAram": False}
            if onPartial is not None and info["summoners"]:
                onPartial(info)
            return info

        patches = [mock.patch.object(main_window.connector, "getLobby", getLobby),
                   mock.patch.object(main_window, "parseLobbyGameInfo", parse)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tags(self, method):
        return [{s["tag"] for s in call.args[0]["summoners"]}
                for call in method.call_args_list]

    def run_overlapping(self, old, new):
        old["tag"], new["tag"] = "old", "new"
        self.lobbies = [old, new]

        async def scenario():
            self.gates["old"] = asyncio.Event()
            first = asyncio.ensure_future(self.refreshFn(self.fake))
            await asyncio.sleep(0.01)   # old refresh is now stuck fetching
            await self.refreshFn(self.fake)
            self.gates["old"].set()
            await first

        asyncio.run(scenario())

    def test_older_custom_refresh_does_not_paint_over_newer(self):
        self.run_overlapping(lobby([member("me", ME)], [member("x", 3)]),
                             lobby([member("me", ME)], [member("y", 4)]))
        self.assertEqual(self.tags(self.ui.updateEnemySummoners), [{"new"}, {"new"}])
        self.assertEqual(self.tags(self.ui.updateAllySummoners), [{"new"}, {"new"}])
        self.assertTrue(self.fake._lobbyEnemyShown)
        self.ui.clearEnemy.assert_not_called()

    def test_older_refresh_cannot_clear_or_flag(self):
        # the newer lobby has enemies; the stale one (empty team 200) must not clear them
        self.fake._lobbyEnemyShown = True
        self.run_overlapping(lobby([member("me", ME)], []),
                             lobby([member("me", ME)], [member("y", 4)]))
        self.ui.clearEnemy.assert_not_called()
        self.assertTrue(self.fake._lobbyEnemyShown)

    def test_older_normal_refresh_does_not_paint_over_newer(self):
        self.run_overlapping(lobby([member("me", ME)], custom=False, queueId=420),
                             lobby([member("me", ME), member("a", 2)], custom=False,
                                   queueId=420))
        self.assertEqual(self.tags(self.ui.updateAllySummoners), [{"new"}, {"new"}])

    def refresh(self, data):
        data["tag"] = "only"
        self.lobbies = [data]
        asyncio.run(self.refreshFn(self.fake))

    def test_empty_enemy_team_keeps_reserved_enemies(self):
        self.refresh(lobby([member("me", ME)], []))
        self.ui.clearEnemy.assert_not_called()
        self.ui.updateEnemySummoners.assert_not_called()
        self.assertFalse(self.fake._lobbyEnemyShown)

    def test_empty_enemy_team_clears_lobby_enemies(self):
        self.fake._lobbyEnemyShown = True
        self.refresh(lobby([member("me", ME)], []))
        self.ui.clearEnemy.assert_called_once()
        self.assertFalse(self.fake._lobbyEnemyShown)

    def test_switch_to_queue_lobby_clears_lobby_enemies_only(self):
        self.refresh(lobby([member("me", ME)], custom=False, queueId=420))
        self.ui.clearEnemy.assert_not_called()

        self.fake._lobbyEnemyShown = True
        self.refresh(lobby([member("me", ME)], custom=False, queueId=420))
        self.ui.clearEnemy.assert_called_once()
        self.assertFalse(self.fake._lobbyEnemyShown)

    def test_leaving_lobby_clears_lobby_enemies(self):
        self.fake._lobbyEnemyShown = True
        self.refresh({"errorCode": "RPC_ERROR", "httpStatus": 404})
        self.ui.clearEnemy.assert_called_once()
        self.assertFalse(self.fake._lobbyEnemyShown)

    def test_champ_select_started_meanwhile_drops_result(self):
        data = lobby([member("me", ME)], [member("x", 3)])
        data["tag"] = "old"
        self.lobbies = [data]

        async def scenario():
            self.gates["old"] = asyncio.Event()
            task = asyncio.ensure_future(self.refreshFn(self.fake))
            await asyncio.sleep(0.01)
            self.fake._champSelectEpoch += 1
            self.gates["old"].set()
            await task

        asyncio.run(scenario())
        self.ui.updateEnemySummoners.assert_not_called()
        self.ui.updateAllySummoners.assert_not_called()
        self.assertFalse(self.fake._lobbyEnemyShown)

    def assertNothingPainted(self):
        for name in ("updateAllySummoners", "updateEnemySummoners", "clearEnemy"):
            getattr(self.ui, name).assert_not_called()
        self.fake.checkAndSwitchTo.assert_not_called()
        self.fake.switchTo.assert_not_called()

    def test_refresh_requested_outside_lobby_phases_paints_nothing(self):
        for status in ("ChampSelect", "GameStart", "InProgress", "EndOfGame",
                       "WaitingForStatus", "Reconnect", "None", None):
            for data in (lobby([member("me", ME)], [member("x", 3)]),
                         lobby([member("me", ME)], custom=False, queueId=420), {}):
                self.fake._gameStatus = status
                self.fake._lobbyEnemyShown = True
                self.refresh(data)
                self.assertNothingPainted()
                self.assertTrue(self.fake._lobbyEnemyShown)
                self.assertEqual(len(self.lobbies), 1)   # no getLobby request either

    def test_refresh_finishing_after_champ_select_began_paints_nothing(self):
        for data in (lobby([member("me", ME)], [member("x", 3)]),
                     lobby([member("me", ME)], custom=False, queueId=420)):
            data["tag"] = "old"
            self.lobbies = [data]
            self.fake._gameStatus = "Lobby"

            async def scenario():
                self.gates["old"] = asyncio.Event()
                task = asyncio.ensure_future(self.refreshFn(self.fake))
                await asyncio.sleep(0.01)
                self.fake._gameStatus = "ChampSelect"   # epoch deliberately unchanged
                self.gates["old"].set()
                await task

            asyncio.run(scenario())
            self.assertNothingPainted()

    def test_matchmaking_and_ready_check_still_paint(self):
        for status in ("Matchmaking", "ReadyCheck"):
            self.ui.reset_mock()
            self.fake._gameStatus = status
            self.refresh(lobby([member("me", ME), member("a", 2)], custom=False,
                               queueId=420))
            self.assertEqual(self.tags(self.ui.updateAllySummoners), [{"only"}, {"only"}])

            self.ui.reset_mock()
            self.refresh(lobby([member("me", ME)], [member("x", 3)]))
            self.assertEqual(self.tags(self.ui.updateEnemySummoners), [{"only"}, {"only"}])


if __name__ == "__main__":
    unittest.main()
