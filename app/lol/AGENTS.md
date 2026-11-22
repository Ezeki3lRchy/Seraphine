<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-06 | Updated: 2026-06-06 -->

# lol

## Purpose
The LoL data layer — everything that talks to the Riot client and external stat sources.
`connector.py` is the heart of the app: the LCU + SGP client used to read your own and
any player's match history (CN servers via the Tencent SGP gateway).

## Key Files
| File | Description |
|------|-------------|
| `connector.py` | **Core.** LCU connection (local `127.0.0.1:{port}` with auth token) + SGP gateway access for match history. `PastRequest` caching of past calls |
| `listener.py` | `LolProcessExistenceListener` (QThread) — watches for the LoL client process starting/stopping, one poll every 1.5 s. While the connected client (`runningPid`) is still the same running process (`clientProcess`: psutil handle of a process named like the client with a readable start time, `is_running()`), a poll does nothing and runs no tasklist / process scan. On macOS the scan is `ProcessNames`: a name is read once per pid, again while the process is under `YOUNG` (5 s) old, and all names once the last full read is `REFRESH` (60 s) old (so the first scan after a connected session reads them all); tests patch `ProcessNames.scan` and `clientProcess` |
| `champions.py` | `ChampionAlias` — champion id/name/alias lookup |
| `aram.py` | `AramBuff` — ARAM per-champion balance buffs/nerfs |
| `opgg.py` | `Opgg` — client for op.gg data (tier lists / builds) |
| `tools.py` | Async orchestrators that tie connector calls together: `parseSummonerData`, `parseGameData` / `parseGameDetailData`, `parse{Ally,EnemyChampSelect,Lobby}GameInfo`, `parseGameInfoByGameflowSession`, `parseSummonerGameInfo`, `getSummonerGamesInfoViaSGP` / `parseGamesDataFromSGP`, `attachGameScores` / `attachSummonerScores`, `gatherProgressively`, `revealHiddenAllies`, `loadGameBuildData`, the queue filters. Re-exports every name of the four modules below (`from .x import name as name`), so existing `from app.lol.tools import ...` and `tools.X` keep working. Tests rebind orchestrators on this module (`tools.parseSummonerGameInfo = ...`), so their callers must stay here |
| `formatting.py` | `SERVERS_NAME` / `SERVERS_SUBSET`, `ToolsTranslator`, `translateTier`, time formatters, `getPositionKey` / `translatePositionKey`. The data layer's only Qt (`QObject`) dependency |
| `payloads.py` | Pure parsing of client payloads, no connector or config: `getTeammates`, `getRecentChampions`, `parseRankInfo*` / `parseDetailRankInfo`, SGP teammate / name helpers, team and lobby helpers (`separateTeams`, `splitCustomLobbyTeams`, `getTeamColor`, `visibleEnemies`, `mergeQueuePage`, ...) |
| `scoring.py` | MVP / SVP scoring (`GAME_SCORE_WEIGHTS`, `opScore`, `scoreGameParticipants`, `getGameScoreFrom{LcuDetail,SgpGame}`, `lcuDetailPlayers`) and the game-info page summaries (`summarizeGames`, `playerInsights` + thresholds) |
| `champ_select.py` | Champ-select automation and client control: `ChampionSelection`, `autoPick` / `autoBan` / `autoComplete` / `autoSwap` / `autoTrade` / `autoShow`, `showOpggBuild`, `rollAndSwapBack`, `fixLCUWindowViaExe`. Reads `cfg`, emits `signalBus` |
| `opscore.py` | Ranked (420/440) 0-10 score: MLP ensemble fitted to OP.GG, ported from `ignaciomigliaro/lol-friends-ladder` (MIT). Weights in `../resource/opscore/opscore-model.json`. Needs the match-server SUMMARY (LCU detail lacks xp / plates / takedowns / teamPosition); falls back to `scoring.scoreGameParticipants`' formula |
| `ledger_model.py` | Cooldown ledger model, no Qt: `estimateRanks` (levels 1-3 one point each in the priority order, R at `rLevels` - a level-1 entry is a free rank -, other points by priority, basic rank r needs level >= 2r-1, capped by `maxRank`; an R inside the priority, as Udyr's, ranks like a basic ability), `cooldown(db, champion, cell, level, items, spells=None)` (base cooldown at the rank / (1 + item haste / 100); R adds ultimate haste, spells summoner spell haste, item actives no haste; at least rank 1 for a seen cast; `None` = the cell does nothing), `championEntry` (by name, Data Dragon id such as `MonkeyKing` = the live API's `rawChampionName`, or championId), `spellForCell` (`D` = Flash whenever carried, `F` = the other spell), `ledgerVisible` (CLASSIC + map 11 + five distinct known positions), `cellText` (Q W E R seconds left, others the return game time `mm:ss`), `LedgerTimers` (game-time timers per row key and cell, extrapolated at most 6 s past the last game time, cleared on a > 30 s backward jump; up to 3 item cells for items with an active, in fixed slots: a removed item leaves a hole (`None` in `cells()`) until the next new item fills it; a cell stays while its timer runs) |
| `ledger_data.py` | `LedgerDatabase`: `load()` reads (files only) the bundled `../resource/ledger/ledger_baseline.json` (missing / unreadable -> `LedgerDataError`), `<config_dir()>/ledger/cache.json` (ignored when older than the baseline's patch) and the user's `<config_dir()>/ledger/priorities.json`, and takes only `priority` from the last two (override > cache > baseline; a priority that does not fit the champion is ignored). `async refresh(fetchBuild, champions)` asks OP.GG (`opgg.getChampionBuild` signature) for the skill priority of champions not cached, at most 5 calls of 10 s each (plus OP.GG's tier list once in a cold session), once per champion per session; a response of another patch starts a new cache; cache written atomically; never raises except `CancelledError`, `KeyboardInterrupt`, `SystemExit`; logs type names only. MainWindow loads it at start-up (logs `ledger db: baseline <patch>, cache <patch|none>, <n> champions`; a `LedgerDataError` means no ledger panel) and runs `refresh(opgg.getChampionBuild, ...)` as a background task when the panel's `enemiesSeen` fires (setting on, OP.GG session open); the end of the client session (before `opgg.close()`) and shutdown cancel it and wait for it to end. Cancelling stops the refresh task, not an OP.GG GET already in flight: `getChampionBuild` is wrapped in `alru_cache`, which shields the underlying request, so that GET runs to completion (or fails when its session closes) and its result is discarded |
| `exceptions.py` | Domain exceptions (`SummonerNotFound`, …) |

## For AI Agents

### Working In This Directory
- `connector.py` uses **undocumented** Tencent/Riot endpoints + the user's own session
  token. CN region codes and SGP paths are in `../../README.md` (workspace) and project
  memory — treat reverse-engineered enum/region values as **unverified** until observed.
- Network calls are async (aiohttp coroutines under `qasync`); don't block the Qt loop.
- This layer is data-only — it must not import from `view/`. UI reacts via `SignalBus`.
- `formatting` / `payloads` / `scoring` / `champ_select` must never import `tools` (it imports
  them). A helper that needs the connector or an orchestrator belongs in `tools.py`. A function
  defined in one of them looks its collaborators up in its own module, so patching `tools.X` does
  not reach it: patch the shared object (`tools.connector`, `tools.cfg`) or the defining module.

### Testing Requirements
Live behavior needs a running, logged-in LoL client (LCU); CN match history additionally requires a
CN-client session for a valid SGP token. The parsing and orchestration logic has offline unit tests
with fixtures under `tests/fixtures/`. Run the suite from the repo root with `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .`
(`-t .` matters: it imports the modules as `tests.test_x`, so `tests/__init__.py` runs first and points the
config dir at a temp dir via `SERAPHINE_CONFIG_DIR`; without it the app is imported before that and the
run would read the real config, which `tests/test_config_dir.py` detects and fails on). Never launch the GUI to check a change: the offscreen unit tests and
`QT_QPA_PLATFORM=offscreen python main.py --version` / `--selftest` are the smoke tests.
The ledger model and database are covered offline by `tests/test_ledger_estimator.py` (real baseline rows
in `tests/fixtures/ledger/`), `tests/test_ledger_timers.py` (fake clock) and `tests/test_ledger_data.py`
(temp config dir, fake OP.GG fetch, and the build tool on canned responses); the MainWindow wiring by
`tests/test_ledger_integration.py`.

### Common Patterns
- LCU bootstrap: parse `LeagueClientUx.exe` cmdline for `--app-port` /
  `--remoting-auth-token` / `--rso_platform_id`.
- SGP: `Authorization: Bearer {RSO token}` to `https://{region}-sgp.lol.qq.com:21019/...`.

## Dependencies

### Internal
- `../common/` — signals, config, logging
- Consumed by `../view/` (career/game-info/search interfaces) and `../components/`

### External
- aiohttp (async HTTP), psutil (process discovery), the LCU API + Tencent SGP gateway

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
