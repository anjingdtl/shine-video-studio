# Shared with bootstrap.ps1; dot-sourcing this file performs no writes.
function Initialize-ShineOperator([string]$EnvFile, [scriptblock]$ReadPassword) {
    $existing = if (Test-Path -LiteralPath $EnvFile) { Get-Content -LiteralPath $EnvFile -Raw -Encoding UTF8 } else { '' }
    $hashMatch = [regex]::Match($existing, '(?m)^SHINE_OPERATOR_TOKEN_SHA256=([^\r\n]*)')
    if ($hashMatch.Success -and $hashMatch.Groups[1].Value) {
        if ($hashMatch.Groups[1].Value -notmatch '^[a-fA-F0-9]{64}$') {
            throw 'Existing operator hash is invalid; reconcile the configuration before bootstrapping.'
        }
    } else {
        $password = & $ReadPassword
        if ($password -isnot [Security.SecureString] -or $password.Length -lt 16) {
            throw 'The operator password must contain at least 16 characters.'
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
