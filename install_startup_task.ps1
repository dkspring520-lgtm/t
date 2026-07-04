$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root "start_dashboard_background.ps1"
$taskName = "TShenqiDashboard"
$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""

if (-not (Test-Path $script)) {
  throw "Missing script: $script"
}

schtasks /Create /TN $taskName /SC ONSTART /RU SYSTEM /RL HIGHEST /TR $action /F | Out-Host
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$script"
Write-Host "Startup task installed: $taskName"
Write-Host "Open: http://127.0.0.1:8765/"
