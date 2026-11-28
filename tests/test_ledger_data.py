"""Cooldown ledger database (app/lol/ledger_data): baseline / cache / override merge, every
fallback step, the bounded OP.GG refresh and where it writes."""
import asyncio
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from app.common import platform
from app.lol import ledger_data
from app.lol.ledger_data import LedgerDatabase, LedgerDataError
from app.lol.ledger_model import HEXFLASH

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'ledger', 'baseline_rows_16_23.json')


class AppError(BaseException):
    """Like the app's own exceptions (app/lol/exceptions.py), not an Exception subclass"""


class FakeOpgg:
    """fetchBuild with the signature of opgg.getChampionBuild, answering like it does"""

    def __init__(self, priorities=None, version='16.23.1', raises=None, hang=False):
        self.priorities = priorities or {}
        self.version = version
        self.raises = raises
        self.hang = hang
        self.calls = []
        self.cancelled = 0
        self.started = asyncio.Event()

    async def __call__(self, region, mode, championId, position, tier):
        self.calls.append((region, mode, championId, position, tier))
        self.started.set()
        if self.hang:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        if self.raises is not None:
            raise self.raises
        masteries = list(self.priorities.get(championId, 'EQW'))
        return {'data': {'championSkills': {'masteries': masteries, 'order': []}},
                'version': self.version, 'mode': mode}


EZREAL, KARMA, AHRI, LEE, UDYR, JAYCE, YUUMI, KAISA = 81, 43, 103, 64, 77, 126, 350, 145


class LedgerDataBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ledger-test-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = os.path.join(self.tmp, 'cfg')
        os.makedirs(self.cfg)
        env = mock.patch.dict(os.environ, {'SERAPHINE_CONFIG_DIR': self.cfg})
        env.start()
        self.addCleanup(env.stop)
        self.baselinePath = os.path.join(self.tmp, 'resource', 'ledger_baseline.json')
        os.makedirs(os.path.dirname(self.baselinePath))
        shutil.copyfile(FIXTURE, self.baselinePath)
        with open(FIXTURE, encoding='utf-8') as f:
            self.baseline = json.load(f)

    def db(self):
        return LedgerDatabase(baselinePath=self.baselinePath).load()

    def ledgerDir(self):
        return os.path.join(platform.config_dir(), 'ledger')

    def writeCache(self, champions, patch='16.23', schema=1):
        os.makedirs(self.ledgerDir(), exist_ok=True)
        with open(os.path.join(self.ledgerDir(), 'cache.json'), 'w', encoding='utf-8') as f:
            json.dump({'schema': schema, 'patch': patch, 'champions': champions}, f)

    def readCache(self):
        with open(os.path.join(self.ledgerDir(), 'cache.json'), encoding='utf-8') as f:
            return json.load(f)

    def priority(self, db, name):
        return db.data['champions'][name]['priority']


