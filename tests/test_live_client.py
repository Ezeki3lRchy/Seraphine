"""Live Client Data item gold (app/lol/live_client.py): pure functions on a fixture
shaped like the documented allgamedata sample, plus the poller with the fetch mocked.
No network."""
import asyncio
import copy
import json
import os
import unittest
import unittest.mock

from app.lol import live_client as L

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "liveclient_allgamedata.json")


def load():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def item(price, count=1, consumable=False):
    return {"itemID": 1, "price": price, "count": count, "consumable": consumable}


class ItemValueTest(unittest.TestCase):

    def test_price_times_count_for_consumables(self):
        self.assertEqual(L.itemValue(item(50, 3, consumable=True)), 150)

    def test_non_consumable_count_ignored(self):
        self.assertEqual(L.itemValue(item(350, 7)), 350)

    def test_trinket_is_free(self):
        self.assertEqual(L.itemValue(item(0)), 0)

    def test_missing_or_bad_fields(self):
        self.assertEqual(L.itemValue({}), 0)
        self.assertEqual(L.itemValue(None), 0)
        self.assertEqual(L.itemValue({"price": None, "count": None}), 0)
        self.assertEqual(L.itemValue({"price": "x"}), 0)
        self.assertEqual(L.itemValue({"price": 75, "count": 0, "consumable": True}), 75)

    def test_player_value(self):
        player = load()["allPlayers"][0]  # Doran's + Black Cleaver + Steelcaps + 2 pots
        self.assertEqual(L.playerItemValue(player), 450 + 3000 + 1100 + 100)
        self.assertEqual(L.playerItemValue({"items": None}), 0)
        self.assertEqual(L.playerItemValue({}), 0)
        self.assertEqual(L.playerItemValue(None), 0)


class TeamTest(unittest.TestCase):

    def test_local_team_by_riot_id(self):
        self.assertEqual(L.localTeam(load()), "ORDER")

    def test_local_team_on_red_side(self):
        data = load()
        data["activePlayer"]["riotId"] = "Enemy Mid#TAG"
        data["activePlayer"]["summonerName"] = "Enemy Mid#TAG"
        self.assertEqual(L.localTeam(data), "CHAOS")

    def test_local_team_by_legacy_summoner_name(self):
        data = load()
        data["activePlayer"] = {"summonerName": "Ally Adc"}
        for p in data["allPlayers"]:
            p.pop("riotId")
        self.assertEqual(L.localTeam(data), "ORDER")

    def test_local_team_unknown(self):
        data = load()
        data.pop("activePlayer")
        self.assertIsNone(L.localTeam(data))
        self.assertIsNone(L.localTeam({}))
        self.assertIsNone(L.localTeam(None))

    def test_totals(self):
        summary = L.summarizeGameData(load())
        self.assertEqual((summary["allyTotal"], summary["enemyTotal"], summary["diff"]),
                         (25425, 17050, 8375))
        self.assertEqual(summary["team"], "ORDER")
        self.assertTrue(summary["localKnown"])
        self.assertEqual(summary["currentGold"], 812.5)

    def test_perspective_flips_on_red_side(self):
        data = load()
        data["activePlayer"]["riotId"] = "Enemy Top#TAG"
        summary = L.summarizeGameData(data)
        self.assertEqual((summary["team"], summary["diff"]), ("CHAOS", -8375))
        self.assertEqual(summary["lanes"][0]["diff"], -400)


