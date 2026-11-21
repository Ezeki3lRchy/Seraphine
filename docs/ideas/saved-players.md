# Saved players

**Status: the saved list is implemented** (sidebar page `app/view/saved_players_interface.py`, store `app/lol/saved_players.py`, star buttons on the Career and Search headers).
The rank shown is the Solo/Duo rank cached when the player's Career page last loaded (no extra requests); the Game info quick-save button, export, and the relationship graph below are not built.
**Decided: the feature gets its own sidebar entry ("Saved players", star icon), not a panel inside Search.**

## Problem

To look someone up again (a duo partner, a smurf suspect) the user has to retype the Riot ID in the
Search page every time. A short saved list reachable from the sidebar would make this one click.

## How it works today (grounding)

- Sidebar: `MainWindow.__initNavigation` in `app/view/main_window.py` registers Start, Career, Search,
  Game info and Auxiliary with `addSubInterface(..., NavigationItemPosition.SCROLL)`; OP.GG, Back to
  Lobby, Notice, the avatar and Settings sit at the BOTTOM. Each page needs a `setObjectName`.
- Search: `SearchInterface.onSearchButtonClicked` (`app/view/search_interface.py`) reads
  `searchLineEdit`, resolves it via `connector.getSummonerByName`, and shows the results page.
- Career: `CareerInterface.updateInterface(puuid=...)` loads a player via
  `connector.getSummonerByPuuid`. Anything can open it with `signalBus.toCareerInterface.emit(puuid)`
  (handled by `MainWindow.__switchToCareerInterface`); `toSearchInterface` does the same for a name.
- Persistence: `config.json` via `cfg` (`app/common/config.py`), plus precedent for small per-player
  JSON side files: `app/lol/rank_history.py` writes `rank_history.json` into `config_dir()`
  (`app/common/platform.py`, honours `SERAPHINE_CONFIG_DIR`, so tests stay isolated).

## Proposed UX

- A star toggle on the Career page header (next to the name/icon) and on the Search result header.
  Starred = saved; click again to remove.
- **Decided: a new sidebar entry ("Saved players", star icon) in the SCROLL group, right after
  Search** (not a panel inside Search). It is the "one click from anywhere" the idea is about. The page
  is a simple list/card grid (icon, Riot ID, note, last-known rank optional).
- Click a row: `signalBus.toCareerInterface.emit(puuid)` opens that player's career/match history.
- Row actions: remove, edit note (inline, e.g. "duo", "toxic"), drag to reorder (the app already has
  `app/components/draggable_widget.py`). Default order: most recently saved first.

## GUI sketch

Layout the user approved from a mockup.

**Saved players page (new sidebar entry)**

- Sidebar order: Home, Career, Search, **Saved players (star icon, new)**, Game info, Auxiliary. OP.GG
  and the bottom buttons are unchanged.
- Page header: title "Saved players", a count such as "5 / 100", and a filter box on the right
  ("Filter by name or note").
- One row per player: drag handle, avatar (profile icon), Riot ID (gameName#tagLine) with "Saved 3 days
  ago" under it, the note as a small tag (e.g. duo / smurf? / toxic; clicking the tag edits the note),
  current rank, and an x to remove.
- Clicking a row opens that player's Career page, the same page Search opens, loaded by puuid
  (`signalBus.toCareerInterface.emit(puuid)`), so no Riot ID typing. Opening it refreshes the cached
  name and icon. It needs the LoL client: with the client closed the list still shows but rows cannot
  open.
- An unreachable entry (account on another server, or deleted) keeps its cached name, marked "(cached
  name)", with the line "Couldn't load from this server". It is not removed automatically.
- Footer hint: "Click a row to open their career · drag to reorder · click the tag to edit the note".

```text
 Saved players                 5 / 100     [ Filter by name or note ]
 ---------------------------------------------------------------------
 ::  (icon)  Name#NA1                  [duo]          Gold II      x
             Saved 3 days ago
 ::  (icon)  Other#1234                [smurf?]       Emerald IV   x
             Saved 2 weeks ago
 ::  (icon)  Gone#0001 (cached name)   [toxic]        --           x
             Couldn't load from this server
 ---------------------------------------------------------------------
 Click a row to open their career · drag to reorder · click the tag to edit the note
```

**Career page changes**

- Header, next to the name: a star toggle button ("Saved" when starred) and, later, a "Relationships"
  button.
- Later relationship view, two columns: left an "Often plays with" table (Player, Games together,
  Win % together coloured green/red, star to save) with a caption such as "last 50 games · ranked";
  right a node-link graph with the player in the centre, edge width = games together, saved players
  highlighted, and a dashed edge between two other players who also often queue together (likely a
  premade group).

