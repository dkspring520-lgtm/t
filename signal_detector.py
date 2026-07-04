"""
买卖信号检测模块 - 毫秒级高频检测
基于技术分析自动判断买卖点，支持做T策略
"""
from datetime import datetime
import logging
import time

from config import (
    MA_SHORT, MA_LONG, RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MIN_PRICE_CHANGE, PROFIT_TARGET, STOP_LOSS, SIGNAL_COOLDOWN_MS
)

logger = logging.getLogger(__name__)


class SignalDetector:
    """毫秒级买卖信号检测器"""
    
    def __init__(self):
        self.last_signals = {}  # 记录每只股票的最新信号时间戳（毫秒）
        self.position_status = {}  # 记录持仓状态: {stock_code: {'has_position': bool, 'entry_price': float, 'entry_time': timestamp}}
        self.price_micro_history = {}  # 毫秒级价格历史
        
    def analyze(self, price_history, volume_history=None, stock_code='unknown'):
        """
        毫秒级分析价格历史，返回买卖信号
        适合做T的短线交易策略
        """
        if len(price_history) < 10:
            return None
        
        # 提取价格和成交量
        prices = [p['price'] for p in price_history]
        volumes = [p.get('volume', 0) for p in price_history]
        
        current_price = prices[-1]
        current_time_ms = time.time() * 1000  # 毫秒时间戳
        
        # 检查信号冷却时间（毫秒级）
        if stock_code in self.last_signals:
            elapsed_ms = current_time_ms - self.last_signals[stock_code]
            if elapsed_ms < SIGNAL_COOLDOWN_MS:
                return None
        
        # 初始化持仓状态
        if stock_code not in self.position_status:
            self.position_status[stock_code] = {
                'has_position': False,
                'entry_price': 0,
                'entry_time': 0
            }
        
        position = self.position_status[stock_code]
        
        # ========== 做T买入信号检测 ==========
        buy_signals = []
        
        # 1. 分钟级RSI超卖反弹
        rsi = self._calculate_rsi(prices, period=9)  # 短周期RSI更灵敏
        if rsi is not None and rsi < 35:
            buy_signals.append(f"RSI超卖({rsi:.1f})")
        
        # 2. 短期均线支撑
        ma_short = self._calculate_ma(prices, MA_SHORT)
        if len(ma_short) >= 2 and current_price > ma_short[-1] * 0.998:
            buy_signals.append("小时均线支撑")
        
        # 3. 快速价格回跳（小幅下跌后反弹）
        if len(prices) >= 5:
            recent_change = (current_price - prices[-5]) / prices[-5]
            if -0.005 < recent_change < -MIN_PRICE_CHANGE:  # 下跌0.3%-0.5%
                buy_signals.append("短线反弹")
        
        # 4. 成交量反弹信号
        if len(volumes) >= 5:
            vol_avg = sum(volumes[-5:-1]) / 4 if len(volumes) >= 5 else volumes[0]
            if vol_avg > 0 and volumes[-1] > vol_avg * 1.3:
                buy_signals.append("量能放大")
        
        # ========== 做T卖出信号检测 ==========
        sell_signals = []
        
        # 1. 持仓有盈利 - 达到目标盈利率
        if position['has_position']:
            profit_pct = (current_price - position['entry_price']) / position['entry_price']
            if profit_pct >= PROFIT_TARGET:
                sell_signals.append(f"目标盈利({profit_pct*100:.2f}%)")
            elif profit_pct <= -STOP_LOSS:
                sell_signals.append(f"触发止损({profit_pct*100:.2f}%)")
        
        # 2. 短期RSI超买
        if rsi is not None and rsi > 65:
            sell_signals.append(f"RSI超买({rsi:.1f})")
        
        # 3. 快速价格上涨后回落危险
        if len(prices) >= 3:
            change_3bar = (current_price - prices[-3]) / prices[-3]
            if change_3bar > MIN_PRICE_CHANGE * 2:  # 短时内涨1%以上
                sell_signals.append("短线涨幅过大")
        
        # 4. 均线压力
        if len(ma_short) >= 2 and current_price < ma_short[-1] * 1.002:
            sell_signals.append("小时均线压力")
        
        # ========== 信号决策 ==========
        # 做T逻辑：有持仓时优先考虑卖出，无持仓时考虑买入
        if position['has_position'] and sell_signals:
            # 更新持仓状态
            self.position_status[stock_code]['has_position'] = False
            self.position_status[stock_code]['entry_price'] = 0
            self.last_signals[stock_code] = current_time_ms
            
            return {
                'type': 'sell',
                'reason': ', '.join(sell_signals[:2]),  # 只显示前两个原因
                'price': current_price,
                'timestamp': datetime.now().strftime('%H:%M:%S.%f')[:-3]  # 毫秒精度
            }
        
        if not position['has_position'] and buy_signals:
            # 更新持仓状态
            self.position_status[stock_code]['has_position'] = True
            self.position_status[stock_code]['entry_price'] = current_price
            self.position_status[stock_code]['entry_time'] = current_time_ms
            self.last_signals[stock_code] = current_time_ms
            
            return {
                'type': 'buy',
                'reason': ', '.join(buy_signals[:2]),
                'price': current_price,
                'timestamp': datetime.now().strftime('%H:%M:%S.%f')[:-3]
            }
        
        return None
    
    def get_position_status(self, stock_code):
        """获取持仓状态"""
        return self.position_status.get(stock_code, {
            'has_position': False,
            'entry_price': 0,
            'entry_time': 0
        })
    
    def reset_position(self, stock_code):
        """重置持仓状态"""
        self.position_status[stock_code] = {
            'has_position': False,
            'entry_price': 0,
            'entry_time': 0
        }
    
    def _calculate_ma(self, prices, period):
        """计算移动均线"""
        if len(prices) < period:
            return []
        ma = []
        for i in range(period, len(prices) + 1):
            ma.append(sum(prices[i - period:i]) / period)
        return ma
    
    def _calculate_rsi(self, prices, period=14):
        """计算RSI指标"""
        if len(prices) < period + 1:
            return None
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i] - prices[i - 1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        if len(gains) < period:
            return None
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100 if avg_gain > 0 else 50
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_bollinger_bands(self, prices, period=20, std_dev=2):
        """计算布林带 - 用于判断超买超卖"""
        if len(prices) < period:
            return None, None, None
        
        recent_prices = prices[-period:]
        ma = sum(recent_prices) / period
        variance = sum((p - ma) ** 2 for p in recent_prices) / period
        std = variance ** 0.5
        
        upper = ma + std_dev * std
        lower = ma - std_dev * std
        
        return upper, ma, lower