class LanePairingTest(unittest.TestCase):

    def test_by_position(self):
        data = load()
        # shuffle list order: pairing must still follow positions
        data["allPlayers"] = data["allPlayers"][::-1]
        lanes = L.summarizeGameData(data)["lanes"]
        self.assertEqual([l["position"] for l in lanes], list(L.LANES))
        self.assertEqual([(l["ally"]["championName"], l["enemy"]["championName"]) for l in lanes],
                         [("Garen", "Darius"), ("Lee Sin", "Vi"), ("Ahri", "Syndra"),
                          ("Jinx", "Caitlyn"), ("Leona", "Thresh")])
        self.assertEqual([l["diff"] for l in lanes], [400, 2450, 350, 3050, 2125])

    def test_without_positions_uses_list_order(self):
        data = load()
        for p in data["allPlayers"]:
            p["position"] = ""
        lanes = L.summarizeGameData(data)["lanes"]
        self.assertEqual([l["position"] for l in lanes], [""] * 5)
        self.assertEqual([l["ally"]["championName"] for l in lanes],
                         ["Garen", "Lee Sin", "Ahri", "Jinx", "Leona"])
        self.assertEqual([l["enemy"]["championName"] for l in lanes],
                         ["Darius", "Vi", "Syndra", "Caitlyn", "Thresh"])

    def test_partial_and_duplicate_positions(self):
        a = [{"championName": "A", "position": "BOTTOM"}, {"championName": "B", "position": None},
             {"championName": "C", "position": "BOTTOM"}]
        e = [{"championName": "X", "position": "TOP"}]
        lanes = L.pairLanes(a, e)
        # B and C (duplicate BOTTOM) fill TOP / JUNGLE in list order; A keeps BOTTOM
        self.assertEqual([(l["position"], (l["ally"] or {}).get("championName"),
                           (l["enemy"] or {}).get("championName")) for l in lanes],
                         [("TOP", "B", "X"), ("", "C", None), ("BOTTOM", "A", None)])

    def test_uneven_teams(self):
        lanes = L.pairLanes([{"items": [item(1000)]}], [])
        self.assertEqual(len(lanes), 1)
        self.assertEqual(lanes[0]["diff"], 1000)
        self.assertEqual(L.pairLanes([], []), [])


class PartialPayloadTest(unittest.TestCase):

    def test_empty(self):
        for data in (None, {}, [], {"allPlayers": None}, {"allPlayers": []},
                     {"allPlayers": [None, "x"]}):
            self.assertIsNone(L.summarizeGameData(data))

    def test_documented_sample_shape(self):
        # the official sample: one player, empty items, empty position, currentGold 0.
        # A single team is not something to compare -> None
        annie = {"championName": "Annie", "items": [], "position": "",
                 "summonerName": "Riot Tuxedo", "team": "ORDER", "scores": {}}
        data = {"activePlayer": {"currentGold": 0.0, "level": 1, "summonerName": "Riot Tuxedo"},
                "allPlayers": [annie], "gameData": {"gameMode": "CLASSIC", "gameTime": 0.0}}
        self.assertIsNone(L.summarizeGameData(data))

        data["allPlayers"].append(dict(annie, summonerName="Other", team="CHAOS"))
        summary = L.summarizeGameData(data)
        self.assertEqual((summary["allyTotal"], summary["enemyTotal"]), (0, 0))
        self.assertTrue(summary["even"])  # a true zero is "even", not missing data
        self.assertFalse(summary["hasLanes"])
        self.assertEqual(len(summary["lanes"]), 1)

    def test_missing_items_and_scores(self):
        data = load()
        for p in data["allPlayers"]:
            p.pop("items")
            p["scores"] = None
        data["gameData"] = None
        summary = L.summarizeGameData(data)
        self.assertEqual(summary["diff"], 0)
        self.assertEqual(summary["gameTime"], 0)

    def test_missing_price(self):
        data = load()
        for it in data["allPlayers"][0]["items"]:
            it.pop("price")
        self.assertEqual(L.summarizeGameData(data)["lanes"][0]["ally"]["value"], 0)

    def test_resolve_champion_id(self):
        champs = {86: "Garen", 64: "Lee Sin"}
        self.assertEqual(L.resolveChampionId("lee sin", champs), 64)
        self.assertIsNone(L.resolveChampionId("Teemo", champs))
        self.assertIsNone(L.resolveChampionId("Garen", None))


