#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""紫金矿业(601899) 买卖点推送。纯腾讯实时行情接口，抛弃东方财富分时API。
信号基于：昨收涨跌幅 + 日内高低点 + 相对最低点反弹幅度 + 成交量突增。
无信号静默。
"""
import urllib.request
from datetime import datetime

PAIR = '601899'
SYM = 'sh' + PAIR

def ts():
    return datetime.now().strftime('%H:%M')

def get(u, t=10):
    r = urllib.request.Request(u)
    with urllib.request.urlopen(r, timeout=t) as f:
        return f.read().decode('utf-8', 'ignore')

def main():
    # 腾讯实时行情
    txt = get('http://qt.gtimg.cn/q=' + SYM).strip()
    if '~' not in txt:
        return
    s = txt.split('~')
    try:
        name = s[1].strip()
        p = float(s[3])          # 最新价
        pc = float(s[4])         # 昨收
        o = float(s[5])          # 今开
        hi = float(s[33])        # 最高
        lo = float(s[34])        # 最低
        chg = float(s[32])       # 涨跌幅%
        v = float(s[36])         # 成交量(手)
        a = float(s[37])         # 成交额(元)
        tm = s[30].strip()[-6:]  # HHMMSS
    except Exception:
        return

    now = datetime.now()
    tod = now.hour * 60 + now.minute
    in_trade = (9 * 60 + 25 <= tod <= 11 * 60 + 30) or (13 * 60 <= tod <= 15 * 60)
    if not in_trade:
        return

    rel_low = ((p - lo) / lo * 100) if lo and lo > 0 else 0   # 相对低点反弹%
    buy = []
    sell = []

    # ▌买入信号
    # 1) 急跌反弹：跌幅>-3%，且相对日内低点已反弹 0.3% 以上（止跌企稳）
    if chg < -3.0 and rel_low >= 0.3:
        buy.append(f"🔵 急跌反弹 跌幅{chg:.1f}% 距低点反弹{rel_low:.2f}%")
    # 2) 超跌：跌幅<-5%，不给反弹条件（激进抄底）
    elif chg < -5.0:
        buy.append(f"🔵 超跌{chg:.1f}% 抢反弹")
    # 3) 放量杀跌后的承接：跌幅>-2%，成交量>200万手（大单承接）
    elif chg < -2.0 and v > 2000000:
        buy.append(f"🔵 放量承接 跌幅{chg:.1f}% 量{v/10000:.0f}万手")
    # 4) 中线弱反弹：跌幅在 -2% ~ -0.5%，且相对低点已反弹 0.5%
    elif -2.0 <= chg <= -0.5 and rel_low >= 0.5:
        buy.append(f"🟢 弱反弹 跌幅{chg:.1f}% 距低点反弹{rel_low:.2f}%")
    # 5) 低位回升：跌幅<-1%，且相对低点反弹 0.8%
    elif chg < -1.0 and rel_low >= 0.8:
        buy.append(f"🟢 低位回升 跌幅{chg:.1f}% 反弹{rel_low:.2f}%")

    # ▌卖出信号
    # 1) 急拉：涨幅>1%
    if chg > 1.0:
        sell.append(f"🔴 急拉{chg:.1f}% 第二止盈清仓")
    elif chg > 0.5:
        sell.append(f"🟠 涨幅{chg:.1f}% 第一止盈减半")
    # 2) 冲高回落：涨跌幅从高点回落（用相对日内高点判断）
    elif chg >= 0 and hi > 0 and (hi - p) / hi * 100 > 1.0:
        sell.append("🟠 冲高回落 高抛")
    # 3) 创日内新低且跌幅扩大：低点刷新且跌幅<-3%
    if lo > 0 and abs(p - lo) / pc * 100 < 0.15 and chg < -3.0:
        sell.append("🔴 创新低 止损")
    # 4) 尾盘强制清仓
    if tod >= 14 * 60 + 30:
        sell.append("⏰ 14:30强制清仓")

    if not (buy or sell):
        return
    out = ' | '.join(buy + sell)
    print(f"{name}({PAIR}) 现价:{p} 跌幅:{chg:.1f}% 成交量:{v/10000:.0f}万手 -> {out}")

if __name__ == '__main__':
    main()
