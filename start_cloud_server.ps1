$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -Path $root

$env:DASHBOARD_HOST = "0.0.0.0"
$env:DASHBOARD_PORT = "8765"

$candidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"),
    "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe",
    "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python314\python.exe",
    (Get-Command python.exe -ErrorAction SilentlyContinue).Source
)

$python = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $python) {
  throw "python.exe not found. Install Python 3.14+ and ensure PATH is available."
}

& $python (Join-Path $root "dashboard_app.py")