class ModeAndEvenTest(unittest.TestCase):

    def test_excluded_modes(self):
        for mode in ("CHERRY", "TFT", "STRAWBERRY", "cherry"):
            data = load()
            data["gameData"]["gameMode"] = mode
            self.assertIsNone(L.summarizeGameData(data), mode)
        data = load()
        data["gameData"]["gameMode"] = "ARAM"
        self.assertIsNotNone(L.summarizeGameData(data))

    def test_not_two_teams(self):
        data = load()
        data["allPlayers"][3]["team"] = "NEUTRAL"  # some third team
        self.assertIsNone(L.summarizeGameData(data))
        data = load()
        data["allPlayers"] = [p for p in data["allPlayers"] if p["team"] == "ORDER"]
        self.assertIsNone(L.summarizeGameData(data))

    def test_even_rule(self):
        self.assertTrue(L.isEven(0, 0, 0))
        self.assertTrue(L.isEven(149, 1000, 851))
        self.assertFalse(L.isEven(150, 1000, 850))
        # large totals: 3% of the larger side
        self.assertTrue(L.isEven(-300, 15050, 15350))
        self.assertFalse(L.isEven(-500, 15050, 15550))

    def test_summary_even_flags(self):
        summary = L.summarizeGameData(load())
        self.assertFalse(summary["even"])
        self.assertEqual([l["even"] for l in summary["lanes"]], [False] * 5)
        data = load()
        for p in data["allPlayers"]:
            p["items"] = [item(3000)]
        summary = L.summarizeGameData(data)
        self.assertTrue(summary["even"])
        self.assertEqual([l["even"] for l in summary["lanes"]], [True] * 5)

    def test_has_lanes_and_ranked_players(self):
        summary = L.summarizeGameData(load())
        self.assertTrue(summary["hasLanes"])
        data = load()
        for p in data["allPlayers"]:
            p["position"] = ""
        summary = L.summarizeGameData(data)
        self.assertFalse(summary["hasLanes"])
        values = [p["value"] for p in summary["allyPlayers"]]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertEqual(summary["allyPlayers"][0]["championName"], "Jinx")
        self.assertEqual(len(summary["enemyPlayers"]), 5)


async def until(predicate, what, timeout=10):
    """Waits for a condition, polling under a bound that fails with a clear message (a
    fixed sleep would be a guess how long a loaded CI runner needs)"""
    async def poll():
        while not predicate():
            await asyncio.sleep(0.005)

    try:
        await asyncio.wait_for(poll(), timeout)
    except asyncio.TimeoutError:
        raise AssertionError(f"timed out after {timeout}s waiting for: {what}") from None


# Simulated backend latency, longer than the poll interval so a poll is still running when
# the next one is due. No assertion depends on how long it really takes.
SLOW_FETCH = 0.02
# How long a stopped poller is watched to prove it makes no further request: an assertion
# that nothing happens can only be bounded in time.
QUIET_WINDOW = 0.05


class FakeResponse:
    def __init__(self, status=200, payload=None, error=None):
        self.status, self.payload, self.error = status, payload, error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        if self.error:
            raise self.error
        return self.payload


class FakeSession:
    instances = []

    def __init__(self, *args, **kwargs):
        self.closed = False
        self.closing = False
        self.response = FakeResponse(payload={"ok": 1})
        self.gets = 0
        FakeSession.instances.append(self)

    def get(self, path, **kwargs):
        self.gets += 1
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def close(self):
        self.closing = True
        await asyncio.sleep(0)
        self.closed = True


