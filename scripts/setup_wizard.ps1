# Native Windows setup wizard for the Shine test package.
#
# Contract:
#   * the tester still double-clicks exactly one entry file;
#   * the wizard shows the real installer output and the real drive
#     inventory - no percentage that was not measured;
#   * every gate of the console path stays in place: no administrator
#     rights, loopback only, no model weights, no operator password;
#   * without a desktop session (service, CI, recovery shell) this script
#     falls back to scripts\install_once.ps1 unchanged, so the console
#     path stays the recovery entry.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_wizard.ps1
#   powershell ... -File scripts\setup_wizard.ps1 -Console   (force console)
#   powershell ... -File scripts\setup_wizard.ps1 -StorageRoot D:\Shine

param(
    [string]$StorageRoot = '',
    [switch]$Console,
    [switch]$NoLaunch,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
} catch { }

$scriptRoot = $PSScriptRoot
$packageRoot = (Resolve-Path (Join-Path $scriptRoot '..')).Path
$installScript = Join-Path $scriptRoot 'install_once.ps1'
$requiredBytes = 180GB

function Invoke-ConsoleInstaller {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installScript)
    if ($StorageRoot) { $arguments += @('-StorageRoot', $StorageRoot) }
    if ($NoLaunch) { $arguments += '-NoLaunch' }
    if ($CheckOnly) { $arguments += '-CheckOnly' }
    & (Get-Command powershell.exe).Source @arguments
    exit $LASTEXITCODE
}

function Test-GuiAvailable {
    if (-not [Environment]::UserInteractive) { return $false }
    if ($env:SESSIONNAME -eq 'Services') { return $false }
    return $true
}

if ($Console -or -not (Test-Path -LiteralPath $installScript) -or -not (Test-GuiAvailable)) {
    Invoke-ConsoleInstaller
}

# --- Package verification -------------------------------------------------

function Get-PackageVerification {
    $manifestPath = Join-Path $packageRoot 'PACKAGE_MANIFEST.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        return [pscustomobject]@{ State = 'MISSING'; Checked = 0; Mismatched = @(); Detail = '未找到 PACKAGE_MANIFEST.json；这可能是开发目录，不是发布包。' }
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return [pscustomobject]@{ State = 'UNREADABLE'; Checked = 0; Mismatched = @(); Detail = 'PACKAGE_MANIFEST.json 无法解析：' + $_.Exception.Message }
    }
    $files = @($manifest.files)
    if ($files.Count -eq 0) {
        return [pscustomobject]@{ State = 'UNREADABLE'; Checked = 0; Mismatched = @(); Detail = 'PACKAGE_MANIFEST.json 没有文件清单。' }
    }
    $mismatched = @()
    $checked = 0
    foreach ($entry in $files) {
        $target = Join-Path $packageRoot $entry.path
        if (-not (Test-Path -LiteralPath $target)) {
            $mismatched += $entry.path
            continue
        }
        try {
            $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLower()
            $checked += 1
            if ($hash -ne $entry.sha256) { $mismatched += $entry.path }
        } catch {
            $mismatched += $entry.path
        }
    }
    $state = 'OK'
    $detail = '已按清单校验 ' + $checked + ' 个文件，大小 ' + [math]::Round($manifest.total_bytes / 1GB, 2) + ' GiB。'
    if ($mismatched.Count -gt 0) {
        $state = 'MISMATCH'
        $detail = '有 ' + $mismatched.Count + ' 个文件与清单不一致：' + (($mismatched | Select-Object -First 5) -join ', ')
    }
    return [pscustomobject]@{ State = $state; Checked = $checked; Mismatched = $mismatched; Detail = $detail }
}

function Get-InstallDriveInventory {
    $items = @()
    try {
        $items = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop | ForEach-Object {
            [pscustomobject]@{
                Root = $_.DeviceID
                Name = $_.VolumeName
                FreeBytes = [int64]$_.FreeSpace
                TotalBytes = [int64]$_.Size
                System = ($_.DeviceID -eq $env:SystemDrive)
            }
        })
    } catch {
        return @()
    }
    return $items
}

function Select-DefaultDrive([object[]]$drives) {
    if (-not $drives -or $drives.Count -eq 0) { return '' }
    $eligible = @($drives | Where-Object { -not $_.System -and $_.FreeBytes -ge $requiredBytes } | Sort-Object -Property @{Expression = { $_.FreeBytes }; Descending = $true })
    if ($eligible.Count -gt 0) { return $eligible[0].Root }
    $fallback = @($drives | Where-Object { -not $_.System } | Sort-Object -Property @{Expression = { $_.FreeBytes }; Descending = $true })
    if ($fallback.Count -gt 0) { return $fallback[0].Root }
    return $drives[0].Root
}

