"""MVP / SVP scoring in app.lol.tools (game-info page badges).

scoreGameParticipants is pure; the LCU and SGP adapters run on hand-built
10-player payloads shaped like the real responses (LCU
/lol-match-history/v1/games/{id}, SGP match json).
"""
import unittest

from app.lol import tools


def player(pid, teamId, win, k, d, a, damage=10000, taken=10000, gold=10000, vision=20):
    return {"id": pid, "teamId": teamId, "win": win, "kills": k, "deaths": d,
            "assists": a, "damage": damage, "damageTaken": taken, "gold": gold,
            "vision": vision}


def tenPlayers():
    """Team 100 wins; pid 3 is clearly best on the winners, pid 8 on the losers."""
    players = []
    for pid in range(1, 6):
        players.append(player(pid, 100, True, 2, 2, 3))
    for pid in range(6, 11):
        players.append(player(pid, 200, False, 1, 3, 1))
    players[2].update(kills=10, deaths=0, assists=8, damage=40000, gold=16000)
    players[7].update(kills=5, deaths=2, assists=2, damage=30000, gold=13000)
    return players


class ScoreGameParticipantsTest(unittest.TestCase):

    def test_mvp_and_svp(self):
        result = tools.scoreGameParticipants(tenPlayers())
        badges = {pid: r["badge"] for pid, r in result.items() if r["badge"]}
        self.assertEqual(badges, {3: "MVP", 8: "SVP"})
        self.assertEqual(result[3]["scoreRank"], 1)

    def test_ranks_are_a_permutation(self):
        result = tools.scoreGameParticipants(tenPlayers())
        self.assertEqual(sorted(r["scoreRank"] for r in result.values()),
                         list(range(1, 11)))

    def test_kill_participation(self):
        result = tools.scoreGameParticipants(tenPlayers())
        # team 100 kills: 2 * 4 + 10 = 18; pid 3 has 10 + 8
        self.assertAlmostEqual(result[3]["kp"], 18 / 18)
        self.assertAlmostEqual(result[1]["kp"], 5 / 18)

    def test_zero_kills_and_zero_stats_do_not_divide_by_zero(self):
        players = [player(pid, 100 if pid <= 5 else 200, pid <= 5, 0, 0, 0,
                          damage=0, taken=0, gold=0, vision=0)
                   for pid in range(1, 11)]
        result = tools.scoreGameParticipants(players)
        self.assertEqual(result[1]["kp"], 0)
        # all tied at 0: deterministic, lowest participantId wins each side
        self.assertEqual(result[1]["badge"], "MVP")
        self.assertEqual(result[6]["badge"], "SVP")

    def test_tie_broken_by_damage(self):
        players = tenPlayers()
        players[0].update(kills=10, deaths=0, assists=8, damage=40000, gold=16000)
        players[2].update(damage=39999)
        players[0]["damageTaken"] = players[2]["damageTaken"]
        result = tools.scoreGameParticipants(players)
        self.assertEqual(result[1]["badge"], "MVP")

    def test_no_badges_without_two_sided_result(self):
        players = [player(pid, 100, True, 1, 1, 1) for pid in range(1, 6)]
        result = tools.scoreGameParticipants(players)
        self.assertTrue(all(r["badge"] is None for r in result.values()))
        self.assertEqual(result[1]["scoreRank"], 1)

    def test_empty(self):
        self.assertEqual(tools.scoreGameParticipants([]), {})


def lcuDetail(players, queueId=420, remakePid=None, subteam=False):
    return {
        "queueId": queueId,
        "participantIdentities": [
            {"participantId": p["id"], "player": {"puuid": f"puuid-{p['id']}"}}
            for p in players],
        "participants": [
            {"participantId": p["id"], "teamId": p["teamId"], "stats": {
                "win": p["win"], "kills": p["kills"], "deaths": p["deaths"],
                "assists": p["assists"],
                "totalDamageDealtToChampions": p["damage"],
                "totalDamageTaken": p["damageTaken"], "goldEarned": p["gold"],
                "visionScore": p["vision"],
                "gameEndedInEarlySurrender": p["id"] == remakePid,
                "playerSubteamId": 1 if subteam else 0}}
            for p in players],
    }


