"""Career-page activity stats in app.lol.activity.

activityByHour / activityByWeekday bucket games by the local time they started.
Timestamps are built from naive local datetimes, so the tests hold in any timezone.
"""
import unittest
from datetime import datetime

from app.lol import activity


def ms(*args):
    return int(datetime(*args).timestamp() * 1000)


def game(timeStamp, win=True, remake=False):
    return {"timeStamp": timeStamp, "win": win, "remake": remake}


# 2026-10-12 is a Monday, 2026-10-17 a Saturday, 2026-10-18 a Sunday.
MON, SAT, SUN = (2026, 10, 12), (2026, 10, 17), (2026, 10, 18)


class ActivityByHourTest(unittest.TestCase):

    def test_empty(self):
        for games in ([], None):
            hours = activity.activityByHour(games)
            self.assertEqual(len(hours), 24)
            self.assertTrue(all(h == {"games": 0, "wins": 0, "losses": 0} for h in hours))

    def test_buckets_by_local_hour(self):
        games = [game(ms(*SAT, 18, 5)), game(ms(*SAT, 18, 59), win=False),
                 game(ms(*MON, 0, 0)), game(ms(*SUN, 23, 30))]
        hours = activity.activityByHour(games)
        self.assertEqual(hours[18], {"games": 2, "wins": 1, "losses": 1})
        self.assertEqual(hours[0], {"games": 1, "wins": 1, "losses": 0})
        self.assertEqual(hours[23], {"games": 1, "wins": 1, "losses": 0})
        self.assertEqual(sum(h["games"] for h in hours), 4)

    def test_remake_counts_as_game_but_not_result(self):
        hours = activity.activityByHour([game(ms(*SAT, 9, 10), remake=True),
                                         game(ms(*SAT, 9, 40), win=False)])
        self.assertEqual(hours[9], {"games": 2, "wins": 0, "losses": 1})

    def test_skips_none_and_missing_timestamps(self):
        games = [None, {}, game(0), game(None), {"win": True},
                 game(ms(*SAT, 12, 0))]
        hours = activity.activityByHour(games)
        self.assertEqual(sum(h["games"] for h in hours), 1)
        self.assertEqual(hours[12]["games"], 1)


class ActivityByWeekdayTest(unittest.TestCase):

    def test_empty(self):
        days = activity.activityByWeekday([])
        self.assertEqual(len(days), 7)
        self.assertTrue(all(d["games"] == 0 for d in days))

    def test_monday_first_sunday_last(self):
        games = [game(ms(*MON, 20, 0)), game(ms(*SAT, 1, 0), win=False),
                 game(ms(*SAT, 22, 0)), game(ms(*SUN, 15, 0), remake=True)]
        days = activity.activityByWeekday(games)
        self.assertEqual(days[0], {"games": 1, "wins": 1, "losses": 0})
        self.assertEqual(days[5], {"games": 2, "wins": 1, "losses": 1})
        self.assertEqual(days[6], {"games": 1, "wins": 0, "losses": 0})
        self.assertEqual(sum(d["games"] for d in days[1:5]), 0)


class WinRateTest(unittest.TestCase):

    def test_rate(self):
        self.assertAlmostEqual(activity.winRate({"games": 3, "wins": 2, "losses": 1}),
                               2 / 3)

    def test_no_counted_games(self):
        self.assertIsNone(activity.winRate({"games": 0, "wins": 0, "losses": 0}))
        # 全是重开: 有场次, 没有胜率
        self.assertIsNone(activity.winRate({"games": 2, "wins": 0, "losses": 0}))


if __name__ == "__main__":
    unittest.main()
