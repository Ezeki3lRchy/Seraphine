import os
import shlex
import subprocess
import sys
import time
from collections import namedtuple

import psutil

from app.common import platform

# Windows in-place updater. Everything it touches comes from its parameters, so it
# does not depend on the working directory (the frozen app's CWD is _internal).
#
# Order (see also the README / AGENTS.md):
#   0. take the single-instance lock (a second instance exits 7 touching nothing),
#   1. wait for the app's PID (bounded), 2. validate the dirs (no drive roots),
#   3. finish an unfinished restore left by an earlier run (backup dir present)
#      only if its backup.meta fingerprint matches this install's Seraphine.exe;
#      a foreign / unfingerprinted backup is discarded without applying it,
#   4. write backup.meta (pre-update exe hash, package exe hash, version),
#   5. precheck every package file against the install (reparse points, file vs
#      directory) and back up each install file the package will replace,
#   6. merge the package over the install (robocopy /IM, bounded retries),
#      on failure restore every backed-up file and remove the files it added,
#   7. delete stale files (old filelist only, never a path the package has, never
#      through a reparse point, never outside the install dir),
#   8. remove backup + package, 9. write the result file, 10. relaunch.
# Every exit path after the app has exited writes the result file and relaunches
# whatever version is in place (the result is written BEFORE the relaunch so the
# new process can read it). Files in no filelist (icon cache, logs) are never touched.
WINDOWS_UPDATER_SCRIPT = r'''
param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$SourceDir,
    [int]$WaitPid = 0,
    [int]$WaitTimeoutSec = 120,
    [int]$DeadlineSec = 600,
    [int]$MergeAttempts = 3,
    [string]$LogPath = '',
    [string]$ResultPath = '',
    [string]$BackupDir = '',
    [string]$LockPath = '',
    [string]$TargetVersion = '',
    [switch]$NoRelaunch
)

$ErrorActionPreference = 'Stop'

# 0. single instance: nothing (not even the log) is touched before this
$script:lock = $null
if ($LockPath) {
    try {
        [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($LockPath))
        $script:lock = [System.IO.File]::Open($LockPath, 'OpenOrCreate', 'ReadWrite', 'None')
    } catch {
        exit 7
    }
}

$script:deadline = (Get-Date).AddSeconds($DeadlineSec)
$script:appExited = $false
$script:mergeStarted = $false
$script:mergeDone = $false
$script:install = $null
$script:backedUp = New-Object System.Collections.ArrayList
$script:added = New-Object System.Collections.ArrayList
$script:createdDirs = New-Object System.Collections.ArrayList

function Write-Log([string]$Message) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    if ($LogPath) {
        try { Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 } catch { }
    }
}

function Write-Result([string]$Status, [int]$Code) {
    if (-not $ResultPath) { return }
    $obj = [ordered]@{ status = $Status; code = $Code; version = $TargetVersion;
                       time = (Get-Date -Format 's') }
    try {
        Set-Content -LiteralPath $ResultPath -Value ($obj | ConvertTo-Json -Compress) -Encoding UTF8
    } catch {
        Write-Log "could not write result file: $($_.Exception.Message)"
    }
}

function Start-App {
    if ($NoRelaunch -or -not $script:appExited -or -not $script:install) { return }
    $exe = Join-Path $script:install 'Seraphine.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        Write-Log 'no Seraphine.exe to relaunch'
        return
    }
    try {
        Start-Process -FilePath $exe -WorkingDirectory $script:install
        Write-Log 'relaunched Seraphine.exe'
    } catch {
        Write-Log "relaunch failed: $($_.Exception.Message)"
    }
}

function Finish([string]$Status, [int]$Code) {
    Write-Result $Status $Code
    Write-Log "result: $Status (exit $Code)"
    Start-App
    exit $Code
}

function Test-UnderDir([string]$Path, [string]$Dir) {
    return $Path.StartsWith($Dir + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-DriveRoot([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    return $full -ieq ([System.IO.Path]::GetPathRoot($full + '\')).TrimEnd('\')
}

$script:pathState = @{}
function Get-PathState([string]$Path) {
    # 'missing' | 'reparse' | 'file' | 'dir', cached (cleared after the merge)
    $k = $Path.ToLowerInvariant()
    if ($script:pathState.ContainsKey($k)) { return $script:pathState[$k] }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item) { $s = 'missing' }
    elseif ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { $s = 'reparse' }
    elseif ($item.PSIsContainer) { $s = 'dir' }
    else { $s = 'file' }
    $script:pathState[$k] = $s
    return $s
}

function Test-ReparseBelow([string]$Full) {
    # any existing component between the install dir (exclusive) and $Full
    $p = $Full
    while ($p -and (Test-UnderDir $p $script:install)) {
        if ((Get-PathState $p) -eq 'reparse') { return $true }
        $p = [System.IO.Path]::GetDirectoryName($p)
    }
    return $false
}

function Read-FileList([string]$Path) {
    $entries = New-Object System.Collections.ArrayList
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return ,$entries }
    foreach ($raw in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $entry = ([string]$raw).Trim().TrimStart([char]0xFEFF).Trim()
        if ($entry) { [void]$entries.Add($entry) }
    }
    return ,$entries
}

function Resolve-Entry([string]$Entry) {
    # filelist entry -> normalised full path under the install dir, or $null
    $rel = $Entry.Replace('/', '\')
    if ($rel.Contains(':') -or [System.IO.Path]::IsPathRooted($rel)) {
        Write-Log "skip (absolute path): $Entry"; return $null
    }
    foreach ($seg in $rel.Split('\')) {
        if ($seg -ne '.' -and $seg -match '^[\. ]+$') {
            Write-Log "skip (parent reference): $Entry"; return $null
        }
    }
    try {
        $full = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($script:install, $rel))
    } catch {
        Write-Log "skip (invalid path): $Entry"; return $null
    }
    if (-not (Test-UnderDir $full $script:install)) {
        Write-Log "skip (outside install dir): $Entry"; return $null
    }
    return $full
}

function Invoke-Robocopy([string]$From, [string]$To) {
    $ErrorActionPreference = 'Continue'
    # /IM: also copy "modified" files (same size + time, different content)
    $out = & robocopy.exe $From $To /E /IS /IT /IM /XJ /R:2 /W:1 /NP /NJH /NJS /NDL 2>&1
    $code = $LASTEXITCODE
    foreach ($l in $out) { if ([string]$l) { Write-Log ('robocopy: ' + ([string]$l).Trim()) } }
    return $code
}

function Get-Sha256([string]$Path) {
    # Upper-case hex SHA-256 of a file; throws when it cannot be read. .NET directly,
    # not Get-FileHash: in Windows PowerShell that is a script function of the
    # Microsoft.PowerShell.Utility module and can be unavailable when the parent
    # process hands down PowerShell 7's PSModulePath (app started from a pwsh
    # terminal, a CI step). Same provider and sharing mode as Get-FileHash uses.
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha = New-Object System.Security.Cryptography.SHA256CryptoServiceProvider
        try {
            return [System.BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', ''
        } finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
}

function Test-SameContent([string]$A, [string]$B) {
    try {
        return (Get-Sha256 $A) -eq (Get-Sha256 $B)
    } catch {
        return $false
    }
}

function Restore-Files($Rels, $Added) {
    # copy every backed-up file back (unconditionally); a file that cannot be
    # rewritten is fine only if its content already equals the backup
    $ok = $true
    foreach ($rel in $Rels) {
        $b = [System.IO.Path]::Combine($BackupDir, $rel)
        $t = [System.IO.Path]::Combine($script:install, $rel)
        $done = $false
        for ($i = 0; $i -lt 3 -and -not $done; $i++) {
            try {
                [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($t))
                [System.IO.File]::Copy($b, $t, $true)
                $done = $true
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $done) {
            if (Test-SameContent $b $t) {
                Write-Log "kept ${rel}: could not rewrite it, content already the previous one"
            } else {
                $ok = $false
                Write-Log "could not restore $rel"
            }
        }
    }
    foreach ($f in $Added) {
        if (-not (Test-UnderDir $f $script:install)) { continue }
        if (Test-Path -LiteralPath $f -PathType Leaf) {
            try { Remove-Item -LiteralPath $f -Force }
            catch { $ok = $false; Write-Log "could not remove added file ${f}" }
        }
    }
    return $ok
}

function Remove-Backup {
    # the backup, its added-files manifest and its fingerprint always go together
    try { Remove-Item -LiteralPath $BackupDir -Recurse -Force -ErrorAction SilentlyContinue } catch { }
    foreach ($suffix in @('.added.txt', '.meta')) {
        try { Remove-Item -LiteralPath ($BackupDir + $suffix) -Force -ErrorAction SilentlyContinue } catch { }
    }
}

function Get-ExeHash([string]$Path) {
    try { return Get-Sha256 $Path } catch { return $null }
}

function Read-BackupMeta {
    # {preExe, pkgExe, version} or $null when missing / unreadable / incomplete
    try {
        $m = Get-Content -LiteralPath ($BackupDir + '.meta') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($m.preExe -and $m.pkgExe) { return $m }
    } catch { }
    return $null
}

function Restore-Install {
    Write-Log 'restoring the previous version'
    $ok = Restore-Files $script:backedUp $script:added
    foreach ($d in ($script:createdDirs | Sort-Object Length -Descending)) {
        if ((Test-Path -LiteralPath $d -PathType Container) -and
            -not (Get-ChildItem -LiteralPath $d -Force | Select-Object -First 1)) {
            try { Remove-Item -LiteralPath $d -Force } catch { }
        }
    }
    if (-not $ok) {
        Write-Log "RESTORE FAILED: the previous files are kept in $BackupDir"
        Finish 'restore-failed' 4
    }
    Remove-Backup
    Write-Log 'previous version restored'
    Finish 'restored' 4
}

try {
    if ($LogPath) {
        try { Set-Content -LiteralPath $LogPath -Value '' -Encoding UTF8 } catch { }
    }
    Write-Log "updater started: install=$InstallDir source=$SourceDir pid=$WaitPid version=$TargetVersion"

    # 1. wait for that exact process (not any process named Seraphine)
    if ($WaitPid -gt 0) {
        $proc = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
        if ($proc) {
            $ms = [int][Math]::Max(0, [Math]::Min($WaitTimeoutSec * 1000,
                ($script:deadline - (Get-Date)).TotalMilliseconds))
            if (-not $proc.WaitForExit($ms)) {
                Write-Log "process $WaitPid did not exit in time; nothing changed"
                Finish 'failed:3' 3
            }
        }
    }
    $script:appExited = $true
    Write-Log 'app process has exited'

    # 2. validate
    if (-not $BackupDir) {
        Write-Log 'no backup dir given; nothing changed'
        Finish 'failed:2' 2
    }
    foreach ($d in @($InstallDir, $SourceDir, $BackupDir)) {
        if (Test-DriveRoot $d) {
            Write-Log "drive-root path is not supported: $d; nothing changed"
            Finish 'failed:2' 2
        }
    }
    $script:install = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    $source = [System.IO.Path]::GetFullPath($SourceDir).TrimEnd('\')
    $BackupDir = [System.IO.Path]::GetFullPath($BackupDir).TrimEnd('\')
    if (-not (Test-Path -LiteralPath $script:install -PathType Container)) {
        $script:install = $null
        Write-Log 'install dir not found; nothing changed'
        Finish 'failed:2' 2
    }
    if (-not (Test-Path -LiteralPath (Join-Path $source 'Seraphine.exe') -PathType Leaf)) {
        Write-Log 'source dir missing or has no Seraphine.exe; nothing changed'
        Finish 'failed:2' 2
    }
    foreach ($pair in @(@($script:install, $source), @($script:install, $BackupDir), @($source, $BackupDir))) {
        if ($pair[0] -ieq $pair[1] -or (Test-UnderDir $pair[0] $pair[1]) -or (Test-UnderDir $pair[1] $pair[0])) {
            Write-Log 'install, source and backup dirs overlap; nothing changed'
            Finish 'failed:2' 2
        }
    }
    $install = $script:install

    # 3. an earlier run left a backup: its restore is unfinished. Apply it only if
    #    the backup belongs to THIS install (fingerprint in backup.meta), else
    #    discard it untouched: never copy it over / delete files from another install
    $useBackup = $false
    if (Test-Path -LiteralPath $BackupDir) {
        $meta = Read-BackupMeta
        $current = Get-ExeHash (Join-Path $install 'Seraphine.exe')
        if (-not $meta) {
            Write-Log 'FOREIGN BACKUP: an earlier backup has no readable backup.meta; discarding it without restoring anything'
        } elseif ($current -and ($current -ieq $meta.preExe -or $current -ieq $meta.pkgExe)) {
            $useBackup = $true
        } else {
            Write-Log ("FOREIGN BACKUP: the installed Seraphine.exe ($current) matches neither the pre-update exe " +
                       "($($meta.preExe)) nor the package exe ($($meta.pkgExe)) of the backup for version " +
                       "$($meta.version); discarding it without restoring anything")
        }
        if (-not $useBackup) {
            Remove-Backup
        }
    }
    if ($useBackup) {
        Write-Log 'an earlier update left a backup for this install; completing its restore first'
        $rels = @(Get-ChildItem -LiteralPath $BackupDir -Recurse -File -Force |
            ForEach-Object { $_.FullName.Substring($BackupDir.Length + 1) })
        $prevAdded = @()
        $manifest = $BackupDir + '.added.txt'
        if (Test-Path -LiteralPath $manifest -PathType Leaf) {
            $prevAdded = @(Get-Content -LiteralPath $manifest -Encoding UTF8 | Where-Object { $_ })
        }
        if (-not (Restore-Files $rels $prevAdded)) {
            Write-Log "RESTORE FAILED again: the previous files are kept in $BackupDir; nothing merged. Please download Seraphine manually."
            Finish 'restore-failed' 8
        }
        Remove-Backup
        Write-Log 'earlier restore completed'
    }

    $oldList = Read-FileList (Join-Path $install 'filelist.txt')
    $newList = Read-FileList (Join-Path $source 'filelist.txt')

    # 4. fingerprint the backup about to be taken, BEFORE anything is backed up or
    #    modified: the install's exe now and the package's exe (the merge copies the
    #    root files first, so a half-merged install has one of the two)
    Remove-Backup
    # a leftover (e.g. a locked file of a foreign backup) would later be restored
    # as if it belonged to this run: stop before backing up or modifying anything
    foreach ($left in @($BackupDir, ($BackupDir + '.added.txt'), ($BackupDir + '.meta'))) {
        if (Test-Path -LiteralPath $left) {
            Write-Log "an earlier backup could not be removed ($left); nothing changed"
            Finish 'failed:5' 5
        }
    }
    $metaObj = [ordered]@{
        preExe = [string](Get-ExeHash (Join-Path $install 'Seraphine.exe'))
        pkgExe = [string](Get-ExeHash (Join-Path $source 'Seraphine.exe'))
        version = $TargetVersion
    }
    [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($BackupDir))
    Set-Content -LiteralPath ($BackupDir + '.meta') -Value ($metaObj | ConvertTo-Json -Compress) -Encoding UTF8

    # 5. precheck + backup of every install file the package will replace
    $sourceFiles = @(Get-ChildItem -LiteralPath $source -Recurse -File -Force |
        ForEach-Object { $_.FullName.Substring($source.Length + 1) })
    foreach ($rel in $sourceFiles) {
        $target = [System.IO.Path]::Combine($install, $rel)
        if (Test-ReparseBelow $target) {
            Write-Log "reparse point in the install path of $rel; nothing changed"
            Remove-Backup
            Finish 'failed:6' 6
        }
        $state = Get-PathState $target
        if ($state -eq 'dir') {
            Write-Log "mismatch: $rel is a directory in the install; nothing changed"
            Remove-Backup
            Finish 'failed:6' 6
        }
        $parent = [System.IO.Path]::GetDirectoryName($target)
        while (Test-UnderDir $parent $install) {
            $ps = Get-PathState $parent
            if ($ps -eq 'file') {
                Write-Log "mismatch: a file is in the way of $rel; nothing changed"
                Remove-Backup
                Finish 'failed:6' 6
            }
            if ($ps -eq 'missing' -and -not $script:createdDirs.Contains($parent)) {
                [void]$script:createdDirs.Add($parent)
            }
            if ($ps -eq 'dir') { break }  # existing dir: its parents were checked
            $parent = [System.IO.Path]::GetDirectoryName($parent)
        }
        if ($state -eq 'file') {
            $b = [System.IO.Path]::Combine($BackupDir, $rel)
            try {
                [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($b))
                [System.IO.File]::Copy($target, $b, $true)
                [void]$script:backedUp.Add($rel)
            } catch {
                Write-Log "backup of $rel failed: $($_.Exception.Message); nothing changed"
                Remove-Backup
                Finish 'failed:5' 5
            }
        } else {
            [void]$script:added.Add($target)
        }
    }
    [void][System.IO.Directory]::CreateDirectory($BackupDir)
    # what the merge will add, so a later run can finish an interrupted restore
    [System.IO.File]::WriteAllLines($BackupDir + '.added.txt', [string[]]@($script:added))
    Write-Log "backed up $($script:backedUp.Count) files"

    # 6. merge, a bounded number of passes (transient locks)
    $script:mergeStarted = $true
    $merged = $false
    for ($i = 1; $i -le $MergeAttempts; $i++) {
        if ((Get-Date) -gt $script:deadline) { Write-Log 'deadline reached during merge'; break }
        $code = Invoke-Robocopy $source $install
        # 1-3: copied / extra files; 4 mismatch, 8 failures, 16 fatal
        if (($code -band 0x1C) -eq 0) { $merged = $true; break }
        Write-Log "merge pass $i failed (robocopy exit $code)"
        if ($code -band 0x04) { break }
        if ($i -lt $MergeAttempts) { Start-Sleep -Seconds 2 }
    }
    if (-not $merged) { Restore-Install }
    $script:mergeStarted = $false
    $script:mergeDone = $true
    Write-Log 'merge finished'

    # 7. stale files: in the old filelist, not in the new one, not in the package
    $script:pathState = @{}
    if ($newList.Count -eq 0) {
        Write-Log 'new filelist missing or empty; skipping stale-file cleanup'
    } else {
        $keep = @{}
        foreach ($e in $newList) {
            $f = Resolve-Entry $e
            if ($f) { $keep[$f.ToLowerInvariant()] = $true }
        }
        foreach ($e in $oldList) {
            $full = Resolve-Entry $e
            if (-not $full) { continue }
            if ($keep.ContainsKey($full.ToLowerInvariant())) { continue }
            $rel = $full.Substring($install.Length + 1)
            if ($rel -ieq 'filelist.txt') { continue }
            if (Test-Path -LiteralPath ([System.IO.Path]::Combine($source, $rel))) { continue }
            if (Test-ReparseBelow $full) { Write-Log "skip (reparse point): $e"; continue }
            if (Test-Path -LiteralPath $full -PathType Leaf) {
                try {
                    Remove-Item -LiteralPath $full -Force
                    Write-Log "removed stale: $rel"
                } catch {
                    Write-Log "could not remove stale ${rel}: $($_.Exception.Message)"
                }
            }
        }
    }

    # 8. clean up
    Remove-Backup
    try {
        Remove-Item -LiteralPath $source -Recurse -Force
        Write-Log 'removed source dir'
    } catch {
        Write-Log "could not remove source dir: $($_.Exception.Message)"
    }

    Write-Log 'update finished'
    Finish 'ok' 0
}
catch {
    Write-Log "updater error: $($_.Exception.Message)"
    if ($script:mergeDone) {
        # the new version is fully in place; only clean-up went wrong
        Write-Log 'warning: clean-up after a completed merge failed; the update itself succeeded'
        Remove-Backup
        Finish 'ok' 0
    }
    if ($script:mergeStarted) {
        try { Restore-Install } catch { Finish 'restore-failed' 4 }
    }
    Finish 'failed:1' 1
}
'''

# CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: no console window, and not tied to
# the app's console group, so the script keeps running after Seraphine exits.
_HIDDEN_DETACHED = 0x08000000 | 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# main.py sets these from the DPI setting; the relaunched app must recompute them
_DROPPED_ENV = ('QT_SCALE_FACTOR', 'QT_ENABLE_HIGHDPI_SCALING')

UPDATER_STARTED = 'updater started'
LOCK_NAME = 'updater.lock'

# process: the Popen; logPath: where the script logs ("updater started" first)
UpdaterLaunch = namedtuple('UpdaterLaunch', ['process', 'logPath'])


def writeWindowsUpdaterScript(path):
    # BOM so Windows PowerShell 5.1 never reads it as ANSI
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write(WINDOWS_UPDATER_SCRIPT)


def windowsUpdaterArgs(scriptPath, installDir, sourceDir, pid, logPath,
                       noRelaunch=False, resultPath='', backupDir='',
                       targetVersion='', lockPath=''):
    args = ['powershell.exe', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
            '-File', scriptPath,
            '-InstallDir', os.path.normpath(installDir),
            '-SourceDir', os.path.normpath(sourceDir),
            '-WaitPid', str(int(pid)),
            '-LogPath', logPath]
    if resultPath:
        args += ['-ResultPath', resultPath]
    if backupDir:
        args += ['-BackupDir', os.path.normpath(backupDir)]
    if lockPath:
        args += ['-LockPath', lockPath]
    if targetVersion:
        args += ['-TargetVersion', str(targetVersion)]
    if noRelaunch:
        args.append('-NoRelaunch')
    return args


