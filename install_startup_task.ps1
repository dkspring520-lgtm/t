$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root "start_dashboard_background.ps1"
$taskName = "A股做T监控后台"
$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
schtasks /Create /TN $taskName /SC ONLOGON /TR $action /F | Out-Host
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$script"
Write-Host "已安装开机自启动：http://127.0.0.1:8765/"
