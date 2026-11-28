"""Enemy roster in the live summary (app/lol/live_client.py, contract C1): per-enemy items,
summoner spells, level and position for the cooldown ledger, with no per-poll values and no
player names."""
import copy
import json
import os
import unittest

from app.lol import live_client as L

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "liveclient_allgamedata.json")


def load():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def spell(key):
    return {"displayName": "x", "rawDescription": "d",
            "rawDisplayName": f"GeneratedTip_SummonerSpell_{key}_DisplayName"}


def roster(data):
    return L.summarizeGameData(data)['enemyRoster']


class SpellKeyTest(unittest.TestCase):

    def test_plain_and_unleashed_names(self):
        self.assertEqual(L.spellKey(spell("SummonerFlash")), "SummonerFlash")
        self.assertEqual(L.spellKey(spell("S12_SummonerTeleportUpgrade")),
                         "S12_SummonerTeleportUpgrade")
        self.assertEqual(L.spellKey(spell("S5_SummonerSmitePlayerGanker")),
                         "S5_SummonerSmitePlayerGanker")

    def test_missing_or_malformed_is_empty(self):
        for bad in (None, {}, "x", {"rawDisplayName": None}, {"rawDisplayName": "other"},
                    {"rawDisplayName": "GeneratedTip_SummonerSpell__DisplayName"}):
            self.assertEqual(L.spellKey(bad), "")


