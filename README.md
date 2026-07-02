# A股做T监控与选股研究

一个面向 A 股日内做T和中期选股研究的本地/服务器 Web 控制台。核心思路是把分时黄线、VWAP、量能、开盘急拉急跌、外盘期货快照、新闻异动核验、多角色评审和 AI 复核合到一个简洁界面里。

> 仅用于策略研究、提醒和复盘，不构成投资建议。

## 主要功能

- 多股票实时监控：价格、涨跌、分时曲线、黄线偏离、买卖点提示
- 做T信号：正T低吸、反T高抛、开盘急跌急拉、冲高回落观察
- 盘前风向：按主监控股票自动匹配黄金、白银、铜、原油、美元、离岸人民币等外盘因子
- 快速异动核验：急拉/急跌时自动检查利好利空消息，避免无消息追涨或突发利空接刀
- 模拟测试：随机股票做T模拟、历史统计、失败原因复盘、策略参数迭代
- 选股研究：稳健中期、激进成长、评审团选股
- 评审团选股：TradingAgents 风格多角色 + UZI 深度评审思路 + Kronos 路径因子
- AI 配置：用户可在设置里自定义 Gemini Key、模型、代理地址
- 服务器部署：支持 `DASHBOARD_HOST=0.0.0.0` 对外访问

## 快速启动

```powershell
cd C:\dabao
python dashboard_app.py
```

默认访问：

```text
http://127.0.0.1:8765/
```

服务器公网部署：

```powershell
$env:DASHBOARD_HOST="0.0.0.0"
$env:DASHBOARD_PORT="8765"
python dashboard_app.py
```

然后访问：

```text
http://服务器公网IP:8765/
```

## Windows 开机启动

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup_shortcut.ps1
```

这会在当前用户启动目录写入快捷方式。登录服务器后自动启动后台服务。

## AI 配置

可以在网页右上角“设置”里填写 Gemini Key，也可以使用环境变量：

```powershell
$env:GEMINI_API_KEY="你的Key"
```

或参考 `.env.example`。

## Git 更新服务器

推荐服务器使用 Git 更新：

```powershell
cd C:\dabao
git pull
$pidValue = (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)
if ($pidValue) { Stop-Process -Id $pidValue -Force }
$env:DASHBOARD_HOST="0.0.0.0"
$env:DASHBOARD_PORT="8765"
python dashboard_app.py
```

## 商业化方向

- 体验版：单股监控、基础提醒、盘前风向
- 专业版：多股监控、模拟复盘、AI 买卖点复核
- 包年版：专业版全部功能、优先更新、策略模板和云端支持

可扩展方向：

- 用户账号和权限
- 支付订阅
- 策略模板市场
- 用户自定义做T逻辑
- 云端数据缓存和回测
- 微信/浏览器/短信多渠道提醒

## 安全提醒

公网部署前建议：

- 加登录保护
- 配置 HTTPS
- 安全组只允许自己的 IP
- 不要提交 API Key、用户账号、日志、模拟历史