def sgpGame(players, queueId=420):
    return {"queueId": queueId, "participants": [
        {"participantId": p["id"], "puuid": f"puuid-{p['id']}",
         "teamId": p["teamId"], "win": p["win"], "kills": p["kills"],
         "deaths": p["deaths"], "assists": p["assists"],
         "totalDamageDealtToChampions": p["damage"],
         "totalDamageTaken": p["damageTaken"], "goldEarned": p["gold"],
         "visionScore": p["vision"], "gameEndedInEarlySurrender": False,
         "playerSubteamId": 0}
        for p in players]}


class AdapterTest(unittest.TestCase):

    def test_lcu_adapter(self):
        detail = lcuDetail(tenPlayers())
        self.assertEqual(tools.getGameScoreFromLcuDetail(detail, "puuid-3")["badge"], "MVP")
        self.assertEqual(tools.getGameScoreFromLcuDetail(detail, "puuid-8")["badge"], "SVP")
        other = tools.getGameScoreFromLcuDetail(detail, "puuid-1")
        self.assertIsNone(other["badge"])
        self.assertEqual(other["scorePlayers"], 10)

    def test_sgp_adapter_matches_lcu(self):
        lcu = tools.getGameScoreFromLcuDetail(lcuDetail(tenPlayers()), "puuid-5")
        sgp = tools.getGameScoreFromSgpGame(sgpGame(tenPlayers()), "puuid-5")
        self.assertEqual(lcu, sgp)

    def test_unknown_puuid_or_bad_payload(self):
        detail = lcuDetail(tenPlayers())
        self.assertIsNone(tools.getGameScoreFromLcuDetail(detail, "nobody"))
        self.assertIsNone(tools.getGameScoreFromLcuDetail(None, "puuid-3"))
        self.assertIsNone(tools.getGameScoreFromLcuDetail({"participants": [{}]}, "x"))
        self.assertIsNone(tools.getGameScoreFromSgpGame({"participants": "?"}, "x"))

    def test_remake_has_rank_but_no_badge(self):
        detail = lcuDetail(tenPlayers(), remakePid=3)
        score = tools.getGameScoreFromLcuDetail(detail, "puuid-3")
        self.assertIsNone(score["badge"])
        self.assertEqual(score["scoreRank"], 1)

    def test_arena_is_skipped(self):
        self.assertIsNone(tools.getGameScoreFromLcuDetail(
            lcuDetail(tenPlayers(), queueId=1700), "puuid-3"))
        self.assertIsNone(tools.getGameScoreFromLcuDetail(
            lcuDetail(tenPlayers(), subteam=True), "puuid-3"))
        self.assertIsNone(tools.getGameScoreFromSgpGame(
            sgpGame(tenPlayers(), queueId=1700), "puuid-3"))


class LcuDetailPlayersTest(unittest.TestCase):

    def test_extracts_all_players(self):
        players = tools.lcuDetailPlayers(lcuDetail(tenPlayers()))
        self.assertEqual(len(players), 10)
        self.assertEqual(players[2]["kills"], 10)
        self.assertEqual(players[2]["damage"], 40000)

    def test_subteams_and_bad_payload(self):
        self.assertEqual(tools.lcuDetailPlayers(lcuDetail(tenPlayers(), subteam=True)), [])
        self.assertEqual(tools.lcuDetailPlayers({"participants": [{}]}), [])
        self.assertEqual(tools.lcuDetailPlayers(None), [])


class SummaryBadgeCountTest(unittest.TestCase):

    def test_counts(self):
        games = [{"championId": 1, "win": True, "remake": False, "kills": 1,
                  "deaths": 1, "assists": 1, "badge": b}
                 for b in ("MVP", "MVP", "SVP", None)]
        s = tools.summarizeGames(games)
        self.assertEqual((s["mvp"], s["svp"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