def _removeQuietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


# macOS lock directory (see macUpdaterScript): a record still incomplete after
# LOCK_GRACE seconds is stale; LOCK_MAX_AGE (an update takes seconds to a few
# minutes) is a backstop only for a record without an identity
LOCK_GRACE = 5
LOCK_MAX_AGE = 1800


def _processIdentity(pid):
    """What the macOS script records as its identity: ``LC_ALL=C ps -o lstart=``
    (the process start time), whitespace-normalised; '' when ps printed nothing,
    None when ps could not tell (failed, timed out, not available).

    Called only when a lock directory exists (an updater runs or was killed), also
    from the event-loop thread (_stageRelease, runUpdaterMac): ps answers in
    milliseconds, and the short timeout bounds the worst case."""
    if os.name == 'nt':
        return None
    try:
        r = subprocess.run(['ps', '-o', 'lstart=', '-p', str(int(pid))],
                           capture_output=True, timeout=2,
                           env=dict(os.environ, LC_ALL='C'))
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return ' '.join(r.stdout.decode('ascii', 'replace').split())


def _processAlive(pid):
    """bash's ``kill -0``: False for a gone process AND for another user's."""
    if os.name == 'nt':
        # tests only (no lock directory in production); os.kill would terminate it
        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _lockDirHeld(lockPath):
    """The macOS lock rule, the same as the script's read_record / record_held:
    the directory holds "<pid>\\n<identity>\\n<created epoch>"; it is held iff that
    record is complete, the pid answers ``kill -0`` (so a process of another user
    does not hold it) and
      - with an identity recorded: ``ps -o lstart=`` of the pid still prints it
        (a reused PID does not hold it), however old the lock is (the script waits
        for the app without a time limit); if ps cannot tell here (None), the
        live pid is not disproved, so the lock counts as held, but only up to
        LOCK_MAX_AGE: a reused PID must not block updates for good when ps
        keeps failing,
      - without one (no usable ps when it was written): it was created at most
        LOCK_MAX_AGE seconds from now.
    An incomplete record (missing, empty, garbage: the script is between mkdir
    and its write) is held only for LOCK_GRACE seconds."""
    try:
        with open(os.path.join(lockPath, 'pid'), encoding='ascii', errors='replace') as f:
            lines = f.read().split('\n')
    except OSError:
        lines = []
    if len(lines) < 3 or not lines[0].isdigit() or not lines[2].isdigit() \
            or int(lines[0]) <= 0:
        try:
            return time.time() - os.path.getmtime(lockPath) < LOCK_GRACE
        except OSError:
            return False
    pid, ident, created = int(lines[0]), lines[1], int(lines[2])
    if not _processAlive(pid):
        return False
    if not ident:
        return abs(time.time() - created) <= LOCK_MAX_AGE
    current = _processIdentity(pid)
    if current is None:
        return abs(time.time() - created) <= LOCK_MAX_AGE
    return current == ident


