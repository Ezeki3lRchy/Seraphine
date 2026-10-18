"""parseAllyGameInfo with a per-draft cache (hidden teammates join the champ-select
chat room one by one; MainWindow re-parses as the participant list grows).

Only the network-facing dependencies are replaced: the Riot Client participant
list, the summoner lookups and the per-summoner history fetch. The reveal merge,
summonerId fill and cache reuse run for real.
"""
import asyncio
import unittest

from app.lol import tools
from app.lol.connector import connector

ME = "me-puuid"


def slot(cellId, puuid="", summonerId=0, hidden=True):
    return {"cellId": cellId, "puuid": puuid, "summonerId": summonerId,
            "assignedPosition": "top", "championId": 0, "selectedPosition": "top",
            "nameVisibilityType": "HIDDEN" if hidden else "VISIBLE",
            "playerType": "PLAYER"}


def participant(puuid):
    return {"puuid": puuid, "game_name": f"n-{puuid}", "game_tag": "NA1",
            "cid": "room@lol-champ-select.na1.pvp.net"}


class AllyRevealRefreshTest(unittest.TestCase):

    def setUp(self):
        self._orig = (connector.getChampSelectChatParticipants,
                      connector.getSummonerByPuuid, connector.isInTencent,
                      connector.getChampionIcon, tools.parseSummonerGameInfo)
        self.fetched = []
        self.participants = []

        async def participants():
            return list(self.participants)

        async def byPuuid(puuid):
            return {"summonerId": abs(hash(puuid)) % 10**6 + 1}

        async def icon(championId):
            return f"icon-{championId}"

        async def fetch(item, isRank, currentSummonerId):
            if item.get("nameVisibilityType") == "HIDDEN" or not item.get("summonerId"):
                return None
            self.fetched.append(item["puuid"])
            return {"puuid": item["puuid"], "summonerId": item["summonerId"],
                    "championId": item.get("championId") or 0, "icon": "x",
                    "cellId": item["cellId"], "selectedPosition": None}

        connector.getChampSelectChatParticipants = participants
        connector.getSummonerByPuuid = byPuuid
        connector.isInTencent = lambda: False
        connector.getChampionIcon = icon
        tools.parseSummonerGameInfo = fetch

    def tearDown(self):
        (connector.getChampSelectChatParticipants, connector.getSummonerByPuuid,
         connector.isInTencent, connector.getChampionIcon,
         tools.parseSummonerGameInfo) = self._orig

    def parse(self, session, cache):
        return asyncio.run(tools.parseAllyGameInfo(
            session, 1, useSGP=False, currentPuuid=ME, cache=cache))

    def test_late_joiners_are_added_and_only_they_are_fetched(self):
        session = {"myTeam": [slot(5), slot(6), slot(7), slot(8),
                              slot(9, ME, 1, hidden=False)]}
        cache = {}

        self.participants = [participant(p) for p in (ME, "h1", "h2")]
        first = self.parse(session, cache)
        self.assertEqual(len(first["summoners"]), 3)
        self.assertEqual(sorted(self.fetched), sorted([ME, "h1", "h2"]))

        self.fetched.clear()
        self.participants = [participant(p) for p in (ME, "h1", "h2", "h3", "h4")]
        second = self.parse(session, cache)
        self.assertEqual(len(second["summoners"]), 5)
        self.assertEqual(sorted(self.fetched), ["h3", "h4"])
        self.assertEqual({s["puuid"] for s in second["summoners"]},
                         {ME, "h1", "h2", "h3", "h4"})

    def test_cached_summoner_picks_up_new_champion(self):
        session = {"myTeam": [slot(9, ME, 1, hidden=False)]}
        cache = {}
        self.parse(session, cache)

        session["myTeam"][0]["championId"] = 17
        again = self.parse(session, cache)
        self.assertEqual(again["summoners"][0]["championId"], 17)
        self.assertEqual(again["summoners"][0]["icon"], "icon-17")
        self.assertEqual(cache[ME]["championId"], 0)


if __name__ == "__main__":
    unittest.main()
