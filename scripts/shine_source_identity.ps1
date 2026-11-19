# The launcher's view of "which code is this checkout running".
# The computation itself has ONE implementation (backend/shine/source_identity.py,
# pure stdlib); this wrapper only picks an interpreter and parses the JSON.
# Whether Git fast-forwarded this run is a separate decision (update_checkout.ps1);
# whether a *running* stack must be replaced is decided from this identity.

function Get-ShineSourceIdentity([string]$RepoRoot, [string]$PreferredPython = '') {
    $script = Join-Path $PSScriptRoot 'shine_source_identity.py'
    $candidates = @()
    if ($PreferredPython -and (Test-Path -LiteralPath $PreferredPython -PathType Leaf)) {
        $candidates += $PreferredPython
    }
    $onPath = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($onPath) { $candidates += $onPath.Source }
    $pyLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($pyLauncher) { $candidates += $pyLauncher.Source }
    foreach ($python in $candidates) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $json = & $python $script $RepoRoot 2>$null
            $code = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previous }
        if ($code -eq 0 -and $json) {
            try {
                $identity = ConvertFrom-Json -InputObject (($json | ForEach-Object { [string]$_ }) -join "`n")
                if ($identity.digest) { return $identity }
            } catch { }
        }
    }
    # No interpreter could answer: the identity is explicitly unknown, never
    # silently "current". A recorded stack without a matching digest is replaced.
    return [pscustomobject]@{
        kind = 'unknown'; commit = $null; short_commit = $null; dirty = $null
        digest = ''; display = 'unknown'; package_version = $null; built_at = $null
    }
}

function Test-ShineSourceIdentityMatch($Recorded, $Current) {
    # An old manifest without an identity is not proven current: it must be
    # replaced once so the record carries the fact.
    $recordedDigest = ''
    if ($Recorded -and $Recorded.digest) { $recordedDigest = [string]$Recorded.digest }
    $currentDigest = ''
    if ($Current -and $Current.digest) { $currentDigest = [string]$Current.digest }
    return ($recordedDigest -ne '' -and $recordedDigest -eq $currentDigest)
}
