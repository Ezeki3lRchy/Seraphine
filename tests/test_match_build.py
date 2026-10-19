"""Match-detail "Build" view parsing in app.lol.match_build (no Qt).

Timelines use the match-v5 event shape (frames[].events[] with type / participantId / itemId /
timestamp, ITEM_UNDO with beforeId / afterId, SKILL_LEVEL_UP with skillSlot / levelUpType).
The live LCU timeline currently carries only kill / monster / building events, which must read
as "no build data".
"""
import unittest

from app.lol import match_build as mb

MIN = 60000


def timeline(*events, perFrame=4):
    """events spread over frames (order within the timeline must not matter)"""
    frames = [{'timestamp': i * MIN, 'events': list(events[i:i + perFrame]),
               'participantFrames': {}}
              for i in range(0, max(len(events), 1), perFrame)]
    return {'frames': frames}


def buy(pid, itemId, t):
    return {'type': 'ITEM_PURCHASED', 'participantId': pid, 'itemId': itemId, 'timestamp': t}


def sell(pid, itemId, t):
    return {'type': 'ITEM_SOLD', 'participantId': pid, 'itemId': itemId, 'timestamp': t}


def destroy(pid, itemId, t):
    return {'type': 'ITEM_DESTROYED', 'participantId': pid, 'itemId': itemId, 'timestamp': t}


def undo(pid, t, beforeId=0, afterId=0):
    return {'type': 'ITEM_UNDO', 'participantId': pid, 'beforeId': beforeId,
            'afterId': afterId, 'goldGain': 0, 'timestamp': t}


def skill(pid, slot, t, kind='NORMAL'):
    return {'type': 'SKILL_LEVEL_UP', 'participantId': pid, 'skillSlot': slot,
            'levelUpType': kind, 'timestamp': t}


# live LCU shape: only these three event types
LCU_TIMELINE = {'frames': [
    {'timestamp': 0, 'participantFrames': {}, 'events': []},
    {'timestamp': 60030, 'participantFrames': {}, 'events': [
        {'type': 'CHAMPION_KILL', 'participantId': 0, 'killerId': 5, 'victimId': 9,
         'itemId': 0, 'skillSlot': 0, 'timestamp': 83653, 'assistingParticipantIds': [4]},
        {'type': 'BUILDING_KILL', 'participantId': 0, 'killerId': 6, 'itemId': 0,
         'skillSlot': 0, 'timestamp': 800605, 'teamId': 100},
        {'type': 'ELITE_MONSTER_KILL', 'participantId': 0, 'killerId': 2, 'itemId': 0,
         'skillSlot': 0, 'timestamp': 427591, 'monsterType': 'DRAGON'}]}]}


