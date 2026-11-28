# Seraphine (custom build)

Custom build with extra features. The source is public; source and compiled releases both live at https://github.com/Ezeki3lRchy/Seraphine (in-app updater download source).

## Changelog

<!--NEW-->

### v1.4.15
- Settings: new "Launch at startup" switch (General group, off by default). When on, Seraphine starts when you log in to the computer. It works in the packaged app only.
- Cooldown ledger: Hexflash has its own cell, so it can be timed while the Flash timer is running. The cell appears for enemies who carry Flash and use the Inspiration rune tree; the game does not show whether they took the rune itself, so some of them will not have it.

### v1.4.14
- Career match list: each game now shows its average rank (emblem, tier and division) under the stats, as OP.GG does. It is the rank at the time of the game, from OP.GG; where OP.GG has none, Seraphine uses the ranks it recorded when the game started, or an estimate from the players' current ranks, marked with "≈".

### v1.4.13
- The Career page appears much sooner: the match list shows right away, and each game's MVP / SVP badge, score, kill participation and the ten players fill in a moment later.
- Refreshing the Career page, or opening a player again, no longer downloads the full games already loaded.
- Seraphine uses much less CPU while it waits for the League client, while the client is open and while its window is hidden in the tray.

### v1.4.12
- Cooldown ledger: Smite (including Unleashed and Primal Smite) and abilities with charges, such as Vel'Koz W, Vi E and Caitlyn W, can now be clicked; their timer shows when one charge comes back.
- Cooldown ledger: Rakan E, Kalista E and Tahm Kench R can now be clicked too.
- New **Cooldown ledger size** setting (80 % to 200 %) makes the whole panel larger or smaller.
- The item value overlay and cooldown ledger settings moved to *Auxiliary Functions → Game*, and they can now be changed while the League client is not running.

### v1.4.11
- New **Cooldown ledger** (off by default; turn it on in *Settings → Cooldown ledger*): on Summoner's Rift, a small panel shows one row per enemy with their abilities, summoner spells and items with an active, and clicking a cell when you see it used starts a timer of its usual cooldown.
- You start every cooldown timer yourself and nothing is detected, so the times are approximate: they use the enemy's level and items as last seen.
- New **Saved players** page in the sidebar: click the star next to a player's name on the Career or Search page to save them, add a note of your own, and open their Career page later without typing their Riot ID (the list is stored on this computer only).
- From the next update on, Settings shows the download progress of an update, and a download interrupted by a game continues where it stopped once the game ends.
- From the next update on, Windows updates are smaller and quicker: when you are exactly one version behind, only the changed files are downloaded, and installing copies only the files that changed.
- Dragging rows to reorder them works again in the champion and client path lists.
- Unlocking the item value overlay makes it clickable again.

### v1.4.10
- Item value (overlay and panel) was far too low because it counted only each item's own combine cost, so completed items were heavily undercounted. It now uses each item's full price.

### v1.4.9
- Item value overlay: each lane now shows both players' item values next to the difference, the difference is written out in full when it fits, and small coloured marks show which column is yours and which is the enemy's.
- "Even" is gone from the item value overlay and panel: the actual difference is always shown (0 when exactly equal).
- Settings: the **Check now** button no longer has its label cut off.

### v1.4.8
- An unexpected error no longer closes Seraphine: a notice with **Copy details** appears and the app keeps running. If connecting to the game client fails, it goes back to the waiting screen and tries again instead of staying on a locked loading page.
- Champ select and game state follow the newest event, so a slow load can no longer overwrite what happened after it; champion icons stay correct after quick swaps.
- Fixed the **Send friend request** button, which always answered "Summoner not found". Creating or deleting a rune page, rerolling and creating a practice lobby are no longer repeated when the client is slow, and the *Change client* dialog no longer freezes on a client that does not answer.
- Updates: a download that GitHub provides no checksum for is refused (use *Manually Download* in that case); the macOS updater is more robust.
- Play style: the (?) help icons light up on hover.

### v1.4.7
- Complete Chinese interface: everything this build adds (Career views, match detail, OP.GG build page, item value, update prompts) is translated to Simplified Chinese.
- Rank tiers, relative times, game length and multi-kill / placement tags follow the interface language instead of staying English.

### v1.4.6
- Updates now download in the background (never during a game) and only ask you to restart. If an update cannot be applied, the previous version is restored and you are told. **Versions up to 1.4.5 cannot update themselves on Windows: download this release once by hand (Manually Download), after that it is automatic.**
- Champions: a third view on the Career page with this season's numbers per champion, expandable into lane matchups.
- Career season statistics (Performance, Play style, Champions) now cover the whole season on non-CN servers instead of the last ~20 games.
- Match detail rows and the Flex line use the small rank icons.

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
