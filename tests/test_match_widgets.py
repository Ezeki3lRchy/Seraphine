"""Pure helpers behind the OP.GG-style widgets (item tooltips, game tags, times)."""
import time
import unittest

from app.components import match_widgets as mw
from app.components import career_widgets as cw


class ItemDescriptionTest(unittest.TestCase):

    def test_stats_and_emphasis(self):
        desc = ("<mainText><stats><attention> 75</attention> Attack Damage<br>"
                "<attention> 25%</attention> Critical Strike Chance</stats><br><br>"
                "<passive>Spellblade</passive><br>After using an Ability...</mainText>")
        out = mw.itemDescriptionToHtml(desc)
        self.assertEqual(out, "<b> 75</b> Attack Damage<br><b> 25%</b> Critical Strike Chance"
                              "<br><br><b>Spellblade</b><br>After using an Ability...")

    def test_unknown_tags_are_stripped_and_list_items_bulleted(self):
        out = mw.itemDescriptionToHtml("<scaleAD>bonus</scaleAD><li>one<li>two")
        self.assertEqual(out, "bonus<br>• one<br>• two")

    def test_empty(self):
        self.assertEqual(mw.itemDescriptionToHtml(""), "")
        self.assertEqual(mw.itemTooltipHtml(0), "")


class GameTagsTest(unittest.TestCase):

    def test_multikill_then_badge(self):
        tags = mw.gameTags({"remake": False, "multiKill": 3, "badge": "MVP", "scoreRank": 1})
        self.assertEqual([t for t, _ in tags], ["Triple kill", "MVP"])

    def test_rank_when_no_badge(self):
        tags = mw.gameTags({"remake": False, "multiKill": 1, "badge": None, "scoreRank": 7})
        self.assertEqual([t for t, _ in tags], ["7th"])

    def test_penta_cap_and_remake(self):
        self.assertEqual(mw.gameTags({"remake": False, "multiKill": 6})[0][0], "Penta kill")
        self.assertEqual(mw.gameTags({"remake": True, "multiKill": 5, "badge": "MVP"}), [])

    def test_ordinal(self):
        self.assertEqual([mw.ordinal(n) for n in (1, 2, 3, 4, 10, 11, 12, 13, 21, 22)],
                         ["1st", "2nd", "3rd", "4th", "10th", "11th", "12th", "13th",
                          "21st", "22nd"])

    def test_rank_emblem_path(self):
        self.assertEqual(mw.rankEmblemPath("DIAMOND"), "app/resource/images/diamond.png")
        self.assertEqual(mw.rankEmblemPath(""), "app/resource/images/unranked.png")
        self.assertEqual(mw.rankEmblemPath("NONE"), "app/resource/images/unranked.png")


class CareerTextTest(unittest.TestCase):

    def test_time_ago(self):
        now = time.time() * 1000
        self.assertEqual(cw.timeAgo(now), "just now")
        self.assertEqual(cw.timeAgo(now - 3 * 60_000), "3 minutes ago")
        self.assertEqual(cw.timeAgo(now - 60 * 60_000), "1 hour ago")
        self.assertEqual(cw.timeAgo(now - 2 * 86_400_000), "2 days ago")

    def test_tier_text_and_duration(self):
        self.assertEqual(cw.tierText("DIAMOND", "II"), "Diamond II")
        self.assertEqual(cw.tierText("MASTER", "NA"), "Master")
        self.assertEqual(cw.tierText("", "I"), "")
        self.assertEqual(cw.durationText(1553), "25m 53s")


if __name__ == "__main__":
    unittest.main()