class LiveClientTest(unittest.TestCase):

    def setUp(self):
        FakeSession.instances = []
        self.patch = unittest.mock.patch.object(L.aiohttp, "ClientSession", FakeSession)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    def test_ok_and_session_reuse(self):
        client = L.LiveClient()
        self.assertEqual(self.run_async(client.allGameData()), {"ok": 1})
        self.assertEqual(self.run_async(client.allGameData()), {"ok": 1})
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertEqual(FakeSession.instances[0].gets, 2)

    def test_non_200_and_bad_json_return_none(self):
        client = L.LiveClient()
        self.run_async(client.allGameData())
        session = FakeSession.instances[0]
        session.response = FakeResponse(status=404)
        self.assertIsNone(self.run_async(client.allGameData()))
        session.response = FakeResponse(error=ValueError("not json"))
        self.assertIsNone(self.run_async(client.allGameData()))
        session.response = OSError("connection refused")
        self.assertIsNone(self.run_async(client.allGameData()))

    def test_cancellation_is_reraised(self):
        client = L.LiveClient()
        self.run_async(client.allGameData())
        FakeSession.instances[0].response = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.run_async(client.allGameData())

    def test_close_detaches_before_awaiting(self):
        client = L.LiveClient()
        self.run_async(client.allGameData())
        first = FakeSession.instances[0]

        async def scenario():
            closing = asyncio.ensure_future(client.close())
            await until(lambda: first.closing, "close() to start closing the session")
            self.assertIsNone(client.session)
            result = await client.allGameData()  # opens a fresh session
            await closing
            return result

        self.assertEqual(self.run_async(scenario()), {"ok": 1})
        self.assertTrue(first.closed)
        self.assertEqual(len(FakeSession.instances), 2)
        self.assertFalse(FakeSession.instances[1].closed)
        self.run_async(client.close())
        self.assertTrue(FakeSession.instances[1].closed)
        self.run_async(client.close())  # closing twice is fine

    def test_poller_aclose_closes_session(self):
        published = []
        poller = L.LiveClientPoller(onData=published.append, interval=0.001)
        poller.client.allGameData = lambda: asyncio.sleep(0, result=load())
        poller.fetch = poller.client.allGameData

        async def scenario():
            poller.start()
            await until(lambda: published, "the poller to publish once")
            poller.client.session = FakeSession()
            await poller.aclose()

        self.run_async(scenario())
        self.assertFalse(poller.running)
        self.assertIsNone(poller.client.session)
        self.assertTrue(FakeSession.instances[-1].closed)
        self.assertIsNone(published[-1])


