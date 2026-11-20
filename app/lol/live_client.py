"""
Riot's Live Client Data API (served by the game process on https://127.0.0.1:2999 while a
game is in progress; self-signed certificate, no auth). Used for the in-game item gold
panel and overlay.

Documented at https://developer.riotgames.com/docs/lol ("Game Client API"). The docs give
the field names (allPlayers[].items[] has canUse / consumable / count / displayName /
itemID / price / slot; team "ORDER" in the sample; activePlayer.currentGold) but do not
define `price` or `count`. Assumptions made here, stated so they can be revisited:

- `price` is the item's full (total) gold cost per unit. The documented trinket sample has
  price 0, so trinkets contribute nothing without a special case.
- `count` is the stack size of stackable consumables (potions, control wards). For
  non-consumable items it is treated as 1, so a stack counter on a non-consumable item
  (if the game ever reports one) cannot multiply its price.
- Enemy items are what the scoreboard shows (last seen); unspent gold is only known for
  the local player (activePlayer.currentGold) and is not part of the item value.

Data only: no view imports. UI updates go through signalBus.liveGameDataChanged.
"""
import asyncio

import aiohttp

from app.common.logger import logger
from app.common.signals import signalBus

TAG = "LiveClient"

BASE_URL = "https://127.0.0.1:2999"
ALL_GAME_DATA = "/liveclientdata/allgamedata"

ORDER, CHAOS = "ORDER", "CHAOS"
LANES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")

POLL_INTERVAL = 3.0
REQUEST_TIMEOUT = 2.0

# gameData.gameMode values that are not one team against another: Arena, TFT, Swarm.
# UNVERIFIED: taken from community reports, not from Riot's docs
EXCLUDED_MODES = {"CHERRY", "TFT", "STRAWBERRY"}

# A difference counts as even below max(EVEN_GOLD, EVEN_SHARE x the larger side).
# One rule for the team headline and every lane
EVEN_GOLD = 150
EVEN_SHARE = 0.03


# ---------------- pure functions ----------------

