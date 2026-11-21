<#
.SYNOPSIS
    release_win.ps1 -- one-command Windows release. Windows counterpart of release_mac.sh.

    Bumps VERSION (only when needed), builds the .7z via Seraphine.win.spec, generates
    filelist.txt + filemanifest.txt and packs Seraphine.7z (native 7-Zip when installed,
    else py7zr; a 7-Zip archive must be plain LZMA2), checks the archive extracts to
    exactly the build with the py7zr pin of every self-updating release
    (tools\check_archive.py, like build_windows.yml), installs the new package over the
    previous release (its Seraphine.7z, downloaded and verified against its API digest)
    with the updater script of every self-updating version and the new one
    (tools\update_e2e.py, the full leg of CI's verify_update_windows.yml), verifies the
    frozen build reports the new version and passes --selftest, and publishes a GitHub
    Release on this (public) source repo, Ezeki3lRchy/Seraphine, whose releases the
    in-app auto-updater reads. Uses your local `gh` login -- no PAT, no CI minutes.

    Assets: a created release gets Seraphine.7z alone, then Seraphine.filemanifest.txt in
    a second upload (every build that reads this repository's releases takes the first
    .7z / .dmg asset); afterwards the first .7z must be Seraphine.7z and the first .dmg
    Seraphine.dmg (tools\release_gates.py --check-first-packages), else the
    manifest asset is deleted again and the script fails. Replacing the Seraphine.7z of an
    existing release first deletes that version's delta (Seraphine-<base>-to-<v>.delta,
    built against the old package) and then replaces the manifest asset. This script
    builds no delta (release.yml does).

    A newly CREATED release's tag is pinned to the built commit (--target <HEAD sha>), so
    that commit must be on GitHub first: when a release will be created, -Push is required
    if a bump commit is made, and the push happens right before the release is created
    (not at the end). Without a bump, HEAD must already be on GitHub (or pass -Push).
    Pre-flight fails early, before the build, if that does not hold.

    DECOUPLED RELEASE MODEL: one GitHub Release per version tag holds BOTH Seraphine.7z
    (Windows) and Seraphine.dmg (macOS). The updater only reads /releases/latest and
    selects the asset matching the OS. So this script:
      - if the version's Release already exists (e.g. macOS shipped it first), UPLOADS the
        .7z onto it (gh release upload --clobber) and SKIPS the VERSION bump (config.py
        already holds the target version);
      - otherwise CREATES the version's Release.
    A separate "-win" tag would steal "latest" and break the macOS updater -- never do that.

.PARAMETER Version
    Target version, X.Y.Z (positional). Must match the GitHub release tag (vX.Y.Z) and the
    VERSION in app/common/config.py that the frozen build reports.

.PARAMETER Notes
    Release notes (markdown), shown in the in-app updater. Used only when CREATING a release.

.PARAMETER NotesFile
    Read release notes from a file instead of -Notes.

.PARAMETER Push
    Push the current branch (git push origin HEAD) right before publishing. Required when
    the release is created from a new bump commit (see above).

.PARAMETER Yes
    Skip the confirmation prompt (non-interactive).

.PARAMETER AllowDirty
    Don't abort on an unclean working tree (not recommended; the release builds from the tree).

