#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A-share intraday T signal helpers."""

from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent
ADAPTIVE_STRATEGY_PATH = Path(os.environ.get("ADAPTIVE_STRATEGY_PATH") or BASE_DIR / "adaptive_strategy.json")
DEFAULT_STRATEGY = {
    "buy_min_dev": -1.15,
    "buy_max_dev": -2.8,
    "buy_rebound": 0.45,
    "buy_confirm": 5,
    "sell_min_dev": 1.35,
    "sell_max_dev": 2.8,
    "sell_fade": 1.2,
    "sell_confirm": 6,
    "observe_dev": 0.8,
    "strict_min_score": 7,
    "strict_day_range": 2.0,
    "vwap_reclaim_pct": 0.35,
    "vwap_take_profit_pct": 0.25,
    "normal_take_profit_pct": 0.60,
    "late_take_profit_pct": 0.45,
    "opening_enabled": 1,
    "opening_drop_pct": -1.8,
    "opening_reclaim_pct": 0.45,
    "opening_spike_pct": 1.6,
    "opening_fade_pct": 0.38,
    "swing_enabled": 1,
    "swing_min_space_pct": 0.85,
    "swing_buy_fade_pct": 0.75,
    "swing_sell_lift_pct": 0.85,
    "reverse_t_enabled": 1,
    "trade_end_minutes": 840,
    "second_confirm_enabled": 1,
}


@dataclass(frozen=True)
class StockConfig:
    name: str
    code: str
    symbol: str


@dataclass(frozen=True)
class Quote:
    name: str
    price: float
    pre_close: float
    high: float
    low: float
    change_pct: float
    volume_lot: float
    amount_wan: float
    time_raw: str


@dataclass(frozen=True)
class MinuteBar:
    time: str
    price: float
    volume_lot: float
    amount_yuan: float


@dataclass(frozen=True)
class Signal:
    name: str
    code: str
    time: str
    price: float
    action: str
    reason: str
    score: int

    def line(self) -> str:
        return f"{self.name}({self.code}) {self.time} {self.price:.2f}｜{self.action}｜{self.reason}"


def fetch_quote(symbol: str) -> Optional[Quote]:
    req = urllib.request.Request(
        f"http://qt.gtimg.cn/q={symbol}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
    )
    raw = urllib.request.urlopen(req, timeout=10).read()
    txt = raw.decode("gb18030", "replace").strip()
    if "~" not in txt:
        return None
    s = txt.split("~")
    try:
        return Quote(
            name=s[1],
            price=float(s[3]),
            pre_close=float(s[4]),
            high=float(s[33]),
            low=float(s[34]),
            change_pct=float(s[32]),
            volume_lot=float(s[36]) if s[36] else 0.0,
            amount_wan=float(s[37]) if s[37] else 0.0,
            time_raw=s[30] if len(s) > 30 else "",
        )
    except Exception:
        return None


def fetch_minutes(symbol: str) -> List[MinuteBar]:
    url = f"http://web.ifzq.gtimg.cn/appstock/app/minute/query?_var=js&code={symbol}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        txt = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
        if "=" not in txt:
            raise ValueError("empty tencent minute response")
        data = json.loads(txt.split("=", 1)[1].strip())
        if not isinstance(data, dict) or data.get("code") != 0:
            raise ValueError("bad tencent minute response")
    except Exception:
        return fetch_minutes_sina(symbol) or fetch_minutes_eastmoney(symbol)

    rows = data.get("data", {}).get(symbol, {}).get("data", {}).get("data", []) or []
    bars: List[MinuteBar] = []
    for row in rows:
        parts = row.split()
        if len(parts) < 4:
            continue
        try:
            bars.append(
                MinuteBar(
                    time=parts[0],
                    price=float(parts[1]),
                    volume_lot=float(parts[2]),
                    amount_yuan=float(parts[3]),
                )
            )
        except Exception:
            continue
    return bars


def fetch_minutes_eastmoney(symbol: str) -> List[MinuteBar]:
    code = symbol[2:]
    market = "1" if symbol.startswith("sh") else "0"
    params = {
        "secid": f"{market}.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "iscca": "0",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "ndays": "1",
    }
    url = "http://push2his.eastmoney.com/api/qt/stock/trends2/get?" + urllib.parse.urlencode(params)
    try:
        data = json.loads(_http_get(url, "utf-8", 10))
    except Exception:
        return []
    rows = data.get("data", {}).get("trends", []) if isinstance(data, dict) else []
    bars: List[MinuteBar] = []
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 7:
            continue
        try:
            hm = parts[0][-5:]
            price = float(parts[2])
            volume_lot = float(parts[5])
            amount_yuan = float(parts[6])
            if price > 0:
                bars.append(MinuteBar(hm, price, volume_lot, amount_yuan))
        except Exception:
            continue
    return bars


