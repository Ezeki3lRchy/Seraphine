"""
对局详情 "出装" 视图的纯数据解析 (不依赖 Qt / connector, 便于单测):

- 时间线 (match-v5 事件格式) -> 每名玩家按分钟分组的购买记录 (同组同物品叠加计数, 卖出的标记 sold,
  撤销的购买直接去掉) 与技能加点顺序 (逐级序列 + 主升顺序);
- 对局详情 participant.stats 的 perk0..5 / perkPrimaryStyle / perkSubStyle -> 符文页 (主系 4 行 +
  副系 3 行, 选中与否; 有 statPerk0..2 时再加属性碎片 3 行).

注意: 目前 LCU 的 /lol-match-history/v1/game-timelines/{gameId} 实测只有 CHAMPION_KILL /
ELITE_MONSTER_KILL / BUILDING_KILL 事件, 没有 ITEM_* / SKILL_LEVEL_UP; 这时改用 Riot 对局服务器
(SGP) 的完整时间线 (connector.getGameTimelineViaSgpQuietly), 其 participantId 用
remapTimelineParticipants 按 puuid 对到对局详情的 participantId. 都拿不到时界面显示 "无出装数据".
事件字段按 match-v5 (ITEM_UNDO 的 beforeId / afterId) 兼容 LCU 旧格式 (只有 itemId).
"""

SKILL_KEYS = {1: 'Q', 2: 'W', 3: 'E', 4: 'R'}
MAX_LEVEL = 18

BUILD_EVENT_TYPES = ('ITEM_PURCHASED', 'ITEM_SOLD', 'ITEM_UNDO', 'SKILL_LEVEL_UP')


def timelineEvents(timeline):
    """所有帧的事件按时间排序 (同一时间保持原顺序)"""
    if not isinstance(timeline, dict):
        return []

    events = [event for frame in timeline.get('frames') or []
              for event in (frame.get('events') or []) if isinstance(event, dict)]
    return sorted(events, key=lambda event: event.get('timestamp') or 0)


def hasBuildEvents(timeline):
    """时间线里是否有出装 / 加点事件 (LCU 目前的时间线没有)"""
    return any(event.get('type') in BUILD_EVENT_TYPES for event in timelineEvents(timeline))


def remapTimelineParticipants(timeline, detail):
    """
    SGP 时间线 (participants: [{participantId, puuid}]) 的 participantId 按 puuid 换成 LCU 对局详情
    (participantIdentities) 里的 participantId; 对不上的事件 (以及 participantId 0 的系统事件)
    保持原样, 不会被归到任何玩家. 返回新的时间线, 不修改输入
    """
    if not isinstance(timeline, dict) or not isinstance(detail, dict):
        return timeline

    sgpByPuuid = {p.get('puuid'): p.get('participantId')
                  for p in timeline.get('participants') or [] if isinstance(p, dict)}
    mapping = {}
    for identity in detail.get('participantIdentities') or []:
        puuid = (identity.get('player') or {}).get('puuid')
        sgpId = sgpByPuuid.get(puuid)
        if puuid and sgpId:
            mapping[sgpId] = identity.get('participantId')

    if not mapping or all(k == v for k, v in mapping.items()):
        return timeline

    frames = []
    for frame in timeline.get('frames') or []:
        events = []
        for event in frame.get('events') or []:
            pid = event.get('participantId')
            if pid in mapping:
                event = dict(event, participantId=mapping[pid])
            elif pid:
                # 对不上的玩家: 不能留着原编号, 否则会被算到同号的另一个人身上
                event = dict(event, participantId=None)
            events.append(event)
        frames.append(dict(frame, events=events))

    return dict(timeline, frames=frames)


def _participantEvents(timeline, participantId):
    return [event for event in timelineEvents(timeline)
            if event.get('participantId') == participantId]


def parsePurchases(timeline, participantId):
    """
    一名玩家的购买记录 [{'itemId', 'timestamp', 'sold'}] (按时间):
    - ITEM_PURCHASED: 新增一条;
    - ITEM_SOLD: 把这件物品最近一条未卖出的购买标为 sold;
    - ITEM_UNDO: beforeId (或旧格式的 itemId) 非 0 时撤销这件物品最近一次购买; 只有 afterId 时是
      撤销卖出, 把最近一条已卖出的恢复;
    - ITEM_DESTROYED (合成 / 消耗) 不影响展示.
    """
    purchases = []

    def latest(itemId, sold, before=None):
        for purchase in reversed(purchases):
            if purchase['itemId'] == itemId and purchase['sold'] == sold and \
                    (before is None or purchase['timestamp'] <= before):
                return purchase
        return None

    for event in _participantEvents(timeline, participantId):
        kind = event.get('type')
        timestamp = event.get('timestamp') or 0

        if kind == 'ITEM_PURCHASED':
            if event.get('itemId'):
                purchases.append({'itemId': event['itemId'], 'timestamp': timestamp,
                                  'sold': False})

        elif kind == 'ITEM_SOLD':
            purchase = latest(event.get('itemId'), False, timestamp)
            if purchase:
                purchase['sold'] = True

        elif kind == 'ITEM_UNDO':
            undone = event.get('beforeId') or event.get('itemId')
            if undone:
                purchase = latest(undone, False, timestamp)
                if purchase:
                    purchases.remove(purchase)
            elif event.get('afterId'):
                purchase = latest(event['afterId'], True, timestamp)
                if purchase:
                    purchase['sold'] = False

    return purchases


