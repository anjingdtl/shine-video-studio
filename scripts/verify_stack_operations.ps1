param([int]$ApiPort = 8128, [int]$WebPort = 5188)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$opsData = Join-Path ([System.IO.Path]::GetTempPath()) ("shine-ops-" + [guid]::NewGuid())
$python = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'
$start = Join-Path $PSScriptRoot 'start_shine.ps1'
$stop = Join-Path $PSScriptRoot 'stop_shine.py'
New-Item -ItemType Directory -Path $opsData | Out-Null
try {
    & $start -Provider mock -ApiPort $ApiPort -WebPort $WebPort -DataDir $opsData -NoWorker
    $status = Invoke-RestMethod "http://127.0.0.1:$WebPort/api/v1/system/status"
    if (-not $status.api_online -or $status.worker_online) { throw 'NoWorker / custom proxy failed' }
    # A second launch must fail and preserve the existing owner manifest.
    $manifest = Join-Path $opsData 'run/processes.json'
    $before = (Get-FileHash $manifest).Hash
    & pwsh -NoProfile -File $start -Provider mock -ApiPort $ApiPort -WebPort $WebPort -DataDir $opsData -NoWorker *> $null
    if ($LASTEXITCODE -eq 0 -or (Get-FileHash $manifest).Hash -ne $before) { throw 'Duplicate launch was not refused' }
    & $python $stop --data-dir $opsData
    if ($LASTEXITCODE -ne 0) { throw 'NoWorker stop failed' }
    & $start -Provider mock -ApiPort $ApiPort -WebPort $WebPort -DataDir $opsData
    $online = $false
    for ($i = 0; $i -lt 20; $i++) {
        $status = Invoke-RestMethod "http://127.0.0.1:$WebPort/api/v1/system/status"
        if ($status.worker_online) { $online = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $online) { throw 'Worker did not become online after restart' }
    & $python $stop --data-dir $opsData
    if ($LASTEXITCODE -ne 0) { throw 'Worker stop failed' }
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object LocalPort -In $ApiPort,$WebPort)
    if ($listeners.Count) { throw 'Owned listener remained after shutdown' }
    Write-Output 'PASS: custom proxy, NoWorker, duplicate refusal, Worker restart and verified shutdown'
    Write-Output "Evidence data retained: $opsData"
} finally {
    if (Test-Path (Join-Path $opsData 'run/processes.json')) {
        & $python $stop --data-dir $opsData
    }
}