def fetch_minutes_sina(symbol: str) -> List[MinuteBar]:
    url = f"https://quotes.sina.cn/cn/api/openapi.php/CN_MinlineService.getMinlineData?symbol={symbol}"
    try:
        data = json.loads(_http_get(url, "utf-8", 10))
    except Exception:
        return []
    rows = data.get("result", {}).get("data", []) if isinstance(data, dict) else []
    bars: List[MinuteBar] = []
    last_total_volume = 0.0
    last_total_amount = 0.0
    running_volume = 0.0
    for row in rows:
        try:
            hm = _hm(str(row.get("m", ""))[:5])
            price = float(row.get("p") or 0)
            avg_price = float(row.get("avg_p") or price)
            volume = float(row.get("v") or 0)
            total_volume = float(row.get("tot_v") or 0)
            if total_volume <= 0:
                running_volume += volume
                total_volume = running_volume
            total_amount = total_volume * avg_price
            minute_volume = max(total_volume - last_total_volume, volume, 0.0)
            minute_amount = max(total_amount - last_total_amount, minute_volume * price, 0.0)
            last_total_volume = total_volume
            last_total_amount = total_amount
            if price > 0:
                bars.append(MinuteBar(hm, price, minute_volume / 100.0, minute_amount))
        except Exception:
            continue
    return bars


def _http_get(url: str, encoding: str, timeout: int) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout).read().decode(encoding, "replace")


def _hm(value: str) -> str:
    value = value.strip()
    if len(value) == 4 and value.isdigit():
        return f"{value[:2]}:{value[2:]}"
    if len(value) >= 5 and ":" in value:
        return value[:5]
    return value


def analyze(config: StockConfig) -> List[Signal]:
    """Strict signal for alerts and WeChat pushes."""
    return _analyze(config, include_watch=False)


def analyze_observation(config: StockConfig) -> List[Signal]:
    """Broader observation signal for the dashboard only.

    This intentionally avoids "低吸/高抛/买入/卖出" in the action text so the
    dashboard sound and WeChat paths keep firing only on strict signals.
    """
    return _analyze(config, include_watch=True)


