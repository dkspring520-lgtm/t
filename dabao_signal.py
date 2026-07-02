#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
紫金矿业(601899) 1分钟买卖点信号。
数据源：腾讯 qt.gtimg.cn（行情快照 + 分钟线算均价）。
输出极简：只给价格和买卖点。
"""
import urllib.request

PAIR = '601899'
SYM = 'sh' + PAIR

def main():
    txt = urllib.request.urlopen('http://qt.gtimg.cn/q=' + SYM, timeout=10).read().decode('utf-8', 'ignore').strip()
    if '~' not in txt:
        return
    s = txt.split('~')
    try:
        p = float(s[3])
        pc = float(s[4])
        chg = float(s[32]) if s[32] else 0.0
    except Exception:
        return

    avg = round((p + pc) / 2, 2)  # 简化均价，避免浮窗大波动

    if avg <= 0:
        return

    dev = (p - avg) / avg * 100

    if dev < -1.5:
        print(f'{p} 买 偏离 {dev:.1f}%')
    elif dev < -0.5:
        print(f'{p} 买 偏离 {dev:.1f}%')
    elif dev > 1.5:
        print(f'{p} 卖 偏离 {dev:.1f}%')
    elif dev > 0.5:
        print(f'{p} 卖 偏离 {dev:.1f}%')

if __name__ == '__main__':
    main()
