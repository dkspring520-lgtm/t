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
    "quantbrain": SmartTProfile("quantbrain", "量化学习", 8, 5, 0.35, 4),
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


def _quantbrain_features(points: Iterable[Mapping[str, object]]) -> dict:
    rows = [item for item in points if _number(item.get("price")) > 0]
    prices = [_number(item.get("price")) for item in rows]
    changes = [prices[index] - prices[index - 1] for index in range(1, len(prices))]
    window = changes[-14:]
    gains = sum(max(value, 0.0) for value in window) / max(len(window), 1)
    losses = sum(max(-value, 0.0) for value in window) / max(len(window), 1)
    rsi = 50.0 if not window else 100.0 if losses <= 1e-12 else 100.0 - 100.0 / (1.0 + gains / losses)
    returns = [abs(changes[index] / max(prices[index], 1e-9)) * 100.0 for index in range(len(changes))]
    volatility = sum(returns[-14:]) / max(len(returns[-14:]), 1)
    volumes = [max(0.0, _number(item.get("volumeDelta"), _number(item.get("volDelta"), _number(item.get("volume"))))) for item in rows]
    recent = volumes[-5:]
    baseline = volumes[-20:-5]
    recent_avg = sum(recent) / max(len(recent), 1)
    baseline_avg = sum(baseline) / max(len(baseline), 1)
    volume_ratio = recent_avg / baseline_avg if baseline_avg > 0 else 1.0
    return {"rsi": round(rsi, 2), "volatilityPct": round(volatility, 4), "volumeRatio": round(volume_ratio, 3)}


