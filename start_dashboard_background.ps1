$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:DASHBOARD_HOST = "127.0.0.1"
$env:DASHBOARD_PORT = "8765"
Set-Location -Path $root

$existing = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
  exit 0
}

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
  $pythonw = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if ($pythonw) {
  Start-Process -FilePath $pythonw -ArgumentList "`"$root\dashboard_app.py`"" -WorkingDirectory $root -WindowStyle Hidden
}
