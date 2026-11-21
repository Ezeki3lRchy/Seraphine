<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-06 | Updated: 2026-06-06 -->

# common

## Purpose
Shared infrastructure used across the whole app: configuration, logging, theming/icons,
the global signal bus, the self-updater, and general utilities. No UI screens here — this
is the cross-cutting layer everything else imports.

## Key Files
| File | Description |
|------|-------------|
| `config.py` | `Language` enum + app config (`cfg`); exposes `VERSION` / `BETA` consumed by `main.py` |
| `signals.py` | `SignalBus(QObject)` — global Qt signals for decoupled cross-component messaging |
| `icons.py` | `Icon(FluentIconBase, Enum)` — app icon set |
| `style_sheet.py` | `StyleSheet` enum — QSS theme bindings (win/loss colors, component styling) |
| `logger.py` | `CustomRotatingFileHandler` — rotating file logging into `../../log/` |
| `update.py` | Platform updaters. Windows `runUpdater(sourceDir)`: writes `updater.ps1` to the config dir and starts it hidden + detached with `-InstallDir` (dir of `sys.executable`; the real layout is `Seraphine.exe` + `filelist.txt` at the root, everything else under `_internal\`, and the app's CWD is `_internal`, so nothing depends on the CWD), `-SourceDir`, `-WaitPid`, `-BackupDir`, `-ResultPath`, `-LockPath`, `-TargetVersion`. The script first takes an exclusive lock file (a second instance exits 7 touching nothing; `runUpdater` does not start one while it is held), waits for that PID, refuses drive-root paths, completes an unfinished restore if an earlier run left a backup whose `backup.meta` fingerprint (SHA-256 of the pre-update and the package `Seraphine.exe`) matches the installed exe — otherwise the backup is foreign and discarded untouched (fails with `restore-failed`/8 if a matching restore cannot complete), writes `backup.meta` before anything is backed up, prechecks (reparse points, file-vs-dir) and backs up every install file the package replaces, robocopy-merges with `/IM` (bounded retries; on failure restores every backed-up file, SHA-256 compare only when a copy fails), treats errors after a completed merge as success, then deletes only files in the old `filelist.txt` that the package does not contain (never outside the install dir, never through a reparse point, skipped without a new filelist), writes `<config>/update/result.json` and relaunches whatever is in place; log in `<config>\updater.log`. `runUpdaterMac` copies the new .app next to the old one, verifies it, then swaps with a backup (restored on failure). Its single-instance lock is a `mkdir` lock directory under the stage dir holding a three-line record (PID, process identity = `ps -o lstart=` start time, creation time), written without overwriting another updater's record; it is held only while that process is alive with the same identity (the 30-minute age limit applies only to a record without an identity), an incomplete record gets a short grace period, a second instance exits 7 touching nothing, a stale lock is taken over, and a run releases the lock only while the record is still its own (same rule in the script and `_lockDirHeld`). `hdiutil attach`/`detach` are retried, and `detach` runs only for a mount the script made; `updaterRunning(lockPath)` handles both the Windows lock file and the macOS lock directory; `noRelaunch` (like Windows `-NoRelaunch`) is the switch tests use to never open an app afterwards. Not yet run on a real Mac; a darwin-only test runs it in CI |
| `update_stage.py` | Non-UI download (configured proxy) → SHA-256 verify (a sha256 digest is required on every path: a release asset without one is refused before anything is downloaded, and a staged update without one is discarded) → (Windows) extract into `<config>/update/package` (must have `Seraphine.exe` + `filelist.txt`) + `staged.json` marker; reads `result.json` at start-up (failed run → keep stage, offer retry); `applyStagedUpdate` only applies the exact on-disk stage and waits for the updater to start; `BackgroundUpdater` stages in the background only when no game is known to run (unknown / ChampSelect / GameStart / InProgress / Reconnect block it); reused by `UpdateMessageBox` |
| `util.py` | Misc helpers (`Github` client, etc.) |
| `qfluentwidgets.py` | Local re-export/shim of PySide6-Fluent-Widgets (incl. `FluentTranslator`) |

## For AI Agents

### Working In This Directory
- Prefer adding cross-component events to `SignalBus` over wiring direct references
  between `view/` interfaces.
- `config.py` is the single source for version strings and persisted settings — change
  versions here, not scattered literals.
- Theming is enum-driven (`Icon`, `StyleSheet`) and resolves against `../resource/qss/`
  (dark + light) and `../resource/icons/`.

### Testing Requirements
Pure-infra, covered by the unit tests (e.g. `test_update_stage.py`, `test_windows_updater_script.py`,
`test_config_dir.py`). Run the suite from the repo root (`..\..` from here) with `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .`
(`-t .` matters: it imports the modules as `tests.test_x`, so `tests/__init__.py` runs first and points the
config dir at a temp dir via `SERAPHINE_CONFIG_DIR`; without it the app is imported before that and the
run would read the real config, which `tests/test_config_dir.py` detects and fails on). Never launch the GUI to check a change: the offscreen unit tests and
`QT_QPA_PLATFORM=offscreen python main.py --version` / `--selftest` are the smoke tests.
`config.py` changes are smoke-tested by `QT_QPA_PLATFORM=offscreen python ../../main.py --version`.
`platform.config_dir()` honours a non-empty `SERAPHINE_CONFIG_DIR` environment variable on every platform
(used by the tests); anything that needs the config location must go through it.

### Common Patterns
- Enums for typed config/icons/styles; QObject signals for messaging.

## Dependencies

### Internal
- Imported by `../lol/`, `../components/`, `../view/`, and `../main.py`

### External
- PySide6, PySide6-Fluent-Widgets

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
