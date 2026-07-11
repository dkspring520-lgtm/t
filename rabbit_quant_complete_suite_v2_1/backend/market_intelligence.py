from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class IntelligenceConfig:
    """兔兔走势研判默认参数。

    这是一套可解释的规则评分模型，不是已经训练完成的机器学习模型。
    任何概率都应视为“条件倾向”，正式上线前必须用用户自己的数据回测校准。
    """

    short_ma: int = 20
    medium_ma: int = 60
    long_ma: int = 120
    atr_window: int = 14
    momentum_short: int = 5
    momentum_medium: int = 20
    momentum_long: int = 60
    relative_strength_window: int = 20
    preopen_min_history: int = 60
    big_trend_min_history: int = 120
    intraday_min_bars: int = 5
    confidence_floor: float = 0.25
    confidence_cap: float = 0.90


@dataclass(frozen=True)
class ForecastProbabilities:
    up: float
    range: float
    down: float

    def as_percent_dict(self) -> Dict[str, float]:
        return {
            "up": round(self.up * 100.0, 1),
            "range": round(self.range * 100.0, 1),
            "down": round(self.down * 100.0, 1),
        }


class DataValidationError(ValueError):
    pass


def _prepare_ohlcv(df: Optional[pd.DataFrame], name: str) -> Optional[pd.DataFrame]:
    if df is None:
        return None
    if not isinstance(df, pd.DataFrame):
        raise DataValidationError(f"{name} 必须是 pandas.DataFrame")
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise DataValidationError(f"{name} 缺少字段：{', '.join(missing)}")
    if df.empty:
        return df.copy()

    out = df.loc[:, REQUIRED_COLUMNS].copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        try:
            out.index = pd.to_datetime(out.index)
        except Exception as exc:
            raise DataValidationError(f"{name} 的索引必须可转换为时间") from exc
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    for col in REQUIRED_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=list(REQUIRED_COLUMNS))
    out = out[(out["high"] >= out["low"]) & (out["volume"] >= 0)]
    return out


def _clip(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))


def _safe_last(series: pd.Series, default: float = 0.0) -> float:
    if series is None or len(series) == 0:
        return default
    value = series.iloc[-1]
    if pd.isna(value):
        return default
    return float(value)


def _pct_change(series: pd.Series, periods: int) -> float:
    if len(series) <= periods:
        return 0.0
    first = float(series.iloc[-periods - 1])
    last = float(series.iloc[-1])
    if first == 0:
        return 0.0
    return last / first - 1.0


def _slope_normalized(series: pd.Series, lookback: int, scale: Optional[float] = None) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < max(3, lookback):
        return 0.0
    y = clean.iloc[-lookback:].to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    slope = np.polyfit(x, y, 1)[0]
    denominator = float(scale if scale not in (None, 0) else max(abs(y[-1]), 1e-9))
    return float(slope / denominator)


