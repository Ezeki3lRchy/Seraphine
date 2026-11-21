<p align='center'>
  <!-- Hero is the upstream Seraphine wordmark, vendored into this repo (not hotlinked). Consider replacing it with a real macOS screenshot later — shot list in docs/screenshots/README.md. -->
  <img src="docs/screenshots/hero.png" alt="Seraphine" width="720">
</p>

<p align='center'>
  <b>Mature · Complete · Native — the League of Legends assistant for Mac players</b><br>
  Match history · champ-select automation · all 10 players in Solo/Duo, even in Streamer Mode · one-click OP.GG runes &amp; builds · ARAM buffs — everything you get on Windows, now on macOS
</p>

<div align="center">

  [![License](https://img.shields.io/github/license/Ezeki3lRchy/Seraphine?style=flat&label=License)](https://github.com/Ezeki3lRchy/Seraphine/blob/main/LICENSE)
  [![Forks](https://img.shields.io/github/forks/Ezeki3lRchy/Seraphine?style=flat&label=Forks)](https://github.com/Ezeki3lRchy/Seraphine/forks)
  [![Stars](https://img.shields.io/github/stars/Ezeki3lRchy/Seraphine?style=flat&label=Stars)](https://github.com/Ezeki3lRchy/Seraphine/stargazers)
  [![Release](https://img.shields.io/github/v/release/Ezeki3lRchy/Seraphine?style=flat&label=Release)](https://github.com/Ezeki3lRchy/Seraphine/releases/latest)

</div>

<p align='center'>
  <b>English</b> · <a href="readme.zh-CN.md">简体中文</a>
</p>

> This repository is a **cross-platform fork** of [Zzaphkiel/Seraphine](https://github.com/Zzaphkiel/Seraphine): it keeps all of the original features, migrates the UI from PyQt5 to **PySide6**, and adds **native macOS support** (`.app` / `.dmg` packaging and a platform-aware self-updater). Windows behavior matches upstream.

## Highlights ✨
- 🎮 **Champ select on autopilot**: auto-accept matches, auto-pick and auto-ban in champ select, auto-accept champion-swap requests.
- 🕵️ **All 10 players in Solo/Duo — even with Streamer Mode on**: ranked champ select hides your teammates' names, and Streamer Mode hides them from you as well. Seraphine still shows who they are, with rank, win rate and recent games, the moment champ select starts, and adds the five opponents as soon as the game loads.
- 📊 **Auto-scout every lobby**: the same player cards in normal queues, Flex, ARAM and custom games, plus on-demand lookups for any same-server summoner.
- 🧠 **One-click OP.GG runes &amp; builds**: auto-show tier lists, builds and skill order, apply runes in one click; ARAM champion buffs shown automatically.
- 🍏 **Native macOS / Apple Silicon**: drag the `.dmg` and go, in-app **Update and Restart**, no Windows environment required.

<p align='center'>
  <a href="https://github.com/Ezeki3lRchy/Seraphine/releases/latest"><b>⬇️ Download for macOS / Windows</b></a>
</p>

## Getting started 🤗

### Use a prebuilt package
Go to the [**latest release page**](https://github.com/Ezeki3lRchy/Seraphine/releases/latest) and download the package for your platform from Assets:

| Platform | Package | How to use |
|----------|---------|------------|
| **Windows** | `Seraphine.7z` | Extract anywhere, then double-click `Seraphine.exe` |
| **macOS** | `Seraphine.dmg` | Open the `.dmg` and drag `Seraphine.app` into `Applications` |

> **First launch on macOS**: the `.app` is currently an **unsigned** build, so Gatekeeper quarantines it after download. Run this once in Terminal to clear the quarantine:
> ```shell
> xattr -cr /Applications/Seraphine.app
> ```
> After that just double-click to launch; later versions can be installed in-app via **Update and Restart**.

> **Builds are not code-signed.** On Windows, SmartScreen or your antivirus may warn about `Seraphine.exe` (an unrecognised publisher); that is expected for an unsigned build. The in-app updater trusts this repository's GitHub Releases, which it reads over HTTPS, and checks the SHA-256 digest GitHub reports for the file it downloaded. That protects against a corrupted or tampered download; it does not protect against a compromised repository or release.

### Or run from source
Works on Windows / macOS / Linux. Requires **Python 3.9 or newer** (PySide6 6.9 no longer supports 3.8).

```shell
git clone https://github.com/Ezeki3lRchy/Seraphine.git
cd Seraphine
```

Create and activate a virtual environment (either `venv` or `conda`):
```shell
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

Install dependencies and run:
```shell
pip install -r requirements.txt
python main.py
```

> `python main.py --version` prints the version without launching the client — handy as a smoke test. Full functionality requires a logged-in League of Legends client (LCU); CN-server match history additionally needs a CN client session (an SGP token).

### Uninstalling Seraphine 😑
Delete the program folder, then remove the config directory for your platform:

| Platform | Config directory |
|----------|------------------|
| Windows | `%AppData%\Seraphine` |
| macOS | `~/Library/Application Support/Seraphine` |
| Linux | `~/.config/Seraphine` |

## Feature overview (work in progress) 🥰
- Match-history lookup (Teamfight Tactics not supported)
  - Look up summoners on the same server ✅
  - Auto-look-up teammates after entering champ select ✅
  - Auto-look-up opponents after the game starts ✅

- Other assistance
  - Auto B/P
    - Auto-accept a match once found ✅
    - Auto-pick your champion in champ select ✅
    - Auto-ban in the ban phase (including auto empty-ban) ✅
    - Auto-accept champion-swap / position-swap requests from teammates ✅

  - External data display
    - Show ARAM champion buff info automatically ✅
    - Show OPGG champion tier list automatically ✅
    - Show OPGG builds & skill order, set runes in one click ✅

  - Game features
    - Create a 5v5 custom practice lobby ✅
    - Spectate same-server players' live games ✅
    - Lock in-game settings ✅

  - Client features
    - Auto-reconnect after a disconnect ✅
    - Fix the end-of-game infinite-loading / shrunken client ✅ (Windows)
    - Hot-restart the client ✅

  - Personalization
    - Change your profile background ✅
    - Change your online status ✅
    - Change your status message ✅
    - Change the rank shown on your status card ✅
    - Remove all challenge medals in one click ✅
    - Remove your avatar frame in one click ✅

> A few capabilities that depend on native Windows window handles (fixing the shrunken end-screen, docking the OPGG overlay to the client window) gracefully degrade on macOS — these will be filled in over later polishing stages.

## What this fork changes 🔧
> For readers who care about the internals, or who come from upstream.
- **Native macOS support**: every Windows-only path (win32 / winreg / window handles) is funneled through a cross-platform shim in [`app/common/platform.py`](app/common/platform.py); on macOS it is implemented natively (Qt, `open`, app-bundle paths), while the Windows path is preserved byte-for-byte.
- **PySide6 migration**: the UI framework moved from PyQt5 to PySide6 + [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets).
- **Platform-aware self-update**: the built-in updater reads this repository's [Releases](https://github.com/Ezeki3lRchy/Seraphine/releases) and downloads only the installer that matches your OS (`.dmg` on macOS, `.7z` on Windows) — it never pushes the other platform's package to you. Updates are checked at start-up and can also be checked manually from Settings. In the packaged app a new version is **downloaded, verified (SHA-256) and prepared in the background** (never while you are in champ select or in a game), with its progress shown on the Settings "Check for updates now" card and in a small info bar you can close without stopping the download; a download interrupted by a game continues where it stopped once the game ends. Once it is ready the card's button becomes **Restart to update**. Seraphine then only asks you to restart ("Restart now" / "Later"); a prepared update survives a restart of the app and is offered again without downloading. On Windows, when you are exactly one version behind, Seraphine downloads only the changed files after checking that they fit your install; if they do not, or anything about that smaller download fails, it downloads the full package instead. Installing an update on Windows backs up and copies only the files that actually changed, so it is quicker. If applying the update fails (for example a file is locked), the previous version is restored and relaunched, and Seraphine offers to retry with the already downloaded package. Releases that are not newer than the running version or carry no SHA-256 digest, and installs in a folder Seraphine cannot write to, use the update dialog instead. Turn off **Download updates automatically** in Settings to always get the old update dialog. Versions up to 1.4.5 cannot update themselves on Windows (their "Update and Restart" button is disabled), so download a newer release once by hand.
- **Automatic release**: pushing a `VERSION` bump to `main` makes [GitHub Actions](.github/workflows/release.yml) build `Seraphine.7z` (Windows) and `Seraphine.dmg` (macOS) and publish both in one Release, so installed copies on both platforms offer the update on their next start.
- **One-command releases**: [`release_mac.sh`](release_mac.sh) and [`release_win.ps1`](release_win.ps1) handle the whole flow by hand — bump version → build the `.dmg` / `.7z` → verify → publish a GitHub Release.
- **History by mode**: with *Settings → Filter history by mode* on, the lobby / champ select / in-game page only shows each player's games in the mode you are in (Solo/Duo shows Solo/Duo only, Flex shows Flex only, ARAM shows ARAM only, ...). A non-ranked mode a player has no recent games in falls back to their latest games.
- **Match score**: every match row and the match detail page show an OP.GG-style 0–10 score next to the MVP / SVP / rank badge. Ranked games (Solo/Duo, Flex) are scored by a small model fitted to OP.GG's real scores (ported from [lol-friends-ladder](https://github.com/ignaciomigliaro/lol-friends-ladder); about 0.3 average error on its held-out games). It needs each game's result from Riot's match server, one extra request per game. Other modes, and ranked games whose result can't be fetched, use Seraphine's own formula (kill participation, damage, KDA, damage taken, gold, vision).
- **Insight tags**: each player card on the lobby / champ select / in-game page shows small tags — **Main champion** when the picked champion is the player's top mastery champion (100k+ points) or at least 40% of their recent games (4+ games), and a **win / loss streak** tag from 3 games in a row. Hover a tag to see why it is shown.
- **Live item value**: while a game is running, the game info page shows an **Item value** panel (the total cost of the items each player holds, not gold earned) — both teams' totals, the difference, and the five lanes side by side; in modes without lanes (e.g. ARAM) each team's players are listed by item value instead. Data comes from Riot's official Live Client Data API (`127.0.0.1:2999`); Arena / TFT are skipped. An optional, opt-in **overlay** (Auxiliary Functions → Game → *Item value overlay*) lists each lane's two item values and the difference in a small always-on-top window you can drag, fade and lock (click-through); it only works over the game in **borderless or windowed** mode. Enemy values are the items last seen on the scoreboard; other players' unspent gold is not available.
- **Cooldown ledger** (off by default; turn it on in *Auxiliary Functions → Game → Cooldown ledger*, and set its size there with *Cooldown ledger size*): on Summoner's Rift, a separate small panel with one row per enemy (top, jungle, mid, bottom, support) and a cell for each of their abilities (Q W E R), both summoner spells and up to three items with an active (plus Teleport on the top row when the top laner does not carry it). When you see an enemy use one of them, click its cell to start a timer of its usual cooldown: Q W E R count down the seconds left, spells and items show the game time they come back; a second click cancels it. **You start every timer yourself; nothing is detected.** The times are rough: they use the enemy's level and items as last seen in the game's Live Client Data API, and runes, stacks and cooldown refunds are not counted. Ability, summoner spell and item cooldowns come from Riot's Data Dragon and the game client's data (via CommunityDragon), skill orders from OP.GG, rebuilt once per patch; the app refreshes the skill orders of the enemies in your game from OP.GG in the background. An ability or spell with charges (Smite, Vel'Koz W, Caitlyn W) times one charge coming back. Abilities without a fixed cooldown (passives, toggles, and Veigar W, whose cooldown shrinks with his stacks) have no timer. The panel takes clicks and never takes focus from the game; its lock corner is the same lock as the item value overlay (unlocked = drag it). It is hidden in every other mode.
- **Play style view**: the Career page has an **Overview / Play style** switch. Play style breaks down this season's sample (the same games the Performance panel uses, no extra requests: up to 200 on non-CN servers, up to 150 on CN) into games, win rate, KDA, play time and champion pool; per-minute damage / CS / gold / vision and kill participation; position share; main-champion and top-3 / top-5 focus; blue vs red side; champion classes; weekday and hour-of-day activity; and the recent games' average score with MVP / SVP counts. Filter by All / Solo/Duo / Flex; hover any number for how it is computed.
- **Champions view**: a third Career switch item, **Champions**, lists this season's games per champion — games with a win / loss bar, win rate, KDA with average K / D / A, kill participation, average score (ranked games), lane lead (share of games ahead of the lane opponent in gold and XP after laning), damage per minute and team share, vision and wards, CS and gold per game and per minute, and multi-kills — under an *All champions* total. Click a column to sort, click a champion to see the same figures against each lane opponent. Same sample and queue filter as Play style; Arena is left out. On the client's own match list fallback (no full game data) matchups, score, lane lead and kill participation are not available.
- **Full-season Career statistics on non-CN servers**: the season sample behind Performance, Play style and Champions now comes from Riot's match server — up to 200 games of this season in a handful of requests — instead of the client's short local match list.
- **Both teams in custom lobbies**: in a custom game lobby the game info page already loads both teams — your team under **Ally**, the other team under **Enemy** (a spectator sees Team 1 / Team 2). Bots and spectators are skipped, players who join or switch teams update the page, and only newly joined players are looked up.
- **Saved players**: click the star next to a player's name on the Career or Search page to save them; the **Saved players** page in the sidebar lists them (up to 100) with their last-seen rank and a short note of your own, and a click opens that player's Career page without typing their Riot ID. Drag rows to reorder, filter by name or note. The list is stored on this computer only (`saved_players.json` next to `config.json`) and stays readable with the client closed; opening a player needs the client, and a player the current client cannot load (another server, or a deleted account) keeps their last known name.
- **Redesigned tooltips**: hover tips (items, runes, scores, charts) use a themed card that follows light / dark mode instead of the plain native tooltip.
- **Faster start-up and browsing**: cached icons skip the request queue, the connector initialises in parallel, and match details are fetched once and reused.
- **Complete Chinese interface**: everything this fork adds (Career views, match detail, OP.GG build page, item value, cooldown ledger, update prompts) is translated to Simplified Chinese, and rank tiers, relative times, game length and multi-kill / placement tags follow the interface language instead of staying English.
- **Keeps running after an error**: an unexpected error shows a notice with *Copy details* instead of closing the app, a failed connection to the game client is retried from the waiting screen, and champ select always follows the newest event.

## FAQ 🧐

### Q: Will using Seraphine get my account banned 😨?
The app's features are built **entirely** on the League of Legends client API (LCU). It does **not** read or tamper with the client, game files, code, or memory in any way (see the [Disclaimer](#disclaimer-) below). So using only Seraphine is very unlikely to get you banned — but there is **no guarantee**.

### Q: What if I really do get banned?
Appeal, or wait it out 😭

### Q: On macOS it says "can't be opened because the developer cannot be verified" / "is damaged" — what now?
That's Gatekeeper quarantining an unsigned app. Run `xattr -cr /Applications/Seraphine.app` to clear it (see [Getting started](#getting-started-)).

### Q: Seraphine can't find the League of Legends client on macOS?
It scans `/Applications/League of Legends.app` and `~/Applications/League of Legends.app` automatically. If it's installed elsewhere, set the client path manually in Settings (point it at the `.app` bundle).

### Q: Why can't the client connect / features don't work / the career page spins forever / match history is delayed?
All match-history data comes from the League client's own API; Seraphine just displays it. If something doesn't work or is delayed, the cause is almost always the League servers themselves — usually nothing to do with Seraphine.

### Q: Why don't you show total games / overall win rate for a specific mode or champion?
The League client doesn't expose that data — we can't do it 🥲

### Q: Why does the client crash sometimes?
We suspect the client can't handle certain HTTP requests (it shatters at a touch).

## Building it yourself 📂

### Windows (`.7z`)
PySide6 6.9.3 needs **PyInstaller 6.x** (no longer the old 5.13.2). Create a PySide6 virtual environment (Python 3.9+, the repo uses 3.12):
```powershell
python -m venv .build-venv-win
.build-venv-win\Scripts\activate
pip install -r requirements-build.txt
```
[`requirements-build.txt`](requirements-build.txt) is [`requirements.txt`](requirements.txt) plus the pinned PyInstaller; it is also what the CI builds install.
Build with [`Seraphine.win.spec`](Seraphine.win.spec) (the counterpart of macOS [`Seraphine.spec`](Seraphine.spec): EXE with `logo.ico`, keeps the Windows-only `fix_lcu_window.exe`, bundles `app/resource` as datas):
```powershell
pyinstaller --noconfirm Seraphine.win.spec
```
The output is in `dist\Seraphine\`. PyInstaller 6.x produces an `_internal\` layout (`Seraphine.exe` at the root, the rest under `_internal\`) — that's fine: `main.py` does `os.chdir` to `_MEIPASS` (= `_internal`) at startup and the resources are bundled there, so the relative paths resolve.

Finally, generate a root-level `filelist.txt` (and `filemanifest.txt`, the sha256 and size of every listed file) and pack `dist\Seraphine\*` with **files at the archive root** (no nested top folder) into `Seraphine.7z`, satisfying the **in-place updater** contract (with the package's `filemanifest.txt` it first checks every file against the install and skips the ones that are already identical; it backs up the files it will replace, merges the new files over the install folder and verifies their hashes — restoring the backup if either fails — then deletes files listed in the old `filelist.txt` that the new package no longer contains; a package without `filelist.txt` is refused, one without `filemanifest.txt` is installed as before). This step is [`tools/pack_win.ps1`](tools/pack_win.ps1) (shared by [`release_win.ps1`](release_win.ps1) below and the `build_windows` workflow): it packs with native 7-Zip when installed (`-Packer auto`; CI uses `-Packer 7z`), otherwise with `py7zr`. Then check that the archive extracts with `py7zr` (what the installed app uses) to exactly the build:
```powershell
.\tools\pack_win.ps1 -DistDir dist\Seraphine -Out dist\Seraphine.7z -Python python -Packer auto
python tools\check_archive.py dist\Seraphine.7z dist\Seraphine
```

### macOS (`.dmg`)
In your virtual environment install the build requirements, then run [`make_mac.sh`](make_mac.sh):
```shell
pip install -r requirements-build.txt
./make_mac.sh
```
The script uses [`Seraphine.spec`](Seraphine.spec) to build `Seraphine.app` and produces `dist/Seraphine.dmg` with an `/Applications` drag target (unsigned).

### Updating the Chinese translation
After adding or changing interface text, collect it from every code file into the translation file, fill in the Chinese with Qt Linguist (`pyside6-linguist`), then compile the `.qm` file the app loads:
```shell
pyside6-lupdate $(git ls-files 'app/*.py') -ts app/resource/i18n/Seraphine.zh_CN.ts
pyside6-lrelease app/resource/i18n/Seraphine.zh_CN.ts
```

### One-command macOS release (maintainers)
[`release_mac.sh`](release_mac.sh) automates: bump `VERSION` → build the `.dmg` → verify the frozen build reports the new version → publish to this repo's Releases (`Ezeki3lRchy/Seraphine`, which the in-app updater reads), using your local `gh` login. `--push` is required: the release tag points at the bump commit, so it is pushed right before the release is created:
```shell
./release_mac.sh 1.2.3 --notes-file NOTES.md --push
```
Use `--dry-run` to preview every step without changing anything.

### One-command Windows release (maintainers)
[`release_win.ps1`](release_win.ps1) is the Windows counterpart of `release_mac.sh`: bump `VERSION` → build with `Seraphine.win.spec` → generate `filelist.txt` + `filemanifest.txt` and pack `Seraphine.7z` (native 7-Zip when installed, else `py7zr`) → check the archive extracts with `py7zr` to exactly the build → verify the frozen build's `--version` and `--selftest` → publish to this repo's Releases (`Ezeki3lRchy/Seraphine`) using your local `gh` login. Before publishing it installs the new package over the previous release with the updater of every version that can update itself (and the new one) and stops if any of them would not produce exactly the new build. A created release gets `Seraphine.7z` first and `Seraphine.filemanifest.txt` in a second upload; replacing an existing release's `Seraphine.7z` first removes that version's delta (`Seraphine-<base>-to-<version>.delta`), which only the automatic release workflow builds. When it creates the release from a new bump commit, `-Push` is required (the tag points at that commit, pushed right before the release is created):
```powershell
.\release_win.ps1 1.2.3 -NotesFile NOTES.md -Push
```
The two platforms use a **decoupled release** model: a **single Release** per version tag carries both `Seraphine.7z` (Windows) and `Seraphine.dmg` (macOS); the updater only reads `/releases/latest` and picks the asset matching the OS. So:
- if the version's Release already exists (e.g. macOS shipped it first), the script `gh release upload --clobber`s the `.7z` onto the existing Release and **skips** the `VERSION` bump (`config.py` already holds the target version);
- otherwise it creates the version's Release.

Never use a separate `-win`-style tag — it would steal "latest" and break the other platform's updater. `-DryRun` rehearses a release of the current `VERSION` end to end without publishing: it really builds, packs, checks the archive and runs the frozen exe, skips the version bump and every `gh` call, only warns about a dirty tree, and prints the push and publish it would do (`.\release_win.ps1 1.2.3 -DryRun -AllowDirty`, with `1.2.3` the current `VERSION`). `-NoManifest` packs without `filemanifest.txt` (escape hatch).

**Automatic alternative:** you don't have to run the script at all. Change `VERSION` in `app/common/config.py`, commit and push to `main`; the `release` workflow ([`.github/workflows/release.yml`](.github/workflows/release.yml)) builds `Seraphine.7z` and `Seraphine.dmg` and publishes them as that version's Release (or uploads them onto the Release if it already exists). A file the Release already has is not built again, so it does nothing when both are there. Running it by hand (`gh workflow run release.yml`) fills in whichever file the current version's Release is missing.

The automatic release (`release.yml`) builds beside the tests and publishes each part only when its own checks pass: the Windows package after its tests, its build and an update test over the previous release; the macOS package after its tests and build; the Windows delta (changed files only, for users exactly one version behind) only with the Windows package and after its own equivalence and update checks — a failed delta never holds back the full package. If a release's update code misbehaves in the field: delete the `.delta` asset (every client takes the full package), set `noDelta` / `noResume` in `document/ver.json`, or set `"noManifest": true` in `tools/release_flags.json` in the next release's version-bump commit.

To build without publishing, the `build-windows` and `build-macos` workflows ([`.github/workflows/build_windows.yml`](.github/workflows/build_windows.yml), [`.github/workflows/build_macos.yml`](.github/workflows/build_macos.yml), `workflow_dispatch`) run the same build / pack / verify steps on a hosted runner and publish nothing (for macOS, use `build_macos.yml`, `Seraphine-mac-X.Y.Z` and `Seraphine.dmg` below). Bump and push `VERSION` first, then:
```shell
gh workflow run build_windows.yml -f version=X.Y.Z
gh run download <run-id> -n Seraphine-win-X.Y.Z -D /tmp/win
gh release upload vX.Y.Z /tmp/win/Seraphine.7z --repo Ezeki3lRchy/Seraphine --clobber
```

## Riot disclaimer 📢
Seraphine is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.

## Disclaimer 🛡️
This program consists of source code open-sourced on GitHub and the binaries uploaded to Releases or official groups. This section aims to give users a fuller, more detailed understanding of the program and its possible risks, so they can make an informed risk assessment before and during use.

1. The program's purpose is to provide **out-of-game** assistance for a better experience. We do not encourage or support anything that violates Riot's or Tencent's rules, or that could make the game environment unfair.
2. The implementation complies with the [Riot Policies](https://developer.riotgames.com/policies/general), and the features comply with the requirements of the *League of Legends* game-plugin convention.
3. The program is built on Riot's League Client Update (LCU) API. Its code and behavior contain no intrusive techniques, and in principle do not damage the integrity of the client or game — including, but not limited to, modifying client files or reading/writing game-process memory.
4. We do our best to keep the program and the game client stable, but during specific game environments and Riot/Tencent service updates (e.g. anti-cheat or other protection updates), using the program may negatively affect your experience, such as client crashes or account bans.
5. You bear all consequences of using this program. We are not responsible for any direct or indirect loss arising from its use. When deciding to use it, you should fully consider and bear all resulting risks and consequences yourself.
6. We reserve the right to amend this disclaimer at any time; please check this page periodically for the latest information.

Before using this program, please make sure you have **read**, **understood**, and **agreed to** the terms above; and please follow the relevant game rules to help keep the environment healthy and fair.

## References 👀
- The GUI is built with [PySide6](https://doc.qt.io/qtforpython/) and [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets); async work is bridged into the Qt event loop via [qasync](https://github.com/CabbageDevelopment/qasync).

- LCU API usage and references
  - https://riot-api-libraries.readthedocs.io/en/latest/lcu.html#lcu-explorer
  - https://hextechdocs.dev/tag/lcu/
  - https://developer.riotgames.com/docs/lol
  - https://www.mingweisamuel.com/lcu-schema/tool/#/

  and other excellent projects using the LCU API
  - https://github.com/KebsCS/KBotExt
  - https://github.com/XHXIAIEIN/LeagueCustomLobby
  - https://github.com/7rebux/league-tools

- Locking in-game settings
  - https://www.bilibili.com/video/BV1s84y1x7ub

- Fixing the infinite-spinner / shrunken-window bug
  - https://www.bilibili.com/video/BV1Cw41147iS
  - https://github.com/LeagueTavern/fix-lcu-window

- Ranked match score model (features and weights, MIT)
  - https://github.com/ignaciomigliaro/lol-friends-ladder

- Game assets
  - https://raw.communitydragon.org/latest/
  - https://github.com/CommunityDragon/Docs/blob/master/assets.md

- Fluent Icons
  - https://fluenticons.co/outlined
  - https://github.com/microsoft/fluentui-system-icons/blob/main/icons_regular.md

## Help us improve Seraphine 😘
If you hit any bug or unexpected behavior, please open an [issue](https://github.com/Ezeki3lRchy/Seraphine/issues) (search first to see whether it's already been discussed). Feature requests and changes are equally welcome — [PRs](https://github.com/Ezeki3lRchy/Seraphine/pulls) are very welcome!

## Acknowledgments 🥳
- Thanks to the original [Zzaphkiel/Seraphine](https://github.com/Zzaphkiel/Seraphine) project and all of its contributors — this fork stands on their shoulders to do the cross-platform port.
  <p align='center'>
    <a href="https://github.com/Zzaphkiel/Seraphine/graphs/contributors">
      <img src="https://contrib.rocks/image?repo=Zzaphkiel/Seraphine">
    </a>
  </p>
- Thanks to [大乱斗之家](http://www.jddld.com) for the ARAM buff-info service.

## License ⚖️
- For non-commercial use, Seraphine is licensed under [GPLv3](https://github.com/Ezeki3lRchy/Seraphine/blob/main/LICENSE).
- All commercial use of the code and binaries is prohibited.
