<#
.SYNOPSIS
    release_win.ps1 -- one-command Windows release. Windows counterpart of release_mac.sh.

    Bumps VERSION (only when needed), builds the .7z via Seraphine.win.spec, verifies
    the frozen build reports the new version, generates filelist.txt + packs Seraphine.7z
    with py7zr, and publishes a GitHub Release on this (public) source repo,
    Ezeki3lRchy/Seraphine, whose releases the in-app auto-updater reads. Uses your local
    `gh` login -- no PAT, no CI minutes.

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
    Print every step without bumping / building / packing / publishing.

.PARAMETER Venv
    Path to the PySide6 build venv (Python 3.12 + requirements.txt + PyInstaller 6.x).
    Default: ..\.build-venv-win  (the PyQt5 .build-venv CANNOT build this source).

.EXAMPLE
    .\release_win.ps1 1.2.3 -NotesFile NOTES.md -Push

.EXAMPLE
    .\release_win.ps1 1.2.3 -DryRun
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
$Py           = Join-Path $Venv "Scripts\python.exe"
$PyInstaller  = Join-Path $Venv "Scripts\pyinstaller.exe"

function Die($m)  { Write-Host "error: $m" -ForegroundColor Red; exit 1 }
function Note($m) { Write-Host "==> $m" -ForegroundColor Cyan }

# ---- pre-flight -------------------------------------------------------------
Note "pre-flight checks"
if (-not $Version) { Die "version required, e.g. .\release_win.ps1 1.2.3" }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { Die "version must be X.Y.Z (got '$Version')" }
if (-not (Test-Path $Py))          { Die "$Py not found -- create the PySide6 venv and install requirements first" }
if (-not (Test-Path $PyInstaller)) { Die "pyinstaller not in venv ($PyInstaller) -- pip install pyinstaller==6.21" }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Die "gh (GitHub CLI) not installed" }
gh auth status 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { Die "gh not authenticated -- run: gh auth login" }

$m = Select-String -Path $Config -Pattern '^VERSION = "(.+)"'
if (-not $m) { Die "could not read current VERSION from $Config" }
$current = $m.Matches[0].Groups[1].Value

