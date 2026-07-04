"""
A股做T监控软件 - 配置文件
"""

# 默认监控的股票代码（6位数字）
STOCK_CODES = []

# 交易时间设置
TRADING_HOURS = {
    'morning_start': '09:30',
    'morning_end': '11:30',
    'afternoon_start': '13:00',
    'afternoon_end': '15:00'
}

# 监控间隔（毫秒级别）
MONITOR_INTERVAL = 0.1  # 100ms - 毫秒级高频监控

# 自动做T设置
AUTO_TRADE_ENABLED = True  # 开启自动做T
POSITION_SIZE_PERCENT = 0.1  # 每笔交易占总资金比例(10%)
MIN_PRICE_CHANGE = 0.003  # 最小价格变动阈值(0.3%)
MAX_POSITIONS = 3  # 最大持仓数量
PROFIT_TARGET = 0.005  # 目标盈利(0.5%)
STOP_LOSS = 0.003  # 止损阈值(0.3%)
SIGNAL_COOLDOWN_MS = 500  # 信号冷却时间（毫秒）

# 语音提醒设置
VOICE_ENABLED = True
VOICE_RATE = 180  # 语速

# 技术指标参数
MA_SHORT = 5    # 短期均线
MA_LONG = 20    # 长期均线
RSI_PERIOD = 14  # RSI周期
RSI_OVERSOLD = 30   # RSI超卖线
RSI_OVERBOUGHT = 70  # RSI超买线

# 买卖点判断参数
PRICE_CHANGE_THRESHOLD = 0.02  # 价格变动阈值（2%）
VOLUME_SPIKE = 2.0  # 成交量突增倍数
