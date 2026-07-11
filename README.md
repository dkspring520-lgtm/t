# 做T神器

A 股日内做 T 监控、模拟复盘和选股研究的 Web 控制台。当前是 Beta 版本，重点先把账号、服务启动、策略配置和核心监控流程做稳。

> 仅用于策略研究、提醒和复盘，不构成投资建议。

## 当前能力

- 多股监控：分时曲线、黄线/VWAP 偏离、价格带、买卖点提醒。
- 做T逻辑：正T低吸、反T高抛、开盘急跌急拉、冲高回落、量价确认与仓位恢复闭环。
- 集合竞价：逐股形成开盘预案，09:35 以后按确认条件约束正T/反T方向，并支持盘中失效。
- 模拟测试：与监控台共用做T、竞价和档位参数，支持随机股票、自定义股票、胜率与失败原因统计。
- 三套档位：稳健、平衡、灵敏分别记录影子学习结果，候选策略需要人工晋级并可回滚。
- 策略自定义：用户可以粘贴个人做T规则，并同步到模拟和监控参数。
- 选股研究：保留股票研究与多维评审；已移除 RPS、龙虎榜和分类选股入口。
- 全市场雷达：覆盖沪深北 A 股有效报价样本，展示趋势、资金、个股赚钱效应和市场广度。
- AI 接入：支持 Gemini、ChatGPT/OpenAI 兼容接口、第三方中转站。
- 商业化雏形：登录注册、30 天免登录、激活码充值、用户数据隔离。

## 快速启动

```powershell
cd C:\dabao
powershell -ExecutionPolicy Bypass -File .\start_cloud_server.ps1
```

本机访问：

```text
http://127.0.0.1:8765/
```

服务器访问：

```text
http://服务器IP:8765/
```

## 开机自启

```powershell
cd C:\dabao
powershell -ExecutionPolicy Bypass -File .\install_startup_task.ps1
```

## 更新服务器

```powershell
cd C:\dabao
taskkill /F /IM python.exe
git pull --ff-only
powershell -ExecutionPolicy Bypass -File .\start_dashboard_background.ps1
```

如果 GitHub 访问慢，可以先设置代理：

```powershell
$env:GIT_PROXY="http://127.0.0.1:10808"
```

## 账号与权限

- 第一个注册用户默认是管理员。
- 登录 Cookie 保存 30 天。
- 每个账号独立保存监控股票、模拟资金、AI Key、策略配置和模拟历史。
- 管理员环境变量：

```powershell
$env:DASHBOARD_ADMINS="admin@example.com"
```

## 商业化计划

优先级：

1. 做稳 Beta：账号隔离、免登录、权限、服务自启、更新脚本。
2. 做强做T核心：监控信号、模拟复盘、黄线/量价/竞价联动。
3. 做稳研究系统：全市场数据覆盖、个股研究、雷达和数据源降级提示。
4. 完善商业包装：充值、价格页、官网、推广文案、用户后台。

## 文件说明

- `dashboard_app.py`：主 Web 服务。
- `simulate_t_random.py`：做T模拟测试。
- `auction_direction.py`：集合竞价方向确认与失效逻辑。
- `smart_t_policy.py`：正T/反T候选和竞价约束。
- `adaptive_profiles.py`：三档策略的独立影子学习、晋级与回滚。
- `rabbit_market_radar.py`：全市场雷达评分。
- `stock_t_signal.py`：实时监控和信号逻辑。
- `monitor_config.py`：股票解析和默认监控池。
- `start_cloud_server.ps1`：前台启动服务。
- `start_dashboard_background.ps1`：后台启动服务。
- `install_startup_task.ps1`：安装开机自启。
- `update_server.ps1`：拉取 GitHub 更新并重启。

## 安全提醒

正式商用前请配置 HTTPS、域名反代、数据备份、后台权限和服务器防火墙。不要提交真实 API Key、用户数据、日志和模拟历史。
