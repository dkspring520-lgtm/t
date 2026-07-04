$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$oldPid = (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)
if ($oldPid) {
  Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}

$env:DASHBOARD_HOST = "0.0.0.0"
$env:DASHBOARD_PORT = "8765"

$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
  $fallback = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
  if (Test-Path $fallback) {
    $python = $fallback
  }
}

if (-not $python) {
  throw "python.exe not found. Install Python or add it to PATH."
}

Start-Process -FilePath $python -ArgumentList "`"$PSScriptRoot\dashboard_app.py`"" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Write-Host "Dashboard started: http://127.0.0.1:8765/"
