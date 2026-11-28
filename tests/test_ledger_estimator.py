"""Cooldown ledger estimator (app/lol/ledger_model): rank from level and skill priority, and the
cooldown with item haste, on real baseline rows (patch 16.23, tests/fixtures/ledger)."""
import json
import os
import unittest

from app.lol.ledger_model import championEntry, cooldown, estimateRanks

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'ledger', 'baseline_rows_16_23.json')

with open(FIXTURE, encoding='utf-8') as _f:
    DB = json.load(_f)

BOOTS = 3158        # Ionian Boots of Lucidity: 10 ability haste, 10 summoner spell haste
CLEAVER = 3071      # Black Cleaver: 20 ability haste
MALIGNANCE = 3118   # 15 ability haste, 20 ultimate haste
CRIMSON = 3171      # Crimson Lucidity: 20 ability haste, 20 summoner spell haste

# champion, level, items, expected ranks (Q, W, E, R), expected cooldown per cell (None = none)
TABLE = [
    ('Ezreal', 9, [BOOTS, CLEAVER], (5, 1, 2, 1),
     {'Q': 4.5 / 1.3, 'W': 8 / 1.3, 'E': 23 / 1.3, 'R': 120 / 1.3}),
    ('Ezreal', 18, [], (5, 5, 5, 3), {'Q': 4.5, 'E': 14, 'R': 90}),
    # rLevels exception: R from level 1 (free), then 6 / 11 / 16; max rank 4
    ('Karma', 1, [], (1, 0, 0, 1), {'Q': 9, 'W': 12, 'E': 10, 'R': 40}),
    ('Karma', 6, [MALIGNANCE], (3, 1, 1, 2), {'Q': 7 / 1.15, 'R': 38 / 1.35}),
    ('Karma', 16, [], (5, 3, 5, 4), {'R': 34}),
    # max rank 6 on Q / W / E, R a single free rank at level 1
    ('Jayce', 11, [CRIMSON], (6, 4, 1, 1), {'Q': 6 / 1.2, 'W': 10 / 1.2, 'E': 20 / 1.2, 'R': 6 / 1.2}),
    # R ranks like a basic ability (priority QEWR, no rLevels)
    ('Udyr', 10, [CLEAVER], (5, 1, 3, 1), {'Q': 5.0, 'R': 5.0}),
    # Q max rank 6; W has no cooldown in the data (inestimable)
    ('Yuumi', 12, [], (6, 1, 3, 2), {'Q': 6.5, 'W': None, 'R': 110}),
    ('Ahri', 13, [BOOTS], (5, 5, 1, 2), {'W': 5 / 1.1, 'E': 12 / 1.1, 'R': 120 / 1.1}),
    ('Lee Sin', 18, [CLEAVER], (5, 5, 5, 3), {'Q': 6 / 1.2, 'R': 60 / 1.2}),
]


class EstimatorTableTest(unittest.TestCase):
    def test_table(self):
        for champion, level, items, ranks, expected in TABLE:
            row = DB['champions'][champion]
            with self.subTest(champion=champion, level=level):
                got = estimateRanks(level, row['priority'], row['maxRank'], row['rLevels'])
                self.assertEqual((got['Q'], got['W'], got['E'], got['R']), ranks)
                for cell, seconds in expected.items():
                    value = cooldown(DB, champion, cell, level, items)
                    if seconds is None:
                        self.assertIsNone(value, cell)
                    else:
                        self.assertAlmostEqual(value, seconds, places=6, msg=cell)

    def test_fixture_rows_are_baseline_shaped(self):
        self.assertGreaterEqual(len(DB['champions']), 5)
        for name, row in DB['champions'].items():
            self.assertEqual(set(row['spells']), {'Q', 'W', 'E', 'R'}, name)
            for slot, spell in row['spells'].items():
                if spell['cooldown'] is not None:
                    self.assertEqual(len(spell['cooldown']), row['maxRank'][slot], f'{name} {slot}')


class RankRuleTest(unittest.TestCase):
    def test_basic_rank_needs_level(self):
        # Ezreal at 8: Q rank 5 needs level 9, so the point goes to E
        self.assertEqual(estimateRanks(8, 'QEW', None, None), {'Q': 4, 'W': 1, 'E': 2, 'R': 1})
        # first three points one each, in priority order
        self.assertEqual(estimateRanks(2, 'EWQ', None, None), {'Q': 0, 'W': 1, 'E': 1, 'R': 0})
        self.assertEqual(estimateRanks(3, 'EWQ', None, None), {'Q': 1, 'W': 1, 'E': 1, 'R': 0})

    def test_r_takes_its_levels(self):
        for level, r in ((5, 0), (6, 1), (10, 1), (11, 2), (16, 3)):
            self.assertEqual(estimateRanks(level, 'QWE', None, None)['R'], r, level)

    def test_points_add_up(self):
        for level in range(1, 19):
            ranks = estimateRanks(level, 'QWE', None, None)
            self.assertEqual(sum(ranks.values()), level, level)

    def test_level_clamped_and_bad_priority_defaults(self):
        self.assertEqual(estimateRanks(30, 'QWE', None, None), estimateRanks(18, 'QWE', None, None))
        self.assertEqual(estimateRanks(0, 'QWE', None, None), {'Q': 0, 'W': 0, 'E': 0, 'R': 0})
        for bad in (None, '', 'QQE', 'QW', 'XYZ', 5):
            self.assertEqual(estimateRanks(9, bad, None, None), estimateRanks(9, 'QWE', None, None), bad)