.PARAMETER DryRun
    Rehearse a release of the CURRENT VERSION end to end without changing anything
    outside dist\ and build\: Version must equal VERSION in app/common/config.py (no
    bump), the release lookup is skipped, a dirty tree only warns; then really builds,
    packs, checks the archive and runs the frozen exe's --version and --selftest, and
    prints "[dry-run]" lines for the push and publish. The update test over the
    previous release runs as in a real release. gh is optional: when it is logged in,
    it is used (read-only) to find every self-updating release's py7zr pin and the
    previous release, as in a real release; without it only the local tags' pins are
    known (the run stops if any of them differs from the build venv's py7zr), and the
    previous release comes from a read-only GET of the public GitHub API (with a
    warning when the newest local tag is below it: run git fetch --tags).

.PARAMETER NoManifest
    Escape hatch: pack without filemanifest.txt (tools\pack_win.ps1 -NoManifest), a
    package exactly like the ones before the manifest existed; no manifest asset is
    uploaded (an existing one of the release is deleted when its package is replaced).

.PARAMETER Venv
    Path to the PySide6 build venv (Python 3.12 + requirements-build.txt: the runtime
    requirements plus the pinned PyInstaller).
    Default: ..\.build-venv-win  (the PyQt5 .build-venv CANNOT build this source).

.EXAMPLE
    .\release_win.ps1 1.2.3 -NotesFile NOTES.md -Push

.EXAMPLE
    .\release_win.ps1 1.2.3 -DryRun -AllowDirty
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Version,
    [string]$Notes = "",
    [string]$NotesFile = "",
    [switch]$Push,
    [switch]$Yes,
    [switch]$AllowDirty,
    [switch]$DryRun,
    [switch]$NoManifest,
    [string]$Venv = "..\.build-venv-win"
)

# Run from the repo root (this script's directory). Don't set ErrorActionPreference=Stop
# globally -- native exes (gh, pyinstaller, git) are guarded via $LASTEXITCODE below, and
# Stop would turn their stderr into terminating errors in Windows PowerShell 5.1.
Set-Location $PSScriptRoot

# ---- config -----------------------------------------------------------------
$RepoReleases = "Ezeki3lRchy/Seraphine"   # this public source repo; the updater reads its releases
$Config       = "app\common\config.py"
$Spec         = "Seraphine.win.spec"
$DistDir      = "dist\Seraphine"
$SevenZ       = "dist\Seraphine.7z"
$ExeBin       = "dist\Seraphine\Seraphine.exe"
$ManifestAsset = "dist\Seraphine.filemanifest.txt"   # the package's filemanifest.txt under its asset name
$Py           = Join-Path $Venv "Scripts\python.exe"
$PyInstaller  = Join-Path $Venv "Scripts\pyinstaller.exe"

function Die($m)  { Write-Host "error: $m" -ForegroundColor Red; exit 1 }
function Note($m) { Write-Host "==> $m" -ForegroundColor Cyan }

# py7zr pin (X.Y.Z) in requirements.txt of release tag v<v>: from the local tag, else
# (when gh may be used) a read-only GitHub API read of this repo; $null if neither.
function Get-Py7zrPin([string]$v, [bool]$UseGh) {
    $req = @(git show "v${v}:requirements.txt" 2>$null)
    if ($LASTEXITCODE -ne 0 -or $req.Count -eq 0) {
        if (-not $UseGh) { return $null }
        $req = @(gh api "repos/$RepoReleases/contents/requirements.txt?ref=v$v" -H "Accept: application/vnd.github.raw" 2>$null)
        if ($LASTEXITCODE -ne 0) { return $null }
    }
    foreach ($line in $req) { if ($line -match '^\s*py7zr\s*==\s*([^\s;#]+)') { return $Matches[1] } }
    return $null
}

