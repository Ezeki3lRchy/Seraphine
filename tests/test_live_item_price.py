"""Live item value at full item prices (app/lol/live_client.py fullPrice lookup).

The game's Live Client Data `price` is only an item's own combine (recipe) cost; the full
price comes from the client's items.json (priceTotal). The id / price pairs below are from
a real game payload. No network."""
import asyncio
import unittest
import unittest.mock

from app.lol import live_client as L

# itemID -> full price (items.json priceTotal)
FULL = {
    1001: 300,   # Boots
    1036: 350,   # Long Sword
    1055: 450,   # Doran's Blade
    1037: 875,   # Pickaxe
    1029: 300,   # Cloth Armor
    2003: 50,    # Health Potion
    1053: 900,   # Vampiric Scepter (payload price 550)
    3077: 1200,  # Tiamat (payload 500)
    3145: 1100,  # Hextech Alternator (payload 300)
    3067: 800,   # Kindlegem (payload 150)
    6692: 2900,  # Eclipse (payload 625)
    3340: 0,     # Stealth Ward (trinket)
}


def lookup(itemId):
    return FULL.get(itemId)


def entry(itemId, price, count=1, consumable=False):
    return {"itemID": itemId, "price": price, "count": count, "consumable": consumable}


def player(team, position, champion, items, riotId):
    return {"team": team, "position": position, "championName": champion,
            "riotId": riotId, "riotIdGameName": riotId.split('#')[0], "items": items}


def game():
    allies = [
        # old 450 + 625 + 2 x 50 = 1175, new 450 + 2900 + 100 = 3450
        player("ORDER", "TOP", "Garen",
               [entry(1055, 450), entry(6692, 625), entry(2003, 50, 2, True),
                entry(3340, 0)], "A1#T"),
        # basic items only: 1225 either way
        player("ORDER", "JUNGLE", "Lee Sin", [entry(1037, 875), entry(1036, 350)], "A2#T"),
    ]
    enemies = [
        # two non-stacking Cloth Armor are two entries: old 550 + 600 = 1150, new 1500
        player("CHAOS", "TOP", "Darius",
               [entry(1053, 550), entry(1029, 300), entry(1029, 300), entry(3340, 0)], "E1#T"),
        # old 300 + 150 = 450, new 1100 + 800 = 1900
        player("CHAOS", "JUNGLE", "Vi", [entry(3145, 300), entry(3067, 150)], "E2#T"),
    ]
    return {"activePlayer": {"riotId": "A1#T", "currentGold": 100.0},
            "allPlayers": allies + enemies,
            "gameData": {"gameMode": "CLASSIC", "gameTime": 600.0}}


class FullPriceItemValueTest(unittest.TestCase):

    def test_built_item_at_full_price(self):
        self.assertEqual(L.itemValue(entry(1053, 550), lookup), 900)
        self.assertEqual(L.itemValue(entry(3077, 500), lookup), 1200)
        self.assertEqual(L.itemValue(entry(6692, 625), lookup), 2900)

    def test_basic_item_unchanged(self):
        for itemId, price in ((1001, 300), (1036, 350), (1055, 450), (1037, 875)):
            self.assertEqual(L.itemValue(entry(itemId, price), lookup), price)

    def test_fallback_to_payload_price(self):
        def raises(itemId):
            raise KeyError(itemId)

        for bad in (lambda i: None, raises, lambda i: 0, lambda i: -5, lambda i: "900",
                    lambda i: float("nan"), lambda i: float("inf"), lambda i: True):
            self.assertEqual(L.itemValue(entry(1053, 550), bad), 550)
        self.assertEqual(L.itemValue(entry(9999, 550), lookup), 550)  # unknown id
        self.assertEqual(L.itemValue(entry(1053, 550), None), 550)    # no lookup
        self.assertEqual(L.itemValue(entry(1053, 550)), 550)

    def test_trinket_stays_free(self):
        self.assertEqual(L.itemValue(entry(3340, 0), lookup), 0)
        self.assertEqual(L.itemValue(entry(3340, 0), lambda i: None), 0)

    def test_consumable_stack_multiplies_full_price(self):
        self.assertEqual(L.itemValue(entry(2003, 50, 2, True), lookup), 100)
        self.assertEqual(L.itemValue(entry(2003, 50, 1, True), lookup), 50)
        self.assertEqual(L.itemValue(entry(2003, 20, 2, True), lookup), 100)
        # a count on a non-consumable never multiplies
        self.assertEqual(L.itemValue(entry(1053, 550, 3), lookup), 900)

    def test_duplicated_non_stacking_entries_count_once_each(self):
        darius = game()["allPlayers"][2]
        self.assertEqual(L.playerItemValue(darius, lookup), 900 + 300 + 300)

    def test_reported_top_laner(self):
        garen = game()["allPlayers"][0]
        self.assertEqual(L.playerItemValue(garen), 1175)
        self.assertEqual(L.playerItemValue(garen, lookup), 3450)


