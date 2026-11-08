# Build the distributable Windows test package from a committed tree.
#
# Why a script: the package must be reproducible and auditable.  Every byte that
# reaches a tester comes either from `git ls-files` at a named commit or from
# packaging/windows, and the result carries a per-file SHA-256 manifest.
#
# Contract:
#   * refuses a dirty working tree (BUILD_INFO must point at a real commit)
#   * never packages models, databases, secrets, evidence or virtualenvs
#   * entry scripts are placed at the package root and written with CRLF
#   * produces <name>.zip plus <name>.zip.sha256
#
# Usage:
#   pwsh -File scripts\build_test_package.ps1 -OutDir F:\releases\shine
#   pwsh -File scripts\build_test_package.ps1 -OutDir F:\releases\shine -Verify

param(
    [string]$OutDir = '',
    [string]$Version = '',
    [switch]$AllowDirty,
    [switch]$Verify,
    [switch]$KeepExpanded
)

$ErrorActionPreference = 'Stop'
# Subprocess encoding contract: Windows Git always emits UTF-8 (tracked
# filenames included).  Pin the decoder so a GBK/OEM console cannot mojibake
# paths into "Tracked file is missing on disk" failures.  The script itself
# stays UTF-8 with BOM per the packaging entry-point rules.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$packagingRoot = Join-Path $repoRoot 'packaging/windows'

Write-Host '[package] repository:' $repoRoot -ForegroundColor Gray

# The package is built from `git ls-files`, so an untracked user file (a script
# draft, a personal note) is not part of it and must not block a reproducible
# build. A modified or staged tracked file still does.
$dirty = (& git -C $repoRoot status --porcelain --untracked-files=no) -join ''
if ($dirty -and -not $AllowDirty) {
    Write-Host '[package] working tree is dirty; commit first or pass -AllowDirty:' -ForegroundColor Red
    Write-Host $dirty -ForegroundColor Red
    exit 2
}

$commit = (& git -C $repoRoot rev-parse HEAD).Trim()
$short = (& git -C $repoRoot rev-parse --short HEAD).Trim()
$branch = (& git -C $repoRoot rev-parse --abbrev-ref HEAD).Trim()
$gitTouched = (& git -C $repoRoot status --porcelain --untracked-files=no) -join ''
$sourceStatus = if ($gitTouched) { 'DIRTY_TREE' } else { 'CLEAN_TREE' }

$now = Get-Date
if (-not $Version) { $Version = "0.1.0-test.$($now.ToString('yyyyMMdd'))" }
$dirtySuffix = if ($dirty) { '-dirty' } else { '' }
$buildId = '{0}-{1}{2}' -f $now.ToString('yyyyMMdd'), $short, $dirtySuffix

