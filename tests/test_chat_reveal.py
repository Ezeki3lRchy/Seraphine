"""chat_reveal.mergeHiddenTeammates — pure merge of Riot Client chat
participants into champ-select myTeam (reveal hidden teammates, task 3)."""
import copy
import unittest

from app.lol.chat_reveal import (mergeHiddenTeammates, isHiddenSlot,
                                 revealStats, SYNTH_CELL_BASE)

ME = "me-puuid"


def visible(cellId, puuid, summonerId, position="top", championId=0):
    return {"cellId": cellId, "puuid": puuid, "summonerId": summonerId,
            "assignedPosition": position, "championId": championId,
            "selectedPosition": position, "nameVisibilityType": "VISIBLE",
            "playerType": "PLAYER", "gameName": f"name-{summonerId}"}


def hidden(cellId, puuid="", position="jungle", championId=0):
    return {"cellId": cellId, "puuid": puuid, "summonerId": 0,
            "assignedPosition": position, "championId": championId,
            "selectedPosition": position, "nameVisibilityType": "HIDDEN",
            "playerType": "PLAYER", "gameName": ""}


def bot(cellId):
    return {"cellId": cellId, "puuid": "", "summonerId": 0,
            "assignedPosition": "", "championId": 1,
            "selectedPosition": "", "nameVisibilityType": "VISIBLE",
            "playerType": "BOT"}


def participant(puuid, name="X", tag="NA1"):
    return {"puuid": puuid, "game_name": name, "game_tag": tag,
            "cid": f"{puuid}@champ-select.na1.pvp.net"}


class IsHiddenSlotTest(unittest.TestCase):

    def test_table(self):
        self.assertFalse(isHiddenSlot(visible(0, ME, 11)))
        self.assertTrue(isHiddenSlot(hidden(1)))
        self.assertTrue(isHiddenSlot({"summonerId": 0, "playerType": "PLAYER"}))
        self.assertTrue(isHiddenSlot({"playerType": "PLAYER"}))  # missing id
        self.assertFalse(isHiddenSlot(bot(2)))
        self.assertFalse(isHiddenSlot({"summonerId": 0, "revealed": True}))
        self.assertFalse(isHiddenSlot("garbage"))


class MergeHiddenTeammatesTest(unittest.TestCase):

    def test_all_visible_unchanged(self):
        team = [visible(i, f"p{i}", 10 + i) for i in range(5)]
        team[0]["puuid"] = ME
        original = copy.deepcopy(team)
        merged = mergeHiddenTeammates(
            team, [participant(f"p{i}") for i in range(5)], ME)
        self.assertEqual(merged, original)
        self.assertIsNot(merged, team)
        self.assertEqual(team, original)  # input not mutated

    def test_four_hidden_plus_five_participants_including_me(self):
        team = [visible(0, ME, 11)] + [hidden(i) for i in range(1, 5)]
        original = copy.deepcopy(team)
        parts = [participant(ME, "Me")] + [
            participant(f"h{i}", f"Hidden{i}") for i in range(1, 5)]

        merged = mergeHiddenTeammates(team, parts, ME)

        synth = [it for it in merged if it.get("synthesized")]
        self.assertEqual(len(synth), 4)
        self.assertEqual([it["cellId"] for it in synth],
                         [SYNTH_CELL_BASE + i for i in range(4)])
        self.assertEqual([it["puuid"] for it in synth],
                         ["h1", "h2", "h3", "h4"])
        for it in synth:
            self.assertEqual(it["summonerId"], 0)
            self.assertEqual(it["championId"], 0)
            self.assertIsNone(it["selectedPosition"])
            self.assertEqual(it["nameVisibilityType"], "VISIBLE")
            self.assertTrue(it["revealed"])
        # me untouched, the 4 HIDDEN originals still present (downstream drops them)
        self.assertEqual(merged[0], original[0])
        self.assertEqual(merged[1:5], original[1:5])
        self.assertEqual(len(merged), 9)
        self.assertEqual(revealStats(merged), (1, 0, 4, 4))
        self.assertEqual(team, original)

    def test_puuid_pairing_keeps_slot(self):
        team = [visible(0, ME, 11), hidden(3, puuid="h3", position="support",
                                            championId=99)]
        merged = mergeHiddenTeammates(
            team, [participant("h3", "Paired", "EUW")], ME)

        self.assertEqual(len(merged), 2)
        got = merged[1]
        self.assertEqual(got["cellId"], 3)
        self.assertEqual(got["assignedPosition"], "support")
        self.assertEqual(got["championId"], 99)
        self.assertEqual(got["puuid"], "h3")
        self.assertEqual(got["gameName"], "Paired")
        self.assertEqual(got["tagLine"], "EUW")
        self.assertEqual(got["nameVisibilityType"], "VISIBLE")
        self.assertTrue(got["revealed"])
        self.assertFalse(got["synthesized"])
        self.assertFalse(isHiddenSlot(got))
        self.assertEqual(revealStats(merged), (1, 1, 0, 0))

    def test_bot_untouched(self):
        team = [visible(0, ME, 11), bot(1), hidden(2)]
        original = copy.deepcopy(team)
        merged = mergeHiddenTeammates(
            team, [participant("h2"), participant("extra")], ME)

        self.assertEqual(merged[1], original[1])
        synth = [it for it in merged if it.get("synthesized")]
        # only ONE hidden slot → only one synthesized, bot never counted as hidden
        self.assertEqual([it["puuid"] for it in synth], ["h2"])

    def test_empty_participants_unchanged(self):
        team = [visible(0, ME, 11)] + [hidden(i) for i in range(1, 5)]
        original = copy.deepcopy(team)
        self.assertEqual(mergeHiddenTeammates(team, [], ME), original)
        self.assertEqual(mergeHiddenTeammates(team, None, ME), original)

    def test_synthesized_capped_by_hidden_count(self):
        team = [visible(0, ME, 11), visible(1, "v1", 12),
                visible(2, "v2", 13), visible(3, "v3", 14), hidden(4)]
        parts = [participant(f"u{i}") for i in range(3)]
        merged = mergeHiddenTeammates(team, parts, ME)
        self.assertEqual(len(merged), 6)  # 5 originals + exactly 1 synthesized
        self.assertEqual(sum(1 for it in merged if it.get("synthesized")), 1)
        self.assertEqual(merged[-1]["puuid"], "u0")

    def test_skips_duplicates_visible_and_me(self):
        team = [visible(0, ME, 11), visible(1, "v1", 12), hidden(2), hidden(3)]
        parts = [participant(ME), participant("v1"), participant("n1"),
                 participant("n1"), {"puuid": ""}, "junk", participant("n2")]
        merged = mergeHiddenTeammates(team, parts, ME)
        synth = [it["puuid"] for it in merged if it.get("synthesized")]
        self.assertEqual(synth, ["n1", "n2"])

    def test_mixed_pair_then_synthesize_order(self):
        team = [visible(0, ME, 11), hidden(1, puuid="k1"), hidden(2)]
        parts = [participant("s1"), participant("k1")]
        merged = mergeHiddenTeammates(team, parts, ME)

        self.assertEqual(merged[1]["puuid"], "k1")      # paired in place
        self.assertEqual(merged[1]["cellId"], 1)
        self.assertTrue(merged[1]["revealed"])
        self.assertEqual(merged[2], hidden(2))          # unmatched hidden kept
        self.assertEqual(merged[3]["puuid"], "s1")      # synthesized appended
        self.assertEqual(merged[3]["cellId"], SYNTH_CELL_BASE)
        self.assertEqual(revealStats(merged), (1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
