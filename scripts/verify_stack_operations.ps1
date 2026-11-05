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
    # A second launch must refuse to start a second stack. The refusal is a
    # friendly no-op ("the workbench is already running"), so the invariant is
    # asserted directly instead of through an exit code: the owner manifest is
    # untouched, the same listeners keep serving, and the launcher says it
    # refused. A second stack here would silently take over the owner's port.
    $manifest = Join-Path $opsData 'run/processes.json'
    $before = (Get-FileHash $manifest).Hash
    $listenersBefore = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object LocalPort -In $ApiPort,$WebPort |
        ForEach-Object { "$($_.LocalPort):$($_.OwningProcess)" } | Sort-Object)
    $secondLog = Join-Path $opsData 'second-launch.log'
    & pwsh -NoProfile -File $start -Provider mock -ApiPort $ApiPort -WebPort $WebPort -DataDir $opsData -NoWorker *> $secondLog
    if ((Get-FileHash $manifest).Hash -ne $before) { throw 'A refused duplicate launch changed the owner manifest' }
    $listenersAfter = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object LocalPort -In $ApiPort,$WebPort |
        ForEach-Object { "$($_.LocalPort):$($_.OwningProcess)" } | Sort-Object)
    if (Compare-Object $listenersBefore $listenersAfter) { throw 'A refused duplicate launch changed the listeners' }
    $secondText = Get-Content -LiteralPath $secondLog -Raw -Encoding UTF8
    if ($secondText -notmatch 'already running') { throw "A duplicate launch did not refuse: $secondText" }
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
