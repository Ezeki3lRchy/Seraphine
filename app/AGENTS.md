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
| `components/` | Reusable PyQt-Fluent widgets (avatars, champion icons, message boxes, setting cards, …) (see `components/AGENTS.md`) |
| `view/` | Top-level windows and tabbed interfaces (career, game info, search, settings, opgg) (see `view/AGENTS.md`) |
| `resource/` | Static assets — `i18n/` translations, `icons/`, `images/`, `qss/` (dark+light QSS), `bin/`. Not documented per-file |

## For AI Agents

### Working In This Directory
- Layering: `view/` composes `components/`, both driven by `lol/` (data) and `common/`
  (config/signals/styles). Keep that direction — don't import `view` from `lol`.
- Cross-component communication goes through `common/signals.py` (`SignalBus`), not direct
  references between interfaces.
- Resource lookups are `__file__`-relative (e.g. `resource/icons/*.svg`); the flat
  PyInstaller layout must be preserved or icon loading breaks (the PyInstaller 5-vs-6 trap).

### Testing Requirements
Behavior requires a running LoL client (LCU). Validate UI/asset changes by launching
`python ../main.py` and exercising the affected interface.

### Common Patterns
- Qt + asyncio via `qasync`; long network calls are coroutines.
- Enums for typed config/icons/stylesheets (`Language`, `Icon`, `StyleSheet`).

## Dependencies

### Internal
- `lol/` ← used by `view/` and `components/` for data
- `common/` ← used everywhere for config, signals, styling

### External
- PyQt5, PyQt-Fluent-Widgets, aiohttp, qasync, psutil

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
