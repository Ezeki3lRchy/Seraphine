"""Ranked OP Score model (app.lol.opscore) and how tools.scoreGameParticipants uses it.

The fixture is lol-friends-ladder's held-out set (test/fixtures/opgg-games.json, MIT):
10 anonymized ranked games in match-v5 shape with OP.GG's real final scores. Those games
were left out of the model's final fit, so the error here is an honest check of the port.
"""
import asyncio
import copy
import json
import os
import unittest

from app.lol import opscore, tools

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'opscore_opgg_games.json')


def loadGames():
    with open(FIXTURE, encoding='utf-8') as f:
        return json.load(f)


class HeldOutAccuracyTest(unittest.TestCase):

    def test_matches_opgg_on_held_out_games(self):
        errors = []
        for game in loadGames():
            info = game['match']['info']
            scores = opscore.scoreSummary(info)
            self.assertIsNotNone(scores)
            for index, p in enumerate(info['participants']):
                real = game['opgg'][p['puuid']]
                errors.append(abs(round(scores[index + 1], 1) - round(real, 1)))

        self.assertEqual(len(errors), 100)
        # same thresholds as the original repo's test/opscore.test.js
        self.assertLessEqual(sum(errors) / len(errors), 0.45)
        self.assertGreaterEqual(sum(e <= 0.5 + 1e-9 for e in errors), 70)
        self.assertLessEqual(max(errors), 2.0)


class GatingTest(unittest.TestCase):

    def setUp(self):
        self.info = copy.deepcopy(loadGames()[0]['match']['info'])

    def test_only_ranked_queues(self):
        self.assertIsNotNone(opscore.scoreSummary(self.info, 420))
        self.assertIsNotNone(opscore.scoreSummary(self.info, 440))
        for queueId in (450, 400, 1700, 0):
            self.assertIsNone(opscore.scoreSummary(self.info, queueId))

    def test_missing_field_falls_back(self):
        del self.info['participants'][3]['champExperience']
        self.assertIsNone(opscore.scoreSummary(self.info, 420))

    def test_missing_plates_falls_back(self):
        self.info['participants'][0]['challenges'] = {}
        self.assertIsNone(opscore.scoreSummary(self.info, 420))

    def test_remake_is_not_scored(self):
        self.info['participants'][0]['gameEndedInEarlySurrender'] = True
        self.assertIsNone(opscore.scoreSummary(self.info, 420))

    def test_needs_ten_players(self):
        self.info['participants'] = self.info['participants'][:9]
        self.assertIsNone(opscore.scoreSummary(self.info, 420))

    def test_garbage(self):
        self.assertIsNone(opscore.scoreSummary(None, 420))
        self.assertIsNone(opscore.scoreSummary({'participants': 'x'}, 420))


class LcuMatchingTest(unittest.TestCase):

    def summaryAndDetail(self):
        info = copy.deepcopy(loadGames()[0]['match']['info'])
        for i, p in enumerate(info['participants']):
            p['participantId'] = i + 1
            p['championId'] = 100 + i
        detail = {'gameId': 7, 'queueId': 420,
                  'participants': [{'participantId': i + 1, 'championId': 100 + i}
                                   for i in range(10)]}
        return info, detail

    def test_same_players(self):
        info, detail = self.summaryAndDetail()
        self.assertTrue(opscore.matchesLcuDetail(info, detail))

    def test_swapped_players_do_not_match(self):
        info, detail = self.summaryAndDetail()
        detail['participants'][0]['championId'] = 999
        self.assertFalse(opscore.matchesLcuDetail(info, detail))

    def test_scores_for_lcu_detail_uses_summary(self):
        info, detail = self.summaryAndDetail()
        calls = []

        async def fakeSummary(gameId):
            calls.append(gameId)
            return info

        orig = opscore.connector.getGameSummaryViaSgpQuietly
        opscore.connector.getGameSummaryViaSgpQuietly = fakeSummary
        opscore._summaries.clear()
        try:
            scores = asyncio.run(opscore.scoresForLcuDetail(detail))
            again = asyncio.run(opscore.scoresForLcuDetail(detail))
            aram = asyncio.run(opscore.scoresForLcuDetail(dict(detail, queueId=450)))
        finally:
            opscore.connector.getGameSummaryViaSgpQuietly = orig
            opscore._summaries.clear()

        self.assertEqual(sorted(scores), list(range(1, 11)))
        self.assertEqual(scores, again)
        self.assertEqual(calls, [7])  # cached, and ARAM never asks
        self.assertIsNone(aram)


class ScoreGameParticipantsWithModelTest(unittest.TestCase):

    @staticmethod
    def players():
        return [{'id': pid, 'teamId': 100 if pid <= 5 else 200, 'win': pid <= 5,
                 'kills': 1, 'deaths': 1, 'assists': 1, 'damage': 1000,
                 'damageTaken': 1000, 'gold': 1000, 'vision': 1} for pid in range(1, 11)]

    def test_model_scores_drive_rank_and_badges(self):
        model = {pid: 5.0 for pid in range(1, 11)}
        model.update({2: 8.34, 9: 7.96, 4: 2.01})
        result = tools.scoreGameParticipants(self.players(), model)
        self.assertEqual(result[2]['opScore'], 8.3)
        self.assertEqual(result[2]['scoreRank'], 1)
        self.assertEqual(result[2]['badge'], 'MVP')
        self.assertEqual(result[9]['badge'], 'SVP')
        self.assertEqual(result[4]['scoreRank'], 10)

    def test_partial_model_scores_use_formula(self):
        players = self.players()
        partial = {1: 9.9}
        self.assertEqual(tools.scoreGameParticipants(players, partial),
                         tools.scoreGameParticipants(players))


if __name__ == '__main__':
    unittest.main()
