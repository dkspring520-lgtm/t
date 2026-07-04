# 部署说明

## Windows Server 快速部署

项目放到：

```powershell
C:\dabao
```

启动服务：

```powershell
cd C:\dabao
powershell -ExecutionPolicy Bypass -File .\start_cloud_server.ps1
```

后台启动：

```powershell
cd C:\dabao
powershell -ExecutionPolicy Bypass -File .\start_dashboard_background.ps1
```

安装开机自启：

```powershell
cd C:\dabao
powershell -ExecutionPolicy Bypass -File .\install_startup_task.ps1
```

更新并重启：

```powershell
cd C:\dabao
powershell -ExecutionPolicy Bypass -File .\update_server.ps1
```

## 端口

安全组和 Windows 防火墙放行 TCP `8765`。

```powershell
New-NetFirewallRule -DisplayName "TShenqiDashboard8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
```

访问地址：

```text
http://服务器IP:8765/
```

## GitHub 访问代理

如果服务器访问 GitHub 慢，可以设置：

```powershell
$env:GIT_PROXY="http://127.0.0.1:10808"
```

或在服务器桌面 `1.env` 写入：

```text
HTTPS_PROXY=http://127.0.0.1:10808
HTTP_PROXY=http://127.0.0.1:10808
```

`update_server.ps1` 会自动读取。

## 当前 Beta 说明

- 账号登录 Cookie 有效期为 30 天。
- 每个账号的监控股票、AI Key、策略配置、模拟历史独立保存。
- 第一个注册用户默认是管理员。
- 管理员也可以通过环境变量指定：

```powershell
$env:DASHBOARD_ADMINS="admin@example.com"
```

## 安全建议

正式商用前建议增加 HTTPS、域名反代、备份用户数据、限制后台管理入口。
