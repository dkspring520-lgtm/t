#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量模拟测试功能 - 自动获取热门股票并测试"""

import json
from datetime import datetime

# 测试批量模拟功能
def test_batch_simulation():
    from simulation_engine import run_batch_simulation, SimulationEngine
    from market_data import MarketDataFetcher
    
    fetcher = MarketDataFetcher()
    
    print("=" * 50)
    print("批量模拟测试功能测试")
    print("=" * 50)
    
    # 获取热门股票
    hot_stocks = fetcher.get_hot_stocks(limit=5)
    print(f"\n获取到 {len(hot_stocks)} 只热门股票:")
    for i, s in enumerate(hot_stocks, 1):
        print(f"  {i}. {s['code']} {s['name']}")
    
    if hot_stocks:
        # 运行批量模拟
        results = run_batch_simulation(hot_stocks, initial_amount=100000, strategy='simple_bias')
        
        print(f"\n批量模拟结果 ({len(results)} 只股票):")
        print("-" * 50)
        
        for r in results[:5]:
            status = "OK" if r['win_rate'] >= 50 else "NG"
            print(f"  [{status}] {r['stock_code']}: 胜率 {r['win_rate']}% | 净利润 {r['total_profit']:.2f}元 | 交易{r['trade_count']}次")
        
        # 统计汇总
        if results:
            avg_win_rate = sum(r['win_rate'] for r in results) / len(results)
            total_profit = sum(r['total_profit'] for r in results)
            total_trades = sum(r['trade_count'] for r in results)
            
            print(f"\n{'=' * 50}")
            print(f"汇总统计:")
            print(f"  平均胜率: {avg_win_rate:.1f}%")
            print(f"  总净利润: {total_profit:.2f}元")
            print(f"  总交易次数: {total_trades}次")
            print(f"{'=' * 50}")
            
        return True
    else:
        print("没有获取到股票数据")
        return False

if __name__ == "__main__":
    success = test_batch_simulation()
    exit(0 if success else 1)