def _analyze(config: StockConfig, include_watch: bool = False) -> List[Signal]:
    strategy = load_adaptive_strategy()
    quote = fetch_quote(config.symbol)
    if not quote or quote.price <= 0 or quote.pre_close <= 0:
        return []

    minutes = fetch_minutes(config.symbol)
    avg = _vwap(quote, minutes)
    if not avg:
        return []

    hm = _format_time(quote.time_raw, minutes)
    if include_watch:
        if not _is_observation_window(hm):
            return []
    elif not _is_signal_window(hm):
        return []

    prices = [m.price for m in minutes if m.price > 0]
    if len(prices) < 18:
        return []
    recent = prices[-8:]
    day_high = max(prices) if prices else quote.high
    day_low = min(prices) if prices else quote.low
    day_range = (day_high - day_low) / quote.pre_close * 100.0 if quote.pre_close > 0 else 0.0
    min_day_range = 1.8 if include_watch else float(strategy.get("strict_day_range", 2.2))
    if day_range < min_day_range:
        return []
    volumes = _minute_volumes(minutes)
    vol_ratio = _volume_ratio(volumes)
    avg_slope = _vwap_slope(minutes)

    dev = (quote.price - avg) / avg * 100.0
    if abs(dev) > 8:
        return []
    vwap_devs = _vwap_devs(minutes)
    vwap_reclaim = _vwap_reclaiming(vwap_devs, float(strategy.get("vwap_reclaim_pct", 0.35)))
    vwap_fade = _vwap_fading(vwap_devs, float(strategy.get("vwap_reclaim_pct", 0.35)))
    change = quote.change_pct
    rebound = (quote.price - day_low) / day_low * 100.0 if day_low > 0 else 0.0
    near_high_pullback = (day_high - quote.price) / day_high * 100.0 if day_high > 0 else 0.0
    momentum_3 = _pct_change(recent[-4], recent[-1]) if len(recent) >= 4 else 0.0
    momentum_5 = _pct_change(recent[-6], recent[-1]) if len(recent) >= 6 else 0.0
    last_up = len(recent) >= 3 and recent[-3] < recent[-2] < recent[-1]
    last_down = len(recent) >= 3 and recent[-3] > recent[-2] > recent[-1]
    breaks_recent_high = len(recent) >= 6 and recent[-1] >= max(recent[-6:-1])
    breaks_recent_low = len(recent) >= 6 and recent[-1] <= min(recent[-6:-1])
    active_volume = quote.volume_lot >= 300000
    price_space = abs(dev) >= 1.0 or abs(change) >= 1.6
    path_view = _intraday_path_view(hm, prices, quote.price, avg, dev, change, avg_slope, momentum_5)

    buy_score = 0
    buy_reasons: List[str] = []
    buy_risk: List[str] = []
    buy_min_dev = float(strategy.get("buy_min_dev", -1.2))
    buy_max_dev = float(strategy.get("buy_max_dev", -2.0))
    buy_rebound_limit = float(strategy.get("buy_rebound", 0.3))
    buy_confirm = int(strategy.get("buy_confirm", 5))
    if dev <= buy_max_dev:
        buy_score += 3
        buy_reasons.append(f"\u4f4e\u4e8e\u5747\u4ef7{abs(dev):.1f}%")
    elif dev <= buy_min_dev:
        buy_score += 2
        buy_reasons.append(f"\u5747\u4ef7\u4e0b\u65b9{abs(dev):.1f}%")
    if change <= -2.5:
        buy_score += 2
        buy_reasons.append(f"\u8dcc\u5e45{abs(change):.1f}%")
    elif change <= -1.2:
        buy_score += 1
        buy_reasons.append(f"\u56de\u843d{abs(change):.1f}%")
    if rebound <= max(buy_rebound_limit + 0.2, 0.35):
        buy_score += 2
        buy_reasons.append("\u8d34\u8fd1\u65e5\u4f4e")
    if last_up and momentum_3 > 0.12 and dev < -0.6:
        buy_score += 2
        buy_reasons.append("\u4f4e\u4f4d\u62d0\u5934")
    if 1.2 <= vol_ratio <= 4.5:
        buy_score += 2
        buy_reasons.append(f"\u91cf\u80fd{vol_ratio:.1f}x\u627f\u63a5")
    elif active_volume:
        buy_score += 1
        buy_reasons.append("\u6210\u4ea4\u6d3b\u8dc3")
    if avg_slope < -0.12 and dev < -1.2:
        buy_score -= 2
        buy_risk.append("\u5747\u4ef7\u4e0b\u884c")
    if rebound < buy_rebound_limit and dev < buy_min_dev:
        buy_score -= 2
        buy_risk.append("\u672a\u89c1\u6b62\u8dcc")
    buy_ready = (
        price_space
        and _is_low_buy_window(hm)
        and dev <= buy_min_dev
        and vwap_reclaim
        and rebound >= max(buy_rebound_limit, 0.22)
        and last_up
        and breaks_recent_high
        and momentum_3 >= 0.12
        and momentum_5 >= 0.10
        and avg_slope > -0.18
        and (vol_ratio >= (1.25 if _is_opening_first_half(hm) else 1.10) or active_volume)
        and (
            not int(strategy.get("second_confirm_enabled", 1))
            or _second_buy_confirm_prices(prices)
            or _sharp_reversal_buy_prices(prices)
        )
    )

    sell_score = 0
    sell_reasons: List[str] = []
    sell_risk: List[str] = []
    sell_min_dev = float(strategy.get("sell_min_dev", 1.2))
    sell_max_dev = float(strategy.get("sell_max_dev", 2.6))
    sell_fade = float(strategy.get("sell_fade", 0.15))
    sell_confirm = int(strategy.get("sell_confirm", 4))
    observe_dev = abs(float(strategy.get("observe_dev", 0.8)))
    reverse_t_enabled = bool(int(strategy.get("reverse_t_enabled", 0)))
    if dev >= sell_max_dev:
        sell_score += 3
        sell_reasons.append(f"\u9ad8\u4e8e\u5747\u4ef7{dev:.1f}%")
    elif dev >= sell_min_dev:
        sell_score += 2
        sell_reasons.append(f"\u5747\u4ef7\u4e0a\u65b9{dev:.1f}%")
    if change >= 1.5:
        sell_score += 2
        sell_reasons.append(f"\u6da8\u5e45{change:.1f}%")
    elif change >= 0.8:
        sell_score += 1
        sell_reasons.append(f"\u51b2\u9ad8{change:.1f}%")
    if near_high_pullback <= max(sell_fade + 0.2, 0.25):
        sell_score += 2
        sell_reasons.append("\u8d34\u8fd1\u65e5\u9ad8")
    if last_down and momentum_3 < -0.12 and dev > 0.3:
        sell_score += 2
        sell_reasons.append("\u9ad8\u4f4d\u62d0\u5934")
    if 1.0 <= vol_ratio <= 5.5:
        sell_score += 2
        sell_reasons.append(f"\u91cf\u80fd{vol_ratio:.1f}x")
    elif active_volume:
        sell_score += 1
        sell_reasons.append("\u6210\u4ea4\u6d3b\u8dc3")
    if avg_slope > 0.18 and last_down:
        sell_score -= 1
        sell_risk.append("\u5747\u4ef7\u4ecd\u5f3a")
    if near_high_pullback < 0.1 and momentum_3 > 0:
        sell_score -= 2
        sell_risk.append("\u672a\u89c1\u6ede\u6da8")
    sell_ready = (
        reverse_t_enabled
        and
        price_space
        and dev >= sell_min_dev
        and vwap_fade
        and near_high_pullback >= max(sell_fade, 0.18)
        and last_down
        and breaks_recent_low
        and momentum_3 <= -0.12
        and momentum_5 <= -0.22
        and avg_slope < 0.05
        and (vol_ratio >= (1.25 if _is_opening_first_half(hm) else 0.9) or active_volume)
        and (
            not int(strategy.get("second_confirm_enabled", 1))
            or _second_sell_confirm_prices(prices)
            or _sharp_reversal_sell_prices(prices)
        )
    )

    strict_min_score = int(strategy.get("strict_min_score", 8))
    if _hm_to_minutes(hm) > int(strategy.get("trade_end_minutes", 840)):
        return []
    swing_signal = _intraday_swing_signal(
        config,
        hm,
        quote,
        avg,
        dev,
        day_high,
        day_low,
        day_range,
        vol_ratio,
        include_watch,
        path_view,
    )
    if swing_signal:
        return [swing_signal]
    opening_buy = _opening_buy_ready(strategy, hm, prices, quote.price, avg, dev, vol_ratio, momentum_3)
    opening_sell = _opening_sell_ready(strategy, hm, prices, quote.price, avg, dev, vol_ratio, momentum_3)
    if opening_buy and path_view["buy_ok"] and buy_score >= 5 and buy_score >= sell_score:
        reason = f"{path_view['text']}；开盘急跌后回收，低点承接明显，现价较均价{dev:+.2f}%；{_price_plan('buy', quote.price)}"
        action = "开盘低位机会" if include_watch else "开盘低吸观察"
        return [Signal(config.name, config.code, hm, quote.price, action, reason, max(buy_score, 7))]
    if opening_sell and path_view["sell_ok"] and sell_score >= 5 and sell_score >= buy_score:
        reason = f"{path_view['text']}；开盘冲高后回落，短线有派发风险，现价较均价{dev:+.2f}%；{_price_plan('sell', quote.price)}"
        action = "开盘高位机会" if include_watch else "开盘高抛观察"
        return [Signal(config.name, config.code, hm, quote.price, action, reason, max(sell_score, 7))]
    if buy_ready and path_view["buy_ok"] and buy_score >= max(buy_confirm, strict_min_score) and buy_score >= sell_score + 2:
        reason = _decision_reason("技术低吸", buy_reasons, buy_risk, buy_score)
        reason = f"{path_view['text']}；{reason}；{_price_plan('buy', quote.price)}"
        action = "低位机会" if include_watch else "正T低吸观察"
        return [Signal(config.name, config.code, hm, quote.price, action, reason, buy_score)]
    if sell_ready and path_view["sell_ok"] and sell_score >= max(sell_confirm, strict_min_score) and sell_score >= buy_score + 2:
        reason = _decision_reason("高位回落", sell_reasons, sell_risk, sell_score)
        reason = f"{path_view['text']}；{reason}；{_price_plan('sell', quote.price)}"
        action = "高位机会" if include_watch else "反T高抛观察"
        return [Signal(config.name, config.code, hm, quote.price, action, reason, sell_score)]
    if include_watch:
        buy_watch = (
            price_space
            and dev <= buy_min_dev
            and rebound >= 0.10
            and avg_slope > -0.28
            and buy_score >= 5
            and buy_score >= sell_score
        )
        sell_watch = (
            price_space
            and dev >= sell_min_dev
            and near_high_pullback >= 0.08
            and avg_slope < 0.32
            and sell_score >= 5
            and sell_score >= buy_score
        )
        if buy_watch and path_view["buy_ok"]:
            reason = _decision_reason("低位观察", buy_reasons, buy_risk, buy_score)
            reason = f"{path_view['text']}；{reason}；{_price_plan('buy', quote.price)}"
            return [Signal(config.name, config.code, hm, quote.price, "低位机会", reason, buy_score)]
        if sell_watch and path_view["sell_ok"]:
            reason = _decision_reason("高位观察", sell_reasons, sell_risk, sell_score)
            reason = f"{path_view['text']}；{reason}；{_price_plan('sell', quote.price)}"
            return [Signal(config.name, config.code, hm, quote.price, "高位机会", reason, sell_score)]
        if change <= -2.5 and dev <= -observe_dev:
            reason = f"{path_view['text']}；跌幅{abs(change):.1f}%，现价低于均价{abs(dev):.2f}%，未形成明确低吸结构，先观察承接；{_price_plan('buy', quote.price)}"
            return [Signal(config.name, config.code, hm, quote.price, "弱势观察", reason, max(buy_score, 1))]
        if change >= 2.5 and dev >= observe_dev:
            reason = f"{path_view['text']}；涨幅{change:.1f}%，现价高于均价{dev:.2f}%，未形成明确高抛回落，先观察量价；{_price_plan('sell', quote.price)}"
            return [Signal(config.name, config.code, hm, quote.price, "强势观察", reason, max(sell_score, 1))]
    return []


