# The V3 closure: complete script -> five real LLM agents -> real H3 series ->
# automatic EDL + automatic FFmpeg assembly -> Web playback.
#
#   1. the acceptance data directory and the managed renderer recipe are written;
#   2. the local reviewer routes are seeded and their protocol is pinned from a
#      live probe of the executors;
#   3. the real LLM provider (MiniMax) is seeded over loopback from a previous
#      acceptance run's DPAPI secret store, and the five crew routes are bound;
#   4. the normal launcher starts the API, the resident Worker and the Web UI
#      in ComfyUI mode;
#   5. a real browser imports the complete script, runs the five-agent
#      storyboard in the browser, applies it (human pre-production
#      confirmation), and starts the production series bound to the edit intent;
#   6. the resident Worker drives every shot behind one managed renderer;
#   7. the PRODUCT builds the EDL and assembles the final MP4 itself; the
#      script only observes, downloads, decodes and plays the reel;
#   8. the stack is stopped and the release of every owned resource is proved.
param(
    [Parameter(Mandatory = $true)][string]$RunName,
    [string]$Profile = 'local-h3-ref2va-pdd8-2img-1aud',
    [string]$SourceDb = '',
    [string]$SecretSourceDir = '',
    [string]$SecretRef = 'dpapi:ffbf39c1-1a30-459b-8e4e-cca7ba10b3cd.secret',
    [int]$ApiPort = 8140,
    [int]$WebPort = 5183,
    [int]$Shots = 3,
    [int]$TimeoutMinutes = 180,
    [string]$ComfyRoot = 'F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base',
    [string]$ReviewManifest = '',
    [string]$ReviewRuntimePackages = '',
    [string]$ReviewPython = '',
    [int]$MinRamMb = 4096,
    [int]$MinVramMb = 12000,
    [switch]$KeepData
)

$ErrorActionPreference = 'Stop'
# The tools in this script write progress to stderr.  PowerShell turns native
# stderr into a terminating error, so every native call merges stderr into
# stdout and is judged by its exit code instead.
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runDir = Join-Path $repoRoot ".local-acceptance/final-closure-v3/$RunName"
$dataDir = Join-Path $runDir 'data'
$logDir = Join-Path $runDir 'logs'
New-Item -ItemType Directory -Force -Path $runDir, $logDir | Out-Null

if (-not $ReviewManifest) {
    $ReviewManifest = Join-Path $repoRoot '.local-acceptance/closeout-20261025/local-review-native-manifest-qwen-asr-20261030.json'
}
if (-not $ReviewRuntimePackages) {
    $ReviewRuntimePackages = Join-Path $repoRoot '.local-acceptance/review-runtime-packages'
}
if (-not $ReviewPython) {
    $ReviewPython = Join-Path $ComfyRoot 'python_embeded/python.exe'
}
foreach ($path in @($ReviewManifest, $ReviewRuntimePackages, $ReviewPython)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing local review input: $path" }
}
if (-not $SourceDb) { $SourceDb = Join-Path $repoRoot '.local-acceptance/multishot-20261031/acceptance/shine.db' }
if (-not (Test-Path -LiteralPath $SourceDb)) { throw "Missing the source master-asset database: $SourceDb" }

# --- the managed renderer recipe -------------------------------------------
$adapterDir = Join-Path $ComfyRoot 'ComfyUI/custom_nodes/shine_comfy_handoff'
$adapterHash = & (Join-Path $repoRoot 'backend/.venv/Scripts/python.exe') -c @"
import hashlib, sys
from pathlib import Path
d = Path(sys.argv[1])
parts = [hashlib.sha256((d / n).read_bytes()).digest() for n in ('__init__.py', 'barrier.py')]
print(hashlib.sha256(b''.join(parts)).hexdigest())
"@ $adapterDir
if ($LASTEXITCODE -ne 0) { throw 'The handoff adapter identity could not be computed.' }
$gpuUuid = (nvidia-smi --query-gpu=uuid --format=csv,noheader | Select-Object -First 1).Trim()
if (-not $gpuUuid) { throw 'No local GPU was found.' }