def _atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    previous_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / max(window, 1), adjust=False).mean()


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / max(window, 1), adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / max(window, 1), adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _softmax(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    arr = arr - np.max(arr)
    exp_arr = np.exp(arr)
    total = exp_arr.sum()
    if total <= 0 or not np.isfinite(total):
        return np.array([1 / len(values)] * len(values), dtype=float)
    return exp_arr / total


def _probabilities_from_score(score: float, uncertainty: float = 0.0) -> ForecastProbabilities:
    """将0～100的方向分映射为上涨/震荡/下跌概率。

    uncertainty 越高，概率越靠近均匀分布，避免把不完整数据包装成高置信预测。
    """

    centered = (float(score) - 50.0) / 11.5
    range_utility = 1.25 - abs(float(score) - 50.0) / 17.5
    probs = _softmax([centered, range_utility, -centered])
    mix = _clip(uncertainty, 0.0, 0.75)
    probs = probs * (1.0 - mix) + np.array([1 / 3, 1 / 3, 1 / 3]) * mix
    return ForecastProbabilities(up=float(probs[0]), range=float(probs[1]), down=float(probs[2]))


def _direction_label(score: float) -> str:
    if score >= 72:
        return "上涨倾向"
    if score >= 59:
        return "震荡偏强"
    if score > 41:
        return "方向不明"
    if score > 28:
        return "震荡偏弱"
    return "下跌倾向"


def _current_trend_label(score: float) -> str:
    if score >= 75:
        return "上升趋势"
    if score >= 60:
        return "震荡偏强"
    if score > 40:
        return "横盘震荡"
    if score > 25:
        return "震荡偏弱"
    return "下降趋势"


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.72:
        return "较高"
    if confidence >= 0.52:
        return "中等"
    return "偏低"


def _agreement_score(signals: Iterable[int]) -> float:
    values = [int(np.sign(v)) for v in signals if int(np.sign(v)) != 0]
    if not values:
        return 0.0
    return abs(sum(values)) / len(values)


def _latest_ma(close: pd.Series, window: int) -> float:
    if close.empty:
        return 0.0
    return _safe_last(close.rolling(window, min_periods=max(2, min(window, 5))).mean(), _safe_last(close))


def _benchmark_relative_strength(
    stock: pd.DataFrame,
    benchmark: Optional[pd.DataFrame],
    window: int,
) -> float:
    if benchmark is None or benchmark.empty:
        return 0.0
    stock_ret = _pct_change(stock["close"], window)
    bench_ret = _pct_change(benchmark["close"], window)
    return stock_ret - bench_ret


def calculate_big_trend(
    daily_df: pd.DataFrame,
    benchmark_daily: Optional[pd.DataFrame] = None,
    sector_daily: Optional[pd.DataFrame] = None,
    config: Optional[IntelligenceConfig] = None,
) -> Dict[str, Any]:
    """计算当前大趋势与未来5/20/60日方向倾向。

    只使用传入DataFrame末尾之前已经发生的数据，不回填未来结果。
    """

    cfg = config or IntelligenceConfig()
    daily = _prepare_ohlcv(daily_df, "daily_df")
    benchmark = _prepare_ohlcv(benchmark_daily, "benchmark_daily")
    sector = _prepare_ohlcv(sector_daily, "sector_daily")

    if daily is None or daily.empty:
        return {
            "available": False,
            "reason": "缺少日线数据",
            "current": {"label": "数据不足", "score": 50.0},
            "forecasts": {},
            "confidence": 0.0,
            "confidence_label": "偏低",
        }

    close = daily["close"]
    volume = daily["volume"]
    latest_price = float(close.iloc[-1])
    ma20 = _latest_ma(close, cfg.short_ma)
    ma60 = _latest_ma(close, cfg.medium_ma)
    ma120 = _latest_ma(close, cfg.long_ma)
    atr14 = _safe_last(_atr(daily, cfg.atr_window), latest_price * 0.02)
    rsi14 = _safe_last(_rsi(close, 14), 50.0)

    ma20_slope = _slope_normalized(close.rolling(cfg.short_ma).mean(), 8, latest_price)
    ma60_slope = _slope_normalized(close.rolling(cfg.medium_ma).mean(), 12, latest_price)
    ma120_slope = _slope_normalized(close.rolling(cfg.long_ma).mean(), 15, latest_price)

    ret5 = _pct_change(close, cfg.momentum_short)
    ret20 = _pct_change(close, cfg.momentum_medium)
    ret60 = _pct_change(close, cfg.momentum_long)
    vol5 = _safe_last(volume.rolling(5, min_periods=2).mean(), 0.0)
    vol60 = _safe_last(volume.rolling(60, min_periods=10).mean(), max(vol5, 1.0))
    volume_ratio = vol5 / max(vol60, 1e-9)

    rs_market = _benchmark_relative_strength(daily, benchmark, cfg.relative_strength_window)
    rs_sector = _benchmark_relative_strength(daily, sector, cfg.relative_strength_window)

    score = 50.0
    reasons: list[str] = []
    risks: list[str] = []
    components: Dict[str, float] = {}

    structure = 0.0
    if latest_price > ma20:
        structure += 6
    else:
        structure -= 6
    if ma20 > ma60:
        structure += 8
    else:
        structure -= 8
    if ma60 > ma120:
        structure += 6
    else:
        structure -= 6
    if ma20_slope > 0:
        structure += 4
    else:
        structure -= 4
    if ma60_slope > 0:
        structure += 4
    else:
        structure -= 4
    score += structure
    components["trend_structure"] = round(structure, 2)

    momentum = _clip(ret5 * 160, -6, 6) + _clip(ret20 * 90, -9, 9) + _clip(ret60 * 45, -7, 7)
    score += momentum
    components["momentum"] = round(momentum, 2)

    relative = _clip(rs_market * 90, -5, 5) + _clip(rs_sector * 70, -4, 4)
    score += relative
    components["relative_strength"] = round(relative, 2)

    volume_component = 0.0
    if ret20 > 0 and volume_ratio >= 1.05:
        volume_component += min((volume_ratio - 1.0) * 10, 4.0)
    elif ret20 < 0 and volume_ratio >= 1.15:
        volume_component -= min((volume_ratio - 1.0) * 10, 4.0)
    score += volume_component
    components["volume_confirmation"] = round(volume_component, 2)

    overheat = 0.0
    distance_ma20_atr = (latest_price - ma20) / max(atr14, 1e-9)
    if rsi14 >= 75:
        overheat -= 3.0
        risks.append("日线动量偏热")
    if distance_ma20_atr >= 2.5:
        overheat -= 4.0
        risks.append("价格偏离MA20较远")
    if rsi14 <= 25:
        overheat += 2.0
    score += overheat
    components["overheat_adjustment"] = round(overheat, 2)

    score = _clip(score, 0.0, 100.0)

    if latest_price > ma20 > ma60:
        reasons.append("价格位于MA20与MA60上方")
    elif latest_price < ma20 < ma60:
        reasons.append("价格位于MA20与MA60下方")
    else:
        reasons.append("均线结构仍有分歧")
    if rs_market > 0.02:
        reasons.append("近20日强于大盘")
    elif rs_market < -0.02:
        risks.append("近20日弱于大盘")
    if rs_sector > 0.02:
        reasons.append("近20日强于所属板块")
    elif rs_sector < -0.02:
        risks.append("近20日弱于所属板块")

    data_ratio = min(len(daily) / max(cfg.big_trend_min_history, 1), 1.0)
    agreement = _agreement_score(
        [
            1 if latest_price > ma20 else -1,
            1 if ma20 > ma60 else -1,
            1 if ma60 > ma120 else -1,
            1 if ma20_slope > 0 else -1,
            1 if ma60_slope > 0 else -1,
            1 if ret20 > 0 else -1,
        ]
    )
    direction_strength = min(abs(score - 50.0) / 35.0, 1.0)
    confidence = cfg.confidence_floor + 0.28 * data_ratio + 0.22 * agreement + 0.18 * direction_strength
    confidence = _clip(confidence, cfg.confidence_floor, cfg.confidence_cap)

    def horizon_score(horizon: int) -> float:
        if horizon == 5:
            raw = 50 + _clip(ret5 * 190, -18, 18) + _clip(ret20 * 45, -7, 7)
            raw += 5 if latest_price > ma20 else -5
            raw += 3 if ma20_slope > 0 else -3
        elif horizon == 20:
            raw = score
        else:
            raw = 50 + _clip(ret60 * 55, -14, 14)
            raw += 10 if ma60 > ma120 else -10
            raw += 7 if ma60_slope > 0 else -7
            raw += 4 if ma120_slope > 0 else -4
            raw += _clip(rs_market * 60, -4, 4)
        return _clip(raw, 0, 100)

    forecasts: Dict[str, Any] = {}
    for horizon in (5, 20, 60):
        h_score = horizon_score(horizon)
        uncertainty = (1.0 - data_ratio) * 0.45 + (1.0 - agreement) * 0.20
        probs = _probabilities_from_score(h_score, uncertainty)
        forecasts[f"{horizon}d"] = {
            "horizon_days": horizon,
            "score": round(h_score, 1),
            "label": _direction_label(h_score),
            "probabilities": probs.as_percent_dict(),
        }

    recent_low_20 = float(daily["low"].tail(20).min())
    recent_low_60 = float(daily["low"].tail(min(60, len(daily))).min())
    recent_high_20 = float(daily["high"].tail(20).max())
    recent_high_60 = float(daily["high"].tail(min(60, len(daily))).max())
    support = max(min(ma20, latest_price), recent_low_20)
    resistance = min(max(recent_high_20, latest_price), recent_high_60)
    if score >= 55:
        invalidation = max(ma60 if ma60 > 0 else recent_low_20, recent_low_20)
    elif score <= 45:
        invalidation = min(ma60 if ma60 > 0 else recent_high_20, recent_high_20)
    else:
        invalidation = ma60

    return {
        "available": True,
        "as_of": daily.index[-1].isoformat(),
        "latest_price": round(latest_price, 4),
        "current": {
            "score": round(score, 1),
            "label": _current_trend_label(score),
            "trend_strength": round(abs(score - 50.0) * 2.0, 1),
        },
        "forecasts": forecasts,
        "confidence": round(confidence, 3),
        "confidence_label": _confidence_label(confidence),
        "levels": {
            "support_reference": round(float(support), 4),
            "resistance_reference": round(float(resistance), 4),
            "trend_invalidation_reference": round(float(invalidation), 4),
            "atr14": round(float(atr14), 4),
        },
        "metrics": {
            "ma20": round(ma20, 4),
            "ma60": round(ma60, 4),
            "ma120": round(ma120, 4),
            "rsi14": round(rsi14, 1),
            "return_5d": round(ret5 * 100, 2),
            "return_20d": round(ret20 * 100, 2),
            "return_60d": round(ret60 * 100, 2),
            "volume_ratio_5d_to_60d": round(volume_ratio, 3),
            "relative_strength_market_20d": round(rs_market * 100, 2),
            "relative_strength_sector_20d": round(rs_sector * 100, 2),
            "distance_ma20_atr": round(distance_ma20_atr, 3),
        },
        "components": components,
        "reasons": reasons[:4],
        "risks": risks[:4],
        "model_status": "试运行" if len(daily) < 250 else "规则模型",
    }


def _extract_change_pct(payload: Optional[Mapping[str, Any]], key: str = "change_pct") -> Optional[float]:
    if not payload:
        return None
    value = payload.get(key)
    if value is None:
        price = payload.get("price")
        previous_close = payload.get("previous_close")
        if price is not None and previous_close not in (None, 0):
            value = (float(price) / float(previous_close) - 1.0) * 100.0
    if value is None:
        return None
    return float(value)


def calculate_preopen_forecast(
    daily_df: pd.DataFrame,
    benchmark_daily: Optional[pd.DataFrame] = None,
    sector_daily: Optional[pd.DataFrame] = None,
    auction: Optional[Mapping[str, Any]] = None,
    benchmark_auction: Optional[Mapping[str, Any]] = None,
    sector_auction: Optional[Mapping[str, Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
    config: Optional[IntelligenceConfig] = None,
) -> Dict[str, Any]:
    """盘前方向预判。

    auction 可传：change_pct、volume_ratio、order_imbalance(-1~1)。
    若没有竞价数据，返回“盘前初步版”；有竞价数据则返回“集合竞价版”。
    """

    cfg = config or IntelligenceConfig()
    daily = _prepare_ohlcv(daily_df, "daily_df")
    benchmark = _prepare_ohlcv(benchmark_daily, "benchmark_daily")
    sector = _prepare_ohlcv(sector_daily, "sector_daily")
    context = dict(context or {})

    if daily is None or daily.empty:
        return {
            "available": False,
            "reason": "缺少日线数据",
            "label": "无法判断",
            "score": 50.0,
            "confidence": 0.0,
        }

    close = daily["close"]
    latest = float(close.iloc[-1])
    ma20 = _latest_ma(close, 20)
    ret1 = _pct_change(close, 1)
    ret5 = _pct_change(close, 5)
    ret20 = _pct_change(close, 20)
    market_ret5 = _pct_change(benchmark["close"], 5) if benchmark is not None and not benchmark.empty else 0.0
    sector_ret5 = _pct_change(sector["close"], 5) if sector is not None and not sector.empty else 0.0

    score = 50.0
    reasons: list[str] = []
    risks: list[str] = []

    score += _clip(ret1 * 140, -5, 5)
    score += _clip(ret5 * 100, -8, 8)
    score += _clip(ret20 * 35, -5, 5)
    score += 5 if latest > ma20 else -5
    score += _clip((ret5 - market_ret5) * 90, -4, 4)
    score += _clip((ret5 - sector_ret5) * 70, -4, 4)

    overnight_score = float(context.get("overnight_score", 0.0))
    news_risk = float(context.get("news_risk", 0.0))
    market_environment = float(context.get("market_environment_score", 50.0))
    score += _clip(overnight_score, -1, 1) * 4
    score -= max(_clip(news_risk, -1, 1), 0) * 6
    score += _clip((market_environment - 50) / 10, -4, 4)

    auction_change = _extract_change_pct(auction)
    benchmark_change = _extract_change_pct(benchmark_auction)
    sector_change = _extract_change_pct(sector_auction)
    version = "盘前初步版"
    auction_quality = 0.0

    if auction_change is not None:
        version = "集合竞价版"
        score += _clip(auction_change * 8.0, -14, 14)
        volume_ratio = float((auction or {}).get("volume_ratio", 1.0))
        order_imbalance = float((auction or {}).get("order_imbalance", 0.0))
        score += _clip((volume_ratio - 1.0) * 3.5, -3, 5) * (1 if auction_change >= 0 else -1)
        score += _clip(order_imbalance, -1, 1) * 5
        auction_quality = min(max(volume_ratio / 2.0, 0.15), 1.0)
        reasons.append(f"集合竞价涨跌幅 {auction_change:+.2f}%")
    else:
        risks.append("尚未使用完整集合竞价数据")

    if benchmark_change is not None:
        score += _clip(benchmark_change * 3.5, -4, 4)
    if sector_change is not None:
        score += _clip(sector_change * 4.0, -5, 5)

    score = _clip(score, 0, 100)
    data_ratio = min(len(daily) / max(cfg.preopen_min_history, 1), 1.0)
    direction_strength = min(abs(score - 50) / 32, 1.0)
    confidence = cfg.confidence_floor + 0.22 * data_ratio + 0.18 * direction_strength
    if auction_change is not None:
        confidence += 0.18 * auction_quality
    else:
        confidence -= 0.05
    confidence = _clip(confidence, 0.20, 0.82)

    uncertainty = (1 - data_ratio) * 0.35 + (0.18 if auction_change is None else 0.05)
    probs = _probabilities_from_score(score, uncertainty)
    label = "偏强" if score >= 60 else "偏弱" if score <= 40 else "中性"

    if score >= 60:
        strategy_hint = "优先等待回踩确认，不追高"
    elif score <= 40:
        strategy_hint = "优先观察反弹压力，不急于低吸"
    else:
        strategy_hint = "等待开盘后5～10分钟确认"

    if latest > ma20:
        reasons.append("日线仍在MA20上方")
    else:
        risks.append("日线位于MA20下方")
    if ret5 > market_ret5 + 0.02:
        reasons.append("近5日相对大盘较强")
    elif ret5 < market_ret5 - 0.02:
        risks.append("近5日相对大盘偏弱")

    return {
        "available": True,
        "version": version,
        "as_of": daily.index[-1].isoformat(),
        "score": round(score, 1),
        "label": label,
        "probabilities": probs.as_percent_dict(),
        "confidence": round(confidence, 3),
        "confidence_label": _confidence_label(confidence),
        "strategy_hint": strategy_hint,
        "reasons": reasons[:4],
        "risks": risks[:4],
        "auction_used": auction_change is not None,
    }


def _resample_5m(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    return (
        df.resample("5min", label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
    )


def calculate_intraday_outlook(
    intraday_df: pd.DataFrame,
    benchmark_intraday: Optional[pd.DataFrame] = None,
    sector_intraday: Optional[pd.DataFrame] = None,
    preopen_forecast: Optional[Mapping[str, Any]] = None,
    config: Optional[IntelligenceConfig] = None,
) -> Dict[str, Any]:
    """实时盘中走势研判，每次调用只使用当前时刻以前的数据。"""

    cfg = config or IntelligenceConfig()
    intraday = _prepare_ohlcv(intraday_df, "intraday_df")
    benchmark = _prepare_ohlcv(benchmark_intraday, "benchmark_intraday")
    sector = _prepare_ohlcv(sector_intraday, "sector_intraday")

    if intraday is None or intraday.empty or len(intraday) < cfg.intraday_min_bars:
        return {
            "available": False,
            "reason": "盘中数据不足",
            "score": 50.0,
            "label": "等待确认",
            "confidence": 0.0,
        }

    typical = (intraday["high"] + intraday["low"] + intraday["close"]) / 3
    cum_volume = intraday["volume"].cumsum().replace(0, np.nan)
    vwap = (typical * intraday["volume"]).cumsum() / cum_volume
    close = intraday["close"]
    latest = float(close.iloc[-1])
    latest_vwap = _safe_last(vwap, latest)
    ema5 = close.ewm(span=5, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    atr14 = _safe_last(_atr(intraday, 14), latest * 0.002)
    volume_ma20 = intraday["volume"].rolling(20, min_periods=3).mean()
    volume_ratio = float(intraday["volume"].iloc[-1] / max(_safe_last(volume_ma20, 1.0), 1e-9))

    five = _resample_5m(intraday)
    five_close = five["close"] if not five.empty else close
    five_ema5 = five_close.ewm(span=5, adjust=False).mean()
    five_ema20 = five_close.ewm(span=20, adjust=False).mean()
    five_slope = _slope_normalized(five_ema20, min(5, len(five_ema20)), latest)

    day_open = float(intraday["open"].iloc[0])
    day_return = latest / day_open - 1 if day_open else 0.0
    vwap_distance_atr = (latest - latest_vwap) / max(atr14, 1e-9)
    opening_high = float(intraday["high"].iloc[: min(15, len(intraday))].max())
    opening_low = float(intraday["low"].iloc[: min(15, len(intraday))].min())

    score = 50.0
    reasons: list[str] = []
    risks: list[str] = []

    score += _clip(day_return * 180, -13, 13)
    score += _clip(vwap_distance_atr * 5, -10, 10)
    score += 7 if latest > _safe_last(ema20, latest) else -7
    score += 5 if _safe_last(ema5, latest) > _safe_last(ema20, latest) else -5
    score += _clip(five_slope * 4500, -7, 7)

    if latest > opening_high and volume_ratio > 1.1:
        score += 5
        reasons.append("放量突破开盘区间")
    elif latest < opening_low and volume_ratio > 1.1:
        score -= 5
        risks.append("放量跌破开盘区间")

    if benchmark is not None and not benchmark.empty:
        benchmark_return = float(benchmark["close"].iloc[-1] / benchmark["open"].iloc[0] - 1)
        score += _clip((day_return - benchmark_return) * 120, -5, 5)
    if sector is not None and not sector.empty:
        sector_return = float(sector["close"].iloc[-1] / sector["open"].iloc[0] - 1)
        score += _clip((day_return - sector_return) * 100, -4, 4)

    if preopen_forecast and preopen_forecast.get("available"):
        pre_score = float(preopen_forecast.get("score", 50.0))
        score += _clip((pre_score - 50) * 0.10, -4, 4)

    score = _clip(score, 0, 100)
    bars_factor = min(len(intraday) / 30.0, 1.0)
    direction_strength = min(abs(score - 50) / 32, 1.0)
    agreement = _agreement_score(
        [
            1 if latest > latest_vwap else -1,
            1 if _safe_last(ema5) > _safe_last(ema20) else -1,
            1 if five_slope > 0 else -1,
            1 if day_return > 0 else -1,
        ]
    )
    confidence = 0.25 + 0.25 * bars_factor + 0.18 * direction_strength + 0.15 * agreement
    confidence = _clip(confidence, 0.22, 0.88)
    probs = _probabilities_from_score(score, (1 - bars_factor) * 0.38 + (1 - agreement) * 0.12)

    if score >= 80:
        label = "单边上涨倾向"
    elif score >= 62:
        label = "震荡偏强"
    elif score > 38:
        label = "横盘震荡"
    elif score > 20:
        label = "震荡偏弱"
    else:
        label = "单边下跌倾向"

    if latest > latest_vwap:
        reasons.append("价格位于日内VWAP上方")
    else:
        risks.append("价格位于日内VWAP下方")
    if _safe_last(ema5) > _safe_last(ema20):
        reasons.append("短均线结构向上")
    else:
        risks.append("短均线结构偏弱")
    if volume_ratio < 0.55:
        risks.append("当前成交活跃度偏低")

    return {
        "available": True,
        "as_of": intraday.index[-1].isoformat(),
        "score": round(score, 1),
        "label": label,
        "probabilities": probs.as_percent_dict(),
        "confidence": round(confidence, 3),
        "confidence_label": _confidence_label(confidence),
        "metrics": {
            "day_return": round(day_return * 100, 3),
            "vwap": round(latest_vwap, 4),
            "vwap_distance_atr": round(vwap_distance_atr, 3),
            "volume_ratio": round(volume_ratio, 3),
            "ema5": round(_safe_last(ema5), 4),
            "ema20": round(_safe_last(ema20), 4),
            "five_minute_trend_slope": round(five_slope, 6),
        },
        "reasons": reasons[:4],
        "risks": risks[:4],
    }


def combine_strategy_context(
    big_trend: Mapping[str, Any],
    preopen: Optional[Mapping[str, Any]] = None,
    intraday: Optional[Mapping[str, Any]] = None,
    bull_market_score: Optional[float] = None,
) -> Dict[str, Any]:
    """把大趋势、盘前和盘中结果压缩成智能做T上层环境。"""

    big_score = float(big_trend.get("current", {}).get("score", 50.0)) if big_trend.get("available") else 50.0
    pre_score = float(preopen.get("score", 50.0)) if preopen and preopen.get("available") else 50.0
    intra_score = float(intraday.get("score", 50.0)) if intraday and intraday.get("available") else 50.0

    available_scores = [big_score]
    weights = [0.48]
    if preopen and preopen.get("available"):
        available_scores.append(pre_score)
        weights.append(0.17)
    if intraday and intraday.get("available"):
        available_scores.append(intra_score)
        weights.append(0.35)
    total_weight = sum(weights)
    composite = sum(s * w for s, w in zip(available_scores, weights)) / total_weight
    if bull_market_score is not None:
        composite = composite * 0.90 + float(bull_market_score) * 0.10

    signs = [1 if s >= 58 else -1 if s <= 42 else 0 for s in available_scores]
    nonzero = [s for s in signs if s != 0]
    conflict = bool(nonzero and max(nonzero) != min(nonzero))

    if conflict and abs(max(available_scores) - min(available_scores)) >= 25:
        mode = "WAIT"
        label = "方向冲突，暂缓做T"
        action = "等待方向确认"
    elif composite >= 61:
        mode = "BUY_FIRST"
        label = "趋势偏强"
        action = "优先回踩正T，减少过早卖飞"
    elif composite <= 39:
        mode = "SELL_FIRST"
        label = "趋势偏弱"
        action = "优先反弹反T，提高低吸门槛"
    else:
        mode = "RANGE"
        label = "震荡环境"
        action = "按区间高抛低吸，弱信号不操作"

    buy_threshold = 82
    sell_threshold = 82
    if mode == "BUY_FIRST":
        buy_threshold = 79
        sell_threshold = 88
    elif mode == "SELL_FIRST":
        buy_threshold = 89
        sell_threshold = 79
    elif mode == "WAIT":
        buy_threshold = 95
        sell_threshold = 95

    if bull_market_score is not None and bull_market_score >= 85:
        buy_threshold = max(buy_threshold, 88)
        sell_threshold = min(sell_threshold, 82)
        action = "市场偏热，停止追高并强化高位确认"

    return {
        "score": round(_clip(composite, 0, 100), 1),
        "mode": mode,
        "label": label,
        "action": action,
        "conflict": conflict,
        "thresholds": {
            "buy_confirmed_score": buy_threshold,
            "sell_confirmed_score": sell_threshold,
        },
        "inputs": {
            "big_trend_score": round(big_score, 1),
            "preopen_score": round(pre_score, 1) if preopen and preopen.get("available") else None,
            "intraday_score": round(intra_score, 1) if intraday and intraday.get("available") else None,
            "bull_market_score": round(float(bull_market_score), 1) if bull_market_score is not None else None,
        },
    }


def generate_market_intelligence(
    daily_df: pd.DataFrame,
    intraday_df: Optional[pd.DataFrame] = None,
    benchmark_daily: Optional[pd.DataFrame] = None,
    sector_daily: Optional[pd.DataFrame] = None,
    benchmark_intraday: Optional[pd.DataFrame] = None,
    sector_intraday: Optional[pd.DataFrame] = None,
    auction: Optional[Mapping[str, Any]] = None,
    benchmark_auction: Optional[Mapping[str, Any]] = None,
    sector_auction: Optional[Mapping[str, Any]] = None,
    preopen_context: Optional[Mapping[str, Any]] = None,
    bull_market_score: Optional[float] = None,
    config: Optional[IntelligenceConfig] = None,
) -> Dict[str, Any]:
    """一次生成网页所需的完整研判JSON。"""

    cfg = config or IntelligenceConfig()
    big = calculate_big_trend(daily_df, benchmark_daily, sector_daily, cfg)
    pre = calculate_preopen_forecast(
        daily_df,
        benchmark_daily,
        sector_daily,
        auction,
        benchmark_auction,
        sector_auction,
        preopen_context,
        cfg,
    )
    intra = (
        calculate_intraday_outlook(
            intraday_df,
            benchmark_intraday,
            sector_intraday,
            pre,
            cfg,
        )
        if intraday_df is not None
        else {
            "available": False,
            "reason": "尚未传入盘中数据",
            "score": 50.0,
            "label": "等待开盘",
            "confidence": 0.0,
        }
    )
    strategy = combine_strategy_context(big, pre, intra, bull_market_score)

    twenty = big.get("forecasts", {}).get("20d", {}) if big.get("available") else {}
    summary = (
        f"大趋势{big.get('current', {}).get('label', '不明')}，"
        f"未来20日{twenty.get('label', '方向不明')}；"
        f"{strategy.get('action', '等待更多数据')}。"
    )

    return {
        "version": "1.0.0",
        "big_trend": big,
        "preopen": pre,
        "intraday": intra,
        "smart_t_context": strategy,
        "summary": summary,
        "disclaimer": "概率与分数仅表示规则模型下的条件倾向，不构成收益保证；上线前需回测、模拟盘和成本校准。",
        "config": asdict(cfg),
    }
