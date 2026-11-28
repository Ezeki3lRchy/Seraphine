<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-06 | Updated: 2026-06-06 -->

# view

## Purpose
Top-level windows and the tabbed interfaces inside them. `main_window.py` is the shell;
each `*_interface.py` is one tab/screen, composed from `../components/` widgets and fed by
`../lol/` data. This is the presentation layer.

## Key Files
| File | Description |
|------|-------------|
| `main_window.py` | Application shell: hosts the navigation + all interfaces; launched by `../main.py`. Also creates the in-game windows: the item value overlay (`../components/gold_overlay.py`) and the cooldown ledger (`../components/ledger_panel.py` with `../lol/ledger_data.py`'s `LedgerDatabase`, loaded from files at start-up; its OP.GG skill-priority refresh runs as a background task when the panel reports a game's enemies, is cancelled when the client ends (before the OP.GG session closes) and at shutdown, where the panel is also closed and disconnected; a failure while building the ledger at start-up only means no panel), and shows the update progress / ready / failure info bars |
| `start_interface.py` | Landing/connection screen (waits for the LoL client) |
| `career_interface.py` | A summoner's career / match-history view |
| `search_interface.py` | Summoner search screen |
| `game_info_interface.py` | Live-game / BP teammate + opponent lookup |
| `auxiliary_interface.py` | Auxiliary tools (auto-accept, profile tweaks, etc.); on Windows the "Auto accept" card has a row for `cfg.hideClientOnReadyCheck` (see `../common/client_focus.py`); its Game group also holds the in-game panels' cards: the item value overlay (on/off, opacity, lock), the "Cooldown ledger" switch (`cfg.enableCooldownLedger`, off by default; its two-line description is the card's only wording about the feature) and "Cooldown ledger size" (`cfg.cooldownLedgerScale`, 80-200 %). While no client is connected MainWindow calls `setClientConnected(False)`, which disables every card except these five (the panels read the game's Live Client Data API, and the lock card is the only way to unlock a click-through overlay); never `setEnabled(False)` the whole page |
| `setting_interface.py` | App settings (built from `../components/setting_cards.py`), incl. the update card's progress subtitle / bar / "Restart to update" button, and the "Launch at startup" switch (General group; no config item: it shows `../common/autostart.py`'s `isEnabled()` when built and on every show, calls `setEnabled` on a toggle and goes back with an error bar when that fails; disabled with "Not available" when the app does not run from a packaged build) |
| `opgg_window.py` | Standalone op.gg window |
| `opgg_tier_interface.py` | op.gg tier-list view |
| `opgg_build_interface.py` | op.gg champion build view |

## For AI Agents

### Working In This Directory
- Screens compose `../components/` and subscribe to `../common/signals.py` (`SignalBus`);
  they pull data from `../lol/` but the data layer must never import back into `view/`.
- Add a new screen by extending the base interface (`../components/seraphine_interface.py`)
  and registering it in `main_window.py`'s navigation.
- Keep network/data work in `../lol/` coroutines; views should react to signals, not block.

### Testing Requirements
Interface logic is unit-tested offscreen without a client. Run the suite from the repo root with `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .`
(`-t .` matters: it imports the modules as `tests.test_x`, so `tests/__init__.py` runs first and points the
config dir at a temp dir via `SERAPHINE_CONFIG_DIR`; without it the app is imported before that and the
run would read the real config, which `tests/test_config_dir.py` detects and fails on). Never launch the GUI to check a change: the offscreen unit tests and
`QT_QPA_PLATFORM=offscreen python main.py --version` / `--selftest` are the smoke tests.

### Common Patterns
- One interface class per screen; navigation/registration centralized in `main_window.py`.

## Dependencies

### Internal
- `../components/` (widgets), `../lol/` (data), `../common/` (config/signals/styles)

### External
- PySide6, PySide6-Fluent-Widgets, qasync

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
