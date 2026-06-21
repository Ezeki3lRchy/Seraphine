<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-06 | Updated: 2026-06-06 -->

# src-v1.1.4

## Purpose
Originally recovered Seraphine v1.1.4 source (commit `32249c8`, 2024-12-31) from the
`se2o/--Seraphine` fork; the directory name is historical. It has since become the
**cross-platform fork** (origin `Ezeki3lRchy/Seraphine`, now VERSION 1.2.3): the UI was
migrated **PyQt5 → PySide6** and native **macOS** support was added. A desktop app that
queries LoL match history via the local LCU API and (for CN servers) the Tencent SGP gateway.

## Key Files
| File | Description |
|------|-------------|
| `main.py` | Entry point: sets HiDPI, loads config + translators, launches `MainWindow` under a `qasync` event loop. `--version`/`-v` prints the version and exits |
| `Seraphine.win.spec` | **Current** Windows PyInstaller spec (PySide6). Build: `pyinstaller --noconfirm Seraphine.win.spec`. Counterpart of macOS `Seraphine.spec` |
| `release_win.ps1` | One-command Windows release: bump → build via spec → verify frozen `--version` → pack `.7z` (py7zr) → `gh release` to `Ezeki3lRchy/Seraphine-releases`. Mirrors `release_mac.sh` |
| `make.ps1` | **STALE** — pre-migration PyQt5 packaging script (bare `pyinstaller`, flat layout, PyInstaller 5.13). Cannot build the PySide6 source; use `Seraphine.win.spec` instead |
| `sync.py` | Helper script for syncing/updating resources or data |
| `requirements.txt` | Pinned deps (PySide6 6.9.3 + PySide6-Fluent-Widgets, aiohttp 3.10.10, qasync, py7zr, pywin32 on win32, …) |
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
- Build/repack via a **PySide6** venv `..\.build-venv-win` (Python 3.12 + `requirements.txt`
  + **PyInstaller 6.x**, e.g. 6.21). The old PyQt5 `.build-venv` (PyInstaller 5.13) cannot
  build this source. See README §"自己打包可执行文件 / Building it yourself".
- `main.py` `os.chdir`s to `_MEIPASS` at startup. PyInstaller 6.x ships an `_internal\`
  onedir layout (`Seraphine.exe` at root, deps under `_internal\`); resources are bundled
  there too, so relative `app/resource/...` loads resolve. No flat layout required.
- `.7z` packing must place files at the **archive root** with a root `filelist.txt` (v1.1.9
  in-place updater contract) — `release_win.ps1` handles this.
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
- PySide6 + PySide6-Fluent-Widgets + PySideSix-Frameless-Window, aiohttp, qasync, psutil,
  py7zr, pyperclip, async-lru, pywin32 (Windows only)

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