class LoadTest(LedgerDataBase):
    def test_no_cache_baseline(self):
        db = self.db()
        self.assertIsNone(db.cache)
        self.assertIsNone(db.cachePatch)
        self.assertEqual(db.baselinePatch, '16.23')
        self.assertEqual(db.data['champions'], self.baseline['champions'])

    def test_corrupt_cache_baseline(self):
        os.makedirs(self.ledgerDir())
        with open(os.path.join(self.ledgerDir(), 'cache.json'), 'w', encoding='utf-8') as f:
            f.write('{"schema": 1, "patch": ')
        db = self.db()
        self.assertIsNone(db.cache)
        self.assertEqual(db.data['champions'], self.baseline['champions'])

    def test_cache_of_another_schema_ignored(self):
        self.writeCache({'Ezreal': {'priority': 'WEQ'}}, schema=2)
        db = self.db()
        self.assertIsNone(db.cache)
        self.assertEqual(self.priority(db, 'Ezreal'), 'QEW')

    def test_partial_cache_over_baseline(self):
        self.writeCache({'Ezreal': {'priority': 'EQW', 'fetchedAt': '2026-11-21T10:00:00+00:00'},
                         'Ahri': {'priority': None, 'fetchedAt': '2026-11-21T10:00:00+00:00'}})
        db = self.db()
        self.assertEqual(set(db.data['champions']), set(self.baseline['champions']))   # every champion
        self.assertEqual(self.priority(db, 'Ezreal'), 'EQW')         # cached priority wins
        self.assertEqual(self.priority(db, 'Ahri'), 'QWE')           # null keeps the baseline
        self.assertEqual(self.priority(db, 'Karma'), 'QEW')          # not cached: baseline
        self.assertNotIn('fetchedAt', db.data['champions']['Ezreal'])
        self.assertEqual(db.data['champions']['Ezreal']['spells'], self.baseline['champions']['Ezreal']['spells'])

    def test_override_wins(self):
        self.writeCache({'Ezreal': {'priority': 'EQW'}, 'Ahri': {'priority': 'EWQ'}})
        os.makedirs(self.ledgerDir(), exist_ok=True)
        with open(os.path.join(self.ledgerDir(), 'priorities.json'), 'w', encoding='utf-8') as f:
            json.dump({'Ezreal': 'wqe', 'Ahri': 'nonsense', 'Karma': 'EWQ', 'Nobody': 'QWE'}, f)
        db = self.db()
        self.assertEqual(self.priority(db, 'Ezreal'), 'WQE')   # override > cache
        self.assertEqual(self.priority(db, 'Ahri'), 'EWQ')     # invalid override keeps the cache
        self.assertEqual(self.priority(db, 'Karma'), 'EWQ')    # override > baseline
        self.assertNotIn('Nobody', db.data['champions'])

    def test_only_fitting_priorities_taken(self):
        self.writeCache({'Ezreal': {'priority': 'QEW', 'spells': {'Q': {'cooldown': [1]}}, 'rLevels': [1]},
                         'Udyr': {'priority': 'QWE'},          # Udyr's R is basic: needs an R
                         'Karma': {'priority': 'QEWR'}})       # Karma's R is not
        os.makedirs(self.ledgerDir(), exist_ok=True)
        with open(os.path.join(self.ledgerDir(), 'priorities.json'), 'w', encoding='utf-8') as f:
            json.dump({'Udyr': 'RQWE', 'Ahri': 'QWER'}, f)
        db = self.db()
        self.assertEqual(db.data['champions']['Ezreal']['spells'], self.baseline['champions']['Ezreal']['spells'])
        self.assertEqual(db.data['champions']['Ezreal']['rLevels'], [6, 11, 16])
        self.assertEqual(self.priority(db, 'Udyr'), 'RQWE')     # override with R accepted
        self.assertEqual(self.priority(db, 'Karma'), 'QEW')
        self.assertEqual(self.priority(db, 'Ahri'), 'QWE')

    def test_cache_with_non_ascii_digits_ignored(self):
        self.writeCache({'Ezreal': {'priority': 'EQW'}}, patch='16.²23')    # '²'.isdigit() is True
        db = self.db()
        self.assertIsNone(db.cache)
        self.assertEqual(self.priority(db, 'Ezreal'), 'QEW')
        self.assertIsNone(ledger_data.patchOf('16.²23'))
        self.assertIsNone(ledger_data.patchOf('١٦.23'))                 # Arabic-Indic digits
        self.assertEqual(ledger_data.patchOf('16.23.1'), '16.23')

    def test_cache_older_than_baseline_ignored(self):
        self.writeCache({'Ezreal': {'priority': 'EQW'}}, patch='16.22')
        db = self.db()
        self.assertIsNone(db.cache)
        self.assertEqual(self.priority(db, 'Ezreal'), 'QEW')
        self.writeCache({'Ezreal': {'priority': 'EQW'}}, patch='16.24')   # newer: used
        self.assertEqual(self.priority(self.db(), 'Ezreal'), 'EQW')

    def test_missing_baseline_raises(self):
        os.remove(self.baselinePath)
        with self.assertRaises(LedgerDataError) as cm:
            self.db()
        self.assertIn('ledger baseline', str(cm.exception))

    def test_unusable_baseline_raises(self):
        for text in ('', '{"schema": 1}', '{"schema": 2, "champions": {}}'):
            with open(self.baselinePath, 'w', encoding='utf-8') as f:
                f.write(text)
            with self.subTest(text), self.assertRaises(LedgerDataError):
                self.db()

    def test_bundled_baseline(self):
        db = LedgerDatabase().load()
        champions = db.data['champions']
        self.assertGreaterEqual(len(champions), 170)
        self.assertEqual(db.baselinePatch, db.baseline['patch'])
        for name, row in champions.items():
            with self.subTest(name):
                self.assertTrue(ledger_data.validPriority(row['priority']))
                self.assertEqual(set(row['spells']), {'Q', 'W', 'E', 'R'})
                self.assertIsInstance(row['championId'], int)
                self.assertIsInstance(row['rLevels'], list)
        for key in ('SummonerFlash', 'SummonerTeleport', 'SummonerDot', 'S12_SummonerTeleportUpgrade',
                    'S5_SummonerSmitePlayerGanker'):
            self.assertIn(key, db.data['summonerSpells'])
        # every Smite the live data can report has a timer
        for key in ('SummonerSmite', 'S5_SummonerSmitePlayerGanker', 'SummonerSmiteAvatarOffensive',
                    'SummonerSmiteAvatarUtility', 'SummonerSmiteAvatarDefensive'):
            self.assertEqual(db.data['summonerSpells'][key]['cooldown'], 90, key)
        # Hexflash, under the key the model's 'HF' cell reads
        self.assertEqual(db.data['summonerSpells'][HEXFLASH]['cooldown'], 20)