class ItemTimelineTest(unittest.TestCase):

    def test_groups_by_minute_and_stacks_repeats(self):
        tl = timeline(buy(1, 1055, 2000), buy(1, 2003, 2500), buy(1, 2003, 2600),
                      buy(1, 3340, 3000), buy(2, 1056, 3000),
                      buy(1, 1036, 7 * MIN + 100), buy(1, 3006, 7 * MIN + 500))
        groups = mb.parseItemGroups(tl, 1)
        self.assertEqual([g['minute'] for g in groups], [0, 7])
        self.assertEqual(groups[0]['items'], [
            {'itemId': 1055, 'count': 1, 'sold': False},
            {'itemId': 2003, 'count': 2, 'sold': False},
            {'itemId': 3340, 'count': 1, 'sold': False}])
        self.assertEqual([i['itemId'] for i in groups[1]['items']], [1036, 3006])

    def test_other_players_and_destroyed_items_are_ignored(self):
        tl = timeline(buy(1, 1055, 1000), buy(3, 1056, 1000), destroy(1, 1055, 5 * MIN))
        groups = mb.parseItemGroups(tl, 1)
        self.assertEqual(groups, [{'minute': 0, 'items': [
            {'itemId': 1055, 'count': 1, 'sold': False}]}])

    def test_sold_marks_the_latest_unsold_copy(self):
        tl = timeline(buy(1, 1055, 1000), buy(1, 2003, 1500), buy(1, 2003, 1600),
                      sell(1, 1055, 20 * MIN), sell(1, 2003, 21 * MIN))
        items = mb.parseItemGroups(tl, 1)[0]['items']
        self.assertEqual(items, [
            {'itemId': 1055, 'count': 1, 'sold': True},
            {'itemId': 2003, 'count': 1, 'sold': False},
            {'itemId': 2003, 'count': 1, 'sold': True}])

    def test_undo_removes_the_purchase(self):
        tl = timeline(buy(1, 1055, 1000), buy(1, 1001, 1500), undo(1, 1600, beforeId=1001),
                      buy(1, 2003, 1700))
        items = mb.parseItemGroups(tl, 1)[0]['items']
        self.assertEqual([i['itemId'] for i in items], [1055, 2003])

    def test_undo_in_the_old_lcu_shape_uses_item_id(self):
        old = {'type': 'ITEM_UNDO', 'participantId': 1, 'itemId': 1001, 'timestamp': 1600}
        tl = timeline(buy(1, 1001, 1000), old)
        self.assertEqual(mb.parseItemGroups(tl, 1), [])

    def test_undoing_a_sale_restores_the_item(self):
        tl = timeline(buy(1, 1055, 1000), sell(1, 1055, 9 * MIN), undo(1, 9 * MIN + 10, afterId=1055))
        items = mb.parseItemGroups(tl, 1)[0]['items']
        self.assertEqual(items, [{'itemId': 1055, 'count': 1, 'sold': False}])

    def test_an_emptied_minute_leaves_no_group(self):
        tl = timeline(buy(1, 1055, 1000), buy(1, 1001, 3 * MIN), undo(1, 3 * MIN + 5, beforeId=1001))
        self.assertEqual([g['minute'] for g in mb.parseItemGroups(tl, 1)], [0])

    def test_events_are_sorted_across_frames(self):
        tl = {'frames': [{'events': [buy(1, 3006, 9 * MIN)]}, {'events': [buy(1, 1001, 1000)]}]}
        self.assertEqual([g['minute'] for g in mb.parseItemGroups(tl, 1)], [0, 9])


class SkillOrderTest(unittest.TestCase):

    def levels(self, pid, slots, evolve=()):
        events = [skill(pid, slot, (i + 1) * MIN) for i, slot in enumerate(slots)]
        events += [skill(pid, slot, 3 * MIN + 1, 'EVOLVE') for slot in evolve]
        return timeline(*events)

    def test_sequence_and_max_order(self):
        # Q E W Q Q R Q E Q E R E E W W R W W
        slots = [1, 3, 2, 1, 1, 4, 1, 3, 1, 3, 4, 3, 3, 2, 2, 4, 2, 2]
        result = mb.parseSkillOrder(self.levels(1, slots), 1)
        self.assertEqual(''.join(result['sequence']), 'QEWQQRQEQEREEWWRWW')
        self.assertEqual(result['order'], ['Q', 'E', 'W'])

    def test_evolve_does_not_take_a_level(self):
        result = mb.parseSkillOrder(self.levels(1, [1, 2, 3], evolve=[1]), 1)
        self.assertEqual(result['sequence'], ['Q', 'W', 'E'])

    def test_short_games_order_by_points_then_first_point(self):
        # W first, then Q twice: Q has more points; E and W tie on 1 point, W came first
        result = mb.parseSkillOrder(self.levels(1, [2, 1, 3, 1]), 1)
        self.assertEqual(result['order'], ['Q', 'W', 'E'])

    def test_caps_at_eighteen_levels(self):
        result = mb.parseSkillOrder(self.levels(1, [1] * 25), 1)
        self.assertEqual(len(result['sequence']), 18)

    def test_no_events(self):
        self.assertEqual(mb.parseSkillOrder(timeline(), 1), {'sequence': [], 'order': []})


def styles():
    def rune(rid):
        return {'runeId': rid, 'icon': f'icons/{rid}.png', 'name': f'Rune {rid}', 'desc': ''}

    shards = [[rune(5008), rune(5005), rune(5007)], [rune(5008), rune(5010), rune(5001)],
              [rune(5011), rune(5013), rune(5001)]]
    return {
        8200: {'name': 'Sorcery', 'icon': 'icons/8200.png', 'slots': [
            [rune(8214), rune(8229), rune(8230), rune(8992)],
            [rune(8224), rune(8226), rune(8275)],
            [rune(8210), rune(8234), rune(8233)],
            [rune(8237), rune(8232), rune(8236)]] + shards},
        8300: {'name': 'Inspiration', 'icon': 'icons/8300.png', 'slots': [
            [rune(8351), rune(8360), rune(8369)],
            [rune(8306), rune(8304), rune(8321)],
            [rune(8313), rune(8352), rune(8345)],
            [rune(8347), rune(8410), rune(8316)]] + shards},
    }


