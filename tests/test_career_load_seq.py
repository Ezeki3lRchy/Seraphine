"""CareerInterface.updateInterface: the newest load wins.

The real CareerInterface is built offscreen (never shown) and the real
updateInterface / repaintInterface run. Only the network side is replaced: the
module's connector, parseSummonerData (gated per player so the test decides which
load finishes first) and rank_history's disk access.
"""
import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.lol import rank_history
from app.view import career_interface


def info(puuid, name):
    return {"name": name, "isPublic": True, "tagLine": "T1",
            "icon": "app/resource/images/champion-0.png", "iconId": 0, "level": 30,
            "xpSinceLastLevel": 0, "xpUntilNextLevel": 1, "puuid": puuid,
            "rankInfo": {}, "games": {}}


class FakeConnector:

    def __init__(self):
        self.summonerGates = {}
        self.summonerErrors = {}

    async def getSummonerByPuuid(self, puuid):
        gate = self.summonerGates.get(puuid)
        if gate is not None:
            await gate.wait()
        if puuid in self.summonerErrors:
            raise self.summonerErrors[puuid]
        return {"puuid": puuid}

    async def getSummonerGamesByPuuid(self, puuid, begIdx, endIdx):
        return {"games": []}

    async def getRankedStatsByPuuid(self, puuid):
        return {}


class CareerLoadSeqTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = career_interface.CareerInterface()
        self.addCleanup(self.view.deleteLater)
        self.fake = FakeConnector()
        self.parseGates = {}
        self.names = {"puuid-a": "PlayerA", "puuid-b": "PlayerB"}
        self.painted = []
        self.loading = []

        async def parse(summoner, rankTask, gamesTask):
            await rankTask
            await gamesTask
            gate = self.parseGates.get(summoner["puuid"])
            if gate is not None:
                await gate.wait()
            return info(summoner["puuid"], self.names[summoner["puuid"]])

        repaint = self.view.repaintInterface
        setLoading = self.view.setLoadingPageEnabled

        async def spyRepaint(data):
            self.painted.append(data["puuid"])
            await repaint(data)

        def spyLoading(enable):
            self.loading.append(enable)
            setLoading(enable)

        for target, name, value in [
                (career_interface, "connector", self.fake),
                (career_interface, "parseSummonerData", parse),
                (rank_history, "record", lambda *a, **k: None),
                (rank_history, "history", lambda *a, **k: []),
                (self.view, "repaintInterface", spyRepaint),
                (self.view, "setLoadingPageEnabled", spyLoading)]:
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def overlap(self, gates):
        """A starts first and is held on `gates`; B starts and finishes; then A resumes"""
        gateA = asyncio.Event()
        gates["puuid-a"] = gateA

        async def main():
            a = asyncio.ensure_future(self.view.updateInterface(puuid="puuid-a"))
            await asyncio.sleep(0)
            resultB = await self.view.updateInterface(puuid="puuid-b")
            self.assertTrue(self.loading)
            self.assertFalse(self.loading[-1])  # B turned the loading page off
            loadingCallsAfterB = len(self.loading)
            gateA.set()
            resultA = await a
            return resultA, resultB, loadingCallsAfterB

        resultA, resultB, loadingCallsAfterB = asyncio.run(main())

        self.assertEqual(self.painted, ["puuid-b"])  # A never repainted
        self.assertEqual(self.view.puuid, "puuid-b")
        self.assertEqual(self.view.name.text(), "PlayerB")
        # the superseded A did not touch the loading page after B finished
        self.assertEqual(len(self.loading), loadingCallsAfterB)
        self.assertFalse(self.loading[-1])
        self.assertIs(resultA, False)
        self.assertIs(resultB, True)

    def test_slow_first_parse_does_not_overwrite_newer_player(self):
        self.overlap(self.parseGates)

    def test_slow_first_summoner_lookup_does_not_overwrite_newer_player(self):
        self.overlap(self.fake.summonerGates)

    def test_newest_load_failing_still_ends_loading(self):
        gateA = asyncio.Event()
        self.parseGates["puuid-a"] = gateA
        self.fake.summonerErrors["puuid-b"] = RuntimeError("lookup failed")

        async def main():
            a = asyncio.ensure_future(self.view.updateInterface(puuid="puuid-a"))
            await asyncio.sleep(0)
            with self.assertRaises(RuntimeError):
                await self.view.updateInterface(puuid="puuid-b")
            self.assertFalse(self.loading[-1])  # B took its own spinner down
            loadingCallsAfterB = len(self.loading)
            gateA.set()
            self.assertIs(await a, False)
            return loadingCallsAfterB

        loadingCallsAfterB = asyncio.run(main())

        self.assertEqual(self.painted, [])  # A never repainted
        self.assertEqual(len(self.loading), loadingCallsAfterB)
        self.assertFalse(self.loading[-1])

    def test_single_load_paints_and_ends_loading(self):
        self.assertIs(asyncio.run(self.view.updateInterface(puuid="puuid-a")), True)
        self.assertEqual(self.painted, ["puuid-a"])
        self.assertEqual(self.view.name.text(), "PlayerA")
        self.assertEqual(self.loading, [True, False])


if __name__ == "__main__":
    unittest.main()
