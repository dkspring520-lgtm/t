# 前后端 API 数据格式（V2.1）

推荐接口：`GET /api/rabbit/complete?symbol=601899`

返回主体：

```json
{
  "version": "2.1.0",
  "symbol": "601899",
  "auction_radar": {
    "stage": "AUCTION_PREVIEW 或 OPEN_VALIDATED",
    "data_level": "基础 或 完整",
    "gap": {"percent": 0.62, "auction_price": 28.10},
    "prediction": {
      "label": "高开低走倾向",
      "code": "HIGH_OPEN_FADE",
      "probability": 68.0,
      "confidence_label": "中等"
    },
    "reasons": ["竞价尾段价格走弱"],
    "validation": {"status": "PENDING", "label": "部分符合，继续观察"},
    "strategy_action": "等待冲高回落确认后再考虑反T"
  },
  "big_trend": {},
  "preopen": {},
  "intraday": {},
  "smart_t_context": {},
  "smart_t": {},
  "learning": {}
}
```

集合竞价输入 `auction_df`：

- 索引：09:15—09:25的时间戳；
- 必填：`virtual_price`；
- 可选：`matched_volume`、`unmatched_buy`、`unmatched_sell`；
- 无完整盘口时也能运行，但 `data_level` 会显示“基础”，置信度上限降低。

前端更新：

```js
RabbitQuantComplete.update(payload);
```
