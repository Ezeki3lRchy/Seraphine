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
| `release_win.ps1` | One-command Windows release: bump → build via spec → verify frozen `--version` → pack `.7z` (py7zr) → `gh release` on this repo (`Ezeki3lRchy/Seraphine`; a created release's tag targets HEAD, so `-Push` is required with a new bump commit and runs before publishing). Mirrors `release_mac.sh` |
| `.github/workflows/build_windows.yml` | `workflow_dispatch` Windows `.7z` build on a GitHub-hosted runner (build/pack/verify only, same steps as `release_win.ps1`, publishes nothing). Bump + push `VERSION` first, `gh workflow run build_windows.yml -f version=X.Y.Z`, download the artifact, `gh release upload vX.Y.Z Seraphine.7z --repo Ezeki3lRchy/Seraphine --clobber`. Lets a macOS maintainer ship both platforms (first used for v1.3.0) |
| `.github/workflows/release_windows.yml` | **Automatic** Windows release: a push to `main` that changes `VERSION` in `app/common/config.py` builds via `build_windows.yml` and publishes `Seraphine.7z` as a Release of this repo (uploads onto the version's release if it exists, else creates it with the tag on the built commit). No-op when that release already has a `Seraphine.7z`. Uses the built-in `GITHUB_TOKEN` (`contents: write` on the publish job only); no secrets |
| `make.ps1` | **STALE** — pre-migration PyQt5 packaging script (bare `pyinstaller`, flat layout, PyInstaller 5.13). Cannot build the PySide6 source; use `Seraphine.win.spec` instead |
| `sync.py` | Helper script for syncing/updating resources or data |
| `requirements.txt` | Pinned deps (PySide6 6.9.3 + PySide6-Fluent-Widgets, aiohttp 3.10.10, qasync, py7zr, pywin32 on win32, …) |
| `Seraphine.pro` | Qt project file for extracting translatable strings (`pylupdate`) |
| `readme.md` | Project README (English); Chinese version in `readme.zh-CN.md` |
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
- Releases live on this repo (`Ezeki3lRchy/Seraphine`); the in-app updater (`app/common/util.py`
  `Github`) reads its `/releases/latest`, `document/ver.json` and `document/notice.md`.

### One-time release-repo transition
Installed copies <= 1.4.0 still read the retired `Ezeki3lRchy/Seraphine-releases`. Once the
first release made here **above 1.4.0** (vX.Y.Z, built from a commit whose updater reads this
repo; do not re-release 1.4.0 here) has **both** `Seraphine.7z` and `Seraphine.dmg`, mirror it
there once so those copies can update to a build that reads the new location (then never again):
```shell
gh release download vX.Y.Z --repo Ezeki3lRchy/Seraphine --pattern 'Seraphine.*' --dir /tmp/sr
gh release view vX.Y.Z --repo Ezeki3lRchy/Seraphine --json body --jq .body > /tmp/sr/notes.md
gh release create vX.Y.Z /tmp/sr/Seraphine.7z /tmp/sr/Seraphine.dmg --repo Ezeki3lRchy/Seraphine-releases \
  --title "vX.Y.Z" --notes-file /tmp/sr/notes.md
```

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
