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


def _completed_five_minute_buckets(points: Iterable[Mapping[str, object]], now_minute: int) -> list[dict]:
    """Aggregate only completed five-minute buckets from causal input rows."""
    buckets: dict[int, dict] = {}
    current_bucket = now_minute // 5 if now_minute >= 0 else 10**9
    for point in points:
        minute = _clock_minutes(point.get("time"))
        price = _number(point.get("price"))
        if minute < 0 or price <= 0:
            continue
        bucket = minute // 5
        if bucket < current_bucket:
            row = buckets.setdefault(bucket, {"close": price, "volume": 0.0})
            row["close"] = price
            volume_value = point.get("volumeDelta")
            if volume_value is None:
                volume_value = point.get("volDelta")
            if volume_value is None and str(point.get("volumeMode") or "").lower() != "cumulative":
                volume_value = point.get("volume")
            row["volume"] += max(0.0, _number(volume_value))
    return [buckets[key] for key in sorted(buckets)]


def _completed_five_minute_closes(points: Iterable[Mapping[str, object]], now_minute: int) -> list[float]:
    return [row["close"] for row in _completed_five_minute_buckets(points, now_minute)]


def market_regime_details(points: Iterable[Mapping[str, object]], average: float, now_minute: int) -> dict:
    """Classify the causal intraday path and expose its audit evidence.

    A three-close slope alone labels a spike-and-fade as a trend.  This gate
    additionally measures directional efficiency and step consistency.  The
    volume term only changes confidence when usable data exists, so a quote
    adapter without minute volume does not become an automatic veto.
    """
    buckets = _completed_five_minute_buckets(points, now_minute)
    base = {
        "state": "OBSERVE",
        "confidence": 0,
        "completedBuckets": len(buckets),
        "netMovePct": 0.0,
        "pathEfficiency": 0.0,
        "directionalStepRatio": 0.0,
        "vwapGapPct": 0.0,
        "volumeParticipationRatio": None,
        "reason": "已完成5分钟K线不足，继续观察。",
    }
    if len(buckets) < 3 or average <= 0:
        return base

    window = buckets[-5:]
    closes = [float(row["close"]) for row in window]
    first, last = closes[0], closes[-1]
    steps = [
        (closes[index] - closes[index - 1]) / max(abs(closes[index - 1]), 1e-9) * 100.0
        for index in range(1, len(closes))
    ]
    net_move = (last - first) / max(abs(first), 1e-9) * 100.0
    path_move = sum(abs(value) for value in steps)
    efficiency = min(1.0, abs(net_move) / max(path_move, 1e-9))
    direction = 1 if net_move > 0 else -1 if net_move < 0 else 0
    directional_steps = sum(1 for value in steps if value * direction > 0)
    directional_ratio = directional_steps / max(len(steps), 1)
    vwap_gap = (last - average) / max(abs(average), 1e-9) * 100.0

    volumes = [max(0.0, _number(row.get("volume"))) for row in window]
    positive_volumes = [value for value in volumes if value > 0]
    volume_ratio = None
    volume_adjustment = 5.0
    if len(positive_volumes) >= 3 and len(volumes) >= 3:
        prior = [value for value in volumes[:-2] if value > 0]
        recent = [value for value in volumes[-2:] if value > 0]
        if prior and recent:
            volume_ratio = (sum(recent) / len(recent)) / max(sum(prior) / len(prior), 1e-9)
            if volume_ratio >= 1.10:
                volume_adjustment = 10.0
            elif volume_ratio >= 0.75:
                volume_adjustment = 5.0
            elif volume_ratio < 0.45:
                volume_adjustment = -10.0
            else:
                volume_adjustment = 0.0

    confidence = max(
        0,
        min(100, round(35.0 + efficiency * 35.0 + directional_ratio * 20.0 + volume_adjustment)),
    )
    opening = now_minute <= 10 * 60
    minimum_move = 0.15 if opening else 0.18
    state = "RANGE"
    if (
        net_move >= minimum_move
        and vwap_gap > 0
        and efficiency >= 0.55
        and directional_ratio >= 0.60
        and confidence >= 75
    ):
        state = "UPTREND"
    elif (
        net_move <= -minimum_move
        and vwap_gap < 0
        and efficiency >= 0.55
        and directional_ratio >= 0.60
        and confidence >= 75
    ):
        state = "DOWNTREND"

    state_label = {"UPTREND": "上涨趋势", "DOWNTREND": "下跌趋势", "RANGE": "震荡区间"}[state]
    volume_text = "量能缺失，不作为否决项" if volume_ratio is None else f"量能参与比{volume_ratio:.2f}"
    return {
        "state": state,
        "confidence": confidence,
        "completedBuckets": len(buckets),
        "netMovePct": round(net_move, 3),
        "pathEfficiency": round(efficiency, 3),
        "directionalStepRatio": round(directional_ratio, 3),
        "vwapGapPct": round(vwap_gap, 3),
        "volumeParticipationRatio": round(volume_ratio, 3) if volume_ratio is not None else None,
        "reason": f"{state_label}：路径效率{efficiency:.2f}、同向步数{directional_ratio:.0%}、{volume_text}。",
    }


