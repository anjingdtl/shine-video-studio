# Cross-process startup critical section for one Shine data directory.
#
# The launcher's check-reuse-reconcile-port-start-verify-record sequence is a
# TOCTOU window when two launchers run it at the same time: both may observe
# "no manifest", both pick ports and both start a full stack.  This guard is
# a named Windows mutex keyed by the canonical data directory, so exactly one
# launcher at a time may execute the sequence for that directory.
#
# A launcher that dies while holding it abandons the mutex; the next WaitOne
# then acquires it anyway, and the ordinary manifest reconciliation decides
# what to do with the leftovers.  scripts/stop_shine.py derives the identical
# name (see _acquire_startup_guard there), so a stop never races a start.
#
# Dot-source this file, then:
#   $guard = Enter-ShineStartupGuard -DataDir $dataDir
#   try { ...critical section... } finally { Exit-ShineStartupGuard $guard }
#
# NOTE: keep this file pure ASCII.  Windows PowerShell 5.1 reads BOM-less
# UTF-8 as ANSI; packaging tests enforce the encoding rules for scripts.

function Get-ShineStartupGuardName([string]$DataDir) {
    # Canonical form first (same rules as stop_shine.py): absolute path, no
    # trailing slash, invariant uppercase, UTF-8 bytes, SHA-256, 40 hex chars.
    $canonical = [System.IO.Path]::GetFullPath($DataDir).TrimEnd('\').ToUpperInvariant()
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($canonical))
    } finally { $sha.Dispose() }
    $hex = ($hash | ForEach-Object { $_.ToString('x2') }) -join ''
    return 'Global\ShineStackStartup-' + $hex.Substring(0, 40)
}

function Enter-ShineStartupGuard([string]$DataDir, [int]$WaitSeconds = 900) {
    $name = Get-ShineStartupGuardName $DataDir
    $mutex = New-Object System.Threading.Mutex($false, $name)
    $acquired = $false
    try {
        # Probe once without waiting: the common case is an uncontended guard.
        $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds(0))
        if (-not $acquired -and $WaitSeconds -gt 0) {
            Write-Host "Another launcher is working on this data directory; waiting for it to finish..." -ForegroundColor DarkGray
            $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds($WaitSeconds))
        }
    } catch [System.Threading.AbandonedMutexException] {
        # The previous holder died mid-startup.  Ownership was still granted;
        # the manifest reconciliation inside the critical section handles
        # whatever the interrupted launcher left behind.
        $acquired = $true
    }
    if (-not $acquired) {
        $mutex.Dispose()
        throw "Another Shine launcher is still starting this data directory ($DataDir). Wait for it to finish, or stop that stack first."
    }
    return @{ Name = $name; Mutex = $mutex }
}

function Exit-ShineStartupGuard($Guard) {
    if (-not $Guard) { return }
    try {
        if ($Guard.Mutex) { $Guard.Mutex.ReleaseMutex() }
    } catch [System.Threading.MutexReleaseException] {
        # Already released or owned by another thread; nothing to do.
    } finally {
        if ($Guard.Mutex) { $Guard.Mutex.Dispose() }
    }
}

function Test-ShineRecordedProcessAlive($Entry) {
    # The recorded pid is only trusted together with its recorded birth.
    # A manifest read back through ConvertFrom-Json turns the ISO 8601
    # start_time string into a [datetime] object; comparing that with -eq
    # against a string silently formats it with the current culture and
    # made every live worker look dead.  Normalise both sides to the
    # round-trip format before comparing.
    if (-not $Entry -or -not $Entry.pid) { return $false }
    $process = Get-Process -Id ([int]$Entry.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    if ($Entry.start_time) {
        $recorded = $Entry.start_time
        if ($recorded -is [datetime]) { $recorded = $recorded.ToString('o') }
        return ($process.StartTime.ToString('o') -eq [string]$recorded)
    }
    return $true
}
