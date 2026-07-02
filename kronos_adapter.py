#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional Kronos-style path forecast for intraday A-share monitoring.

The real Kronos model is heavier and should run as an offline/async enhancer.
This adapter gives the dashboard a stable local factor now, and keeps the
output shape compatible with a future real Kronos backend.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Iterable


@dataclass(frozen=True)
class OhlcvBar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


def forecast_from_minutes(minutes: Iterable[object], avg: float | None = None, horizon: int = 15) -> dict:
    bars = minutes_to_ohlcv(minutes)
    if len(bars) < 12:
        return {
            "ok": False,
            "mode": "local-kronos",
            "label": "数据不足",
            "summary": "Kronos因子：分钟线不足，暂不参与判断",
            "confidence": 0,
            "horizon": f"{horizon}分钟",
        }
    return heuristic_path_forecast(bars, avg, horizon)


def minutes_to_ohlcv(minutes: Iterable[object]) -> list[OhlcvBar]:
    out: list[OhlcvBar] = []
    prev_price = 0.0
    prev_volume = 0.0
    prev_amount = 0.0
    for item in minutes:
        try:
            close = float(getattr(item, "price", 0) or 0)
            if close <= 0:
                continue
            raw_volume = float(getattr(item, "volume_lot", 0) or 0)
            raw_amount = float(getattr(item, "amount_yuan", 0) or 0)
            volume = raw_volume - prev_volume if raw_volume >= prev_volume and prev_volume > 0 else raw_volume
            amount = raw_amount - prev_amount if raw_amount >= prev_amount and prev_amount > 0 else raw_amount
            prev_volume = max(prev_volume, raw_volume)
            prev_amount = max(prev_amount, raw_amount)
            open_price = prev_price or close
            high = max(open_price, close)
            low = min(open_price, close)
            out.append(
                OhlcvBar(
                    time=str(getattr(item, "time", "")),
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=max(volume, 0.0),
                    amount=max(amount, 0.0),
                )
            )
            prev_price = close
        except Exception:
            continue
    return out


def heuristic_path_forecast(bars: list[OhlcvBar], avg: float | None, horizon: int) -> dict:
    closes = [bar.close for bar in bars if bar.close > 0]
    current = closes[-1]
    day_high = max(closes)
    day_low = min(closes)
    day_range = pct(day_low, day_high)
    momentum_5 = pct(closes[-6], closes[-1]) if len(closes) >= 6 else 0.0
    momentum_15 = pct(closes[-16], closes[-1]) if len(closes) >= 16 else momentum_5
    volatility = realized_vol(closes[-30:])
    high_fade = pct(day_high, current)
    low_lift = pct(day_low, current)
    avg_dev = pct(avg, current) if avg else 0.0
    vol_ratio = volume_ratio([bar.volume for bar in bars])

    label = "震荡等待"
    bias = "观察"
    action = "等价格回到黄线附近，再看二次确认"
    confidence = 48
    expected = clamp(momentum_5 * 0.35 + momentum_15 * 0.25, -1.8, 1.8)

    if avg and avg_dev >= 1.2 and high_fade >= 0.25 and momentum_5 < 0.1:
        label = "冲高回落"
        bias = "偏空"
        action = "反T高抛优先，等回落黄线或计划价再接"
        confidence = 62
        expected = min(expected, -0.35)
    elif avg and avg_dev <= -1.2 and low_lift >= 0.25 and momentum_5 > -0.05:
        label = "低位修复"
        bias = "偏多"
        action = "正T低吸可观察，必须等止跌拐头和量能承接"
        confidence = 61
        expected = max(expected, 0.32)
    elif momentum_15 >= 1.0 and avg_dev > 0.5 and high_fade < 0.35:
        label = "趋势上冲"
        bias = "偏多"
        action = "不追高，等回踩不破黄线；已有仓位可分批锁定"
        confidence = 58
        expected = max(expected, 0.28)
    elif momentum_15 <= -1.0 and avg_dev < -0.4 and low_lift < 0.45:
        label = "弱势下探"
        bias = "偏空"
        action = "不急接刀，等长下影/黄线回收后再判断"
        confidence = 60
        expected = min(expected, -0.32)
    elif day_range >= 2.0 and abs(avg_dev) < 0.8:
        label = "黄线震荡"
        bias = "中性"
        action = "围绕黄线做T，只做两端，不在中位追单"
        confidence = 54

    if vol_ratio >= 1.6:
        confidence += 5
    if volatility >= 0.55:
        confidence += 4
    if day_range < 1.0:
        confidence -= 8
        action = "波动不够，降低做T频率"

    confidence = int(clamp(confidence, 30, 78))
    return {
        "ok": True,
        "mode": "local-kronos",
        "label": label,
        "bias": bias,
        "horizon": f"{horizon}分钟",
        "expectedPct": round(expected, 2),
        "confidence": confidence,
        "summary": f"Kronos因子：{horizon}分钟更像{label}，方向{bias}，预估{expected:+.2f}%",
        "action": action,
        "features": {
            "黄线偏离": round(avg_dev, 2),
            "5分钟动量": round(momentum_5, 2),
            "15分钟动量": round(momentum_15, 2),
            "日内振幅": round(day_range, 2),
            "量能比": round(vol_ratio, 2),
            "波动率": round(volatility, 2),
        },
        "backend": kronos_backend_status(),
    }


def kronos_backend_status() -> str:
    if os.environ.get("KRONOS_REPO_PATH") or os.environ.get("KRONOS_MODEL_PATH"):
        return "已配置真实Kronos路径，当前仍使用轻量适配输出"
    return "未配置真实Kronos模型，当前使用本地K线预测因子"


def pct(start: float | None, end: float | None) -> float:
    try:
        start_f = float(start or 0)
        end_f = float(end or 0)
        if start_f <= 0:
            return 0.0
        return (end_f - start_f) / start_f * 100.0
    except Exception:
        return 0.0


def volume_ratio(volumes: list[float]) -> float:
    values = [max(float(v or 0), 0.0) for v in volumes]
    if len(values) < 8:
        return 0.0
    recent = sum(values[-5:]) / 5
    base = sum(values[-20:-5]) / max(len(values[-20:-5]), 1)
    return recent / base if base > 0 else 0.0


def realized_vol(values: list[float]) -> float:
    returns = [pct(values[i - 1], values[i]) for i in range(1, len(values)) if values[i - 1] > 0]
    if len(returns) < 3:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / len(returns)
    return math.sqrt(variance)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