class RosterTest(unittest.TestCase):

    def test_raw_champion_name_prefix_stripped(self):
        data = load()
        enemies = [p for p in data['allPlayers'] if p['team'] == 'CHAOS']
        enemies[0]['championName'] = '德迪'  # localized
        enemies[0]['rawChampionName'] = 'game_character_displayname_Darius'
        out = roster(data)
        self.assertEqual(out[0]['rawChampionName'], 'Darius')
        self.assertEqual(out[0]['championName'], '德迪')

    def test_raw_champion_name_missing_or_malformed(self):
        data = load()
        enemies = [p for p in data['allPlayers'] if p['team'] == 'CHAOS']
        enemies[0].pop('rawChampionName', None)
        enemies[1]['rawChampionName'] = 'Vi'
        enemies[2]['rawChampionName'] = None
        enemies[3]['rawChampionName'] = 42
        enemies[4]['rawChampionName'] = 'other_prefix_Thresh'
        self.assertEqual([e['rawChampionName'] for e in roster(data)], [''] * 5)

    def test_raw_champion_name_stable_across_polls(self):
        a = load()
        b = copy.deepcopy(a)
        for p in b['allPlayers']:
            p['respawnTimer'] = 5.0
            p['scores']['kills'] += 1
        self.assertEqual(roster(a), roster(b))
        self.assertEqual([e['rawChampionName'] for e in roster(a)],
                         ['Darius', 'Vi', 'Syndra', 'Caitlyn', 'Thresh'])

    def test_fixture_roster(self):
        data = load()
        enemies = [p for p in data['allPlayers'] if p['team'] == 'CHAOS']
        enemies[0]['items'] = list(reversed(enemies[0]['items']))
        # the fixture's summonerSpells is {}; this synthetic shape follows the real payload path
        # summonerSpells.summonerSpellOne/Two.rawDisplayName
        enemies[0]['summonerSpells'] = {'summonerSpellOne': spell('SummonerFlash'),
                                        'summonerSpellTwo': spell('S12_SummonerTeleportUpgrade')}
        out = roster(data)
        self.assertEqual([e['championName'] for e in out],
                         ['Darius', 'Vi', 'Syndra', 'Caitlyn', 'Thresh'])
        self.assertEqual([e['key'] for e in out],
                         ['TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY'])
        self.assertEqual([e['position'] for e in out], [e['key'] for e in out])
        self.assertEqual(out[0]['spells'], ['SummonerFlash', 'S12_SummonerTeleportUpgrade'])
        self.assertEqual(out[1]['spells'], ['', ''])
        self.assertEqual(out[0]['runeTrees'], [0, 0])   # the fixture's runes is {}
        self.assertEqual(out[0]['level'], enemies[0]['level'])
        # slot order, whatever the payload order
        expected = [i['itemID'] for i in sorted(enemies[0]['items'], key=lambda i: i['slot'])]
        self.assertEqual(out[0]['items'], expected)
        self.assertTrue(expected)

    def test_items_kept_per_enemy(self):
        data = load()
        enemies = [p for p in data['allPlayers'] if p['team'] == 'CHAOS']
        self.assertTrue(any(e['items'] for e in roster(data)))
        for e, p in zip(roster(data), enemies):
            self.assertEqual(len(e['items']), len(p['items']))

    def test_missing_spell_is_empty(self):
        data = load()
        enemies = [p for p in data['allPlayers'] if p['team'] == 'CHAOS']
        # the fixture's summonerSpells is {}; this synthetic shape follows the real payload path
        # summonerSpells.summonerSpellOne/Two.rawDisplayName
        enemies[0]['summonerSpells'] = {'summonerSpellTwo': spell('SummonerFlash')}
        self.assertEqual(roster(data)[0]['spells'], ['', 'SummonerFlash'])

    def test_spectator_has_no_roster(self):
        data = load()
        data['activePlayer'] = {}
        self.assertEqual(roster(data), [])

    def test_one_for_all_distinct_keys(self):
        data = load()
        for p in data['allPlayers']:
            p['championName'] = 'Ahri'
            p['position'] = ''
        out = roster(data)
        self.assertEqual(len(out), 5)
        self.assertEqual([e['key'] for e in out], ['#0', '#1', '#2', '#3', '#4'])
        self.assertEqual(len({e['key'] for e in out}), 5)

    def test_missing_positions_use_index_keys(self):
        data = load()
        enemies = [p for p in data['allPlayers'] if p['team'] == 'CHAOS']
        enemies[1]['position'] = ''
        enemies[2]['position'] = 'MIDDLE'
        enemies[3]['position'] = 'MIDDLE'
        out = roster(data)
        self.assertEqual([e['key'] for e in out], ['TOP', '#1', '#2', '#3', 'UTILITY'])
        self.assertEqual(out[2]['position'], 'MIDDLE')
        self.assertEqual(out[1]['position'], '')

    def test_map_number(self):
        data = load()
        self.assertEqual(L.summarizeGameData(data)['mapNumber'], 11)
        del data['gameData']['mapNumber']
        self.assertEqual(L.summarizeGameData(data)['mapNumber'], 0)

    def test_no_names_in_roster(self):
        data = load()
        text = json.dumps(roster(data))
        for p in data['allPlayers']:
            for k in ('riotId', 'summonerName', 'riotIdGameName'):
                if p.get(k):
                    self.assertNotIn(p[k], text)

    def test_per_poll_values_do_not_change_roster(self):
        a = load()
        b = copy.deepcopy(a)
        for p in b['allPlayers']:
            p['respawnTimer'] = 17.5
            p['isDead'] = True
            p['scores'] = {'kills': 9, 'deaths': 9, 'assists': 9, 'creepScore': 999,
                           'wardScore': 99.0}
            for i in p['items']:
                i['price'] += 1
                i['canUse'] = not i['canUse']
        b['activePlayer']['currentGold'] = 12345.6
        b['gameData']['gameTime'] += 30
        self.assertEqual(roster(a), roster(b))

    def test_rune_trees(self):
        # the payload's shape for other players: keystone + the two trees
        data = load()
        enemies = [p for p in data['allPlayers'] if p['team'] == 'CHAOS']
        enemies[0]['runes'] = {'keystone': {'id': 8214, 'displayName': 'Summon Aery'},
                               'primaryRuneTree': {'id': 8200, 'displayName': 'Sorcery'},
                               'secondaryRuneTree': {'id': 8300, 'displayName': 'Inspiration'}}
        enemies[1]['runes'] = {'primaryRuneTree': {'id': 8300}}
        enemies[2]['runes'] = {'primaryRuneTree': {'id': '8300'}, 'secondaryRuneTree': 'x'}
        enemies[3]['runes'] = None
        out = roster(data)
        self.assertEqual([e['runeTrees'] for e in out[:4]], [[8200, 8300], [8300, 0], [0, 0], [0, 0]])
        self.assertNotEqual(out, roster(load()))

    def test_roster_changes_with_items_spells_level(self):
        base = roster(load())
        data = load()
        enemy = [p for p in data['allPlayers'] if p['team'] == 'CHAOS'][0]
        enemy['items'].pop()
        self.assertNotEqual(roster(data), base)
        data = load()
        enemy = [p for p in data['allPlayers'] if p['team'] == 'CHAOS'][0]
        enemy['level'] += 1
        self.assertNotEqual(roster(data), base)
        data = load()
        enemy = [p for p in data['allPlayers'] if p['team'] == 'CHAOS'][0]
        enemy['summonerSpells'] = {'summonerSpellOne': spell('SummonerFlash')}
        self.assertNotEqual(roster(data), base)

    def test_display_key_ignores_per_poll_values_in_roster(self):
        a = L.summarizeGameData(load())
        data = load()
        for p in data['allPlayers']:
            p['respawnTimer'] = 9.0
        b = L.summarizeGameData(data)
        self.assertEqual(L._displayKey(a), L._displayKey(b))


if __name__ == '__main__':
    unittest.main()