$recipe = @{
    renderer = @{
        instance_id     = 'shine-local-h3'
        python          = (Join-Path $ComfyRoot 'python_embeded/python.exe')
        root            = (Join-Path $ComfyRoot 'ComfyUI')
        base_directory  = (Join-Path $ComfyRoot 'ComfyUI')
        bootstrap       = (Join-Path $repoRoot 'scripts/managed_comfy_bootstrap.py')
        adapter_dir     = $adapterDir
        adapter_sha256  = $adapterHash.Trim()
        gpu_uuid        = $gpuUuid
        port            = 8188
        min_ram_mb      = $MinRamMb
        min_vram_mb     = $MinVramMb
        argv = @(
            '{python}', '{bootstrap}', '--windows-standalone-build', '--listen', '127.0.0.1',
            '--port', '{port}', '--disable-async-offload', '--disable-pinned-memory',
            '--reserve-vram', '1', '--cache-ram', '6', '10', '--fast', 'fp16_accumulation',
            '--disable-all-custom-nodes', '--whitelist-custom-nodes',
            'shine_comfy_handoff', 'ComfyUI-KJNodes', 'ComfyUI-MiniMax-H3-PDD-Acc',
            'Comfyui_Minimax_h3_latent_Upscaler', 'ComfyUI-SolAttn_triton',
            'comfyui-videohelpersuite', 'shine_h3_sampling_mode.py'
        )
        pinned_files = @(
            '{python}', '{root}\main.py', '{adapter_dir}\__init__.py', '{adapter_dir}\barrier.py', '{bootstrap}'
        )
    }
    reviews = @{
        VLM = @{
            service_id       = 'shine-local-vlm'
            python           = $ReviewPython
            manifest         = $ReviewManifest
            runtime_packages = $ReviewRuntimePackages
            port             = 8116
            min_ram_mb       = 4096
            min_vram_mb      = 8000
            argv = @(
                '{python}',
                (Join-Path $repoRoot 'scripts/local_review_isolated_server.py'),
                '--manifest', '{manifest}', '--runtime-packages', '{runtime_packages}', '--port', '{port}'
            )
        }
        ASR = @{
            service_id       = 'shine-local-asr'
            python           = $ReviewPython
            manifest         = $ReviewManifest
            runtime_packages = $ReviewRuntimePackages
            port             = 8117
            min_ram_mb       = 2048
            min_vram_mb      = 4000
            argv = @(
                '{python}',
                (Join-Path $repoRoot 'scripts/local_review_isolated_server.py'),
                '--manifest', '{manifest}', '--runtime-packages', '{runtime_packages}', '--port', '{port}'
            )
        }
    }
}
$recipePath = Join-Path $runDir 'managed_recipe.json'
$recipe | ConvertTo-Json -Depth 8 | Set-Content -Path $recipePath -Encoding utf8
Write-Host "Managed recipe: $recipePath"
Write-Host "Adapter identity: $($adapterHash.Trim())"
Write-Host "GPU: $gpuUuid"
# The renderer is admitted against the memory this machine actually has free.
# The measured headroom is recorded with the evidence, and the run itself
# proves whether the render fits.
$ramFreeMb = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB)
$gpuFreeLine = (nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | Select-Object -First 1).Trim()
Write-Host "Measured headroom: RAM ${ramFreeMb} MB (required ${MinRamMb} MB), VRAM $gpuFreeLine MB (required ${MinVramMb} MB)"
if ($ramFreeMb -lt $MinRamMb) {
    Write-Warning "The machine has only ${ramFreeMb} MB of free RAM; the renderer admission needs ${MinRamMb} MB."
}

# --- the operator credential -----------------------------------------------
# The credential is generated for this run only and never written to the
# repository or to the evidence directory.
$operatorToken = [Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Maximum 256 }))
$operatorToken = 'acceptance-' + $operatorToken
$tokenDigest = & (Join-Path $repoRoot 'backend/.venv/Scripts/python.exe') -c @"
import hashlib, sys
print(hashlib.sha256(sys.argv[1].encode()).hexdigest())
"@ $operatorToken
$env:SHINE_OPERATOR_ID = 'local-acceptance-operator'
$env:SHINE_OPERATOR_TOKEN_SHA256 = $tokenDigest.Trim()

