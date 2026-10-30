# Proves that a stopped local stack released every resource it owned.
#
# The evidence is the observation itself: the listening sockets of the owned
# ports, the processes of this project, the GPU memory, and the stop log.
# The same text is rendered into a PNG so the receipt can be read without a
# terminal.  Nothing of the desktop is captured.
param(
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$DataDir = '',
    [string[]]$Ports = @('8140', '5183', '8188', '8116', '8117'),
    [string]$StopLog = ''
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Get-ListenerReport([string[]]$Owned) {
    $rows = foreach ($port in $Owned) {
        $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        [pscustomobject]@{
            Port = $port
            Listening = $listeners.Count -gt 0
            Pids = ($listeners | ForEach-Object { $_.OwningProcess }) -join ','
        }
    }
    return $rows
}

function Get-ProjectProcesses {
    return @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe' OR Name='pythonw.exe'" |
        Where-Object {
            $_.CommandLine -like '*shine*' -or $_.CommandLine -like '*ComfyUI*' -or
            $_.CommandLine -like '*managed_comfy_bootstrap*' -or $_.CommandLine -like '*vite*'
        } |
        Select-Object ProcessId, Name, CommandLine)
}

$listeners = Get-ListenerReport $Ports
$processes = Get-ProjectProcesses
$gpu = (nvidia-smi --query-gpu=uuid,memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader)
$gpuLines = @($gpu)

$portText = ($listeners | ForEach-Object {
    "port {0,-6} listening={1,-5} pids={2}" -f $_.Port, $_.Listening, $_.Pids
}) -join "`r`n"
$processText = if ($processes.Count) {
    ($processes | ForEach-Object { "pid=$($_.ProcessId) name=$($_.Name) cmd=$($_.CommandLine)" }) -join "`r`n"
} else {
    "no project python/node process remains"
}
$gpuText = ($gpuLines -join "`r`n")

$summary = [ordered]@{
    captured_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    data_dir = $DataDir
    owned_ports = $Ports
    listeners = @($listeners)
    residual_project_processes = @($processes | ForEach-Object {
        @{pid = $_.ProcessId; name = $_.Name; command_line = $_.CommandLine}
    })
    gpu = $gpuLines
    all_owned_ports_free = -not ($listeners | Where-Object { $_.Listening })
    no_residual_project_process = ($processes.Count -eq 0)
    verdict = if (-not ($listeners | Where-Object { $_.Listening }) -and $processes.Count -eq 0) { 'PASS' } else { 'FAIL' }
}
$summary | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $OutDir 'release-evidence.json') -Encoding utf8
$portText | Set-Content -Path (Join-Path $OutDir 'ports-after-stop.txt') -Encoding utf8
$processText | Set-Content -Path (Join-Path $OutDir 'processes-after-stop.txt') -Encoding utf8
$gpuText | Set-Content -Path (Join-Path $OutDir 'gpu-after-stop.txt') -Encoding utf8

# The receipt as an image: the same observation, rendered.
Add-Type -AssemblyName System.Drawing
$lines = @(
    'Shine video studio - resource release receipt',
    ("captured: " + $summary.captured_at),
    '',
    '== owned ports =='
) + @($portText -split "`r`n") + @('', '== project processes ==') + @($processText -split "`r`n") +
    @('', '== gpu ==') + @($gpuText -split "`r`n") + @('', ("verdict: " + $summary.verdict))
$font = New-Object System.Drawing.Font('Consolas', 11)
$brush = [System.Drawing.Brushes]::Black
$background = [System.Drawing.Brushes]::White
$width = 1000
$height = 40 + ($lines.Count * 18)
$bitmap = New-Object System.Drawing.Bitmap $width, $height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.FillRectangle($background, 0, 0, $width, $height)
$graphics.DrawString(($lines -join "`r`n"), $font, $brush, 12, 10)
$graphics.Dispose()
$bitmap.Save((Join-Path $OutDir 'release-receipt.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$bitmap.Dispose()
$font.Dispose()

if ($StopLog -and (Test-Path -LiteralPath $StopLog)) {
    Copy-Item -LiteralPath $StopLog -Destination (Join-Path $OutDir 'stop-stack.log') -Force
}

Write-Output ("verdict: " + $summary.verdict)
Write-Output ("evidence: " + $OutDir)
exit $(if ($summary.verdict -eq 'PASS') { 0 } else { 1 })