# real participant stats from a live game (no stat shards in the LCU detail)
STATS = {'perk0': 8229, 'perk1': 8226, 'perk2': 8210, 'perk3': 8237, 'perk4': 8345,
         'perk5': 8347, 'perkPrimaryStyle': 8200, 'perkSubStyle': 8300}


def selected(rows):
    return [[r['id'] for r in row if r['selected']] for row in rows]


class RunePageTest(unittest.TestCase):

    def test_primary_and_secondary_trees(self):
        page = mb.parseRunePage(STATS, styles())
        self.assertEqual(page['primary']['name'], 'Sorcery')
        self.assertEqual(len(page['primary']['rows']), 4)
        self.assertEqual(len(page['primary']['rows'][0]), 4)
        self.assertEqual(selected(page['primary']['rows']), [[8229], [8226], [8210], [8237]])
        self.assertEqual(page['secondary']['name'], 'Inspiration')
        self.assertEqual(selected(page['secondary']['rows']), [[], [8345], [8347]])
        self.assertEqual(page['primary']['rows'][0][1]['icon'], 'icons/8229.png')

    def test_no_shards_without_stat_perks(self):
        self.assertEqual(mb.parseRunePage(STATS, styles())['shards'], [])

    def test_shards_are_matched_per_row(self):
        stats = dict(STATS, statPerk0=5008, statPerk1=5008, statPerk2=5001)
        shards = mb.parseRunePage(stats, styles())['shards']
        self.assertEqual(selected(shards), [[5008], [5008], [5001]])

    def test_unknown_style_or_missing_data(self):
        self.assertIsNone(mb.parseRunePage(dict(STATS, perkPrimaryStyle=9999), styles()))
        self.assertIsNone(mb.parseRunePage(None, styles()))
        self.assertIsNone(mb.parseRunePage(STATS, None))
        page = mb.parseRunePage(dict(STATS, perkSubStyle=0), styles())
        self.assertIsNone(page['secondary'])


class GameBuildsTest(unittest.TestCase):

    def detail(self):
        return {'participants': [{'participantId': 1, 'stats': STATS},
                                 {'participantId': 2, 'stats': dict(STATS, perk0=8214)}]}

    def test_lcu_timeline_has_no_build_events(self):
        self.assertFalse(mb.hasBuildEvents(LCU_TIMELINE))
        self.assertFalse(mb.hasBuildEvents(None))
        self.assertFalse(mb.hasBuildEvents({'frames': []}))
        builds = mb.parseGameBuilds(self.detail(), LCU_TIMELINE, styles())
        self.assertEqual(set(builds), {1, 2})
        self.assertIsNone(builds[1]['itemGroups'])
        self.assertIsNone(builds[1]['skills'])
        self.assertEqual(selected(builds[2]['runes']['primary']['rows'])[0], [8214])

    def test_full_timeline(self):
        tl = timeline(buy(1, 1055, 1000), buy(2, 1056, 1000), skill(1, 1, MIN), skill(2, 3, MIN))
        self.assertTrue(mb.hasBuildEvents(tl))
        builds = mb.parseGameBuilds(self.detail(), tl, styles())
        self.assertEqual(builds[1]['itemGroups'][0]['items'][0]['itemId'], 1055)
        self.assertEqual(builds[2]['skills']['sequence'], ['E'])
        self.assertEqual(mb.buildItemIds(builds), {1055, 1056})

    def test_timeline_without_detail(self):
        tl = timeline(buy(1, 1055, 1000))
        builds = mb.parseGameBuilds(None, tl, styles())
        self.assertEqual(set(builds), {1})
        self.assertIsNone(builds[1]['runes'])

    def test_nothing(self):
        self.assertEqual(mb.parseGameBuilds(None, None, None), {})


if __name__ == '__main__':
    unittest.main()
