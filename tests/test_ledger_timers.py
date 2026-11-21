"""Cooldown ledger timer model (app/lol/ledger_model.LedgerTimers) on a fake clock, the D / F
spell columns and ledgerVisible."""
import unittest

from app.lol.ledger_model import (LANES, LedgerTimers, cellText, ledgerVisible, spellForCell)

# synthetic item table for the timer model: items with an active cooldown get a cell, 3158
# (summoner spell haste only) and ids absent from the table get none
DB = {'items': {'3157': {'active': 120, 'haste': 0, 'ultHaste': 0, 'spellHaste': 0},
                '3190': {'active': 90, 'haste': 10, 'ultHaste': 0, 'spellHaste': 0},
                '3222': {'active': 120, 'haste': 15, 'ultHaste': 0, 'spellHaste': 0},
                '6698': {'active': 15, 'haste': 10, 'ultHaste': 0, 'spellHaste': 0},
                '3158': {'active': None, 'haste': 0, 'ultHaste': 0, 'spellHaste': 10},
                # an active whose cooldown is unknown: an inert cell
                '3142': {'active': None, 'hasActive': True, 'haste': 0, 'ultHaste': 0, 'spellHaste': 0}}}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def entry(position, items=(), spells=('SummonerFlash', 'SummonerDot'), key=None):
    return {'key': key or position, 'championName': 'Ahri', 'position': position, 'level': 6,
            'items': list(items), 'spells': list(spells)}


def roster(**overrides):
    return [overrides.get(p) or entry(p) for p in LANES]


class TimerTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.t = LedgerTimers(clock=self.clock, db=DB)

    def test_no_start_before_first_sync(self):
        self.t.toggle('MIDDLE', 'Q', 8)
        self.assertEqual(self.t.state('MIDDLE', 'Q'), ('idle', 0.0, None))
        self.assertIsNone(self.t.now())

    def test_start_counts_down_and_returns(self):
        self.t.sync(600, roster())
        self.t.toggle('MIDDLE', 'Q', 8)
        self.assertEqual(self.t.state('MIDDLE', 'Q'), ('running', 8.0, 608.0))
        self.clock.t += 3
        self.assertEqual(self.t.state('MIDDLE', 'Q'), ('running', 5.0, 608.0))
        self.assertTrue(self.t.active())
        self.t.sync(605, roster())
        self.clock.t += 3
        self.assertEqual(self.t.state('MIDDLE', 'Q')[0], 'idle')   # expiry -> idle, no "ready"
        self.assertFalse(self.t.active())

    def test_second_click_cancels(self):
        self.t.sync(600, roster())
        self.t.toggle('TOP', 'D', 300)
        self.t.toggle('TOP', 'D', 300)
        self.assertEqual(self.t.state('TOP', 'D'), ('idle', 0.0, None))
        self.assertFalse(self.t.active())

    def test_inestimable_cell_does_nothing(self):
        self.t.sync(600, roster())
        for duration in (None, 0, -5):
            self.t.toggle('TOP', 'E', duration)
            self.assertEqual(self.t.state('TOP', 'E')[0], 'idle')

    def test_countdown_vs_return_time_text(self):
        self.t.sync(720, roster())
        self.t.toggle('MIDDLE', 'R', 120)
        self.t.toggle('MIDDLE', 'D', 130)
        self.t.toggle('MIDDLE', 'item:3157', 120)
        self.assertEqual(cellText('R', self.t.state('MIDDLE', 'R')), '120')     # seconds, not 2:00
        self.assertEqual(cellText('D', self.t.state('MIDDLE', 'D')), '14:10')   # return game time
        self.assertEqual(cellText('item:3157', self.t.state('MIDDLE', 'item:3157')), '14:00')
        self.clock.t += 0.4
        self.assertEqual(cellText('R', self.t.state('MIDDLE', 'R')), '120')     # 119.6 rounds up
        self.assertEqual(cellText('Q', self.t.state('MIDDLE', 'Q')), '')        # idle

    def test_pause_caps_extrapolation(self):
        self.t.sync(600, roster())
        self.t.toggle('BOTTOM', 'F', 180)
        self.clock.t += 60          # game paused: no new game time for a minute
        self.assertEqual(self.t.now(), 606.0)
        self.assertEqual(self.t.state('BOTTOM', 'F'), ('running', 174.0, 780.0))
        self.t.sync(606, roster())  # resumes where it stopped
        self.clock.t += 1
        self.assertEqual(self.t.state('BOTTOM', 'F'), ('running', 173.0, 780.0))

    def test_new_game_resets(self):
        self.t.sync(1500, roster())
        self.t.toggle('TOP', 'R', 100)
        self.t.sync(1475, roster())     # a small backward step is not a new game
        self.assertEqual(self.t.state('TOP', 'R')[0], 'running')
        self.t.sync(5, roster())        # dropped by more than 30 s
        self.assertEqual(self.t.state('TOP', 'R'), ('idle', 0.0, None))

    def test_spell_upgrade_in_place_keeps_timer(self):
        top = entry('TOP', spells=('SummonerTeleport', 'SummonerFlash'))
        self.t.sync(500, roster(TOP=top))
        self.assertEqual(spellForCell(top, 'D'), 'SummonerFlash')       # Flash always left
        self.assertEqual(spellForCell(top, 'F'), 'SummonerTeleport')
        self.t.toggle('TOP', 'F', 300)
        upgraded = entry('TOP', spells=('S12_SummonerTeleportUpgrade', 'SummonerFlash'))
        self.clock.t += 2
        self.t.sync(602, roster(TOP=upgraded))
        self.assertEqual(spellForCell(upgraded, 'F'), 'S12_SummonerTeleportUpgrade')
        self.assertEqual(self.t.state('TOP', 'F'), ('running', 198.0, 800.0))

    def test_row_dropped_with_its_timers(self):
        self.t.sync(500, roster())
        self.t.toggle('UTILITY', 'Q', 10)
        others = [entry(p) for p in LANES if p != 'UTILITY']
        self.t.sync(501, others)
        self.assertEqual(self.t.state('UTILITY', 'Q')[0], 'idle')
        self.t.toggle('JUNGLE', 'Q', 10)
        self.t.sync(502, [])            # an empty roster carries no information: kept
        self.assertEqual(self.t.state('JUNGLE', 'Q')[0], 'running')

    def test_clear(self):
        self.t.sync(500, roster())
        self.t.toggle('TOP', 'Q', 10)
        self.t.clear()
        self.assertFalse(self.t.active())
        self.assertIsNone(self.t.now())


class CellsTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.t = LedgerTimers(clock=self.clock, db=DB)

    def cellsOf(self, e):
        return self.t.cells(e)

    def test_base_cells_and_tp_on_top_without_teleport(self):
        top = entry('TOP')
        mid = entry('MIDDLE')
        self.t.sync(100, roster(TOP=top))
        self.assertEqual(self.cellsOf(top), ['Q', 'W', 'E', 'R', 'D', 'F', 'TP'])
        self.assertEqual(self.cellsOf(mid), ['Q', 'W', 'E', 'R', 'D', 'F'])
        tpTop = entry('TOP', spells=('SummonerFlash', 'S12_SummonerTeleportUpgrade'))
        self.assertEqual(self.cellsOf(tpTop), ['Q', 'W', 'E', 'R', 'D', 'F'])

    def test_item_appears_and_disappears(self):
        e = entry('MIDDLE', items=[1056, 3020])          # nothing with an active
        self.t.sync(100, roster(MIDDLE=e))
        self.assertEqual(self.cellsOf(e)[6:], [])
        e = entry('MIDDLE', items=[1056, 3157, 3020])
        self.t.sync(200, roster(MIDDLE=e))
        self.assertEqual(self.cellsOf(e)[6:], ['item:3157'])
        e = entry('MIDDLE', items=[1056, 3020])          # sold, no timer running
        self.t.sync(300, roster(MIDDLE=e))
        self.assertEqual(self.cellsOf(e)[6:], [])

    def test_item_kept_while_running_then_goes(self):
        e = entry('MIDDLE', items=[3157])
        self.t.sync(100, roster(MIDDLE=e))
        self.t.toggle('MIDDLE', 'item:3157', 120)
        e = entry('MIDDLE', items=[])
        self.clock.t += 2
        self.t.sync(102, roster(MIDDLE=e))
        self.assertEqual(self.cellsOf(e)[6:], ['item:3157'])
        self.clock.t += 5
        self.t.sync(220, roster(MIDDLE=e))               # timer ended
        self.assertEqual(self.cellsOf(e)[6:], [])

    def test_appended_never_reordered_at_most_three(self):
        e = entry('UTILITY', items=[3190])
        self.t.sync(100, roster(UTILITY=e))
        # new items arrive in slots before the old one: the old cell stays first
        e = entry('UTILITY', items=[3222, 3157, 3190])
        self.t.sync(200, roster(UTILITY=e))
        self.assertEqual(self.cellsOf(e)[6:], ['item:3190', 'item:3222', 'item:3157'])
        e = entry('UTILITY', items=[6698, 3222, 3157, 3190])
        self.t.sync(300, roster(UTILITY=e))
        self.assertEqual(self.cellsOf(e)[6:], ['item:3190', 'item:3222', 'item:3157'])
        self.assertEqual(len(self.cellsOf(e)), 9)

    def test_removed_item_leaves_a_hole_until_filled(self):
        e = entry('UTILITY', items=[3190, 3222, 3157])
        self.t.sync(100, roster(UTILITY=e))
        self.assertEqual(self.cellsOf(e)[6:], ['item:3190', 'item:3222', 'item:3157'])
        e = entry('UTILITY', items=[3222, 3157])              # first one sold: the others stay put
        self.t.sync(200, roster(UTILITY=e))
        self.assertEqual(self.cellsOf(e)[6:], [None, 'item:3222', 'item:3157'])
        e = entry('UTILITY', items=[3222, 3157, 6698])         # the next new item fills the hole
        self.t.sync(300, roster(UTILITY=e))
        self.assertEqual(self.cellsOf(e)[6:], ['item:6698', 'item:3222', 'item:3157'])
        e = entry('UTILITY', items=[6698, 3222])               # last one sold: nothing after it moves
        self.t.sync(400, roster(UTILITY=e))
        self.assertEqual(self.cellsOf(e)[6:], ['item:6698', 'item:3222'])

    def test_hole_cell_is_inert(self):
        e = entry('UTILITY', items=[3190, 3222])
        self.t.sync(100, roster(UTILITY=e))
        e = entry('UTILITY', items=[3222])
        self.t.sync(200, roster(UTILITY=e))
        hole = self.cellsOf(e)[6]
        self.assertIsNone(hole)
        self.t.toggle('UTILITY', hole, None)
        self.assertEqual(self.t.state('UTILITY', hole), ('idle', 0.0, None))
        self.assertEqual(cellText(hole, self.t.state('UTILITY', hole)), '')

    def test_active_with_unknown_cooldown_gets_an_inert_cell(self):
        e = entry('BOTTOM', items=[3142, 3158])
        self.t.sync(100, roster(BOTTOM=e))
        self.assertEqual(self.cellsOf(e)[6:], ['item:3142'])
        self.t.toggle('BOTTOM', 'item:3142', None)            # what the panel passes: no duration
        self.assertEqual(self.t.state('BOTTOM', 'item:3142')[0], 'idle')

    def test_no_database_no_item_cells(self):
        t = LedgerTimers(clock=self.clock)
        e = entry('MIDDLE', items=[3157])
        t.sync(100, roster(MIDDLE=e))
        self.assertEqual(t.cells(e), ['Q', 'W', 'E', 'R', 'D', 'F'])


def summary(**overrides):
    s = {'gameMode': 'CLASSIC', 'mapNumber': 11, 'enemyRoster': roster()}
    s.update(overrides)
    return s


class LedgerVisibleTest(unittest.TestCase):
    def test_summoners_rift_with_five_positions(self):
        self.assertTrue(ledgerVisible(summary()))

    def test_hidden(self):
        positions = [entry(p) for p in LANES]
        cases = {
            'URF': summary(gameMode='URF'),
            'ONEFORALL': summary(gameMode='ONEFORALL'),
            'ARAM': summary(gameMode='ARAM', mapNumber=12),
            'map 12': summary(mapNumber=12),
            '4 positions': summary(enemyRoster=positions[:4]),
            '4 positions + unknown': summary(enemyRoster=positions[:4] + [entry('', key='#4')]),
            'duplicate positions': summary(enemyRoster=positions[:4] + [entry('TOP', key='#4')]),
            'no roster': summary(enemyRoster=[]),
            'None': None,
        }
        for name, s in cases.items():
            with self.subTest(name):
                self.assertFalse(ledgerVisible(s))


if __name__ == '__main__':
    unittest.main()
