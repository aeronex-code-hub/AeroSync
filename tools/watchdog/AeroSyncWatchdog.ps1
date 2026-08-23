param(
  [int[]]$RequiredPorts = @(19000,19002,19003,19004,19006,19007),
  [int]$StartupGraceSeconds = 45,
  [int]$HealthIntervalSeconds = 3,
  [int]$RestartDelaySeconds = 5
)

$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$DataDir = Join-Path $Root 'data'
$LogDir = Join-Path $DataDir 'logs'
$WatchdogLog = Join-Path $LogDir 'watchdog.log'
$ServerScript = Join-Path $Root 'app\server.py'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$script:CoreProcess = $null
$script:ManualStop = $false
$script:CoreStartedAt = $null
$script:RestartPendingAt = $null
$script:AllowExit = $false
$script:TrayRegistered = $false
$script:TrayRegisterLogged = $false
$script:AppContext = New-Object System.Windows.Forms.ApplicationContext

function Write-WatchdogLog([string]$Level,[string]$Message) {
  $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),$Level,$Message
  Add-Content -LiteralPath $WatchdogLog -Value $line -Encoding UTF8
}

# Prevent more than one Watchdog GUI from running at the same time.
$createdNew = $false
$script:WatchdogMutex = New-Object System.Threading.Mutex($true,'Global\AeroSyncWatchdogGUI',[ref]$createdNew)
if (-not $createdNew) {
  Write-WatchdogLog 'WARN' 'Another Watchdog GUI instance is already running. This instance will exit.'
  exit 0
}

function Get-PythonExecutable {
  try {
    $pythonCmd = Get-Command python.exe -ErrorAction Stop
    if ($pythonCmd -and $pythonCmd.Source) { return $pythonCmd.Source }
  } catch {}

  try {
    $pyCmd = Get-Command py.exe -ErrorAction Stop
    if ($pyCmd) {
      $resolved = (& $pyCmd.Source -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
      if ($resolved -and (Test-Path -LiteralPath $resolved.Trim())) { return $resolved.Trim() }
    }
  } catch {}
  return $null
}

$script:PythonExe = Get-PythonExecutable

function Get-CoreProcessFromPort {
  try {
    $conn = Get-NetTCPConnection -State Listen -LocalPort 19000 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn -and $conn.OwningProcess) {
      return Get-Process -Id ([int]$conn.OwningProcess) -ErrorAction SilentlyContinue
    }
  } catch {}
  return $null
}

function Get-CoreProcessFromCommandLine {
  try {
    $needle = [regex]::Escape($ServerScript)
    $rows = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
      $_.CommandLine -and ($_.CommandLine -match $needle -or $_.CommandLine -match 'app[\\/]server\.py')
    }
    foreach ($row in $rows) {
      $p = Get-Process -Id ([int]$row.ProcessId) -ErrorAction SilentlyContinue
      if ($p) { return $p }
    }
  } catch {}
  return $null
}

function Find-CoreProcess {
  $p = Get-CoreProcessFromPort
  if ($p) { return $p }
  return Get-CoreProcessFromCommandLine
}

function Attach-CoreProcess {
  $p = Find-CoreProcess
  if (-not $p) {
    $script:CoreProcess = $null
    return $false
  }

  if (-not $script:CoreProcess -or $script:CoreProcess.Id -ne $p.Id) {
    $script:CoreProcess = $p
    try { $script:CoreStartedAt = $p.StartTime } catch { $script:CoreStartedAt = Get-Date }
    Write-WatchdogLog 'INFO' "Attached to existing AeroSync Core PID $($p.Id)"
  }
  return $true
}

function Test-CoreHealthy {
  if (-not (Attach-CoreProcess)) { return $false }
  try {
    if ($script:CoreProcess.HasExited) { return $false }
  } catch { return $false }

  if ($script:CoreStartedAt -and (((Get-Date) - $script:CoreStartedAt).TotalSeconds -lt $StartupGraceSeconds)) { return $true }
  try {
    $listening = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort -Unique)
    return (@($RequiredPorts | Where-Object { $listening -notcontains $_ }).Count -eq 0)
  } catch {
    return $true
  }
}

