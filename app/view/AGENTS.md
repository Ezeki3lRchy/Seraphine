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
| `main_window.py` | Application shell: hosts the navigation + all interfaces; launched by `../main.py` |
| `start_interface.py` | Landing/connection screen (waits for the LoL client) |
| `career_interface.py` | A summoner's career / match-history view |
| `search_interface.py` | Summoner search screen |
| `game_info_interface.py` | Live-game / BP teammate + opponent lookup |
| `auxiliary_interface.py` | Auxiliary tools (auto-accept, profile tweaks, etc.) |
| `setting_interface.py` | App settings (built from `../components/setting_cards.py`) |
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
Requires a running LoL client. Launch `python ../main.py` and navigate to the changed tab;
op.gg interfaces can be exercised without a client (they hit external op.gg data).

### Common Patterns
- One interface class per screen; navigation/registration centralized in `main_window.py`.

## Dependencies

### Internal
- `../components/` (widgets), `../lol/` (data), `../common/` (config/signals/styles)

### External
- PyQt5, PyQt-Fluent-Widgets, qasync

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