def _num(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def itemValue(item) -> int:
    """Gold value of one inventory entry: price, times count for stackable consumables"""
    if not isinstance(item, dict):
        return 0
    price = max(_num(item.get('price')), 0)
    count = max(_num(item.get('count')), 1) if item.get('consumable') else 1
    return price * count


def playerItemValue(player) -> int:
    items = (player or {}).get('items') or []
    return sum(itemValue(item) for item in items)


def playerName(player) -> str:
    player = player or {}
    return (player.get('riotIdGameName') or player.get('riotId')
            or player.get('summonerName') or "")


def _sameName(a, b):
    return bool(a) and bool(b) and a.lower() == b.lower()


def localTeam(data):
    """Team ("ORDER" / "CHAOS") of the local player, or None when it can't be told
    (spectating, or activePlayer missing)"""
    data = data or {}
    active = data.get('activePlayer') or {}
    players = data.get('allPlayers') or []

    keys = [active.get('riotId'), active.get('summonerName'), active.get('riotIdGameName')]
    for key in keys:
        if not key:
            continue
        for player in players:
            if not isinstance(player, dict):
                continue
            names = (player.get('riotId'), player.get('summonerName'),
                     player.get('riotIdGameName'))
            if any(_sameName(key, n) for n in names):
                team = player.get('team')
                if team in (ORDER, CHAOS):
                    return team

    # riotId "Name#TAG" against riotIdGameName "Name"
    gameName = (active.get('riotId') or "").split('#')[0]
    for player in players:
        if isinstance(player, dict) and _sameName(gameName, player.get('riotIdGameName')):
            if player.get('team') in (ORDER, CHAOS):
                return player.get('team')
    return None


def splitTeams(data):
    ally, enemy = [], []
    team = localTeam(data) or ORDER
    for player in (data or {}).get('allPlayers') or []:
        if not isinstance(player, dict):
            continue
        if player.get('team') == team:
            ally.append(player)
        elif player.get('team') in (ORDER, CHAOS):
            enemy.append(player)
    return ally, enemy


def teamTotal(players) -> int:
    return sum(playerItemValue(p) for p in players or [])


def _assignLanes(players):
    """Five lane slots: players with a known, unique position go to that lane, the rest
    fill the empty slots in list order"""
    slots = [None] * len(LANES)
    rest = []
    for player in players[:len(LANES) * 2]:
        position = (player.get('position') or "").upper()
        if position in LANES and slots[LANES.index(position)] is None:
            slots[LANES.index(position)] = player
        else:
            rest.append(player)
    for i in range(len(slots)):
        if slots[i] is None and rest:
            slots[i] = rest.pop(0)
    return slots


def pairLanes(ally, enemy):
    """
    [{'position', 'ally', 'enemy', 'diff'}] for the five lanes; diff is ally - enemy item
    value. 'position' is the lane name when either side's player reported it, else ""
    (row in list order). Rows with nobody on either side are dropped
    """
    allySlots, enemySlots = _assignLanes(list(ally or [])), _assignLanes(list(enemy or []))
    lanes = []
    for i, lane in enumerate(LANES):
        a, e = allySlots[i], enemySlots[i]
        if a is None and e is None:
            continue
        known = any(((p or {}).get('position') or "").upper() == lane for p in (a, e))
        lanes.append({
            'position': lane if known else "",
            'ally': a, 'enemy': e,
            'diff': playerItemValue(a) - playerItemValue(e),
        })
    return lanes


def isEven(diff, a, b):
    """True when a - b == diff is small next to the values compared (see EVEN_GOLD)"""
    return abs(diff) < max(EVEN_GOLD, EVEN_SHARE * max(abs(a or 0), abs(b or 0)))


def _playerSummary(player):
    if player is None:
        return None
    scores = player.get('scores') or {}
    return {
        'championName': player.get('championName') or "",
        'name': playerName(player),
        'position': (player.get('position') or "").upper(),
        'value': playerItemValue(player),
        'level': _num(player.get('level')),
        'isDead': bool(player.get('isDead')),
        'kills': _num(scores.get('kills')),
        'deaths': _num(scores.get('deaths')),
        'assists': _num(scores.get('assists')),
    }


def summarizeGameData(data):
    """
    allgamedata payload -> the plain dict the panel and overlay draw, or None when the
    payload has no players. All values are from the local player's team perspective
    """
    if not isinstance(data, dict):
        return None
    players = [p for p in data.get('allPlayers') or [] if isinstance(p, dict)]
    if not players:
        return None

    team = localTeam(data)
    ally, enemy = splitTeams(data)
    gameData = data.get('gameData') or {}
    gameMode = str(gameData.get('gameMode') or "").upper()
    if gameMode in EXCLUDED_MODES:
        return None
    # only two teams of players make sense here
    if not ally or not enemy or len(ally) + len(enemy) != len(players):
        return None

    allyTotal, enemyTotal = teamTotal(ally), teamTotal(enemy)
    active = data.get('activePlayer') or {}
    gold = active.get('currentGold')

    lanes = []
    for lane in pairLanes(ally, enemy):
        a, e = _playerSummary(lane['ally']), _playerSummary(lane['enemy'])
        lanes.append({'position': lane['position'], 'diff': lane['diff'],
                      'even': isEven(lane['diff'], (a or {}).get('value'),
                                     (e or {}).get('value')),
                      'ally': a, 'enemy': e})

    def ranked(team):
        return sorted((_playerSummary(p) for p in team), key=lambda p: -p['value'])

    return {
        'team': team or ORDER,
        'localKnown': team is not None,
        'gameMode': gameMode,
        'gameTime': float(gameData.get('gameTime') or 0),
        'allyTotal': allyTotal,
        'enemyTotal': enemyTotal,
        'diff': allyTotal - enemyTotal,
        'even': isEven(allyTotal - enemyTotal, allyTotal, enemyTotal),
        # lane rows only mean something when the game reported every position
        'hasLanes': bool(lanes) and all(lane['position'] for lane in lanes),
        'lanes': lanes,
        'allyPlayers': ranked(ally),
        'enemyPlayers': ranked(enemy),
        'currentGold': float(gold) if isinstance(gold, (int, float)) else None,
    }


def resolveChampionId(championName, champs):
    """championName from the live API -> championId via the connector's {id: name} map"""
    if not championName or not champs:
        return None
    low = championName.lower()
    for championId, name in champs.items():
        if isinstance(name, str) and name.lower() == low:
            return championId
    return None


# ---------------- network ----------------

class LiveClient:
    """GET the live game data; any failure (game not running, loading screen, timeout,
    bad JSON) returns None quietly"""

    def __init__(self, baseUrl=BASE_URL, timeout=REQUEST_TIMEOUT):
        self.baseUrl = baseUrl
        self.timeout = timeout
        self.session = None

    async def allGameData(self):
        try:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    base_url=self.baseUrl,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    connector=aiohttp.TCPConnector(limit=1))
            async with self.session.get(ALL_GAME_DATA, ssl=False, proxy=None) as res:
                if res.status != 200:
                    return None
                return await res.json(content_type=None)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def close(self):
        # detach first: a fetch running meanwhile opens a fresh session instead of
        # reusing the one being closed
        session, self.session = self.session, None
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass


class LiveClientPoller:
    """
    Polls the live game data every `interval` seconds while started. One poll at a time
    (the next sleep starts only after the previous fetch returned), a failing fetch or
    callback never ends the loop, and nothing raises into the Qt loop. Each changed
    summary (or None when the data goes away) is passed to `onData`, by default
    signalBus.liveGameDataChanged.emit
    """

    def __init__(self, fetch=None, onData=None, interval=POLL_INTERVAL):
        self.client = LiveClient()
        self.fetch = fetch or self.client.allGameData
        self.onData = onData or signalBus.liveGameDataChanged.emit
        self.interval = interval
        self.task = None
        self.last = None

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def start(self):
        if self.running:
            return
        self.task = asyncio.ensure_future(self.__loop())

    def stop(self):
        """Cancel polling and clear whatever is displayed"""
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()
        if self.client.session is not None:
            try:
                asyncio.ensure_future(self.client.close())
            except RuntimeError:  # no event loop (shutdown)
                pass
        self.__publish(None)

    async def aclose(self):
        """stop() for the app's async shutdown: also waits for the session to close"""
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        await self.client.close()
        self.__publish(None)

    async def __loop(self):
        try:
            while True:
                try:
                    summary = summarizeGameData(await self.fetch())
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"live client poll failed: {e!r}", TAG)
                    summary = None
                self.__publish(summary)
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            pass

    def __publish(self, summary):
        if summary == self.last:
            return
        self.last = summary
        try:
            self.onData(summary)
        except Exception as e:
            logger.error(f"live client update failed: {e!r}", TAG)


liveClientPoller = LiveClientPoller()