def market_regime(points: Iterable[Mapping[str, object]], average: float, now_minute: int) -> str:
    """Return the proven execution regime; richer path logic stays shadow-only.

    The path-efficiency classifier is exposed by ``market_regime_details`` for
    audit and later promotion.  It is intentionally not an execution veto yet:
    a balanced cached replay reduced tail loss but did not improve net profit
    or profit factor versus this causal three-bucket rule.
    """
    closes = _completed_five_minute_closes(points, now_minute)
    if len(closes) < 3 or average <= 0:
        return "OBSERVE"
    earlier, previous, last = closes[-3:]
    slope = (last - earlier) / max(abs(earlier), 1e-9) * 100.0
    if last > average and previous >= earlier and slope >= 0.18:
        return "UPTREND"
    if last < average and previous <= earlier and slope <= -0.18:
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


def _point_volume_delta(point: Mapping[str, object]) -> float:
    value = point.get("volumeDelta")
    if value is None:
        value = point.get("volDelta")
    if value is None and str(point.get("volumeMode") or "").lower() != "cumulative":
        value = point.get("volume")
    return max(0.0, _number(value))


def _volume_price_context(
    points: Iterable[Mapping[str, object]],
    current: float,
    day_high: float,
    day_low: float,
) -> dict:
    """Classify a causal volume climax as continuation or exhaustion.

    Large volume alone is deliberately not a top/bottom signal.  It becomes
    actionable only when the current price is near an observed extreme and
    the latest price step confirms either continuation or a right-side turn.
    """
    rows = [item for item in points if _number(item.get("price")) > 0]
    prices = [_number(item.get("price")) for item in rows]
    volumes = [_point_volume_delta(item) for item in rows]
    positive = [value for value in volumes[-13:-3] if value > 0]
    recent_pairs = [
        (index, volumes[index])
        for index in range(max(0, len(volumes) - 3), len(volumes))
        if volumes[index] > 0
    ]
    volume_ratio = None
    spike_index = None
    if len(positive) >= 5 and recent_pairs:
        ordered = sorted(positive)
        midpoint = len(ordered) // 2
        median = (
            ordered[midpoint]
            if len(ordered) % 2
            else (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
        )
        # A climax is a single exceptional bar, not merely two moderately
        # active bars. Keep a three-bar causal window so one exceptional bar
        # may be followed by up to two completed bars that confirm the turn.
        spike_index, spike_volume = max(recent_pairs, key=lambda item: item[1])
        volume_ratio = spike_volume / max(median, 1e-9)

    observed_range = max(0.0, day_high - day_low)
    location = (current - day_low) / observed_range if current > 0 and observed_range > 0 else 0.5
    rising = len(prices) >= 3 and prices[-1] > prices[-2] >= prices[-3]
    falling = len(prices) >= 3 and prices[-1] < prices[-2] <= prices[-3]
    prior_recent = prices[-5:-1]
    phase = "NEUTRAL"
    climax = volume_ratio is not None and volume_ratio >= 3.00
    if volume_ratio is None or volume_ratio < 3.00:
        volume_tier = "NORMAL"
    elif volume_ratio < 5.00:
        volume_tier = "LARGE_3X"
    elif volume_ratio < 8.00:
        volume_tier = "SUPER_5X"
    else:
        volume_tier = "EXTREME_8X"
    if climax and location >= 0.75:
        if falling and prior_recent and current < max(prior_recent):
            phase = "TOP_EXHAUSTION"
        elif rising:
            phase = "UP_CONTINUATION"
    elif climax and location <= 0.25:
        if rising and prior_recent and current > min(prior_recent):
            phase = "BOTTOM_EXHAUSTION"
        elif falling:
            phase = "DOWN_CONTINUATION"
    labels = {
        "NEUTRAL": "量价中性",
        "TOP_EXHAUSTION": "高位放量衰竭",
        "UP_CONTINUATION": "高位放量延续",
        "BOTTOM_EXHAUSTION": "低位放量衰竭",
        "DOWN_CONTINUATION": "低位放量延续",
    }
    return {
        "phase": phase,
        "label": labels[phase],
        "volumeRatio": round(volume_ratio, 3) if volume_ratio is not None else None,
        "priceLocation": round(max(0.0, min(1.0, location)), 3),
        "climax": climax,
        "tier": volume_tier,
        "spikeBarOffset": (len(volumes) - 1 - spike_index) if spike_index is not None else None,
        "reason": (
            "分钟增量成交量不足，量能不参与否决。"
            if volume_ratio is None
            else f"近3根中最大单柱量比{volume_ratio:.2f}，价格位于日内区间{location:.0%}。"
        ),
    }


def _price_impulse_context(
    points: Iterable[Mapping[str, object]],
    current: float,
    day_high: float,
    day_low: float,
) -> dict:
    """Detect a straight three-bar price impulse and its causal exhaustion.

    The impulse must be materially faster than the preceding quiet movement.
    A live impulse is never treated as a reversal.  Exhaustion is reported
    only after one or two later bars stop extending the impulse extreme.
    """
    rows = [item for item in points if _number(item.get("price")) > 0]
    prices = [_number(item.get("price")) for item in rows]
    empty = {
        "phase": "NEUTRAL",
        "label": "价格脉冲未确认",
        "movePct": None,
        "baselineMovePct": None,
        "strength": None,
        "pathEfficiency": None,
        "barsAfterImpulse": None,
    }
    if len(prices) < 12:
        return empty

    changes = [
        (prices[index] - prices[index - 1]) / max(prices[index - 1], 1e-9) * 100.0
        for index in range(1, len(prices))
    ]
    observed_range = max(0.0, day_high - day_low)
    candidates = []
    for bars_after in (0, 1, 2):
        end_index = len(prices) - 1 - bars_after
        start_index = end_index - 3
        if start_index < 6:
            continue
        impulse_steps = changes[start_index:end_index]
        baseline_steps = changes[max(0, start_index - 8):start_index]
        if len(impulse_steps) != 3 or len(baseline_steps) < 5:
            continue
        direction = 1 if all(step > 0 for step in impulse_steps) else -1 if all(step < 0 for step in impulse_steps) else 0
        if not direction:
            continue
        ordered = sorted(abs(step) for step in baseline_steps)
        midpoint = len(ordered) // 2
        baseline_median = ordered[midpoint] if len(ordered) % 2 else (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
        move_pct = (prices[end_index] - prices[start_index]) / max(prices[start_index], 1e-9) * 100.0
        travelled = sum(abs(step) for step in impulse_steps)
        efficiency = min(1.0, abs(move_pct) / max(travelled, 1e-9))
        strength = abs(move_pct) / max(baseline_median * 3.0, 0.09)
        minimum_move = max(0.45, baseline_median * 3.0 * 1.8)
        if abs(move_pct) < minimum_move or efficiency < 0.88 or strength < 1.8:
            continue
        extreme_price = prices[end_index]
        location = (extreme_price - day_low) / observed_range if observed_range > 0 else 0.5
        if direction > 0 and location < 0.72:
            continue
        if direction < 0 and location > 0.28:
            continue
        post_prices = prices[end_index + 1:]
        tolerance_pct = max(0.04, baseline_median * 0.6)
        if not post_prices:
            phase = "UP_IMPULSE_CONTINUATION" if direction > 0 else "DOWN_IMPULSE_CONTINUATION"
        elif direction > 0:
            no_new_high = max(post_prices) <= extreme_price * (1.0 + tolerance_pct / 100.0)
            turned = current <= extreme_price * (1.0 - tolerance_pct / 100.0)
            phase = "TOP_IMPULSE_EXHAUSTION" if no_new_high and turned else "UP_IMPULSE_CONTINUATION"
        else:
            no_new_low = min(post_prices) >= extreme_price * (1.0 - tolerance_pct / 100.0)
            turned = current >= extreme_price * (1.0 + tolerance_pct / 100.0)
            phase = "BOTTOM_IMPULSE_EXHAUSTION" if no_new_low and turned else "DOWN_IMPULSE_CONTINUATION"
        candidates.append((bars_after, abs(move_pct), phase, move_pct, baseline_median, strength, efficiency))

    if not candidates:
        return empty
    # Prefer a confirmed exhaustion over a still-live impulse.  Within the
    # same state, use the most recent and then the strongest structure.
    candidates.sort(key=lambda item: ("EXHAUSTION" in item[2], -item[0], item[1]), reverse=True)
    bars_after, _, phase, move_pct, baseline_median, strength, efficiency = candidates[0]
    labels = {
        "UP_IMPULSE_CONTINUATION": "直线拉升仍在延续",
        "TOP_IMPULSE_EXHAUSTION": "直线拉升后冲高乏力",
        "DOWN_IMPULSE_CONTINUATION": "直线杀跌仍在延续",
        "BOTTOM_IMPULSE_EXHAUSTION": "直线杀跌后止跌回升",
    }
    return {
        "phase": phase,
        "label": labels[phase],
        "movePct": round(move_pct, 3),
        "baselineMovePct": round(baseline_median, 3),
        "strength": round(strength, 2),
        "pathEfficiency": round(efficiency, 3),
        "barsAfterImpulse": bars_after,
    }


def intraday_reversal_context(
    points: Iterable[Mapping[str, object]],
    current: object,
    high: object,
    low: object,
) -> dict:
    """Return a causal reversal candidate from price/volume exhaustion.

    This is intentionally a *candidate*, not an execution order.  The caller
    must still pass it through :func:`evaluate_trade_decision`, which applies
    trend direction, VWAP edge, costs, reward/risk and time gates.  Keeping the
    detector public lets live monitoring and replay use the same definition.
    """
    point_list = list(points)
    price = _number(current)
    day_high = _number(high, price)
    day_low = _number(low, price)
    volume = _volume_price_context(point_list, price, day_high, day_low)
    impulse = _price_impulse_context(point_list, price, day_high, day_low)
    buy_evidence = sum(
        (
            volume["phase"] == "BOTTOM_EXHAUSTION",
            impulse["phase"] == "BOTTOM_IMPULSE_EXHAUSTION",
        )
    )
    sell_evidence = sum(
        (
            volume["phase"] == "TOP_EXHAUSTION",
            impulse["phase"] == "TOP_IMPULSE_EXHAUSTION",
        )
    )
    direction = "BUY_FIRST" if buy_evidence > sell_evidence and buy_evidence else "SELL_FIRST" if sell_evidence else ""
    evidence_count = max(buy_evidence, sell_evidence)
    exceptional_volume = volume.get("tier") in {"SUPER_5X", "EXTREME_8X"}
    quality = "EXTREME" if evidence_count >= 2 or (evidence_count and exceptional_volume) else "STRONG" if evidence_count else "NONE"
    recommended_fraction = 0.33 if quality == "EXTREME" else 0.20 if quality == "STRONG" else 0.0
    return {
        "direction": direction,
        "quality": quality,
        "recommendedBasePositionFraction": recommended_fraction,
        "volume": volume,
        "impulse": impulse,
        "reason": "；".join(
            item["label"]
            for item in (volume, impulse)
            if "EXHAUSTION" in str(item.get("phase") or "")
        ) or "尚未形成右侧衰竭确认",
    }


def _opening_rsi_extremes(points: Iterable[Mapping[str, object]]) -> tuple[float, float]:
    """Return the current causal RSI for opening chase/exhaustion protection.

    A historical extreme that has already reversed is evidence for the setup,
    not a reason to keep blocking it.  The opening gate therefore rejects only
    a candidate that is *still* extreme at the decision minute.
    """
    rows = list(points)
    current_rsi = _number(_quantbrain_features(rows).get("rsi"), 50.0) if len(rows) >= 15 else 50.0
    return current_rsi, current_rsi


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
    min_reward_risk_ratio: float = 1.50,
    min_structural_risk_pct: float = 0.35,
    max_structural_risk_pct: float = 0.60,
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
    regime_details = market_regime_details(point_list, avg, minute)
    regime = market_regime(point_list, avg, minute)
    regime_details = {
        **regime_details,
        "executionState": regime,
        "shadowState": str(regime_details["state"]),
        "shadowDiverged": regime != str(regime_details["state"]),
    }
    opening_net_move = _number(regime_details.get("netMovePct"))
    if 9 * 60 + 35 <= minute <= 10 * 60 and regime == "RANGE":
        if opening_net_move >= 0.45:
            regime = "UPTREND"
        elif opening_net_move <= -0.45:
            regime = "DOWNTREND"
        regime_details["executionState"] = regime
    opening_reference = next(
        (
            _number(item.get("price"))
            for item in point_list
            if 9 * 60 + 30 <= _clock_minutes(item.get("time")) <= 9 * 60 + 35
            and _number(item.get("price")) > 0
        ),
        0.0,
    )
    from_open_pct = (
        (current - opening_reference) / opening_reference * 100.0
        if current > 0 and opening_reference > 0
        else 0.0
    )
    vwap_gap_pct = (current - avg) / avg * 100.0 if current > 0 and avg > 0 else 0.0
    opening_trial = 9 * 60 + 35 <= minute <= 10 * 60
    # Opening trades need the same exhaustion check in every profile.  A
    # low-gap reclaim with an already overbought RSI is a chase, while a
    # high-gap fade that is already oversold is a late sell.  QuantBrain also
    # reuses these deterministic features for its learned adjustment below.
    quant_features = _quantbrain_features(point_list) if selected.name == "quantbrain" or opening_trial else {}
    opening_rsi_peak, opening_rsi_trough = _opening_rsi_extremes(point_list) if opening_trial else (50.0, 50.0)
    if opening_trial:
        quant_features["openingRsiPeak"] = round(opening_rsi_peak, 2)
        quant_features["openingRsiTrough"] = round(opening_rsi_trough, 2)
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
    volume_context = _volume_price_context(point_list, current, day_high, day_low)
    volume_adjustment = 0
    if direction == "BUY_FIRST":
        if volume_context["phase"] == "BOTTOM_EXHAUSTION":
            volume_adjustment = 2 if volume_context["tier"] in {"SUPER_5X", "EXTREME_8X"} else 1
        elif volume_context["phase"] == "DOWN_CONTINUATION":
            volume_adjustment = -2
    elif direction == "SELL_FIRST":
        if volume_context["phase"] == "TOP_EXHAUSTION":
            volume_adjustment = 2 if volume_context["tier"] in {"SUPER_5X", "EXTREME_8X"} else 1
        elif volume_context["phase"] == "UP_CONTINUATION":
            volume_adjustment = -2
    impulse_context = _price_impulse_context(point_list, current, day_high, day_low)
    impulse_adjustment = 0
    if direction == "BUY_FIRST":
        if impulse_context["phase"] == "BOTTOM_IMPULSE_EXHAUSTION":
            impulse_adjustment = 1
        elif impulse_context["phase"] == "DOWN_IMPULSE_CONTINUATION":
            impulse_adjustment = -2
    elif direction == "SELL_FIRST":
        if impulse_context["phase"] == "TOP_IMPULSE_EXHAUSTION":
            impulse_adjustment = 1
        elif impulse_context["phase"] == "UP_IMPULSE_CONTINUATION":
            impulse_adjustment = -2
    # Volume and price impulse describe the same market event, so supporting
    # evidence is not double-counted. A continuation warning always wins.
    context_adjustment = (
        min(volume_adjustment, impulse_adjustment)
        if volume_adjustment < 0 or impulse_adjustment < 0
        else max(volume_adjustment, impulse_adjustment)
    )
    effective_score = max(0, raw_score + quant_adjustment + context_adjustment)
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
    risk_cap = max(risk_floor, _number(max_structural_risk_pct, 0.60))
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
    required_reward_risk = max(0.0, _number(min_reward_risk_ratio, 1.50))
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
    elif opening_trial and auction_confirmed and direction != auction_preference:
        expected = "正T先买后卖" if auction_preference == "BUY_FIRST" else "反T先卖后买"
        state, reason = "AUCTION_DIRECTION_BLOCKED", f"集合竞价与09:35走势已确认{expected}，拦截相反方向。"
    elif (
        opening_trial
        and direction == "BUY_FIRST"
        and opening_rsi_peak >= 85
    ):
        state, reason = "OPENING_EXHAUSTION_BLOCKED", "低开修复已进入短线过热区，放弃追高正T，等待新的回踩结构。"
    elif (
        opening_trial
        and direction == "SELL_FIRST"
        and opening_rsi_trough <= 15
    ):
        state, reason = "OPENING_EXHAUSTION_BLOCKED", "高开回落已进入短线超卖区，放弃低位反T，避免在缺口基本释放后卖出。"
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
    elif (
        direction == "BUY_FIRST" and volume_context["phase"] == "DOWN_CONTINUATION"
    ) or (
        direction == "SELL_FIRST" and volume_context["phase"] == "UP_CONTINUATION"
    ):
        state, reason = "VOLUME_CONTINUATION_BLOCKED", f"{volume_context['label']}，巨量仍在推动原方向，等待量价衰竭后再考虑逆向做T。"
    elif (
        direction == "BUY_FIRST" and impulse_context["phase"] == "DOWN_IMPULSE_CONTINUATION"
    ) or (
        direction == "SELL_FIRST" and impulse_context["phase"] == "UP_IMPULSE_CONTINUATION"
    ):
        state, reason = "IMPULSE_CONTINUATION_BLOCKED", f"{impulse_context['label']}，等待价格停止创新高或新低后再考虑逆向做T。"
    elif effective_score < required_score:
        suffix = f"；多因子修正{quant_adjustment:+d}" if selected.name == "quantbrain" else ""
        state, reason = "SCORE_BLOCKED", f"信号{effective_score}分，未达到{selected.label}档{selected.confirmed_score}分门槛{suffix}。"
    elif regime == "OBSERVE":
        state, reason = "REGIME_OBSERVE", "已完成5分钟K线不足，暂不判断趋势。"
    elif opening_trial and regime == "RANGE":
        state, reason = "OPENING_RANGE_BLOCKED", "开盘方向仍处于震荡，等待形成明确的五分钟趋势后再执行。"
    elif (
        opening_trial
        and direction == "BUY_FIRST"
        and _number(regime_details.get("netMovePct")) < 0.45
    ) or (
        opening_trial
        and direction == "SELL_FIRST"
        and _number(regime_details.get("netMovePct")) > -0.45
    ):
        state, reason = "OPENING_FOLLOW_THROUGH_BLOCKED", "开盘方向虽已形成，但前三个五分钟段的净推进不足0.45%，暂不支付试探仓的固定交易成本。"
    elif regime == "UPTREND" and direction != "BUY_FIRST":
        state, reason = "TREND_BLOCKED", "上涨趋势只允许回踩正T，不逆势先卖。"
    elif regime == "DOWNTREND" and direction != "SELL_FIRST":
        state, reason = "TREND_BLOCKED", "弱势趋势只允许冲高反T，不逆势加仓。"
    elif (
        minute > 10 * 60
        and direction == "SELL_FIRST"
        and opening_reference > 0
        and from_open_pct >= 3.0
        and vwap_gap_pct >= 0.5
    ):
        state, reason = "STRONG_RECOVERY_BLOCKED", "价格较开盘仍上涨3%以上且位于VWAP上方，强势修复尚未真正转弱，暂缓反T避免卖飞。"
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

    signal_margin = effective_score - required_score
    if confirmed:
        if signal_margin >= 2 or context_adjustment >= 2:
            signal_strength, recommended_fraction = "EXTREME", 0.33
        elif signal_margin >= 1 or context_adjustment >= 1:
            signal_strength, recommended_fraction = "STRONG", 0.20
        else:
            signal_strength, recommended_fraction = "NORMAL", 0.10
    else:
        signal_strength, recommended_fraction = "WAIT", 0.0

    return {
        "profile": asdict(selected),
        "regime": regime,
        "regimeDetails": regime_details,
        "state": state,
        "direction": direction,
        "auctionDirection": auction_preference,
        "auctionState": auction_gate_state,
        "openingReferencePrice": round(opening_reference, 4) if opening_reference > 0 else None,
        "fromOpenPct": round(from_open_pct, 3) if opening_reference > 0 else None,
        "vwapGapPct": round(vwap_gap_pct, 3) if avg > 0 else None,
        "openingTrial": opening_trial and auction_confirmed,
        "positionFraction": (1.0 / 6.0) if opening_trial and auction_confirmed else 1.0,
        "signalStrength": signal_strength,
        "recommendedBasePositionFraction": recommended_fraction,
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
        "volumeContext": volume_context,
        "volumeScoreAdjustment": volume_adjustment,
        "priceImpulseContext": impulse_context,
        "priceImpulseScoreAdjustment": impulse_adjustment,
        "contextScoreAdjustment": context_adjustment,
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
