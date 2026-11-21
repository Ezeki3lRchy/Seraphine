"""
Cooldown ledger database: the bundled baseline, the per-patch OP.GG cache and the user's
priorities, merged per champion and field (override > cache > baseline; a missing or null field
keeps the lower layer).

- baseline: `app/resource/ledger/ledger_baseline.json` (built by tools/build_ledger_baseline.py)
- cache: `<config_dir()>/ledger/cache.json`
  {"schema": 1, "patch": "X.Y", "champions": {name: {"priority": "QEW", "fetchedAt": iso}}}
- override: `<config_dir()>/ledger/priorities.json` {name: "QEW"}, edited by the user

`load()` reads files only. `refresh()` asks OP.GG for the skill priority of champions the cache
does not hold yet, at most once per champion per session; it never raises into the caller
except CancelledError.
"""
import asyncio
import copy
import datetime
import json
import os

from app.common import platform
from app.common.logger import logger
from app.lol.ledger_model import LANES, championEntry, validPriority

TAG = "ledger"
SCHEMA = 1
# next to this package, not the working directory (frozen: <_MEIPASS>/app/resource/...)
BASELINE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'resource', 'ledger', 'ledger_baseline.json')
CACHE_NAME = 'cache.json'
OVERRIDE_NAME = 'priorities.json'
# the live API's positions -> OP.GG's
OPGG_POSITIONS = dict(zip(LANES, ('TOP', 'JUNGLE', 'MID', 'ADC', 'SUPPORT')))


class LedgerDataError(RuntimeError):
    """The bundled baseline is missing or unreadable"""


def _patchKey(patch):
    """'16.23' -> (16, 23) for ordering; (0, 0) when not of that shape"""
    p = patchOf(patch)
    return tuple(int(x) for x in p.split('.')) if p else (0, 0)


def patchOf(version):
    """'16.23' from '16.23', '16.23.1' or 16.23; None when not of that shape"""
    parts = str(version or '').split('.')
    if len(parts) < 2 or not all(p.isascii() and p.isdigit() for p in parts[:2]):
        return None
    return '.'.join(parts[:2])