$verification = Get-PackageVerification
$drives = Get-InstallDriveInventory
$defaultDrive = Select-DefaultDrive $drives

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Shine 视频工作台 - 安装向导'
$form.ClientSize = New-Object System.Drawing.Size(900, 660)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $true

$titleLabel = New-Object System.Windows.Forms.Label
$titleLabel.Text = 'Shine 视频工作台 · 首次安装'
$titleLabel.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 14, [System.Drawing.FontStyle]::Bold)
$titleLabel.AutoSize = $true
$titleLabel.Location = New-Object System.Drawing.Point(20, 18)

$introLabel = New-Object System.Windows.Forms.Label
$introLabel.Text = ('本向导只在本机安装工具与依赖，不申请管理员权限，不改动系统级 Python 或 Node，' +
    '不监听非本机地址，也不下载模型权重。模型与审片组件由工作台向导在你确认后单独处理。')
$introLabel.Size = New-Object System.Drawing.Size(860, 46)
$introLabel.Location = New-Object System.Drawing.Point(20, 54)

$manifestLabel = New-Object System.Windows.Forms.Label
$manifestLabel.Size = New-Object System.Drawing.Size(860, 40)
$manifestLabel.Location = New-Object System.Drawing.Point(20, 108)
$manifestLabel.Text = '正在校验分发包清单……'

$driveLabel = New-Object System.Windows.Forms.Label
$driveLabel.Text = '安装位置（默认选择非系统盘）：'
$driveLabel.AutoSize = $true
$driveLabel.Location = New-Object System.Drawing.Point(20, 162)

$driveCombo = New-Object System.Windows.Forms.ComboBox
$driveCombo.DropDownStyle = 'DropDownList'
$driveCombo.Size = New-Object System.Drawing.Size(360, 26)
$driveCombo.Location = New-Object System.Drawing.Point(200, 158)

$driveHint = New-Object System.Windows.Forms.Label
$driveHint.Size = New-Object System.Drawing.Size(680, 34)
$driveHint.Location = New-Object System.Drawing.Point(20, 190)

$privacyCheck = New-Object System.Windows.Forms.CheckBox
$privacyCheck.Text = '我已阅读上面的说明，允许本向导在选定目录写入文件。'
$privacyCheck.AutoSize = $true
$privacyCheck.Location = New-Object System.Drawing.Point(20, 228)

$startButton = New-Object System.Windows.Forms.Button
$startButton.Text = '开始安装'
$startButton.Size = New-Object System.Drawing.Size(140, 34)
$startButton.Location = New-Object System.Drawing.Point(20, 262)

$closeButton = New-Object System.Windows.Forms.Button
$closeButton.Text = '关闭'
$closeButton.Size = New-Object System.Drawing.Size(120, 34)
$closeButton.Location = New-Object System.Drawing.Point(172, 262)

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = '等待开始'
$statusLabel.AutoSize = $true
$statusLabel.Location = New-Object System.Drawing.Point(310, 270)

$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Style = 'Marquee'
$progressBar.MarqueeAnimationSpeed = 30
$progressBar.Size = New-Object System.Drawing.Size(860, 10)
$progressBar.Location = New-Object System.Drawing.Point(20, 306)
$progressBar.Visible = $false

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = 'Vertical'
$logBox.Size = New-Object System.Drawing.Size(860, 320)
$logBox.Location = New-Object System.Drawing.Point(20, 326)
$logBox.BackColor = [System.Drawing.Color]::FromArgb(24, 24, 24)
$logBox.ForeColor = [System.Drawing.Color]::Gainsboro
$logBox.Font = New-Object System.Drawing.Font('Consolas', 9)

$form.Controls.AddRange(@($titleLabel, $introLabel, $manifestLabel, $driveLabel, $driveCombo,
    $driveHint, $privacyCheck, $startButton, $closeButton, $statusLabel, $progressBar, $logBox))

function Write-WizardLog([string]$text) {
    if ([string]::IsNullOrWhiteSpace($text)) { return }
    $logBox.AppendText($text + [Environment]::NewLine)
    $logBox.SelectionStart = $logBox.TextLength
    $logBox.ScrollToCaret()
}

# --- Preflight results -----------------------------------------------------

if ($verification.State -eq 'OK') {
    $manifestLabel.ForeColor = [System.Drawing.Color]::FromArgb(20, 110, 40)
    $manifestLabel.Text = '分发包校验通过：' + $verification.Detail
} elseif ($verification.State -eq 'MISSING') {
    $manifestLabel.ForeColor = [System.Drawing.Color]::FromArgb(150, 100, 0)
    $manifestLabel.Text = '分发包校验：' + $verification.Detail
} else {
    $manifestLabel.ForeColor = [System.Drawing.Color]::Firebrick
    $manifestLabel.Text = '分发包校验未通过：' + $verification.Detail
}

