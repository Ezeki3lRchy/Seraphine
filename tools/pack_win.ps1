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

    filemanifest.txt (unless -NoManifest) is written next to it in the same way:
    UTF-8 with BOM, CRLF, first line exactly "#seraphine-manifest 1", then one line
    per filelist.txt entry in the same order: <sha256 lowercase hex> TAB <size in
    bytes> TAB <relative backslash path>. Neither file lists filelist.txt or
    filemanifest.txt, so filelist.txt has the same bytes with or without the
    manifest. Updaters that do not know the manifest ignore it.

    The archive holds the files at the archive root (no nested top folder);
    filelist.txt and filemanifest.txt are generated before packing, so they ship
    at the root.

.PARAMETER DistDir
    The built directory (e.g. dist\Seraphine).

.PARAMETER Out
    The .7z to create (replaced if it exists). Omit to only write the lists.

.PARAMETER Python
    Python with py7zr installed (the py7zr packer). Default: python.

.PARAMETER Packer
    py7zr (default; pure Python, single-threaded), 7z (native 7-Zip: 7z on PATH or
    $env:ProgramFiles\7-Zip\7z.exe; exit 1 if missing) or auto (7z when found, else
    py7zr). 7-Zip runs as "7z a -t7z -m0=LZMA2 -mf=off -mx=5 -mmt=on": plain
    LZMA2 with no executable filters (BCJ2 cannot be read by the app's py7zr).
    After a 7-Zip pack, every "Method =" line of "7z l -slt" must be plain LZMA2
    (else exit 1, no archive). The 7-Zip used is printed as "sevenzip: <path>".
    Either way, run tools/check_archive.py on the result.

.PARAMETER NoManifest
    Write no filemanifest.txt (escape hatch: a package exactly like the ones
    before the manifest existed).

.PARAMETER CheckMethods
    Only check the given .7z: print "sevenzip: <path>" and its methods, exit 0
    when every coder is plain LZMA2, else 1 (also 1 when 7-Zip is missing).
    Nothing is written. DistDir is not needed.
#>
[CmdletBinding()]
param(
    [string]$DistDir = "",
    [string]$Out = "",
    [string]$Python = "python",
    [ValidateSet('auto', '7z', 'py7zr')][string]$Packer = "py7zr",
    [switch]$NoManifest,
    [string]$CheckMethods = ""
)

# Native 7-Zip: 7z on PATH, else the default install location; $null if neither.
function Find-SevenZip {
    $cmd = Get-Command 7z -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    if ($env:ProgramFiles) {
        $p = Join-Path $env:ProgramFiles '7-Zip\7z.exe'
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

# Throws unless every coder of the archive is plain LZMA2 (no BCJ/BCJ2/ARM64/Delta
# filters, no other method): an allowlist, since the app's py7zr reads only what
# it supports.
function Assert-PlainLzma2([string]$SevenZip, [string]$Archive) {
    $listing = @(& $SevenZip l -slt $Archive)
    if ($LASTEXITCODE -ne 0) { throw "7z l -slt failed on $Archive (exit $LASTEXITCODE)" }
    $methods = @($listing | Where-Object { $_ -like 'Method =*' } | ForEach-Object { $_.TrimEnd() } | Sort-Object -Unique)
    if ($methods.Count -eq 0) { throw "7z l -slt shows no Method line for $Archive" }
    Write-Host ("archive methods: " + ($methods -join ', '))
    $bad = @($methods | Where-Object { $_ -notmatch '^Method =( LZMA2(:\S+)?)?$' })
    if ($bad.Count -gt 0) { throw "$Archive uses more than plain LZMA2: $($bad -join ', ')" }
}

# Any failure must end the script with a non-zero exit code. A bare throw is not
# enough: under release_win.ps1's 'Continue' default the caller would carry on
# (and could pack an archive without filelist.txt), so errors are caught and
# turned into exit 1, which callers check through $LASTEXITCODE.
$ErrorActionPreference = 'Stop'
try {
    if ($CheckMethods) {
        $sevenZip = Find-SevenZip
        if (-not $sevenZip) { throw "-CheckMethods needs 7-Zip: neither 7z on PATH nor $env:ProgramFiles\7-Zip\7z.exe exists" }
        Write-Host "sevenzip: $sevenZip"
        if (-not (Test-Path -LiteralPath $CheckMethods)) { throw "no archive at $CheckMethods" }
        Assert-PlainLzma2 $sevenZip $CheckMethods
        Write-Host "methods OK: $CheckMethods"
        exit 0
    }
    if (-not $DistDir) { throw "-DistDir is required" }
    if (-not (Test-Path $DistDir)) { throw "build did not produce $DistDir" }

    # Remove any stale filelist.txt / filemanifest.txt first so they can't be listed.
    Remove-Item "$DistDir\filelist.txt" -ErrorAction SilentlyContinue
    Remove-Item "$DistDir\filemanifest.txt" -ErrorAction SilentlyContinue
    $base = (Resolve-Path $DistDir).Path + "\"
    # The root spelled the way the enumeration spells it. $base keeps an 8.3 short
    # name given on the command line (C:\Users\RUNNER~1\...) while the wildcard
    # enumeration returns long names, so stripping $base would leave absolute paths
    # in the list. Take the prefix from the enumerated items themselves.
    $top = @(Get-ChildItem -Path "$DistDir\*") | Select-Object -First 1
    if (-not $top) { throw "$DistDir is empty" }
    $root = (Split-Path -Parent $top.FullName).TrimEnd('\') + '\'
    $entries = @(
        Get-ChildItem -Path "$DistDir\*" -File -Recurse |
            Select-Object -ExpandProperty FullName |
            ForEach-Object {
                if (-not $_.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
                    throw "'$_' is not under '$root'"
                }
                $_.Substring($root.Length)
            } |
            Where-Object { $_ -ne "filelist.txt" -and $_ -ne "filemanifest.txt" }
    )
    # the updater treats every line as a path relative to the install dir
    foreach ($e in $entries) {
        if (-not $e -or $e.StartsWith('\') -or $e.Contains(':') -or $e.Contains('/')) {
            throw "not a relative backslash path: '$e'"
        }
    }
    $text = ""
    foreach ($e in $entries) { $text += $e + "`r`n" }
    $listPath = $base + "filelist.txt"
    [System.IO.File]::WriteAllText($listPath, $text, (New-Object System.Text.UTF8Encoding($true)))
    if (-not (Test-Path -LiteralPath $listPath) -or (Get-Item -LiteralPath $listPath).Length -le 3) {
        throw "filelist.txt was not written (or is empty) in $DistDir"
    }
    Write-Host ("filelist.txt entries: " + $entries.Count)

    if ($NoManifest) {
        Write-Host "filemanifest.txt: not written (-NoManifest)"
    }
    else {
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append("#seraphine-manifest 1`r`n")
        try {
            foreach ($e in $entries) {
                $fs = [System.IO.File]::OpenRead($root + $e)
                try { $hash = $sha.ComputeHash($fs); $len = $fs.Length }
                finally { $fs.Dispose() }
                $hex = [System.BitConverter]::ToString($hash).Replace('-', '').ToLowerInvariant()
                [void]$sb.Append($hex + "`t" + $len + "`t" + $e + "`r`n")
            }
        }
        finally { $sha.Dispose() }
        $manifestPath = $base + "filemanifest.txt"
        [System.IO.File]::WriteAllText($manifestPath, $sb.ToString(), (New-Object System.Text.UTF8Encoding($true)))
        if (-not (Test-Path -LiteralPath $manifestPath)) { throw "filemanifest.txt was not written in $DistDir" }
        Write-Host ("filemanifest.txt entries: {0} ({1:N1} s)" -f $entries.Count, $clock.Elapsed.TotalSeconds)
    }

    if ($Out) {
        $sevenZip = $null
        if ($Packer -ne 'py7zr') {
            $sevenZip = Find-SevenZip
            if (-not $sevenZip -and $Packer -eq '7z') {
                throw "7-Zip requested (-Packer 7z) but neither 7z on PATH nor $env:ProgramFiles\7-Zip\7z.exe exists"
            }
        }
        $outFull = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Out)
        # 7z a adds to an existing archive: always start from none
        Remove-Item -LiteralPath $outFull -ErrorAction SilentlyContinue
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        if ($sevenZip) {
            $banner = @(& $sevenZip i) | Where-Object { $_ -match '7-Zip' } | Select-Object -First 1
            Write-Host "sevenzip: $sevenZip"
            Write-Host ("packer: 7-Zip: {0}" -f "$banner".Trim())
            # Exactly the listed files plus the two lists, at the archive root: run
            # from the dist root with a UTF-8 list file of relative paths.
            $names = @($entries) + @("filelist.txt")
            if (-not $NoManifest) { $names += "filemanifest.txt" }
            $listFile = Join-Path ([System.IO.Path]::GetTempPath()) ("seraphine_pack_" + [guid]::NewGuid().ToString('N') + ".txt")
            [System.IO.File]::WriteAllText($listFile, (($names -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
            Push-Location -LiteralPath $root
            try {
                & $sevenZip a -t7z -m0=LZMA2 -mf=off -mx=5 -mmt=on -scsUTF-8 -bd -y $outFull "@$listFile"
                $rc = $LASTEXITCODE
            }
            finally {
                Pop-Location
                Remove-Item -LiteralPath $listFile -ErrorAction SilentlyContinue
            }
            if ($rc -ne 0 -or -not (Test-Path -LiteralPath $outFull)) { throw "7-Zip pack failed (exit $rc)" }
            Assert-PlainLzma2 $sevenZip $outFull
            $used = "7-Zip"
        }
        else {
            Write-Host "packer: py7zr ($Python)"
            & $Python (Join-Path $PSScriptRoot "pack_7z.py") $DistDir $outFull
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outFull)) { throw "py7zr pack failed" }
            $used = "py7zr"
        }
        Write-Host ("packed {0} with {1} in {2:N1} s ({3:N1} MB)" -f $Out, $used, $clock.Elapsed.TotalSeconds, ((Get-Item -LiteralPath $outFull).Length / 1MB))
    }
}
catch {
    Write-Host "error: pack_win.ps1: $_" -ForegroundColor Red
    if ($Out) { Remove-Item -LiteralPath $Out -ErrorAction SilentlyContinue }
    exit 1
}