class RefreshTest(LedgerDataBase):
    async def test_refresh_writes_cache_under_config_dir(self):
        db = self.db()
        fake = FakeOpgg({EZREAL: 'EWQ', AHRI: 'WQE'})
        with self.assertLogs('Seraphine', 'INFO') as logs:
            stored = await db.refresh(fake, [{'championName': 'Ezreal', 'position': 'BOTTOM'},
                                             {'championName': 'Ahri', 'position': 'MIDDLE'}])
        self.assertEqual(stored, 2)
        self.assertEqual(sorted(fake.calls), [('global', 'ranked', EZREAL, 'ADC', 'emerald_plus'),
                                              ('global', 'ranked', AHRI, 'MID', 'emerald_plus')])
        cache = self.readCache()
        self.assertEqual((cache['schema'], cache['patch']), (1, '16.23'))
        self.assertEqual({n: e['priority'] for n, e in cache['champions'].items()},
                         {'Ezreal': 'EWQ', 'Ahri': 'WQE'})
        self.assertTrue(all(e['fetchedAt'] for e in cache['champions'].values()))
        self.assertEqual(self.priority(db, 'Ezreal'), 'EWQ')
        self.assertEqual(self.priority(self.db(), 'Ahri'), 'WQE')    # read back by a new load
        self.assertTrue(any('ledger refresh: 2 champions, patch 16.23' in line for line in logs.output))

        written = []
        for root, _, files in os.walk(self.tmp):
            written += [os.path.join(root, f) for f in files]
        under = os.path.join(platform.config_dir(), '')
        self.assertEqual(sorted(p for p in written if p != self.baselinePath),
                         [os.path.join(self.ledgerDir(), 'cache.json')])
        self.assertTrue(all(p.startswith(under) for p in written if p != self.baselinePath))

    async def test_refresh_failure_keeps_cache(self):
        self.writeCache({'Karma': {'priority': 'EQW'}})
        db = self.db()
        for error in (RuntimeError('player secret'), AppError('x'), ValueError()):
            fake = FakeOpgg(raises=error)
            with self.subTest(type(error).__name__), self.assertLogs('Seraphine', 'WARNING') as logs:
                db._attempted.clear()
                self.assertEqual(await db.refresh(fake, ['Ezreal']), 0)
                self.assertEqual(len(fake.calls), 1)
                self.assertTrue(any(type(error).__name__ in line for line in logs.output))
                self.assertFalse(any('player secret' in line for line in logs.output))
        self.assertEqual(self.readCache()['champions'], {'Karma': {'priority': 'EQW'}})
        self.assertEqual(self.priority(db, 'Karma'), 'EQW')
        self.assertEqual(self.priority(db, 'Ezreal'), 'QEW')

    def test_interrupts_are_not_swallowed(self):
        # driven by hand, outside the event loop: asyncio itself re-raises these from tasks
        for error in (KeyboardInterrupt, SystemExit):
            db = self.db()
            with self.subTest(error.__name__), mock.patch.object(db, '_wanted', side_effect=error):
                coro = db.refresh(FakeOpgg(), ['Ezreal'])
                with self.assertRaises(error):
                    coro.send(None)

    async def test_roster_entry_found_by_raw_name(self):
        db = self.db()
        fake = FakeOpgg()
        await db.refresh(fake, [{'championName': '伊泽瑞尔', 'rawChampionName': 'Ezreal',
                                 'position': 'BOTTOM'},
                                {'championName': 'Wukong', 'rawChampionName': 'MonkeyKing',
                                 'position': 'TOP'}])
        self.assertEqual(sorted(c[2] for c in fake.calls), [62, EZREAL])

    async def test_response_without_priority_stores_nothing(self):
        db = self.db()

        async def empty(region, mode, championId, position, tier):
            return {'data': {'championSkills': None}, 'version': '16.23'}

        self.assertEqual(await db.refresh(empty, ['Ezreal']), 0)
        self.assertFalse(os.path.exists(os.path.join(self.ledgerDir(), 'cache.json')))

    async def test_new_patch_drops_old_entries(self):
        self.writeCache({'Karma': {'priority': 'EQW'}, 'Ahri': {'priority': 'EWQ'}}, patch='16.22')
        db = self.db()
        await db.refresh(FakeOpgg({EZREAL: 'WEQ'}, version='16.23'), ['Ezreal'])
        cache = self.readCache()
        self.assertEqual(cache['patch'], '16.23')
        self.assertEqual(set(cache['champions']), {'Ezreal'})
        self.assertEqual(self.priority(db, 'Karma'), 'QEW')     # back to the baseline
        self.assertEqual(db.cachePatch, '16.23')

    async def test_same_patch_adds_to_cache(self):
        self.writeCache({'Karma': {'priority': 'EQW'}})
        db = self.db()
        await db.refresh(FakeOpgg({EZREAL: 'WEQ'}), ['Ezreal', 'Karma'])
        self.assertEqual(set(self.readCache()['champions']), {'Karma', 'Ezreal'})

    async def test_once_per_champion_per_session(self):
        self.writeCache({'Karma': {'priority': 'EQW'}})
        db = self.db()
        fake = FakeOpgg(raises=RuntimeError())
        await db.refresh(fake, ['Ezreal', 'Karma'])        # Karma cached: no GET
        await db.refresh(fake, ['Ezreal', 'Karma'])        # Ezreal tried already: no GET
        self.assertEqual([c[2] for c in fake.calls], [EZREAL])
        ok = FakeOpgg()
        await db.refresh(ok, ['Ezreal', 'Ahri'])
        self.assertEqual([c[2] for c in ok.calls], [AHRI])

    async def test_at_most_five_gets_and_unknown_skipped(self):
        db = self.db()
        fake = FakeOpgg()
        names = ['Nobody', 'Ezreal', 'Karma', 'Ahri', 'Lee Sin', 'Udyr', 'Jayce', 'Yuumi']
        self.assertEqual(await db.refresh(fake, names), 5)
        self.assertEqual(sorted(c[2] for c in fake.calls), sorted([EZREAL, KARMA, AHRI, LEE, UDYR]))

    async def test_never_resolving_fetch_is_bounded(self):
        self.assertEqual(LedgerDatabase.FETCH_TIMEOUT, 10.0)
        db = self.db()
        self.assertEqual(db.fetchTimeout, 10.0)
        db.fetchTimeout = 0.05
        fake = FakeOpgg(hang=True)
        start = time.perf_counter()
        with self.assertLogs('Seraphine', 'WARNING') as logs:
            self.assertEqual(await db.refresh(fake, ['Ezreal', 'Ahri']), 0)
        self.assertLess(time.perf_counter() - start, 2.0)
        self.assertEqual(fake.cancelled, 2)
        self.assertTrue(any('TimeoutError' in line for line in logs.output))

    async def test_load_does_not_wait_for_refresh(self):
        db = self.db()
        fake = FakeOpgg(hang=True)
        task = asyncio.ensure_future(db.refresh(fake, ['Ezreal']))
        await asyncio.wait_for(fake.started.wait(), 5)       # the fetch has started and never ends
        start = time.perf_counter()
        other = LedgerDatabase(baselinePath=self.baselinePath).load()   # synchronous: no await
        self.assertLess(time.perf_counter() - start, 1.0)    # sanity bound only
        self.assertEqual(self.priority(other, 'Ezreal'), 'QEW')
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(task.done())                        # load returned while the fetch is pending
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):    # cancellation reaches the caller
            await task

    async def test_write_failure_does_not_raise(self):
        db = self.db()
        with mock.patch.object(ledger_data.os, 'replace', side_effect=PermissionError('denied')), \
                self.assertLogs('Seraphine', 'WARNING') as logs:
            self.assertEqual(await db.refresh(FakeOpgg(), ['Ezreal']), 0)
        self.assertTrue(any('PermissionError' in line for line in logs.output))
        self.assertEqual(self.priority(db, 'Ezreal'), 'QEW')


