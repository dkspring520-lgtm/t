#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股分时数据抓取服务
提供实时行情和历史分时数据API
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import json
import re
from datetime import datetime, timedelta
import os

app = Flask(__name__)
CORS(app)  # 允许跨域请求

# 数据缓存目录
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)


def get_eastmoney_code(stock_code):
    """获取东方财富格式的股票代码"""
    if stock_code.startswith('6'):
        return f"1.{stock_code}"  # 上海
    elif stock_code.startswith('0') or stock_code.startswith('3'):
        return f"0.{stock_code}"  # 深市/创业板
    return f"0.{stock_code}"


def get_cache_path(stock_code, date_str):
    """获取缓存文件路径"""
    return os.path.join(CACHE_DIR, f"{stock_code}_{date_str}.json")


def load_cached_data(stock_code, date_str):
    """加载缓存的分时数据"""
    cache_path = get_cache_path(stock_code, date_str)
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def save_cache_data(stock_code, date_str, data):
    """保存分时数据到缓存"""
    cache_path = get_cache_path(stock_code, date_str)
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route('/')
def index():
    """首页"""
    return jsonify({
        'status': 'ok',
        'service': 'A股分时数据API',
        'version': '1.0',
        'endpoints': [
            '/api/quote/<code> - 获取实时行情',
            '/api/intraday/<code> - 获取今日分时',
            '/api/history/<code>/<date> - 获取历史分时',
            '/api/history_days/<code>/<days> - 获取多日历史',
        ]
    })


