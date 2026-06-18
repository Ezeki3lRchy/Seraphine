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
| `listener.py` | `LolProcessExistenceListener` (QThread) — watches for the LoL client process starting/stopping |
| `champions.py` | `ChampionAlias` — champion id/name/alias lookup |
| `aram.py` | `AramBuff` — ARAM per-champion balance buffs/nerfs |
| `opgg.py` | `Opgg` — client for op.gg data (tier lists / builds) |
| `tools.py` | `ToolsTranslator` + helpers used across the data layer |
| `exceptions.py` | Domain exceptions (`SummonerNotFound`, …) |

## For AI Agents

### Working In This Directory
- `connector.py` uses **undocumented** Tencent/Riot endpoints + the user's own session
  token. CN region codes and SGP paths are in `../../README.md` (workspace) and project
  memory — treat reverse-engineered enum/region values as **unverified** until observed.
- Network calls are async (aiohttp coroutines under `qasync`); don't block the Qt loop.
- This layer is data-only — it must not import from `view/`. UI reacts via `SignalBus`.

### Testing Requirements
Requires a running, logged-in LoL client (LCU). CN match history additionally requires a
CN-client session for a valid SGP token. There are no offline unit tests.

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
