<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-06 | Updated: 2026-06-06 -->

# src-v1.1.4

## Purpose
Recovered Seraphine v1.1.4 source (git repo, commit `32249c8`, 2024-12-31), cloned from
the `se2o/--Seraphine` fork. A PyQt5 desktop app that queries LoL match history via the
local LCU API and (for CN servers) the Tencent SGP gateway.

## Key Files
| File | Description |
|------|-------------|
| `main.py` | Entry point: sets HiDPI, loads config + translators, launches `MainWindow` under a `qasync` event loop. `--version`/`-v` prints the version and exits |
| `make.ps1` | PyInstaller packaging script (`-dbg` skips 7z compression). **Pin PyInstaller 5.x** |
| `sync.py` | Helper script for syncing/updating resources or data |
| `requirements.txt` | Pinned deps (PyQt5 5.15.9, aiohttp 3.10.10, qasync, py7zr, …) |
| `Seraphine.pro` | Qt project file for extracting translatable strings (`pylupdate`) |
| `readme.md` | Upstream project README (Chinese) — features, build via conda |
| `LICENSE` | Upstream license |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `app/` | Application source — config, LCU/SGP logic, widgets, windows (see `app/AGENTS.md`) |
| `document/` | `notice.md` (in-app notice) + `ver.json` (version/update metadata) |
| `log/` | Runtime log output |
| `.github/` | Issue templates + CI workflows (upstream) |

## For AI Agents

### Working In This Directory
- Run/repack via the workspace `.build-venv` (Python 3.11), not the system Python 3.14
  (can't install the pinned old deps). See `../README.md`.
- `main.py` `os.chdir`s to its own dir, so all resource paths are relative to this folder —
  keep the flat layout intact when packaging.
- Version/beta strings come from `app/common/config.py` (`VERSION`, `BETA`).

### Testing Requirements
- `python main.py --version` → smoke test (no client needed).
- Full run needs a logged-in LoL client (LCU). CN match history additionally needs a CN
  client session (SGP token).

### Common Patterns
- asyncio coroutines bridged into Qt via `qasync`; cross-component messaging via a
  `SignalBus` (`app/common/signals.py`).

## Dependencies

### Internal
- `app/` — all real logic lives here

### External
- PyQt5 + PyQt-Fluent-Widgets, aiohttp, qasync, psutil, py7zr, pyperclip, async-lru

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
