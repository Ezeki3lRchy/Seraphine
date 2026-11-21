"""
Cooldown ledger model: rank estimate, estimated cooldowns and the click timers (no Qt).

The ledger is a rough record, not a calculation: every timer starts on the user's click, its
length is the ability's base cooldown at an estimated rank divided by (1 + haste / 100) from held
items only (ultimate haste for R, summoner spell haste for the spells). No runes, stacks or
refunds. Level and items are the last values the live API reported.

`db` is the merged database of `app/lol/ledger_data.py` (the shape of
`app/resource/ledger/ledger_baseline.json`) or the LedgerDatabase holding it.
"""
import math
import time

# The live API's positions, in the ledger's row order (same as app/lol/live_client.py LANES)
LANES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ABILITIES = ('Q', 'W', 'E', 'R')
SPELL_CELLS = ('D', 'F')
FLASH = 'SummonerFlash'
TELEPORT = 'SummonerTeleport'
TP_CELL = 'TP'
ITEM_PREFIX = 'item:'
MAX_ITEM_CELLS = 3
MAX_LEVEL = 18

DEFAULT_PRIORITY = 'QWE'
DEFAULT_MAX_RANK = {'Q': 5, 'W': 5, 'E': 5, 'R': 3}
DEFAULT_R_LEVELS = (6, 11, 16)

MAX_EXTRAPOLATE = 6.0      # seconds the clock runs on past the last game time (a paused game)
NEW_GAME_BACKWARD = 30.0   # game time dropping by more than this = a new game


def _data(db):
    return getattr(db, 'data', db) or {}


def validPriority(priority):
    """'QEW' style max order: Q, W and E once each, optionally R (an R that ranks like a basic
    ability, as Udyr's)"""
    if not isinstance(priority, str):
        return False
    p = priority.upper()
    return len(p) == len(set(p)) and set('QWE') <= set(p) <= set(ABILITIES)


def estimateRanks(level, priority, maxRank, rLevels):
    """
    Level -> estimated rank of Q, W, E, R.

    The first skill points go one each into the abilities of `priority` in that order; R gains a
    rank at each level of `rLevels` (a level-1 entry is a free starting rank and does not use that
    level's point); every other point goes to the first ability of `priority` that can take it: a
    basic ability's rank r needs level >= 2r - 1, and no rank exceeds `maxRank`
    """
    level = max(0, min(MAX_LEVEL, int(level or 0)))
    order = (priority or DEFAULT_PRIORITY).upper() if validPriority(priority) else DEFAULT_PRIORITY
    maxRank = {**DEFAULT_MAX_RANK, **(maxRank or {})}
    rLevels = DEFAULT_R_LEVELS if rLevels is None else tuple(rLevels)
    rIsBasic = 'R' in order
    ranks = dict.fromkeys(ABILITIES, 0)

    def canTake(ability, lv):
        return ranks[ability] < maxRank[ability] and lv >= 2 * (ranks[ability] + 1) - 1

    for lv in range(1, level + 1):
        if not rIsBasic and lv in rLevels and ranks['R'] < maxRank['R']:
            ranks['R'] += 1
            if lv > 1:
                continue
        first = [a for a in order if ranks[a] == 0 and canTake(a, lv)]
        if first:
            ranks[first[0]] += 1
            continue
        for ability in order:
            if canTake(ability, lv):
                ranks[ability] += 1
                break
    return ranks


def championEntry(db, champion):
    """The database row of a champion: by name (exact, then ignoring case, spaces and
    punctuation), by Data Dragon id (e.g. 'MonkeyKing', as in the live API's rawChampionName) or
    by championId (int); None when unknown"""
    champions = _data(db).get('champions') or {}
    if isinstance(champion, int):
        return next((c for c in champions.values() if c.get('championId') == champion), None)
    if not champion:
        return None
    if champion in champions:
        return champions[champion]
    norm = _norm(champion)
    return next((c for name, c in champions.items() if norm in (_norm(name), _norm(c.get('id') or ''))),
                None)


def _norm(name):
    return ''.join(ch for ch in str(name).lower() if ch.isalnum())


