"""
Saved players: a short local list of players the user wants to open again with one click.

    saved_players.json (in the same folder as config.json):
    {"version": 1, "players": [{"puuid": ..., "name": "gameName#tagLine", "icon": 4567,
                                "note": "duo", "added": 1730000000, "rank": ["GOLD", "II"],
                                "unreachable": false}, ...]}

- Keyed on puuid (it survives renames); list order is the display order, newest saved first.
- `name`, `icon` and `rank` are a display cache, refreshed whenever the player's Career
  page loads. `rank` is the Solo/Duo [tier, division], [] for unranked, or None while it is
  not known (saved from Search, or the rank request failed); `unreachable` is set when the
  current client could not find the player (another server, or a deleted account).
- At most MAX_PLAYERS entries.
Pure data module: no Qt, no view layer.
"""
import json
import os
import time

from app.common.platform import config_dir

MAX_PLAYERS = 100
MAX_NOTE = 24

# Tests point this at another path
storePath = os.path.join(config_dir(), 'saved_players.json')

_data = None
_dataPath = None


def _load():
    global _data, _dataPath
    if _data is not None and _dataPath == storePath:
        return _data

    data = None
    exists = os.path.exists(storePath)
    if exists:
        # an OSError (locked, no permission) is raised: the file may be fine
        with open(storePath, encoding='utf-8') as f:
            try:
                data = json.load(f)
            except ValueError:
                pass
    if not isinstance(data, dict) or not isinstance(data.get('players'), list):
        if exists:
            # Corrupt file: keep it under a new backup name and start empty
            os.replace(storePath, f"{storePath}.{int(time.time())}.bak")
        data = {'version': 1, 'players': []}
    data['players'] = [p for p in data['players']
                       if isinstance(p, dict) and isinstance(p.get('puuid'), str)]

    _data, _dataPath = data, storePath
    return data


def _save(data):
    os.makedirs(os.path.dirname(storePath), exist_ok=True)
    tmp = storePath + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, storePath)


def _find(puuid):
    for entry in _load()['players']:
        if entry['puuid'] == puuid:
            return entry
    return None


def entries():
    """All saved players in display order (copies; edit through the functions below)"""
    return [dict(entry) for entry in _load()['players']]


def count():
    return len(_load()['players'])


def isSaved(puuid):
    return _find(puuid) is not None


def isFull():
    return count() >= MAX_PLAYERS


def add(puuid, name, icon, rank=None, now=None):
    """Save a player at the top of the list. Returns False when the list is full;
    a player who is already saved stays where they are (returns True)."""
    if isSaved(puuid):
        return True
    if isFull():
        return False

    data = _load()
    data['players'].insert(0, {
        'puuid': puuid,
        'name': name,
        'icon': icon,
        'note': '',
        'added': int(now if now is not None else time.time()),
        'rank': None if rank is None else list(rank),
        'unreachable': False,
    })
    _save(data)
    return True


def remove(puuid):
    """Returns True when the player was saved"""
    data = _load()
    players = [entry for entry in data['players'] if entry['puuid'] != puuid]
    if len(players) == len(data['players']):
        return False
    data['players'] = players
    _save(data)
    return True


def setNote(puuid, note):
    """Store a trimmed note (at most MAX_NOTE characters). Returns True when it changed."""
    entry = _find(puuid)
    note = note.strip()[:MAX_NOTE]
    if entry is None or entry.get('note', '') == note:
        return False
    entry['note'] = note
    _save(_load())
    return True


def reorder(puuids):
    """Put the saved players in the order of `puuids`. Saved players missing from it keep
    their relative order after the given ones; unknown puuids are ignored."""
    data = _load()
    byPuuid = {entry['puuid']: entry for entry in data['players']}
    ordered = [byPuuid.pop(puuid) for puuid in puuids if puuid in byPuuid]
    ordered += [entry for entry in data['players'] if entry['puuid'] in byPuuid]
    if [e['puuid'] for e in ordered] == [e['puuid'] for e in data['players']]:
        return False
    data['players'] = ordered
    _save(data)
    return True


def refresh(puuid, name, icon, rank):
    """A saved player was loaded: update the display cache and clear `unreachable`.
    rank None (not known this time) keeps the stored rank. Returns True when anything
    changed (nothing happens for a player who is not saved)."""
    entry = _find(puuid)
    if entry is None:
        return False

    new = {'name': name, 'icon': icon, 'unreachable': False}
    if rank is not None:
        new['rank'] = list(rank)
    if all(entry.get(key) == value for key, value in new.items()):
        return False
    entry.update(new)
    _save(_load())
    return True


def markUnreachable(puuid):
    """The current client could not load a saved player. Returns True when it changed."""
    entry = _find(puuid)
    if entry is None or entry.get('unreachable'):
        return False
    entry['unreachable'] = True
    _save(_load())
    return True


def matches(entry, text):
    """Filter: the text appears in the name or the note (case-insensitive)"""
    text = text.strip().lower()
    return not text or text in entry.get('name', '').lower() \
        or text in entry.get('note', '').lower()
