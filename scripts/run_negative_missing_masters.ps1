# The real negative acceptance against a running local stack.
#
# A script that names a character without an approved master must be refused
# with BLOCKED_MISSING_MASTERS and an exact list of the characters that lack
# a master.  The stack is started on the data directory of the acceptance run
# that proved the positive path, so the refusal is tested against the very
# project whose masters are approved and locked.
param(
    [Parameter(Mandatory = $true)][string]$RunName,
    [int]$ApiPort = 8140,
    [int]$WebPort = 5183,
    [string]$ExpectMissing = 'CHAR_CHENMO',
    [string]$ExpectPresent = 'CHAR_LIANG',
    [switch]$NoRemoteText
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runDir = Join-Path $repoRoot ".local-acceptance/final-closure-v3/$RunName"
$dataDir = Join-Path $runDir 'data'
$recipePath = Join-Path $runDir 'managed_recipe.json'
$recordPath = Join-Path $runDir 'real-production-record.json'
$outDir = Join-Path $runDir 'negative-missing-masters'

if (-not (Test-Path -LiteralPath $recipePath)) { throw "The managed recipe is missing: $recipePath" }
if (-not (Test-Path -LiteralPath $recordPath)) { throw "The acceptance record is missing: $recordPath" }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$projectId = $record.project_id
if (-not $projectId) { throw 'The acceptance record carries no project id.' }

$env:SHINE_MANAGED_RECIPE = $recipePath
$env:SHINE_DATA_DIR = $dataDir
$started = $false
try {
    Write-Host "Starting the local stack on the acceptance data directory..."
    & (Join-Path $PSScriptRoot 'start_shine.ps1') -Provider comfyui -ApiPort $ApiPort -WebPort $WebPort `
        -DataDir $dataDir 2>&1 | Tee-Object -FilePath (Join-Path $outDir 'start-stack.log')
    if ($LASTEXITCODE -ne 0) { throw 'The local stack did not start.' }
    $started = $true

    $ready = $false
    $lastError = ''
    for ($i = 0; $i -lt 180; $i++) {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$ApiPort/health" -TimeoutSec 10
            if ($health) { $ready = $true; break }
        } catch { $lastError = $_.Exception.Message }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "The API did not come up: $lastError" }
    $status = Invoke-RestMethod "http://127.0.0.1:$ApiPort/api/v1/system/status" -TimeoutSec 60
    Write-Host ("API status: api_online={0} worker_online={1} render_mode={2}" -f `
        $status.api_online, $status.worker_online, $status.render_mode)

    Write-Host "Submitting the negative script to project $projectId ..."
    # The crew provider is a hosted service, so the script text may only be
    # sent with the same explicit consent the workbench asks the user for.
    $scriptPath = Join-Path $repoRoot '.local-acceptance/final-closure-v3/negative-missing-masters-script.md'
    $verifyArgs = @(
        (Join-Path $PSScriptRoot 'verify_negative_missing_masters.py'),
        '--api', "http://127.0.0.1:$ApiPort",
        '--project-id', $projectId,
        '--script', $scriptPath,
        '--out', $outDir,
        '--expect-missing', $ExpectMissing,
        '--expect-present', $ExpectPresent
    )
    if (-not $NoRemoteText) { $verifyArgs += '--allow-remote-text' }
    & (Join-Path $repoRoot 'backend/.venv/Scripts/python.exe') @verifyArgs `
        2>&1 | Tee-Object -FilePath (Join-Path $outDir 'negative-run.log')
    $negativeExit = $LASTEXITCODE
} finally {
    if ($started) {
        Write-Host 'Stopping the local stack...'
        & (Join-Path $repoRoot 'backend/.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'stop_shine.py') `
            --data-dir $dataDir 2>&1 | Tee-Object -FilePath (Join-Path $outDir 'stop-stack.log')
    }
    Remove-Item Env:SHINE_MANAGED_RECIPE -ErrorAction SilentlyContinue
    Remove-Item Env:SHINE_DATA_DIR -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host "Negative evidence: $outDir"
exit $negativeExit
