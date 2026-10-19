"""Search page redesign: detail-table column layout + the game-list short texts."""
import time
import unittest

from app.components import match_detail_widgets as md
from app.components import scoreboard as sb

ORDER = ('icons', 'name', 'score', 'kda', 'damage', 'wards', 'cs', 'items')


class DetailColumnsTest(unittest.TestCase):

    def walk(self, cols):
        """columns are contiguous left to right; returns the right edge of the items column"""
        x = md.PAD_L
        for key in ORDER:
            cx, cw = cols[key]
            self.assertAlmostEqual(cx, x, msg=key)
            x = cx + cw + (md.ICONS_GAP if key == 'icons' else 0)
        return x

    def test_wide_tables_cap_the_name_and_split_the_rest_evenly(self):
        for width in (816, 1086, 1400):
            cols = md.columns(width)
            self.assertEqual(cols['name'][1], md.NAME_W)
            self.assertEqual(cols['items'][1], md.ITEMS_W)
            self.assertAlmostEqual(self.walk(cols) + md.PAD_R, width)
            extras = {round(cols[key][1] - w, 6) for key, w in md.STAT_COLUMNS}
            self.assertEqual(len(extras), 1, width)
            self.assertGreaterEqual(extras.pop(), 0)

    def test_narrow_tables_shrink_only_the_name(self):
        cols = md.columns(md.TABLE_MIN_W + 20)
        self.assertEqual(cols['name'][1], md.NAME_MIN_W + 20)
        for key, w in md.STAT_COLUMNS:
            self.assertEqual(cols[key][1], w)
        self.assertAlmostEqual(self.walk(cols) + md.PAD_R, md.TABLE_MIN_W + 20)

    def test_never_below_the_minimums(self):
        cols = md.columns(300)
        self.assertEqual(cols['name'][1], md.NAME_MIN_W)
        for key, w in md.STAT_COLUMNS:
            self.assertEqual(cols[key][1], w)

    def test_fits_the_users_detail_panel_without_horizontal_scroll(self):
        # detail panel ~830px -> tables ~816px wide
        self.assertLessEqual(md.TABLE_MIN_W, 800)


class GameListTextTest(unittest.TestCase):

    def test_queue_short_names(self):
        self.assertEqual(sb.queueShortName(420, "Ranked Solo/Duo"), "Solo/Duo")
        self.assertEqual(sb.queueShortName(440, "Ranked Flex"), "Flex")
        self.assertEqual(sb.queueShortName(450, "ARAM"), "ARAM")
        # 中文客户端 / 未收录的队列: 去掉 "排位赛 " / "Ranked " 前缀的原名
        self.assertEqual(sb.queueShortName(420, "排位赛 单排/双排"), "单排/双排")
        self.assertEqual(sb.queueShortName(99999, "Ranked Something"), "Something")
        self.assertEqual(sb.queueShortName(None, None), "")

    def test_compact_ago(self):
        now = time.time()
        stamp = lambda secs: (now - secs) * 1000
        self.assertEqual(sb.compactAgo(stamp(20), "10/19", now), "now")
        self.assertEqual(sb.compactAgo(stamp(5 * 60), "10/19", now), "5m")
        self.assertEqual(sb.compactAgo(stamp(3 * 3600 + 5), "10/19", now), "3h")
        self.assertEqual(sb.compactAgo(stamp(2 * 86400), "10/17", now), "2d")
        self.assertEqual(sb.compactAgo(stamp(9 * 86400), "10/10", now), "10/10")
        self.assertEqual(sb.compactAgo(stamp(9 * 86400), "", now), "9d")

    def test_duration_and_result(self):
        self.assertEqual(sb.durationText(32 * 60 + 9), "32m 09s")
        self.assertEqual(sb.durationText(0), "")
        self.assertEqual(sb.resultType(True, False), 'win')
        self.assertEqual(sb.resultType(False, False), 'lose')
        self.assertEqual(sb.resultType(True, True), 'remake')


if __name__ == '__main__':
    unittest.main()
