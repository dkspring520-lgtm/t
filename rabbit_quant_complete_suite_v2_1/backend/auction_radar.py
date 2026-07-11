from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional

import numpy as np
import pandas as pd


class AuctionDataError(ValueError):
    pass


@dataclass(frozen=True)
class AuctionRadarConfig:
    high_gap_rate: float = 0.003
    low_gap_rate: float = -0.003
    strong_gap_rate: float = 0.012
    tail_start: str = "09:20"
    tail_end: str = "09:25"
    validation_end: str = "09:35"
    directional_threshold: float = 60.0
    uncertain_band: float = 7.0
    min_snapshots: int = 3
    min_validation_bars: int = 3


def _clip(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))


def _confidence_label(value: float) -> str:
    if value >= 0.72:
        return "较高"
    if value >= 0.52:
        return "中等"
    return "偏低"


def _prepare_auction(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None:
        return None
    if not isinstance(df, pd.DataFrame):
        raise AuctionDataError("auction_df 必须是 pandas.DataFrame")
    if df.empty:
        return df.copy()
    if "virtual_price" not in df.columns:
        raise AuctionDataError("auction_df 缺少 virtual_price 字段")
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    for col in ["virtual_price", "matched_volume", "unmatched_buy", "unmatched_sell"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["virtual_price"])
    return out


def _prepare_intraday(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None:
        return None
    if not isinstance(df, pd.DataFrame):
        raise AuctionDataError("intraday_df 必须是 pandas.DataFrame")
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise AuctionDataError(f"intraday_df 缺少字段：{', '.join(missing)}")
    out = df[required].copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=required)


def _last_change(payload: Optional[Mapping[str, Any]]) -> float:
    if not payload:
        return 0.0
    for key in ("change_pct", "gap_rate", "return_rate"):
        if key in payload and payload[key] is not None:
            value = float(payload[key])
            return value / 100.0 if abs(value) > 1.5 else value
    return 0.0


def _daily_levels(daily_df: Optional[pd.DataFrame], latest_price: float) -> Dict[str, float]:
    if daily_df is None or daily_df.empty or "close" not in daily_df.columns:
        return {"resistance": latest_price * 1.03, "support": latest_price * 0.97, "atr": latest_price * 0.02, "yesterday_return": 0.0}
    daily = daily_df.copy()
    if not isinstance(daily.index, pd.DatetimeIndex):
        daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()
    close = pd.to_numeric(daily["close"], errors="coerce")
    high = pd.to_numeric(daily.get("high", close), errors="coerce")
    low = pd.to_numeric(daily.get("low", close), errors="coerce")
    prev = close.shift(1)
    tr = pd.concat([(high-low), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14, min_periods=3).mean().iloc[-1]) if len(tr.dropna()) else latest_price * 0.02
    resistance = float(high.tail(20).max()) if len(high.dropna()) else latest_price * 1.03
    support = float(low.tail(20).min()) if len(low.dropna()) else latest_price * 0.97
    yesterday_return = float(close.pct_change().iloc[-1]) if len(close.dropna()) >= 2 else 0.0
    return {"resistance": resistance, "support": support, "atr": max(atr, latest_price * 0.002), "yesterday_return": yesterday_return}


def _prediction_label(gap_type: str, direction_score: float, threshold: float, band: float) -> tuple[str, str]:
    upper = threshold
    lower = 100.0 - threshold
    if gap_type == "HIGH_OPEN":
        if direction_score >= upper:
            return "高开高走倾向", "HIGH_OPEN_CONTINUE"
        if direction_score <= lower:
            return "高开低走倾向", "HIGH_OPEN_FADE"
        return "高开震荡", "HIGH_OPEN_RANGE"
    if gap_type == "LOW_OPEN":
        if direction_score >= upper:
            return "低开高走倾向", "LOW_OPEN_RECOVERY"
        if direction_score <= lower:
            return "低开低走倾向", "LOW_OPEN_CONTINUE"
        return "低开震荡", "LOW_OPEN_RANGE"
    if direction_score >= 50 + band:
        return "平开偏强", "FLAT_STRONG"
    if direction_score <= 50 - band:
        return "平开偏弱", "FLAT_WEAK"
    return "平开震荡", "FLAT_RANGE"


def _validate_open(intraday_df: Optional[pd.DataFrame], predicted_code: str, cfg: AuctionRadarConfig) -> Dict[str, Any]:
    intra = _prepare_intraday(intraday_df)
    if intra is None or intra.empty:
        return {"available": False, "status": "WAIT", "label": "等待09:35验证", "confirmation_count": 0, "checks": {}}
    first_day = intra.index[-1].date()
    bars = intra[intra.index.date == first_day]
    bars = bars.between_time("09:30", cfg.validation_end)
    if len(bars) < cfg.min_validation_bars:
        return {"available": False, "status": "WAIT", "label": "开盘K线不足", "confirmation_count": 0, "checks": {}}

    open_price = float(bars.iloc[0]["open"])
    last = float(bars.iloc[-1]["close"])
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    vol_sum = bars["volume"].cumsum().replace(0, np.nan)
    vwap = (typical.mul(bars["volume"]).cumsum() / vol_sum).iloc[-1]
    vwap = float(vwap) if pd.notna(vwap) else open_price
    first_high = float(bars.iloc[0]["high"])
    first_low = float(bars.iloc[0]["low"])
    highs_lowering = bool(len(bars) >= 3 and bars["high"].iloc[-1] < bars["high"].iloc[-2] < bars["high"].iloc[-3])
    lows_rising = bool(len(bars) >= 3 and bars["low"].iloc[-1] > bars["low"].iloc[-2] > bars["low"].iloc[-3])

    fade_checks = {
        "below_open": last < open_price,
        "below_vwap": last < vwap,
        "below_first_low": last < first_low,
        "lowering_highs": highs_lowering,
    }
    recovery_checks = {
        "above_open": last > open_price,
        "above_vwap": last > vwap,
        "above_first_high": last > first_high,
        "rising_lows": lows_rising,
    }

    expect_down = predicted_code in {"HIGH_OPEN_FADE", "LOW_OPEN_CONTINUE", "FLAT_WEAK"}
    expect_up = predicted_code in {"LOW_OPEN_RECOVERY", "HIGH_OPEN_CONTINUE", "FLAT_STRONG"}
    checks = fade_checks if expect_down else recovery_checks
    count = sum(bool(v) for v in checks.values())
    if not (expect_down or expect_up):
        status = "NEUTRAL"
        label = "维持震荡观察"
    elif count >= 2:
        status = "CONFIRMED"
        label = "开盘走势已确认"
    elif count == 0:
        status = "REJECTED"
        label = "开盘走势与预判相反"
    else:
        status = "PENDING"
        label = "部分符合，继续观察"
    return {
        "available": True,
        "status": status,
        "label": label,
        "confirmation_count": count,
        "checks": checks,
        "open_price": round(open_price, 4),
        "last_price": round(last, 4),
        "vwap": round(vwap, 4),
        "bar_count": int(len(bars)),
    }


def calculate_auction_radar(
    previous_close: float,
    auction_df: pd.DataFrame,
    intraday_df: Optional[pd.DataFrame] = None,
    daily_df: Optional[pd.DataFrame] = None,
    benchmark_auction: Optional[Mapping[str, Any]] = None,
    sector_auction: Optional[Mapping[str, Any]] = None,
    avg_auction_volume_20d: Optional[float] = None,
    config: Optional[AuctionRadarConfig] = None,
) -> Dict[str, Any]:
    """集合竞价预判 + 09:35验证。

    auction_df 索引建议覆盖09:15—09:25，至少包含 virtual_price。
    可选列：matched_volume、unmatched_buy、unmatched_sell。
    所有计算只使用传入时点之前的数据，不读取未来K线。
    """
    cfg = config or AuctionRadarConfig()
    if previous_close <= 0:
        raise AuctionDataError("previous_close 必须大于0")
    auction = _prepare_auction(auction_df)
    if auction is None or auction.empty:
        return {"available": False, "reason": "缺少集合竞价数据", "config": asdict(cfg)}
    # 即使调用方误传了全天数据，也只能读取 09:25 及以前的竞价快照。
    auction = auction.between_time("09:15", cfg.tail_end)
    if auction.empty:
        return {"available": False, "reason": "09:25前无有效集合竞价数据", "config": asdict(cfg)}

    latest_price = float(auction["virtual_price"].iloc[-1])
    gap_rate = latest_price / float(previous_close) - 1.0
    gap_type = "HIGH_OPEN" if gap_rate >= cfg.high_gap_rate else "LOW_OPEN" if gap_rate <= cfg.low_gap_rate else "FLAT_OPEN"
    core = auction.between_time(cfg.tail_start, cfg.tail_end)
    if len(core) < cfg.min_snapshots:
        core = auction.tail(max(cfg.min_snapshots, min(5, len(auction))))

    first_core = float(core["virtual_price"].iloc[0])
    tail_change = latest_price / first_core - 1.0 if first_core else 0.0
    x = np.arange(len(core), dtype=float)
    if len(core) >= 2:
        slope = float(np.polyfit(x, core["virtual_price"].to_numpy(float), 1)[0] / previous_close)
    else:
        slope = 0.0

    imbalance = 0.0
    has_imbalance = {"unmatched_buy", "unmatched_sell"}.issubset(core.columns)
    if has_imbalance:
        buy = float(core["unmatched_buy"].fillna(0).iloc[-1])
        sell = float(core["unmatched_sell"].fillna(0).iloc[-1])
        imbalance = (buy - sell) / max(buy + sell, 1.0)

    volume_strength = 1.0
    has_volume = "matched_volume" in core.columns and pd.notna(core["matched_volume"].iloc[-1])
    if has_volume and avg_auction_volume_20d and avg_auction_volume_20d > 0:
        volume_strength = float(core["matched_volume"].iloc[-1]) / float(avg_auction_volume_20d)

    benchmark_change = _last_change(benchmark_auction)
    sector_change = _last_change(sector_auction)
    relative_market = gap_rate - 0.45 * benchmark_change - 0.55 * sector_change
    levels = _daily_levels(daily_df, latest_price)
    near_resistance = (levels["resistance"] - latest_price) / levels["atr"] <= 0.35
    near_support = (latest_price - levels["support"]) / levels["atr"] <= 0.35

    direction_score = 50.0
    direction_score += _clip(tail_change / 0.004 * 18.0, -18.0, 18.0)
    direction_score += _clip(slope / 0.0005 * 10.0, -10.0, 10.0)
    direction_score += _clip(imbalance * 15.0, -15.0, 15.0)
    direction_score += _clip((sector_change + benchmark_change) / 0.008 * 8.0, -8.0, 8.0)
    direction_score += _clip(relative_market / 0.015 * 5.0, -5.0, 5.0)

    # 量价一致加分；放量但价格尾段走弱，视为衰竭。
    if has_volume and avg_auction_volume_20d:
        if volume_strength >= 1.5 and tail_change > 0:
            direction_score += 5.0
        elif volume_strength >= 1.5 and tail_change < 0:
            direction_score -= 5.0
    if near_resistance:
        direction_score -= 7.0
    if near_support:
        direction_score += 7.0
    direction_score += _clip(levels["yesterday_return"] / 0.04 * 4.0, -4.0, 4.0)
    direction_score = _clip(direction_score, 0.0, 100.0)

    label, code = _prediction_label(gap_type, direction_score, cfg.directional_threshold, cfg.uncertain_band)
    evidence_count = 2 + int(has_imbalance) + int(has_volume) + int(benchmark_auction is not None) + int(sector_auction is not None) + int(daily_df is not None)
    distance = abs(direction_score - 50.0) / 50.0
    confidence = _clip(0.22 + evidence_count * 0.07 + distance * 0.32, 0.25, 0.90)
    if len(core) < cfg.min_snapshots:
        confidence *= 0.75

    reasons = []
    reasons.append("竞价尾段价格回升" if tail_change > 0.001 else "竞价尾段价格走弱" if tail_change < -0.001 else "竞价尾段变化不大")
    if has_imbalance:
        reasons.append("未匹配买盘占优" if imbalance > 0.12 else "未匹配卖盘占优" if imbalance < -0.12 else "买卖盘较均衡")
    if sector_change > 0.002:
        reasons.append("所属板块竞价偏强")
    elif sector_change < -0.002:
        reasons.append("所属板块竞价偏弱")
    if near_resistance:
        reasons.append("开盘接近日线压力")
    if near_support:
        reasons.append("开盘接近日线支撑")
    if has_volume and avg_auction_volume_20d:
        reasons.append(f"竞价量为20日均值的{volume_strength:.1f}倍")

    validation = _validate_open(intraday_df, code, cfg)
    if code == "HIGH_OPEN_FADE":
        action = "等待冲高回落确认后再考虑反T"
    elif code == "LOW_OPEN_RECOVERY":
        action = "等待止跌并站上VWAP后再考虑正T"
    elif code == "HIGH_OPEN_CONTINUE":
        action = "避免过早卖出，优先等待回踩机会"
    elif code == "LOW_OPEN_CONTINUE":
        action = "禁止下跌途中连续低吸，等待反弹确认"
    else:
        action = "方向不明确，开盘后等待5分钟确认"

    data_level = "完整" if has_imbalance and has_volume and len(core) >= 4 else "基础"
    probability = round(50.0 + abs(direction_score - 50.0), 1)
    return {
        "available": True,
        "version": "2.1.0",
        "stage": "OPEN_VALIDATED" if validation.get("available") else "AUCTION_PREVIEW",
        "data_level": data_level,
        "gap": {
            "type": gap_type,
            "rate": round(gap_rate, 5),
            "percent": round(gap_rate * 100.0, 2),
            "auction_price": round(latest_price, 4),
            "previous_close": round(float(previous_close), 4),
        },
        "prediction": {
            "label": label,
            "code": code,
            "direction_score": round(direction_score, 1),
            "probability": probability,
            "probability_calibrated": False,
            "confidence": round(confidence, 3),
            "confidence_label": _confidence_label(confidence),
        },
        "features": {
            "tail_change": round(tail_change, 6),
            "tail_slope": round(slope, 7),
            "imbalance": round(imbalance, 4) if has_imbalance else None,
            "volume_strength": round(volume_strength, 2) if has_volume and avg_auction_volume_20d else None,
            "benchmark_change": round(benchmark_change, 5),
            "sector_change": round(sector_change, 5),
            "near_resistance": bool(near_resistance),
            "near_support": bool(near_support),
        },
        "reasons": reasons[:4],
        "validation": validation,
        "strategy_action": action,
        "risk_note": "集合竞价只用于制定开盘预案，09:35未确认前不得直接作为买卖指令。",
        "config": asdict(cfg),
    }