foreach ($drive in $drives) {
    $label = $drive.Root + '  ' + [math]::Round($drive.FreeBytes / 1GB, 1) + ' GiB 可用'
    if ($drive.System) { $label += '（系统盘，不推荐）' }
    [void]$driveCombo.Items.Add($label)
}
if ($defaultDrive) {
    for ($index = 0; $index -lt $drives.Count; $index += 1) {
        if ($drives[$index].Root -eq $defaultDrive) { $driveCombo.SelectedIndex = $index; break }
    }
}
$driveHint.Text = '安装至少需要 180 GiB 空闲空间；默认选择空闲最多的非系统盘。'

# --- Install run -----------------------------------------------------------

$script:logText = ''
$script:completed = 0
$script:process = $null
$script:logFile = $null
$script:errorFile = $null

function Get-SelectedStorageRoot {
    $index = $driveCombo.SelectedIndex
    if ($index -lt 0 -or $index -ge $drives.Count) { return '' }
    return $drives[$index].Root
}

function Update-LogView {
    foreach ($path in @($script:logFile, $script:errorFile)) {
        if (-not $path -or -not (Test-Path -LiteralPath $path)) { continue }
        try {
            $all = [IO.File]::ReadAllText($path)
        } catch { continue }
        if ($all.Length -le $script:logText.Length) { continue }
        $new = $all.Substring($script:logText.Length)
        $script:logText = $all
        foreach ($line in [regex]::Split($new, '?
')) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            Write-WizardLog $line
            if ($line -match '[OK]') { $script:completed += 1 }
            if ($line -match '[失败]') { $statusLabel.Text = '安装未完成（详见下方日志）' }
        }
    }
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    Update-LogView
    if ($script:process -and $script:process.HasExited) {
        $timer.Stop()
        $progressBar.Visible = $false
        $code = $script:process.ExitCode
        if ($code -eq 0) {
            $statusLabel.Text = '安装完成，工作台向导已打开。'
            Write-WizardLog '安装完成。'
        } else {
            $statusLabel.Text = '安装未完成，退出码 ' + $code + '（详见下方日志）。'
            Write-WizardLog ('安装未完成，退出码 ' + $code + '。')
        }
        $startButton.Enabled = $true
        $startButton.Text = '重新安装'
    } elseif ($script:process) {
        $statusLabel.Text = '安装中：已完成 ' + $script:completed + ' 个步骤'
    }
})

$startButton.Add_Click({
    if (-not $privacyCheck.Checked) {
        [System.Windows.Forms.MessageBox]::Show('请先确认上面的说明。', 'Shine 安装向导',
            [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
        return
    }
    if ($script:process -and -not $script:process.HasExited) { return }
    $root = Get-SelectedStorageRoot
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installScript)
    if ($root) { $arguments += @('-StorageRoot', $root) }
    if ($NoLaunch) { $arguments += '-NoLaunch' }
    if ($CheckOnly) { $arguments += '-CheckOnly' }
    $script:logFile = Join-Path $env:TEMP ('shine-setup-' + [Guid]::NewGuid().ToString('N') + '.log')
    $script:errorFile = $script:logFile + '.err'
    $script:logText = ''
    $script:completed = 0
    $logBox.Clear()
    Write-WizardLog '开始安装。下面的每一行都来自安装器本身，没有估算的进度。'
    $startButton.Enabled = $false
    $progressBar.Visible = $true
    $statusLabel.Text = '安装中：已完成 0 个步骤'
    try {
        $script:process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $script:logFile -RedirectStandardError $script:errorFile
    } catch {
        $progressBar.Visible = $false
        $statusLabel.Text = '安装器无法启动：' + $_.Exception.Message
        $startButton.Enabled = $true
        return
    }
    $timer.Start()
})

$closeButton.Add_Click({
    if ($script:process -and -not $script:process.HasExited) {
        $answer = [System.Windows.Forms.MessageBox]::Show('安装仍在进行。关闭窗口不会停止安装器，但下次打开仍可重新附着到同一个安装。确定关闭？',
            'Shine 安装向导', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($answer -ne [System.Windows.Forms.DialogResult]::Yes) { return }
    }
    $form.Close()
})

$form.Add_Shown({
    Write-WizardLog ('安装根目录候选：' + (($drives | ForEach-Object { $_.Root }) -join ', '))
    Write-WizardLog ('分发包校验：' + $verification.Detail)
})

[void]$form.ShowDialog()
$timer.Stop()
exit 0
