<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-06 | Updated: 2026-06-06 -->

# components

## Purpose
Reusable PySide6-Fluent-Widgets building blocks composed by the screens in `../view/`. Each
file is a self-contained widget (avatars, champion icons, message boxes, setting cards,
etc.) with no top-level window logic of its own.

## Key Files
| File | Description |
|------|-------------|
| `seraphine_interface.py` | Base interface widget that the `view/` screens extend |
| `avatar_widget.py` | Summoner avatar display |
| `champion_icon_widget.py` | Champion portrait icon |
| `multi_champion_select.py` | Multi-champion picker |
| `profile_level_icon_widget.py` | Summoner level badge |
| `summoner_name_button.py` | Clickable summoner-name button (jumps to career) |
| `multi_lol_path_setting.py` | Multi-client LoL install-path setting |
| `setting_cards.py` | Fluent setting cards for the settings interface |
| `search_line_edit.py` | Search input with completion |
| `message_box.py` | Modal dialogs |
| `ledger_panel.py` | `LedgerPanel`: the cooldown ledger, an always-on-top panel (separate window, own saved position `cfg.cooldownLedgerPos`, on/off `cfg.enableCooldownLedger`, default off; background alpha follows the overlay's `goldOverlayOpacity`; `cfg.cooldownLedgerScale` scales the whole panel: the layout below is in unscaled px, `paintEvent` scales the painter, icons and outlined labels are rendered at device ratio x scale, and mouse positions go through `unscaled()` before `gridAt`) with one row per enemy in the fixed order top / jungle / mid / bottom / support: lane icon, Q W E R (36 x 28 px, the larger cells; while running: seconds left, e.g. `120`, with the ability letter in the corner, blue), Flash (always the left spell cell), the other spell, three item-active slots (a sold item leaves a hole, cells never move) and, on the top row only, TP when the top laner carries no Teleport (30 x 28 px; while running: the game time the cell returns, `mm:ss`, outlined over the dimmed icon, gold). A click starts / cancels a timer of the estimated cooldown (`app/lol/ledger_model.py` `LedgerTimers` / `cooldown`, champion looked up by the roster's `rawChampionName` first); an inestimable cell does nothing and is greyed (text and icon alike); a double click toggles once. Press -> release within 5 px is a click; a longer move drags the panel when unlocked. The bottom-right corner is the shared lock (`cfg.goldOverlayLocked`, gold padlock = unlocked): it only stops dragging here; the panel is never click-through and its window flags are set once (`WindowDoesNotAcceptFocus`, never `WindowTransparentForInput`); `set_window_no_activate` on every show. Shown only while the setting is on and `ledgerVisible(summary)` (CLASSIC, map 11, five distinct enemy positions); logs `ledger panel shown` / `ledger panel hidden (<reason>)` on changes only. Icons through injected async loaders (`connector.getSummonerSpellIcon` / `getItemIcon`) and an optional `spellKnown(id)` check (spell ids the client does not list are never requested); a loader failure keeps the cell's two-letter text; `disconnectSources()` cancels pending loads. `enemiesSeen` signal = the database refresh trigger. Offscreen it is never shown (`overlayAllowed`). Created by MainWindow next to the item value overlay (`gold_overlay.py`) |
| `color_label.py` / `transparent_button.py` / `tool_tip.py` | Small styled primitives |
| `animation_frame.py` / `draggable_widget.py` | Animated / draggable container widgets |
| `temp_system_tray_menu.py` | System tray menu |

## For AI Agents

### Working In This Directory
- Widgets are leaf UI — keep them dumb: take data in, emit signals out (via
  `../common/signals.py`), don't reach into `../lol/` or `../view/` directly.
- Styling comes from `../common/style_sheet.py` + `../resource/qss/` (dark/light); use the
  enum bindings rather than inline stylesheets.
- Icons via `../common/icons.py` and `../resource/icons/` (`__file__`-relative paths).

### Testing Requirements
Widget logic is unit-tested offscreen; there is no need to open a window. Run the suite from the repo root with `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .`
(`-t .` matters: it imports the modules as `tests.test_x`, so `tests/__init__.py` runs first and points the
config dir at a temp dir via `SERAPHINE_CONFIG_DIR`; without it the app is imported before that and the
run would read the real config, which `tests/test_config_dir.py` detects and fails on). Never launch the GUI to check a change: the offscreen unit tests and
`QT_QPA_PLATFORM=offscreen python main.py --version` / `--selftest` are the smoke tests.

### Common Patterns
- Subclass a PySide6-Fluent-Widgets base; expose Qt signals for interaction.

## Dependencies

### Internal
- `../common/` (icons, styles, signals); consumed by `../view/`

### External
- PySide6, PySide6-Fluent-Widgets

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