function Start-Core {
  # First attach to any already-running AeroSync process. Never create a duplicate.
  if (Attach-CoreProcess) {
    $script:RestartPendingAt = $null
    return
  }

  if (-not $script:PythonExe -or -not (Test-Path -LiteralPath $ServerScript)) {
    Write-WatchdogLog 'ERROR' 'Python executable or app\server.py not found.'
    return
  }

  $si = New-Object System.Diagnostics.ProcessStartInfo
  $si.FileName = $script:PythonExe
  $si.Arguments = '"' + $ServerScript + '"'
  $si.WorkingDirectory = $Root
  $si.UseShellExecute = $false
  $si.CreateNoWindow = $true

  $p = New-Object System.Diagnostics.Process
  $p.StartInfo = $si
  try {
    [void]$p.Start()
    $script:CoreProcess = $p
    try { $script:CoreStartedAt = $p.StartTime } catch { $script:CoreStartedAt = Get-Date }
    $script:RestartPendingAt = $null
    Write-WatchdogLog 'INFO' "AeroSync Core started PID $($p.Id)"
  } catch {
    Write-WatchdogLog 'ERROR' "Failed to start AeroSync Core: $($_.Exception.Message)"
  }
}

function Stop-Core([bool]$Intentional=$true) {
  if ($Intentional) { $script:ManualStop = $true }
  $script:RestartPendingAt = $null

  [void](Attach-CoreProcess)
  if ($script:CoreProcess) {
    try {
      $pidToStop = $script:CoreProcess.Id
      & taskkill.exe /PID $pidToStop /T /F | Out-Null
      Write-WatchdogLog 'INFO' "AeroSync Core stopped PID $pidToStop"
    } catch {
      Write-WatchdogLog 'ERROR' "Unable to stop AeroSync Core: $($_.Exception.Message)"
    }
  }
  $script:CoreProcess = $null
  if ($Intentional) { $script:CoreStartedAt = $null }
}

