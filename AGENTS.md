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
| `.github/workflows/build_macos.yml` | `workflow_dispatch` macOS `.dmg` build on a GitHub-hosted Apple Silicon runner: creates `venv/` and runs `make_mac.sh` unchanged, verifies frozen `--version`, uploads artifact `Seraphine-mac-X.Y.Z`; publishes nothing. Unsigned, like the local build |
| `.github/workflows/test.yml` | Unit tests (`python -m unittest discover -s tests -t .`, Qt offscreen) on `windows-latest` + `macos-latest`: runs on push to `main` and on pull requests, and is called by `release.yml` with an `os` input (one runner) so each platform is gated on its own result |
| `.github/workflows/release.yml` | **Automatic** release for both platforms: a push to `main` that changes `VERSION` in `app/common/config.py` runs `test.yml` per platform (a platform whose own tests fail is not built or published; the other still is), then builds via `build_windows.yml` and `build_macos.yml` and publishes `Seraphine.7z` + `Seraphine.dmg` as one Release of this repo (uploads onto the version's release if it exists, else creates it with the tag on the built commit). Each platform is skipped when the release already has its file; a failed build on one platform still publishes the other. `workflow_dispatch` fills in what the current version's release is missing. Uses the built-in `GITHUB_TOKEN` (`contents: write` on the publish job only); no secrets |
| `requirements.txt` | Pinned runtime deps (PySide6 6.9.3 + PySide6-Fluent-Widgets, aiohttp 3.10.10, qasync, py7zr, pywin32 on win32, …); the test workflow installs only this |
| `requirements-build.txt` | `-r requirements.txt` + the pinned PyInstaller: the **single source** of the PyInstaller version (both build workflows, `release_win.ps1`'s message, the macOS `venv/` for `make_mac.sh` all install it; `make_mac.sh` itself installs nothing). Bump it here only |
| `tools/` | `read_version.py` (prints `VERSION` from `app/common/config.py`; used by `release_win.ps1`, `release.yml` and the build workflows); `pack_win.ps1` (stops on any error; writes the in-place updater's `filelist.txt` — UTF-8 BOM + CRLF written explicitly so the bytes are the same under Windows PowerShell and PowerShell 7, backslash relative paths, not listing itself — checks it exists, then packs `Seraphine.7z` via `pack_7z.py`/py7zr; shared by `release_win.ps1` and `build_windows.yml`, covered by `tests/test_pack_win.py`) |
| `.github/dependabot.yml` | Monthly `pip` + `github-actions` update PRs (small open-PR limit, grouped); the way the older runtime pins get reviewed. Never auto-merged |
| `pyproject.toml` | Tooling config only: a minimal ruff setup (error-finding rules F/E9/B006/B018/PLE, py312, line length 120, no naming/formatter rules; bare-except `E722` is deliberately not enforced, see the comment there). `ruff check .` is clean and enforced by the `lint` job in `test.yml` (push / pull request only; it does not gate releases) |
| `requirements-dev.txt` | The pinned ruff and PyYAML for the lint job; install it in any venv except the build venv |
| `.editorconfig` | UTF-8, LF (what the index stores; the Windows working tree is CRLF via `core.autocrlf`), 4-space Python |
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
- Build/repack via a **PySide6** venv `..\.build-venv-win` (Python 3.12 +
  `requirements-build.txt`, i.e. the runtime pins plus the pinned **PyInstaller 6.x**). The old PyQt5 `.build-venv` (PyInstaller 5.13) cannot
  build this source. See README §"自己打包可执行文件 / Building it yourself".
- `main.py` `os.chdir`s to `_MEIPASS` at startup. PyInstaller 6.x ships an `_internal\`
  onedir layout (`Seraphine.exe` at root, deps under `_internal\`); resources are bundled
  there too, so relative `app/resource/...` loads resolve. No flat layout required.
- `.7z` packing must place files at the **archive root** with a root `filelist.txt` (in-place
  updater contract) — `tools/pack_win.ps1` handles this (called by `release_win.ps1` and
  `build_windows.yml`; do not re-inline it).
- `release.yml`'s `publish` job only runs on `main` (a manual run elsewhere builds but does not
  publish) and its checkout does not persist git credentials (`gh` uses `GH_TOKEN`).
- Self-update (`app/common/update_stage.py` + `update.py`): packaged builds download, verify and
  stage a newer release in the background (not during a game) under `<config>/update/`, then
  only ask to restart; the Windows updater script gets the install dir (dir of
  `sys.executable`, since the CWD is `_internal\`) as a parameter and does not depend on the
  CWD. Builds <= 1.4.5 cannot self-update (button disabled) and need one manual download.
- Version/beta strings come from `app/common/config.py` (`VERSION`, `BETA`).
- Releases live on this repo (`Ezeki3lRchy/Seraphine`); the in-app updater (`app/common/util.py`
  `Github`) reads its `/releases/latest`, `document/ver.json` and `document/notice.md`.

### Testing Requirements
- Suite: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .` (stdlib unittest, no
  client needed). With the `-t .` above the modules import as `tests.test_x`, so `tests/__init__.py`
  runs first and sets `SERAPHINE_CONFIG_DIR` (and, on Windows, `APPDATA`) to a fresh temp dir before any
  app import: the run never touches the real config. Without `-t .` (modules imported as `test_x`) the
  app is imported first and WOULD read the real config; `tests/test_config_dir.py` asserts that
  `LOCAL_PATH` and the other import-time config paths lie under the override and fails with a message
  telling the reader to use `-t .`.
  `app.common.platform.config_dir()` returns `SERAPHINE_CONFIG_DIR` as-is on every platform when it is
  set to a non-empty **absolute** path (a relative one is ignored: in a frozen build it would resolve
  inside `_internal\`); all config-location code must go through it. Never launch the GUI to verify a change.
- `QT_QPA_PLATFORM=offscreen python main.py --version` / `--selftest` → smoke tests (no client needed).
- `python main.py --selftest` (hidden) → import smoke test: imports the whole app graph, creates a
  QApplication (offscreen by default), loads the zh_CN translator and checks bundled resources,
  prints `selftest OK <version>`; builds no window and starts no loop. The build workflows and
  release scripts run it on the frozen build right after the `--version` check (after packing, since
  it may create `log/` in the bundle). Unit tests: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .`.
- Lint: `pip install -r requirements-dev.txt` (pinned ruff, in any venv but the build venv), then
  `ruff check .` must exit 0 (rules in `pyproject.toml`; CI's `lint` job in `test.yml` runs it).
  After editing anything under `.github/`, run `python tools/check_yaml.py` as well: a workflow file
  that does not parse is rejected by GitHub before any job runs (an unquoted step name containing
  `: ` did exactly that once), and only the lint job of another workflow can report it.
- Full run needs a logged-in LoL client (LCU). CN match history additionally needs a CN
  client session (SGP token).

### Common Patterns
- asyncio coroutines bridged into Qt via `qasync`; cross-component messaging via a
  `SignalBus` (`app/common/signals.py`).
- Error handling: after start-up, uncaught exceptions are logged and shown as a non-modal notice
  (`app/common/error_policy.py`) instead of ending the app; that path logs the full traceback. Log
  lines you write in an `except` block carry the exception **type name only** (see `errorSummary` in
  `app/lol/connector.py`), never its `repr`: an aiohttp error's repr includes the request headers with
  the client token, and its message can carry a player id. A bare `except:` around an `await` must
  re-raise `asyncio.CancelledError`, and the app's own exceptions in `app/lol/exceptions.py` subclass
  `BaseException`.

## Dependencies

### Internal
- `app/` — all real logic lives here

### External
- PySide6 + PySide6-Fluent-Widgets + PySideSix-Frameless-Window, aiohttp, qasync, psutil,
  py7zr, pyperclip, async-lru, pywin32 (Windows only)

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
