"""Fast, dependency-free Smart-T policy gate.

This module does not fetch data and does not call an LLM.  It decides whether
an already-computed intraday signal may start a complete T cycle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class SmartTProfile:
    name: str
    label: str
    confirmed_score: int
    cooldown_minutes: int
    min_expected_net_pct: float
    max_daily_cycles: int


PROFILES = {
    "steady": SmartTProfile("steady", "稳健", 9, 8, 0.50, 2),
    "balanced": SmartTProfile("balanced", "平衡", 8, 5, 0.35, 3),
    "sensitive": SmartTProfile("sensitive", "灵敏", 7, 3, 0.25, 5),
}


def resolve_profile(value: object) -> SmartTProfile:
    return PROFILES.get(str(value or "balanced").lower(), PROFILES["balanced"])


def _clock_minutes(value: object) -> int:
    text = str(value or "").strip().replace(":", "")
    if len(text) < 4 or not text[:4].isdigit():
        return -1
    return int(text[:2]) * 60 + int(text[2:4])


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _completed_five_minute_closes(points: Iterable[Mapping[str, object]], now_minute: int) -> list[float]:
    buckets: dict[int, float] = {}
    current_bucket = now_minute // 5 if now_minute >= 0 else 10**9
    for point in points:
        minute = _clock_minutes(point.get("time"))
        price = _number(point.get("price"))
        if minute < 0 or price <= 0:
            continue
        bucket = minute // 5
        if bucket < current_bucket:
            buckets[bucket] = price
    return [buckets[key] for key in sorted(buckets)]


def market_regime(points: Iterable[Mapping[str, object]], average: float, now_minute: int) -> str:
    closes = _completed_five_minute_closes(points, now_minute)
    if len(closes) < 3 or average <= 0:
        return "OBSERVE"
    last, previous, earlier = closes[-1], closes[-2], closes[-3]
    slope_pct = (last - earlier) / max(abs(earlier), 1e-9) * 100.0
    if last > average and previous >= earlier and slope_pct >= 0.18:
        return "UPTREND"
    if last < average and previous <= earlier and slope_pct <= -0.18:
        return "DOWNTREND"
    return "RANGE"


def evaluate_smart_t(
    *,
    profile: object = "balanced",
    time_text: object,
    price: object,
    average: object,
    high: object,
    low: object,
    points: Iterable[Mapping[str, object]],
    signal_action: object,
    signal_score: object,
    strict_signal: bool,
    quote_stale: bool = False,
    market_status: object = "交易中",
    auction_direction: object = "",
    auction_state: object = "NEUTRAL",
    estimated_cycle_cost_pct: float = 0.10,
    slippage_per_side_pct: float = 0.02,
) -> dict:
    """Evaluate an existing signal and return a UI/execution policy payload."""

    selected = resolve_profile(profile)
    minute = _clock_minutes(time_text)
    current = _number(price)
    avg = _number(average)
    day_high = _number(high, current)
    day_low = _number(low, current)
    raw_score = max(0, int(_number(signal_score)))
    action = str(signal_action or "")
    direction = "BUY_FIRST" if any(word in action for word in ("低吸", "买入", "正T")) else "SELL_FIRST" if any(word in action for word in ("高抛", "卖出", "反T")) else ""
    regime = market_regime(points, avg, minute)
    required_gross = selected.min_expected_net_pct + max(0.0, estimated_cycle_cost_pct) + 2 * max(0.0, slippage_per_side_pct)
    day_range = (day_high - day_low) / max(current, 1e-9) * 100.0 if current > 0 and day_high >= day_low else 0.0
    vwap_space = abs(current - avg) / max(current, 1e-9) * 100.0 if current > 0 and avg > 0 else 0.0
    available_space = max(vwap_space, day_range * 0.35)

    state = "WAIT_CONFIRMATION"
    reason = "观察状态不直接成交，等待反转确认。"
    confirmed = False
    new_cycle_allowed = False
    force_close = minute >= 14 * 60 + 50
    auction_preference = str(auction_direction or "")
    auction_gate_state = str(auction_state or "NEUTRAL").upper()
    auction_confirmed = auction_gate_state == "CONFIRMED" and auction_preference in {"BUY_FIRST", "SELL_FIRST"}

    if quote_stale or current <= 0 or avg <= 0:
        state, reason = "DATA_RISK", "行情延迟或价格数据不完整，暂停开启新循环。"
    elif str(market_status) != "交易中":
        state, reason = "MARKET_CLOSED", "当前不在连续竞价时段。"
    elif minute < 9 * 60 + 35:
        state, reason = "AUCTION_WAIT_CONFIRMATION", "09:35前只制定竞价预案，不成交。"
    elif auction_preference and auction_gate_state in {"PENDING_CONFIRMATION", "WAIT_DATA"} and minute < 9 * 60 + 45:
        state, reason = "AUCTION_WAIT_CONFIRMATION", "集合竞价方向尚未满足两项确认条件，继续等待。"
    elif minute < 9 * 60 + 45 and not auction_confirmed:
        state, reason = "OPENING_OBSERVE", "竞价方向未确认，09:45前继续观察开盘噪声。"
    elif force_close:
        state, reason = "FORCE_CLOSE", "14:50后只恢复T仓状态，不开启新循环。"
    elif minute >= 14 * 60 + 30:
        state, reason = "ENTRY_CUTOFF", "14:30后停止开启新的T循环。"
    elif not strict_signal or not direction:
        state, reason = "WAIT_CONFIRMATION", "当前仅为观察信号，等待量价反转确认。"
    elif auction_confirmed and direction != auction_preference:
        expected = "正T先买后卖" if auction_preference == "BUY_FIRST" else "反T先卖后买"
        state, reason = "AUCTION_DIRECTION_BLOCKED", f"集合竞价与09:35走势已确认{expected}，拦截相反方向。"
    elif raw_score < selected.confirmed_score:
        state, reason = "SCORE_BLOCKED", f"信号{raw_score}分，未达到{selected.label}档{selected.confirmed_score}分门槛。"
    elif regime == "OBSERVE":
        state, reason = "REGIME_OBSERVE", "已完成5分钟K线不足，暂不判断趋势。"
    elif regime == "UPTREND" and direction != "BUY_FIRST":
        state, reason = "TREND_BLOCKED", "上涨趋势只允许回踩正T，不逆势先卖。"
    elif regime == "DOWNTREND" and direction != "SELL_FIRST":
        state, reason = "TREND_BLOCKED", "弱势趋势只允许冲高反T，不逆势加仓。"
    elif available_space + 1e-9 < required_gross:
        state, reason = "EDGE_BLOCKED", f"预估毛价差{available_space:.2f}%不足，至少需要{required_gross:.2f}%。"
    else:
        confirmed = True
        new_cycle_allowed = True
        state = "READY"
        style = "回踩正T" if direction == "BUY_FIRST" else "冲高反T"
        reason = f"{selected.label}档确认：{style}，预估毛价差{available_space:.2f}%，覆盖费用后再执行。"

    return {
        "profile": asdict(selected),
        "regime": regime,
        "state": state,
        "direction": direction,
        "auctionDirection": auction_preference,
        "auctionState": auction_gate_state,
        "confirmed": confirmed,
        "newCycleAllowed": new_cycle_allowed,
        "forceClose": force_close,
        "rawScore": raw_score,
        "score": min(100, raw_score * 10),
        "requiredGrossSpreadPct": round(required_gross, 3),
        "availableSpreadPct": round(available_space, 3),
        "reason": reason,
    }
