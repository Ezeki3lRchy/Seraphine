"""Reveal hidden champ-select teammates — pure merge logic, stdlib only.

In ranked champ select Riot marks teammates ``nameVisibilityType == 'HIDDEN'``
in ``/lol-champ-select/v1/session`` ``myTeam`` (``summonerId`` 0, ``puuid``
usually empty). The Riot Client's own chat API still lists every member of the
team chat room with a full puuid (see ``connector.getChampSelectChatParticipants``).
This module merges that participant list back into ``myTeam``:

* visible slots are kept as they are (shallow-copied, input never mutated);
* a hidden slot whose ``puuid`` matches a participant is revealed IN PLACE
  (keeps ``cellId`` / ``assignedPosition`` / ``championId``);
* remaining participants become SYNTHESIZED slots (``cellId`` 100+, no
  champion, no position), capped by the number of unmatched hidden slots so
  the merged list can never exceed the team size (the panel drops lists > 5);
* BOT slots are never touched.

Revealed items carry ``revealed=True`` (and ``synthesized=True`` for the
made-up ones) and ``nameVisibilityType='VISIBLE'`` so the per-summoner parsers
in tools.py stop returning ``None`` for them.
"""

SYNTH_CELL_BASE = 100


def isHiddenSlot(item):
    """A champ-select ``myTeam`` entry Riot refuses to name.

    ``HIDDEN`` visibility, or a zero/missing ``summonerId`` that is not a BOT.
    Items this module already revealed are never hidden again.
    """
    if not isinstance(item, dict):
        return False
    if item.get('playerType') == 'BOT':
        return False
    if item.get('revealed'):
        return False
    if item.get('nameVisibilityType') == 'HIDDEN':
        return True
    return not item.get('summonerId')


def _revealSlot(slot, participant):
    item = dict(slot)
    item['puuid'] = participant['puuid']
    item['summonerId'] = slot.get('summonerId') or 0
    item['nameVisibilityType'] = 'VISIBLE'
    item['gameName'] = participant.get('game_name') or item.get('gameName') or ''
    item['tagLine'] = participant.get('game_tag') or item.get('tagLine') or ''
    item['revealed'] = True
    item['synthesized'] = False
    return item


def _synthSlot(participant, index):
    return {
        'puuid': participant['puuid'],
        'summonerId': 0,
        'championId': 0,
        'cellId': SYNTH_CELL_BASE + index,
        'selectedPosition': None,
        'assignedPosition': '',
        'nameVisibilityType': 'VISIBLE',
        'gameName': participant.get('game_name') or '',
        'tagLine': participant.get('game_tag') or '',
        'revealed': True,
        'synthesized': True,
    }


def mergeHiddenTeammates(myTeam, participants, currentPuuid):
    """Merge Riot Client chat participants into champ-select ``myTeam``.

    @param myTeam: ``session['myTeam']`` list of dicts
    @param participants: ``connector.getChampSelectChatParticipants()`` result
        (dicts with at least ``puuid``; ``game_name`` / ``game_tag`` optional)
    @param currentPuuid: the local player's puuid (never revealed as a teammate)
    @return: a NEW list; same length as ``myTeam`` plus any synthesized slots.
        With no hidden slot or no participants it is an equal copy of the input.
    """
    team = [dict(it) if isinstance(it, dict) else it for it in (myTeam or [])]

    hiddenIdx = [i for i, it in enumerate(team) if isHiddenSlot(it)]
    if not hiddenIdx or not participants:
        return team

    visiblePuuids = {
        it.get('puuid') for i, it in enumerate(team)
        if i not in hiddenIdx and isinstance(it, dict) and it.get('puuid')}

    # candidates: ordered, de-duplicated, not already visible, not myself
    pending = []
    seen = set()
    for p in participants:
        if not isinstance(p, dict):
            continue
        puuid = p.get('puuid')
        if not puuid or puuid in seen or puuid in visiblePuuids or puuid == currentPuuid:
            continue
        seen.add(puuid)
        pending.append(p)

    # pass 1: pair hidden slots with participants by puuid (keeps the slot)
    unmatched = []
    for i in hiddenIdx:
        slotPuuid = team[i].get('puuid')
        match = None
        if slotPuuid:
            match = next((p for p in pending if p['puuid'] == slotPuuid), None)
        if match is None:
            unmatched.append(i)
            continue
        pending.remove(match)
        team[i] = _revealSlot(team[i], match)

    # pass 2: synthesize slots for the rest, never more than the hidden slots left
    for index, p in enumerate(pending[:len(unmatched)]):
        team.append(_synthSlot(p, index))

    return team


def revealStats(merged):
    """``(visible, revealed, synthesized, hiddenLeft)`` counts for the log line."""
    visible = revealed = synthesized = hiddenLeft = 0
    for it in merged:
        if not isinstance(it, dict):
            continue
        if it.get('synthesized'):
            synthesized += 1
        elif it.get('revealed'):
            revealed += 1
        elif isHiddenSlot(it):
            hiddenLeft += 1
        else:
            visible += 1
    return visible, revealed, synthesized, hiddenLeft
