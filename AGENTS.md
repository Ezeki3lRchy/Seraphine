<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-06 | Updated: 2026-06-06 -->

# src-v1.1.4

## Purpose
Originally recovered Seraphine v1.1.4 source (commit `32249c8`, 2024-12-31) from the
`se2o/--Seraphine` fork; the directory name is historical. It has since become the
**cross-platform fork** (origin `Ezeki3lRchy/Seraphine`, now VERSION 1.2.3): the UI was
migrated **PyQt5 → PySide6** and native **macOS** support was added. A desktop app that
queries LoL match history via the local LCU API and (for CN servers) the Tencent SGP gateway.

## Key Files
| File | Description |
|------|-------------|
| `main.py` | Entry point: sets HiDPI, loads config + translators, launches `MainWindow` under a `qasync` event loop. `--version`/`-v` prints the version and exits |
| `Seraphine.win.spec` | **Current** Windows PyInstaller spec (PySide6). Build: `pyinstaller --noconfirm Seraphine.win.spec`. Counterpart of macOS `Seraphine.spec` |
| `release_win.ps1` | One-command Windows release: bump → build via spec → pack (`tools/pack_win.ps1 -Packer auto`: native 7-Zip when installed, with the same plain-LZMA2 check as CI, else py7zr) → `tools/check_archive.py` with the py7zr pin of every self-updating release (`tools/prev_release.py --all-self-updating`, each tag's `requirements.txt` from the local tag or a read-only `gh api` read; a pin equal to the build venv's py7zr reuses it, any other pin gets a scratch venv `%TEMP%\seraphine-py7zr-<pin>`) → update test (`tools/update_e2e.py`, full leg: the new `Seraphine.7z` installed over the previous release's `Seraphine.7z` — `tools/prev_release.py`, downloaded and checked against its API digest by `tools/make_delta.py --fetch-base` — by the updater script of every self-updating version, `git show v<x>:app/common/update.py` or a read-only `gh api` read, and by the new script; runs before the frozen exe runs, also in `-DryRun`) → verify frozen `--version` / `--selftest` (both with `SERAPHINE_CONFIG_DIR` pointed at a fresh temp dir) → `gh release` on this repo (`Ezeki3lRchy/Seraphine`; a created release gets `Seraphine.7z` alone, then `Seraphine.filemanifest.txt` in a second upload; replacing an existing release's `Seraphine.7z` first deletes that version's `Seraphine-*-to-<v>.delta` and then replaces the manifest asset; afterwards `assets[0]` must be `Seraphine.7z` / `Seraphine.dmg`, else the manifest asset is deleted and the script fails; a created release's tag targets HEAD, so `-Push` is required with a new bump commit and runs before publishing). `-DryRun` rehearses the **current** VERSION end to end (it really builds, packs, checks and runs the frozen exe; no bump, no release lookup, a dirty tree only warns; push / publish are printed as `[dry-run]` lines): `.\release_win.ps1 <VERSION> -DryRun -AllowDirty`. In `-DryRun` gh is optional: logged in, it finds the field pins and the previous release as in a real release; without it only the local tags' pins are known (the run stops if one differs from the build venv's py7zr) and the previous release comes from a read-only GET of the public GitHub API, with a warning to run `git fetch --tags` when the newest local tag is below it. `-NoManifest`: escape hatch, packs without `filemanifest.txt` and uploads no manifest asset (deletes an existing one when the package is replaced). The script builds no delta (only `release.yml` does). Does not run CI's BCJ2 reverse step. Mirrors `release_mac.sh` |
| `.github/workflows/build_windows.yml` | `workflow_dispatch` / `workflow_call` Windows `.7z` build on a GitHub-hosted runner (build/pack/verify only, publishes nothing). Top-level `permissions: contents: read`; checks out with `fetch-depth: 0` and `persist-credentials: false`; only the step that runs `tools/prev_release.py --all-self-updating` (read-only gh) gets `GH_TOKEN`, and it writes the versions to `$RUNNER_TEMP\self-updating.txt`. Packs with native 7-Zip (`pack_win.ps1 -Packer 7z`: `7z a -t7z -m0=LZMA2 -mf=off -mx=5 -mmt=on`; pack_win then fails unless every `Method =` line of `7z l -slt` is plain LZMA2); right after packing, before the frozen runs, `tools/check_archive.py` runs once per distinct py7zr pin of those releases (`git show v<x>:requirements.txt`, one scratch venv per pin); a reverse step packs a small tree with `-mf=BCJ2` (7-Zip path taken from pack_win's `sevenzip: <path>` line) and requires both `pack_win.ps1 -CheckMethods` and the extraction check to fail on it and pass on the normal pack. Frozen `--version` / `--selftest` run with `SERAPHINE_CONFIG_DIR=$RUNNER_TEMP\cfg`. The artifact `Seraphine-win-X.Y.Z` holds `Seraphine.7z` and `Seraphine.filemanifest.txt`. Input `no_manifest` (boolean, default false, both triggers): pack without `filemanifest.txt`, check with `--no-manifest`, upload only the `.7z`. Manual use: bump + push `VERSION` first, `gh workflow run build_windows.yml -f version=X.Y.Z`, download the artifact; replacing a release's package: first `gh release delete-asset vX.Y.Z Seraphine-<base>-to-X.Y.Z.delta --repo Ezeki3lRchy/Seraphine -y` for any delta of that version (it was built against the old package), then `gh release upload vX.Y.Z Seraphine.7z --repo Ezeki3lRchy/Seraphine --clobber`, then the same for `Seraphine.filemanifest.txt` (the workflow header lists the commands). Lets a macOS maintainer ship both platforms (first used for v1.3.0) |
| `.github/workflows/build_macos.yml` | `workflow_dispatch` macOS `.dmg` build on a GitHub-hosted Apple Silicon runner: creates `venv/` and runs `make_mac.sh` unchanged, verifies frozen `--version`, uploads artifact `Seraphine-mac-X.Y.Z`; publishes nothing. Unsigned, like the local build |
| `.github/workflows/test.yml` | Unit tests (`python -m unittest discover -s tests -t .`, Qt offscreen) on `windows-latest` + `macos-latest`: runs on push to `main` and on pull requests, and is called by `release.yml` with an `os` input (one runner) so each platform is gated on its own result. The `lint` job (push / pull request only) runs ruff, `tools/check_yaml.py`, `tools/check_release_graph.py` and its reverse cases `python tools/check_release_graph.py --self-test` (they need PyYAML, so they live in the tool, not in `tests/`) |
| `.github/workflows/release.yml` | **Automatic** release for both platforms: a push to `main` that changes `VERSION` in `app/common/config.py` (or `workflow_dispatch`, which fills in what the current version's release is missing; a platform whose file the release already has is skipped). Jobs: `check` (VERSION, which platforms are needed, `no_manifest` from the repo-tracked `tools/release_flags.json`); `test-win` / `test-mac` (`test.yml` per platform) **beside** `build-win` / `build-mac` (both need only `check`; `build-win` passes `no_manifest`); `verify-full-win` (`verify_update_windows.yml` leg `full`: the new `Seraphine.7z` installed over the previous release by every self-updating version's updater script and the new one); `delta-win` (skipped under `no_manifest`, and with a notice when there is no previous release: downloads the previous `Seraphine.7z`, verifies it against its API digest, builds `Seraphine-<prev>-to-<new>.delta` with `tools/make_delta.py`, which proves previous + delta == the new package, and checks it with every field py7zr pin); `verify-delta-win` (leg `delta`: the delta installed by the new script); `publish` (only on `main`, needs all of them, `contents: write`; `GH_TOKEN` only on its upload steps). `publish`, `verify-full-win`, `delta-win` and `verify-delta-win` run on `${{ !cancelled() && ... }}` (not `always()`), so a run cancelled by hand publishes nothing. Publish decisions come only from `tools/release_gates.py` (`toJSON(needs)` + the flag): `Seraphine.7z` needs test-win + build-win + verify-full-win; `Seraphine.dmg` needs test-mac + build-mac (independent of Windows); `Seraphine.filemanifest.txt` comes with the Windows package unless `noManifest`; the delta also needs delta-win + verify-delta-win; a failed delta leg never blocks the Windows package. Upload order: full packages first and alone (`release_gates.py --check-full-names`: only `Seraphine.7z` / `Seraphine.dmg`; create with the tag on the built commit, or upload onto the existing release, where a new `Seraphine.7z` first deletes that version's old `Seraphine-*-to-<v>.delta` and `Seraphine.filemanifest.txt`); then, only next to a `Seraphine.7z` this run uploaded, the manifest asset and the delta in a separate upload (`--check-names`; a failed upload deletes what it uploaded); a final `always()` step (whenever that upload ran, also after a failure or a cancel) reads `assets[0]` from the REST API and, unless it is `Seraphine.7z` or `Seraphine.dmg`, deletes the version's manifest and delta assets and fails the job; a failed read counts as a wrong `assets[0]`, and after a cancelled upload (a partial asset may be left) it deletes them whatever `assets[0]` is. `delta-win` treats only "no previous release" as "no delta"; a gh / network failure of `prev_release.py` fails the job (it blocks only the delta). `verify-delta-win` re-checks the artifact with `make_delta.py --check` before installing it. Uses the built-in `GITHUB_TOKEN`; no secrets |
| `.github/workflows/verify_update_windows.yml` | `workflow_call` (inputs `version`, `leg: full|delta`), called by `release.yml`; publishes nothing. Checks out with `fetch-depth: 0` (each self-updating tag's `app/common/update.py` via `git show`), downloads the previous release's `Seraphine.7z` and verifies it against its API digest (`make_delta.py --fetch-base`), downloads this run's `Seraphine-win-X.Y.Z` (and for `delta` the `Seraphine-delta-win-X.Y.Z`) artifact and runs `tools/update_e2e.py`; writes the runs to the job summary. Its result gates only what it covers |
| `tools/release_flags.json` | `{"noManifest": false}` (the tests check only its schema, so the escape-hatch commit passes them): the escape hatch for push-triggered releases. Set `true` only in the reviewed commit of a release's VERSION bump: that release is packed without `filemanifest.txt` and gets no manifest asset and no delta. Not a repository variable on purpose (a variable change would not be reviewed) |
| `requirements.txt` | Pinned runtime deps (PySide6 6.9.3 + PySide6-Fluent-Widgets, aiohttp 3.10.10, qasync, py7zr, pywin32 on win32, …); the test workflow installs only this |
| `requirements-build.txt` | `-r requirements.txt` + the pinned PyInstaller: the **single source** of the PyInstaller version (both build workflows, `release_win.ps1`'s message, the macOS `venv/` for `make_mac.sh` all install it; `make_mac.sh` itself installs nothing). Bump it here only |
| `tools/` | `read_version.py` (prints `VERSION` from `app/common/config.py`; used by `release_win.ps1`, `release.yml` and the build workflows); `pack_win.ps1` (stops on any error; writes the in-place updater's `filelist.txt` — UTF-8 BOM + CRLF written explicitly so the bytes are the same under Windows PowerShell and PowerShell 7, backslash relative paths, listing neither itself nor `filemanifest.txt` — then, unless `-NoManifest`, `filemanifest.txt` in the same encoding: first line `#seraphine-manifest 1`, then `<sha256 lowercase>\t<size>\t<path>` per filelist entry in filelist order; packs `Seraphine.7z` with `-Packer py7zr` (default, via `pack_7z.py`), `7z` (native 7-Zip, plain LZMA2 without filters, checked after the pack: every `Method =` line of `7z l -slt` must be LZMA2, else exit 1 and no archive; exit 1 if 7-Zip is missing) or `auto`; prints `sevenzip: <path>`, the packer, 7-Zip version, pack time and size; `-CheckMethods <archive>` runs only that method check; shared by `release_win.ps1` and `build_windows.yml`, covered by `tests/test_pack_win.py` and `tests/test_pack_manifest.py`); `check_archive.py <archive> <tree> [--no-manifest] [--partial]` (stdlib + py7zr only: extracts with py7zr and requires the same file set and sha256 as the tree, a valid root `filelist.txt` listing every other file, and a `filemanifest.txt` matching it; `--partial` for a delta, whose lists describe the whole release; `tests/test_check_archive.py`); `prev_release.py [--version X.Y.Z] [--all-self-updating]` (read-only `gh release list` / `gh release view`: the highest `vX.Y.Z` release below VERSION with a `Seraphine.7z`, or every such release from 1.4.6 on; exit 1 when none, on a bad version or a gh failure, 2 on a usage error; `tests/test_prev_release.py`). Tools never import `app.common.util` / `app.common.config`; they load the stdlib-only `app/common/release_names.py` by file path with `importlib.util`. `build_ledger_baseline.py` (stdlib only, read-only GETs; the app itself never calls these sources) rebuilds `app/resource/ledger/ledger_baseline.json` from Riot Data Dragon (ability cooldowns per rank, max ranks, item haste), CommunityDragon's mirror of the game client's data for the same patch (item active and summoner spell cooldowns, incl. Unleashed Teleport) and OP.GG (skill max priority); run it once per patch (`python tools/build_ledger_baseline.py --version <Data Dragon version>`, about 3 minutes: one OP.GG call per champion, 0.5 s apart). It exits 1 when an OP.GG build failed (that champion keeps the previous file's priority) unless `--allow-failures`. Charge-based abilities (client `mMaxAmmo` > 1 recharging slower than the cast lockout), toggles and lockouts under 2 s stay `null`; one recharging charge (Rengar) uses its recharge time; genuine short cooldowns listed in `GENUINE_SHORT` (Akali Q, Cassiopeia E, Aphelios W) keep their value. Values no source gives stay `null` (the ledger cell does nothing); `--manual <file.json>` adds hand-entered summoner spell / item active cooldowns. `make_delta.py <prev> <new> <out>` (prev / new: a directory or a `.7z`; out named `Seraphine-<base>-to-<new>.delta`: the new filelist's changed and new files plus always `Seraphine.exe`, `filelist.txt`, `filemanifest.txt`, packed with py7zr; then copies prev, overlays the packed delta, deletes previous-filelist entries the new one dropped, and requires the result to equal `<new>` file by file, plus `check_archive.py --partial`; any mismatch: exit 1 and no output file; `--check <prev> <new> <delta>` runs the same equivalence check on an existing delta (the release re-checks its artifact with it); `--verify-base <digest> <file>` / `--fetch-base X.Y.Z <dir>` check a previous `Seraphine.7z` against its release API digest (read with `gh api repos/<repo>/releases/tags/vX.Y.Z`: `gh release view --json assets` of gh 2.69 carries no digest); `tests/test_make_delta.py`); `update_e2e.py` (installs a new package and / or delta over a previous one by running real `WINDOWS_UPDATER_SCRIPT`s on temp copies: full leg = every self-updating version's script (`--versions` or `--all-self-updating`; `git show v<x>:app/common/update.py`, else read-only `gh api`) + `--script LABEL=PATH` + the new script; delta leg = the new script, plus the previous one if it carries `# seraphine-updater: manifest 1`; each run must exit 0 with `result.json` `ok` and leave the install equal to the new tree and its manifest; when the previous install has a root `filemanifest.txt`, every run is repeated on a copy without it and on one with a mismatching one; `tests/test_update_e2e.py`); `release_gates.py` (pure publish decisions of `release.yml`, `--flags` for `release_flags.json`, `--check-full-names` / `--check-names` for the two uploads; `tests/test_release_gates.py`, exhaustive truth table + metamorphic properties); `check_release_graph.py` (PyYAML: checks `release.yml` / `build_windows.yml` structure, incl. `!cancelled()` on the late jobs and the publishing steps' script lines in order, and simulates 32,768 scenarios (job outcomes, flag, previous release, ref, hand release, cancel) through the real `if:` expressions and the gate against the publish rules; lint job only; `--self-test` runs its reverse cases on broken copies of `release.yml`) |
| `.github/dependabot.yml` | Monthly `pip` + `github-actions` update PRs (small open-PR limit, grouped); the way the older runtime pins get reviewed. Never auto-merged |
| `pyproject.toml` | Tooling config only: a minimal ruff setup (error-finding rules F/E9/B006/B018/PLE, py312, line length 120, no naming/formatter rules; bare-except `E722` is deliberately not enforced, see the comment there). `ruff check .` is clean and enforced by the `lint` job in `test.yml` (push / pull request only; it does not gate releases) |
| `requirements-dev.txt` | The pinned ruff and PyYAML for the lint job; install it in any venv except the build venv |
| `.editorconfig` | UTF-8, LF (what the index stores; the Windows working tree is CRLF via `core.autocrlf`), 4-space Python |
| `readme.md` | Project README (English); Chinese version in `readme.zh-CN.md` |
| `LICENSE` | Upstream license |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `app/` | Application source — config, LCU/SGP logic, widgets, windows (see `app/AGENTS.md`) |
| `document/` | `notice.md` (in-app notice) + `ver.json` (version/update metadata) |
| `log/` | Runtime log output |
| `.github/` | Issue templates + CI workflows (upstream) |

## For AI Agents

### Working In This Directory
- Build/repack via a **PySide6** venv `..\.build-venv-win` (Python 3.12 +
  `requirements-build.txt`, i.e. the runtime pins plus the pinned **PyInstaller 6.x**). The old PyQt5 `.build-venv` (PyInstaller 5.13) cannot
  build this source. See README §"自己打包可执行文件 / Building it yourself".
- `main.py` `os.chdir`s to `_MEIPASS` at startup. PyInstaller 6.x ships an `_internal\`
  onedir layout (`Seraphine.exe` at root, deps under `_internal\`); resources are bundled
  there too, so relative `app/resource/...` loads resolve. No flat layout required.
- `.7z` packing must place files at the **archive root** with a root `filelist.txt` (in-place
  updater contract; its bytes never change) — `tools/pack_win.ps1` handles this and adds the root
  `filemanifest.txt` (called by `release_win.ps1` and `build_windows.yml`; do not re-inline it).
  Every produced archive must pass `tools/check_archive.py` with the py7zr pin of every self-updating
  version in the field before it is used: the installed app extracts updates with py7zr, which
  cannot read BCJ2 (hence 7-Zip runs with `-mf=off`). 7-Zip is not required locally
  (`-Packer auto` falls back to py7zr).
- `release.yml`'s `publish` job only runs on `main` (a manual run elsewhere builds but does not
  publish) and its checkout does not persist git credentials (`gh` uses `GH_TOKEN`).
- Release graph changes: `tools/check_release_graph.py` (lint job of `test.yml`, needs PyYAML from
  `requirements-dev.txt`) must exit 0. It gates **pushes and pull requests**, not the release run itself:
  that is enough because `release.yml` changes reach `main` only by push / pull request, where the lint
  job runs. A new job in `release.yml` must be taught to it (it refuses unknown jobs), and a change to
  the publishing steps' script lines must be mirrored in its expected lines (it compares them line by
  line). The late jobs and `publish` must stay on `${{ !cancelled() && ... }}` (a bare leading `!` is a
  YAML tag).
- Field switches, fastest first: **delete the `.delta` asset** from a release (immediate: every client
  stages the full package; one that already staged the delta replaces it at its next update check);
  **`document/ver.json`** `noDelta` / `noResume` (JSON `true`) in the running version's entry (no release
  needed; read by `checkUpdate` like `forbidden`); **`tools/release_flags.json` `{"noManifest": true}`**
  (the next push-triggered release ships without `filemanifest.txt`, manifest asset and delta), likewise
  the `build_windows.yml` `no_manifest` input and `release_win.ps1 -NoManifest` for hand releases. A
  package without a manifest takes the pre-manifest path of every updater script (escape hatch).
- Previous package as base / install: always verified against its release API digest first
  (`make_delta.py --fetch-base`); a mismatch or missing digest fails the job (no delta, no full-leg pass).
- Self-update (`app/common/update_stage.py` + `update.py`): packaged builds download, verify and
  stage a newer release in the background (not during a game) under `<config>/update/`, then
  only ask to restart; the download shows its progress on the Settings "Check for updates now"
  card (its button is disabled meanwhile and becomes "Restart to update" once the update is
  ready) and in a closable info bar (closing it does not cancel; a failure becomes a timed
  "Update failed" warning), and a download paused by a game resumes from the partial file
  (HTTP range, full restart when the server does not honour it); the Windows updater script
  gets the install dir (dir of `sys.executable`, since the CWD is `_internal\`) as a parameter
  and does not depend on the CWD. A package with `filemanifest.txt` is checked file by file
  against the install before anything is changed (exit 9 `failed:9` on a mismatch, nothing
  changed) and only differing files are backed up and copied; a package without one takes the
  pre-manifest path unchanged. The manifest itself is never copied into the install: the in-app
  delta precheck reads the `filemanifest.txt` inside the downloaded, digest-verified delta
  package (the separate `Seraphine.filemanifest.txt` asset is published for later use and is
  not read by the app).
  On Windows a release's delta (`Seraphine-<base>-to-<new>.delta`, changed files only) is used only
  from exactly its base version, on the install it was checked against, and only after an in-app
  check that it fits the install; any delta failure stages the full package in the same call.
  `<config>/update-fallback.json` (kept across restarts) stops the delta for a version whose delta
  failed for good or whose delta run failed, and stops automatic staging of a version after two
  distinct `failed:9` runs (the manual-download bar instead). Field switches without a release:
  `noDelta` / `noResume` (JSON `true`) in the running version's `document/ver.json` entry; deleting
  the `.delta` asset from a release makes every client take the full package. Both kill switches
  act at staging: a client that has not staged the delta yet takes the full package; one that has
  already staged it replaces it with the full package at its next update check, but a delta staged
  before that check can still be offered at start-up (its updater run is then guarded by step 4b).
  Downgrades of a `forbidden` running version (dialog only, never in the background) are not
  bounded across restarts: a failed:9 run of an older version is dropped at start-up (it is not
  newer than the running version), so its count starts again; each attempt is the user's choice.
  If `update-fallback.json` can never be written (e.g. a read-only or persistently locked config
  dir), no count or `nodelta` persists: `result.json` is then kept, so every start-up retries the
  recording and clears the stage, and the release is staged again. Each further updater run still
  needs the user to choose "Restart", and a `failed:9` run changes nothing in the install, so this
  residual case cannot loop on its own or damage the install; it only never reaches the
  manual-download bar. Builds <= 1.4.5 cannot self-update (button disabled) and need one manual download.
- Version/beta strings come from `app/common/config.py` (`VERSION`, `BETA`).
- Releases live on this repo (`Ezeki3lRchy/Seraphine`); the in-app updater (`app/common/util.py`
  `Github`) reads its `/releases/latest`, `document/ver.json` and `document/notice.md`.

### Testing Requirements
- Suite: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .` (stdlib unittest, no
  client needed). With the `-t .` above the modules import as `tests.test_x`, so `tests/__init__.py`
  runs first and sets `SERAPHINE_CONFIG_DIR` (and, on Windows, `APPDATA`) to a fresh temp dir before any
  app import: the run never touches the real config. Without `-t .` (modules imported as `test_x`) the
  app is imported first and WOULD read the real config; `tests/test_config_dir.py` asserts that
  `LOCAL_PATH` and the other import-time config paths lie under the override and fails with a message
  telling the reader to use `-t .`.
  `app.common.platform.config_dir()` returns `SERAPHINE_CONFIG_DIR` as-is on every platform when it is
  set to a non-empty **absolute** path (a relative one is ignored: in a frozen build it would resolve
  inside `_internal\`); all config-location code must go through it. Never launch the GUI to verify a change.
- `QT_QPA_PLATFORM=offscreen python main.py --version` / `--selftest` → smoke tests (no client needed).
- `python main.py --selftest` (hidden) → import smoke test: imports the whole app graph, creates a
  QApplication (offscreen by default), loads the zh_CN translator and checks bundled resources,
  prints `selftest OK <version>`; builds no window and starts no loop. The build workflows and
  release scripts run it on the frozen build right after the `--version` check (after packing, since
  it may create `log/` in the bundle). Unit tests: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .`.
- Lint: `pip install -r requirements-dev.txt` (pinned ruff, in any venv but the build venv), then
  `ruff check .` must exit 0 (rules in `pyproject.toml`; CI's `lint` job in `test.yml` runs it).
  After editing anything under `.github/`, run `python tools/check_yaml.py` as well: a workflow file
  that does not parse is rejected by GitHub before any job runs (an unquoted step name containing
  `: ` did exactly that once), and only the lint job of another workflow can report it.
- Full run needs a logged-in LoL client (LCU). CN match history additionally needs a CN
  client session (SGP token).
- Release tooling: `tests/test_release_gates.py` (13,122-row truth table against an independent oracle, plus metamorphic properties over every row),
  `tests/test_make_delta.py` (equivalence pass + reverse cases: flipped byte, missing changed / new file,
  skipped stale deletion; `--verify-base` / `--fetch-base` digest checks with a stand-in gh),
  `tests/test_update_e2e.py` and `tests/test_delta_app_e2e.py` (Windows: real updater scripts on temp
  trees; the app's `stageRelease` with a real delta), `tests/test_release_win_tools.py` (Windows:
  `pack_win.ps1 -CheckMethods` with trailing spaces, `release_win.ps1`'s tag-lag warning through the
  PowerShell parser). Unit tests read only the committed legacy snapshots; the release gate is
  `update_e2e.py` with real `git show` of every self-updating tag in `verify_update_windows.yml`.
  After editing `release.yml` or `build_windows.yml`: `python tools/check_release_graph.py` and
  `python tools/check_release_graph.py --self-test` (in a venv with `requirements-dev.txt`; the
  self-test cases must be updated when the lines they break change).

### Common Patterns
- asyncio coroutines bridged into Qt via `qasync`; cross-component messaging via a
  `SignalBus` (`app/common/signals.py`).
- Error handling: after start-up, uncaught exceptions are logged and shown as a non-modal notice
  (`app/common/error_policy.py`) instead of ending the app; that path logs the full traceback. Log
  lines you write in an `except` block carry the exception **type name only** (see `errorSummary` in
  `app/lol/connector.py`), never its `repr`: an aiohttp error's repr includes the request headers with
  the client token, and its message can carry a player id. A bare `except:` around an `await` must
  re-raise `asyncio.CancelledError`, and the app's own exceptions in `app/lol/exceptions.py` subclass
  `BaseException`.

## Dependencies

### Internal
- `app/` — all real logic lives here

### External
- PySide6 + PySide6-Fluent-Widgets + PySideSix-Frameless-Window, aiohttp, qasync, psutil,
  py7zr, pyperclip, async-lru, pywin32 (Windows only)

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