def load_adaptive_strategy() -> dict:
    strategy = dict(DEFAULT_STRATEGY)
    try:
        data = json.loads(ADAPTIVE_STRATEGY_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if isinstance(data, dict):
        for key, default in DEFAULT_STRATEGY.items():
            if key not in data:
                continue
            try:
                value = data.get(key)
                strategy[key] = int(value) if isinstance(default, int) else float(value)
            except Exception:
                strategy[key] = default
    return strategy


def _vwap(quote: Quote, minutes: List[MinuteBar]) -> Optional[float]:
    if minutes and minutes[-1].volume_lot > 0 and minutes[-1].amount_yuan > 0:
        return minutes[-1].amount_yuan / (minutes[-1].volume_lot * 100.0)
    if quote.volume_lot > 0 and quote.amount_wan > 0:
        return quote.amount_wan * 10000.0 / (quote.volume_lot * 100.0)
    return None


def _pct_change(start: float, end: float) -> float:
    if not math.isfinite(start) or start <= 0:
        return 0.0
    return (end - start) / start * 100.0


def _decision_reason(label: str, reasons: List[str], risks: List[str], score: int) -> str:
    text = "\uff0c".join(reasons[:3])
    if risks:
        text += "；风险：" + "，".join(risks[:2])
    return f"{label}\uff0c\u5f97\u5206{score}\uff0c{text}"


def _price_plan(side: str, price: float) -> str:
    strategy = load_adaptive_strategy()
    target_pct = max(0.10, min(1.50, float(strategy.get("normal_take_profit_pct", 0.60)))) / 100
    stop_pct = max(0.0045, min(0.0120, target_pct * 1.5))
    if side == "buy":
        stop = price * (1 - stop_pct)
        target = price * (1 + target_pct)
        force = price * 0.998
        return f"低位计划：{force:.2f}附近/以下买入，{target:.2f}附近卖出，小目标{target_pct*100:.2f}%，{stop:.2f}跌破止损"
    stop = price * (1 + stop_pct)
    target = price * (1 - target_pct)
    force = price * 1.002
    return f"高位计划：{force:.2f}附近/以上卖出，{target:.2f}附近接回，小目标{target_pct*100:.2f}%，{stop:.2f}突破止损接回"


def _intraday_swing_signal(
    config: StockConfig,
    hm: str,
    quote: Quote,
    avg: float,
    dev: float,
    day_high: float,
    day_low: float,
    day_range: float,
    vol_ratio: float,
    include_watch: bool,
    path_view: dict,
) -> Optional[Signal]:
    strategy = load_adaptive_strategy()
    if not int(strategy.get("swing_enabled", 1)) or day_high <= 0 or day_low <= 0 or avg <= 0:
        return None
    min_space = float(strategy.get("swing_min_space_pct", 0.85))
    if day_range < min_space:
        return None
    price = quote.price
    lift_from_low = (price - day_low) / day_low * 100.0
    fade_from_high = (day_high - price) / day_high * 100.0
    high_low_space = (day_high - day_low) / day_low * 100.0
    target_pct = max(0.10, min(1.50, float(strategy.get("normal_take_profit_pct", 0.40))))
    buy_target = price * (1 + target_pct / 100.0)
    sell_cover = price * (1 - target_pct / 100.0)
    score_base = 7 if include_watch else 8

    near_vwap_low = price <= avg * 1.0015 and dev <= 0.20
    high_has_faded = fade_from_high >= float(strategy.get("swing_buy_fade_pct", 0.75))
    low_zone = lift_from_low <= max(0.85, min_space)
    if path_view.get("buy_ok", True) and near_vwap_low and high_has_faded and low_zone:
        action = "低位机会" if include_watch else "正T低吸观察"
        reason = (
            f"{path_view.get('text', '走势预判：待确认')}；"
            f"日内高点{day_high:.2f}后回落{fade_from_high:.2f}%，现价接近黄线{avg:.2f}下方，"
            f"属于回落接回/低吸区；计划：{price:.2f}附近买入，{buy_target:.2f}附近卖出，"
            f"空间够但不贪心"
        )
        return Signal(config.name, config.code, hm, price, action, reason, score_base)

    near_day_high = fade_from_high <= 0.55
    enough_lift = lift_from_low >= float(strategy.get("swing_sell_lift_pct", 0.85))
    above_vwap = price >= avg * 1.002
    high_has_faded = fade_from_high >= max(0.18, float(strategy.get("opening_fade_pct", 0.38)) * 0.45)
    high_is_extended = dev >= max(0.65, float(strategy.get("sell_min_dev", 1.35)) * 0.55)
    vol_ok = vol_ratio >= 0.75
    if path_view.get("sell_ok", True) and int(strategy.get("reverse_t_enabled", 1)) and near_day_high and enough_lift and above_vwap and high_has_faded and high_is_extended and vol_ok:
        action = "高位机会" if include_watch else "反T高抛观察"
        reason = (
            f"{path_view.get('text', '走势预判：待确认')}；"
            f"日内低点{day_low:.2f}后拉升{lift_from_low:.2f}%，现价高于黄线{avg:.2f}，"
            f"接近日内高点{day_high:.2f}；计划：{price:.2f}附近卖出，{sell_cover:.2f}附近接回，"
            f"优先锁定小利润"
        )
        return Signal(config.name, config.code, hm, price, action, reason, score_base)

    if high_low_space >= 1.0 and abs(dev) <= 0.20 and include_watch:
        reason = (
            f"{path_view.get('text', '走势预判：待确认')}；"
            f"日内振幅{high_low_space:.2f}%，价格回到黄线附近，正反T都要等下一次靠近日内高低点；"
            f"参考区间：{day_low:.2f}附近低吸，{day_high:.2f}附近高抛"
        )
        return Signal(config.name, config.code, hm, price, "黄线战斗观察", reason, 5)
    return None


def _minute_volumes(minutes: List[MinuteBar]) -> List[float]:
    if not minutes:
        return []
    out: List[float] = []
    last = 0.0
    for bar in minutes:
        vol = max(bar.volume_lot - last, 0.0)
        if vol == 0 and bar.volume_lot > 0 and not out:
            vol = bar.volume_lot
        out.append(vol)
        last = max(last, bar.volume_lot)
    return out


def _volume_ratio(volumes: List[float]) -> float:
    if len(volumes) < 8:
        return 0.0
    base = volumes[-8:-1]
    avg = sum(base) / len(base) if base else 0.0
    return volumes[-1] / avg if avg > 0 else 0.0


def _vwap_slope(minutes: List[MinuteBar]) -> float:
    avgs: List[float] = []
    for bar in minutes:
        if bar.volume_lot > 0 and bar.amount_yuan > 0:
            avgs.append(bar.amount_yuan / (bar.volume_lot * 100.0))
    if len(avgs) < 8:
        return 0.0
    old = sum(avgs[-8:-3]) / 5
    new = sum(avgs[-5:]) / 5
    return (new - old) / old * 100.0 if old > 0 else 0.0


def _vwap_devs(minutes: List[MinuteBar], lookback: int = 8) -> List[float]:
    out: List[float] = []
    for bar in minutes[-lookback:]:
        if bar.price > 0 and bar.volume_lot > 0 and bar.amount_yuan > 0:
            avg = bar.amount_yuan / (bar.volume_lot * 100.0)
            if avg > 0:
                out.append((bar.price - avg) / avg * 100.0)
    return out


def _vwap_reclaiming(devs: List[float], min_reclaim: float) -> bool:
    if len(devs) < 4:
        return False
    current = devs[-1]
    worst = min(devs[:-1])
    return worst <= -0.9 and current - worst >= min_reclaim and current >= devs[-2]


def _vwap_fading(devs: List[float], min_fade: float) -> bool:
    if len(devs) < 4:
        return False
    current = devs[-1]
    best = max(devs[:-1])
    return best >= 0.9 and best - current >= min_fade and current <= devs[-2]


def _opening_buy_ready(strategy: dict, hm: str, prices: List[float], price: float, avg: float, dev: float, vol_ratio: float, momentum_3: float) -> bool:
    if not int(strategy.get("opening_enabled", 1)) or not _is_opening_trade_window(hm) or len(prices) < 8:
        return False
    open_price = prices[0]
    low = min(prices)
    drop = (low - open_price) / open_price * 100.0 if open_price > 0 else 0.0
    reclaim = (price - low) / low * 100.0 if low > 0 else 0.0
    low_pos = prices.index(low) if low in prices else 0
    low_open_reclaim = (
        low_pos <= max(8, len(prices) // 2)
        and reclaim >= 0.25
        and -1.6 <= dev <= 0.45
        and price >= avg * 0.996
    )
    panic_reclaim = drop <= float(strategy.get("opening_drop_pct", -1.8))
    return (
        (panic_reclaim or low_open_reclaim)
        and reclaim >= (0.25 if low_open_reclaim else float(strategy.get("opening_reclaim_pct", 0.45)))
        and -2.7 <= dev <= (0.55 if low_open_reclaim else 0.35)
        and vol_ratio >= (0.70 if low_open_reclaim else 0.85)
        and (momentum_3 > (-0.03 if low_open_reclaim else 0) or price > max(prices[-2], prices[-3]))
    )


def _opening_sell_ready(strategy: dict, hm: str, prices: List[float], price: float, avg: float, dev: float, vol_ratio: float, momentum_3: float) -> bool:
    if not int(strategy.get("opening_enabled", 1)) or not int(strategy.get("reverse_t_enabled", 1)):
        return False
    if not _is_opening_trade_window(hm) or len(prices) < 8:
        return False
    open_price = prices[0]
    high = max(prices)
    spike = (high - open_price) / open_price * 100.0 if open_price > 0 else 0.0
    fade = (high - price) / high * 100.0 if high > 0 else 0.0
    return (
        spike >= float(strategy.get("opening_spike_pct", 2.4))
        and fade >= float(strategy.get("opening_fade_pct", 0.65))
        and dev >= 0.0
        and vol_ratio >= 0.75
        and (momentum_3 < -0.05 or price < min(prices[-2], prices[-3]))
    )


def _intraday_path_view(
    hm: str,
    prices: List[float],
    price: float,
    avg: float,
    dev: float,
    change: float,
    avg_slope: float,
    momentum_5: float,
) -> dict:
    """Lightweight pre-trade path forecast used before doing T."""
    if len(prices) < 10 or avg <= 0:
        return {"label": "路径待确认", "text": "走势预判：路径待确认", "buy_ok": True, "sell_ok": True}
    day_high = max(prices)
    day_low = min(prices)
    open_price = prices[0]
    high_pos = prices.index(day_high)
    low_pos = prices.index(day_low)
    high_fade = (day_high - price) / day_high * 100.0 if day_high > 0 else 0.0
    low_reclaim = (price - day_low) / day_low * 100.0 if day_low > 0 else 0.0
    open_move = (price - open_price) / open_price * 100.0 if open_price > 0 else 0.0
    early = _is_opening_trade_window(hm)

    if early and low_pos <= max(6, len(prices) // 3) and low_reclaim >= 0.35 and price >= avg * 0.996:
        return {
            "label": "低开修复",
            "text": "走势预判：低位修复，正T优先，跌破早盘低点失效",
            "buy_ok": True,
            "sell_ok": False if dev <= 0.6 else True,
        }
    if early and high_pos <= max(6, len(prices) // 3) and high_fade >= 0.35 and momentum_5 <= 0:
        return {
            "label": "冲高回落",
            "text": "走势预判：冲高回落，反T优先，不追正T",
            "buy_ok": False if dev >= -0.8 else True,
            "sell_ok": True,
        }
    if avg_slope <= -0.14 and price < avg and momentum_5 <= -0.18 and open_move <= 0:
        return {
            "label": "单边偏弱",
            "text": "走势预判：单边偏弱，只等深水止跌，不主动正T",
            "buy_ok": dev <= -2.2 and low_reclaim >= 0.55,
            "sell_ok": True,
        }
    if avg_slope >= 0.14 and price > avg and momentum_5 >= 0.18 and open_move >= 0:
        return {
            "label": "低开高走/趋势偏强",
            "text": "走势预判：趋势偏强，正T等回踩，反T只做明显冲高回落",
            "buy_ok": True,
            "sell_ok": high_fade >= 0.55 and dev >= 1.2,
        }
    return {
        "label": "震荡做T",
        "text": "走势预判：震荡做T，围绕黄线高抛低吸",
        "buy_ok": True,
        "sell_ok": True,
    }


def _format_time(time_raw: str, minutes: List[MinuteBar]) -> str:
    if time_raw and len(time_raw) >= 12:
        return f"{time_raw[8:10]}:{time_raw[10:12]}"
    if minutes:
        t = minutes[-1].time
        if len(t) == 4:
            return f"{t[:2]}:{t[2:]}"
    return datetime.now().strftime("%H:%M")


def _is_signal_window(hm: str) -> bool:
    try:
        hour, minute = [int(part) for part in hm.split(":", 1)]
        now = time(hour, minute)
    except Exception:
        return True
    return time(9, 35) <= now <= time(14, 30)


def _is_observation_window(hm: str) -> bool:
    try:
        hour, minute = [int(part) for part in hm.split(":", 1)]
        now = time(hour, minute)
    except Exception:
        return True
    return time(9, 30) <= now <= time(14, 55)


def _is_low_buy_window(hm: str) -> bool:
    try:
        hour, minute = [int(part) for part in hm.split(":", 1)]
        now = time(hour, minute)
    except Exception:
        return True
    return time(9, 35) <= now <= time(11, 15) or time(13, 0) <= now <= time(13, 30)


def _is_opening_first_half(hm: str) -> bool:
    try:
        hour, minute = [int(part) for part in hm.split(":", 1)]
        now = time(hour, minute)
    except Exception:
        return False
    return time(9, 35) <= now < time(10, 0)


def _second_buy_confirm_prices(prices: List[float]) -> bool:
    window = [p for p in prices[-19:] if p > 0]
    if len(window) < 10:
        return False
    low = min(window[:-2])
    low_pos = window.index(low)
    if low_pos >= len(window) - 4:
        return False
    high_after = max(window[low_pos + 1 :])
    rebound = (high_after - low) / low * 100.0 if low > 0 else 0.0
    pullback_low = min(window[-4:])
    current = window[-1]
    return rebound >= 0.55 and pullback_low > low * 1.001 and current >= pullback_low * 1.001 and current > window[-2]


def _sharp_reversal_buy_prices(prices: List[float]) -> bool:
    if len(prices) < 5 or prices[-5] <= 0:
        return False
    drop = (prices[-1] - prices[-5]) / prices[-5] * 100.0
    return drop <= -2.4 and prices[-1] > prices[-2] > prices[-3]


def _second_sell_confirm_prices(prices: List[float]) -> bool:
    window = [p for p in prices[-19:] if p > 0]
    if len(window) < 10:
        return False
    high = max(window[:-2])
    high_pos = window.index(high)
    if high_pos >= len(window) - 4:
        return False
    low_after = min(window[high_pos + 1 :])
    fade = (high - low_after) / high * 100.0 if high > 0 else 0.0
    rebound_high = max(window[-4:])
    current = window[-1]
    return fade >= 0.55 and rebound_high < high * 0.999 and current <= rebound_high * 0.999 and current < window[-2]


def _sharp_reversal_sell_prices(prices: List[float]) -> bool:
    if len(prices) < 5 or prices[-5] <= 0:
        return False
    rise = (prices[-1] - prices[-5]) / prices[-5] * 100.0
    return rise >= 2.4 and prices[-1] < prices[-2] < prices[-3]


def _hm_to_minutes(hm: str) -> int:
    try:
        hour, minute = [int(part) for part in hm.split(":", 1)]
        return hour * 60 + minute
    except Exception:
        return 0


def _is_opening_trade_window(hm: str) -> bool:
    try:
        hour, minute = [int(part) for part in hm.split(":", 1)]
        now = time(hour, minute)
    except Exception:
        return False
    return time(9, 35) <= now <= time(10, 5)


def print_signals(stocks: List[StockConfig]) -> int:
    lines: List[str] = []
    for stock in stocks:
        for signal in analyze(stock):
            lines.append(signal.line())
    if lines:
        print("\n".join(lines))
    return 0