def _loadTool():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'tools', 'build_ledger_baseline.py')
    spec = importlib.util.spec_from_file_location('build_ledger_baseline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildToolTest(unittest.TestCase):
    """tools/build_ledger_baseline.py on Data Dragon / CommunityDragon / OP.GG shaped excerpts (no network)"""

    @classmethod
    def setUpClass(cls):
        cls.tool = _loadTool()

    def test_items(self):
        item = {'data': {
            '3118': {'maps': {'11': True}, 'tags': ['SpellDamage'], 'description':
                     '<mainText><stats><attention>15</attention> Ability Haste</stats><br><passive>Scorn'
                     '</passive><br>Gain 20 Ultimate Ability Haste.</mainText>'},
            '3158': {'maps': {'11': True}, 'tags': ['Boots'], 'description':
                     '<mainText><stats><attention>10</attention> Ability Haste</stats><passive>Ionian '
                     'Insight</passive><br>Gain 10 Summoner Spell Haste.</mainText>'},
            '3157': {'maps': {'11': True}, 'tags': ['Active'], 'description':
                     '<mainText><stats></stats><active>Time Stop</active><br>Stasis.</mainText>'},
            '9001': {'maps': {'11': True}, 'tags': ['Active'], 'description':
                     '<mainText><stats></stats><active>Thing</active><br>Does.</mainText>'},
            '2420': {'maps': {'11': True}, 'tags': ['Active'], 'description':
                     '<mainText><active>Time Stop</active> (Single use)<br>Stasis.</mainText>'},
            '2010': {'maps': {'11': True}, 'tags': [], 'description':
                     '<mainText><active>Active - Consume:</active> Eat the biscuit.</mainText>'},
            '3340': {'maps': {'11': True}, 'tags': ['Trinket', 'Vision'], 'description':
                     '<mainText><active>Active</active> (<scaleLevel>210 - 90</scaleLevel>s)</mainText>'},
            '2003': {'maps': {'11': True}, 'tags': ['Consumable'], 'description': '<active>Consume</active>'},
            '1001': {'maps': {'11': True}, 'tags': ['Boots'], 'description': '<stats>25 Move Speed</stats>'},
            '4000': {'maps': {'11': False}, 'tags': [], 'description':
                     '<stats><attention>20</attention> Ability Haste</stats>'},
        }}
        client = {'Items/3157': {'mDataValues': [{'mName': 'Cooldown', 'mValue': 120.0},
                                                 {'mName': 'Duration', 'mValue': 2.5}]},
                  'Items/2420': {'mDataValues': [{'mName': 'Cooldown', 'mValue': 90.0}]},
                  'Items/9001': {'mDataValues': [{'mName': 'CleanseCooldown', 'mValue': 30.0}]}}
        self.assertEqual(self.tool.buildItems(item, client), {
            '3118': {'active': None, 'haste': 15, 'ultHaste': 20, 'spellHaste': 0},
            '3158': {'active': None, 'haste': 10, 'ultHaste': 0, 'spellHaste': 10},
            '3157': {'active': 120, 'hasActive': True, 'haste': 0, 'ultHaste': 0, 'spellHaste': 0},
            '9001': {'active': None, 'hasActive': True, 'haste': 0, 'ultHaste': 0, 'spellHaste': 0},
        })

    def test_summoner_spells(self):
        summoner = {'data': {
            'SummonerFlash': {'key': '4', 'cooldown': [300], 'modes': ['ARAM', 'CLASSIC']},
            'SummonerSmite': {'key': '11', 'cooldown': [15], 'modes': ['CLASSIC']},
            'SummonerTeleport': {'key': '12', 'cooldown': [300], 'modes': ['CLASSIC']},
            'SummonerDot': {'key': '14', 'cooldown': [180], 'modes': ['CLASSIC']},
            'SummonerSnowball': {'key': '32', 'cooldown': [80], 'modes': ['ARAM']},
        }}

        def spell(cd, ammo=None, recharge=None):
            s = {'cooldownTime': [cd] * 7}
            if ammo:
                s['mMaxAmmo'] = [ammo] * 7
            if recharge:
                s['mAmmoRechargeTime'] = [recharge] * 7
            return {'mSpell': s}

        shared = {'Shared/Spells/SummonerFlash': spell(300.0),
                  'Shared/Spells/SummonerSmite': spell(15.0, ammo=2, recharge=90.0),
                  'Shared/Spells/SummonerTeleport': spell(360.0),
                  'Shared/Spells/S12_SummonerTeleportUpgrade': spell(300.0),
                  'Shared/Spells/S5_SummonerSmitePlayerGanker': spell(15.0, ammo=2, recharge=90.0),
                  'Shared/Spells/SummonerSmiteAvatarOffensive': spell(15.0, ammo=2, recharge=90.0),
                  'Shared/Spells/SummonerSmiteAvatarUtility': spell(15.0, ammo=2, recharge=90.0),
                  'Shared/Spells/SummonerSmiteAvatarDefensive': spell(15.0, ammo=2, recharge=90.0),
                  # the rune's spell as the client lists it: its own cooldown, a 2 s channel
                  'Shared/Spells/SummonerFlashPerksHextechFlashtraptionV2': spell(20.0)}
        stats = {'spellMismatch': []}
        self.assertEqual(self.tool.buildSummonerSpells(summoner, shared, stats), {
            'SummonerFlash': {'id': 4, 'cooldown': 300},
            'SummonerSmite': {'id': 11, 'cooldown': 90},               # charges: one charge's recharge
            'SummonerTeleport': {'id': 12, 'cooldown': 360},           # the client's value wins
            'SummonerDot': {'id': 14, 'cooldown': 180},                # not in the client data
            'S12_SummonerTeleportUpgrade': {'id': 12, 'cooldown': 300},
            'S5_SummonerSmitePlayerGanker': {'id': 11, 'cooldown': 90},
            # Primal Smite, one per jungle pet: Smite's icon
            'SummonerSmiteAvatarOffensive': {'id': 11, 'cooldown': 90},
            'SummonerSmiteAvatarUtility': {'id': 11, 'cooldown': 90},
            'SummonerSmiteAvatarDefensive': {'id': 11, 'cooldown': 90},
            # Hexflash: the client's cooldown under Flash's id
            'SummonerFlashPerksHextechFlashtraptionV2': {'id': 4, 'cooldown': 20},
        })
        # a spell with charges is not compared with Data Dragon's lockout (15)
        self.assertEqual(stats['spellMismatch'], ['SummonerTeleport: client 360, Data Dragon [300]'])
        self.assertEqual(self.tool.buildSummonerSpells(summoner)['S12_SummonerTeleportUpgrade'],
                         {'id': 12, 'cooldown': None})                # no client data: unknown

    def test_charges_as_a_scalar(self):
        shared = {'Shared/Spells/SummonerSmite': {'mSpell': {'cooldownTime': [15.0], 'mMaxAmmo': 2,
                                                             'mAmmoRechargeTime': [90.0]}},
                  'Shared/Spells/NoRecharge': {'mSpell': {'cooldownTime': [15.0], 'mMaxAmmo': 2}},
                  'Shared/Spells/SummonerFlash': {'mSpell': {'cooldownTime': [300.0], 'mMaxAmmo': 1}}}
        self.assertEqual(self.tool.clientSpellCooldown(shared, 'SummonerSmite'), (90, True))
        self.assertEqual(self.tool.clientSpellCooldown(shared, 'NoRecharge'), (None, True))
        shared['Shared/Spells/ScalarRecharge'] = {'mSpell': {'mMaxAmmo': 2, 'mAmmoRechargeTime': 90.0}}
        self.assertEqual(self.tool.clientSpellCooldown(shared, 'ScalarRecharge'), (90, True))
        self.assertEqual(self.tool.clientSpellCooldown(shared, 'SummonerFlash'), (300, True))
        self.assertEqual(self.tool.clientSpellCooldown(shared, 'SummonerDot'), (None, False))

    def test_write_failure_leaves_no_tmp(self):
        tmp = tempfile.mkdtemp(prefix='ledger-tool-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        out = os.path.join(tmp, 'ledger_baseline.json')
        with open(out, 'w', encoding='utf-8') as f:
            f.write('{"old": true}')
        with self.assertRaises(TypeError):
            self.tool.writeAtomic(out, {'schema': 1, 'bad': object()})    # json.dump fails part-way
        self.assertEqual(os.listdir(tmp), ['ledger_baseline.json'])
        with open(out, encoding='utf-8') as f:
            self.assertEqual(f.read(), '{"old": true}')                   # previous file untouched

    def stats(self):
        return {'priority_masteries': 0, 'priority_order': 0, 'priority_previous': 0, 'priority_default': 0,
                'nullCooldowns': 0, 'short': [], 'genuineShort': [], 'charges': [], 'recharge': [],
                'clientMissing': [],
                'rLevelsMismatch': [], 'spellMismatch': []}

    def test_champions(self):
        def spell(cd, maxrank):
            return {'cooldown': cd, 'maxrank': maxrank}

        full = {'data': {
            'Karma': {'key': '43', 'name': 'Karma', 'spells': [
                spell([9, 8, 7, 6, 5], 5), spell([12] * 5, 5), spell([10, 9.5, 9, 8.5, 8], 5),
                spell([40, 38, 36, 34], 4)]},
            'Udyr': {'key': '77', 'name': 'Udyr', 'spells': [spell([6] * 6, 6)] * 4},
            'Yuumi': {'key': '350', 'name': 'Yuumi', 'spells': [
                spell([6.5] * 6, 6), spell([0] * 5, 5), spell([10] * 5, 5), spell([120, 110, 100], 3)]},
            'Rengar': {'key': '107', 'name': 'Rengar', 'spells': [
                spell([0.25] * 5, 5), spell([0.25] * 5, 5), spell([0.25] * 5, 5), spell([110, 90, 70], 3)]},
            'MonkeyKing': {'key': '62', 'name': 'Wukong', 'spells': [spell([8] * 5, 5)] * 3 + [
                spell([120, 100, 80], 3)]},
        }}
        builds = {43: {'skill_masteries': [{'ids': ['Q', 'E', 'W'], 'builds': [
            {'order': ['Q', 'E', 'W', 'Q', 'Q', 'R', 'Q', 'E', 'Q', 'E', 'R', 'E', 'E', 'W', 'W']}]}]},
                  77: {'skill_masteries': [{'ids': ['Q', 'E', 'W', 'R']}]},
                  350: {'skills': [{'order': ['Q', 'E', 'Q', 'E', 'Q', 'R', 'Q', 'E', 'W']}]}}
        previous = {'Rengar': {'priority': 'QWE'}, 'Wukong': {'priority': 'EQW'}}
        stats = self.stats()
        champions = self.tool.buildChampions(full, builds, stats, previous)
        self.assertEqual((champions['Karma']['priority'], champions['Karma']['rLevels']), ('QEW', [1, 6, 11, 16]))
        self.assertEqual((champions['Udyr']['priority'], champions['Udyr']['rLevels']), ('QEWR', []))
        self.assertEqual((champions['Yuumi']['priority'], champions['Yuumi']['rLevels']), ('QEW', [6, 11, 16]))
        self.assertEqual(champions['Wukong']['priority'], 'EQW')        # no build: previous baseline
        self.assertEqual(champions['Rengar']['priority'], 'QWE')
        self.assertEqual((champions['Wukong']['id'], champions['Wukong']['championId']), ('MonkeyKing', 62))
        self.assertIsNone(champions['Yuumi']['spells']['W']['cooldown'])
        for slot in 'QWE':                                              # under 2 s: nothing to time
            self.assertIsNone(champions['Rengar']['spells'][slot]['cooldown'])
        self.assertEqual(champions['Rengar']['spells']['R']['cooldown'], [110, 90, 70])
        self.assertEqual(champions['Karma']['spells']['E']['cooldown'], [10, 9.5, 9, 8.5, 8])
        self.assertEqual((stats['priority_masteries'], stats['priority_order'], stats['priority_previous'],
                          stats['priority_default']), (2, 1, 2, 0))
        self.assertEqual((stats['nullCooldowns'], len(stats['short'])), (4, 3))
        self.assertEqual(stats['rLevelsMismatch'], [])

    def test_ability_cooldown_from_client_data(self):
        def client(cd, ammo=None, recharge=None):
            spell = {'cooldownTime': [cd[0]] + cd + [cd[-1]]}               # rank-0 entry first
            if ammo is not None:
                spell['mMaxAmmo'] = ammo
            if recharge is not None:
                spell['mAmmoRechargeTime'] = [recharge[0]] + recharge + [recharge[-1]]
            return spell

        cases = [
            # (Data Dragon spell, client spell, expected, stats list it lands in)
            ('Vi E charges', {'cooldown': [1] * 5, 'maxammo': '2', 'maxrank': 5},
             client([1.0] * 5, [2] * 7, [12.0, 11.0, 10.0, 9.0, 8.0]), [12, 11, 10, 9, 8], 'charges'),
            ('Caitlyn W charges', {'cooldown': [0.5] * 5, 'maxammo': '2', 'maxrank': 5},
             client([0.5] * 5, [2, 3, 3, 4, 4, 5, 5], [26.0, 22.0, 18.0, 14.0, 10.0]),
             [26, 22, 18, 14, 10], 'charges'),
            ('scalar ammo', {'cooldown': [1] * 5, 'maxammo': '2', 'maxrank': 5},
             client([1.0] * 5, 2, [12.0] * 5), [12] * 5, 'charges'),
            ('Karthus Q charges under 2 s', {'cooldown': [0] * 5, 'maxammo': '2', 'maxrank': 5},
             client([0.0] * 5, [2] * 7, [1.0] * 5), None, 'short'),            # listed as too short only
            ('Rengar Q one charge', {'cooldown': [0.25] * 5, 'maxammo': '1', 'maxrank': 5},
             client([0.25] * 5, [1] * 7, [6.0, 5.5, 5.0, 4.5, 4.0]), [6, 5.5, 5, 4.5, 4], 'recharge'),
            ('Rakan E recharge without a charge count', {'cooldown': [0] * 5, 'maxammo': '-1', 'maxrank': 5},
             client([0.0] * 5, None, [20.0, 18.0, 16.0, 14.0, 12.0]), [20, 18, 16, 14, 12], 'recharge'),
            ('recharge as a single number', {'cooldown': [1] * 5, 'maxammo': '2', 'maxrank': 5},
             dict(client([1.0] * 5, 2), mAmmoRechargeTime=15.0), [15] * 5, 'charges'),
            ('LeBlanc R recast window', {'cooldown': [45, 35, 25], 'maxammo': '2', 'maxrank': 3},
             client([45.0, 35.0, 25.0], [2] * 7, [5.0] * 3), [45, 35, 25], None),
            ('Jinx Q toggle', {'cooldown': [0.9] * 5, 'maxammo': '-1', 'maxrank': 5},
             client([0.9] * 5), None, 'short'),
            ('Ezreal Q plain', {'cooldown': [5.5, 5.25, 5, 4.75, 4.5], 'maxammo': '-1', 'maxrank': 5},
             None, [5.5, 5.25, 5, 4.75, 4.5], None),
        ]
        for name, dd, clientSpell, expected, bucket in cases:
            stats = self.stats()
            with self.subTest(name):
                self.assertEqual(self.tool.abilityCooldown(dd, clientSpell, dd['maxrank'], stats, name), expected)
                buckets = bucket if isinstance(bucket, tuple) else (bucket,)
                for key in ('charges', 'recharge', 'short'):
                    self.assertEqual(any(name in line for line in stats[key]), key in buckets, key)
        self.assertFalse(self.tool.needsClientCheck({'cooldown': [5.5, 4.5], 'maxammo': '-1'}))

    def test_genuine_short_cooldowns_kept(self):
        def spell(cd, maxrank=5):
            return {'cooldown': cd, 'maxrank': maxrank, 'maxammo': '-1'}

        full = {'data': {
            'Akali': {'key': '84', 'name': 'Akali', 'spells': [spell([1.5] * 5), spell([20] * 5),
                                                               spell([16] * 5), spell([100, 80, 60], 3)]},
            'Cassiopeia': {'key': '69', 'name': 'Cassiopeia', 'spells': [spell([3.5] * 5), spell([24] * 5),
                                                                         spell([0.75] * 5), spell([120] * 3, 3)]},
            'Aphelios': {'key': '523', 'name': 'Aphelios', 'spells': [spell([9] * 6, 6), spell([0.8] * 6, 6),
                                                                      spell([0] * 6, 6), spell([120] * 3, 3)]},
            'Jinx': {'key': '222', 'name': 'Jinx', 'spells': [spell([0.9] * 5), spell([8] * 5),
                                                              spell([24] * 5), spell([90] * 3, 3)]},
        }}
        stats = self.stats()
        champions = self.tool.buildChampions(full, {}, stats)
        self.assertEqual(champions['Akali']['spells']['Q']['cooldown'], [1.5] * 5)
        self.assertEqual(champions['Cassiopeia']['spells']['E']['cooldown'], [0.75] * 5)
        self.assertEqual(champions['Aphelios']['spells']['W']['cooldown'], [0.8] * 6)
        self.assertIsNone(champions['Jinx']['spells']['Q']['cooldown'])            # toggle: not listed
        self.assertEqual(len(stats['genuineShort']), 3)
        self.assertEqual(stats['short'], ['Jinx Q [0.9, 0.9, 0.9, 0.9, 0.9]'])
        self.assertEqual(self.tool.GENUINE_SHORT, {('Akali', 'Q'), ('Cassiopeia', 'E'), ('Aphelios', 'W')})
        self.assertTrue(self.tool.needsClientCheck({'cooldown': [30] * 5, 'maxammo': '2'}))
        self.assertTrue(self.tool.needsClientCheck({'cooldown': [1.5] * 5, 'maxammo': '-1'}))

    def test_manual_abilities(self):
        def champions():
            return {'Kalista': {'maxRank': {'Q': 5, 'W': 5, 'E': 5, 'R': 3},
                                'spells': {s: {'cooldown': None} for s in 'QWER'}}}
        built = champions()
        self.assertEqual(self.tool.applyManualAbilities(built, {'Kalista': {'E': [10, 9.5, 9.0, 8.5, 8]}}), [])
        self.assertEqual(built['Kalista']['spells']['E']['cooldown'], [10, 9.5, 9, 8.5, 8])
        self.assertIsNone(built['Kalista']['spells']['Q']['cooldown'])
        # a value the patch data did give is still replaced, and reported
        built['Kalista']['spells']['W']['cooldown'] = [90, 80, 70, 60, 50]
        self.assertEqual(self.tool.applyManualAbilities(built, {'Kalista': {'W': [1, 2, 3, 4, 5]}}),
                         ['Kalista W: [90, 80, 70, 60, 50] -> [1, 2, 3, 4, 5]'])
        for bad in ({'Nobody': {'E': [1, 2, 3, 4, 5]}},
                    {'Kalista': {'P': [1, 2, 3, 4, 5]}},
                    {'Kalista': {'E': [10, 9, 8]}},                         # 3 ranks, E has 5
                    {'Kalista': {'R': [120, 0, 80]}},
                    {'Kalista': {'E': '10'}}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.tool.applyManualAbilities(champions(), bad)

    def test_repo_manual_file_fits_the_baseline(self):
        # every hand-entered ability names a bundled champion and slot with one value per rank
        # and the baseline carries exactly those values
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'tools', 'ledger_manual.json'), encoding='utf-8') as f:
            manual = json.load(f)
        with open(os.path.join(root, 'app', 'resource', 'ledger', 'ledger_baseline.json'), encoding='utf-8') as f:
            baseline = json.load(f)
        self.assertTrue(manual['abilities'])
        for name, slots in manual['abilities'].items():
            for slot, values in slots.items():
                with self.subTest(champion=name, slot=slot):
                    self.assertIn(f'{name} {slot}', manual['sources'])
                    self.assertEqual(baseline['champions'][name]['spells'][slot]['cooldown'], values)
        self.assertEqual(baseline['sources'].get('manual'), 'ledger_manual.json')

    def fakeGet(self, failing=()):
        dd = 'https://ddragon.leagueoflegends.com/cdn/16.23.1/data/en_US'
        full = {'data': {
            'Ahri': {'key': '103', 'name': 'Ahri', 'spells': [{'cooldown': [7] * 5, 'maxrank': 5}] * 3 + [
                {'cooldown': [140, 120, 100], 'maxrank': 3}]},
            'Ezreal': {'key': '81', 'name': 'Ezreal', 'spells': [{'cooldown': [5] * 5, 'maxrank': 5}] * 3 + [
                {'cooldown': [120, 105, 90], 'maxrank': 3}]}}}
        responses = {
            f'{dd}/championFull.json': full,
            f'{dd}/summoner.json': {'data': {}},
            f'{dd}/item.json': {'data': {}},
            'https://raw.communitydragon.org/16.23/content-metadata.json': {'version': '16.23.1+x'},
            'https://raw.communitydragon.org/16.23/game/items.cdtb.bin.json': {},
            'https://raw.communitydragon.org/16.23/game/shared.cdtb.bin.json': {},
            'https://lol-api-champion.op.gg/api/global/champions/ranked?tier=emerald_plus': {'data': [
                {'id': 103, 'positions': [{'name': 'MID', 'stats': {'play': 9}}]},
                {'id': 81, 'positions': [{'name': 'ADC', 'stats': {'play': 9}}]}]},
        }

        def get(url):
            if any(f'/ranked/{cid}/' in url for cid in failing):
                raise OSError('offline')
            if '/champions/ranked/' in url:
                return {'data': {'skill_masteries': [{'ids': ['E', 'W', 'Q']}]}, 'meta': {'version': '16.23'}}
            return responses[url]
        return get

    def test_main_keeps_previous_priority_and_fails_on_failures(self):
        tmp = tempfile.mkdtemp(prefix='ledger-tool-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        out = os.path.join(tmp, 'ledger_baseline.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump({'champions': {'Ezreal': {'priority': 'QEW'}}}, f)
        with mock.patch.object(self.tool, 'get', self.fakeGet(failing=(81,))), \
                mock.patch.object(self.tool.time, 'sleep'), \
                mock.patch('sys.stdout', new=io.StringIO()), mock.patch('sys.stderr', new=io.StringIO()) as err:
            self.assertEqual(self.tool.main(['--version', '16.23.1', '--out', out]), 1)
            self.assertIn('1 OP.GG builds failed', err.getvalue())
            with open(out, encoding='utf-8') as f:
                db = json.load(f)
            self.assertEqual(db['champions']['Ezreal']['priority'], 'QEW')     # kept from the file
            self.assertEqual(db['champions']['Ahri']['priority'], 'EWQ')
            self.assertEqual(db['sources']['communitydragon']['version'], '16.23.1+x')
            self.assertEqual(os.listdir(tmp), ['ledger_baseline.json'])           # no .tmp left
            self.assertEqual(self.tool.main(['--version', '16.23.1', '--out', out, '--allow-failures']), 0)
        with mock.patch.object(self.tool, 'get', self.fakeGet()), mock.patch.object(self.tool.time, 'sleep'), \
                mock.patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(self.tool.main(['--version', '16.23.1', '--out', out]), 0)


class PriorityFromBuildTest(unittest.TestCase):
    def test_shapes(self):
        parsed = {'data': {'championSkills': {'masteries': ['Q', 'E', 'W']}}, 'version': '16.23'}
        raw = {'data': {'skill_masteries': [{'ids': ['Q', 'E', 'W', 'R']}]}, 'meta': {'version': '16.23'}}
        self.assertEqual(ledger_data.priorityFromBuild(parsed), ('QEW', '16.23'))
        self.assertEqual(ledger_data.priorityFromBuild(raw), ('QEWR', '16.23'))
        self.assertEqual(ledger_data.priorityFromBuild({'data': {}, 'version': 'x'}), (None, None))
        self.assertEqual(ledger_data.priorityFromBuild(None), (None, None))
        self.assertEqual(ledger_data.patchOf('16.23.1'), '16.23')


if __name__ == '__main__':
    unittest.main()
