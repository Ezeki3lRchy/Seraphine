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
| `update.py` | Platform updaters. Windows `runUpdater(sourceDir)`: writes `updater.ps1` to the config dir and starts it hidden + detached with `-InstallDir` (dir of `sys.executable`; the real layout is `Seraphine.exe` + `filelist.txt` at the root, everything else under `_internal\`, and the app's CWD is `_internal`, so nothing depends on the CWD), `-SourceDir`, `-WaitPid`, `-BackupDir`, `-ResultPath`, `-LockPath`, `-TargetVersion`. The script first takes an exclusive lock file (a second instance exits 7 touching nothing; `runUpdater` does not start one while it is held), waits for that PID, refuses drive-root paths, completes an unfinished restore if an earlier run left a backup whose `backup.meta` fingerprint (SHA-256 of the pre-update and the package `Seraphine.exe`) matches the installed exe — otherwise the backup is foreign and discarded untouched (fails with `restore-failed`/8 if a matching restore cannot complete), writes `backup.meta` before anything is backed up, prechecks (reparse points, file-vs-dir) and backs up every install file the package replaces, robocopy-merges with `/IM` (bounded retries; on failure restores every backed-up file, SHA-256 compare only when a copy fails), treats errors after a completed merge as success, then deletes only files in the old `filelist.txt` that the package does not contain (never outside the install dir, never through a reparse point, skipped without a new filelist), writes `<config>/update/result.json` and relaunches whatever is in place; log in `<config>\updater.log`. `runUpdaterMac` copies the new .app next to the old one, verifies it, then swaps with a backup (restored on failure); untested on a real Mac |
| `update_stage.py` | Non-UI download (configured proxy) → SHA-256 verify → (Windows) extract into `<config>/update/package` (must have `Seraphine.exe` + `filelist.txt`) + `staged.json` marker; reads `result.json` at start-up (failed run → keep stage, offer retry); `applyStagedUpdate` only applies the exact on-disk stage and waits for the updater to start; `BackgroundUpdater` stages in the background only when no game is known to run (unknown / ChampSelect / GameStart / InProgress / Reconnect block it); reused by `UpdateMessageBox` |
| `util.py` | Misc helpers (`Github` client, etc.) |
| `qfluentwidgets.py` | Local re-export/shim of PyQt-Fluent-Widgets (incl. `FluentTranslator`) |

## For AI Agents

### Working In This Directory
- Prefer adding cross-component events to `SignalBus` over wiring direct references
  between `view/` interfaces.
- `config.py` is the single source for version strings and persisted settings — change
  versions here, not scattered literals.
- Theming is enum-driven (`Icon`, `StyleSheet`) and resolves against `../resource/qss/`
  (dark + light) and `../resource/icons/`.

### Testing Requirements
Pure-infra; exercise indirectly by launching the app. `config.py` changes are smoke-tested
by `python ../main.py --version`.

### Common Patterns
- Enums for typed config/icons/styles; QObject signals for messaging.

## Dependencies

### Internal
- Imported by `../lol/`, `../components/`, `../view/`, and `../main.py`

### External
- PyQt5, PyQt-Fluent-Widgets

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
