<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-06 | Updated: 2026-06-06 -->

# app

## Purpose
The application package: all of Seraphine's logic. Splits into LCU/SGP/data access
(`lol/`), reusable Qt widgets (`components/`), top-level windows/interfaces (`view/`),
shared infrastructure (`common/`), and static assets (`resource/`).

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `lol/` | LoL data layer: LCU + SGP connector, champion/ARAM data, opgg client, process listener (see `lol/AGENTS.md`) |
| `common/` | Shared infra: config, logging, icons, stylesheets, the `SignalBus`, updater, utils (see `common/AGENTS.md`) |
| `components/` | Reusable PySide6-Fluent-Widgets widgets (avatars, champion icons, message boxes, setting cards, …) (see `components/AGENTS.md`) |
| `view/` | Top-level windows and tabbed interfaces (career, game info, search, settings, opgg) (see `view/AGENTS.md`) |
| `resource/` | Static assets — `i18n/` translations, `icons/`, `images/`, `qss/` (dark+light QSS), `bin/`. Not documented per-file |

## For AI Agents

### Working In This Directory
- Layering: `view/` composes `components/`, both driven by `lol/` (data) and `common/`
  (config/signals/styles). Keep that direction — don't import `view` from `lol`.
- Cross-component communication goes through `common/signals.py` (`SignalBus`), not direct
  references between interfaces.
- Resource lookups are `__file__`-relative (e.g. `resource/icons/*.svg`) and, in a frozen build,
  resolve inside PyInstaller 6's `_internal\` onedir layout (`main.py` chdirs to `_MEIPASS`,
  where the specs bundle `app/resource/`); keep new resources listed in the specs' datas.

### Testing Requirements
Behavior against the real client needs a logged-in LoL client (LCU), but the logic is unit-tested
without one. Run the suite from the repo root (`..` from here) with `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .`
(`-t .` matters: it imports the modules as `tests.test_x`, so `tests/__init__.py` runs first and points the
config dir at a temp dir via `SERAPHINE_CONFIG_DIR`; without it the app is imported before that and the
run would read the real config, which `tests/test_config_dir.py` detects and fails on). Never launch the GUI to check a change: the offscreen unit tests and
`QT_QPA_PLATFORM=offscreen python main.py --version` / `--selftest` are the smoke tests.

### Common Patterns
- Qt + asyncio via `qasync`; long network calls are coroutines.
- Enums for typed config/icons/stylesheets (`Language`, `Icon`, `StyleSheet`).

## Dependencies

### Internal
- `lol/` ← used by `view/` and `components/` for data
- `common/` ← used everywhere for config, signals, styling

### External
- PySide6, PySide6-Fluent-Widgets, aiohttp, qasync, psutil

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
