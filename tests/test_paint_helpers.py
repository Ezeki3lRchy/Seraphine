"""Shared painting helpers: one definition each, and the helpers that only share a name
keep their own behaviour.

drawText / drawRuns / drawRoundedPixmap / drawChampion / drawCard live in
app/components/paint.py and kdaColor in design_tokens.py; the modules that used to carry
a copy import them, so their attributes are the same function objects.
"""
import ast
import glob
import inspect
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def topLevelDefinitions(name):
    """files under app/ that define `name` at module level"""
    out = []
    for path in glob.glob(os.path.join(ROOT, "app", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        if any(isinstance(node, ast.FunctionDef) and node.name == name for node in tree.body):
            out.append(os.path.relpath(path, ROOT).replace(os.sep, "/"))
    return sorted(out)


class SingleDefinitionTest(unittest.TestCase):

    def test_paint_helpers_are_the_same_objects_everywhere(self):
        from app.components import (paint, scoreboard, career_widgets, opgg_build_widgets,
                                    match_detail_widgets, match_graph_widgets,
                                    play_style_widget, champion_stats_widget,
                                    item_gold_panel, gold_overlay, performance_widget,
                                    tier_graph_widget, match_build_widgets)
        users = {
            'drawText': (scoreboard, career_widgets, opgg_build_widgets, match_detail_widgets,
                         match_graph_widgets, play_style_widget, champion_stats_widget,
                         item_gold_panel, gold_overlay, performance_widget, tier_graph_widget,
                         match_build_widgets),
            'drawRuns': (scoreboard, career_widgets, opgg_build_widgets, match_detail_widgets,
                         play_style_widget, champion_stats_widget, performance_widget),
            'drawRoundedPixmap': (scoreboard, career_widgets, match_detail_widgets,
                                  match_graph_widgets, match_build_widgets),
            'drawChampion': (scoreboard, career_widgets, match_detail_widgets,
                             match_graph_widgets, play_style_widget, champion_stats_widget,
                             item_gold_panel),
            'drawCard': (career_widgets, tier_graph_widget),
        }
        for name, modules in users.items():
            shared = getattr(paint, name)
            self.assertEqual(shared.__module__, 'app.components.paint', name)
            for module in modules:
                self.assertIs(getattr(module, name), shared, f"{module.__name__}.{name}")

    def test_kda_color_is_the_same_object_everywhere(self):
        from app.components import (design_tokens, career_widgets, match_detail_widgets,
                                    champion_stats_widget)
        from app.view import game_info_interface

        self.assertEqual(design_tokens.kdaColor.__module__, 'app.components.design_tokens')
        for module in (career_widgets, match_detail_widgets, champion_stats_widget,
                       game_info_interface):
            self.assertIs(module.kdaColor, design_tokens.kdaColor, module.__name__)

    def test_one_source_definition(self):
        for name, files in (
                ('drawText', ['app/components/paint.py']),
                ('drawRuns', ['app/components/paint.py']),
                ('drawChampion', ['app/components/paint.py']),
                ('kdaColor', ['app/components/design_tokens.py']),
                # these two share only the name: they draw differently, kept apart
                ('drawRoundedPixmap', ['app/components/opgg_build_widgets.py',
                                       'app/components/paint.py']),
                ('drawCard', ['app/components/match_build_widgets.py',
                              'app/components/paint.py'])):
            self.assertEqual(topLevelDefinitions(name), files, name)


class SharedHelperBehaviourTest(unittest.TestCase):

    def test_kda_color_thresholds(self):
        from app.components.design_tokens import kdaColor

        green, blue, orange = QColor(0, 163, 80), QColor(0, 147, 255), QColor(240, 111, 0)
        for kda, expected in ((0, None), (2.99, None), (3, green), (3.99, green), (4, blue),
                              (4.99, blue), (5, orange), (12.5, orange)):
            self.assertEqual(kdaColor(kda), expected, kda)

    def test_champion_ring_default(self):
        # career_widgets' old copy defaulted to 2, but all its call sites pass the width
        from app.components.paint import drawChampion

        self.assertEqual(inspect.signature(drawChampion).parameters['ring'].default, 1.5)
        self.assertEqual(inspect.signature(drawChampion).parameters['crop'].default, 0)

    def test_card_default_radius_matches_the_tier_graph(self):
        from app.components import paint, tier_graph_widget

        self.assertEqual(inspect.signature(paint.drawCard).parameters['radius'].default,
                         tier_graph_widget.RADIUS)


class SameNameHelpersTest(unittest.TestCase):
    """helpers with one name in two modules that intentionally differ"""

    def test_compact_number(self):
        from app.components import match_graph_widgets as mg, play_style_widget as psw

        # graph axis: 'K' from 1000, trailing zeros dropped, truncated below
        for value, text in ((0, "0"), (999, "999"), (999.9, "999"), (1000, "1K"),
                            (1234, "1.2K"), (2000, "2K"), (10500, "10.5K"),
                            (16052, "16.1K")):
            self.assertEqual(mg.compactNumber(value), text, value)
        # play style tiles: thousands separators below 10000, 'k' with one decimal from it
        for value, text in ((0, "0"), (999, "999"), (1234, "1,234"), (9999, "9,999"),
                            (9999.6, "10,000"), (10000, "10.0k"), (12345, "12.3k")):
            self.assertEqual(psw.compactNumber(value), text, value)

    def test_pct(self):
        from app.components import opgg_build_widgets as ob, play_style_widget as psw

        self.assertEqual(ob.pct(0.5123), "51.2%")
        self.assertEqual(ob.pct(0.5, 0), "50%")
        self.assertEqual(ob.pct(0), "0.0%")
        self.assertEqual(ob.pct(None), "-")
        self.assertEqual(psw.pct(0.5), "50%")
        self.assertEqual(psw.pct(0.126), "13%")
        self.assertEqual(psw.pct(0), "0%")
        self.assertEqual(psw.pct(None), "--")

    def test_result_type(self):
        from app.components import career_widgets as cw, scoreboard as sb

        self.assertEqual(cw.resultType({'win': True}), 'win')
        self.assertEqual(cw.resultType({'win': False}), 'lose')
        self.assertEqual(cw.resultType({}), 'lose')
        self.assertEqual(cw.resultType({'win': True, 'remake': True}), 'remake')
        self.assertEqual(sb.resultType(True, False), 'win')
        self.assertEqual(sb.resultType(False, False), 'lose')
        self.assertEqual(sb.resultType(True, True), 'remake')

    def test_win_rate_text(self):
        from app.components import opgg_build_widgets as ob
        from app.view import game_info_interface as gi

        self.assertEqual(len(inspect.signature(ob.winRateText).parameters), 0)
        self.assertEqual(gi.winRateText(0, 0), "--")
        text = gi.winRateText(3, 1)
        self.assertIn("75%", text)
        self.assertTrue(text.endswith(" (3W 1L)"), text)

    def test_chevrons_keep_their_own_geometry(self):
        from app.components import match_build_widgets as mb, opgg_build_widgets as ob

        self.assertEqual(list(inspect.signature(mb.drawChevron).parameters),
                         ['p', 'center', 'color', 'size'])
        self.assertEqual(inspect.signature(mb.drawChevron).parameters['size'].default, 4.5)
        self.assertEqual(list(inspect.signature(ob.drawChevron).parameters),
                         ['p', 'cx', 'cy', 'color', 'size'])
        self.assertEqual(inspect.signature(ob.drawChevron).parameters['size'].default, 4.0)


if __name__ == "__main__":
    unittest.main()
