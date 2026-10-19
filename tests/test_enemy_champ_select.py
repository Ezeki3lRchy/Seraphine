"""Enemy history during champ select (tools.visibleEnemies / parseEnemyChampSelectInfo).

Ranked hides the enemy team (puuid "", summonerId 0, nameVisibilityType HIDDEN), so
nothing is loaded; modes that show enemies (custom / tournament draft) load their
history. Only the network-facing calls are replaced.
"""
import asyncio
import unittest

from app.lol import tools
from app.lol.connector import connector


def enemy(cellId, puuid="", summonerId=0, hidden=False, championId=0, bot=False):
    return {"cellId": cellId, "puuid": puuid, "summonerId": summonerId,
            "championId": championId, "assignedPosition": "",
            "nameVisibilityType": "HIDDEN" if hidden else "VISIBLE",
            "playerType": "BOT" if bot else "PLAYER"}


class VisibleEnemiesTest(unittest.TestCase):

    def test_ranked_hidden_team_gives_nothing(self):
        session = {"theirTeam": [enemy(5 + i, hidden=True) for i in range(5)]}
        self.assertEqual(tools.visibleEnemies(session), [])

    def test_visible_players_only(self):
        session = {"theirTeam": [enemy(5, "a", 11), enemy(6, hidden=True),
                                 enemy(7, "", 0), enemy(8, "bot", 12, bot=True),
                                 enemy(9, "b", 0)]}
        self.assertEqual([m["cellId"] for m in tools.visibleEnemies(session)], [5, 9])

    def test_missing_or_bad_input(self):
        self.assertEqual(tools.visibleEnemies(None), [])
        self.assertEqual(tools.visibleEnemies({"theirTeam": ["x", None]}), [])


class ParseEnemyChampSelectInfoTest(unittest.TestCase):

    def setUp(self):
        self._orig = (tools.parseSummonerGameInfo, connector.getSummonerByPuuid,
                      connector.isInTencent, connector.getChampionIcon)
        self.fetched = []

        async def fetch(item, isRank, currentSummonerId):
            self.fetched.append(item["puuid"])
            return {"puuid": item["puuid"], "summonerId": item["summonerId"],
                    "championId": item.get("championId") or 0, "icon": "x",
                    "cellId": item["cellId"]}

        async def byPuuid(puuid):
            return {"summonerId": 900 + len(puuid)}

        async def icon(championId):
            return f"icon-{championId}"

        tools.parseSummonerGameInfo = fetch
        connector.getSummonerByPuuid = byPuuid
        connector.isInTencent = lambda: False
        connector.getChampionIcon = icon

    def tearDown(self):
        (tools.parseSummonerGameInfo, connector.getSummonerByPuuid,
         connector.isInTencent, connector.getChampionIcon) = self._orig

    def parse(self, session, cache, partials=None):
        onPartial = (lambda info: partials.append(len(info["summoners"]))) \
            if partials is not None else None
        return asyncio.run(tools.parseEnemyChampSelectInfo(
            session, 1, useSGP=False, cache=cache, onPartial=onPartial))

    def test_hidden_team_loads_nothing(self):
        session = {"myTeam": [{}], "theirTeam": [enemy(5 + i, hidden=True) for i in range(5)]}
        info = self.parse(session, {})
        self.assertEqual(info["summoners"], [])
        self.assertEqual(self.fetched, [])

    def test_visible_team_loads_progressively_in_cell_order(self):
        session = {"myTeam": [{}],
                   "theirTeam": [enemy(7, "c", 13), enemy(5, "a", 11), enemy(6, "b", 12)]}
        partials = []
        info = self.parse(session, {}, partials)
        self.assertEqual([s["puuid"] for s in info["summoners"]], ["a", "b", "c"])
        self.assertEqual(partials, [1, 2, 3])
        self.assertEqual(info["champions"], {11: 0, 12: 0, 13: 0})

    def test_cache_reuses_players_and_follows_new_picks(self):
        cache = {}
        session = {"myTeam": [{}], "theirTeam": [enemy(5, "a", 11)]}
        self.parse(session, cache)
        self.fetched.clear()

        session["theirTeam"] = [enemy(5, "a", 11, championId=17), enemy(6, "b", 12)]
        info = self.parse(session, cache)
        self.assertEqual(self.fetched, ["b"])
        first = info["summoners"][0]
        self.assertEqual((first["championId"], first["icon"]), (17, "icon-17"))

    def test_puuid_only_gets_summoner_id(self):
        session = {"myTeam": [{}], "theirTeam": [enemy(5, "abc", 0)]}
        info = self.parse(session, {})
        self.assertEqual(info["summoners"][0]["summonerId"], 903)


if __name__ == "__main__":
    unittest.main()
