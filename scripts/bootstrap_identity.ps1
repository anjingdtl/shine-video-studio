# Shared with bootstrap.ps1; dot-sourcing this file performs no writes.
function Get-ShineOperatorPasswordFile([string]$Path, [int]$Length = 40) {
    <#
    One-click installs cannot depend on a human inventing a 16+ character
    password correctly.  This returns a SecureString from a local credential
    file, generating it once with a crypto RNG when the file is absent.  The
    plaintext never leaves this function and is never written to the log.
    #>
    if (-not $Path) { throw 'Operator credential path is required.' }
    $Path = [IO.Path]::GetFullPath($Path)
    if (Test-Path -LiteralPath $Path) {
        $stored = (Get-Content -LiteralPath $Path -Raw -Encoding UTF8).Trim()
        if ($stored.Length -lt 32) {
            throw "Existing operator credential is shorter than the 32 characters the API requires: $Path"
        }
        return (ConvertTo-SecureString $stored -AsPlainText -Force)
    }
    # No 0/O/1/l/I: testers read this off a screen and retype it.
    $alphabet = 'abcdefghijkmnpqrstuvwxyz'.ToCharArray() + 'ABCDEFGHJKLMNPQRSTUVWXYZ'.ToCharArray() + '23456789'.ToCharArray()
    $bytes = New-Object 'byte[]' ($Length * 4)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $builder = New-Object Text.StringBuilder
    for ($i = 0; $i -lt $Length; $i++) {
        $value = [BitConverter]::ToUInt32($bytes, $i * 4)
        [void]$builder.Append($alphabet[$value % $alphabet.Length])
    }
    $generated = $builder.ToString()
    $directory = Split-Path -Parent $Path
    if ($directory) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
    [IO.File]::WriteAllText($Path, $generated + "`n", (New-Object Text.UTF8Encoding($false)))
    return (ConvertTo-SecureString $generated -AsPlainText -Force)
}

function Initialize-ShineOperator([string]$EnvFile, [scriptblock]$ReadPassword) {
    $existing = if (Test-Path -LiteralPath $EnvFile) { Get-Content -LiteralPath $EnvFile -Raw -Encoding UTF8 } else { '' }
    $hashMatch = [regex]::Match($existing, '(?m)^SHINE_OPERATOR_TOKEN_SHA256=([^\r\n]*)')
    if ($hashMatch.Success -and $hashMatch.Groups[1].Value) {
        if ($hashMatch.Groups[1].Value -notmatch '^[a-fA-F0-9]{64}$') {
            throw 'Existing operator hash is invalid; reconcile the configuration before bootstrapping.'
        }
    } else {
        $password = & $ReadPassword
        # The API rejects anything shorter than 32 characters (operator_review.authenticate),
        # so the prompt enforces the same floor instead of accepting a secret that cannot approve.
        if ($password -isnot [Security.SecureString] -or $password.Length -lt 32) {
            throw 'The operator password must contain at least 32 characters.'
        }
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            $hasher = [Security.Cryptography.SHA256]::Create()
            try { $digest = $hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($plain)) }
            finally { $hasher.Dispose(); $plain = $null }
            $hash = ([BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
        } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        $existing = [regex]::Replace($existing, '(?m)^SHINE_OPERATOR_TOKEN_SHA256=.*\r?\n?', '')
        $existing += "`nSHINE_OPERATOR_TOKEN_SHA256=$hash`n"
    }
    if ($existing -notmatch '(?m)^SHINE_OPERATOR_ID=[^\s\r\n]+') {
        $existing = [regex]::Replace($existing, '(?m)^SHINE_OPERATOR_ID=.*\r?\n?', '')
        $existing += "`nSHINE_OPERATOR_ID=local-operator`n"
    }
    $existing = [regex]::Replace($existing, '(?m)^SHINE_ENABLE_LOCAL_EXECUTION=.*\r?\n?', '')
    $existing += "`nSHINE_ENABLE_LOCAL_EXECUTION=1`n"
    [IO.File]::WriteAllText($EnvFile, $existing, (New-Object Text.UTF8Encoding($false)))
}
