$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$oldPid = (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)
if ($oldPid) {
  Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}

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
  throw "python.exe not found. Install Python or add it to PATH."
}

# The adaptive-learning worker imports pandas/numpy. Detect absent modules
# without deliberately raising ImportError: with ErrorActionPreference=Stop,
# Windows PowerShell promotes a native process traceback to NativeCommandError
# before this script can inspect LASTEXITCODE.
$missingDependencies = [string](& $python -c "import importlib.util; print(','.join(name for name in ('requests','numpy','pandas') if importlib.util.find_spec(name) is None))")
if ($missingDependencies.Trim()) {
  Write-Host "Installing missing dashboard dependencies: $missingDependencies"
  & $python -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements.txt")
  if ($LASTEXITCODE -ne 0) {
    throw "Dashboard dependencies could not be installed."
  }
}

Start-Process -FilePath $python -ArgumentList "`"$(Join-Path $PSScriptRoot dashboard_app.py)`"" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Write-Host "Dashboard started: http://127.0.0.1:8765/"
