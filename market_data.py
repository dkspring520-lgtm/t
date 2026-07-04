import requests
import json
import re
import time
from datetime import datetime, timedelta

class MarketData:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_realtime_data(self, stock_code):
        """
        获取实时行情数据
        支持: 上海(sh)、深圳(sz)、北京(bj)市场
        """
        try:
            # 格式化股票代码
            formatted_code = self._format_stock_code(stock_code)
            
            # 使用东财实时行情API
            url = f"https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                'fltt': 2,
                'invt': 2,
                'v': int(time.time() * 1000),
                'fields': 'f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f116,f117,f162',
                'secid': self._get_market_code(stock_code) + '.' + re.sub(r'[^0-9]', '', stock_code)
            }
            
            response = self.session.get(url, params=params, headers=self.headers, timeout=10)
            data = response.json()
            
            if data.get('data'):
                stock_data = data['data']
                return {
                    'code': stock_code,
                    'name': stock_data.get('f58', ''),
                    'price': float(stock_data.get('f43', 0)) / 100 if stock_data.get('f43') else 0,
                    'open': float(stock_data.get('f46', 0)) / 100 if stock_data.get('f46') else 0,
                    'high': float(stock_data.get('f44', 0)) / 100 if stock_data.get('f44') else 0,
                    'low': float(stock_data.get('f45', 0)) / 100 if stock_data.get('f45') else 0,
                    'volume': stock_data.get('f47', 0),
                    'amount': stock_data.get('f48', 0),
                    'change': float(stock_data.get('f170', 0)) / 100 if stock_data.get('f170') else 0,
                    'change_percent': float(stock_data.get('f169', 0)) / 100 if stock_data.get('f169') else 0,
                    'pre_close': float(stock_data.get('f60', 0)) / 100 if stock_data.get('f60') else 0,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            return None
            
        except Exception as e:
            print(f"获取股票 {stock_code} 数据失败: {e}")
            return None
    
    def get_hot_stocks(self, limit=10):
        """
        获取市场热门股票 - 基于涨幅、成交量、换手率等指标
        返回热门股票列表
        """
        try:
            hot_stocks = []
            
            # 获取涨跌幅榜 - 涨幅较高但未涨停的股票（做T机会）
            url1 = "https://push2ex.eastmoney.com/getTopicZDFRank"
            params1 = {
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                'dpt': 'wz.ztzt',
                'pageindex': '0',
                'pagesize': '20',
                'sort': 'zdf:desc',
                'date': datetime.now().strftime('%Y%m%d')
            }
            
            response = self.session.get(url1, params=params1, headers=self.headers, timeout=10)
            data = response.json()
            
            if data.get('data') and data['data'].get('rank'):
                for item in data['data']['rank'][:15]:
                    code = item.get('c', '')
                    name = item.get('n', '')
                    change_percent = float(item.get('zdf', 0))
                    price = float(item.get('p', 0))
                    
                    # 过滤涨停股（无法做T）和跌幅过小的股票
                    if 2 <= change_percent <= 6 and price > 5:
                        hot_stocks.append({
                            'code': code,
                            'name': name,
                            'price': price,
                            'change_percent': change_percent,
                            'type': 'up'
                        })
            
            # 获取成交量榜 - 活跃度高的股票
            url2 = "https://push2.eastmoney.com/api/qt/clist/get"
            params2 = {
                'pn': 1,
                'pz': 20,
                'po': 1,
                'np': 1,
                'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                'fltt': 2,
                'invt': 2,
                'fid': 'f20',  # 按成交量排序
                'fs': 'm:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23',  # 深市+上海
                'fields': 'f12,f14,f2,f20',
                '_': int(time.time() * 1000)
            }
            
            response = self.session.get(url2, params=params2, headers=self.headers, timeout=10)
            data = response.json()
            
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    code = item.get('f12', '')
                    name = item.get('f14', '')
                    price = item.get('f2', 0)
                    amount = item.get('f20', 0)
                    
                    if price and float(price) > 0:
                        price_val = float(price) / 100 if float(price) > 100 else float(price)
                        # 检查是否已在列表中
                        if not any(s['code'] == code for s in hot_stocks):
                            hot_stocks.append({
                                'code': code,
                                'name': name,
                                'price': price_val,
                                'amount': amount,
                                'type': 'volume'
                            })
            
            # Merge Dragon-Tiger List candidates into the hot-stock pool.
            lhb_stocks = self.get_longhubang_stocks(limit=max(limit * 2, 20))
            for stock in lhb_stocks:
                existing = next((s for s in hot_stocks if s.get('code') == stock.get('code')), None)
                if existing:
                    existing.update({
                        'is_longhubang': True,
                        'lhb_score': stock.get('lhb_score', 0),
                        'lhb_reason': stock.get('lhb_reason', ''),
                        'lhb_net_buy': stock.get('lhb_net_buy', 0),
                        'lhb_buy_amount': stock.get('lhb_buy_amount', 0),
                        'lhb_sell_amount': stock.get('lhb_sell_amount', 0),
                        'lhb_trade_date': stock.get('lhb_trade_date', ''),
                    })
                else:
                    hot_stocks.append(stock)

            hot_stocks.sort(key=lambda s: (
                1 if s.get('is_longhubang') else 0,
                s.get('lhb_score', 0),
                s.get('change_percent', 0) or 0,
            ), reverse=True)

            # 返回前N个热门股
            selected = hot_stocks[:limit]
            
            # 如果不够，补充一些默认的高流动性股票
            if len(selected) < limit:
                default_stocks = [
                    {'code': '000001', 'name': '平安银行', 'price': 0, 'type': 'default'},
                    {'code': '600000', 'name': '浦发银行', 'price': 0, 'type': 'default'},
                    {'code': '000858', 'name': '五粮液', 'price': 0, 'type': 'default'},
                    {'code': '601318', 'name': '中国平安', 'price': 0, 'type': 'default'},
                    {'code': '601398', 'name': '工商银行', 'price': 0, 'type': 'default'},
                    {'code': '600519', 'name': '贵州茅台', 'price': 0, 'type': 'default'},
                    {'code': '000333', 'name': '美的集团', 'price': 0, 'type': 'default'},
                    {'code': '002594', 'name': '比亚迪', 'price': 0, 'type': 'default'},
                ]
                for stock in default_stocks:
                    if len(selected) >= limit:
                        break
                    if not any(s['code'] == stock['code'] for s in selected):
                        selected.append(stock)
            
            return selected[:limit]
            
        except Exception as e:
            print(f"获取热门股票失败: {e}")
            lhb_fallback = self.get_longhubang_stocks(limit=limit)
            if lhb_fallback:
                return lhb_fallback[:limit]
            # 返回默认股票列表作为后备
            return [
                {'code': '000001', 'name': '平安银行', 'price': 0, 'type': 'default'},
                {'code': '600000', 'name': '浦发银行', 'price': 0, 'type': 'default'},
                {'code': '000858', 'name': '五粮液', 'price': 0, 'type': 'default'},
                {'code': '601318', 'name': '中国平安', 'price': 0, 'type': 'default'},
                {'code': '601398', 'name': '工商银行', 'price': 0, 'type': 'default'},
                {'code': '600519', 'name': '贵州茅台', 'price': 0, 'type': 'default'},
                {'code': '000333', 'name': '美的集团', 'price': 0, 'type': 'default'},
                {'code': '002594', 'name': '比亚迪', 'price': 0, 'type': 'default'},
                {'code': '600036', 'name': '招商银行', 'price': 0, 'type': 'default'},
                {'code': '000568', 'name': '泸州老窖', 'price': 0, 'type': 'default'},
            ][:limit]
    
    def get_longhubang_stocks(self, limit=20, days=5):
        """Fetch recent Dragon-Tiger List stocks and normalize them for stock picking."""
        try:
            rows = self._fetch_longhubang_rows(limit=max(limit * 3, 60), days=days)
            by_code = {}

            for item in rows:
                code = str(item.get('SECURITY_CODE') or item.get('SECURITYCODE') or item.get('code') or '').strip()
                code = re.sub(r'[^0-9]', '', code)
                if len(code) != 6:
                    continue

                buy_amount = self._safe_float(
                    item.get('BILLBOARD_BUY_AMT')
                    or item.get('BUY_AMT')
                    or item.get('TOTAL_BUYAMT')
                    or 0
                )
                sell_amount = self._safe_float(
                    item.get('BILLBOARD_SELL_AMT')
                    or item.get('SELL_AMT')
                    or item.get('TOTAL_SELLAMT')
                    or 0
                )
                net_buy = self._safe_float(
                    item.get('BILLBOARD_NET_AMT')
                    or item.get('NET_BUY_AMT')
                    or item.get('NETAMT')
                    or (buy_amount - sell_amount)
                )
                reason = (
                    item.get('EXPLANATION')
                    or item.get('EXPLAIN')
                    or item.get('BILLBOARD_TYPE')
                    or '龙虎榜上榜'
                )
                trade_date = str(item.get('TRADE_DATE') or item.get('TDATE') or '')[:10]

                stock = by_code.setdefault(code, {
                    'code': code,
                    'name': item.get('SECURITY_NAME_ABBR') or item.get('SECURITY_NAME') or item.get('SNAME') or code,
                    'price': self._safe_float(item.get('CLOSE_PRICE') or 0),
                    'change_percent': self._safe_float(item.get('CHANGE_RATE') or item.get('CHANGE_PERCENT') or 0),
                    'type': 'longhubang',
                    'is_longhubang': True,
                    'lhb_score': 0,
                    'lhb_reason': '',
                    'lhb_net_buy': 0,
                    'lhb_buy_amount': 0,
                    'lhb_sell_amount': 0,
                    'lhb_trade_date': trade_date,
                    'lhb_reasons': [],
                })

                stock['lhb_net_buy'] += net_buy
                stock['lhb_buy_amount'] += buy_amount
                stock['lhb_sell_amount'] += sell_amount
                if trade_date and trade_date > stock.get('lhb_trade_date', ''):
                    stock['lhb_trade_date'] = trade_date
                if reason and reason not in stock['lhb_reasons']:
                    stock['lhb_reasons'].append(reason)

            stocks = []
            for stock in by_code.values():
                stock['lhb_reason'] = '；'.join(stock.pop('lhb_reasons', [])[:2]) or '龙虎榜上榜'
                stock['lhb_score'] = self._longhubang_score(stock)
                stock['lhb_net_buy'] = round(stock['lhb_net_buy'], 2)
                stock['lhb_buy_amount'] = round(stock['lhb_buy_amount'], 2)
                stock['lhb_sell_amount'] = round(stock['lhb_sell_amount'], 2)
                stocks.append(stock)

            stocks.sort(key=lambda s: (s.get('lhb_score', 0), s.get('lhb_net_buy', 0)), reverse=True)
            return stocks[:limit]
        except Exception as e:
            print(f"获取龙虎榜数据失败: {e}")
            return []

    def _fetch_longhubang_rows(self, limit=60, days=5):
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        end = datetime.now()
        start = end - timedelta(days=days)
        params = {
            'sortColumns': 'TRADE_DATE,SECURITY_CODE',
            'sortTypes': '-1,1',
            'pageSize': limit,
            'pageNumber': 1,
            'reportName': 'RPT_DAILYBILLBOARD_DETAILS',
            'columns': 'ALL',
            'source': 'WEB',
            'client': 'WEB',
            'filter': "(TRADE_DATE>='{0}')(TRADE_DATE<='{1}')".format(
                start.strftime('%Y-%m-%d'),
                end.strftime('%Y-%m-%d'),
            ),
        }
        response = self.session.get(url, params=params, headers=self.headers, timeout=10)
        data = response.json()
        result = data.get('result') or data.get('data') or {}
        rows = result.get('data') if isinstance(result, dict) else None
        return rows or []

    def _longhubang_score(self, stock):
        net_buy = stock.get('lhb_net_buy', 0) or 0
        buy_amount = stock.get('lhb_buy_amount', 0) or 0
        sell_amount = stock.get('lhb_sell_amount', 0) or 0
        change_percent = abs(stock.get('change_percent', 0) or 0)

        score = 35
        score += min(30, max(-20, net_buy / 100000000 * 15))
        if buy_amount > sell_amount:
            score += 15
        if change_percent >= 5:
            score += 8
        if '机构' in stock.get('lhb_reason', ''):
            score += 8
        if '游资' in stock.get('lhb_reason', ''):
            score += 4
        return round(max(0, min(score, 100)), 1)

    def _safe_float(self, value):
        try:
            if value in (None, '-', ''):
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _format_stock_code(self, code):
        """标准化股票代码"""
        code = str(code).strip()
        # 清理前缀
        if code.startswith(('sh', 'sz', 'bj')):
            code = code[2:]
        # 清理市场后缀
        code = re.sub(r'\.S[HZ]$', '', code, flags=re.IGNORECASE)
        code = re.sub(r'[^0-9]', '', code)
        return code
    
    def _get_market_code(self, stock_code):
        """获取市场代码用于API调用"""
        code = self._format_stock_code(stock_code)
        if code.startswith('6'):
            return '1'  # 上海
        elif code.startswith(('0', '3')):
            return '0'  # 深圳
        elif code.startswith('8') or code.startswith('4'):
            return '0'  # 北交
        return '0'

# 测试代码
if __name__ == '__main__':
    md = MarketData()
    
    # 测试热门股票
    print("获取热门股票...")
    hot = md.get_hot_stocks(10)
    for stock in hot:
        print(f"{stock['code']} - {stock['name']}: ¥{stock.get('price', 'N/A')}")