def updaterRunning(lockPath):
    """An updater holds the lock while it runs. Windows: the lock file, opened
    exclusively. macOS: a lock directory, see _lockDirHeld."""
    if os.path.isdir(lockPath):
        return _lockDirHeld(lockPath)
    if not os.path.exists(lockPath):
        return False
    try:
        with open(lockPath, 'a'):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def updaterEnv():
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
    # The relaunched exe is a new, independent instance of this frozen app
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def _popenDetached(args, cwd):
    kwargs = dict(cwd=cwd, env=updaterEnv(), close_fds=True,
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL)
    try:
        # leave the app's job object (if any) so its teardown cannot kill us
        return subprocess.Popen(
            args, creationflags=_HIDDEN_DETACHED | _CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:
        # access denied: the job does not allow breakaway
        return subprocess.Popen(args, creationflags=_HIDDEN_DETACHED, **kwargs)


def runUpdater(sourceDir, installDir=None, configDir=None, targetVersion='',
               stageDir=None, noRelaunch=False):
    """Windows: launch the hidden, detached updater that replaces the install with
    the extracted package in ``sourceDir`` once this process has exited.

    The backup dir, lock and result file live in ``stageDir`` (default: the folder
    that holds ``sourceDir``, i.e. <config>/update).
    @return: UpdaterLaunch, or None when nothing was started (not packaged, no
    Seraphine.exe in the package, or an updater is already running).
    """
    if sys.platform != 'win32':
        return None

    installDir = installDir or platform.install_dir()
    if not installDir or not os.path.isfile(os.path.join(sourceDir, 'Seraphine.exe')):
        return None

    configDir = configDir or platform.config_dir()
    stageDir = stageDir or os.path.dirname(os.path.normpath(sourceDir))
    lockPath = os.path.join(stageDir, LOCK_NAME)
    if updaterRunning(lockPath):
        # never disturb a running updater's log, result or script
        return None

    os.makedirs(configDir, exist_ok=True)
    scriptPath = os.path.join(configDir, 'updater.ps1')
    logPath = os.path.join(configDir, 'updater.log')
    resultPath = os.path.join(stageDir, 'result.json')
    _removeQuietly(logPath)
    _removeQuietly(resultPath)
    writeWindowsUpdaterScript(scriptPath)

    process = _popenDetached(
        windowsUpdaterArgs(scriptPath, installDir, sourceDir, os.getpid(), logPath,
                           resultPath=resultPath,
                           backupDir=os.path.join(stageDir, 'backup'),
                           targetVersion=targetVersion, noRelaunch=noRelaunch,
                           lockPath=lockPath),
        configDir)
    return UpdaterLaunch(process, logPath)


def updaterStarted(logPath):
    try:
        with open(logPath, encoding='utf-8-sig', errors='replace') as f:
            return UPDATER_STARTED in f.read(4096)
    except OSError:
        return False


def macUpdaterScript(dmgPath, appPath, pid, scriptPath, logPath, resultPath='',
                     version='', lockPath='', noRelaunch=False, lockGrace=LOCK_GRACE,
                     attachDelay=2, detachDelay=1):
    """The bash updater for macOS. Never removes the installed bundle before the
    new copy is verified next to it; on any failure the old app is put back and
    reopened. Every step is logged to ``logPath``; the outcome goes to
    ``resultPath`` (same format as the Windows updater's result.json).

    ``lockPath``: a directory taken with mkdir (atomic) before anything else and
    holding the script's record; a second instance exits 7 touching nothing, a
    stale lock (see _lockDirHeld) is taken over. ``noRelaunch``: never open an
    app afterwards (tests), like the Windows -NoRelaunch. ``lockGrace``,
    ``attachDelay``, ``detachDelay``: seconds waited for an incomplete lock record
    and between hdiutil attach / detach attempts (tests pass small values)."""
    q = shlex.quote
    return f'''#!/bin/bash
set -u

LOG={q(logPath)}
DMG={q(dmgPath)}
APP={q(appPath)}
PID={int(pid)}
SCRIPT={q(scriptPath)}
RESULT={q(resultPath)}
VERSION={q(str(version))}
LOCK={q(lockPath)}
NO_RELAUNCH={1 if noRelaunch else 0}
LOCK_GRACE={int(lockGrace)}
LOCK_MAX_AGE={LOCK_MAX_AGE}
MNT=""
MOUNTED=0

# 0. single instance: nothing (not even the log) is touched before this.
# $LOCK/pid holds "<pid>\\n<identity>\\n<created epoch>"; identity = the start time
# from `LC_ALL=C ps -o lstart=`. The rule (the same as _lockDirHeld in update.py):
# held iff the record is complete, the pid answers kill -0 (another user's process
# does not) and, with an identity recorded, ps still prints it for that pid (a
# reused PID does not hold it) however old the lock is; without an identity it
# must be at most LOCK_MAX_AGE s old. An incomplete record is waited for
# LOCK_GRACE s, then stale. (Python also counts as held a recorded identity it
# cannot check because its own ps call failed.)
proc_id() {{ LC_ALL=C ps -o lstart= -p "$1" 2>/dev/null | tr -s '[:blank:]' ' ' | sed 's/^ //; s/ $//'; }}

read_record() {{
    holder=""; ident=""; created=""
    record=$(cat "$LOCK/pid" 2>/dev/null) || record=""
    {{ IFS= read -r holder; IFS= read -r ident; IFS= read -r created; }} <<< "$record"
    case "$holder" in ''|*[!0-9]*) return 1;; esac
    case "$created" in ''|*[!0-9]*) return 1;; esac
    [ "$holder" -gt 0 ]
}}

record_held() {{
    kill -0 "$holder" 2>/dev/null || return 1
    if [ -n "$ident" ]; then
        [ "$(proc_id "$holder")" = "$ident" ]
        return
    fi
    age=$(( $(date +%s) - created ))
    [ "$age" -lt 0 ] && age=$(( -age ))
    [ "$age" -le "$LOCK_MAX_AGE" ]
}}

take_lock() {{
    mkdir "$LOCK" 2>/dev/null && return 0
    waited=0
    until read_record; do
        [ "$waited" -ge "$LOCK_GRACE" ] && break
        sleep 1  # its owner may be between mkdir and writing its record
        waited=$(( waited + 1 ))
    done
    if read_record && record_held; then
        return 1
    fi
    # stale: move it aside atomically, then make sure it is still the lock judged
    # stale (not one another updater just took)
    seen=$record
    stale="$LOCK.stale.$$"
    mv "$LOCK" "$stale" 2>/dev/null || return 1
    if [ "$(cat "$stale/pid" 2>/dev/null)" != "$seen" ]; then
        [ -e "$LOCK" ] || mv "$stale" "$LOCK" 2>/dev/null
        return 1
    fi
    rm -rf "$stale"
    mkdir "$LOCK" 2>/dev/null
}}
if [ -n "$LOCK" ]; then
    take_lock || exit 7
    # noclobber: never overwrite the record of another updater that took this
    # directory meanwhile; without our own record we do not own the lock
    if ! (set -C; printf '%s\\n%s\\n%s\\n' "$$" "$(proc_id $$)" "$(date +%s)" > "$LOCK/pid") 2>/dev/null; then
        exit 7
    fi
fi

# every exit from here on: detach only a dmg this run mounted, remove its empty
# mount dir, and release the lock only while the record is still this run's
release() {{
    if [ -n "$MNT" ]; then
        detach_dmg
    fi
    if [ -n "$LOCK" ] && [ "$(sed -n 1p "$LOCK/pid" 2>/dev/null)" = "$$" ]; then
        rm -rf "$LOCK"
    fi
}}
trap release EXIT
trap 'exit 1' HUP INT TERM

log() {{ printf '[%s] %s\\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }}

write_result() {{
    if [ -n "$RESULT" ]; then
        printf '{{"status": "%s", "code": %s, "version": "%s"}}\\n' "$1" "$2" "$VERSION" > "$RESULT" 2>/dev/null \\
            || log "could not write the result file"
    fi
    log "result: $1 (exit $2)"
}}

reopen() {{
    if [ "$NO_RELAUNCH" = 1 ]; then
        log "relaunch disabled: $1"
        return 0
    fi
    open "$1"
}}

: > "$LOG"
log "{UPDATER_STARTED}: app=$APP dmg=$DMG pid=$PID version=$VERSION"

# wait for Seraphine (that pid) to exit
while kill -0 "$PID" 2>/dev/null; do
    sleep 0.5
done
log "app process has exited"

PARENT=$(dirname "$APP")
NAME=$(basename "$APP")
NEW="$PARENT/.$NAME.new.$$"
BACKUP="$PARENT/.$NAME.old.$$"

# copies left by an interrupted earlier run (only .new.*, never .old.*)
for stale in "$PARENT/.$NAME.new."*; do
    if [ -d "$stale" ]; then
        log "removing a stale copy from an earlier run: $stale"
        rm -rf "$stale"
    fi
done

detach_dmg() {{
    # hdiutil detach only for the mount this run made (MOUNTED is set after a
    # successful attach): a plain directory is never detached, let alone forced
    if [ "$MOUNTED" = 1 ]; then
        # "Resource busy" is often transient: retry before forcing
        for try in 1 2 3; do
            if hdiutil detach "$MNT" -quiet >/dev/null 2>&1; then
                MOUNTED=0
                break
            fi
            if [ "$try" = 3 ]; then
                if hdiutil detach "$MNT" -force -quiet >/dev/null 2>&1; then
                    MOUNTED=0
                else
                    log "could not detach $MNT"
                fi
            else
                sleep {float(detachDelay):g}
            fi
        done
    fi
    if [ -n "$MNT" ] && [ "$MOUNTED" = 0 ]; then
        rmdir "$MNT" 2>/dev/null
        MNT=""
    fi
}}

# the installed app is in place (untouched or restored): clean up, reopen it
# $1 = message, $2 = result status (default failed:1)
fail() {{
    log "$1"
    rm -rf "$NEW"
    detach_dmg
    write_result "${{2:-failed:1}}" 1
    log "reopening the previous app"
    reopen "$APP"
    exit 1
}}

MNT=$(mktemp -d /tmp/seraphine-update.XXXXXX) || {{ MNT=""; fail "mktemp failed"; }}

# attach can fail transiently ("Resource busy"): a few attempts
attached=0
for try in 1 2 3; do
    if hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MNT" >>"$LOG" 2>&1; then
        attached=1
        MOUNTED=1
        break
    fi
    log "hdiutil attach failed (attempt $try)"
    [ "$try" = 3 ] || sleep {float(attachDelay):g}
done
if [ "$attached" != 1 ]; then
    fail "hdiutil attach failed"
fi
if [ ! -d "$MNT/Seraphine.app" ]; then
    fail "Seraphine.app not found in the mounted dmg"
fi

# 1. copy the new app next to the installed one (same volume)
rm -rf "$NEW"
if ! ditto "$MNT/Seraphine.app" "$NEW" >>"$LOG" 2>&1; then
    fail "copying the new app failed"
fi
detach_dmg

# 2. verify the copy
EXE=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$NEW/Contents/Info.plist" 2>/dev/null || echo Seraphine)
if [ ! -x "$NEW/Contents/MacOS/$EXE" ]; then
    fail "the new app is incomplete (no Contents/MacOS/$EXE)"
fi
xattr -cr "$NEW" 2>/dev/null
log "new app verified at $NEW"

# 3. swap: installed -> backup, new -> installed
if ! mv "$APP" "$BACKUP"; then
    fail "moving the installed app aside failed"
fi
if ! mv "$NEW" "$APP"; then
    log "moving the new app into place failed; restoring the previous app"
    if mv "$BACKUP" "$APP"; then
        fail "previous app restored" restored
    fi
    log "RESTORE FAILED: the previous app is at: $BACKUP"
    log "RESTORE FAILED: the new copy is at: $NEW"
    write_result restore-failed 2
    log "trying to open the previous app from $BACKUP"
    reopen "$BACKUP" || log "could not open $BACKUP"
    exit 2
fi

# 4. only now drop the backup and the dmg
rm -rf "$BACKUP"
rm -f "$DMG"
write_result ok 0
log "update finished"

reopen "$APP"
rm -f "$SCRIPT"
'''


def runUpdaterMac(dmg_path, app_path, version='', noRelaunch=False):
    # 仅 macOS: 写一个分离的 bash 脚本, 等待本进程退出后用 dmg 内的 .app 替换安装,
    # 重新启动并自删. 通过 start_new_session=True 让脚本在 Seraphine 退出后存活.
    # @return: UpdaterLaunch, or None on other platforms / while an updater runs
    # (its lock directory lives next to the dmg, in the stage dir)
    if sys.platform != 'darwin':
        return None

    from app.common.config import LOCAL_PATH

    lock_path = os.path.join(os.path.dirname(dmg_path), LOCK_NAME)
    if updaterRunning(lock_path):
        # never disturb a running updater's log, result or script
        return None

    script_path = os.path.join(LOCAL_PATH, "updater.sh")
    log_path = os.path.join(LOCAL_PATH, "updater.log")
    result_path = os.path.join(os.path.dirname(dmg_path), "result.json")
    _removeQuietly(log_path)
    _removeQuietly(result_path)

    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(macUpdaterScript(dmg_path, app_path, os.getpid(), script_path,
                                 log_path, result_path, version, lock_path,
                                 noRelaunch))

    os.chmod(script_path, 0o755)

    process = subprocess.Popen(['/bin/bash', script_path], start_new_session=True,
                               env=updaterEnv())
    return UpdaterLaunch(process, log_path)
