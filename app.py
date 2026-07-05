"""
A股做T监控系统 - Flask后端
包含实时监控、买卖信号、持仓管理、模拟测试功能
"""
from flask import Flask, render_template, jsonify, request, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import threading
import time
from datetime import datetime
import json
import random
import os

from env_bootstrap import apply_local_env

apply_local_env()

# 导入自定义模块
from market_data import MarketData
from signal_detector import SignalDetector
from voice_alert import VoiceAlert
from simulation_engine import SimulationEngine

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'stock-monitor-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# 全局状态
monitoring_active = False
monitor_thread = None
monitored_stocks = {}  # 监控中的股票
user_positions = {}    # 用户持仓
simulation_results = {}  # 模拟测试结果
signals_history = []     # 信号历史

# 初始化组件
data_fetcher = MarketData()
signal_detector = SignalDetector()
voice_alert = VoiceAlert()
simulation_engine = SimulationEngine()

# ========== 页面路由 ==========
@app.route('/')
def index():
    minimal_ui = os.getenv('TSHU_UI_MINIMAL', '1') == '1'
    ui_features = {
        'minimal': minimal_ui,
        'show_position_tab': not minimal_ui,
        'show_research_tab': not minimal_ui,
        'show_simulation_tab': True
    }
    return render_template('index.html', ui_features=ui_features)


def _redirect_to_home(reason: str = ''):
    """
    非公开页面统一回首页，避免误点到未开放后台/入口页面。
    """
    if reason:
        return redirect(f"/?from={reason}")
    return redirect(url_for('index'))


@app.route('/admin')
def admin_blocked():
    return _redirect_to_home('admin')


@app.route('/about')
def about_us_blocked():
    return _redirect_to_home('about')


@app.route('/register')
def register_blocked():
    return _redirect_to_home('register')


@app.route('/login')
def login_blocked():
    return _redirect_to_home('login')


@app.route('/landing')
def landing_redirect():
    return _redirect_to_home()


@app.route('/commercial')
def commercial_redirect():
    return _redirect_to_home('commercial')


@app.route('/account')
def account_redirect():
    return _redirect_to_home('account')

# ========== 持仓管理API ==========
@app.route('/api/positions', methods=['GET'])
def get_positions():
    """获取用户持仓列表"""
    positions_list = []
    total_cost = 0
    total_market = 0
    total_pnl = 0
    
    for code, pos in user_positions.items():
        # 获取实时价格
        real_time_data = data_fetcher.get_realtime_data(code)
        current_price = real_time_data.get('price', pos.get('avg_cost', 0)) if real_time_data else pos.get('avg_cost', 0)
        
        quantity = pos.get('quantity', 0)
        avg_cost = pos.get('avg_cost', 0)
        cost_value = quantity * avg_cost
        market_value = quantity * current_price
        pnl = market_value - cost_value
        pnl_percent = (pnl / cost_value * 100) if cost_value > 0 else 0
        
        positions_list.append({
            'stock_code': code,
            'stock_name': pos.get('name', code),
            'quantity': quantity,
            'avg_cost': avg_cost,
            'current_price': current_price,
            'market_value': round(market_value, 2),
            'cost_value': round(cost_value, 2),
            'pnl': round(pnl, 2),
            'pnl_percent': round(pnl_percent, 2)
        })
        
        total_cost += cost_value
        total_market += market_value
        total_pnl += pnl
    
    summary = {
        'total_cost': round(total_cost, 2),
        'total_market': round(total_market, 2),
        'total_pnl': round(total_pnl, 2),
        'total_pnl_percent': round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0
    }
    
    return jsonify({
        'success': True, 
        'data': {
            'positions': positions_list,
            'summary': summary
        }
    })

