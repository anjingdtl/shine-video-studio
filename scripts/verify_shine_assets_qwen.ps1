[CmdletBinding()]
param(
    [ValidateSet('preflight', 't2i-sample')]
    [string]$Action = 'preflight',
    [string]$ApiBase = 'http://127.0.0.1:8000/api/v1',
    [ValidateSet('character', 'costume', 'scene', 'prop')]
    [string]$Category = 'prop',
    [string]$Prompt = 'A red paper lantern on a plain neutral background.',
    [ValidateRange(256, 2048)]
    [int]$Width = 1024,
    [ValidateRange(256, 2048)]
    [int]$Height = 1024,
    [ValidateRange(60, 7200)]
    [int]$TimeoutSeconds = 3600,
    [string]$EvidencePath,
    [switch]$ConfirmLocalGpuUse
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Invoke-ShineGet([string]$Path) {
    Invoke-RestMethod -Method Get -Uri "$script:ApiRoot$Path" -TimeoutSec 15
}

$parsedApi = [Uri]$ApiBase
if ($parsedApi.Scheme -ne 'http' -or -not $parsedApi.IsLoopback -or -not $ApiBase.TrimEnd('/').EndsWith('/api/v1')) {
    throw 'ApiBase must be the local loopback Shine API ending in /api/v1.'
}
$script:ApiRoot = $ApiBase.TrimEnd('/')

$status = Invoke-RestMethod -Method Get -Uri "$script:ApiRoot/system/status" -TimeoutSec 15
$capabilities = Invoke-ShineGet '/asset-center/capabilities'
$plan = Invoke-ShineGet '/asset-center/local-validation-plan'
$t2i = $capabilities.image_capabilities.QWEN_IMAGE_T2I

$report = [ordered]@{
    checked_at_utc = [DateTime]::UtcNow.ToString('o')
    api_base = $script:ApiRoot
    host = [Environment]::OSVersion.VersionString
    system_status = $status
    t2i_capability = $t2i
    workflow_plan = $plan
    action = $Action
    real_gpu_result = 'NOT_RUN'
}

if ($Action -eq 'preflight') {
    $report | ConvertTo-Json -Depth 30
    Write-Host 'Preflight completed. No generation was submitted.' -ForegroundColor Green
    return
}

if (-not $ConfirmLocalGpuUse) {
    throw 't2i-sample submits one real local GPU job. Re-run with -ConfirmLocalGpuUse after checking the idle test instance.'
}
if (-not $EvidencePath) {
    throw 'Provide -EvidencePath inside an isolated test folder; the script does not choose an evidence destination.'
}
if (-not $t2i.can_validate) {
    $report.real_gpu_result = 'BLOCKED'
    $report.blocker = $t2i.reason
    $report | ConvertTo-Json -Depth 30
    throw "T2I sample is blocked by local capability checks: $($t2i.reason)"
}

$body = @{
    idempotency_key = "qwen-local-validation-$([Guid]::NewGuid().ToString('N'))"
    title = '本机 Qwen T2I 验证候选'
    mode = 'T2I'
    prompt = $Prompt
    category = $Category
    output = @{ width = $Width; height = $Height }
    candidate_count = 1
    validation_only = $true
} | ConvertTo-Json -Depth 10

$created = Invoke-RestMethod -Method Post -Uri "$script:ApiRoot/asset-center/image-jobs" `
    -ContentType 'application/json' -Body $body -TimeoutSec 30
$task = $created.items | Select-Object -First 1
if (-not $task -or -not $task.id) { throw 'The API did not return a durable task ID.' }
$report.task_id = $task.id
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$terminalStates = @('CANDIDATE_READY', 'QC_FAILED', 'FAILED', 'UNKNOWN', 'RECONCILE_REQUIRED', 'BLOCKED_CAPABILITY', 'CANCELLED')

do {
    Start-Sleep -Seconds 3
    $task = Invoke-ShineGet "/asset-center/image-jobs/$($task.id)"
    Write-Host "Task $($task.id): $($task.status) / $($task.external_outcome)"
} while ($task.status -notin $terminalStates -and [DateTime]::UtcNow -lt $deadline)

$report.task = $task
if ($task.status -ne 'CANDIDATE_READY' -or $task.external_outcome -ne 'SUCCEEDED' -or -not $task.result_version_id) {
    $report.real_gpu_result = if ($task.status -in @('UNKNOWN', 'RECONCILE_REQUIRED')) { 'BLOCKED_UNKNOWN_DO_NOT_REPLAY' } else { 'BLOCKED' }
    $report | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $EvidencePath -Encoding UTF8
    throw "Task ended as $($task.status). Keep the attempt record and reconcile UNKNOWN; do not resubmit automatically."
}

$version = Invoke-ShineGet "/asset-center/versions/$($task.result_version_id)"
$mediaUri = [Uri]::new($parsedApi, [string]$version.media_url)
$imagePath = Join-Path (Split-Path -Parent $EvidencePath) "$($task.id).png"
Invoke-WebRequest -Method Get -Uri $mediaUri -OutFile $imagePath -TimeoutSec 60 | Out-Null
$fileHash = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash.ToLowerInvariant()
Add-Type -AssemblyName System.Drawing
$image = [System.Drawing.Image]::FromFile($imagePath)
try {
    $actualWidth = $image.Width
    $actualHeight = $image.Height
} finally {
    $image.Dispose()
}

$proof = [ordered]@{
    task_id = $task.id
    task_status = $task.status
    external_outcome = $task.external_outcome
    external_prompt_id = $task.external_prompt_id
    version_id = $version.id
    workflow_revision_hash = $version.workflow_revision_hash
    declared_output_node_id = $version.provenance.output_node_id
    model_revision = $version.provenance.model_revision
    content_sha256 = $version.content_sha256
    downloaded_sha256 = $fileHash
    requested_dimensions = @{ width = $Width; height = $Height }
    decoded_dimensions = @{ width = $actualWidth; height = $actualHeight }
    is_mock = $version.is_mock
    image_path = $imagePath
    version_qc = $version.qc
}
$report.real_gpu_result = if ($version.is_mock -eq $false -and $fileHash -eq $version.content_sha256 -and
    $actualWidth -eq $Width -and $actualHeight -eq $Height -and $version.provenance.output_node_id -eq '461') {
    'SAMPLE_BYTES_AND_DIMENSIONS_VERIFIED_HUMAN_REVIEW_REQUIRED'
} else {
    'BLOCKED_OUTPUT_EVIDENCE_MISMATCH'
}
$report.output_proof = $proof
$report | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $EvidencePath -Encoding UTF8

if ($report.real_gpu_result -notlike 'SAMPLE_BYTES*') {
    throw 'Output evidence did not match the task version, declared node, or requested dimensions.'
}
Write-Host "Evidence saved to $EvidencePath" -ForegroundColor Green
Write-Host "Candidate: $($version.id) · SHA-256: $fileHash · ${actualWidth}x${actualHeight}" -ForegroundColor Green
Write-Host 'This sample does not grant commercial rights or replace human visual and license review.' -ForegroundColor Yellow