```text
 Name#NA1   [* Saved]   [Relationships]
 ---------------------------------------------------------------------
 Often plays with                  |            (C)
 last 50 games · ranked            |        .--/  |  \--.
 Player    Games  Win %   Save     |      (A)=====+=====(B)
 A#1       18     61%     *        |        \  - - - -  /
 B#2       12     42%     *        |         (D)     (E)
 D#3        7     57%     [ ]      |   centre = this player; wide edge =
 ...                               |   many games; dashed = A and B also
                                   |   queue together (likely premade)
```

## Data to store

Key on **puuid** (stable across renames). Per entry:

```json
{"puuid": "...", "name": "gameName#tagLine", "icon": 4567, "note": "duo", "added": 1730000000}
```

`name` and `icon` are a display cache only; refresh them whenever the player is loaded again (Career
`updateInterface` already has the fresh summoner) and write back if changed. Keep an `order` implied by
list position.

**Persistence recommendation:** a dedicated `saved_players.json` in `config_dir()`, loaded/saved by a
small module modelled on `app/lol/rank_history.py` (atomic write, tolerate a corrupt/missing file). Not
a `ConfigItem`: a growing list of dicts does not suit the settings UI, and a separate file avoids
bloating `config.json`.

## Edge cases

- **Renamed player:** puuid still resolves; the cached name is refreshed on next load. Show the cached
  name until then.
- **Other shard / not reachable:** the user is on NA, but a saved account may live on another region the
  current client cannot query. Opening it fails; show the cached name with a "could not load" notice
  rather than removing the entry.
- **Client not running / not logged in:** the list itself works offline (it is local); clicking an entry
  needs the LCU, so disable or show the existing "connect to the client" message.
- **Size:** soft cap (e.g. 100) with a message when reached; no pagination needed below that.
- Deleted/inactive accounts: keep the entry, let the user remove it.

## Rough implementation pointers

- New `app/lol/saved_players.py` (store: load/save/add/remove/update/reorder), unit-tested like
  `rank_history`.
- New `app/view/saved_players_interface.py`, extending the base in `app/components/seraphine_interface.py`;
  register in `app/view/main_window.py` (`__initInterface` object name + `__initNavigation`).
- Star button: `app/view/career_interface.py` (header) and `app/view/search_interface.py` (result header).
- Icon in `app/common/icons.py` / `app/resource/icons`; new strings need translations in
  `app/resource/i18n` (and the README feature line in both languages).
- Optionally a `SignalBus` signal so the list refreshes when a star is toggled elsewhere.

## Later: relationship graph

Idea: for a saved player, show who they often play with. Builds on saved players: people in the result
can be starred, and a saved player's result can be cached.

**What already exists.** The Career page already has a "Recent teammates" flyout:
`getRecentTeammates(games, puuid, details)` in `app/lol/tools.py` counts, per teammate puuid, games, wins
and losses over the loaded games and keeps the top 5. It uses `getTeammates(game, puuid)` in
`app/lol/payloads.py` (same-team and enemy participants of one full game). `CareerInterface` feeds it
`self.games` (`careerGamesNumber`, default 20, via `connector.getSummonerGamesByPuuid`) and reuses the
per-game details already fetched for scores. The game-info page's `fateFlag` (`tools.py`) is the same
idea for one previous game.

**Data needed.** The match-history list alone has no full participant list, so each game needs one
`connector.getGameDetailByGameId(gameId)` call. From each detail: the same-team participants (and
optionally `enemies`), then per other puuid: games together, wins together (skip remakes, as
`getRecentTeammates` does), last played together (`gameCreation`), and name/icon as a display cache.

**Cost and bounds.** 1 list call plus N detail calls for N games. Keep it bounded: cap N (e.g. 50-100);
cache details per `gameId` (a finished game never changes; a small JSON side file like
`rank_history.json`); run lazily (only when the user opens the view) in the background with the
`Quietly` variants (no retry, no popups; calls already go through the connector semaphore); never
during a game (same rule as the update staging), and resume or skip when a game starts.

**Display.** Start with a ranked "often plays with" list: name, games together, win rate together,
last played; click to open their Career page (`signalBus.toCareerInterface`), star button to save them.
A node-link graph view (player in the centre, edge weight = games together) comes later and reuses the
same data. Optional: also count frequent opponents from `enemies`.

## Open questions

1. Is a free-text note enough, or do you want preset tags/colours?
2. Should entries show live data (current rank, last game time) or just name and icon? Live data costs
   LCU calls per row.
3. Should the Game info page (teammates/opponents) also get a quick "save" button?
4. Any need to sync/export the list (e.g. a plain JSON export) or is per-machine fine?
5. Relationship graph: how many recent games to scan (20 like Career, or 50-100 with caching)?
6. Relationship graph: include normals and ARAM, or only ranked (solo/flex), where duos matter most?