@app.route('/api/position/add', methods=['POST'])
def add_position():
    """添加持仓"""
    data = request.json
    stock_code = data.get('stock_code')
    stock_name = data.get('stock_name', '')
    quantity = data.get('quantity', 0)
    avg_cost = data.get('avg_cost', 0)
    
    if not stock_code or len(stock_code) != 6:
        return jsonify({'success': False, 'error': '请输入有效的6位股票代码'})
    
    # 如果没填股票名称，自动获取
    if not stock_name:
        stock_data = data_fetcher.get_realtime_data(stock_code)
        stock_name = stock_data.get('name', stock_code) if stock_data else stock_code
    
    user_positions[stock_code] = {
        'name': stock_name,
        'quantity': quantity,
        'avg_cost': avg_cost,
        'added_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    return jsonify({'success': True, 'message': f'持仓 {stock_code} 添加成功'})

@app.route('/api/position/remove', methods=['POST'])
def remove_position():
    """删除持仓"""
    data = request.json
    stock_code = data.get('stock_code')
    
    if stock_code in user_positions:
        del user_positions[stock_code]
        return jsonify({'success': True, 'message': '持仓已删除'})
    
    return jsonify({'success': False, 'error': '持仓不存在'})

# ========== 股票监控API ==========
@app.route('/api/stocks', methods=['GET'])
def get_stocks():
    """获取监控中的股票列表"""
    stocks_list = []
    for code, stock in monitored_stocks.items():
        stocks_list.append({
            'stock_code': code,
            'stock_name': stock.get('name', code),
            'current_price': stock.get('price', 0),
            'change_percent': stock.get('change_percent', 0),
            'signals_count': stock.get('signals_count', 0)
        })
    return jsonify({'success': True, 'data': stocks_list})

@app.route('/api/stock/add', methods=['POST'])
def add_stock():
    """添加监控股票"""
    data = request.json
    stock_code = data.get('stock_code')
    
    if not stock_code or len(stock_code) != 6:
        return jsonify({'success': False, 'error': '请输入有效的6位股票代码'})
    
    # 获取股票名称
    stock_data = data_fetcher.get_realtime_data(stock_code)
    stock_name = stock_data.get('name', stock_code) if stock_data else stock_code
    
    monitored_stocks[stock_code] = {
        'code': stock_code,
        'name': stock_name,
        'price': 0,
        'change_percent': 0,
        'price_history': [],
        'signals_count': 0
    }
    
    return jsonify({
        'success': True, 
        'message': f'{stock_name} 添加成功',
        'data': {'stock_name': stock_name}
    })

@app.route('/api/stock/remove', methods=['POST'])
def remove_stock():
    """移除监控股票"""
    data = request.json
    stock_code = data.get('stock_code')
    
    if stock_code in monitored_stocks:
        del monitored_stocks[stock_code]
        return jsonify({'success': True, 'message': '股票已移除监控'})
    
    return jsonify({'success': False, 'error': '股票不在监控列表中'})

@app.route('/api/stock/search/<stock_code>', methods=['GET'])
def search_stock(stock_code):
    """搜索股票信息"""
    try:
        stock_data = data_fetcher.get_realtime_data(stock_code)
        if stock_data:
            return jsonify({'success': True, 'data': {'name': stock_data.get('name', stock_code), 'price': stock_data.get('price', 0)}})
        return jsonify({'success': False, 'error': '未找到股票'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ========== 模拟测试API ==========
@app.route('/api/simulate', methods=['POST'])
def run_simulation():
    """运行做T模拟测试"""
    data = request.json
    stock_code = data.get('stock_code')
    amount = data.get('amount', 100000)
    strategy = data.get('strategy', 'zijin_special')
    
    if not stock_code or len(stock_code) != 6:
        return jsonify({'success': False, 'error': '请输入有效的6位股票代码'})
    
    try:
        result = simulation_engine.run_simulation(stock_code, amount, strategy)
        if result:
            simulation_results[stock_code] = result
            return jsonify({'success': True, 'data': result})
        return jsonify({'success': False, 'error': '模拟运行失败'})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'模拟运行失败: {str(e)}'})


@app.route('/api/simulate/batch', methods=['POST'])
def run_batch_simulation_api():
    """一键批量模拟测试10只热门股票，带10次验算"""
    data = request.json or {}
    amount = data.get('amount', 100000)
    strategy = data.get('strategy', 'zijin_special')
    stock_count = data.get('count', 10)
    validation_runs = data.get('validation_runs', 10)  # 默认10次验算
    
    try:
        # 获取热门股票
        hot_stocks = data_fetcher.get_hot_stocks(limit=stock_count)
        
        if not hot_stocks:
            return jsonify({'success': False, 'error': '获取热门股票失败'})
        
        # 提取股票代码列表
        stock_codes = [s['code'] for s in hot_stocks]
        
        # 运行批量模拟，带10次验算
        batch_result = simulation_engine.run_batch_with_validation(
            stock_codes, strategy, amount, validation_runs
        )
        batch_result['stocks'] = hot_stocks
        
        return jsonify({
            'success': True,
            'data': batch_result
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'批量模拟失败: {str(e)}'})


@app.route('/api/hot_stocks', methods=['GET'])
def get_hot_stocks_api():
    """获取市场热门股票列表"""
    limit = request.args.get('limit', 10, type=int)
    
    try:
        hot_stocks = data_fetcher.get_hot_stocks(limit=limit)
        return jsonify({'success': True, 'data': hot_stocks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/longhubang', methods=['GET'])
def get_longhubang_api():
    """获取近期龙虎榜候选股票。"""
    limit = request.args.get('limit', 20, type=int)
    days = request.args.get('days', 5, type=int)

    try:
        stocks = data_fetcher.get_longhubang_stocks(limit=limit, days=days)
        return jsonify({'success': True, 'data': stocks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/strategies', methods=['GET'])
def get_strategies_api():
    """获取可用的做T策略列表"""
    strategies = [
        {'id': 'zijin_special', 'name': '紫金矿业专用策略', 'description': '基于偏离率+资金流向+技术指标'},
        {'id': 'simple_bias', 'name': '简单偏离率策略', 'description': '基于简单偏离率买卖点'},
        {'id': 'advanced_bias', 'name': '进阶偏离率策略', 'description': '加入波动率调节的偏离率策略'},
        {'id': 'zijin_high_profit', 'name': '紫金高利润策略', 'description': '更激进的做T策略，适合追求高收益'}
    ]
    return jsonify({'success': True, 'data': strategies})

# ========== 状态API ==========
@app.route('/api/status')
def get_status():
    """获取系统状态"""
    return jsonify({
        'monitoring': monitoring_active,
        'monitored_count': len(monitored_stocks),
        'positions_count': len(user_positions),
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

# ========== Socket.IO事件 ==========
@socketio.on('connect')
def handle_connect():
    """客户端连接"""
    print('客户端已连接')
    emit('status_update', {'message': '已连接到服务器'})

@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开"""
    print('客户端已断开')

# ========== 后台监控线程 ==========
def monitor_loop():
    """监控循环 - 监控股票并发出买卖点提醒"""
    global monitoring_active
    
    while monitoring_active:
        try:
            for stock_code in list(monitored_stocks.keys()):
                # 获取实时数据
                data = data_fetcher.get_realtime_data(stock_code)
                if not data:
                    continue
                
                # 更新股票数据
                stock = monitored_stocks[stock_code]
                stock['price'] = data['price']
                stock['change_percent'] = data.get('change_percent', 0)
                
                # 添加价格历史
                stock['price_history'].append({
                    'price': data['price'],
                    'volume': data.get('volume', 0),
                    'timestamp': datetime.now().strftime('%H:%M:%S')
                })
                
                # 限制历史记录长度
                if len(stock['price_history']) > 100:
                    stock['price_history'] = stock['price_history'][-100:]
                
                # 发送价格更新
                socketio.emit('price_update', {
                    'stock_code': stock_code,
                    'stock_name': stock['name'],
                    'price': data['price'],
                    'change_percent': data.get('change_percent', 0),
                    'volume': data.get('volume', 0)
                })
                
                # 检测信号
                if len(stock['price_history']) >= 10:
                    signal = signal_detector.analyze(
                        stock['price_history'], 
                        stock_code=stock_code
                    )
                    
                    if signal:
                        stock['signals_count'] = stock.get('signals_count', 0) + 1
                        
                        # 发送信号
                        signal_data = {
                            'stock_code': stock_code,
                            'stock_name': stock['name'],
                            'signal': signal
                        }
                        socketio.emit('new_signal', signal_data)
                        
                        # 语音提醒
                        if signal.get('action') in ['buy', 'sell']:
                            voice_alert.speak(
                                f"{stock['name']} {signal['action']}信号，"
                                f"置信度{signal.get('confidence', 0)}%"
                            )
                
        except Exception as e:
            print(f"监控循环出错: {e}")
        
        time.sleep(5)  # 5秒更新一次

@app.route('/api/monitor/start', methods=['POST'])
def start_monitoring():
    """启动监控"""
    global monitoring_active, monitor_thread
    
    if monitoring_active:
        return jsonify({'success': False, 'error': '监控已在运行中'})
    
    monitoring_active = True
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    
    return jsonify({'success': True, 'message': '监控已启动'})


@app.route('/api/monitor/stop', methods=['POST'])
def stop_monitoring():
    """停止监控"""
    global monitoring_active
    
    monitoring_active = False
    return jsonify({'success': True, 'message': '监控已停止'})


# 运行服务器
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