class PollerTest(unittest.TestCase):

    def run_async(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_no_overlap_stop_and_failures(self):
        published = []
        state = {"active": 0, "maxActive": 0, "calls": 0}
        payload = load()

        async def fetch():
            state["calls"] += 1
            state["active"] += 1
            state["maxActive"] = max(state["maxActive"], state["active"])
            try:
                await asyncio.sleep(SLOW_FETCH)
                if state["calls"] == 2:
                    raise RuntimeError("boom")
                if state["calls"] == 3:
                    return None
                data = copy.deepcopy(payload)
                data["gameData"]["gameTime"] = state["calls"]
                return data
            finally:
                state["active"] -= 1

        async def scenario():
            poller = L.LiveClientPoller(fetch=fetch, onData=published.append, interval=0.005)
            poller.start()
            poller.start()  # second start is a no-op
            # until the loop got past the failing and the None fetch
            await until(lambda: state["calls"] >= 5, "five polls")
            self.assertTrue(poller.running)
            task = poller.task
            poller.stop()
            await asyncio.wait({task}, timeout=10)
            self.assertTrue(task.done(), "the poll task did not end after stop()")
            calls = state["calls"]
            await asyncio.sleep(QUIET_WINDOW)
            self.assertFalse(poller.running)
            self.assertEqual(state["calls"], calls)  # no polls after stop
            return poller

        self.run_async(scenario())
        self.assertEqual(state["maxActive"], 1)
        self.assertGreaterEqual(state["calls"], 4)  # loop survived the exception
        self.assertIsNotNone(published[0])
        self.assertIsNone(published[-1])  # stop clears the displays
        self.assertIn(None, published[1:-1] or [None])

    def test_unchanged_data_published_once(self):
        published = []
        payload = load()

        fetches = []

        async def fetch():
            fetches.append(1)
            return payload

        async def scenario():
            poller = L.LiveClientPoller(fetch=fetch, onData=published.append, interval=0.001)
            poller.start()
            await until(lambda: len(fetches) >= 5, "five polls of the same data")
            poller.stop()
            poller.stop()  # idempotent

        self.run_async(scenario())
        self.assertEqual(len(published), 2)
        self.assertEqual(published[0]["diff"], 8375)
        self.assertIsNone(published[1])

    def test_publish_compares_at_the_displayed_granularity(self):
        """game time is drawn as m:ss and unspent gold as whole gold: a poll that only
        moves their fractions is not re-published; a change that shows is"""
        published = []
        times = iter([(61.2, 500.1), (61.9, 500.4),   # first, then same m:ss / gold
                      (62.0, 500.4),                   # clock ticks
                      (62.5, 500.4),                   # nothing shown changed
                      (62.6, 620.0)])                  # unspent gold changed

        async def fetch():
            gameTime, gold = next(times)
            data = load()
            data["gameData"]["gameTime"] = gameTime
            data["activePlayer"]["currentGold"] = gold
            return data

        async def scenario():
            poller = L.LiveClientPoller(fetch=fetch, onData=published.append, interval=0)
            for _ in range(5):
                summary = L.summarizeGameData(await poller.fetch())
                poller._LiveClientPoller__publish(summary)

        self.run_async(scenario())
        self.assertEqual([(s["gameTime"], s["currentGold"]) for s in published],
                         [(61.2, 500.1), (62.0, 500.4), (62.6, 620.0)])

    def test_failing_callback_does_not_kill_loop(self):
        calls = []

        async def fetch():
            data = load()
            data["gameData"]["gameTime"] = len(calls)
            return data

        def onData(summary):
            calls.append(summary)
            raise ValueError("ui broke")

        async def scenario():
            poller = L.LiveClientPoller(fetch=fetch, onData=onData, interval=0.001)
            poller.start()
            await until(lambda: len(calls) > 2, "the callback to run more than twice")
            self.assertTrue(poller.running)
            poller.stop()

        self.run_async(scenario())
        self.assertGreater(len(calls), 2)



class WidgetTest(unittest.TestCase):
    """Offscreen only: the overlay must never reach a real screen"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        if cls.app.platformName() != "offscreen":
            raise unittest.SkipTest("widget checks run with QT_QPA_PLATFORM=offscreen only")

    def test_overlay_position_parsing(self):
        from app.components.gold_overlay import overlayPosition
        for bad in (["a", 1], [], None, [1, 2, 3], "12", [None, 3], [1e400, 1]):
            self.assertIsNone(overlayPosition(bad), bad)
        point = overlayPosition([10, "20"])
        self.assertEqual((point.x(), point.y()), (10, 20))

    def test_panel_and_overlay_follow_the_signal(self):
        from app.common.config import cfg
        from app.common.signals import signalBus
        from app.components.gold_overlay import GoldOverlay
        from app.components.item_gold_panel import ItemGoldPanel, CONTENT_H

        old = (cfg.enableGoldOverlay.value, cfg.goldOverlayPos.value)
        panel, overlay = ItemGoldPanel(), GoldOverlay()
        try:
            self.assertTrue(panel.isHidden())
            cfg.enableGoldOverlay.value = True  # in memory only, not saved
            cfg.goldOverlayPos.value = ["a", 1]  # corrupted: must not raise
            signalBus.liveGameDataChanged.emit(L.summarizeGameData(load()))
            self.assertFalse(panel.isHidden())
            self.assertFalse(overlay.isVisible())  # offscreen: never shown
            panel.content.resize(1100, CONTENT_H)
            self.assertFalse(panel.content.grab().isNull())

            # no lanes (ARAM) and spectator payloads paint too
            data = load()
            for p in data["allPlayers"]:
                p["position"] = ""
            data.pop("activePlayer")
            signalBus.liveGameDataChanged.emit(L.summarizeGameData(data))
            self.assertFalse(panel.content.grab().isNull())
            self.assertFalse(overlay.grab().isNull())

            signalBus.liveGameDataChanged.emit(None)
            self.assertIsNone(panel.summary)
            self.assertFalse(overlay.isVisible())
        finally:
            cfg.enableGoldOverlay.value, cfg.goldOverlayPos.value = old
            for w in (panel, overlay):
                signalBus.liveGameDataChanged.disconnect(w.setSummary)
                w.deleteLater()


if __name__ == "__main__":
    unittest.main()
