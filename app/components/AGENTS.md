<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-06 | Updated: 2026-06-06 -->

# components

## Purpose
Reusable PyQt-Fluent-Widgets building blocks composed by the screens in `../view/`. Each
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
| `game_infobar_widget.py` | Per-game info bar (KDA / result row) |
| `mode_filter_widget.py` | Game-mode filter control |
| `multi_lol_path_setting.py` | Multi-client LoL install-path setting |
| `setting_cards.py` | Fluent setting cards for the settings interface |
| `search_line_edit.py` | Search input with completion |
| `message_box.py` | Modal dialogs |
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
Visual; verify by launching `python ../main.py` and viewing the screen that hosts the widget.

### Common Patterns
- Subclass a PyQt-Fluent-Widgets base; expose Qt signals for interaction.

## Dependencies

### Internal
- `../common/` (icons, styles, signals); consumed by `../view/`

### External
- PyQt5, PyQt-Fluent-Widgets

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