function Restart-Core {
  $script:ManualStop = $true
  Stop-Core $false
  Start-Sleep -Milliseconds 900
  $script:ManualStop = $false
  Start-Core
  Write-WatchdogLog 'INFO' 'Manual restart requested.'
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Aero Sync - Watchdog'
$form.StartPosition = 'CenterScreen'
$form.ClientSize = New-Object System.Drawing.Size(520,300)
$form.MinimumSize = $form.Size
$form.MaximumSize = $form.Size
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.ShowInTaskbar = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(15,22,31)
$form.ForeColor = [System.Drawing.Color]::White
$form.Font = New-Object System.Drawing.Font('Segoe UI',9)

# Branding icon is used only for the Windows title bar and system tray.
# It is intentionally not displayed anywhere inside the Watchdog window.
$script:AppIcon = $null
$iconPath = Join-Path $PSScriptRoot 'aerosync.ico'
try {
  if (Test-Path -LiteralPath $iconPath) {
    $script:AppIcon = New-Object System.Drawing.Icon($iconPath)
    $form.Icon = $script:AppIcon
  }
} catch {
  Write-WatchdogLog 'WARN' "Unable to load AeroSync icon: $($_.Exception.Message)"
}


$title = New-Object System.Windows.Forms.Label
$title.Text = 'AERO SYNC - Watchdog'
$title.Font = New-Object System.Drawing.Font('Segoe UI Semibold',16)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(20,14)
$form.Controls.Add($title)

$credit = New-Object System.Windows.Forms.Label
$credit.Text = 'Designed & Developed by AERO NEX FZCO'
$credit.ForeColor = [System.Drawing.Color]::Silver
$credit.AutoSize = $true
$credit.Location = New-Object System.Drawing.Point(22,46)
$form.Controls.Add($credit)

$statusPanel = New-Object System.Windows.Forms.Panel
$statusPanel.Location = New-Object System.Drawing.Point(18,70)
$statusPanel.Size = New-Object System.Drawing.Size(484,62)
$statusPanel.BackColor = [System.Drawing.Color]::FromArgb(23,35,48)
$form.Controls.Add($statusPanel)

$statusDot = New-Object System.Windows.Forms.Panel
$statusDot.Size = New-Object System.Drawing.Size(16,16)
$statusDot.Location = New-Object System.Drawing.Point(20,23)
$statusDot.BackColor = [System.Drawing.Color]::Gold
$dotPath = New-Object System.Drawing.Drawing2D.GraphicsPath
$dotPath.AddEllipse(0,0,15,15)
$statusDot.Region = New-Object System.Drawing.Region($dotPath)
$statusPanel.Controls.Add($statusDot)

$stateLabel = New-Object System.Windows.Forms.Label
$stateLabel.Text = 'Aero Sync - STARTING'
$stateLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold',13)
$stateLabel.AutoSize = $true
$stateLabel.Location = New-Object System.Drawing.Point(54,19)
$statusPanel.Controls.Add($stateLabel)

function New-ControlButton([string]$Text,[int]$X) {
  $b = New-Object System.Windows.Forms.Button
  $b.Text = $Text
  $b.Size = New-Object System.Drawing.Size(140,40)
  $b.Location = New-Object System.Drawing.Point($X,145)
  $b.FlatStyle = 'Flat'
  $b.BackColor = [System.Drawing.Color]::FromArgb(36,55,76)
  $b.ForeColor = [System.Drawing.Color]::White
  $b.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(67,91,119)
  $form.Controls.Add($b)
  return $b
}
$stopBtn = New-ControlButton 'Stop' 18
$startBtn = New-ControlButton 'Start' 190
$restartBtn = New-ControlButton 'Restart' 362

$healthLabel = New-Object System.Windows.Forms.Label
$healthLabel.Text = 'System Healthy'
$healthLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold',11)
$healthLabel.AutoSize = $true
$healthLabel.Location = New-Object System.Drawing.Point(20,205)
$form.Controls.Add($healthLabel)

$uptimeLabel = New-Object System.Windows.Forms.Label
$uptimeLabel.Text = 'Uptime: --'
$uptimeLabel.AutoSize = $true
$uptimeLabel.Location = New-Object System.Drawing.Point(20,232)
$form.Controls.Add($uptimeLabel)

$footer = New-Object System.Windows.Forms.Label
$footer.Text = 'Watchdog 1.0.6'
$footer.ForeColor = [System.Drawing.Color]::Gray
$footer.AutoSize = $true
$footer.Location = New-Object System.Drawing.Point(20,272)
$form.Controls.Add($footer)

# Use a Windows-owned icon object for maximum tray compatibility.
$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Icon = if ($script:AppIcon) { $script:AppIcon } else { [System.Drawing.SystemIcons]::Application }
$tray.Text = 'AeroSync Watchdog - Starting'
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miOpen = $menu.Items.Add('Open')
$miStart = $menu.Items.Add('Start AeroSync')
$miStop = $menu.Items.Add('Stop AeroSync')
$miRestart = $menu.Items.Add('Restart AeroSync')
[void]$menu.Items.Add('-')
$miExit = $menu.Items.Add('Exit Watchdog')
$tray.ContextMenuStrip = $menu

function Ensure-TrayIcon {
  try {
    if (-not $tray.Visible) {
      $tray.Visible = $true
      [System.Windows.Forms.Application]::DoEvents()
      Start-Sleep -Milliseconds 100
      [System.Windows.Forms.Application]::DoEvents()
    }
    $script:TrayRegistered = [bool]$tray.Visible
    if ($script:TrayRegistered -and -not $script:TrayRegisterLogged) {
      Write-WatchdogLog 'INFO' 'System tray icon registered.'
      $script:TrayRegisterLogged = $true
    }
    return $script:TrayRegistered
  } catch {
    $script:TrayRegistered = $false
    Write-WatchdogLog 'ERROR' "System tray initialization failed: $($_.Exception.Message)"
    return $false
  }
}

function Restore-Window {
  $form.ShowInTaskbar = $true
  $form.Show()
  $form.WindowState = 'Normal'
  $form.Activate()
}

function Hide-ToTray {
  if (Ensure-TrayIcon) {
    $form.ShowInTaskbar = $false
    $form.Hide()
    Write-WatchdogLog 'INFO' 'Watchdog window minimized to system tray.'
  } else {
    $form.ShowInTaskbar = $true
    $form.WindowState = 'Normal'
    $form.Show()
    Write-WatchdogLog 'WARN' 'Tray icon unavailable; Watchdog window kept visible.'
    [System.Windows.Forms.MessageBox]::Show('The Windows tray icon could not be created. The Watchdog will remain visible so it can still be controlled.','Aero Sync - Watchdog',[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
  }
}

$tray.Add_DoubleClick({ Restore-Window })
$miOpen.Add_Click({ Restore-Window })
$miStart.Add_Click({ $script:ManualStop=$false; Start-Core })
$miStop.Add_Click({ Stop-Core $true })
$miRestart.Add_Click({ Restart-Core })
$miExit.Add_Click({
  $script:AllowExit=$true
  try { $tray.Visible=$false } catch {}
  try { $timer.Stop() } catch {}
  try { $form.Close() } catch {}
  try { $script:AppContext.ExitThread() } catch {}
})

$stopBtn.Add_Click({ Stop-Core $true })
$startBtn.Add_Click({ $script:ManualStop=$false; Start-Core })
$restartBtn.Add_Click({ Restart-Core })

$form.Add_SizeChanged({
  if ($form.WindowState -eq 'Minimized') {
    [void]$form.BeginInvoke([System.Action]{ Hide-ToTray })
  }
})

$form.Add_FormClosing({ param($s,$e)
  if (-not $script:AllowExit) {
    $e.Cancel=$true
    Hide-ToTray
  }
})

$form.Add_Shown({
  $ok = Ensure-TrayIcon
  Write-WatchdogLog 'INFO' ("Tray visible at startup: {0}; ApartmentState={1}" -f $ok,[System.Threading.Thread]::CurrentThread.ApartmentState)
})

function Format-Uptime([timespan]$span) {
  '{0}D {1:00}H {2:00}M' -f [math]::Floor($span.TotalDays),$span.Hours,$span.Minutes
}

function Refresh-Ui {
  $running = Attach-CoreProcess
  $healthy = $false
  if ($running) { $healthy = Test-CoreHealthy }

  if ($running -and $healthy) {
    $statusDot.BackColor = [System.Drawing.Color]::LimeGreen
    $stateLabel.Text='Aero Sync - RUNNING'
    $stateLabel.ForeColor=[System.Drawing.Color]::LimeGreen
    $healthLabel.Text='System Healthy'
    $healthLabel.ForeColor=[System.Drawing.Color]::LimeGreen
    $tray.Text='AeroSync Watchdog - Running'
  } elseif ($running) {
    $statusDot.BackColor = [System.Drawing.Color]::Gold
    $stateLabel.Text='Aero Sync - STARTING'
    $stateLabel.ForeColor=[System.Drawing.Color]::Gold
    $healthLabel.Text='Starting / Checking'
    $healthLabel.ForeColor=[System.Drawing.Color]::Gold
    $tray.Text='AeroSync Watchdog - Starting'
  } else {
    $statusDot.BackColor = [System.Drawing.Color]::Tomato
    $stateLabel.Text='Aero Sync - STOPPED'
    $stateLabel.ForeColor=[System.Drawing.Color]::Tomato
    $healthLabel.Text = if ($script:ManualStop) {'Stopped by Administrator'} else {'Aero Sync Stopped'}
    $healthLabel.ForeColor=[System.Drawing.Color]::Tomato
    $tray.Text='AeroSync Watchdog - Stopped'
  }

  [void](Ensure-TrayIcon)
  $startBtn.Enabled = -not $running
  $stopBtn.Enabled = $running
  $restartBtn.Enabled = $running
  $miStart.Enabled = -not $running
  $miStop.Enabled = $running
  $miRestart.Enabled = $running

  if ($running -and $script:CoreStartedAt) {
    $uptimeLabel.Text='Uptime: ' + (Format-Uptime ((Get-Date)-$script:CoreStartedAt))
  } else {
    $uptimeLabel.Text='Uptime: --'
  }
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = [Math]::Max(1000,$HealthIntervalSeconds*1000)
$timer.Add_Tick({
  $running = Attach-CoreProcess

  if (-not $script:ManualStop) {
    if ($running) {
      if (-not (Test-CoreHealthy)) {
        Write-WatchdogLog 'ERROR' 'AeroSync Core unhealthy. Restart scheduled.'
        Stop-Core $false
        $script:RestartPendingAt=(Get-Date).AddSeconds($RestartDelaySeconds)
      }
    } elseif (-not $script:RestartPendingAt -or (Get-Date) -ge $script:RestartPendingAt) {
      Start-Core
    }
  }
  Refresh-Ui
})

Write-WatchdogLog 'INFO' "Watchdog GUI 1.0.6 started. Root=$Root"
Start-Core
Refresh-Ui
$timer.Start()

# Run a standard WinForms application message loop owned by an ApplicationContext.
# This keeps NotifyIcon registered even while the main form is hidden.
$script:AppContext.MainForm = $form
[void](Ensure-TrayIcon)
$form.Show()
try {
  [System.Windows.Forms.Application]::Run($script:AppContext)
} finally {
  try { $timer.Stop() } catch {}
  try { $tray.Visible=$false } catch {}
  try { $tray.Dispose() } catch {}
  try { $form.Dispose() } catch {}
  try { if ($script:AppIcon) { $script:AppIcon.Dispose() } } catch {}
  try { $script:AppContext.Dispose() } catch {}
  if ($script:WatchdogMutex) {
    try { $script:WatchdogMutex.ReleaseMutex() } catch {}
    try { $script:WatchdogMutex.Dispose() } catch {}
  }
}