@app.route('/api/quote/<stock_code>')
def get_quote(stock_code):
    """获取实时行情"""
    try:
        em_code = get_eastmoney_code(stock_code)
        url = f'https://push2.eastmoney.com/api/qt/stock/get'
        params = {
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': 2,
            'invt': 2,
            'voltage': 2,
            'fields': 'f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f170',
            'secid': em_code,
            '_': int(datetime.now().timestamp() * 1000)
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data and data.get('data'):
            d = data['data']
            return jsonify({
                'success': True,
                'code': stock_code,
                'name': d.get('f58', ''),
                'current': d.get('f43', 0) / 100,
                'open': d.get('f46', 0) / 100,
                'high': d.get('f44', 0) / 100,
                'low': d.get('f45', 0) / 100,
                'prevClose': d.get('f60', 0) / 100,
                'volume': d.get('f47', 0),
                'turnover': d.get('f48', 0),
                'change': d.get('f170', 0) / 100,
                'time': datetime.now().strftime('%H:%M:%S')
            })
        
        return jsonify({'success': False, 'error': '无数据'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/intraday/<stock_code>')
def get_intraday(stock_code):
    """获取今日分时数据"""
    try:
        today = datetime.now().strftime('%Y%m%d')
        
        # 先检查缓存
        cached = load_cached_data(stock_code, today)
        if cached:
            return jsonify({
                'success': True,
                'source': 'cache',
                'code': stock_code,
                'date': today,
                'data': cached
            })
        
        # 获取实时分时
        em_code = get_eastmoney_code(stock_code)
        url = 'https://push2.eastmoney.com/api/qt/stock/trends2'
        params = {
            'secid': em_code,
            'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58',
            'iscr': 0,
            'ndays': 1,
            '_': int(datetime.now().timestamp() * 1000)
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data and data.get('data') and data['data'].get('trends'):
            trends_str = data['data']['trends']
            pre_close = data['data'].get('preClose', 0)
            
            # 解析分时数据
            trends = []
            for item in trends_str.split(';'):
                parts = item.split(',')
                if len(parts) >= 4:
                    time_str = parts[0]
                    price = float(parts[1])
                    volume = int(parts[2])
                    avg_price = float(parts[3])
                    
                    # 格式化时间
                    if len(time_str) == 4:
                        hours = time_str[:2]
                        minutes = time_str[2:]
                        formatted_time = f"{hours}:{minutes}"
                    else:
                        formatted_time = time_str
                    
                    trends.append({
                        'time': formatted_time,
                        'price': price,
                        'volume': volume,
                        'avgPrice': avg_price
                    })
            
            result = {
                'trends': trends,
                'preClose': pre_close,
                'updateTime': datetime.now().strftime('%H:%M:%S')
            }
            
            # 保存缓存
            save_cache_data(stock_code, today, result)
            
            return jsonify({
                'success': True,
                'source': 'api',
                'code': stock_code,
                'date': today,
                'data': result
            })
        
        return jsonify({'success': False, 'error': '无分时数据'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/history/<stock_code>/<date_str>')
def get_history(stock_code, date_str):
    """获取指定日期的历史分时数据
    date_str: YYYYMMDD 格式
    """
    try:
        # 先检查缓存
        cached = load_cached_data(stock_code, date_str)
        if cached:
            return jsonify({
                'success': True,
                'source': 'cache',
                'code': stock_code,
                'date': date_str,
                'data': cached
            })
        
        # 尝试获取历史数据
        em_code = get_eastmoney_code(stock_code)
        
        # 东方财富历史分时接口（可能需要调整）
        url = 'https://push2.eastmoney.com/api/qt/stock/trends2'
        params = {
            'secid': em_code,
            'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58',
            'iscr': 0,
            'ndays': 1,
            'startdate': date_str,
            '_': int(datetime.now().timestamp() * 1000)
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data and data.get('data') and data['data'].get('trends'):
            trends_str = data['data']['trends']
            pre_close = data['data'].get('preClose', 0)
            
            trends = []
            for item in trends_str.split(';'):
                parts = item.split(',')
                if len(parts) >= 4:
                    trends.append({
                        'time': f"{parts[0][:2]}:{parts[0][2:4]}",
                        'price': float(parts[1]),
                        'volume': int(parts[2]),
                        'avgPrice': float(parts[3])
                    })
            
            result = {
                'trends': trends,
                'preClose': pre_close,
                'updateTime': datetime.now().strftime('%H:%M:%S')
            }
            
            # 保存缓存
            save_cache_data(stock_code, date_str, result)
            
            return jsonify({
                'success': True,
                'source': 'api',
                'code': stock_code,
                'date': date_str,
                'data': result
            })
        
        # 如果API没有返回，生成模拟数据用于测试
        if request.args.get('simulate', 'false').lower() == 'true':
            mock_data = generate_mock_intraday(stock_code, date_str)
            return jsonify({
                'success': True,
                'source': 'mock',
                'code': stock_code,
                'date': date_str,
                'data': mock_data,
                'note': '使用模拟数据'
            })
        
        return jsonify({'success': False, 'error': '无历史数据'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/history_days/<stock_code>/<int:days>')
def get_history_days(stock_code, days):
    """获取多日历史分时数据"""
    results = []
    today = datetime.now()
    
    for i in range(days):
        date = today - timedelta(days=i)
        # 跳过周末
        if date.weekday() >= 5:
            continue
        
        date_str = date.strftime('%Y%m%d')
        cached = load_cached_data(stock_code, date_str)
        
        if cached:
            results.append({
                'date': date_str,
                'data': cached,
                'source': 'cache'
            })
    
    return jsonify({
        'success': True,
        'code': stock_code,
        'days': len(results),
        'data': results
    })


def generate_mock_intraday(stock_code, date_str):
    """生成模拟分时数据用于测试"""
    import random
    
    base_price = 50 + random.random() * 50
    price = base_price
    trends = []
    
    # 上午 9:30-11:30
    for h in range(9, 12):
        start_m = 30 if h == 9 else 0
        for m in range(start_m, 60):
            change = (random.random() - 0.5) * 0.01
            price *= (1 + change)
            volume = random.randint(1000, 5000)
            avg = price * (1 + (random.random() - 0.5) * 0.005)
            trends.append({
                'time': f"{h}:{m:02d}",
                'price': round(price, 2),
                'volume': volume,
                'avgPrice': round(avg, 2)
            })
    
    # 下午 13:00-15:00
    for h in range(13, 16):
        end_m = 1 if h == 15 else 60
        for m in range(0, end_m):
            change = (random.random() - 0.5) * 0.01
            price *= (1 + change)
            volume = random.randint(800, 4000)
            avg = price * (1 + (random.random() - 0.5) * 0.005)
            trends.append({
                'time': f"{h}:{m:02d}",
                'price': round(price, 2),
                'volume': volume,
                'avgPrice': round(avg, 2)
            })
    
    return {
        'trends': trends,
        'preClose': round(base_price, 2),
        'updateTime': datetime.now().strftime('%H:%M:%S')
    }


@app.route('/api/hot_stocks')
def get_hot_stocks():
    """获取热门股票列表"""
    try:
        url = 'https://push2.eastmoney.com/api/qt/clist/get'
        params = {
            'pn': 1,
            'pz': 20,
            'po': 1,
            'np': 1,
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': 2,
            'invt': 2,
            'fid': 'f3',
            'fs': 'm:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23',
            'fields': 'f12,f14,f2,f3,f4,f5,f6,f7,f8',
            '_': int(datetime.now().timestamp() * 1000)
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        stocks = []
        if data and data.get('data') and data['data'].get('diff'):
            for item in data['data']['diff']:
                name = item.get('f14', '')
                if 'ST' in name or '退' in name:
                    continue
                stocks.append({
                    'code': item.get('f12', ''),
                    'name': name,
                    'current': item.get('f2', 0) / 100,
                    'change': item.get('f3', 0) / 100,
                    'volume': item.get('f5', 0),
                    'turnover': item.get('f6', 0)
                })
        
        return jsonify({
            'success': True,
            'count': len(stocks),
            'stocks': stocks[:10]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


if __name__ == '__main__':
    print("")
    print("=" * 50)
    print("A股分时数据服务已启动")
    print("=" * 50)
    print("")
    print("访问地址: http://localhost:5001")
    print("")
    print("API端点:")
    print("  - 实时行情: /api/quote/<股票代码>")
    print("  - 今日分时: /api/intraday/<股票代码>")
    print("  - 历史分时: /api/history/<股票代码>/<日期>")
    print("  - 热门股票: /api/hot_stocks")
    print("")
    print("=" * 50)
    
    app.run(host='0.0.0.0', port=5001, debug=True)
