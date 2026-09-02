"""tools.revealHiddenAllies / fillRevealedSummonerIds wiring (task 3).

Only the connector's two network calls are replaced by canned async functions
(dependencies, not the code under test). The merge, the stats and the
'reveal-hidden' critical log line run for real; log output is captured by
tests.LOG_RECORDS instead of the log/ directory.
"""
import asyncio
import unittest

from tests import LOG_RECORDS
from app.lol import tools
from app.lol.connector import connector

ME = "me-puuid"


def visible(cellId, puuid, summonerId):
    return {"cellId": cellId, "puuid": puuid, "summonerId": summonerId,
            "assignedPosition": "top", "championId": 0,
            "selectedPosition": "top", "nameVisibilityType": "VISIBLE",
            "playerType": "PLAYER"}


def hidden(cellId):
    return {"cellId": cellId, "puuid": "", "summonerId": 0,
            "assignedPosition": "jungle", "championId": 0,
            "selectedPosition": "jungle", "nameVisibilityType": "HIDDEN",
            "playerType": "PLAYER"}


def participant(puuid):
    return {"puuid": puuid, "game_name": f"n-{puuid}", "game_tag": "NA1",
            "cid": f"{puuid}@champ-select.na1.pvp.net"}


class _ConnectorStubMixin:

    def setUp(self):
        self._orig = (connector.getChampSelectChatParticipants,
                      connector.getSummonerByPuuid)
        LOG_RECORDS.clear()
        self.calls = []

    def tearDown(self):
        (connector.getChampSelectChatParticipants,
         connector.getSummonerByPuuid) = self._orig

    def stubParticipants(self, parts):
        async def fake():
            self.calls.append("participants")
            return parts
        connector.getChampSelectChatParticipants = fake


class RevealHiddenAlliesTest(_ConnectorStubMixin, unittest.TestCase):

    def test_no_hidden_slot_skips_riot_client_and_log(self):
        self.stubParticipants([participant("h1")])
        team = [visible(i, f"p{i}", 10 + i) for i in range(5)]

        out = asyncio.run(tools.revealHiddenAllies(team, ME))

        self.assertIs(out, team)
        self.assertEqual(self.calls, [])
        self.assertFalse(any("reveal-hidden" in r for r in LOG_RECORDS))

    def test_hidden_slots_merged_and_logged_once(self):
        self.stubParticipants(
            [participant(ME)] + [participant(f"h{i}") for i in range(1, 5)])
        team = [visible(0, ME, 11)] + [hidden(i) for i in range(1, 5)]

        out = asyncio.run(tools.revealHiddenAllies(team, ME))

        synth = [it for it in out if it.get("synthesized")]
        self.assertEqual([it["puuid"] for it in synth], ["h1", "h2", "h3", "h4"])
        self.assertEqual(self.calls, ["participants"])
        lines = [r for r in LOG_RECORDS if "reveal-hidden" in r]
        self.assertEqual(len(lines), 1)
        self.assertIn("CRITICAL - [Tools] reveal-hidden: 可见 1/揭示 0/合成 4", lines[0])

    def test_riot_client_failure_falls_back_to_original_team(self):
        async def boom():
            raise RuntimeError("boom")
        connector.getChampSelectChatParticipants = boom
        team = [visible(0, ME, 11), hidden(1)]

        out = asyncio.run(tools.revealHiddenAllies(team, ME))

        self.assertIs(out, team)
        self.assertTrue(any("merge failed" in r for r in LOG_RECORDS))


class FillRevealedSummonerIdsTest(_ConnectorStubMixin, unittest.TestCase):

    def test_fills_only_revealed_slots_without_id(self):
        async def fake(puuid):
            self.calls.append(puuid)
            if puuid == "h1":
                return {"summonerId": 777, "puuid": puuid}
            raise RuntimeError("not found")
        connector.getSummonerByPuuid = fake

        team = [visible(0, ME, 11),
                {"puuid": "h1", "summonerId": 0, "revealed": True},
                {"puuid": "h2", "summonerId": 0, "revealed": True},
                {"puuid": "h3", "summonerId": 55, "revealed": True},
                {"puuid": "x", "summonerId": 0}]           # HIDDEN, not revealed

        asyncio.run(tools.fillRevealedSummonerIds(team))

        self.assertEqual([it["summonerId"] for it in team], [11, 777, 0, 55, 0])
        self.assertEqual(sorted(self.calls), ["h1", "h2"])

    def test_untouched_team_makes_no_calls(self):
        async def fake(puuid):
            self.calls.append(puuid)
            return {"summonerId": 1}
        connector.getSummonerByPuuid = fake
        team = [visible(0, ME, 11), hidden(1)]

        asyncio.run(tools.fillRevealedSummonerIds(team))

        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