def spellForCell(entry, cellId):
    """Spell key in a spell column: 'D' is Flash whenever the enemy carries it (else spell one),
    'F' the other spell. '' or None when there is none"""
    spells = list((entry or {}).get('spells') or [])[:2]
    spells += [''] * (2 - len(spells))
    if FLASH in spells:
        other = spells[1] if spells[0] == FLASH else spells[0]
        pair = (FLASH, other)
    else:
        pair = tuple(spells)
    return {'D': pair[0], 'F': pair[1]}.get(cellId) or None


def carriesTeleport(entry):
    return any('Teleport' in (s or '') for s in (entry or {}).get('spells') or [])


def _haste(db, items, field):
    table = _data(db).get('items') or {}
    return sum((table.get(str(i)) or {}).get(field) or 0 for i in items or [])


def _positive(value):
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def cooldown(db, champion, cell, level, items, spells=None):
    """
    Estimated cooldown in seconds of one ledger cell, or None when it cannot be estimated (the
    cell then does nothing).

    cell: 'Q' 'W' 'E' 'R'; 'D' / 'F' (resolved through `spells`, the roster entry's two spell
    keys); a summoner spell key (e.g. 'SummonerFlash'); 'TP'; or 'item:<id>'.
    `items` are the held item ids (each one's haste counts)
    """
    data = _data(db)
    if cell in ABILITIES:
        row = championEntry(data, champion)
        if row is None:
            return None
        ranks = estimateRanks(level, row.get('priority'), row.get('maxRank'), row.get('rLevels'))
        perRank = ((row.get('spells') or {}).get(cell) or {}).get('cooldown')
        if not perRank:
            return None
        # a cast was seen, so the ability has at least rank 1 whatever the estimate says
        rank = min(max(1, ranks[cell]), len(perRank))
        base = _positive(perRank[rank - 1])
        if base is None:
            return None
        haste = _haste(data, items, 'haste') + (_haste(data, items, 'ultHaste') if cell == 'R' else 0)
        return base / (1 + haste / 100)

    if isinstance(cell, str) and cell.startswith(ITEM_PREFIX):
        return _positive(((data.get('items') or {}).get(cell[len(ITEM_PREFIX):]) or {}).get('active'))

    if cell in SPELL_CELLS:
        cell = spellForCell({'spells': spells}, cell)
    elif cell == TP_CELL:
        cell = TELEPORT
    base = _positive(((data.get('summonerSpells') or {}).get(cell) or {}).get('cooldown')) if cell else None
    if base is None:
        return None
    return base / (1 + _haste(data, items, 'spellHaste') / 100)


def ledgerVisible(summary):
    """The panel is for Summoner's Rift with five enemies on five known, distinct positions
    (URF, ARURF and One for All also run on map 11, hence the mode check)"""
    if not isinstance(summary, dict):
        return False
    if summary.get('gameMode') != 'CLASSIC' or summary.get('mapNumber') != 11:
        return False
    roster = summary.get('enemyRoster') or []
    positions = [e.get('position') for e in roster if isinstance(e, dict)]
    return len(roster) == 5 and len(positions) == 5 and set(positions) == set(LANES)


def isCountdownCell(cellId):
    """Q W E R show seconds left; spells, items and TP show the game time they return"""
    return cellId in ABILITIES


def cellText(cellId, state):
    """Text of a running cell from `LedgerTimers.state`: seconds left ('120') for Q W E R, the
    return game time ('14:10') otherwise; '' when idle"""
    status, secondsLeft, returnGameTime = state
    if status != 'running':
        return ''
    if isCountdownCell(cellId):
        return str(max(0, math.ceil(secondsLeft)))
    total = max(0, math.ceil(returnGameTime))
    return f'{total // 60:02d}:{total % 60:02d}'


