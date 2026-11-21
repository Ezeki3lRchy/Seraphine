"""Build app/resource/ledger/ledger_baseline.json, the cooldown ledger's bundled database.

Sources (read-only GETs; used by this tool only, never by the app at runtime):
- Riot Data Dragon: champion ability cooldowns per rank and max ranks (championFull.json),
  summoner spells (summoner.json), items (item.json: which items have an active, and ability /
  ultimate / summoner spell haste read from the description text).
- CommunityDragon's mirror of the game client's data for the same patch
  (raw.communitydragon.org/<X.Y>/game/...): item active cooldowns (`mDataValues` "Cooldown" in
  items.cdtb.bin.json) and summoner spell cooldowns incl. the Unleashed spells (`cooldownTime` in
  shared.cdtb.bin.json; a spell with charges, `mMaxAmmo` > 1, gets null), and, for abilities
  with ammo or a cooldown under MIN_COOLDOWN, the champion's spell data
  (game/data/characters/<id>/<id>.bin.json) to tell charges and recharge times from cast lockouts.
- OP.GG champion builds (the GET endpoint app/lol/opgg.py uses): the most played skill max
  priority (`skill_masteries`). OP.GG has no cooldown fields, so cooldowns come from Riot.

A value no source gives is written as null (the ledger cell then does nothing). `--manual` takes a
JSON file {"summonerSpells": {key: seconds}, "items": {itemId: seconds}} of values entered by hand
from another source; they are applied last and the file name is recorded under `sources`.

When an OP.GG build fails, the champion keeps the priority of the existing output file (if any);
the tool then exits 1 unless `--allow-failures` is given. The output is written atomically, sorted
keys, so the same inputs give the same bytes. Stdlib only; imports nothing from app/.

    python tools/build_ledger_baseline.py [--version 16.23.1] [--region global] [--tier emerald_plus]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

DDRAGON = 'https://ddragon.leagueoflegends.com'
CDRAGON = 'https://raw.communitydragon.org'
OPGG = 'https://lol-api-champion.op.gg'
HEADERS = {'User-Agent': 'Mozilla/5.0'}
OPGG_DELAY = 0.5

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'app', 'resource', 'ledger', 'ledger_baseline.json')

SLOTS = ('Q', 'W', 'E', 'R')
# Levels at which R gains a rank, by R's max rank. R at level 1 is the free starting rank of
# champions such as Karma, Elise, Nidalee (max rank 4) and Jayce (max rank 1)
R_LEVELS = {3: [6, 11, 16], 4: [1, 6, 11, 16], 1: [1]}
# Item tags whose actives get no ledger cell, and active texts of one-off uses
NO_CELL_TAGS = {'Trinket', 'Consumable', 'Vision'}
NO_CELL_ACTIVE = re.compile(r'Consume|Single use', re.I)
# Spells the live API reports after an in-place upgrade -> the spell they upgrade from (its id is
# used for the icon: the client lists no id of their own)
UNLEASHED = {'S12_SummonerTeleportUpgrade': 'SummonerTeleport',
             'S5_SummonerSmitePlayerGanker': 'SummonerSmite'}
# Ability cooldowns whose longest rank is below this are toggles, recast lockouts or spammable
# spells: nothing to time
MIN_COOLDOWN = 2.0
# Genuine short cooldowns (not a toggle, lockout or resource gate) that keep their real value: the
# owner wants every button clickable, with no threshold. (Data Dragon id, slot)
GENUINE_SHORT = {('Akali', 'Q'), ('Cassiopeia', 'E'), ('Aphelios', 'W')}
# OP.GG position names
OPGG_POSITIONS = ('TOP', 'JUNGLE', 'MID', 'ADC', 'SUPPORT')


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=180) as res:
        return json.loads(res.read())


def patchOf(version):
    return '.'.join(str(version).split('.')[:2])


def _number(v):
    return int(v) if float(v).is_integer() else float(v)


def cooldowns(values, stats=None, name='', keepShort=False):
    """Per-rank list -> list of numbers, or None when it gives nothing to time (all zero, or every
    rank below MIN_COOLDOWN: a toggle, a recast lockout or a resource-gated spell; counted in
    stats['short']). `keepShort` (GENUINE_SHORT) keeps a short list, counted in stats['genuineShort']"""
    if not values or not any(values):
        return None
    if max(values) < MIN_COOLDOWN and keepShort:
        if stats is not None:
            stats['genuineShort'].append(f'{name} {list(values)}')
    elif max(values) < MIN_COOLDOWN:
        if stats is not None:
            stats['short'].append(f'{name} {list(values)}')
        return None
    return [_number(round(v, 3)) for v in values]


def needsClientCheck(spell):
    """Data Dragon spells whose `cooldown` may be a lockout between casts rather than the time to
    time: the spell has ammo (charges, or one charge that recharges), or its cooldown is short"""
    cd = spell.get('cooldown') or []
    return str(spell.get('maxammo')) not in ('None', '-1', '0') or bool(cd and max(cd) < MIN_COOLDOWN)


def abilityCooldown(spell, clientSpell, maxRank, stats, name, keepShort=False):
    """
    A basic ability's per-rank cooldown. Data Dragon's `cooldown`, checked against the client's
    spell (`mSpell` of game/data/characters/<id>/<id>.bin.json; its per-rank lists start with a
    rank-0 entry) when needsClientCheck:
    - several charges that recharge slower than the cast lockout (Vi E, Caitlyn W): charge-based,
      None (like Smite)
    - one charge that recharges slower than the lockout (Rengar Q W E): the recharge time
    - otherwise (LeBlanc R, Warwick W: a recast window of 2): the cooldown as listed
    """
    values = spell.get('cooldown')
    if clientSpell is None and needsClientCheck(spell):
        stats['clientMissing'].append(name)
    elif clientSpell is not None:
        ranks = slice(1, maxRank + 1)
        raw = clientSpell.get('mMaxAmmo')
        ammo = raw[ranks] if isinstance(raw, list) else [raw or 0]
        recharge = (clientSpell.get('mAmmoRechargeTime') or [])[ranks]
        lockout = (clientSpell.get('cooldownTime') or [])[ranks] or values or [0]
        if ammo and recharge and max(ammo) >= 1 and max(recharge) > max(lockout):
            if max(ammo) > 1:
                stats['charges'].append(name)
                return None
            stats['recharge'].append(f'{name} {recharge}')
            values = recharge
    return cooldowns(values, stats, name, keepShort)


def priorityFromBuild(data):
    """OP.GG build data -> ('QEW', source) from skill_masteries, else from the most played
    skill order (abilities by points in the first 15 levels), else (None, None)"""
    for m in data.get('skill_masteries') or []:
        ids = [i for i in m.get('ids') or [] if i in SLOTS]
        if len(ids) >= 3:
            return ''.join(ids), 'masteries'
    for s in data.get('skills') or []:
        order = [i for i in s.get('order') or [] if i in ('Q', 'W', 'E')]
        if order:
            ranked = sorted('QWE', key=lambda a: (-order.count(a), order.index(a) if a in order else 99))
            return ''.join(ranked), 'order'
    return None, None


def rLevelsOrder(data):
    """Levels at which the most played OP.GG order takes R (a 15-level order shows 2..15 only)"""
    for m in data.get('skill_masteries') or []:
        for b in m.get('builds') or []:
            return [i + 1 for i, a in enumerate(b.get('order') or []) if a == 'R']
    return None


def buildChampions(full, builds, stats, previous=None, clientSpell=None):
    """`builds`: {championId: OP.GG build data}; a champion without one keeps its priority from
    `previous` (the existing baseline's champions), else gets 'QWE'. `clientSpell(ddId, spellId)`
    returns the client's spell data (or None) for the abilities needsClientCheck flags"""
    previous = previous or {}
    clientSpell = clientSpell or (lambda ddId, spellId: None)
    champions = {}
    for ddId, champ in sorted(full['data'].items()):
        spells = champ['spells']
        maxRank = {slot: s['maxrank'] for slot, s in zip(SLOTS, spells)}
        build = builds.get(int(champ['key'])) or {}
        priority, source = priorityFromBuild(build)
        if priority is None and (previous.get(champ['name']) or {}).get('priority'):
            priority, source = previous[champ['name']]['priority'], 'previous'
        if priority is None:
            priority, source = 'QWE', 'default'
        if 'R' in priority:
            rLevels = []          # R is a basic ability (Udyr)
        else:
            rLevels = R_LEVELS.get(maxRank['R'], R_LEVELS[3])
            fromOrder = rLevelsOrder(build)
            if fromOrder is not None and fromOrder != [lv for lv in rLevels if 1 < lv <= 15]:
                stats['rLevelsMismatch'].append(f"{champ['name']} {rLevels} vs OP.GG {fromOrder}")
        stats['priority_' + source] += 1
        entry = {
            'championId': int(champ['key']),
            'id': ddId,
            'spells': {slot: {'cooldown': abilityCooldown(
                s, clientSpell(ddId, s.get('id')) if needsClientCheck(s) else None, s['maxrank'], stats,
                f"{champ['name']} {slot}", (ddId, slot) in GENUINE_SHORT)} for slot, s in zip(SLOTS, spells)},
            'maxRank': maxRank,
            'rLevels': rLevels,
            'priority': priority,
        }
        stats['nullCooldowns'] += sum(1 for s in entry['spells'].values() if s['cooldown'] is None)
        champions[champ['name']] = entry
    return champions


def clientSpellCooldown(shared, key):
    """(seconds | None, found) from the client's spell data: None for a spell with charges"""
    spell = (shared.get(f'Shared/Spells/{key}') or {}).get('mSpell')
    if not spell:
        return None, False
    ammo = spell.get('mMaxAmmo') or 0
    if (ammo[0] if isinstance(ammo, list) else ammo) > 1:
        return None, True
    cd = next((v for v in spell.get('cooldownTime') or [] if v), None)
    return (_number(cd) if cd else None), True


def buildSummonerSpells(summoner, shared=None, stats=None):
    """CLASSIC summoner spells + the Unleashed ones: the client's cooldown when it lists the spell,
    else Data Dragon's"""
    shared = shared or {}
    spells = {}
    for key, s in summoner['data'].items():
        if 'CLASSIC' not in (s.get('modes') or []):
            continue
        cd, found = clientSpellCooldown(shared, key)
        if not found:
            cd = (s.get('cooldown') or [None])[0] or None
        elif stats is not None and cd is not None and cd != (s.get('cooldown') or [None])[0]:
            stats['spellMismatch'].append(f'{key}: client {cd}, Data Dragon {s.get("cooldown")}')
        spells[key] = {'id': int(s['key']), 'cooldown': cd}
    for key, base in UNLEASHED.items():
        if base in spells:
            cd, _ = clientSpellCooldown(shared, key)
            spells[key] = {'id': spells[base]['id'], 'cooldown': cd}
    return spells


_STATS = re.compile(r'<stats>(.*?)</stats>', re.S)
_HASTE = re.compile(r'<attention>(\d+)</attention>\s*Ability Haste')
_ULT_HASTE = re.compile(r'(\d+)\s*Ultimate Ability Haste')
_SPELL_HASTE = re.compile(r'(\d+)\s*Summoner Spell Haste')
_ACTIVE = re.compile(r'<active>(.*?)(?:<br>|$)', re.S)


def clientItemCooldown(clientItems, itemId):
    """The client's item `mDataValues` "Cooldown" in seconds, or None"""
    item = clientItems.get(f'Items/{itemId}') or {}
    for value in item.get('mDataValues') or []:
        if str(value.get('mName') or '').lower() == 'cooldown' and (value.get('mValue') or 0) > 0:
            return _number(value['mValue'])
    return None


def buildItems(item, clientItems=None):
    clientItems = clientItems or {}
    items = {}
    for itemId, it in item['data'].items():
        if not (it.get('maps') or {}).get('11'):
            continue
        desc = it.get('description') or ''
        stats = _STATS.search(desc)
        haste = sum(int(n) for n in _HASTE.findall(stats.group(1) if stats else ''))
        ultHaste = sum(int(n) for n in _ULT_HASTE.findall(desc))
        spellHaste = sum(int(n) for n in _SPELL_HASTE.findall(desc))
        hasActive = ('<active>' in desc and not NO_CELL_TAGS & set(it.get('tags') or [])
                     and not any(NO_CELL_ACTIVE.search(a) for a in _ACTIVE.findall(desc)))
        if not (hasActive or haste or ultHaste or spellHaste):
            continue
        entry = {'active': clientItemCooldown(clientItems, itemId) if hasActive else None,
                 'haste': haste, 'ultHaste': ultHaste, 'spellHaste': spellHaste}
        if hasActive:
            entry['hasActive'] = True
        items[itemId] = entry
    return items


def mainPositions(tierList):
    """OP.GG tier list -> {championId: the position with the most games}"""
    res = {}
    for row in tierList.get('data') or []:
        positions = [p for p in row.get('positions') or [] if p.get('name') in OPGG_POSITIONS]
        if positions:
            best = max(positions, key=lambda p: (p.get('stats') or {}).get('play') or 0)
            res[row['id']] = best['name']
    return res


def readPrevious(path):
    try:
        with open(path, encoding='utf-8') as f:
            return (json.load(f).get('champions') or {})
    except (OSError, ValueError, AttributeError):
        return {}


def writeAtomic(path, db):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(db, f, ensure_ascii=False, sort_keys=True, indent=1)
            f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--version', help='Data Dragon version (default: the latest)')
    ap.add_argument('--region', default='global')
    ap.add_argument('--tier', default='emerald_plus')
    ap.add_argument('--manual', help='JSON file of hand-entered summoner spell / item active cooldowns')
    ap.add_argument('--allow-failures', action='store_true',
                    help='exit 0 even when some OP.GG builds failed (they keep the previous priority)')
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args(argv)

    version = args.version or get(f'{DDRAGON}/api/versions.json')[0]
    patch = patchOf(version)
    base = f'{DDRAGON}/cdn/{version}/data/en_US'
    full = get(f'{base}/championFull.json')
    summoner = get(f'{base}/summoner.json')
    item = get(f'{base}/item.json')

    cdBase = f'{CDRAGON}/{patch}'
    cdUrls = {'metadata': f'{cdBase}/content-metadata.json',
              'items': f'{cdBase}/game/items.cdtb.bin.json',
              'spells': f'{cdBase}/game/shared.cdtb.bin.json',
              'characters': f'{cdBase}/game/data/characters/<id>/<id>.bin.json'}
    cdVersion = get(cdUrls['metadata']).get('version')
    clientItems = get(cdUrls['items'])
    shared = get(cdUrls['spells'])

    characterBins = {}

    def clientSpell(ddId, spellId):
        if ddId not in characterBins:
            low = ddId.lower()
            try:
                characterBins[ddId] = get(f'{cdBase}/game/data/characters/{low}/{low}.bin.json')
            except Exception as e:
                print(f'client data failed: {ddId}: {type(e).__name__}', file=sys.stderr)
                characterBins[ddId] = {}
        for key, value in characterBins[ddId].items():
            if isinstance(value, dict) and isinstance(value.get('mSpell'), dict)                     and key.split('/')[-1].lower() == str(spellId).lower():
                return value['mSpell']
        return None

    tierList = get(f'{OPGG}/api/{args.region}/champions/ranked?tier={args.tier}')
    positions = mainPositions(tierList)
    builds, opggVersions, failed = {}, set(), []
    for champ in sorted(full['data'].values(), key=lambda c: int(c['key'])):
        championId = int(champ['key'])
        if championId not in positions:
            failed.append(champ['name'])
            continue
        time.sleep(OPGG_DELAY)
        try:
            raw = get(f'{OPGG}/api/{args.region}/champions/ranked/{championId}/{positions[championId]}'
                      f'?tier={args.tier}')
        except Exception as e:
            failed.append(champ['name'])
            print(f'OP.GG build failed: {champ["name"]}: {type(e).__name__}', file=sys.stderr)
            continue
        builds[championId] = raw.get('data') or {}
        opggVersions.add(patchOf((raw.get('meta') or {}).get('version')))

    stats = {'priority_masteries': 0, 'priority_order': 0, 'priority_previous': 0, 'priority_default': 0,
             'nullCooldowns': 0, 'short': [], 'genuineShort': [], 'charges': [], 'recharge': [],
             'clientMissing': [],
             'rLevelsMismatch': [], 'spellMismatch': []}
    db = {
        'schema': 1,
        'patch': patch,
        'sources': {'ddragon': version, 'opgg': sorted(opggVersions), 'region': args.region,
                    'tier': args.tier, 'communitydragon': {'version': cdVersion, **cdUrls}},
        'champions': buildChampions(full, builds, stats, readPrevious(args.out), clientSpell),
        'summonerSpells': buildSummonerSpells(summoner, shared, stats),
        'items': buildItems(item, clientItems),
    }
    if args.manual:
        with open(args.manual, encoding='utf-8') as f:
            manual = json.load(f)
        for key, cd in (manual.get('summonerSpells') or {}).items():
            db['summonerSpells'].setdefault(key, {'id': 0})['cooldown'] = cd
        for key, cd in (manual.get('items') or {}).items():
            db['items'].setdefault(str(key), {'haste': 0, 'ultHaste': 0, 'spellHaste': 0})['active'] = cd
        db['sources']['manual'] = os.path.basename(args.manual)

    writeAtomic(args.out, db)

    items = db['items'].values()
    print(f"patch {patch} (Data Dragon {version}, CommunityDragon {cdVersion}, OP.GG "
          f"{', '.join(sorted(opggVersions)) or 'none'})")
    print(f"champions: {len(db['champions'])} (Data Dragon: {len(full['data'])}); priority from masteries "
          f"{stats['priority_masteries']}, from order {stats['priority_order']}, previous baseline "
          f"{stats['priority_previous']}, default {stats['priority_default']}; OP.GG builds failed "
          f"{len(failed)}; null ability cooldowns {stats['nullCooldowns']}")
    print(f"  charge-based (null): {', '.join(stats['charges']) or 'none'}")
    print(f"  one recharging charge (recharge time used): {', '.join(stats['recharge']) or 'none'}")
    print(f"  below {MIN_COOLDOWN:g} s (null): {', '.join(stats['short']) or 'none'}")
    print(f"  below {MIN_COOLDOWN:g} s, genuine (kept, GENUINE_SHORT): {', '.join(stats['genuineShort']) or 'none'}")
    if stats['clientMissing']:
        print(f"  no client spell data (Data Dragon used): {', '.join(stats['clientMissing'])}")
    for line in stats['rLevelsMismatch']:
        print(f'  rLevels differ from the OP.GG order: {line}')
    spells = db['summonerSpells']
    print(f"summoner spells: {len(spells)}, null cooldown: "
          f"{sorted(k for k, s in spells.items() if s['cooldown'] is None)}; client vs Data Dragon "
          f"differences: {stats['spellMismatch'] or 'none'}")
    withActive = [k for k, i in db['items'].items() if i.get('hasActive')]
    print(f"items: {len(db['items'])}; with an active {len(withActive)}, of them with a cooldown "
          f"{sum(1 for k in withActive if db['items'][k]['active'] is not None)} (missing: "
          f"{sorted(k for k in withActive if db['items'][k]['active'] is None) or 'none'}); haste "
          f"{sum(1 for i in items if i['haste'])}, ultimate haste {sum(1 for i in items if i['ultHaste'])}, "
          f"summoner spell haste {sum(1 for i in items if i['spellHaste'])}")
    print(f'written {os.path.abspath(args.out)}')
    if failed and not args.allow_failures:
        print(f'{len(failed)} OP.GG builds failed (kept the previous priority): {", ".join(failed)}; '
              'rerun, or pass --allow-failures', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
