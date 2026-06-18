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
| `update.py` | `runUpdater()` — waits for `Seraphine.exe` to exit, then applies an update |
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
