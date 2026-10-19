"""Match-detail "Graphs" view (app.components.match_graph_widgets): axis helpers, the
selection / color-slot rules and the end-label layout."""
import unittest

from PySide6.QtCore import QRectF

from app.components import match_graph_widgets as mg


class HelpersTest(unittest.TestCase):

    def test_compact_number(self):
        self.assertEqual(mg.compactNumber(222), "222")
        self.assertEqual(mg.compactNumber(1000), "1K")
        self.assertEqual(mg.compactNumber(9030), "9K")
        self.assertEqual(mg.compactNumber(16052), "16.1K")

    def test_nice_ticks(self):
        self.assertEqual(mg.niceTicks(222), ([0, 100, 200, 300], 300))
        self.assertEqual(mg.niceTicks(16052), ([0, 5000, 10000, 15000, 20000], 20000))
        self.assertEqual(mg.niceTicks(0), ([0, 1], 1))
        ticks, top = mg.niceTicks(3)
        self.assertEqual(top, 3)
        self.assertTrue(all(float(t).is_integer() for t in ticks))

    def test_clock_text(self):
        self.assertEqual(mg.clockText(0), "0:00")
        self.assertEqual(mg.clockText(21.3021), "21:18")
        self.assertEqual(mg.clockText(1.9999), "2:00")


class GraphCardTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def card(self, current=9):
        card = mg.GraphCard()
        team = [{'participantId': i, 'championIcon': '', 'summonerName': f'p{i}'}
                for i in range(1, 6)]
        enemy = [{'participantId': i, 'championIcon': '', 'summonerName': f'p{i}'}
                 for i in range(6, 11)]
        card.setPlayers(team, enemy, ("Blue", "Red"), current)
        return card

    def test_current_player_starts_selected_in_the_first_slot(self):
        self.assertEqual(self.card().slots, {9: 0})
        self.assertEqual(self.card(None).slots, {})

    def test_colors_follow_the_champion(self):
        card = self.card()
        card.toggle(1)
        card.toggle(6)
        self.assertEqual(card.slots, {9: 0, 1: 1, 6: 2})
        card.toggle(1)              # free slot 1; nobody else repaints
        self.assertEqual(card.slots, {9: 0, 6: 2})
        card.toggle(3)              # takes the free slot
        self.assertEqual(card.slots, {9: 0, 6: 2, 3: 1})

    def test_a_fourth_pick_replaces_the_oldest_other_pick(self):
        card = self.card()
        card.toggle(1)
        card.toggle(6)
        card.toggle(8)
        self.assertEqual(card.slots, {9: 0, 6: 2, 8: 1})

    def test_the_current_player_can_be_dropped(self):
        card = self.card()
        card.toggle(9)
        self.assertEqual(card.slots, {})
        for pid in (1, 2, 3, 4):
            card.toggle(pid)
        self.assertEqual(card.slots, {2: 1, 3: 2, 4: 0})

    def test_unknown_metric_falls_back_to_gold(self):
        card = self.card()
        card.metricKey = 'damage'
        card.setSeries({'minutes': [0, 1], 'metrics': ['gold', 'xp', 'cs'], 'series': {}})
        self.assertEqual(card.metricKey, 'gold')

    def test_end_labels_are_spread_and_stay_inside_the_plot(self):
        card = self.card()
        plot = QRectF(0, 100, 500, 200)
        centers = card.endLabelCenters(plot, [(1, 150), (2, 152), (3, 151)])
        ys = sorted(centers.values())
        self.assertTrue(all(b - a >= card.LABEL_SEP for a, b in zip(ys, ys[1:])))
        # order by the line ends is kept
        self.assertLess(centers[1], centers[3])
        self.assertLess(centers[3], centers[2])

        centers = card.endLabelCenters(plot, [(1, 299), (2, 300)])
        self.assertLessEqual(max(centers.values()), plot.bottom() - card.END_ICON / 2)
        self.assertGreaterEqual(min(centers.values()), plot.top() + card.END_ICON / 2)


if __name__ == '__main__':
    unittest.main()
