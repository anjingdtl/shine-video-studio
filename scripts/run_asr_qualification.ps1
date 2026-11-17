# Qualify the local Chinese speech recognition on ground-truth audio.
#
# The fixtures are synthesised by the local Qwen3-TTS model from a known
# script, so the expected text is exact ground truth.  The production ASR
# transcribes them and the raw responses are recorded next to the score.
param(
    [Parameter(Mandatory = $true)][string]$EvidenceDir,
    [Parameter(Mandatory = $true)][string]$Manifest,
    [Parameter(Mandatory = $true)][string]$RuntimePackages,
    [Parameter(Mandatory = $true)][string]$ReviewPython,
    [Parameter(Mandatory = $true)][string]$TtsPackages,
    [Parameter(Mandatory = $true)][string]$TtsModel,
    [Parameter(Mandatory = $true)][string]$QwenAsrPackages,
    [Parameter(Mandatory = $true)][string]$QwenAsrModelRoot,
    [int]$AsrPort = 8117,
    [string]$AsrModel = 'local-qwen3-asr-0.6b',
    [int]$TimeoutMinutes = 90
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'shine_local_config.ps1')
$venvPython = (Get-ShineStackSettings $repoRoot).Python
$Manifest = (Resolve-Path -LiteralPath $Manifest).Path
$RuntimePackages = (Resolve-Path -LiteralPath $RuntimePackages).Path
$ReviewPython = (Resolve-Path -LiteralPath $ReviewPython).Path
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null

$executor = $null
try {
    $script = Join-Path $repoRoot 'scripts/local_review_isolated_server.py'
    $executorArguments = @($script, '--manifest', $Manifest, '--runtime-packages', $RuntimePackages, '--port', "$AsrPort")
    $executor = Start-Process -PassThru -WindowStyle Hidden -FilePath $ReviewPython -WorkingDirectory $repoRoot `
        -ArgumentList ($executorArguments | ForEach-Object { '"' + $_ + '"' }) `
        -RedirectStandardOutput (Join-Path $EvidenceDir 'asr-executor.out.log') `
        -RedirectStandardError (Join-Path $EvidenceDir 'asr-executor.err.log')
    $ready = $false
    for ($i = 0; $i -lt 240; $i++) {
        Start-Sleep -Milliseconds 500
        try { if ((Invoke-RestMethod "http://127.0.0.1:$AsrPort/health" -TimeoutSec 2).status -eq 'ok') { $ready = $true; break } } catch { }
        if (-not (Get-Process -Id $executor.Id -ErrorAction SilentlyContinue)) { break }
    }
    if (-not $ready) { throw "The local review executor did not start on port $AsrPort" }
    Write-Host "Local review executor ready on port $AsrPort"

    & $venvPython (Join-Path $PSScriptRoot 'qualify_local_asr.py') `
        --asr-base "http://127.0.0.1:$AsrPort" --evidence-dir $EvidenceDir `
        --asr-model $AsrModel --asr-python $ReviewPython `
        --tts-python $ReviewPython --tts-packages $TtsPackages --tts-model $TtsModel `
        --qwen-asr-packages $QwenAsrPackages --qwen-asr-model-root $QwenAsrModelRoot `
        2> (Join-Path $EvidenceDir 'asr-qualification.err.log')
    Get-Content (Join-Path $EvidenceDir 'asr-qualification.err.log') -Tail 20 -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw 'The ASR qualification failed.' }
} finally {
    if ($executor) {
        $info = Get-Process -Id $executor.Id -ErrorAction SilentlyContinue
        if ($info) {
            $birth = $info.StartTime.ToUniversalTime().ToFileTimeUtc().ToString()
            & $venvPython (Join-Path $PSScriptRoot 'shine_processes.py') $executor.Id $birth --stop 2>&1 | Out-Null
        }
    }
    Start-Sleep -Seconds 2
}
Write-Host "ASR qualification evidence: $EvidenceDir"