# The installed app extracts updates with py7zr: the archive must extract to exactly
# the build with the py7zr pin of every self-updating version in the field (like
# build_windows.yml). A pin equal to the build venv's py7zr reuses that venv; any
# other pin gets its own scratch venv under %TEMP% (outside the repo, reused later).
function Test-ArchiveForField([string[]]$CheckArgs, [bool]$UseGh) {
    $buildPin = "$(& $Py -c 'import py7zr; print(py7zr.__version__)')".Trim()
    if (-not $buildPin) { Die "cannot read the build venv's py7zr version" }
    if ($UseGh) {
        $versions = @(& $Py "tools\prev_release.py" --version $Version --all-self-updating)
        if ($LASTEXITCODE -ne 0 -or $versions.Count -eq 0) { Die "prev_release.py found no self-updating release below $Version" }
        $source = "self-updating releases"
    }
    else {
        $versions = @(git tag --list 'v*' |
            ForEach-Object { if ($_ -match '^v(\d+\.\d+\.\d+)$') { $Matches[1] } } |
            Where-Object { [version]$_ -ge [version]'1.4.6' -and [version]$_ -lt [version]$Version } |
            Sort-Object { [version]$_ })
        if ($versions.Count -eq 0) { Die "no local self-updating release tag below $Version (and gh unavailable)" }
        $source = "self-updating releases (local tags; gh unavailable)"
    }
    Write-Host "    ${source}: $($versions -join ', ')"
    $pins = @()
    foreach ($v in $versions) {
        $pin = Get-Py7zrPin $v $UseGh
        if (-not $pin) { Die "found no py7zr==<version> pin in requirements.txt of v$v" }
        Write-Host "    v${v}: py7zr==$pin"
        $pins += $pin
    }
    $pins = @($pins | Sort-Object -Unique)
    if (-not $UseGh) {
        $other = @($pins | Where-Object { $_ -ne $buildPin })
        if ($other.Count -gt 0) {
            Die "-DryRun without gh: the field uses py7zr $($pins -join ', ') but the build venv has $buildPin; run gh auth login so every pin gets its own check"
        }
        Write-Host "    note: single-pin check (py7zr $buildPin, the build venv's); without gh only the local tags' pins are known"
    }
    foreach ($pin in $pins) {
        if ($pin -eq $buildPin) {
            $checkPy = $Py
            Write-Host "    py7zr ${pin}: the build venv"
        }
        else {
            $venvDir = Join-Path ([System.IO.Path]::GetTempPath()) "seraphine-py7zr-$pin"
            $checkPy = Join-Path $venvDir "Scripts\python.exe"
            if (-not (Test-Path $checkPy)) {
                & $Py -m venv $venvDir
                if ($LASTEXITCODE -ne 0) { Die "creating a scratch venv for py7zr $pin failed" }
            }
            & $checkPy -c "import sys, py7zr; sys.exit(py7zr.__version__ != '$pin')" 2>$null
            if ($LASTEXITCODE -ne 0) {
                & $checkPy -m pip install --disable-pip-version-check -q "py7zr==$pin"
                if ($LASTEXITCODE -ne 0) { Die "pip install py7zr==$pin into $venvDir failed" }
            }
            Write-Host "    py7zr ${pin}: scratch venv $venvDir"
        }
        & $checkPy "tools\check_archive.py" @CheckArgs
        if ($LASTEXITCODE -ne 0) { Die "py7zr $pin cannot extract $SevenZ to exactly $DistDir -- aborting before publish" }
    }
}

# A warning (or $null) when the newest local vX.Y.Z tag is below the previous
# release: the local tags are stale, so without gh the newer releases' py7zr pins
# and updater scripts are unknown here.
function Get-TagLagWarning([string[]]$Tags, [string]$Previous) {
    $local = @($Tags | ForEach-Object { if ("$_" -match '^v(\d+\.\d+\.\d+)$') { [version]$Matches[1] } } | Sort-Object)
    $newest = if ($local.Count -gt 0) { $local[-1] } else { $null }
    if ($null -ne $newest -and $newest -ge [version]$Previous) { return $null }
    $have = if ($null -ne $newest) { "v$newest" } else { "none" }
    return "the newest local release tag ($have) is below the previous release v$Previous -- run git fetch --tags, or the newer releases' py7zr pins and updater scripts are missed"
}