def _radar_score(value: object) -> float | None:
    """Return a valid 0-100 market-radar score, or ``None`` when unavailable."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, score))


def _has_pullback_confirmation(points: Iterable[Mapping[str, object]], current: float) -> bool:
    """A reverse-T in an overheated market needs a real turn, not just a high price."""
    prices = [_number(point.get("price")) for point in points]
    prices = [price for price in prices if price > 0]
    if len(prices) < 3 or current <= 0:
        return False
    recent_peak = max(prices[-4:-1])
    return current < recent_peak and prices[-1] <= prices[-2]


def evaluate_trade_decision(
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
    learned_params: Mapping[str, object] | None = None,
    estimated_cycle_cost_pct: float = 0.10,
    slippage_per_side_pct: float = 0.02,
    market_radar_score: object = None,
    structural_stop_price: object = None,
    min_reward_risk_ratio: float = 1.25,
    min_structural_risk_pct: float = 0.35,
    max_structural_risk_pct: float = 0.80,
) -> dict:
    """Single Smart-T decision gate used by live monitoring and replay.

    Callers may build their raw signal from different data adapters, but a
    candidate cannot execute until it has passed this same direction, auction,
    trend, score, cost and time gate.  Keeping this function data-source free
    makes historical replay deterministic and avoids a second policy engine.
    """

    selected = resolve_profile(profile)
    learned = dict(learned_params or {}) if selected.name == "quantbrain" else {}
    if learned:
        selected = SmartTProfile(
            "quantbrain",
            "量化学习",
            max(7, min(10, round(_number(learned.get("confirmed_score"), 82.0) / 10.0))),
            max(3, min(15, int(_number(learned.get("cooldown_bars"), 5)))),
            max(0.25, min(0.65, _number(learned.get("min_expected_net_rate"), 0.0035) * 100.0)),
            4,
        )
    minute = _clock_minutes(time_text)
    current = _number(price)
    avg = _number(average)
    day_high = _number(high, current)
    day_low = _number(low, current)
    raw_score = max(0, int(_number(signal_score)))
    action = str(signal_action or "")
    action_upper = action.upper()
    direction = action_upper if action_upper in {"BUY_FIRST", "SELL_FIRST"} else "BUY_FIRST" if any(word in action for word in ("低吸", "买入", "正T")) else "SELL_FIRST" if any(word in action for word in ("高抛", "卖出", "反T")) else ""
    point_list = list(points)
    regime = market_regime(point_list, avg, minute)
    quant_features = _quantbrain_features(point_list) if selected.name == "quantbrain" else {}
    quant_adjustment = 0
    if selected.name == "quantbrain":
        rsi = _number(quant_features.get("rsi"), 50.0)
        volume_ratio = _number(quant_features.get("volumeRatio"), 1.0)
        if direction == "BUY_FIRST":
            quant_adjustment += 1 if 32 <= rsi <= 55 else -2 if rsi >= 72 else 0
        elif direction == "SELL_FIRST":
            quant_adjustment += 1 if 55 <= rsi <= 78 else -2 if rsi <= 28 else 0
        if volume_ratio >= 1.15:
            quant_adjustment += 1
        elif volume_ratio < _number(learned.get("min_volume_ratio"), 0.18):
            quant_adjustment -= 1
    effective_score = max(0, raw_score + quant_adjustment)
    radar_score = _radar_score(market_radar_score)
    radar_band = "UNAVAILABLE"
    radar_adjustment = 0
    radar_block = ""
    if radar_score is not None:
        if radar_score >= 88:
            radar_band = "OVERHEATED"
            if direction == "BUY_FIRST" and current >= avg:
                radar_block = "RADAR_OVERHEAT_NO_CHASE"
            elif direction == "SELL_FIRST":
                radar_adjustment = 2
                if not _has_pullback_confirmation(point_list, current):
                    radar_block = "RADAR_OVERHEAT_WAIT_PULLBACK"
            elif direction == "BUY_FIRST":
                radar_adjustment = 1
        elif radar_score >= 75:
            radar_band = "STRONG"
            if direction == "SELL_FIRST":
                radar_adjustment = 2
        elif radar_score >= 45:
            radar_band = "RANGE"
        elif radar_score >= 25:
            radar_band = "WEAK"
            if direction == "BUY_FIRST":
                radar_adjustment = 2
        else:
            radar_band = "RISK_OFF"
            if direction == "BUY_FIRST":
                radar_block = "RADAR_RISK_OFF_BUY_BLOCKED"
            elif direction == "SELL_FIRST":
                radar_adjustment = 2
    auction_preference = str(auction_direction or "")
    auction_gate_state = str(auction_state or "NEUTRAL").upper()
    auction_confirmed = auction_gate_state == "CONFIRMED" and auction_preference in {"BUY_FIRST", "SELL_FIRST"}
    opening_trial = 9 * 60 + 35 <= minute <= 10 * 60
    required_score = min(10, selected.confirmed_score + radar_adjustment)
    required_gross = selected.min_expected_net_pct + max(0.0, estimated_cycle_cost_pct) + 2 * max(0.0, slippage_per_side_pct)
    # The executable target is the current VWAP reversion, not an optimistic
    # fraction of today's complete range.  The latter can include a move that
    # happened before the signal and led to systematically overstated edges.
    if direction == "BUY_FIRST":
        available_space = max(0.0, (avg - current) / max(current, 1e-9) * 100.0)
    elif direction == "SELL_FIRST":
        available_space = max(0.0, (current - avg) / max(current, 1e-9) * 100.0)
    else:
        available_space = 0.0
    if opening_trial and auction_confirmed and current > 0 and day_high > day_low > 0:
        # The confirmed opening strategy is a staged continuation trade:
        # low-gap BUY enters only after reclaiming VWAP, while high-gap SELL
        # enters only after losing VWAP.  Requiring the normal mean-reversion
        # side of VWAP here made both valid opening directions impossible.
        # Use a conservative fraction of the range observed *so far* instead;
        # this stays causal and still requires enough room to cover costs.
        observed_opening_range = (day_high - day_low) / current * 100.0
        available_space = max(available_space, observed_opening_range * 0.35)

    risk_floor = max(0.10, _number(min_structural_risk_pct, 0.35))
    risk_cap = max(risk_floor, _number(max_structural_risk_pct, 0.80))
    explicit_stop = _number(structural_stop_price)
    recent_prices = [_number(point.get("price")) for point in point_list[-6:]]
    recent_prices = [value for value in recent_prices if value > 0]
    if current > 0 and direction == "BUY_FIRST" and 0 < explicit_stop < current:
        structural_risk = (current - explicit_stop) / current * 100.0
    elif current > 0 and direction == "SELL_FIRST" and explicit_stop > current:
        structural_risk = (explicit_stop - current) / current * 100.0
    elif current > 0 and recent_prices:
        raw_risk = (
            (current - min(recent_prices + [current])) / current * 100.0
            if direction == "BUY_FIRST"
            else (max(recent_prices + [current]) - current) / current * 100.0
        )
        structural_risk = min(risk_cap, max(risk_floor, raw_risk))
    else:
        structural_risk = risk_cap
    structural_risk = max(risk_floor, structural_risk)
    # Zero is reserved for deterministic legacy A/B replay.  Production
    # profiles clamp the configured value to at least 1.0 before reaching here.
    required_reward_risk = max(0.0, _number(min_reward_risk_ratio, 1.25))
    reward_risk_ratio = available_space / structural_risk if structural_risk > 0 else 0.0

    state = "WAIT_CONFIRMATION"
    reason = "观察状态不直接成交，等待反转确认。"
    confirmed = False
    new_cycle_allowed = False
    force_close = minute >= 14 * 60 + 50
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
    elif opening_trial and not auction_confirmed:
        state, reason = "OPENING_TRIAL_WAIT", "\u5f00\u76d8\u8bd5\u63a2T\u9700\u7ade\u4ef7\u65b9\u5411\u786e\u8ba4\uff1b\u672a\u786e\u8ba4\u5219\u7b49\u5f8510:00\u540e\u8f6c\u5165\u5f53\u524d\u7b56\u7565\u3002"
    elif force_close:
        state, reason = "FORCE_CLOSE", "14:50后只恢复T仓状态，不开启新循环。"
    elif minute >= 14 * 60 + 30:
        state, reason = "ENTRY_CUTOFF", "14:30后停止开启新的T循环。"
    elif not strict_signal or not direction:
        state, reason = "WAIT_CONFIRMATION", "当前仅为观察信号，等待量价反转确认。"
    elif auction_confirmed and direction != auction_preference:
        expected = "正T先买后卖" if auction_preference == "BUY_FIRST" else "反T先卖后买"
        state, reason = "AUCTION_DIRECTION_BLOCKED", f"集合竞价与09:35走势已确认{expected}，拦截相反方向。"
    elif selected.name == "quantbrain" and direction == "BUY_FIRST" and _number(quant_features.get("rsi"), 50) >= 78:
        state, reason = "QUANT_FACTOR_BLOCKED", "量化学习档识别到RSI极度过热，拦截追高正T。"
    elif selected.name == "quantbrain" and direction == "SELL_FIRST" and _number(quant_features.get("rsi"), 50) <= 22:
        state, reason = "QUANT_FACTOR_BLOCKED", "量化学习档识别到RSI极度超卖，拦截低位反T。"
    elif radar_block == "RADAR_RISK_OFF_BUY_BLOCKED":
        state, reason = radar_block, "市场雷达低于25，禁止激进正T；等待强确认反T或继续观察。"
    elif radar_block == "RADAR_OVERHEAT_NO_CHASE":
        state, reason = radar_block, "市场过热时禁止追高正T，必须等待回踩黄线后的确认。"
    elif radar_block == "RADAR_OVERHEAT_WAIT_PULLBACK":
        state, reason = radar_block, "市场过热，反T需先出现真实回落确认，避免强势行情卖飞。"
    elif effective_score < required_score:
        suffix = f"；多因子修正{quant_adjustment:+d}" if selected.name == "quantbrain" else ""
        state, reason = "SCORE_BLOCKED", f"信号{effective_score}分，未达到{selected.label}档{selected.confirmed_score}分门槛{suffix}。"
    elif regime == "OBSERVE":
        state, reason = "REGIME_OBSERVE", "已完成5分钟K线不足，暂不判断趋势。"
    elif regime == "UPTREND" and direction != "BUY_FIRST" and not opening_trial:
        state, reason = "TREND_BLOCKED", "上涨趋势只允许回踩正T，不逆势先卖。"
    elif regime == "DOWNTREND" and direction != "SELL_FIRST" and not opening_trial:
        state, reason = "TREND_BLOCKED", "弱势趋势只允许冲高反T，不逆势加仓。"
    elif available_space + 1e-9 < required_gross:
        state, reason = "EDGE_BLOCKED", f"预估毛价差{available_space:.2f}%不足，至少需要{required_gross:.2f}%。"
    elif required_reward_risk > 0 and reward_risk_ratio + 1e-9 < required_reward_risk:
        state, reason = "REWARD_RISK_BLOCKED", f"预估收益风险比{reward_risk_ratio:.2f}不足，至少需要{required_reward_risk:.2f}。"
    else:
        confirmed = True
        new_cycle_allowed = True
        state = "READY"
        style = "回踩正T" if direction == "BUY_FIRST" else "冲高反T"
        factor_text = ""
        if selected.name == "quantbrain":
            factor_text = f"，RSI {quant_features.get('rsi', 50):.0f}、量比 {quant_features.get('volumeRatio', 1):.2f}、经验修正{quant_adjustment:+d}"
        reason = f"{selected.label}档确认：{style}，预估毛价差{available_space:.2f}%、收益风险比{reward_risk_ratio:.2f}{factor_text}，覆盖费用后再执行。"

    if state == "SCORE_BLOCKED" and radar_score is not None and radar_adjustment:
        reason = f"信号{effective_score}分；市场雷达{radar_score:.0f}分，确认门槛提高至{required_score}分。"

    return {
        "profile": asdict(selected),
        "regime": regime,
        "state": state,
        "direction": direction,
        "auctionDirection": auction_preference,
        "auctionState": auction_gate_state,
        "openingTrial": opening_trial and auction_confirmed,
        "positionFraction": (1.0 / 6.0) if opening_trial and auction_confirmed else 1.0,
        "confirmed": confirmed,
        "newCycleAllowed": new_cycle_allowed,
        "forceClose": force_close,
        "rawScore": raw_score,
        "effectiveScore": effective_score,
        "requiredScore": required_score,
        "score": min(100, effective_score * 10),
        "marketRadarScore": round(radar_score, 1) if radar_score is not None else None,
        "marketRadarBand": radar_band,
        "radarScoreAdjustment": radar_adjustment,
        "quantFeatures": quant_features,
        "experienceVersion": str(learned.get("version_id") or "初始经验") if selected.name == "quantbrain" else "",
        "requiredGrossSpreadPct": round(required_gross, 3),
        "availableSpreadPct": round(available_space, 3),
        "structuralRiskPct": round(structural_risk, 3),
        "rewardRiskRatio": round(reward_risk_ratio, 3),
        "requiredRewardRiskRatio": round(required_reward_risk, 3),
        "reason": reason,
    }


def evaluate_smart_t(**kwargs: object) -> dict:
    """Compatibility alias for older callers.

    New code should use :func:`evaluate_trade_decision` so the shared decision
    boundary is explicit.
    """
    return evaluate_trade_decision(**kwargs)
