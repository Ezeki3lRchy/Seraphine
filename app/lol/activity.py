"""
近期对局的活跃时段 (生涯页 "Activity"): 按本地时间把对局分到 24 个小时 / 7 个星期几,
统计场次与胜负. 重开局计入场次, 但不计入胜负 (不影响胜率). 纯函数, 不依赖 Qt.
"""
from datetime import datetime


def gameTime(game):
    """对局开始的本地时间; 时间戳缺失 / 非法时返回 None"""
    timeStamp = (game or {}).get('timeStamp')
    if not timeStamp:
        return None
    try:
        return datetime.fromtimestamp(timeStamp / 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def tally(games, slots, slotOf):
    """slotOf(datetime) -> 0..slots-1; 返回 slots 个 {games, wins, losses}"""
    result = [{'games': 0, 'wins': 0, 'losses': 0} for _ in range(slots)]
    for game in games or []:
        t = gameTime(game)
        if t is None:
            continue
        entry = result[slotOf(t)]
        entry['games'] += 1
        if game.get('remake'):
            continue
        entry['wins' if game.get('win') else 'losses'] += 1
    return result


def activityByHour(games):
    """24 项, 第 i 项为 i:00 ~ i:59 开始的对局"""
    return tally(games, 24, lambda t: t.hour)


def activityByWeekday(games):
    """7 项, 周一 (0) ~ 周日 (6)"""
    return tally(games, 7, lambda t: t.weekday())


def winRate(entry):
    """0..1; 没有计入胜负的对局 (无对局或全是重开) 时为 None"""
    counted = entry['wins'] + entry['losses']
    return entry['wins'] / counted if counted else None