if (-not $OutDir) {
    $OutDir = if ($env:SHINE_RELEASE_DIR) { $env:SHINE_RELEASE_DIR } else { Join-Path $repoRoot 'releases' }
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path

$packageName = "ShineVideoStudio-$buildId-win64"
$staging = Join-Path $OutDir $packageName
if (Test-Path -LiteralPath $staging) {
    Write-Host "[package] removing previous staging directory: $staging" -ForegroundColor Yellow
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging | Out-Null

# --- 1. Copy the committed tree -------------------------------------------
# The package must not offer a development entry point: the root dev launcher
# expects a checkout with backend/.venv and frontend/node_modules beside it,
# which an installed copy does not have. Installed users get START_APP.cmd.
$excludedPrefixes = @('packaging/', 'docs/evidence/', '启动 Shine 工作台.cmd')
$copied = 0
$skipped = @()
$raw = & git -C $repoRoot ls-files -z
$tracked = ($raw -join '').Split([char]0, [StringSplitOptions]::RemoveEmptyEntries)
foreach ($relative in $tracked) {
    $normalized = $relative.Replace('\', '/')
    $skip = $false
    foreach ($prefix in $excludedPrefixes) {
        if ($normalized.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { $skip = $true; break }
    }
    if ($skip) { $skipped += $normalized; continue }
    $source = Join-Path $repoRoot $relative
    if (-not (Test-Path -LiteralPath $source)) { throw "Tracked file is missing on disk: $normalized" }
    $destination = Join-Path $staging $relative
    $parent = Split-Path -Parent $destination
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath $source -Destination $destination -Force
    $copied++
}
Write-Host "[package] copied $copied tracked files, skipped $($skipped.Count) excluded paths" -ForegroundColor Gray

# --- 2. Entry scripts at the package root ---------------------------------
$entryScripts = @(
    '一键安装并启动.cmd',
    'SETUP.cmd',
    'START_APP.cmd',
    'STOP_APP.cmd',
    'CHECK_ENVIRONMENT.cmd'
)
foreach ($name in $entryScripts) {
    $source = Join-Path $packagingRoot $name
    if (-not (Test-Path -LiteralPath $source)) { throw "Packaging asset missing: $name" }
    $text = [IO.File]::ReadAllText($source, [Text.UTF8Encoding]::new($false))
    # cmd.exe is unreliable with LF-only batch files; force CRLF, no BOM.
    $crlf = ($text -replace "`r`n", "`n") -replace "`n", "`r`n"
    [IO.File]::WriteAllText((Join-Path $staging $name), $crlf, (New-Object Text.UTF8Encoding($false)))
}

# --- 3. Rendered documents ------------------------------------------------
$tokens = [ordered]@{
    '{{PACKAGE_VERSION}}' = $Version
    '{{BUILD_ID}}' = $buildId
    '{{SOURCE_COMMIT}}' = $commit
    '{{SOURCE_SHORT}}' = $short
    '{{BUILT_AT}}' = $now.ToString('yyyy-MM-ddTHH:mm:sszzz')
    '{{FILE_COUNT}}' = ''   # filled after the manifest is known
}
foreach ($document in @('START_HERE.md', 'TESTER_CHECKLIST.md')) {
    $source = Join-Path $packagingRoot $document
    $text = [IO.File]::ReadAllText($source, [Text.UTF8Encoding]::new($false))
    foreach ($key in $tokens.Keys) {
        if ($key -eq '{{FILE_COUNT}}') { continue }
        $text = $text.Replace($key, [string]$tokens[$key])
    }
    [IO.File]::WriteAllText((Join-Path $staging $document), $text, (New-Object Text.UTF8Encoding($false)))
}

# --- 4. Integrity manifest ------------------------------------------------
function New-FileManifest([string]$Root) {
    $files = @()
    $total = [long]0
    $rootFull = (Resolve-Path $Root).Path
    foreach ($item in (Get-ChildItem -LiteralPath $rootFull -Recurse -File | Sort-Object FullName)) {
        $relative = $item.FullName.Substring($rootFull.Length).TrimStart('\').Replace('\', '/')
        $files += [ordered]@{
            path = $relative
            bytes = $item.Length
            sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLower()
        }
        $total += $item.Length
    }
    return [ordered]@{ file_count = $files.Count; total_bytes = $total; files = $files }
}

# The package metadata is counted from the shipped catalog, not typed by hand:
# a fixed number drifts the moment a component gains or loses a file, and the
# person comparing the ZIP against the installer would see two different totals.
$catalogPath = Join-Path $repoRoot 'config/deployment/h3-ref2va-windows-x64.json'
$catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
$catalogComponents = @($catalog.components)
$catalogFileDeclarations = 0
$catalogDeclaredBytes = [long]0
foreach ($component in $catalogComponents) {
    $componentFiles = @($component.files)
    $catalogFileDeclarations += $componentFiles.Count
    foreach ($componentFile in $componentFiles) {
        $catalogDeclaredBytes += [long]$componentFile.size
    }
}
Write-Host "[package] catalog $($catalog.catalog_id): $($catalogComponents.Count) components, $catalogFileDeclarations file declarations, $catalogDeclaredBytes bytes" -ForegroundColor Gray

# Same grouping rule the installer uses: advisory reviewers are optional, the
# rest is the default production set; weights alone are the model subset.
$catalogProductionBytes = [long]0
$catalogOptionalBytes = [long]0
foreach ($component in $catalogComponents) {
    $componentBytes = [long]0
    foreach ($componentFile in @($component.files)) { $componentBytes += [long]$componentFile.size }
    $optional = ($component.kind -eq 'review-model') -or (@('VLM', 'ASR') -contains $component.role)
    if ($optional) { $catalogOptionalBytes += $componentBytes } else { $catalogProductionBytes += $componentBytes }
}
# The install requirement is the doubled on-disk footprint plus the runtime
# reserve the catalog itself declares, exactly as create_install_plan computes it.
$catalogRuntimeReserve = [long]0
if ($catalog.dependency_policy.runtime_disk.runtime_reserve_bytes) {
    $catalogRuntimeReserve = [long]$catalog.dependency_policy.runtime_disk.runtime_reserve_bytes
}
$catalogInstallBytes = ($catalogDeclaredBytes * 2) + $catalogRuntimeReserve

$buildInfo = [ordered]@{
    product = 'Shine视频工作台 / Shine Video Studio'
    package_version = $Version
    channel = 'TEST_BUILD'
    status = 'PARTIAL'
    source_branch = $branch
    source_commit = $commit
    source_short_commit = $short
    source_tree = $sourceStatus
    built_at = $now.ToString('yyyy-MM-ddTHH:mm:sszzz')
    build_id = $buildId
    purpose = 'new-machine installation and real-machine integration test'
    production_ready = $false
    contains_models = $false
    contains_generated_media = $false
    contains_database = $false
    contains_secrets = $false
    requires = [ordered]@{
        os = 'Windows 10/11 x64'
        gpu = 'NVIDIA, >= 15.3 GiB VRAM'
        ram_gib = 30
        free_disk_gib = 180
        network = 'GitHub, Hugging Face, PyPI, PyTorch official download sources'
    }
    fixed_materials = [ordered]@{
        catalog = $catalog.catalog_id
        catalog_version = $catalog.catalog_version
        statistics_basis = 'config/deployment/h3-ref2va-windows-x64.json: components[] count, components[].files[] count and sum of components[].files[].size'
        components = $catalogComponents.Count
        # Kept under its historical name; it now counts file declarations, the
        # same unit the installer and the readiness page use.
        packages = $catalogFileDeclarations
        file_declarations = $catalogFileDeclarations
        download_bytes = $catalogDeclaredBytes
        download_gib_approx = [math]::Round($catalogDeclaredBytes / 1GB, 1)
        # The complete catalog, the default production components and the
        # optional advisory components are three different numbers, all counted
        # from the same catalog with the installer's own grouping rule.
        default_production_bytes = $catalogProductionBytes
        default_production_gib_approx = [math]::Round($catalogProductionBytes / 1GB, 1)
        optional_review_bytes = $catalogOptionalBytes
        optional_review_gib_approx = [math]::Round($catalogOptionalBytes / 1GB, 1)
        install_required_bytes = $catalogInstallBytes
        install_required_gib_approx = [math]::Round($catalogInstallBytes / 1GB, 1)
        recommended_free_gib = 180
    }
    entrypoints = [ordered]@{
        one_click_install = '一键安装并启动.cmd'
        ascii_alias = 'SETUP.cmd'
        start_after_install = 'START_APP.cmd'
        stop = 'STOP_APP.cmd'
        check_environment = 'CHECK_ENVIRONMENT.cmd'
        instructions = 'START_HERE.md'
        checklist = 'TESTER_CHECKLIST.md'
    }
    auto_decisions = [ordered]@{
        install_drive = 'largest non-system fixed disk with >= 180 GiB free'
        install_root = '<drive>:\ShineVideoStudio'
        data_dir = '<install_root>\data'
        operator_password = 'not_required_for_personal_desktop'
        ports = 'API 8000 / Web 5173, advanced automatically when busy'
        browser = 'existing workspace -> /; empty workspace -> /#d/setup'
    }
    known_blockers = @(
        'a full 60.8GB download and a clean Windows install have not been exercised',
        'the three Qwen-Image-2.1 material samples have not been rendered on a newly installed machine; their weights install through their own pinned catalog',
        'no real H3 inference through a newly installed managed instance has been demonstrated',
        'the M4 60-second, two-person-dialogue acceptance film has not been run',
        'existing real films still require final human review; this build does not promote pending_review to accepted',
        'the delegated material round on the existing installation is closed (candidates reviewed and promoted, the Style Bible and the official rule pack frozen, three shots really rendered and delivered); what stays open is the final human review of that film and the VLM recalibration below',
        'the six-image Ref2VA profile that carried the clock-shop shot is project-local: it is not part of the deployment catalog, so another project that binds more than two picture references still needs its own wider profile, built from the real node and validated on the real renderer',
        'VLM recalibration is now REQUIRED and still not done: this build changes backend/shine/worker.py, which client_review_identity pins, so the published VLM revision and the frozen preproduction plan are invalid by design; a real positive/negative calibration, a new revision, the deprecation of the old one and a manual re-freeze of a newly analysed plan must happen before any real production acceptance (the formal project database is not on this machine and its qwen3-vl runtime is not running)',
        'no real H3 multi-episode unattended delivery has been run: this build closes the run-layer scheduling starvation and the creative-task lease defect offline, but a real three-episode, multi-batch run started from the Web is still outstanding',
        'no 8 hour or 24 hour continuous production has been run; waiting, sleeping and pausing never count as active production time',
        'alembic check reports two pre-existing delivery_orders index differences; normal upgrades and rollback tests pass',
        'L4 services and node contracts stay PENDING until the wizard service validation is run on the real machine',
        'the per-shot / total seconds timer is implemented against server timestamps and covered by tests, but its live per-second ticking has not been human-observed inside a real render window',
        'the H3 renderer release is proven for process and port; a GPU memory before/after reading currently exists only for the image renderer release path'
    )
    closed_in_this_build = @(
        'the whole-film run layer no longer starves its own executors: it separates "inspected" from "really changed", a reused pending delivery order is not new work, a run that only checked yields the tick to the series, the episode assembly and the ordinary queue, and several runs take turns instead of one starving the others',
        'the brief-to-script task carries a renewable lease and a durable stage: an expired claim is reconciled instead of being left RUNNING under a dead owner, an unknown outcome is never replayed, a stored answer is reused instead of paid for twice, and every write-back is fenced by status, owner and state version',
        'a whole-film integration regression now runs through the resident Worker scheduling entry: three episodes, six batches and six shots, assembled with real FFmpeg; only the render boundary is substituted',
        'the director mode ships a state-driven five-step journey: page checkmarks, a next-step bar on every page, a first-run journey map and per-stage focus on the production page',
        'one-click production start: deterministic profile pick, per-shot reference auto-binding from the workflow''s declared slots, auto shot selection, single submit',
        'a live production progress card with real phase names, shot dots, per-shot and total elapsed seconds; start-blockers hide while production runs',
        'blocking remedies are inlined: the official rulepack binds together with the style freeze, and per-project workflow registration happens on the production page',
        'a worker crash between lease acquisition and the startup journal no longer deadlocks the GPU ledger: a conservative orphan-lease proof (dead job, no live recorded process, free managed port) releases it with an audit event',
        'the real user journey ran end-to-end on the real H3 stack: 2/2 shots passed, auto delivery v1 (18.85 s, 1344x768)',
        'dialogue-free shots no longer quote the frozen sound-performance dimension nor carry a human-voice reference: the foreign-language muttering is gone (user-confirmed on the re-render)',
        'delivery orders sort newest-first with a Latest badge and collapsed history',
        'the installer creates a desktop one-click shortcut on both the fresh-install and the already-installed paths',
        'three shooting-quality tiers (PDD 8-step / native 20-step / PDD 8-step + upscale) come from the upstream canonical graphs, catalog 1.2.0',
        'reviewer routes are created and bound to the actually installed 8B VLM and 0.6B ASR',
        'L4 readiness is derived from sealed service evidence instead of a hard-coded PENDING',
        'child process TMPDIR no longer falls back to the system drive',
        'production completion (PASSED) is separated from acceptance_status',
        'disk budget, package count and download size match the catalog (180 GiB / 154 / 60.8 GB)',
        'the acceptance script no longer depends on a personal external ComfyUI checkout',
        'the Web workbench has four sections with plain-language navigation and five new-user flow fixes',
        'style freeze compares all seven approved fields and consumes the grant in the same write transaction',
        'concurrent global workflow registrations are protected by a partial unique database index (f150a0020001)',
        'a stale process record whose PID was recycled by Windows can no longer block the single entry point',
        'the launcher requires a live API, a live page and the recorded listener identity before reporting "already running"',
        'a package start advances both ports when the defaults are busy, and the dev launcher archives unverifiable records instead of killing unknown processes',
        'the five crew roles are served the finished editorial segmentation, so the director copies the timing instead of inventing it',
        'an ungroupable cut table and a beat count that cannot match the table are refused before any model call',
        'the workbench closes the ComfyUI and local reviewer processes it started, and the launcher releases leftover backends before a clean start',
        'the director mode can prepare AI material (character, scene, prop) through a pinned Qwen-Image-2.1 workflow: the server splits the script, the producer writes one appearance brief per requirement, and the candidates stay on the ordinary asset ledger until a human promotes them',
        'the three material samples really rendered through the workbench-owned managed renderer on the existing installation: renderer_owned attempts, technical/decode QC pass, RENDER_VERIFIED for character/scene/prop, renderer stopped with GPU back to baseline',
        'the first adoption takes over the read-only detection row of the same identity instead of refusing with INSTANCE_IDENTITY_CHANGED',
        'the browser acceptance gate (verify_v13_web.py) is green end-to-end, twice in a row, with no API errors',
        'an unknown scene result is never replayed: the second identical prepare returns the original task and the provider is called once (durable input identity, atomic claim)',
        'source segmentation is lossless: the preamble before the first heading is retained, scenes beyond the span or character budgets keep splitting, and citations may not cross the current input range',
        'material candidates are chosen one by one from a responsive grid with thumbnails, contain-fit previews, zoom and per-requirement selection; the confirm action only promotes what the user actually selected, and validation samples are never offered as production candidates',
        'material strategy and existing-asset binding are restored from the authoritative snapshot with explicit asset/version selectors; an explicit asset id, version or hash wins over identity matching and is strictly validated',
        'remote text providers work through a persisted, scoped consent that the worker verifies; a request flag can no longer grant its own consent',
        'failed or unknown material items have in-place resume, reconcile and regenerate actions; the worker no longer re-claims a failed item in a loop and a busy worker keeps its heartbeat',
        'the deliver page shows the real H3 generation segments (editorial cuts inside one segment are not re-take units), with single-segment retake, dependency invalidation, selection and a new delivery revision that never overwrites passed media',
        'long scripts are produced in steps: a confirmed overall outline, user-chosen production sections, durable worker scheduling with pause/cancel/resume and precise invalidation of changed sections only',
        'the carry-over between production sections is versioned: a section whose shots all really passed freezes into an immutable end state; a later section queues without user facts only against that verified state, a forged render claim is overwritten on the server, and a superseded end state refuses the run',
        'the status probe answers once for an idle-stopped renderer, so a healthy machine waiting for work no longer shows production state unavailable',
        'the image renderer no longer leaves its own marks on the shared H3 instance row, so runtime activation completes after image work',
        'a delegated material round ran end-to-end on the real H3 stack: 12 reviewed candidates promoted to real masters, materials_ready judged by the server, the Style Bible and the official rule pack frozen, the five-role crew chain planning three shots, and 3/3 shots really rendered (real technical QC, real decode) into one delivered cut of 22.60 s at 1344x768',
        'a project-local Ref2VA profile with one subject plus five picture slots, built from the canonical pdd8 graph on the real node COMFY_AUTOGROW_V3 image group (max=9) and validated against the live renderer, so a shot really carries every approved master it binds',
        'a real run no longer fails because a leftover managed renderer still owns the port: the release action stops it with Job Object ownership proof, the attempt that never reached a renderer is closed without resubmission, and the GPU lease is released only after the renderer is provably gone',
        'the three director pages stay mounted and only toggle `hidden`, so switching pages keeps the expanded material stage, the selected candidate and an open shot preview; the `[hidden]` rule is global, so a page style can no longer render a hidden panel',
        'material candidates are one dense grid instead of one row per batch, an entry with no image yet still occupies a cell (a pending clarification no longer disappears from the grid), and batch/attempt detail folds into a collapsible block',
        'a produced shot really plays in the workbench: `GET /api/v1/jobs/{job_id}/media` serves that job''s own artifact and `GET /api/v1/jobs/{job_id}/thumbnail` its poster frame, both digest-checked; the segment grid shows posters and opens a player',
        'generated material files are named `{nn}-{entity}.png`, numbered by the scene in which the entity first appears in the script, so sorting by file name follows the script and the storyboard; hand-uploaded material keeps its own file name'
    )
    excluded_from_package = @(
        'models and weights',
        'generated media',
        'SQLite databases',
        'secrets and .env',
        'virtual environments and node_modules',
        'docs/evidence and acceptance evidence',
        'logs'
    )
    license_note = 'Model, node and source licenses are not changed by this package. MiniMax-H3 community license restrictions and local-law compliance must be reviewed before publication or commercial use.'
}
[IO.File]::WriteAllText((Join-Path $staging 'BUILD_INFO.json'), ($buildInfo | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))

# The manifest never describes itself: a file that lists its own hash can never
# match. Resolve the count now that every document exists, substitute it, then
# hash the final bytes once.
$preliminary = New-FileManifest $staging
$finalCount = $preliminary.file_count
$startHere = Join-Path $staging 'START_HERE.md'
$startHereText = [IO.File]::ReadAllText($startHere, [Text.UTF8Encoding]::new($false)).Replace('{{FILE_COUNT}}', [string]$finalCount)
[IO.File]::WriteAllText($startHere, $startHereText, (New-Object Text.UTF8Encoding($false)))

# Hash the final bytes now that every document exists, then write the manifest.
$manifest = New-FileManifest $staging
$manifestDocument = [ordered]@{
    package_version = $Version
    build_id = $buildId
    source_commit = $commit
    status = 'TEST_BUILD / PARTIAL'
    file_count = $manifest.file_count
    total_bytes = $manifest.total_bytes
    note = 'This manifest does not list itself; every other packaged file is hashed.'
    files = $manifest.files
}
[IO.File]::WriteAllText((Join-Path $staging 'PACKAGE_MANIFEST.json'),
    ($manifestDocument | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))

Write-Host "[package] staged $($manifest.file_count) files, $($manifest.total_bytes) bytes" -ForegroundColor Gray

# --- 5. Zip ---------------------------------------------------------------
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zipPath = Join-Path $OutDir ($packageName + '.zip')
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
[IO.Compression.ZipFile]::CreateFromDirectory($staging, $zipPath, [IO.Compression.CompressionLevel]::Optimal, $true)
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLower()
$zipBytes = (Get-Item -LiteralPath $zipPath).Length
[IO.File]::WriteAllText("$zipPath.sha256", "$zipHash  $([IO.Path]::GetFileName($zipPath))`n", (New-Object Text.UTF8Encoding($false)))
Write-Host "[package] $zipPath" -ForegroundColor Green
Write-Host "[package] $zipBytes bytes  sha256=$zipHash" -ForegroundColor Green

if (-not $KeepExpanded) {
    Remove-Item -LiteralPath $staging -Recurse -Force
    Write-Host '[package] staging directory removed (pass -KeepExpanded to keep it)' -ForegroundColor Gray
}

# --- 6. Verify the produced archive --------------------------------------
if ($Verify) {
    $verifyRoot = Join-Path $OutDir 'verify-expand'
    if (Test-Path -LiteralPath $verifyRoot) { Remove-Item -LiteralPath $verifyRoot -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $verifyRoot | Out-Null
    Expand-Archive -LiteralPath $zipPath -DestinationPath $verifyRoot -Force
    $expanded = Join-Path $verifyRoot $packageName
    if (-not (Test-Path -LiteralPath $expanded)) { throw 'Expanded package root is missing.' }
    $names = @('一键安装并启动.cmd', 'SETUP.cmd', 'START_APP.cmd', 'STOP_APP.cmd', 'CHECK_ENVIRONMENT.cmd',
        'START_HERE.md', 'TESTER_CHECKLIST.md', 'BUILD_INFO.json', 'PACKAGE_MANIFEST.json')
    foreach ($name in $names) {
        if (-not (Test-Path -LiteralPath (Join-Path $expanded $name))) { throw "Missing after extraction: $name" }
    }
    $expandedManifest = Get-Content -LiteralPath (Join-Path $expanded 'PACKAGE_MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $actual = @(Get-ChildItem -LiteralPath $expanded -Recurse -File).Count
    if ($actual -ne ($expandedManifest.file_count + 1)) {
        throw "Extracted file count $actual does not match manifest $($expandedManifest.file_count) + 1"
    }
    $mismatch = @()
    foreach ($entry in $expandedManifest.files) {
        $target = Join-Path $expanded $entry.path
        $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLower()
        if ($hash -ne $entry.sha256) { $mismatch += $entry.path }
    }
    if ($mismatch.Count -gt 0) {
        Write-Host "[package] hash mismatch after extraction: $($mismatch -join ', ')" -ForegroundColor Red
        exit 3
    }
    Write-Host "[package] verified $actual files by SHA-256 after Expand-Archive" -ForegroundColor Green
    Remove-Item -LiteralPath $verifyRoot -Recurse -Force
}

Write-Host ''
Write-Host "ZIP:    $zipPath"
Write-Host "SHA256: $zipHash"
Write-Host "SIZE:   $zipBytes bytes"
