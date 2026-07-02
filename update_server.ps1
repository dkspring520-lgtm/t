$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

git pull

$pidValue = (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)
if ($pidValue) {
  Stop-Process -Id $pidValue -Force
  Start-Sleep -Seconds 1
}

$env:DASHBOARD_HOST = "0.0.0.0"
$env:DASHBOARD_PORT = "8765"

$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
  $python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
}

Start-Process -FilePath $python -ArgumentList "`"$PSScriptRoot\dashboard_app.py`"" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Write-Host "已更新并重启：http://0.0.0.0:8765/"
