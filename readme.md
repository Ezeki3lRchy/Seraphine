<p align='center'>
  <!-- Hero is the upstream Seraphine wordmark, vendored into this repo (not hotlinked). Consider replacing it with a real macOS screenshot later — shot list in docs/screenshots/README.md. -->
  <img src="docs/screenshots/hero.png" alt="Seraphine" width="720">
</p>

<p align='center'>
  <b>Mature · Complete · Native — the League of Legends assistant for Mac players</b><br>
  Match history · champ-select automation · auto-scout all 10 players · one-click OP.GG runes &amp; builds · ARAM buffs — everything you get on Windows, now on macOS
</p>

<div align="center">

  [![License](https://img.shields.io/github/license/Ezeki3lRchy/Seraphine?style=flat&label=License)](https://github.com/Ezeki3lRchy/Seraphine/blob/main/LICENSE)
  [![Forks](https://img.shields.io/github/forks/Ezeki3lRchy/Seraphine?style=flat&label=Forks)](https://github.com/Ezeki3lRchy/Seraphine/forks)
  [![Stars](https://img.shields.io/github/stars/Ezeki3lRchy/Seraphine?style=flat&label=Stars)](https://github.com/Ezeki3lRchy/Seraphine/stargazers)
  [![Release](https://img.shields.io/github/v/release/Ezeki3lRchy/Seraphine-releases?style=flat&label=Release)](https://github.com/Ezeki3lRchy/Seraphine-releases/releases/latest)

</div>

<p align='center'>
  <b>English</b> · <a href="readme.zh-CN.md">简体中文</a>
</p>

> This repository is a **cross-platform fork** of [Zzaphkiel/Seraphine](https://github.com/Zzaphkiel/Seraphine): it keeps all of the original features, migrates the UI from PyQt5 to **PySide6**, and adds **native macOS support** (`.app` / `.dmg` packaging and a platform-aware self-updater). Windows behavior matches upstream.

## Highlights ✨
- 🎮 **Champ select on autopilot**: auto-accept matches, auto-pick and auto-ban in champ select, auto-accept champion-swap requests.
- 📊 **Auto-scout all 10 players**: teammates the moment you enter champ select, opponents once the game starts, plus on-demand lookups for any same-server summoner.
- 🧠 **One-click OP.GG runes &amp; builds**: auto-show tier lists, builds and skill order, apply runes in one click; ARAM champion buffs shown automatically.
- 🍏 **Native macOS / Apple Silicon**: drag the `.dmg` and go, in-app **Update and Restart**, no Windows environment required.

<p align='center'>
  <a href="https://github.com/Ezeki3lRchy/Seraphine-releases/releases/latest"><b>⬇️ Download for macOS / Windows</b></a>
</p>

## Getting started 🤗

### Use a prebuilt package
Go to the [**latest release page**](https://github.com/Ezeki3lRchy/Seraphine-releases/releases/latest) and download the package for your platform from Assets:

| Platform | Package | How to use |
|----------|---------|------------|
| **Windows** | `Seraphine.7z` | Extract anywhere, then double-click `Seraphine.exe` |
| **macOS** | `Seraphine.dmg` | Open the `.dmg` and drag `Seraphine.app` into `Applications` |

> **First launch on macOS**: the `.app` is currently an **unsigned** build, so Gatekeeper quarantines it after download. Run this once in Terminal to clear the quarantine:
> ```shell
> xattr -cr /Applications/Seraphine.app
> ```
> After that just double-click to launch; later versions can be installed in-app via **Update and Restart**.

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
- **Platform-aware self-update**: the built-in updater reads from a single release repository and downloads only the installer that matches your OS (`.dmg` on macOS, `.7z` on Windows) — it never pushes the other platform's package to you.
- **One-command macOS release**: [`release_mac.sh`](release_mac.sh) handles the whole flow — bump version → build the `.dmg` → verify → publish a GitHub Release.

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
> ⚠️ The old `make.ps1` (bare `pyinstaller` + PyQt5 flat layout + pinned PyInstaller 5.13) is the **pre-migration** script and **cannot** package the current PySide6 source — don't use it. The path below is the Windows counterpart of the macOS `.spec`.

PySide6 6.9.3 needs **PyInstaller 6.x** (no longer the old 5.13.2). Create a PySide6 virtual environment (Python 3.9+, the repo uses 3.12):
```powershell
python -m venv .build-venv-win
.build-venv-win\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller==6.21
```
Build with [`Seraphine.win.spec`](Seraphine.win.spec) (the counterpart of macOS [`Seraphine.spec`](Seraphine.spec): EXE with `logo.ico`, keeps the Windows-only `fix_lcu_window.exe`, bundles `app/resource` as datas):
```powershell
pyinstaller --noconfirm Seraphine.win.spec
```
The output is in `dist\Seraphine\`. PyInstaller 6.x produces an `_internal\` layout (`Seraphine.exe` at the root, the rest under `_internal\`) — that's fine: `main.py` does `os.chdir` to `_MEIPASS` (= `_internal`) at startup and the resources are bundled there, so the relative paths resolve.

Finally, generate a root-level `filelist.txt` and use `py7zr` to pack `dist\Seraphine\*` with **files at the archive root** (no nested top folder) into `Seraphine.7z`, satisfying the **v1.1.9 in-place updater** contract (it deletes old files per the old `filelist.txt`, then moves the new root-level files in). This step is automated by [`release_win.ps1`](release_win.ps1) below.

### macOS (`.dmg`)
In your virtual environment install PyInstaller, then run [`make_mac.sh`](make_mac.sh):
```shell
pip install pyinstaller
./make_mac.sh
```
The script uses [`Seraphine.spec`](Seraphine.spec) to build `Seraphine.app` and produces `dist/Seraphine.dmg` with an `/Applications` drag target (unsigned).

### One-command macOS release (maintainers)
[`release_mac.sh`](release_mac.sh) automates: bump `VERSION` → build the `.dmg` → verify the frozen build reports the new version → publish to `Ezeki3lRchy/Seraphine-releases` (the public repo the in-app updater reads), using your local `gh` login:
```shell
./release_mac.sh 1.2.3 --notes-file NOTES.md --push
```
Use `--dry-run` to preview every step without changing anything.

### One-command Windows release (maintainers)
[`release_win.ps1`](release_win.ps1) is the Windows counterpart of `release_mac.sh`: bump `VERSION` → build with `Seraphine.win.spec` → verify the frozen build's `--version` → generate `filelist.txt` and pack `Seraphine.7z` with `py7zr` → publish to `Ezeki3lRchy/Seraphine-releases` using your local `gh` login:
```powershell
.\release_win.ps1 1.2.3 -NotesFile NOTES.md -Push
```
The two platforms use a **decoupled release** model: a **single Release** per version tag carries both `Seraphine.7z` (Windows) and `Seraphine.dmg` (macOS); the updater only reads `/releases/latest` and picks the asset matching the OS. So:
- if the version's Release already exists (e.g. macOS shipped it first), the script `gh release upload --clobber`s the `.7z` onto the existing Release and **skips** the `VERSION` bump (`config.py` already holds the target version);
- otherwise it creates the version's Release.

Never use a separate `-win`-style tag — it would steal "latest" and break the other platform's updater. Use `-DryRun` to preview every step.

No Windows machine at hand? The `build-windows` GitHub Actions workflow ([`.github/workflows/build_windows.yml`](.github/workflows/build_windows.yml), `workflow_dispatch`) runs the same build / pack / verify steps on a hosted runner and publishes nothing. Bump and push `VERSION` first, then:
```shell
gh workflow run build_windows.yml -f version=X.Y.Z
gh run download <run-id> -n Seraphine-win-X.Y.Z -D /tmp/win
gh release upload vX.Y.Z /tmp/win/Seraphine.7z --repo Ezeki3lRchy/Seraphine-releases --clobber
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