def _readJson(path):
    """Parsed JSON, or None when the file is absent or unreadable"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.warning(f"ledger: {os.path.basename(path)} ignored ({type(e).__name__})", TAG)
        return None


def priorityFromBuild(result):
    """('QEW', 'X.Y') from what opgg.getChampionBuild returns, or from a raw OP.GG build
    response; None for a part that is missing"""
    if not isinstance(result, dict):
        return None, None
    version = result.get('version') or (result.get('meta') or {}).get('version')
    data = result.get('data') or {}
    ids = ((data.get('championSkills') or {}).get('masteries')
           or ((data.get('skill_masteries') or [{}])[0] or {}).get('ids'))
    priority = ''.join(ids) if isinstance(ids, list) and all(isinstance(i, str) for i in ids) else None
    return (priority if validPriority(priority) else None), patchOf(version)


class LedgerDatabase:
    FETCH_TIMEOUT = 10.0
    MAX_FETCHES = 5

    def __init__(self, baselinePath=BASELINE_PATH, configDir=None):
        self.baselinePath = baselinePath
        self._configDir = configDir
        self.baseline = None
        self.cache = None
        self.override = {}
        self.data = None
        self.fetchTimeout = self.FETCH_TIMEOUT
        self._attempted = set()

    @property
    def ledgerDir(self):
        return os.path.join(self._configDir or platform.config_dir(), 'ledger')

    @property
    def cachePath(self):
        return os.path.join(self.ledgerDir, CACHE_NAME)

    @property
    def overridePath(self):
        return os.path.join(self.ledgerDir, OVERRIDE_NAME)

    @property
    def baselinePatch(self):
        return (self.baseline or {}).get('patch')

    @property
    def cachePatch(self):
        return (self.cache or {}).get('patch')

    def load(self):
        """Read baseline, cache and override (files only) and merge them. A missing or unreadable
        baseline raises LedgerDataError; a bad cache or override is ignored"""
        try:
            with open(self.baselinePath, encoding='utf-8') as f:
                baseline = json.load(f)
        except (OSError, ValueError) as e:
            raise LedgerDataError(
                f"ledger baseline unreadable ({type(e).__name__}): {self.baselinePath}") from None
        if not isinstance(baseline, dict) or baseline.get('schema') != SCHEMA \
                or not isinstance(baseline.get('champions'), dict):
            raise LedgerDataError(f"ledger baseline has no schema {SCHEMA} champions: {self.baselinePath}")
        self.baseline = baseline

        cache = _readJson(self.cachePath)
        valid = (isinstance(cache, dict) and cache.get('schema') == SCHEMA and patchOf(cache.get('patch'))
                 and isinstance(cache.get('champions'), dict))
        if cache is not None and not valid:
            logger.warning("ledger: cache ignored (unknown schema or shape)", TAG)
        elif valid and _patchKey(cache['patch']) < _patchKey(baseline.get('patch')):
            logger.info("ledger: cache ignored (older patch than the baseline)", TAG)
            valid = False
        self.cache = cache if valid else None

        override = _readJson(self.overridePath)
        self.override = override if isinstance(override, dict) else {}
        self._merge()
        return self

    def _merge(self):
        """Only `priority` comes from the cache and the override; one is taken only when it is valid
        and fits the champion (an R in it exactly when R ranks like a basic ability, rLevels [])"""
        data = copy.deepcopy(self.baseline)
        layers = [{name: entry.get('priority') for name, entry in
                   ((self.cache or {}).get('champions') or {}).items() if isinstance(entry, dict)},
                  dict(self.override)]
        for layer in layers:
            for name, priority in layer.items():
                row = championEntry(data, name)
                if row is None or not validPriority(priority):
                    continue
                priority = priority.upper()
                if ('R' in priority) != (row.get('rLevels') == []):
                    continue
                row['priority'] = priority
        self.data = data

    def _wanted(self, champions):
        """[(name, championId, opggPosition)] of champions to fetch now"""
        cached = set(((self.cache or {}).get('champions') or {}))
        wanted = []
        for c in champions or []:
            if isinstance(c, dict):
                row = championEntry(self.baseline, c.get('rawChampionName')) \
                    or championEntry(self.baseline, c.get('championName'))
                position = c.get('position')
            else:
                row, position = championEntry(self.baseline, c), ''
            if row is None:
                continue
            canonical = next(n for n, r in self.baseline['champions'].items() if r is row)
            if canonical in cached or canonical in self._attempted:
                continue
            self._attempted.add(canonical)
            wanted.append((canonical, row.get('championId'), OPGG_POSITIONS.get(position, 'MID')))
            if len(wanted) >= self.MAX_FETCHES:
                break
        return wanted

    async def _fetchOne(self, fetchBuild, region, mode, tier, championId, position):
        try:
            result = await asyncio.wait_for(fetchBuild(region, mode, championId, position, tier),
                                            self.fetchTimeout)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:
            logger.warning(f"ledger refresh: one champion failed ({type(e).__name__})", TAG)
            return None, None
        return priorityFromBuild(result)

    async def refresh(self, fetchBuild, champions, region='global', tier='emerald_plus', mode='ranked'):
        """
        Fetch the OP.GG skill priority of the given champions (names, or roster entries with
        'championName' / 'position') that the cache does not hold, at most MAX_FETCHES calls of
        `fetchBuild(region, mode, championId, position, tier)` (the signature of
        opgg.getChampionBuild), each bounded by fetchTimeout. A response of another patch starts a
        new cache. Returns the number of champions stored.

        With opgg.getChampionBuild a cold session can mean MAX_FETCHES + 1 GETs: it also loads the
        tier list once (cached afterwards) to check the champion's positions
        """
        try:
            if self.baseline is None:
                return 0
            wanted = self._wanted(champions)
            if not wanted:
                return 0
            results = await asyncio.gather(*(self._fetchOne(fetchBuild, region, mode, tier, cid, pos)
                                             for _, cid, pos in wanted))
            cache = copy.deepcopy(self.cache) if self.cache else None
            stored = 0
            now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
            for (name, _, _), (priority, patch) in zip(wanted, results):
                if priority is None or patch is None:
                    continue
                if cache is None or cache.get('patch') != patch:
                    cache = {'schema': SCHEMA, 'patch': patch, 'champions': {}}
                cache['champions'][name] = {'priority': priority, 'fetchedAt': now}
                stored += 1
            if not stored:
                return 0
            self._writeCache(cache)
            self.cache = cache
            self._merge()
            logger.info(f"ledger refresh: {stored} champions, patch {cache['patch']}", TAG)
            return stored
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:
            logger.warning(f"ledger refresh failed ({type(e).__name__})", TAG)
            return 0

    def _writeCache(self, cache):
        os.makedirs(self.ledgerDir, exist_ok=True)
        tmp = self.cachePath + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, self.cachePath)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