$stackStarted = $false
try {
    Write-Host 'Preparing the local reviewer routes...'
    & (Join-Path $PSScriptRoot 'prepare_local_reviewers.ps1') -DataDir $dataDir -Manifest $ReviewManifest `
        -RuntimePackages $ReviewRuntimePackages -ReviewPython $ReviewPython -ApiPort 8141 2>&1 |
        Tee-Object -FilePath (Join-Path $logDir 'prepare-reviewers.log')
    if ($LASTEXITCODE -ne 0) { throw 'The reviewer preparation failed.' }

    $env:SHINE_MANAGED_RECIPE = $recipePath
    $env:SHINE_DATA_DIR = $dataDir
    Write-Host 'Starting the local stack in ComfyUI mode...'
    & (Join-Path $PSScriptRoot 'start_shine.ps1') -Provider comfyui -ApiPort $ApiPort -WebPort $WebPort `
        -DataDir $dataDir 2>&1 | Tee-Object -FilePath (Join-Path $logDir 'start-stack.log')
    if ($LASTEXITCODE -ne 0) { throw 'The local stack did not start.' }
    $stackStarted = $true

    Write-Host 'Registering the real H3 workflow profiles...'
    & uv run --project (Join-Path $repoRoot 'backend') python (Join-Path $repoRoot 'scripts/register_ref2va_profiles.py') `
        "http://127.0.0.1:$ApiPort" 2>&1 | Tee-Object -FilePath (Join-Path $logDir 'register-profiles.log')
    if ($LASTEXITCODE -ne 0) { throw 'The workflow profiles could not be registered.' }

    Write-Host 'Seeding the real LLM provider over loopback...'
    $secretDir = $SecretSourceDir
    if (-not $secretDir) { $secretDir = Join-Path $repoRoot '.local-acceptance/closeout-20261025/secrets' }
    if (-not (Test-Path -LiteralPath $secretDir)) { throw "The source secret store is missing: $secretDir" }
    & uv run --project (Join-Path $repoRoot 'backend') python (Join-Path $repoRoot 'scripts/seed_llm_provider.py') `
        --api "http://127.0.0.1:$ApiPort" --source-secret-dir $secretDir --source-ref $SecretRef `
        --name 'minimax-agent-storyboard' --model 'MiniMax-M3.1-Flash' --evidence (Join-Path $runDir 'llm-provider-seed.json') `
        2>&1 | Tee-Object -FilePath (Join-Path $logDir 'llm-provider-seed.log')
    if ($LASTEXITCODE -ne 0) { throw 'The LLM provider seeding failed.' }

    Write-Host 'Running the real browser closure acceptance...'
    & uv run --project (Join-Path $repoRoot 'backend') --with playwright==1.62.0 python `
        (Join-Path $repoRoot 'scripts/verify_real_closure_v3.py') `
        --api "http://127.0.0.1:$ApiPort" --web "http://127.0.0.1:$WebPort" `
        --source-db $SourceDb `
        --evidence-dir $runDir --profile $Profile --operator-token $operatorToken `
        --shots $Shots --timeout-minutes $TimeoutMinutes 2>&1 | Tee-Object -FilePath (Join-Path $logDir 'real-closure.log')
    $acceptanceExit = $LASTEXITCODE
} finally {
    if ($stackStarted) {
        Write-Host 'Stopping the local stack...'
        & uv run --project (Join-Path $repoRoot 'backend') python (Join-Path $repoRoot 'scripts/stop_shine.py') `
            --data-dir $dataDir 2>&1 | Tee-Object -FilePath (Join-Path $logDir 'stop-stack.log')
    }
    Remove-Item Env:SHINE_OPERATOR_TOKEN_SHA256 -ErrorAction SilentlyContinue
    Remove-Item Env:SHINE_OPERATOR_ID -ErrorAction SilentlyContinue
    Remove-Item Env:SHINE_MANAGED_RECIPE -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host "Acceptance run: $runDir"
exit $acceptanceExit
