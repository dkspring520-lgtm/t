# A股分时数据服务

## 安装依赖

```bash
# 安装 Python 依赖
pip install flask flask-cors requests
```

## 启动服务

```bash
# 进入服务目录
cd server

# 启动服务
python app.py
```

服务会在 `http://localhost:5001` 运行

## API 接口

### 1. 获取实时行情
```
GET /api/quote/601899
```

### 2. 获取今日分时数据（带缓存）
```
GET /api/intraday/601899
```

### 3. 获取历史分时数据
```
GET /api/history/601899/20240618
# 如果没有历史数据可以添加 simulate 参数使用模拟数据
GET /api/history/601899/20240618?simulate=true
```

### 4. 获取多日历史
```
GET /api/history_days/601899/5
```

### 5. 获取热门股票
```
GET /api/hot_stocks
```

## 使用流程

1. 启动服务后，Chrome插件可以调用这些API
2. 分时数据会自动缓存到 `cache/` 目录
3. 之后的请求会优先使用缓存

## 数据格式

分时数据返回格式：
```json
{
  "success": true,
  "source": "api",
  "code": "601899",
  "date": "20240618",
  "data": {
    "trends": [
      {"time": "09:30", "price": 18.50, "volume": 12500, "avgPrice": 18.48},
      {"time": "09:31", "price": 18.45, "volume": 8900, "avgPrice": 18.47}
    ],
    "preClose": 18.52,
    "updateTime": "14:35:22"
  }
}
```

## 注意事项

- 历史分时数据可能需要VIP权限，没有的情况下可以使用模拟数据
- 服务默认缓存当天分时数据，用于后续测试
- 请不要频繁调用API，避免被封IP
