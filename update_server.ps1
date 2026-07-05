$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$oldPid = (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)
if ($oldPid) {
  Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
}

$proxy = $env:GIT_PROXY
if (-not $proxy -and (Test-Path "C:\Users\Administrator\Desktop\1.env")) {
  $envText = Get-Content "C:\Users\Administrator\Desktop\1.env" -ErrorAction SilentlyContinue
  $line = $envText | Where-Object { $_ -match "^(HTTPS_PROXY|HTTP_PROXY)=" } | Select-Object -First 1
  if ($line) {
    $proxy = ($line -split "=", 2)[1].Trim()
  }
}

if ($proxy) {
  git -c http.proxy=$proxy -c https.proxy=$proxy pull --ff-only
} else {
  git pull --ff-only
}

powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PSScriptRoot\start_dashboard_background.ps1"
Write-Host "Updated and restarted."
Write-Host "Open: http://127.0.0.1:8765/"
