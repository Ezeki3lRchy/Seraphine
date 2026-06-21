<#
.SYNOPSIS
    release_win.ps1 -- one-command Windows release. Windows counterpart of release_mac.sh.

    Bumps VERSION (only when needed), builds the .7z via Seraphine.win.spec, verifies
    the frozen build reports the new version, generates filelist.txt + packs Seraphine.7z
    with py7zr, and publishes to the PUBLIC Seraphine-releases repo that the in-app
    auto-updater reads. Uses your local `gh` login -- no PAT, no CI minutes.

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
    After committing the bump, push the current branch (git push origin HEAD).

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
$RepoReleases = "Ezeki3lRchy/Seraphine-releases"   # public repo the updater reads
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
    $Notes = Get-Content -Path $NotesFile -Raw
}
if (-not $Notes) {
    $Notes = "## v$Version (Windows)`n`nMaintenance release. Use **Update and Restart** to install."
}

$dirty = git status --porcelain
if (-not $dirty)        { Write-Host "    working tree: clean" }
elseif ($AllowDirty)    { Write-Host "    working tree: DIRTY (-AllowDirty set, continuing)" }
else { Die "working tree not clean -- commit your changes first (the release builds from the tree). Use -AllowDirty to override." }

gh release view "v$Version" --repo $RepoReleases 1>$null 2>$null
$releaseExists = ($LASTEXITCODE -eq 0)
if ($releaseExists) { Write-Host "    release v$($Version): EXISTS in $RepoReleases -> will upload .7z onto it" }
else                { Write-Host "    release v$($Version): not found -> will create it" }

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
# from its own listing -- exactly like the original make.ps1.
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

    # ---- confirm ------------------------------------------------------------
    if (-not $Yes -and -not $DryRun) {
        $verb = if ($releaseExists) { "UPLOAD onto existing release" } else { "CREATE release" }
        Write-Host ""
        Write-Host "About to $verb  v$Version  ->  $RepoReleases   (asset: $SevenZ)"
        $ans = Read-Host "Proceed? [y/N]"
        if ($ans -notmatch '^(y|Y|yes)$') { Die "aborted by user (bump commit, if any, kept locally; not published)" }
    }

    # ---- publish ------------------------------------------------------------
    Note "publishing release v$Version to $RepoReleases"
    if ($DryRun) {
        if ($releaseExists) { Write-Host "[dry-run] gh release upload v$Version $SevenZ --repo $RepoReleases --clobber" }
        else                { Write-Host "[dry-run] gh release create v$Version --repo $RepoReleases --title 'v$Version (Windows)' --notes <notes> $SevenZ" }
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
            gh release create "v$Version" --repo $RepoReleases --title "v$Version (Windows)" --notes-file $notesPath $SevenZ
            $rc = $LASTEXITCODE
            Remove-Item $notesPath -ErrorAction SilentlyContinue
            if ($rc -ne 0) { Die "gh release create failed" }
        }
    }

    # ---- optional push ------------------------------------------------------
    if ($Push) {
        Note "pushing current branch"
        if ($DryRun) { Write-Host "[dry-run] git push origin HEAD" }
        else { git push origin HEAD; if ($LASTEXITCODE -ne 0) { Die "git push failed" } }
    }
    else {
        Write-Host "==> note: bump commit (if any) NOT pushed (run with -Push, or push/PR yourself)"
    }

    Note "done. v$Version is live; existing Windows users (< $Version) will see Update and Restart."
}
finally {
    Remove-Item $bumpPath, $packPath -ErrorAction SilentlyContinue
}