# The releases from the public GitHub API (read-only GET, no gh): every published
# vX.Y.Z below $Below with a Seraphine.7z asset, lowest first.
function Get-PublicReleases([string]$Below) {
    $all = @()
    for ($page = 1; $page -le 10; $page++) {
        $batch = @(Invoke-RestMethod -Uri "https://api.github.com/repos/$RepoReleases/releases?per_page=100&page=$page" `
            -Headers @{ 'User-Agent' = 'seraphine-release-dry-run'; 'Accept' = 'application/vnd.github+json' } -ErrorAction Stop)
        $all += $batch
        if ($batch.Count -lt 100) { break }
    }
    return @($all | Where-Object {
            -not $_.draft -and -not $_.prerelease -and "$($_.tag_name)" -match '^v(\d+\.\d+\.\d+)$' -and
            [version]$Matches[1] -lt [version]$Below -and @($_.assets | Where-Object { $_.name -eq 'Seraphine.7z' }).Count -gt 0
        } | Sort-Object { [version]("$($_.tag_name)".Substring(1)) })
}

# ---- pre-flight -------------------------------------------------------------
Note "pre-flight checks"
if (-not $Version) { Die "version required, e.g. .\release_win.ps1 1.2.3" }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { Die "version must be X.Y.Z (got '$Version')" }
if (-not (Test-Path $Py))          { Die "$Py not found -- create the PySide6 venv and install requirements first" }
if (-not (Test-Path $PyInstaller)) { Die "pyinstaller not in venv ($PyInstaller) -- pip install -r requirements-build.txt" }
$ghUsable = $false
if ($DryRun) {
    # gh is optional here: used only (read-only) to find the field's py7zr pins
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        gh auth status 1>$null 2>$null
        $ghUsable = ($LASTEXITCODE -eq 0)
    }
    $ghNote = if ($ghUsable) { "gh available: used read-only for the field's py7zr pins" } else { "gh unavailable: py7zr pins from local tags only" }
    Write-Host "[dry-run] release lookup skipped; $ghNote"
}
else {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Die "gh (GitHub CLI) not installed" }
    gh auth status 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { Die "gh not authenticated -- run: gh auth login" }
}

$current = & $Py "tools\read_version.py" $Config
if ($LASTEXITCODE -ne 0 -or -not $current) { Die "could not read current VERSION from $Config" }
$current = "$current".Trim()

if ($DryRun) {
    if ($current -ne $Version) { Die "-DryRun builds the current VERSION ($current in $Config), got $Version" }
    Write-Host "    VERSION: $Version (current; the dry run builds it as is)"
    $bumpNeeded = $false
}
elseif ($current -eq $Version) {
    Note "VERSION already $Version in $Config -- skipping bump (decoupled release: another platform set it)"
    $bumpNeeded = $false
}
else {
    $bumpNeeded = $true
    Write-Host "    current VERSION: $current  ->  new: $Version"
}

if ($NotesFile) {
    if (-not (Test-Path $NotesFile)) { Die "notes file not found: $NotesFile" }
    # -Encoding UTF8: Windows PowerShell 5.1 otherwise reads a BOM-less file in the
    # system code page (e.g. GBK), garbling non-ASCII notes (Chinese, arrows, dashes).
    $Notes = Get-Content -Path $NotesFile -Raw -Encoding UTF8
}
if (-not $Notes) {
    $Notes = "## v$Version`n`nMaintenance release. Use **Update and Restart** to install."
}

$dirty = git status --porcelain
if (-not $dirty)        { Write-Host "    working tree: clean" }
elseif ($AllowDirty)    { Write-Host "    working tree: DIRTY (-AllowDirty set, continuing)" }
elseif ($DryRun)        { Write-Host "    working tree: DIRTY (warning only in -DryRun; a real release needs a clean tree or -AllowDirty)" -ForegroundColor Yellow }
else { Die "working tree not clean -- commit your changes first (the release builds from the tree). Use -AllowDirty to override." }

$releaseExists = $false
$releaseAssets = @()
if (-not $DryRun) {
    gh release view "v$Version" --repo $RepoReleases 1>$null 2>$null
    $releaseExists = ($LASTEXITCODE -eq 0)
    if ($releaseExists) {
        $releaseAssets = @(gh release view "v$Version" --repo $RepoReleases --json assets --jq '.assets[].name')
        if ($LASTEXITCODE -ne 0) { Die "cannot list the assets of v$Version" }
        Write-Host "    release v$($Version): EXISTS in $RepoReleases -> will upload .7z onto it"
    }
    else { Write-Host "    release v$($Version): not found -> will create it" }
}

# The update test installs the new package over the previous release: find it now,
# not after the build.
$prevPublic = $null
if ($ghUsable -or -not $DryRun) {
    $prevVersion = "$(& $Py "tools\prev_release.py" --version $Version)".Trim()
    if ($LASTEXITCODE -ne 0 -or -not $prevVersion) { Die "prev_release.py found no release below $Version with Seraphine.7z -- the update test installs over it" }
    $selfUpdating = @(& $Py "tools\prev_release.py" --version $Version --all-self-updating)
    if ($LASTEXITCODE -ne 0 -or $selfUpdating.Count -eq 0) { Die "prev_release.py found no self-updating release below $Version" }
}
else {
    try { $public = @(Get-PublicReleases $Version) }
    catch { Die "-DryRun without gh: cannot read the releases from the public GitHub API ($($_.Exception.GetType().Name)); the update test needs the previous release" }
    if ($public.Count -eq 0) { Die "no release below $Version with Seraphine.7z -- the update test installs over it" }
    $prevPublic = $public[-1]
    $prevVersion = "$($prevPublic.tag_name)".Substring(1)
    $selfUpdating = @($public | ForEach-Object { "$($_.tag_name)".Substring(1) } | Where-Object { [version]$_ -ge [version]'1.4.6' })
    $lag = Get-TagLagWarning @(git tag --list 'v*') $prevVersion
    if ($lag) { Write-Host "    warning: $lag" -ForegroundColor Yellow }
}
Write-Host "    previous release: v$prevVersion (self-updating: $($selfUpdating -join ', '))"

# Creating the release tags the built commit (--target <sha>); GitHub must have that
# commit by then. Check now, not after a ~10 minute build.
if (-not $DryRun -and -not $releaseExists -and -not $Push) {
    if ($bumpNeeded) {
        Die "creating v$Version tags the new bump commit, which must be pushed first -- rerun with -Push (it pushes right before the release is created)"
    }
    $headSha = (git rev-parse HEAD).Trim()
    gh api "repos/$RepoReleases/commits/$headSha" 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        Die "HEAD $headSha is not on GitHub ($RepoReleases); the release tag would point at a commit GitHub doesn't have -- push it first or rerun with -Push"
    }
    Write-Host "    HEAD $($headSha.Substring(0,7)): on GitHub -> release tag will target it"
}

# ---- temp helper scripts ----------------------------------------------------
$bumpPy = @'
import re, sys
ver = sys.argv[1]
p = "app/common/config.py"
s = open(p, encoding="utf-8").read()
s2, n = re.subn(r'^VERSION = "[^"]*"', 'VERSION = "%s"' % ver, s, flags=re.M)
if n != 1:
    sys.exit("expected exactly one VERSION line, found %d" % n)
open(p, "w", encoding="utf-8", newline="").write(s2)
'@

$bumpPath = Join-Path $env:TEMP "seraphine_bump.py"
Set-Content -Path $bumpPath -Value $bumpPy -Encoding UTF8

try {
    # ---- bump + commit (only if VERSION != target) --------------------------
    if ($bumpNeeded) {
        Note "bumping VERSION $current -> $Version in $Config"
        if ($DryRun) {
            Write-Host "[dry-run] would set VERSION = `"$Version`" and commit 'release: bump VERSION to $Version'"
        }
        else {
            & $Py $bumpPath $Version
            if ($LASTEXITCODE -ne 0) { Die "VERSION bump failed" }
            git add $Config;                                      if ($LASTEXITCODE -ne 0) { Die "git add failed" }
            git commit -q -m "release: bump VERSION to $Version"; if ($LASTEXITCODE -ne 0) { Die "git commit failed" }
            Write-Host "    committed bump"
        }
    }

    # ---- build (also in -DryRun: the rehearsal builds for real) ---------------
    Note "building (pyinstaller --noconfirm $Spec)"
    & $PyInstaller --noconfirm $Spec
    if ($LASTEXITCODE -ne 0) { Die "pyinstaller failed" }

    # ---- filelist + manifest + pack + archive check ---------------------------
    Note "generating filelist.txt$(if (-not $NoManifest) { ' + filemanifest.txt' }) and packing $SevenZ"
    if (-not (Test-Path $DistDir)) { Die "build did not produce $DistDir" }
    # tools\pack_win.ps1 (shared with the build_windows.yml workflow) writes the
    # updater-contract filelist.txt (+ filemanifest.txt) and packs the archive root:
    # native 7-Zip when installed, else py7zr.
    & (Join-Path $PSScriptRoot "tools\pack_win.ps1") -DistDir $DistDir -Out $SevenZ -Python $Py -Packer auto -NoManifest:$NoManifest
    if ($LASTEXITCODE -ne 0) { Die "filelist/pack step failed" }
    if (-not (Test-Path $SevenZ)) { Die "pack did not produce $SevenZ" }
    # Right after the pack, before the frozen runs below (they may create log\ in the
    # build dir): the archive must extract with py7zr to exactly the build.
    Note "checking $SevenZ extracts to exactly $DistDir with the py7zr of every self-updating release"
    $checkArgs = @($SevenZ, $DistDir)
    if ($NoManifest) { $checkArgs += "--no-manifest" }
    Test-ArchiveForField $checkArgs ((-not $DryRun) -or $ghUsable)
    if ($NoManifest) { Remove-Item -LiteralPath $ManifestAsset -ErrorAction SilentlyContinue }
    else { Copy-Item -LiteralPath (Join-Path $DistDir "filemanifest.txt") -Destination $ManifestAsset -Force -ErrorAction Stop }

    # ---- the new package installed over the previous release (before the frozen
    #      runs too: they may create log\ in the build dir, the expected tree) ------
    Note "update test: $SevenZ over v$prevVersion with every self-updating version's updater script and the new one"
    $prevDir = Join-Path ([System.IO.Path]::GetTempPath()) ("seraphine-release-prev-" + [guid]::NewGuid().ToString('N'))
    try {
        if ($null -eq $prevPublic) {
            & $Py "tools\make_delta.py" --fetch-base $prevVersion $prevDir --repo $RepoReleases
            if ($LASTEXITCODE -ne 0) { Die "the previous package v$prevVersion does not match its API digest -- aborting before publish" }
        }
        else {
            $asset = @($prevPublic.assets | Where-Object { $_.name -eq 'Seraphine.7z' })[0]
            New-Item -ItemType Directory -Force -Path $prevDir | Out-Null
            $prevFile = Join-Path $prevDir "Seraphine.7z"
            try { Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $prevFile -UseBasicParsing -Headers @{ 'User-Agent' = 'seraphine-release-dry-run' } -ErrorAction Stop }
            catch { Die "downloading Seraphine.7z of v$prevVersion failed ($($_.Exception.GetType().Name))" }
            & $Py "tools\make_delta.py" --verify-base "$($asset.digest)" $prevFile
            if ($LASTEXITCODE -ne 0) { Die "the previous package v$prevVersion does not match its API digest -- aborting" }
        }
        & $Py "tools\update_e2e.py" --prev-package (Join-Path $prevDir "Seraphine.7z") --new-package $SevenZ `
            --new-tree $DistDir --versions ($selfUpdating -join ',') --version $Version --leg full --repo $RepoReleases
        if ($LASTEXITCODE -ne 0) { Die "update test failed: a self-updating version would not install $SevenZ correctly -- aborting before publish" }
    }
    finally {
        Remove-Item -LiteralPath $prevDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # The frozen runs below import the app, which loads <config dir>\config.json:
    # point the config dir at a fresh temp dir so they never read or write the real
    # %APPDATA%\Seraphine.
    $cfgDir = Join-Path ([System.IO.Path]::GetTempPath()) ("seraphine-release-cfg-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $cfgDir | Out-Null
    $savedCfg = $env:SERAPHINE_CONFIG_DIR
    $env:SERAPHINE_CONFIG_DIR = $cfgDir
    try {
        # ---- verify frozen build reports the new version --------------------
        Note "verifying the frozen exe reports $Version"
        $reported = "$(& $ExeBin --version | Select-Object -Last 1)".Trim()
        if ($reported -ne $Version) { Die "frozen exe reports '$reported', expected '$Version' -- aborting before publish" }
        $sizeMB = "{0:N0} MB" -f ((Get-Item $SevenZ).Length / 1MB)
        Write-Host "    frozen exe reports: $reported OK ($sizeMB .7z)"

        # ---- self-test the frozen exe (after the pack: it may create log\ in the
        #      build dir, which must not end up in the .7z) ----------------------
        Note "self-testing the frozen exe (--selftest)"
        $env:QT_QPA_PLATFORM = "offscreen"
        try { $selftestOut = (& $ExeBin --selftest | Out-String).Trim(); $selftestRc = $LASTEXITCODE }
        finally { Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
        if ($selftestRc -ne 0) { Die "frozen exe --selftest failed (exit $selftestRc): $selftestOut -- aborting before publish" }
        Write-Host "    $selftestOut"
    }
    finally {
        if ($null -eq $savedCfg) { Remove-Item Env:\SERAPHINE_CONFIG_DIR -ErrorAction SilentlyContinue }
        else { $env:SERAPHINE_CONFIG_DIR = $savedCfg }
        Remove-Item -LiteralPath $cfgDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # ---- confirm ------------------------------------------------------------
    if (-not $Yes -and -not $DryRun) {
        $verb = if ($releaseExists) { "UPLOAD onto existing release" } else { "CREATE release" }
        Write-Host ""
        Write-Host "About to $verb  v$Version  ->  $RepoReleases   (asset: $SevenZ)"
        $ans = Read-Host "Proceed? [y/N]"
        if ($ans -notmatch '^(y|Y|yes)$') { Die "aborted by user (bump commit, if any, kept locally; not published)" }
    }

    # ---- optional push (before publishing: a created release tags this commit) --
    if ($DryRun) {
        if ($Push) { Write-Host "[dry-run] would push: git push origin HEAD" }
        else       { Write-Host "[dry-run] push: none (-Push not set)" }
    }
    elseif ($Push) {
        Note "pushing current branch"
        git push origin HEAD; if ($LASTEXITCODE -ne 0) { Die "git push failed (nothing published)" }
    }
    else {
        Write-Host "==> note: bump commit (if any) NOT pushed (run with -Push, or push/PR yourself)"
    }
    $targetSha = (git rev-parse HEAD).Trim()

    # ---- publish ------------------------------------------------------------
    Note "publishing release v$Version to $RepoReleases"
    $uploads = @($SevenZ)
    if (-not $NoManifest) { $uploads += $ManifestAsset }
    & $Py "tools\release_gates.py" --check-names @uploads
    if ($LASTEXITCODE -ne 0) { Die "refusing to publish a file that is not a release name" }
    if ($DryRun) {
        # the release lookup was skipped: show both forms
        $manifestLine = if ($NoManifest) { "gh release delete-asset v$Version Seraphine.filemanifest.txt --repo $RepoReleases -y (if it has one; -NoManifest)" } else { "gh release upload v$Version $ManifestAsset --repo $RepoReleases --clobber" }
        Write-Host "[dry-run] would publish (release exists): gh release delete-asset v$Version <each Seraphine-*-to-$Version.delta> --repo $RepoReleases -y; gh release upload v$Version $SevenZ --repo $RepoReleases --clobber; $manifestLine"
        $createLine = if ($NoManifest) { "" } else { "; then gh release upload v$Version $ManifestAsset --repo $RepoReleases" }
        Write-Host "[dry-run] would publish (no release yet): gh release create v$Version --repo $RepoReleases --target $targetSha --title 'v$Version' --notes <notes> $SevenZ$createLine"
        Write-Host "[dry-run] then: the first .7z / .dmg asset of v$Version must be Seraphine.7z / Seraphine.dmg (else the manifest asset is deleted and the script fails)"
    }
    else {
        if ($releaseExists) {
            # a delta of this version was built against the package about to be replaced;
            # list the assets again now (the pre-flight list is a build old)
            $releaseAssets = @(gh api "repos/$RepoReleases/releases/tags/v$Version" --jq '.assets[].name')
            if ($LASTEXITCODE -ne 0) { Die "cannot list the assets of v$Version (nothing replaced)" }
            $vRe = [regex]::Escape($Version)
            foreach ($d in @($releaseAssets | Where-Object { $_ -match "^Seraphine-\d+\.\d+\.\d+-to-$vRe\.delta$" })) {
                gh release delete-asset "v$Version" $d --repo $RepoReleases -y
                if ($LASTEXITCODE -ne 0) { Die "could not delete $d from v$Version (nothing replaced)" }
                Write-Host "    deleted $d (built against the replaced package)"
            }
            gh release upload "v$Version" $SevenZ --repo $RepoReleases --clobber
            if ($LASTEXITCODE -ne 0) { Die "gh release upload failed" }
            if ($NoManifest) {
                if ($releaseAssets -contains 'Seraphine.filemanifest.txt') {
                    gh release delete-asset "v$Version" Seraphine.filemanifest.txt --repo $RepoReleases -y
                    if ($LASTEXITCODE -ne 0) { Die "could not delete the old Seraphine.filemanifest.txt of v$Version" }
                }
            }
            else {
                gh release upload "v$Version" $ManifestAsset --repo $RepoReleases --clobber
                if ($LASTEXITCODE -ne 0) { Die "gh release upload of $ManifestAsset failed" }
            }
        }
        else {
            $notesPath = Join-Path $env:TEMP "seraphine_release_notes.md"
            # BOM-less UTF-8: Set-Content -Encoding UTF8 (PS 5.1) would prepend a BOM that
            # gh carries into the release body (a stray glyph in the in-app updater notes).
            [System.IO.File]::WriteAllText($notesPath, $Notes, (New-Object System.Text.UTF8Encoding($false)))
            # the full package alone first, the manifest in a second upload
            gh release create "v$Version" --repo $RepoReleases --target $targetSha --title "v$Version" --notes-file $notesPath $SevenZ
            $rc = $LASTEXITCODE
            Remove-Item $notesPath -ErrorAction SilentlyContinue
            if ($rc -ne 0) { Die "gh release create failed" }
            if (-not $NoManifest) {
                gh release upload "v$Version" $ManifestAsset --repo $RepoReleases
                if ($LASTEXITCODE -ne 0) { Die "gh release upload of $ManifestAsset failed (the release has Seraphine.7z only)" }
            }
        }
        # the REST API's order: what the in-app updater reads
        $assetNames = @(gh api "repos/$RepoReleases/releases/tags/v$Version" --jq '.assets[].name')
        & $Py "tools\release_gates.py" --check-first-packages -- @assetNames
        if ($LASTEXITCODE -ne 0) {
            if (-not $NoManifest) { gh release delete-asset "v$Version" Seraphine.filemanifest.txt --repo $RepoReleases -y }
            Die "v${Version}: the first .7z / .dmg asset is not the full package (assets: $($assetNames -join ', ')); the manifest asset was deleted again"
        }
        Write-Host "    first .7z / .dmg asset OK"
    }

    if ($DryRun) {
        Note "dry run done: v$Version built, packed and checked ($SevenZ); nothing was bumped, pushed or published."
    }
    else {
        Note "done. v$Version is live; existing Windows users (< $Version) will see Update and Restart."
    }
}
finally {
    Remove-Item $bumpPath -ErrorAction SilentlyContinue
}
