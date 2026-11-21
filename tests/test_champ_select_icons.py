"""Champ-select icon updates (GameInfoInterface.__updateIcons).

The champion icon is fetched with an await; meanwhile the view can rebuild or clear its
columns, and a newer pick can be requested. Stand-ins replace the view, the columns and
the icon fetch; the columns are real QObjects so a deleted one raises like in the app.
"""
import asyncio
import os
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from app.lol.connector import connector
from app.view.game_info_interface import GameInfoInterface

updateIcons = GameInfoInterface._GameInfoInterface__updateIcons


class Column(QObject):
    def __init__(self):
        super().__init__()
        self.drawn = []

    def updateChampion(self, championId, icon, aramInfo):
        self.objectName()  # raises RuntimeError once the C++ object is deleted
        self.drawn.append(championId)


class UpdateIconsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._orig = connector.getChampionIcon
        self.owner = types.SimpleNamespace(isAram=False)
        self.column = Column()
        self.view = types.SimpleNamespace(items={7: self.column})
        self.champions = {7: 1}

    def tearDown(self):
        connector.getChampionIcon = self._orig

    def pick(self, championId):
        return updateIcons(self.owner, self.view, self.champions,
                           [{"summonerId": 7, "championId": championId}])

    def test_simple_pick_is_drawn(self):
        async def icon(championId):
            return f"icon-{championId}"
        connector.getChampionIcon = icon

        asyncio.run(self.pick(2))
        self.assertEqual(self.column.drawn, [2])
        self.assertEqual(self.champions[7], 2)

    def test_column_deleted_during_the_await_is_skipped(self):
        async def icon(championId):
            # updateSummoners rebuilt the view meanwhile: the old column is gone
            self.view.items = {}
            shiboken6.delete(self.column)
            await asyncio.sleep(0)
            return f"icon-{championId}"
        connector.getChampionIcon = icon

        asyncio.run(self.pick(2))  # must not raise RuntimeError

    def test_cleared_view_keeps_a_deleted_column_and_is_skipped(self):
        async def icon(championId):
            # clear() deletes the widgets but leaves view.items as it was
            shiboken6.delete(self.column)
            return f"icon-{championId}"
        connector.getChampionIcon = icon

        asyncio.run(self.pick(2))  # must not raise RuntimeError

    def test_slower_older_pick_does_not_overwrite_a_newer_one(self):
        delays = {2: 0.05, 3: 0.0}  # A=1 -> B=2 (slow) -> C=3 (fast)

        async def icon(championId):
            await asyncio.sleep(delays[championId])
            return f"icon-{championId}"
        connector.getChampionIcon = icon

        async def run():
            b = asyncio.ensure_future(self.pick(2))
            await asyncio.sleep(0)  # B's request is in flight
            await self.pick(3)
            await b

        asyncio.run(run())
        self.assertEqual(self.column.drawn, [3])
        self.assertEqual(self.champions[7], 3)

    def test_failure_after_a_superseded_pick_rolls_back_to_what_is_drawn(self):
        """A -> B -> C: B's icon arrives while C is newest (skipped), then C's fetch fails.
        The column still shows A, so picking B again must draw B"""
        async def icon(championId):
            if championId == 2:
                await asyncio.sleep(0.01)
                return "icon-2"
            await asyncio.sleep(0.03)
            raise OSError("no icon")
        connector.getChampionIcon = icon

        async def run():
            b = asyncio.ensure_future(self.pick(2))
            await asyncio.sleep(0)  # B's request is in flight
            with self.assertRaises(OSError):
                await self.pick(3)
            await b
            self.assertEqual(self.column.drawn, [])  # B was superseded, C failed
            self.assertEqual(self.champions[7], 1)   # bookkeeping matches the drawn A

            async def fast(championId):
                return f"icon-{championId}"
            connector.getChampionIcon = fast
            await self.pick(2)

        asyncio.run(run())
        self.assertEqual(self.column.drawn, [2])
        self.assertEqual(self.champions[7], 2)

    def test_replaced_champion_map_does_not_hide_a_repick(self):
        """the column shows B, a reload replaces the map with one saying A (the reused
        column keeps B); picking A again must draw A"""
        async def icon(championId):
            return f"icon-{championId}"
        connector.getChampionIcon = icon

        asyncio.run(self.pick(2))
        self.assertEqual(self.column.drawn, [2])
        self.champions = {7: 1}  # updateAllySummoners: freshly parsed map, same column
        asyncio.run(self.pick(1))
        self.assertEqual(self.column.drawn, [2, 1])
        self.assertEqual(self.champions[7], 1)

    def test_same_pick_requested_twice_concurrently_fetches_once(self):
        fetched = []

        async def icon(championId):
            fetched.append(championId)
            await asyncio.sleep(0.01)
            return f"icon-{championId}"
        connector.getChampionIcon = icon

        async def run():
            await asyncio.gather(self.pick(2), self.pick(2))

        asyncio.run(run())
        self.assertEqual(fetched, [2])
        self.assertEqual(self.column.drawn, [2])

    def test_failure_after_the_column_was_deleted(self):
        async def icon(championId):
            self.view.items = {}
            shiboken6.delete(self.column)
            raise OSError("no icon")
        connector.getChampionIcon = icon

        with self.assertRaises(OSError):
            asyncio.run(self.pick(2))
        self.assertEqual(self.champions[7], 1)

    def test_failed_fetch_keeps_the_old_champion_for_a_retry(self):
        async def icon(championId):
            raise OSError("no icon")
        connector.getChampionIcon = icon

        with self.assertRaises(OSError):
            asyncio.run(self.pick(2))
        self.assertEqual(self.champions[7], 1)
        self.assertEqual(self.column.drawn, [])


if __name__ == "__main__":
    unittest.main()
