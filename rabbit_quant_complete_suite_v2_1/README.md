# Rabbit Quant 完整研判与自成长智能做T V2.1

本包将之前几套模块合并为一个可直接接入网页的完整版本：

1. **集合竞价雷达**：09:25预判高开低走、低开高走、高开高走、低开低走或方向不明；
2. **09:35开盘验证**：至少两项条件成立才显示“已确认”；
3. **股票大趋势与未来5/20/60日倾向**；
4. **盘前、盘中走势研判**；
5. **智能做T**：上涨回踩、震荡高抛低吸、下跌反弹反T；
6. **超买超卖反转确认和完整风控**；
7. **自成长模块**：全量记录、收盘贴标签、周度挑战版、影子验证、手动晋级与回滚；
8. **可爱兔兔前端组件**。

## 最快预览

进入 `frontend` 目录启动静态服务器：

```bash
python -m http.server 8080
```

浏览器打开：`http://localhost:8080/demo.html`

> 不要直接双击 `demo.html`，部分浏览器会阻止本地 `fetch` JSON。

## 网页接入

```html
<div id="rabbit-complete"></div>
<script src="/static/strategy-growth-card.js"></script>
<script src="/static/rabbit-quant-intelligence.js"></script>
<script src="/static/rabbit-auction-radar.js"></script>
<script src="/static/rabbit-quant-complete.js"></script>
<script>
RabbitQuantComplete.mount('#rabbit-complete', {
  avatarUrl: '/static/assets/rabbit-avatar.png'
});
fetch('/api/rabbit/complete?symbol=601899')
  .then(r => r.json())
  .then(data => RabbitQuantComplete.update(data));
</script>
```

## Python 一次生成完整数据

```python
from backend.complete_integration import build_complete_payload

signals, trades, payload = build_complete_payload(
    symbol="601899",
    daily_df=daily_df,
    minute_df=minute_df,
    previous_close=previous_close,
    auction_df=auction_df,
    benchmark_daily=index_daily,
    sector_daily=sector_daily,
    benchmark_auction={"change_pct": 0.12},
    sector_auction={"change_pct": -0.08},
    avg_auction_volume_20d=auction_avg_volume,
)
```

## 集合竞价时间逻辑

- 09:15—09:20：低权重观察；
- 09:20—09:25：核心预判；
- 09:25：只生成“倾向”，不直接买卖；
- 09:30—09:35：使用开盘价、VWAP、首根K线高低点和高低点结构验证；
- 09:35后：至少两项确认才允许影响智能做T方向。

## 重要原则

- 集合竞价不能单独生成真实买卖指令；
- 预测存在“方向不明”状态；
- 盘中只记录，学习参数不在盘中修改；
- 硬风控不允许学习模块放宽；
- 默认用于提醒、模拟和影子验证，不建议直接自动下单。

## 运行测试

```bash
pip install -r requirements.txt
pytest -q
python examples/complete_demo.py
```