class LedgerTimers:
    """
    The click timers of the ledger, in game seconds: {(rowKey, cellId): returnGameTime}.

    Rows are keyed by the roster entry's `key` (its position on the panel); spell cells by column
    ('D', 'F'), so a spell upgraded in place keeps its timer. Between polls the clock is
    extrapolated with `clock`, at most MAX_EXTRAPOLATE seconds past the last game time.
    `db` gives the items that get a cell (those with an active cooldown)
    """

    def __init__(self, clock=time.monotonic, db=None):
        self._clock = clock
        self._db = db
        self._timers = {}
        self._rows = {}          # rowKey -> {'items': held ids, 'itemCells': [ids]}
        self._gameTime = None
        self._anchor = None

    def now(self):
        """Estimated current game time, None before the first sync"""
        if self._gameTime is None:
            return None
        return self._gameTime + min(max(0.0, self._clock() - self._anchor), MAX_EXTRAPOLATE)

    def sync(self, gameTime, roster):
        """Every summary: a game time dropping by more than NEW_GAME_BACKWARD is a new game (all
        timers cleared); rows missing from a non-empty roster are dropped; item cells follow the
        held items"""
        gameTime = float(gameTime or 0)
        if self._gameTime is not None and gameTime < self._gameTime - NEW_GAME_BACKWARD:
            self.clear()
        self._gameTime, self._anchor = gameTime, self._clock()

        entries = [e for e in roster or [] if isinstance(e, dict) and e.get('key')]
        if entries:
            keys = {e['key'] for e in entries}
            for key in list(self._rows):
                if key not in keys:
                    del self._rows[key]
            for k in [k for k in self._timers if k[0] not in keys]:
                del self._timers[k]
        for entry in entries:
            row = self._rows.setdefault(entry['key'], {'items': [], 'itemCells': []})
            row['items'] = list(entry.get('items') or [])
            self._updateItemCells(entry['key'], row)

    def _hasActive(self, itemId):
        """An item with an active gets a cell, an inert one when its cooldown is unknown"""
        item = (_data(self._db).get('items') or {}).get(str(itemId)) or {}
        return bool(item.get('hasActive')) or _positive(item.get('active')) is not None

    def _updateItemCells(self, key, row):
        """Item slots stay where they are: a removed item leaves a hole (None) that the next new
        item fills, so no cell moves under the cursor; trailing holes are dropped"""
        held = row['items']
        slots = [i if i is not None and (i in held or self._running(key, ITEM_PREFIX + str(i))) else None
                 for i in row['itemCells']]
        for itemId in held:
            if itemId in slots or not self._hasActive(itemId):
                continue
            if None in slots:
                slots[slots.index(None)] = itemId
            elif len(slots) < MAX_ITEM_CELLS:
                slots.append(itemId)
        while slots and slots[-1] is None:
            slots.pop()
        row['itemCells'] = slots

    def cells(self, entry):
        """Cell ids of a row: Q W E R D F, then up to three item cells ('item:<id>', or None for an
        empty slot between item cells: a removed item's slot is kept until a new item fills it, so
        cells never move; a cell stays while its timer runs after the item left), then TP on the
        top row when the enemy carries no Teleport"""
        key = (entry or {}).get('key')
        row = self._rows.get(key)
        if row is None:
            row = {'items': list((entry or {}).get('items') or []), 'itemCells': []}
        else:
            row = {'items': row['items'], 'itemCells': list(row['itemCells'])}
        self._updateItemCells(key, row)
        if key in self._rows:
            self._rows[key]['itemCells'] = row['itemCells']
        cells = [*ABILITIES, *SPELL_CELLS,
                 *(None if i is None else ITEM_PREFIX + str(i) for i in row['itemCells'])]
        if (entry or {}).get('position') == 'TOP' and not carriesTeleport(entry):
            cells.append(TP_CELL)
        return cells

    def _running(self, rowKey, cellId):
        end = self._timers.get((rowKey, cellId))
        now = self.now()
        if end is None or now is None:
            return False
        if end <= now:
            del self._timers[(rowKey, cellId)]
            return False
        return True

    def toggle(self, rowKey, cellId, duration):
        """A running cell is cancelled; an idle one starts a timer of `duration` seconds. Nothing
        happens without a duration (inestimable cell) or before the first sync"""
        if self._running(rowKey, cellId):
            del self._timers[(rowKey, cellId)]
            return
        now = self.now()
        if now is None or not isinstance(duration, (int, float)) or duration <= 0:
            return
        self._timers[(rowKey, cellId)] = now + float(duration)

    def state(self, rowKey, cellId):
        """('running', secondsLeft, returnGameTime) or ('idle', 0.0, None); an expired timer is idle"""
        if not self._running(rowKey, cellId):
            return ('idle', 0.0, None)
        end = self._timers[(rowKey, cellId)]
        return ('running', end - self.now(), end)

    def active(self):
        """True while any timer runs (the panel ticks only then)"""
        return any(self._running(*k) for k in list(self._timers))

    def clear(self):
        self._timers.clear()
        self._rows.clear()
        self._gameTime = self._anchor = None
