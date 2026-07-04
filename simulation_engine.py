# -*- coding: utf-8 -*-
"""
做T模拟引擎
支持多种策略的回测
"""

import json
import time
from datetime import datetime, timedelta
from market_data import MarketData
from signal_detector import SignalDetector

class SimulationEngine:
    def __init__(self):
        self.market = MarketData()
        self.detector = SignalDetector()
        
    def run_simulation(self, stock_code, initial_capital=100000, strategy='zijin_special'):
        """
        运行单只股票模拟
        返回: {
            'stock_code': 股票代码,
            'stock_name': 股票名称,
            'strategy': 策略,
            'initial_capital': 初始资金,
            'final_capital': 最终资金
            'profit': 盈亏金额
            'profit_rate': 盈亏率
            'trade_count': 交易次数,
            'success_count': 成功交易次数,
            'trades': [交易记录]
        }
        """
        # 获取实时数据
        data = self.market.get_realtime_data(stock_code)
        if not data:
            return None
            
        stock_name = data['name']
        current_price = data['price']
        
        # 根据策略运行模拟
        if strategy == 'zijin_special':
            result = self._simulate_zijin_special(stock_code, stock_name, initial_capital, current_price)
        elif strategy == 'simple_bias':
            result = self._simulate_simple_bias(stock_code, stock_name, initial_capital, current_price)
        elif strategy == 'advanced_bias':
            result = self._simulate_advanced_bias(stock_code, stock_name, initial_capital, current_price)
        elif strategy == 'zijin_high_profit':
            result = self._simulate_zijin_high_profit(stock_code, stock_name, initial_capital, current_price)
        else:
            result = self._simulate_zijin_special(stock_code, stock_name, initial_capital, current_price)
        
        return result
    
    def run_batch_simulation(self, stock_codes, strategy='zijin_special', amount_per_stock=100000):
        """
        批量运行模拟
        stock_codes: 股票代码列表
        返回: {
            'summary': {
                'total_stocks': 总股票数,
                'success_count': 成功股票数
                'total_trades': 总交易次数
                'total_profit': 总盈亏
                'avg_profit_rate': 平均盈亏率
                'win_rate': 胜率
            },
            'results': [每只股票的结果]
        }
        """
        results = []
        total_trades = 0
        total_success = 0
        total_profit = 0
        success_stocks = 0
        
        for code in stock_codes:
            try:
                result = self.run_simulation(code, amount_per_stock, strategy)
                if result:
                    results.append(result)
                    total_trades += result['trade_count']
                    total_success += result['success_count']
                    total_profit += result['profit']
                    if result['profit'] > 0:
                        success_stocks += 1
                time.sleep(0.1)  # 避免请求过快
            except Exception as e:
                print(f"模拟股票 {code} 出错: {e}")
                continue
        
        # 计算汇总数据
        summary = {
            'total_stocks': len(stock_codes),
            'success_stocks': success_stocks,
            'total_trades': total_trades,
            'total_success_trades': total_success,
            'total_profit': round(total_profit, 2),
            'avg_profit_rate': round(total_profit / (amount_per_stock * len(stock_codes)) * 100, 2) if stock_codes else 0,
            'stock_win_rate': round(success_stocks / len(stock_codes) * 100, 2) if stock_codes else 0,
            'trade_win_rate': round(total_success / total_trades * 100, 2) if total_trades else 0
        }
        
        return {
            'summary': summary,
            'results': results
        }

    def run_batch_with_validation(self, stock_codes, strategy='zijin_special', amount_per_stock=100000, validation_runs=10):
        """
        运行批量模拟并进行多次验算
        validation_runs: 验算次数，默认10次
        返回: {
            'summary': 汇总数据,
            'validation_results': [每次验算结果],
            'consistency_report': 一致性报告,
            'results': [最后一次的详细结果]
        }
        """
        import statistics
        
        validation_results = []
        all_profit_rates = []
        all_win_rates = []
        
        print(f"\n========== 开始 {validation_runs} 次批量模拟验算 ==========")
        
        for run in range(validation_runs):
            print(f"\n--- 第 {run + 1}/{validation_runs} 次验算 ---")
            
            # 每次运行使用相同的股票代码但重新生成价格数据
            batch_result = self.run_batch_simulation(stock_codes, strategy, amount_per_stock)
            
            if batch_result and batch_result['summary']:
                summary = batch_result['summary']
                validation_results.append({
                    'run': run + 1,
                    'total_profit': summary['total_profit'],
                    'avg_profit_rate': summary['avg_profit_rate'],
                    'stock_win_rate': summary['stock_win_rate'],
                    'trade_win_rate': summary['trade_win_rate'],
                    'total_trades': summary['total_trades'],
                    'success_stocks': summary['success_stocks']
                })
                
                all_profit_rates.append(summary['avg_profit_rate'])
                all_win_rates.append(summary['stock_win_rate'])
                
                print(f"  平均收益率: {summary['avg_profit_rate']:.2f}%, 股票胜率: {summary['stock_win_rate']:.2f}%, 总交易: {summary['total_trades']}")
            else:
                print(f"  第 {run + 1} 次验算失败")
            
            time.sleep(0.2)  # 次之间稍作延迟
        
        # 计算统计信息
        if len(all_profit_rates) >= 2:
            profit_std = statistics.stdev(all_profit_rates)
            win_rate_std = statistics.stdev(all_win_rates)
            profit_mean = statistics.mean(all_profit_rates)
            win_rate_mean = statistics.mean(all_win_rates)
        else:
            profit_std = 0
            win_rate_std = 0
            profit_mean = all_profit_rates[0] if all_profit_rates else 0
            win_rate_mean = all_win_rates[0] if all_win_rates else 0
        
        consistency_report = {
            'runs_completed': len(validation_results),
            'profit_rate_mean': round(profit_mean, 2),
            'profit_rate_std': round(profit_std, 2),
            'profit_rate_min': round(min(all_profit_rates), 2) if all_profit_rates else 0,
            'profit_rate_max': round(max(all_profit_rates), 2) if all_profit_rates else 0,
            'stock_win_rate_mean': round(win_rate_mean, 2),
            'stock_win_rate_std': round(win_rate_std, 2),
            'consistency_score': max(0, 100 - profit_std * 10)  # 一致性得分，标准差越小分越高
        }
        
        print(f"\n========== {validation_runs} 次验算完成 ==========")
        print(f"平均收益率: {profit_mean:.2f}% ± {profit_std:.2f}%")
        print(f"股票胜率: {win_rate_mean:.2f}% ± {win_rate_std:.2f}%")
        print(f"一致性得分: {consistency_report['consistency_score']:.1f}/100")
        
        # 最终汇总使用平均值
        final_summary = {
            'total_stocks': len(stock_codes),
            'validation_runs': len(validation_results),
            'avg_profit_rate': round(profit_mean, 2),
            'stock_win_rate': round(win_rate_mean, 2),
            'consistency_score': round(consistency_report['consistency_score'], 1),
            'total_trades': sum(r['total_trades'] for r in validation_results) // len(validation_results) if validation_results else 0
        }
        
        # 获取最后一次的详细结果作为展示用
        last_result = self.run_batch_simulation(stock_codes, strategy, amount_per_stock)
        
        return {
            'summary': final_summary,
            'validation_results': validation_results,
            'consistency_report': consistency_report,
            'results': last_result['results'] if last_result else [],
            'stock_codes': stock_codes
        }
    
    def _simulate_zijin_special(self, stock_code, stock_name, capital, current_price):
        """
        紫金策略 - 基于偏离率+资金流向+技术指标
        """
        import random
        import math
        
        # 生成模拟价格数据 (240个1分钟K线)
        prices = self._generate_price_data(current_price)
        
        cash = capital * 0.5  # 50%现金
        position = int((capital * 0.5) / current_price) if current_price > 0 else 0  # 50%仓位
        # 确保position是100的整数倍且至少为0
        position = (position // 100) * 100
        avg_price = current_price if current_price > 0 else 1
        trades = []
        
        # 盈亏统计
        total_profit = 0
        
        for i, price in enumerate(prices):
            if i < 20:
                continue  # 前20分钟收集数据
            
            if price <= 0:
                continue  # 跳过无效价格
                
            # 获取信号
            signals = self._detect_signals(prices, i)
            
            # 买入信号
            if 'buy' in signals and cash > price * 100:
                shares = int(cash * 0.3 / price / 100) * 100
                if shares >= 100:
                    cash -= shares * price
                    position += shares
                    avg_price = (avg_price * (position - shares) + shares * price) / position if position > 0 else price
                    trades.append({
                        'time': f"09:{30+i//60:02d}:{i%60:02d}",
                        'type': '买入',
                        'price': round(price, 2),
                        'shares': shares
                    })
            
            # 卖出信号
            if 'sell' in signals and position >= 100:
                shares = min(int(position * 0.5 / 100) * 100, position)
                if shares >= 100:
                    profit = shares * (price - avg_price)
                    cash += shares * price
                    position -= shares
                    trades.append({
                        'time': f"09:{30+i//60:02d}:{i%60:02d}",
                        'type': '卖出',
                        'price': round(price, 2),
                        'shares': shares,
                        'profit': round(profit, 2)
                    })
                    total_profit += profit
        
        # 收盘计算
        final_value = cash + position * prices[-1] if prices else cash
        profit = final_value - capital
        
        return {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'strategy': 'zijin_special',
            'initial_capital': capital,
            'final_capital': round(final_value, 2),
            'profit': round(profit, 2),
            'profit_rate': round(profit / capital * 100, 2) if capital > 0 else 0,
            'trade_count': len(trades),
            'success_count': sum(1 for t in trades if t.get('profit', 0) > 0),
            'trades': trades
        }
    
    def _simulate_simple_bias(self, stock_code, stock_name, capital, current_price):
        """
        简单偏离率策略
        """
        prices = self._generate_price_data(current_price)
        
        cash = capital * 0.5
        position = int((capital * 0.5) / current_price) if current_price > 0 else 0
        position = (position // 100) * 100
        avg_price = current_price if current_price > 0 else 1
        trades = []
        total_profit = 0
        
        for i, price in enumerate(prices):
            if i < 10 or price <= 0:
                continue
                
            # 简单偏离率计算
            recent_avg = sum(prices[i-10:i]) / 10
            bias = (price - recent_avg) / recent_avg * 100 if recent_avg > 0 else 0
            
            if bias < -1.5 and cash > price * 100:
                shares = int(cash * 0.3 / price / 100) * 100
                if shares >= 100:
                    cash -= shares * price
                    position += shares
                    avg_price = (avg_price * (position - shares) + shares * price) / position if position > 0 else price
                    trades.append({
                        'time': f"09:{30+i//60:02d}:{i%60:02d}",
                        'type': '买入',
                        'price': round(price, 2),
                        'shares': shares
                    })
            
            if bias > 1.5 and position >= 100:
                shares = min(int(position * 0.5 / 100) * 100, position)
                if shares >= 100:
                    profit = shares * (price - avg_price)
                    cash += shares * price
                    position -= shares
                    trades.append({
                        'time': f"09:{30+i//60:02d}:{i%60:02d}",
                        'type': '卖出',
                        'price': round(price, 2),
                        'shares': shares,
                        'profit': round(profit, 2)
                    })
                    total_profit += profit
        
        final_value = cash + position * prices[-1] if prices else cash
        profit = final_value - capital
        
        return {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'strategy': 'simple_bias',
            'initial_capital': capital,
            'final_capital': round(final_value, 2),
            'profit': round(profit, 2),
            'profit_rate': round(profit / capital * 100, 2) if capital > 0 else 0,
            'trade_count': len(trades),
            'success_count': sum(1 for t in trades if t.get('profit', 0) > 0),
            'trades': trades
        }
    
    def _simulate_advanced_bias(self, stock_code, stock_name, capital, current_price):
        """
        高级偏离率策略 - 加入波动率调节
        """
        prices = self._generate_price_data(current_price)
        
        cash = capital * 0.5
        position = int((capital * 0.5) / current_price) if current_price > 0 else 0
        position = (position // 100) * 100
        avg_price = current_price if current_price > 0 else 1
        trades = []
        total_profit = 0
        
        for i, price in enumerate(prices):
            if i < 20 or price <= 0:
                continue
                
            # 计算多周期偏离率
            short_avg = sum(prices[i-5:i]) / 5
            long_avg = sum(prices[i-20:i]) / 20
            short_bias = (price - short_avg) / short_avg * 100 if short_avg > 0 else 0
            long_bias = (price - long_avg) / long_avg * 100 if long_avg > 0 else 0
            
            # 动态阈值
            volatility = self._calculate_volatility(prices[max(0,i-20):i])
            threshold = 1.0 + volatility * 2
            
            if short_bias < -threshold and long_bias < -0.5 and cash > price * 100:
                shares = int(cash * 0.3 / price / 100) * 100
                if shares >= 100:
                    cash -= shares * price
                    position += shares
                    avg_price = (avg_price * (position - shares) + shares * price) / position if position > 0 else price
                    trades.append({
                        'time': f"09:{30+i//60:02d}:{i%60:02d}",
                        'type': '买入',
                        'price': round(price, 2),
                        'shares': shares
                    })
            
            if short_bias > threshold and position >= 100:
                shares = min(int(position * 0.5 / 100) * 100, position)
                if shares >= 100:
                    profit = shares * (price - avg_price)
                    cash += shares * price
                    position -= shares
                    trades.append({
                        'time': f"09:{30+i//60:02d}:{i%60:02d}",
                        'type': '卖出',
                        'price': round(price, 2),
                        'shares': shares,
                        'profit': round(profit, 2)
                    })
                    total_profit += profit
        
        final_value = cash + position * prices[-1] if prices else cash
        profit = final_value - capital
        
        return {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'strategy': 'advanced_bias',
            'initial_capital': capital,
            'final_capital': round(final_value, 2),
            'profit': round(profit, 2),
            'profit_rate': round(profit / capital * 100, 2) if capital > 0 else 0,
            'trade_count': len(trades),
            'success_count': sum(1 for t in trades if t.get('profit', 0) > 0),
            'trades': trades
        }
    
    def _simulate_zijin_high_profit(self, stock_code, stock_name, capital, current_price):
        """
        紫金高收益率策略 - 更激进的做T策略
        """
        prices = self._generate_price_data(current_price)
        
        cash = capital * 0.6  # 更多现金做T
        position = int((capital * 0.4) / current_price) if current_price > 0 else 0
        position = (position // 100) * 100
        avg_price = current_price if current_price > 0 else 1
        trades = []
        total_profit = 0
        
        for i, price in enumerate(prices):
            if i < 30 or price <= 0:
                continue
                
            signals = self._detect_advanced_signals(prices, i)
            
            # 更激进的买入信号
            if 'strong_buy' in signals and cash > price * 100:
                shares = int(cash * 0.4 / price / 100) * 100
                if shares >= 100:
                    cash -= shares * price
                    position += shares
                    avg_price = (avg_price * (position - shares) + shares * price) / position if position > 0 else price
                    trades.append({
                        'time': f"09:{30+i//60:02d}:{i%60:02d}",
                        'type': '买入',
                        'price': round(price, 2),
                        'shares': shares
                    })
            
            elif 'buy' in signals and cash > price * 100:
                shares = int(cash * 0.25 / price / 100) * 100
                if shares >= 100:
                    cash -= shares * price
                    position += shares
                    avg_price = (avg_price * (position - shares) + shares * price) / position if position > 0 else price
                    trades.append({
                        'time': f"09:{30+i//60:02d}:{i%60:02d}",
                        'type': '买入',
                        'price': round(price, 2),
                        'shares': shares
                    })
            
            # 更激进的卖出信号
            if ('strong_sell' in signals or 'sell' in signals) and position >= 100:
                shares = min(int(position * 0.6 / 100) * 100, position)
                if shares >= 100:
                    profit = shares * (price - avg_price)
                    cash += shares * price
                    position -= shares
                    trades.append({
                        'time': f"09:{30+i//60:02d}:{i%60:02d}",
                        'type': '卖出',
                        'price': round(price, 2),
                        'shares': shares,
                        'profit': round(profit, 2)
                    })
                    total_profit += profit
        
        final_value = cash + position * prices[-1] if prices else cash
        profit = final_value - capital
        
        return {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'strategy': 'zijin_high_profit',
            'initial_capital': capital,
            'final_capital': round(final_value, 2),
            'profit': round(profit, 2),
            'profit_rate': round(profit / capital * 100, 2) if capital > 0 else 0,
            'trade_count': len(trades),
            'success_count': sum(1 for t in trades if t.get('profit', 0) > 0),
            'trades': trades
        }
    
    def _generate_price_data(self, base_price, points=240):
        """
        生成模拟价格数据
        """
        import random
        import math
        
        if base_price <= 0:
            base_price = 10.0  # 默认价格
        
        prices = [base_price]
        trend = random.choice([-1, 0, 1])  # 趋势方向
        
        for i in range(points - 1):
            # 趋势维持
            if i % 30 == 0:
                trend = random.choice([-1, 0, 1])
            
            # 基于趋势的随机波动
            base_change = trend * random.uniform(0.001, 0.005)
            noise = random.uniform(-0.003, 0.003)
            
            # 添加周期性波动(模拟市场行情)
            cycle = math.sin(i / 30 * math.pi) * 0.002
            
            change = base_change + noise + cycle
            new_price = prices[-1] * (1 + change)
            # 确保价格不为负
            new_price = max(new_price, 0.01)
            prices.append(new_price)
        
        return prices
    
    def _detect_signals(self, prices, index):
        """检测基础买卖信号"""
        signals = []
        
        if index < 10:
            return signals
        
        current = prices[index]
        ma5 = sum(prices[index-5:index]) / 5
        ma10 = sum(prices[index-10:index]) / 10
        
        if ma10 <= 0:
            return signals
        
        # 偏离率
        bias = (current - ma10) / ma10 * 100
        
        # 买入信号
        if bias < -1.5 and current > ma5:
            signals.append('buy')
        
        # 卖出信号
        if bias > 1.5 and current < ma5:
            signals.append('sell')
        
        return signals
    
    def _detect_advanced_signals(self, prices, index):
        """检测高级信号"""
        signals = []
        
        if index < 30:
            return signals
        
        current = prices[index]
        ma5 = sum(prices[index-5:index]) / 5
        ma10 = sum(prices[index-10:index]) / 10
        ma20 = sum(prices[index-20:index]) / 20
        
        if ma5 <= 0 or ma20 <= 0:
            return signals
        
        bias_short = (current - ma5) / ma5 * 100
        bias_long = (current - ma20) / ma20 * 100
        
        # 强买信号
        if bias_short < -2.0 and bias_long < -1.0 and current > ma10:
            signals.append('strong_buy')
        elif bias_short < -1.0:
            signals.append('buy')
        
        # 强卖信号
        if bias_short > 2.0 and bias_long > 1.0 and current < ma10:
            signals.append('strong_sell')
        elif bias_short > 1.0:
            signals.append('sell')
        
        return signals
    
    def _calculate_volatility(self, prices):
        """计算波动率"""
        if len(prices) < 2:
            return 0.01
        
        changes = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices)) if prices[i-1] > 0]
        if not changes:
            return 0.01
        
        try:
            import statistics
            return statistics.stdev(changes) if changes else 0.01
        except:
            return 0.01


def run_batch_simulation(stock_codes, initial_amount=100000, strategy='zijin_special'):
    """Compatibility wrapper used by older batch-test scripts."""
    normalized_codes = []
    for stock in stock_codes:
        if isinstance(stock, dict):
            code = stock.get('code') or stock.get('stock_code')
        else:
            code = stock
        if code:
            normalized_codes.append(str(code))

    engine = SimulationEngine()
    batch = engine.run_batch_simulation(
        normalized_codes,
        strategy=strategy,
        amount_per_stock=initial_amount,
    )

    legacy_results = []
    for result in batch.get('results', []):
        legacy_results.append({
            **result,
            'win_rate': result.get('profit_rate', 0),
            'total_profit': result.get('profit', 0),
        })
    return legacy_results


if __name__ == '__main__':
    engine = SimulationEngine()
    # 测试批量验算
    test_codes = ['000001', '600000', '000858', '601318', '601398']
    result = engine.run_batch_with_validation(test_codes, 'zijin_special', 100000, 10)
    print("\n验算结果:")
    print(f"一致性得分: {result['consistency_report']['consistency_score']}")
    print(f"平均股票胜率: {result['consistency_report']['stock_win_rate_mean']}%")