if ($current -eq $Version) {
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
else { Die "working tree not clean -- commit your changes first (the release builds from the tree). Use -AllowDirty to override." }

gh release view "v$Version" --repo $RepoReleases 1>$null 2>$null
$releaseExists = ($LASTEXITCODE -eq 0)
if ($releaseExists) { Write-Host "    release v$($Version): EXISTS in $RepoReleases -> will upload .7z onto it" }
else                { Write-Host "    release v$($Version): not found -> will create it" }

# Creating the release tags the built commit (--target <sha>); GitHub must have that
# commit by then. Check now, not after a ~10 minute build.
if (-not $releaseExists -and -not $Push) {
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

# Pack dist\Seraphine\* with files at the ARCHIVE ROOT (no nested top folder), matching
# the v1.1.9 in-place updater contract. filelist.txt is generated (in PowerShell, UTF-8
# BOM + CRLF + backslash paths) BEFORE this runs, so it ships at the root but is absent
# from its own listing.
$packPy = @'
import os, sys, py7zr
dist = sys.argv[1]
out = sys.argv[2]
if os.path.exists(out):
    os.remove(out)
with py7zr.SevenZipFile(out, "w") as z:
    for root, dirs, files in os.walk(dist):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, dist)
            z.write(full, arcname=rel)
print("packed " + out)
'@

$bumpPath = Join-Path $env:TEMP "seraphine_bump.py"
$packPath = Join-Path $env:TEMP "seraphine_pack.py"
Set-Content -Path $bumpPath -Value $bumpPy -Encoding UTF8
Set-Content -Path $packPath -Value $packPy -Encoding UTF8

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

    # ---- build --------------------------------------------------------------
    Note "building (pyinstaller --noconfirm $Spec)"
    if ($DryRun) {
        Write-Host "[dry-run] $PyInstaller --noconfirm $Spec"
    }
    else {
        & $PyInstaller --noconfirm $Spec
        if ($LASTEXITCODE -ne 0) { Die "pyinstaller failed" }
    }

    # ---- filelist + pack ----------------------------------------------------
    Note "generating filelist.txt and packing $SevenZ"
    if ($DryRun) {
        Write-Host "[dry-run] would write $DistDir\filelist.txt and pack $SevenZ via py7zr"
    }
    else {
        if (-not (Test-Path $DistDir)) { Die "build did not produce $DistDir" }
        # Remove any stale filelist.txt first so it can't list itself (the build dir is
        # normally fresh from --noconfirm, but be robust to re-runs).
        Remove-Item "$DistDir\filelist.txt" -ErrorAction SilentlyContinue
        $base = (Resolve-Path $DistDir).Path + "\"
        Get-ChildItem -Path "$DistDir\*" -File -Recurse |
            Select-Object -ExpandProperty FullName |
            ForEach-Object { $_.Replace($base, "") } |
            Where-Object { $_ -ne "filelist.txt" } |
            Out-File -FilePath "$DistDir\filelist.txt" -Encoding UTF8
        & $Py $packPath $DistDir $SevenZ
        if ($LASTEXITCODE -ne 0) { Die "py7zr pack failed" }
    }

    # ---- verify frozen build reports the new version ------------------------
    Note "verifying the frozen exe reports $Version"
    if ($DryRun) {
        Write-Host "[dry-run] would run: $ExeBin --version  (expect $Version)"
    }
    else {
        if (-not (Test-Path $SevenZ)) { Die "pack did not produce $SevenZ" }
        $reported = (& $ExeBin --version | Select-Object -Last 1).Trim()
        if ($reported -ne $Version) { Die "frozen exe reports '$reported', expected '$Version' -- aborting before publish" }
        $sizeMB = "{0:N0} MB" -f ((Get-Item $SevenZ).Length / 1MB)
        Write-Host "    frozen exe reports: $reported  OK   ($sizeMB .7z)"
    }

    # ---- self-test the frozen exe (after the pack: it may create log\ in the build dir,
    #      which must not end up in the .7z) ------------------------------------------
    Note "self-testing the frozen exe (--selftest)"
    if ($DryRun) {
        Write-Host "[dry-run] would run: $ExeBin --selftest  (offscreen)"
    }
    else {
        $env:QT_QPA_PLATFORM = "offscreen"
        try { $selftestOut = (& $ExeBin --selftest | Out-String).Trim(); $selftestRc = $LASTEXITCODE }
        finally { Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
        if ($selftestRc -ne 0) { Die "frozen exe --selftest failed (exit $selftestRc): $selftestOut -- aborting before publish" }
        Write-Host "    $selftestOut"
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
    if ($Push) {
        Note "pushing current branch"
        if ($DryRun) { Write-Host "[dry-run] git push origin HEAD" }
        else { git push origin HEAD; if ($LASTEXITCODE -ne 0) { Die "git push failed (nothing published)" } }
    }
    else {
        Write-Host "==> note: bump commit (if any) NOT pushed (run with -Push, or push/PR yourself)"
    }
    $targetSha = (git rev-parse HEAD).Trim()

    # ---- publish ------------------------------------------------------------
    Note "publishing release v$Version to $RepoReleases"
    if ($DryRun) {
        if ($releaseExists) { Write-Host "[dry-run] gh release upload v$Version $SevenZ --repo $RepoReleases --clobber" }
        else {
            $shaShown = if ($bumpNeeded) { "<sha of the bump commit>" } else { $targetSha }
            Write-Host "[dry-run] gh release create v$Version --repo $RepoReleases --target $shaShown --title 'v$Version' --notes <notes> $SevenZ"
        }
    }
    else {
        if ($releaseExists) {
            gh release upload "v$Version" $SevenZ --repo $RepoReleases --clobber
            if ($LASTEXITCODE -ne 0) { Die "gh release upload failed" }
        }
        else {
            $notesPath = Join-Path $env:TEMP "seraphine_release_notes.md"
            # BOM-less UTF-8: Set-Content -Encoding UTF8 (PS 5.1) would prepend a BOM that
            # gh carries into the release body (a stray glyph in the in-app updater notes).
            [System.IO.File]::WriteAllText($notesPath, $Notes, (New-Object System.Text.UTF8Encoding($false)))
            gh release create "v$Version" --repo $RepoReleases --target $targetSha --title "v$Version" --notes-file $notesPath $SevenZ
            $rc = $LASTEXITCODE
            Remove-Item $notesPath -ErrorAction SilentlyContinue
            if ($rc -ne 0) { Die "gh release create failed" }
        }
    }

    Note "done. v$Version is live; existing Windows users (< $Version) will see Update and Restart."
}
finally {
    Remove-Item $bumpPath, $packPath -ErrorAction SilentlyContinue
}
