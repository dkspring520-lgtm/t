$ErrorActionPreference = "Stop"
$env:DASHBOARD_HOST = "0.0.0.0"
$env:DASHBOARD_PORT = "8765"
Set-Location -Path $PSScriptRoot
python dashboard_app.py