def groupPurchases(purchases):
    """
    按分钟分组: [{'minute', 'items': [{'itemId', 'count', 'sold'}]}].
    同一组里同一物品 (且卖出状态相同) 叠成一个, count 计数, 位置取第一次出现
    """
    groups = []
    for purchase in purchases:
        minute = purchase['timestamp'] // 60000
        if not groups or groups[-1]['minute'] != minute:
            groups.append({'minute': minute, 'items': []})

        items = groups[-1]['items']
        for item in items:
            if item['itemId'] == purchase['itemId'] and item['sold'] == purchase['sold']:
                item['count'] += 1
                break
        else:
            items.append({'itemId': purchase['itemId'], 'count': 1,
                          'sold': purchase['sold']})

    return groups


def parseItemGroups(timeline, participantId):
    return groupPurchases(parsePurchases(timeline, participantId))


def parseSkillOrder(timeline, participantId):
    """
    {'sequence': ['Q', 'E', 'W', ...] (最多 18 级), 'order': ['Q', 'E', 'W'] (主升顺序)}.
    进化 (levelUpType == 'EVOLVE') 不占等级. 主升顺序: 先点满 5 级的在前; 没点满的按点数多少,
    再按第一次加点的先后
    """
    sequence = []
    for event in _participantEvents(timeline, participantId):
        if event.get('type') != 'SKILL_LEVEL_UP' or event.get('levelUpType') == 'EVOLVE':
            continue
        key = SKILL_KEYS.get(event.get('skillSlot'))
        if key and len(sequence) < MAX_LEVEL:
            sequence.append(key)

    stats = {}
    for level, key in enumerate(sequence):
        if key == 'R':
            continue
        count, first, maxed = stats.get(key, (0, level, None))
        count += 1
        if count == 5:
            maxed = level
        stats[key] = (count, first, maxed)

    inf = MAX_LEVEL + 1
    order = sorted(stats, key=lambda k: (stats[k][2] if stats[k][2] is not None else inf,
                                         -stats[k][0], stats[k][1]))
    return {'sequence': sequence, 'order': order}


def _runeEntry(perk, selected):
    return {'id': perk.get('runeId'), 'name': perk.get('name', ''), 'icon': perk.get('icon'),
            'desc': perk.get('desc', ''), 'selected': selected}


def parseRunePage(stats, perkStyles):
    """
    符文页. perkStyles 为 connector.manager.perkStyles 的结构:
        {styleId: {'name', 'icon', 'slots': [[{'runeId', 'icon', 'name', 'desc'}, ...], ...]}}
    其中 slots[0] 为基石, slots[1:4] 为普通符文行, slots[4:7] 为属性碎片行.

    返回 {'primary': {'id', 'name', 'icon', 'rows'}, 'secondary': {... 'rows'} | None,
          'shards': rows} 或 None (没有符文数据 / 未知系别); rows 为 [[rune, ...], ...],
    rune 带 'selected'. 选中判断按 perk0..5 是否在该行 (不按下标)
    """
    if not isinstance(stats, dict) or not perkStyles:
        return None

    primary = perkStyles.get(stats.get('perkPrimaryStyle'))
    if not primary:
        return None

    chosen = {stats.get(f'perk{i}') for i in range(6)} - {None, 0}

    def tree(styleId, style, slots):
        return {'id': styleId, 'name': style.get('name', ''), 'icon': style.get('icon'),
                'rows': [[_runeEntry(perk, perk.get('runeId') in chosen) for perk in slot]
                         for slot in slots]}

    page = {'primary': tree(stats['perkPrimaryStyle'], primary, primary['slots'][:4])}

    subId = stats.get('perkSubStyle')
    sub = perkStyles.get(subId)
    page['secondary'] = tree(subId, sub, sub['slots'][1:4]) if sub else None

    # 属性碎片: 每行各自判断 (同一碎片可能出现在多行)
    shardIds = [stats.get(f'statPerk{i}') for i in range(3)]
    shardSlots = primary['slots'][4:7]
    if any(shardIds) and len(shardSlots) == 3:
        page['shards'] = [[_runeEntry(perk, perk.get('runeId') == shardId) for perk in slot]
                          for slot, shardId in zip(shardSlots, shardIds)]
    else:
        page['shards'] = []

    return page


def parseGameBuilds(detail, timeline, perkStyles):
    """
    整局 (按 participantId): {participantId: {'itemGroups', 'skills', 'runes'}}.
    时间线没有出装 / 加点事件时 itemGroups / skills 为 None; detail 缺失时 runes 为 None
    """
    hasEvents = hasBuildEvents(timeline)

    participantIds = set()
    statsById = {}
    if isinstance(detail, dict):
        for participant in detail.get('participants') or []:
            pid = participant.get('participantId')
            if pid is not None:
                participantIds.add(pid)
                statsById[pid] = participant.get('stats')

    if hasEvents:
        participantIds |= {event.get('participantId') for event in timelineEvents(timeline)
                           if event.get('type') in BUILD_EVENT_TYPES and event.get('participantId')}

    return {pid: {'itemGroups': parseItemGroups(timeline, pid) if hasEvents else None,
                  'skills': parseSkillOrder(timeline, pid) if hasEvents else None,
                  'runes': parseRunePage(statsById.get(pid), perkStyles)}
            for pid in participantIds}


def buildItemIds(builds):
    """所有玩家时间线里出现过的物品 id (预取图标用)"""
    return {item['itemId'] for build in builds.values() for group in build.get('itemGroups') or []
            for item in group['items']}
