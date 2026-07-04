$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root "install_startup_task.ps1"

if (-not (Test-Path $script)) {
  throw "Missing script: $script"
}

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$script"
