<#
.SYNOPSIS
    Writes filelist.txt into a built Windows dist directory and packs it as a .7z.
    Shared by release_win.ps1 and .github/workflows/build_windows.yml.

.DESCRIPTION
    filelist.txt is the in-place updater's contract (see Read-FileList in
    app/common/update.py): one path per line, relative to the dist root, with
    backslash separators, UTF-8 with BOM, CRLF line endings (a trailing CRLF
    after the last entry), and no entry for filelist.txt itself. The bytes are
    written explicitly, so the result is the same in Windows PowerShell 5.1 and
    PowerShell 7 (Out-File -Encoding UTF8 alone would not produce the same bytes
    in both).

    The archive holds the files at the archive root (no nested top folder);
    filelist.txt is generated before packing, so it ships at the root.

.PARAMETER DistDir
    The built directory (e.g. dist\Seraphine).

.PARAMETER Out
    The .7z to create (replaced if it exists). Omit to only write filelist.txt.

.PARAMETER Python
    Python with py7zr installed. Default: python.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DistDir,
    [string]$Out = "",
    [string]$Python = "python"
)

# Any failure must end the script with a non-zero exit code. A bare throw is not
# enough: under release_win.ps1's 'Continue' default the caller would carry on
# (and could pack an archive without filelist.txt), so errors are caught and
# turned into exit 1, which callers check through $LASTEXITCODE.
$ErrorActionPreference = 'Stop'
try {
    if (-not (Test-Path $DistDir)) { throw "build did not produce $DistDir" }

    # Remove any stale filelist.txt first so it can't list itself.
    Remove-Item "$DistDir\filelist.txt" -ErrorAction SilentlyContinue
    $base = (Resolve-Path $DistDir).Path + "\"
    $entries = @(
        Get-ChildItem -Path "$DistDir\*" -File -Recurse |
            Select-Object -ExpandProperty FullName |
            ForEach-Object { $_.Replace($base, "") } |
            Where-Object { $_ -ne "filelist.txt" }
    )
    $text = ""
    foreach ($e in $entries) { $text += $e + "`r`n" }
    $listPath = $base + "filelist.txt"
    [System.IO.File]::WriteAllText($listPath, $text, (New-Object System.Text.UTF8Encoding($true)))
    if (-not (Test-Path -LiteralPath $listPath) -or (Get-Item -LiteralPath $listPath).Length -le 3) {
        throw "filelist.txt was not written (or is empty) in $DistDir"
    }
    Write-Host ("filelist.txt entries: " + $entries.Count)

    if ($Out) {
        & $Python (Join-Path $PSScriptRoot "pack_7z.py") $DistDir $Out
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Out)) { throw "py7zr pack failed" }
    }
}
catch {
    Write-Host "error: pack_win.ps1: $_" -ForegroundColor Red
    if ($Out) { Remove-Item -LiteralPath $Out -ErrorAction SilentlyContinue }
    exit 1
}
