# Seraphine (custom build)

Custom build with extra features. The source is public; source and compiled releases both live at https://github.com/Ezeki3lRchy/Seraphine (in-app updater download source).

## Changelog

<!--NEW-->

### v1.4.5
- Play style: the Career page has an **Overview / Play style** switch with season statistics — positions, champion focus, blue / red side, classes, activity by weekday and hour, and per-minute numbers.
- Live item value: during a game the game info page shows each lane's and each team's item value. An optional on-top overlay is under *Settings → Item value overlay* (borderless or windowed mode).
- Custom lobbies load both teams while you are still in the lobby.
- *Settings → Check now* checks for updates on demand.
- macOS builds are published automatically alongside Windows.

### v1.4.4
- Insight tags on the lobby / champ select / in-game player cards: a gold **Main champion** pill (top mastery with 100k+ points, or 40%+ of recent games) and win / loss streak pills. Hover a tag to see why it is shown.

### v1.4.3
- Performance card: the (?) help icons show their explanation as soon as you hover them, and light up on hover.
- Match rows: player names are larger and less truncated; the #TAG moved to the hover tooltip.

### v1.4.2
- History by mode: with *Filter history by mode* on, the lobby / champ select / in-game page only shows games from the mode you are in (Solo/Duo no longer lists Flex).
- Ranked score: Solo/Duo and Flex games are scored by a model fitted to OP.GG's real scores (about 0.3 average error on held-out games). Other modes keep Seraphine's own formula.

### v1.4.1
- Match score: every match row and the match detail page show a 0–10 score next to the MVP / SVP / rank badge.
- Redesigned hover tooltips: a themed card that follows light / dark mode.
- Faster start-up and browsing: cached icons skip the request queue, the connector initialises in parallel, match details are fetched once and reused.
- Releases and the in-app updater now use the main repository.

### v1.2.1
- Fix a macOS crash where the app froze on the splash screen after connecting to a running client (summoner-avatar `QPen` keyword-arg incompatibility under PySide6).
- If you're on the v1.2.0 macOS build, use **Update and Restart** to get this fix.

### v1.2.0 — First macOS build 🍎
- Now runs on macOS (Apple Silicon via Rosetta 2, and Intel) alongside Windows.
- Native `.dmg` build — drag to **Applications** and run; native top-left traffic-light window controls; in-app auto-update on macOS.
- Under the hood: migrated **PyQt5 → PySide6**; cross-platform **LCU connection** via the client lockfile. Windows builds are unaffected.
- First launch on macOS (unsigned build): run `xattr -cr /Applications/Seraphine.app` once, then open.

### v1.1.9
- Fix clipped "Provide feedback" button in settings (#13).

### v1.1.8
- Auto empty-ban: ban no champion on your ban turn (the "None" option).
- Open game detail when clicking a card in the game-info panel.
- Surface detail load failures instead of a silent blank.

### v1.1.7
- Rank tier / map / game-mode names follow system language (were forced Chinese).

### v1.1.6
- Lobby panel shows stats for any member count (including solo).
- Live refresh when lobby members join / leave.

### v1.1.5
- Lobby: show party/teammate recent matches + recent win rate in the game-info panel (#310).
