# 部署说明

## Windows Server

1. 安装 Python 3.11 或 3.12，并勾选 `Add python.exe to PATH`。
2. 上传项目到 `C:\dabao`。
3. 开放 Windows 防火墙：

```powershell
New-NetFirewallRule -DisplayName "A股做T监控8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
```

4. 云服务器安全组放行 TCP 8765。
5. 启动：

```powershell
cd C:\dabao
$env:DASHBOARD_HOST="0.0.0.0"
$env:DASHBOARD_PORT="8765"
python dashboard_app.py
```

## 开机自启动

```powershell
cd C:\dabao
powershell -ExecutionPolicy Bypass -File .\install_startup_shortcut.ps1
```

## Linux 服务器

```bash
cd /opt/dabao
DASHBOARD_HOST=0.0.0.0 DASHBOARD_PORT=8765 python3 dashboard_app.py
```

建议用 systemd 或 supervisor 守护进程。

## 更新

```powershell
cd C:\dabao
git pull
$pidValue = (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)
if ($pidValue) { Stop-Process -Id $pidValue -Force }
$env:DASHBOARD_HOST="0.0.0.0"
$env:DASHBOARD_PORT="8765"
python dashboard_app.py
```
