"""Saved players store (app/lol/saved_players.py): order, cap, notes, display cache and a
corrupt file, against a temp file (never the real config)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from app.lol import saved_players as sp


class SavedPlayersStoreTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='seraphine-saved-')
        self.path = os.path.join(self.dir, 'saved_players.json')
        for name, value in (('storePath', self.path), ('_data', None), ('_dataPath', None)):
            p = mock.patch.object(sp, name, value)
            p.start()
            self.addCleanup(p.stop)

    def reload(self):
        """drop the in-memory copy so the next call reads the file"""
        sp._data = None

    def test_add_puts_newest_first_and_persists(self):
        self.assertTrue(sp.add('a', 'A#1', 1, now=100))
        self.assertTrue(sp.add('b', 'B#2', 2, rank=('GOLD', 'II'), now=200))
        self.reload()
        entries = sp.entries()
        self.assertEqual([e['puuid'] for e in entries], ['b', 'a'])
        self.assertEqual(entries[0]['rank'], ['GOLD', 'II'])
        self.assertIsNone(entries[1]['rank'])          # not known (saved from Search)
        self.assertEqual(entries[1]['added'], 100)
        self.assertTrue(sp.isSaved('a'))
        self.assertFalse(sp.isSaved('c'))

    def test_add_existing_keeps_position(self):
        sp.add('a', 'A#1', 1)
        sp.add('b', 'B#2', 2)
        self.assertTrue(sp.add('a', 'A#1', 1))
        self.assertEqual([e['puuid'] for e in sp.entries()], ['b', 'a'])

    def test_cap(self):
        for i in range(sp.MAX_PLAYERS):
            self.assertTrue(sp.add(str(i), f'P#{i}', 1))
        self.assertTrue(sp.isFull())
        self.assertFalse(sp.add('extra', 'X#1', 1))
        self.assertEqual(sp.count(), sp.MAX_PLAYERS)

    def test_remove(self):
        sp.add('a', 'A#1', 1)
        self.assertTrue(sp.remove('a'))
        self.assertFalse(sp.remove('a'))
        self.reload()
        self.assertEqual(sp.entries(), [])

    def test_note_trimmed_and_capped(self):
        sp.add('a', 'A#1', 1)
        self.assertTrue(sp.setNote('a', '  duo  '))
        self.assertFalse(sp.setNote('a', 'duo'))
        sp.setNote('a', 'x' * 100)
        self.reload()
        self.assertEqual(sp.entries()[0]['note'], 'x' * sp.MAX_NOTE)
        self.assertFalse(sp.setNote('missing', 'duo'))

    def test_reorder(self):
        for puuid in 'abc':
            sp.add(puuid, puuid, 1)            # list is c, b, a
        self.assertTrue(sp.reorder(['a', 'unknown', 'c']))
        self.assertEqual([e['puuid'] for e in sp.entries()], ['a', 'c', 'b'])
        self.assertFalse(sp.reorder(['a', 'c', 'b']))

    def test_refresh_updates_cache_and_clears_unreachable(self):
        sp.add('a', 'Old#1', 1)
        self.assertTrue(sp.markUnreachable('a'))
        self.assertFalse(sp.markUnreachable('a'))
        self.assertTrue(sp.refresh('a', 'New#1', 7, ('EMERALD', 'IV')))
        entry = sp.entries()[0]
        self.assertEqual((entry['name'], entry['icon'], entry['rank'], entry['unreachable']),
                         ('New#1', 7, ['EMERALD', 'IV'], False))
        self.assertFalse(sp.refresh('a', 'New#1', 7, ('EMERALD', 'IV')))
        # a failed rank request (None) keeps the known rank; () is unranked
        self.assertTrue(sp.refresh('a', 'New#2', 7, None))
        self.assertEqual(sp.entries()[0]['rank'], ['EMERALD', 'IV'])
        sp.refresh('a', 'New#2', 7, ())
        self.assertEqual(sp.entries()[0]['rank'], [])
        # a player who is not saved is not added by a refresh
        self.assertFalse(sp.refresh('b', 'B#1', 1, None))
        self.assertFalse(sp.isSaved('b'))

    def test_entries_are_copies(self):
        sp.add('a', 'A#1', 1)
        sp.entries()[0]['note'] = 'changed'
        self.assertEqual(sp.entries()[0]['note'], '')

    def test_corrupt_file_is_kept_as_backup(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertEqual(sp.entries(), [])
        backups = [n for n in os.listdir(self.dir) if n.endswith('.bak')]
        self.assertEqual(len(backups), 1)
        sp.add('a', 'A#1', 1)
        with open(self.path, encoding='utf-8') as f:
            self.assertEqual(json.load(f)['players'][0]['puuid'], 'a')

    def test_bad_rows_are_dropped(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'version': 1, 'players': [{'puuid': 'a', 'name': 'A'}, 5, {}]}, f)
        self.assertEqual([e['puuid'] for e in sp.entries()], ['a'])

    def test_non_ascii_name_round_trips(self):
        sp.add('a', '提莫#CN1', 1)
        self.reload()
        self.assertEqual(sp.entries()[0]['name'], '提莫#CN1')

    def test_filter(self):
        entry = {'name': 'Faker#KR1', 'note': 'Mid duo'}
        self.assertTrue(sp.matches(entry, ''))
        self.assertTrue(sp.matches(entry, 'faker'))
        self.assertTrue(sp.matches(entry, 'DUO'))
        self.assertFalse(sp.matches(entry, 'toxic'))


if __name__ == '__main__':
    unittest.main()