class CooldownCellTest(unittest.TestCase):
    def test_unlearned_ability_counts_as_rank_one(self):
        # Ezreal at level 1 has only Q; a seen E still has its rank-1 cooldown
        self.assertEqual(cooldown(DB, 'Ezreal', 'E', 1, []), 26)
        self.assertEqual(cooldown(DB, 'Ezreal', 'R', 3, []), 120)

    def test_haste_of_every_held_item_counts(self):
        self.assertAlmostEqual(cooldown(DB, 'Ezreal', 'E', 18, [CLEAVER, CLEAVER]), 14 / 1.4)
        self.assertEqual(cooldown(DB, 'Ezreal', 'E', 18, [1001, 9999999]), 14)   # unknown ids: no haste

    def test_ultimate_haste_only_on_r(self):
        self.assertAlmostEqual(cooldown(DB, 'Ezreal', 'Q', 18, [MALIGNANCE]), 4.5 / 1.15)
        self.assertAlmostEqual(cooldown(DB, 'Ezreal', 'R', 18, [MALIGNANCE]), 90 / 1.35)

    def test_summoner_spells(self):
        spells = ['SummonerDot', 'SummonerFlash']
        self.assertEqual(cooldown(DB, 'Ahri', 'D', 9, [], spells=spells), 300)      # Flash column
        self.assertEqual(cooldown(DB, 'Ahri', 'F', 9, [], spells=spells), 180)
        self.assertAlmostEqual(cooldown(DB, 'Ahri', 'D', 9, [BOOTS], spells=spells), 300 / 1.1)
        self.assertAlmostEqual(cooldown(DB, 'Ahri', 'SummonerFlash', 9, [CRIMSON]), 250)
        self.assertEqual(cooldown(DB, 'Ahri', 'TP', 9, []), 300)
        self.assertAlmostEqual(cooldown(DB, 'Ahri', 'Q', 9, [CRIMSON]), 7 / 1.2)   # ability haste too

    def test_hexflash(self):
        # its own cooldown from the client data, whatever the two spells; summoner spell haste counts
        self.assertEqual(cooldown(DB, 'Ahri', 'HF', 9, [], spells=['SummonerDot', 'SummonerFlash']), 20)
        self.assertAlmostEqual(cooldown(DB, 'Ahri', 'HF', 9, [BOOTS, CRIMSON]), 20 / 1.3)
        self.assertAlmostEqual(cooldown(DB, 'Ahri', 'HF', 9, [CLEAVER]), 20)    # ability haste does not
        self.assertIsNone(cooldown({'summonerSpells': {}}, 'Ahri', 'HF', 9, []))  # an older database
        # a Flash slot reporting Hexflash is still the Flash cell
        for key in ('SummonerFlashPerksHextechFlashtraptionV2', 'SummonerFlashPerksHextechFlashtraption'):
            self.assertEqual(cooldown(DB, 'Ahri', 'D', 9, [], spells=['SummonerDot', key]), 300, key)
            self.assertEqual(cooldown(DB, 'Ahri', 'F', 9, [], spells=['SummonerDot', key]), 180, key)

    def test_inestimable_cells(self):
        self.assertIsNone(cooldown(DB, 'Lee Sin', 'F', 9, [], spells=['SummonerFlash', 'SummonerSmite']))
        self.assertIsNone(cooldown(DB, 'Lee Sin', 'F', 9, [],
                                   spells=['SummonerFlash', 'S5_SummonerSmitePlayerGanker']))
        self.assertEqual(cooldown(DB, 'Ahri', 'F', 9, [], spells=['SummonerFlash', 'S12_SummonerTeleportUpgrade']),
                         300)                                           # Unleashed Teleport: client data
        self.assertIsNone(cooldown(DB, 'Ahri', 'F', 9, [], spells=['SummonerFlash', '']))
        self.assertIsNone(cooldown(DB, 'Ahri', 'D', 9, []))
        self.assertIsNone(cooldown(DB, 'Ahri', 'item:3158', 9, [BOOTS]))   # no active
        self.assertIsNone(cooldown(DB, 'Ahri', None, 9, []))                 # an empty item slot
        self.assertIsNone(cooldown(DB, 'Nobody', 'Q', 9, []))

    def test_item_actives(self):
        self.assertEqual(cooldown(DB, 'Ahri', 'item:3157', 9, [3157]), 120)
        self.assertEqual(cooldown(DB, 'Ahri', 'item:3142', 9, [3142]), 45)
        # ability haste does not shorten item actives
        self.assertEqual(cooldown(DB, 'Ahri', 'item:6698', 9, [6698, CLEAVER]), 10)
        self.assertIsNone(cooldown(DB, 'Ahri', 'item:999999', 9, [999999]))

    def test_champion_lookup_by_data_dragon_id(self):
        for raw, name in (('MonkeyKing', 'Wukong'), ('Nunu', 'Nunu & Willump'), ('Renata', 'Renata Glasc'),
                          ('KaiSa', "Kai'Sa"), ('monkeyking', 'Wukong')):
            with self.subTest(raw):
                self.assertIs(championEntry(DB, raw), DB['champions'][name])
        self.assertEqual(cooldown(DB, 'MonkeyKing', 'R', 6, []), DB['champions']['Wukong']['spells']['R']['cooldown'][0])

    def test_champion_lookup(self):
        row = DB['champions']["Kai'Sa"]
        self.assertIs(championEntry(DB, "Kai'Sa"), row)
        self.assertIs(championEntry(DB, 'KaiSa'), row)
        self.assertIs(championEntry(DB, 'lee sin'), DB['champions']['Lee Sin'])
        self.assertIs(championEntry(DB, row['championId']), row)
        self.assertIsNone(championEntry(DB, ''))


if __name__ == '__main__':
    unittest.main()