class FullPriceSummaryTest(unittest.TestCase):

    def test_totals_lanes_and_order(self):
        old = L.summarizeGameData(game())
        new = L.summarizeGameData(game(), lookup)
        self.assertEqual((old["allyTotal"], old["enemyTotal"], old["diff"]),
                         (1175 + 1225, 1150 + 450, 800))
        self.assertEqual((new["allyTotal"], new["enemyTotal"], new["diff"]),
                         (3450 + 1225, 1500 + 1900, 1275))
        self.assertEqual([lane["diff"] for lane in old["lanes"]], [25, 775])
        self.assertEqual([lane["diff"] for lane in new["lanes"]], [1950, -675])
        self.assertEqual([lane["ally"]["value"] for lane in new["lanes"]], [3450, 1225])
        self.assertEqual([lane["enemy"]["value"] for lane in new["lanes"]], [1500, 1900])
        self.assertEqual([p["championName"] for p in old["allyPlayers"]], ["Lee Sin", "Garen"])
        self.assertEqual([p["championName"] for p in new["allyPlayers"]], ["Garen", "Lee Sin"])
        self.assertEqual([p["championName"] for p in new["enemyPlayers"]], ["Vi", "Darius"])
        self.assertEqual(set(new), set(old))  # same shape

    def test_failing_lookup_degrades_to_payload_prices(self):
        def raises(itemId):
            raise RuntimeError("manager not ready")

        self.assertEqual(L.summarizeGameData(game(), raises), L.summarizeGameData(game()))


class PollerFullPriceTest(unittest.TestCase):

    def run_poller(self, poller, published):
        async def scenario():
            poller.start()
            for _ in range(2000):
                if published:
                    break
                await asyncio.sleep(0.005)
            poller.stop()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(scenario())
        finally:
            loop.close()
        self.assertTrue(published, "the poller published nothing")
        return published[0]

    def test_lookup_set_on_the_poller_is_used(self):
        async def fetch():
            return game()

        published = []
        poller = L.LiveClientPoller(fetch=fetch, onData=published.append, interval=0.001)
        poller.fullPrice = lookup  # how the main window wires it
        summary = self.run_poller(poller, published)
        self.assertEqual(summary["allyTotal"], 3450 + 1225)
        self.assertEqual(summary["enemyTotal"], 1500 + 1900)

    def test_no_lookup_uses_payload_prices(self):
        async def fetch():
            return game()

        published = []
        poller = L.LiveClientPoller(fetch=fetch, onData=published.append, interval=0.001)
        summary = self.run_poller(poller, published)
        self.assertEqual(summary["allyTotal"], 1175 + 1225)


class MainWindowLookupTest(unittest.TestCase):
    """The lookup the main window gives the poller reads the connector's items.json data"""

    def setUp(self):
        from app.view import main_window as mw
        self.mw = mw

    def test_wired_to_the_shared_poller(self):
        self.assertIs(L.liveClientPoller.fullPrice, self.mw.itemFullPrice)

    def test_reads_price_total_and_handles_missing_manager(self):
        class Manager:
            def getItemInfo(self, itemId):
                return {1053: {"name": "x", "priceTotal": 900}}.get(itemId)

        with unittest.mock.patch.object(self.mw.connector, "manager", Manager()):
            self.assertEqual(self.mw.itemFullPrice(1053), 900)
            self.assertIsNone(self.mw.itemFullPrice(9999))
        with unittest.mock.patch.object(self.mw.connector, "manager", None):
            self.assertIsNone(self.mw.itemFullPrice(1053))


if __name__ == "__main__":
    unittest.main()
