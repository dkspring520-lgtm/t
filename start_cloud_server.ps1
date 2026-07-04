$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

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

& $python "$PSScriptRoot\dashboard_app.py"
