from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time
from typing import Dict, Iterable, List, Literal, Optional, Tuple

import numpy as np
import pandas as pd


SignalMode = Literal["sell_first", "buy_first"]


@dataclass(frozen=True)
class SignalConfig:
    """1分钟日内超买/超卖信号参数。

    所有阈值都应通过回测按股票流动性、波动率和交易成本校准，
    不建议直接把默认值当作固定盈利参数。
    """

    # 指标参数
    atr_period: int = 14
    rsi_fast_period: int = 6
    rsi_slow_period: int = 14
    kdj_period: int = 9
    range_period: int = 30
    extreme_period: int = 20
    volume_period: int = 20
    ema_fast: int = 5
    ema_mid: int = 13

    # 观察区保留与信号去重
    zone_memory_bars: int = 5
    signal_cooldown_bars: int = 5
    rearm_score: float = 42.0

    # 分级阈值
    watch_score: float = 52.0
    candidate_score: float = 68.0
    confirmed_score: float = 82.0
    strong_trend_extra_score: float = 8.0

    # 最低数据量与价格保护
    min_history_bars: int = 20
    min_body_epsilon: float = 1e-6
    min_range_epsilon: float = 1e-9

    # 做T执行参数：请根据真实费率修改 estimated_cycle_cost_rate
    estimated_cycle_cost_rate: float = 0.0010
    cost_buffer_multiple: float = 3.0
    min_profit_rate: float = 0.0035
    atr_profit_multiple: float = 0.60
    risk_reentry_rate: float = 0.0050
    risk_reentry_atr_multiple: float = 0.80

    # 可选：临近收盘强制恢复底仓/平掉T仓
    force_close_enabled: bool = False
    force_close_time: str = "14:50"


@dataclass
class TradeRecord:
    mode: SignalMode
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    gross_return: float
    estimated_cost_rate: float
    net_return: float
    result: str
    exit_reason: str


_REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}


def _validate_input(df: pd.DataFrame) -> pd.DataFrame:
    missing = _REQUIRED_COLUMNS.difference(df.columns)
    if missing:
        raise ValueError(f"缺少必要列: {sorted(missing)}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("DataFrame 必须使用 DatetimeIndex，且每行代表一根1分钟K线。")

    result = df.copy().sort_index()
    result = result[~result.index.duplicated(keep="last")]
    for col in _REQUIRED_COLUMNS:
        result[col] = pd.to_numeric(result[col], errors="coerce")
    if result[list(_REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError("OHLCV 数据包含无法转换为数字的值或缺失值。")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC 价格必须大于0。")
    if (result["volume"] < 0).any():
        raise ValueError("成交量不能为负数。")

    highest_body = result[["open", "close"]].max(axis=1)
    lowest_body = result[["open", "close"]].min(axis=1)
    invalid_ohlc = (
        (result["high"] < result["low"])
        | (result["high"] < highest_body)
        | (result["low"] > lowest_body)
    )
    if invalid_ohlc.any():
        first_bad = result.index[invalid_ohlc][0]
        raise ValueError(f"OHLC 关系不合法，首个异常时间: {first_bad}")
    return result


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # 连续上涨时 avg_loss=0，应视作100；连续下跌时 avg_gain=0，应视作0。
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
    return rsi


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _linear_score(
    values: pd.Series,
    start: float,
    full: float,
    max_points: float,
) -> pd.Series:
    if full <= start:
        raise ValueError("full 必须大于 start")
    scaled = (values - start) / (full - start)
    return scaled.clip(lower=0.0, upper=1.0) * max_points


def _reverse_linear_score(
    values: pd.Series,
    start: float,
    full: float,
    max_points: float,
) -> pd.Series:
    """数值越低分越高；start 是开始计分值，full 是满分值。"""
    if full >= start:
        raise ValueError("full 必须小于 start")
    scaled = (start - values) / (start - full)
    return scaled.clip(lower=0.0, upper=1.0) * max_points


def _rolling_bool_max(series: pd.Series, window: int) -> pd.Series:
    return series.astype(float).rolling(window, min_periods=1).max().fillna(0).astype(bool)


def _compute_1m_day(day: pd.DataFrame, cfg: SignalConfig) -> pd.DataFrame:
    out = day.copy()
    open_ = out["open"]
    high = out["high"]
    low = out["low"]
    close = out["close"]
    volume = out["volume"]

    typical = (high + low + close) / 3.0
    cumulative_volume = volume.cumsum().replace(0.0, np.nan)
    out["vwap"] = typical.mul(volume).cumsum() / cumulative_volume
    out["atr14"] = _atr(high, low, close, cfg.atr_period)
    out["rsi6"] = _rsi(close, cfg.rsi_fast_period)
    out["rsi14"] = _rsi(close, cfg.rsi_slow_period)

    lowest_n = low.rolling(cfg.kdj_period, min_periods=max(5, cfg.kdj_period // 2)).min()
    highest_n = high.rolling(cfg.kdj_period, min_periods=max(5, cfg.kdj_period // 2)).max()
    denominator = (highest_n - lowest_n).replace(0.0, np.nan)
    rsv = (close - lowest_n) / denominator * 100.0
    out["kdj_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    out["kdj_d"] = out["kdj_k"].ewm(alpha=1 / 3, adjust=False).mean()
    out["kdj_j"] = 3.0 * out["kdj_k"] - 2.0 * out["kdj_d"]

    out["ema5"] = close.ewm(span=cfg.ema_fast, adjust=False).mean()
    out["ema13"] = close.ewm(span=cfg.ema_mid, adjust=False).mean()

    out["rolling_high_20"] = high.rolling(cfg.extreme_period, min_periods=10).max()
    out["rolling_low_20"] = low.rolling(cfg.extreme_period, min_periods=10).min()
    out["rolling_high_30"] = high.rolling(cfg.range_period, min_periods=10).max()
    out["rolling_low_30"] = low.rolling(cfg.range_period, min_periods=10).min()

    price_range = (out["rolling_high_30"] - out["rolling_low_30"]).replace(
        0.0, np.nan
    )
    out["price_position"] = ((close - out["rolling_low_30"]) / price_range).clip(0.0, 1.0)
    out["vwap_atr_z"] = (close - out["vwap"]) / out["atr14"].replace(0.0, np.nan)

    volume_ma = volume.rolling(cfg.volume_period, min_periods=5).mean()
    out["volume_ratio"] = volume / volume_ma.replace(0.0, np.nan)

    body = (close - open_).abs()
    safe_body = body.clip(lower=cfg.min_body_epsilon)
    bar_range = (high - low).clip(lower=cfg.min_range_epsilon)
    out["upper_shadow"] = high - pd.concat([open_, close], axis=1).max(axis=1)
    out["lower_shadow"] = pd.concat([open_, close], axis=1).min(axis=1) - low
    out["upper_wick_ratio"] = out["upper_shadow"] / safe_body
    out["lower_wick_ratio"] = out["lower_shadow"] / safe_body
    out["close_position_in_bar"] = (close - low) / bar_range

    out["running_day_high"] = high.cummax()
    out["running_day_low"] = low.cummin()
    out["bars_from_open"] = np.arange(len(out), dtype=int)

    # 只使用前一根K线之前的信息判断是否创新高/新低，避免当前值把自己包含进去。
    previous_high_20 = out["rolling_high_20"].shift(1)
    previous_low_20 = out["rolling_low_20"].shift(1)
    out["new_high_20"] = high >= previous_high_20
    out["new_low_20"] = low <= previous_low_20

    out["failed_new_high"] = (
        out["new_high_20"]
        & (out["close_position_in_bar"] <= 0.62)
        & (close < high.shift(1).fillna(high))
    )
    out["failed_new_low"] = (
        out["new_low_20"]
        & (out["close_position_in_bar"] >= 0.38)
        & (close > low.shift(1).fillna(low))
    )

    out["two_lower_highs"] = (high < high.shift(1)) & (high.shift(1) < high.shift(2))
    out["two_higher_lows"] = (low > low.shift(1)) & (low.shift(1) > low.shift(2))
    out["rsi_turn_down"] = (out["rsi6"] < out["rsi6"].shift(1)) & (
        out["rsi6"].shift(1) >= out["rsi6"].shift(2)
    )
    out["rsi_turn_up"] = (out["rsi6"] > out["rsi6"].shift(1)) & (
        out["rsi6"].shift(1) <= out["rsi6"].shift(2)
    )
    out["kdj_turn_down"] = (out["kdj_j"] < out["kdj_j"].shift(1)) & (
        out["kdj_j"].shift(1) >= out["kdj_j"].shift(2)
    )
    out["kdj_turn_up"] = (out["kdj_j"] > out["kdj_j"].shift(1)) & (
        out["kdj_j"].shift(1) <= out["kdj_j"].shift(2)
    )

    return out


def _add_completed_5m_context(day: pd.DataFrame, cfg: SignalConfig) -> pd.DataFrame:
    """只把已经完成的5分钟K线向后映射到1分钟，避免偷看未完成5分钟K线。"""
    out = day.copy()
    bars5 = (
        day[["open", "high", "low", "close", "volume"]]
        .resample(
            "5min",
            label="right",
            closed="right",
            origin="start_day",
            offset="30min",
        )
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
    )

    if bars5.empty:
        for col in [
            "close_5m",
            "vwap_5m",
            "atr_5m",
            "ema8_5m",
            "ema20_5m",
            "ema20_slope_5m",
            "trend_up_5m",
            "trend_down_5m",
            "strong_trend_5m",
        ]:
            out[col] = np.nan if "trend" not in col else False
        return out

    typical5 = (bars5["high"] + bars5["low"] + bars5["close"]) / 3.0
    cum_vol5 = bars5["volume"].cumsum().replace(0.0, np.nan)
    bars5["vwap_5m"] = typical5.mul(bars5["volume"]).cumsum() / cum_vol5
    bars5["atr_5m"] = _atr(
        bars5["high"], bars5["low"], bars5["close"], cfg.atr_period
    )
    bars5["ema8_5m"] = bars5["close"].ewm(span=8, adjust=False).mean()
    bars5["ema20_5m"] = bars5["close"].ewm(span=20, adjust=False).mean()
    bars5["ema20_slope_5m"] = bars5["ema20_5m"].diff()
    bars5["trend_up_5m"] = (
        (bars5["close"] > bars5["vwap_5m"])
        & (bars5["ema8_5m"] > bars5["ema20_5m"])
        & (bars5["ema20_slope_5m"] > 0)
    )
    bars5["trend_down_5m"] = (
        (bars5["close"] < bars5["vwap_5m"])
        & (bars5["ema8_5m"] < bars5["ema20_5m"])
        & (bars5["ema20_slope_5m"] < 0)
    )
    bars5["strong_trend_5m"] = (
        (bars5["ema8_5m"] - bars5["ema20_5m"]).abs()
        / bars5["atr_5m"].replace(0.0, np.nan)
        >= 0.35
    )
    bars5["close_5m"] = bars5["close"]

    columns = [
        "close_5m",
        "vwap_5m",
        "atr_5m",
        "ema8_5m",
        "ema20_5m",
        "ema20_slope_5m",
        "trend_up_5m",
        "trend_down_5m",
        "strong_trend_5m",
    ]
    mapped = bars5[columns].reindex(day.index, method="ffill")
    for col in columns:
        if col in {"trend_up_5m", "trend_down_5m", "strong_trend_5m"}:
            out[col] = mapped[col].fillna(False).astype(bool)
        else:
            out[col] = mapped[col]
    return out


def _score_day(day: pd.DataFrame, cfg: SignalConfig) -> pd.DataFrame:
    out = day.copy()

    # ---------- 顶部：位置与动量（候选区，不直接交易） ----------
    out["top_location_score"] = (
        _linear_score(out["price_position"], 0.72, 0.97, 18.0)
        + _linear_score(out["vwap_atr_z"], 0.45, 1.80, 12.0)
        + out["new_high_20"].astype(float) * 6.0
    )
    out["top_momentum_score"] = (
        _linear_score(out["rsi6"], 67.0, 88.0, 12.0)
        + _linear_score(out["rsi14"], 61.0, 78.0, 6.0)
        + _linear_score(out["kdj_j"], 78.0, 118.0, 8.0)
    )
    out["top_exhaustion_score"] = (
        _linear_score(out["upper_wick_ratio"], 0.8, 2.8, 6.0)
        + out["failed_new_high"].astype(float) * 6.0
        + (
            (out["volume_ratio"] >= 1.25)
            & (out["close_position_in_bar"] <= 0.55)
        ).astype(float)
        * 4.0
    )
    top_context = (
        out["trend_down_5m"].astype(float) * 5.0
        + (~out["trend_up_5m"] & ~out["trend_down_5m"]).astype(float) * 2.0
    )
    out["top_setup_score"] = (
        out["top_location_score"]
        + out["top_momentum_score"]
        + out["top_exhaustion_score"]
        + top_context
    ).clip(0.0, 80.0)

    # ---------- 底部：位置与动量（候选区，不直接交易） ----------
    out["bottom_location_score"] = (
        _reverse_linear_score(out["price_position"], 0.28, 0.03, 18.0)
        + _reverse_linear_score(out["vwap_atr_z"], -0.45, -1.80, 12.0)
        + out["new_low_20"].astype(float) * 6.0
    )
    out["bottom_momentum_score"] = (
        _reverse_linear_score(out["rsi6"], 33.0, 12.0, 12.0)
        + _reverse_linear_score(out["rsi14"], 39.0, 22.0, 6.0)
        + _reverse_linear_score(out["kdj_j"], 22.0, -18.0, 8.0)
    )
    out["bottom_exhaustion_score"] = (
        _linear_score(out["lower_wick_ratio"], 0.8, 2.8, 6.0)
        + out["failed_new_low"].astype(float) * 6.0
        + (
            (out["volume_ratio"] >= 1.25)
            & (out["close_position_in_bar"] >= 0.45)
        ).astype(float)
        * 4.0
    )
    bottom_context = (
        out["trend_up_5m"].astype(float) * 5.0
        + (~out["trend_up_5m"] & ~out["trend_down_5m"]).astype(float) * 2.0
    )
    out["bottom_setup_score"] = (
        out["bottom_location_score"]
        + out["bottom_momentum_score"]
        + out["bottom_exhaustion_score"]
        + bottom_context
    ).clip(0.0, 80.0)

    # 候选区记忆：顶部形成后，等待后续2~5根K线确认反转。
    out["top_zone"] = out["top_setup_score"] >= cfg.watch_score
    out["bottom_zone"] = out["bottom_setup_score"] >= cfg.watch_score
    out["recent_top_zone"] = _rolling_bool_max(out["top_zone"], cfg.zone_memory_bars)
    out["recent_bottom_zone"] = _rolling_bool_max(out["bottom_zone"], cfg.zone_memory_bars)
    out["top_setup_peak"] = out["top_setup_score"].rolling(
        cfg.zone_memory_bars, min_periods=1
    ).max()
    out["bottom_setup_peak"] = out["bottom_setup_score"].rolling(
        cfg.zone_memory_bars, min_periods=1
    ).max()

    # ---------- 反转确认：必须至少出现两类确认 ----------
    top_break_prev_low = out["close"] < out["low"].shift(1)
    top_below_ema = out["close"] < out["ema5"]
    top_reversal_flags = pd.concat(
        [
            top_break_prev_low,
            top_below_ema,
            out["two_lower_highs"],
            out["rsi_turn_down"],
            out["kdj_turn_down"],
            out["failed_new_high"],
        ],
        axis=1,
    ).fillna(False)
    out["top_reversal_count"] = top_reversal_flags.astype(int).sum(axis=1)
    out["top_reversal_score"] = (
        top_break_prev_low.astype(float) * 12.0
        + top_below_ema.astype(float) * 7.0
        + out["two_lower_highs"].astype(float) * 5.0
        + out["rsi_turn_down"].astype(float) * 3.0
        + out["kdj_turn_down"].astype(float) * 3.0
        + out["failed_new_high"].astype(float) * 4.0
    ).clip(0.0, 30.0)

    bottom_break_prev_high = out["close"] > out["high"].shift(1)
    bottom_above_ema = out["close"] > out["ema5"]
    bottom_reversal_flags = pd.concat(
        [
            bottom_break_prev_high,
            bottom_above_ema,
            out["two_higher_lows"],
            out["rsi_turn_up"],
            out["kdj_turn_up"],
            out["failed_new_low"],
        ],
        axis=1,
    ).fillna(False)
    out["bottom_reversal_count"] = bottom_reversal_flags.astype(int).sum(axis=1)
    out["bottom_reversal_score"] = (
        bottom_break_prev_high.astype(float) * 12.0
        + bottom_above_ema.astype(float) * 7.0
        + out["two_higher_lows"].astype(float) * 5.0
        + out["rsi_turn_up"].astype(float) * 3.0
        + out["kdj_turn_up"].astype(float) * 3.0
        + out["failed_new_low"].astype(float) * 4.0
    ).clip(0.0, 30.0)

    out["top_score"] = (out["top_setup_peak"] + out["top_reversal_score"]).clip(0.0, 100.0)
    out["bottom_score"] = (
        out["bottom_setup_peak"] + out["bottom_reversal_score"]
    ).clip(0.0, 100.0)

    # 强趋势中，提高逆势顶部/底部确认门槛。
    out["top_confirm_threshold"] = cfg.confirmed_score + np.where(
        out["trend_up_5m"] & out["strong_trend_5m"],
        cfg.strong_trend_extra_score,
        0.0,
    )
    out["bottom_confirm_threshold"] = cfg.confirmed_score + np.where(
        out["trend_down_5m"] & out["strong_trend_5m"],
        cfg.strong_trend_extra_score,
        0.0,
    )

    enough_history = out["bars_from_open"] >= cfg.min_history_bars
    out["top_confirmed_raw"] = (
        enough_history
        & out["recent_top_zone"]
        & (out["top_reversal_count"] >= 2)
        & (out["top_score"] >= out["top_confirm_threshold"])
    )
    out["bottom_confirmed_raw"] = (
        enough_history
        & out["recent_bottom_zone"]
        & (out["bottom_reversal_count"] >= 2)
        & (out["bottom_score"] >= out["bottom_confirm_threshold"])
    )

    # 参考点和失效点，仅用于界面解释；真正成交价由执行器记录。
    out["top_reference_price"] = out["high"].rolling(
        cfg.zone_memory_bars, min_periods=1
    ).max()
    out["bottom_reference_price"] = out["low"].rolling(
        cfg.zone_memory_bars, min_periods=1
    ).min()
    out["top_invalidation_price"] = out["top_reference_price"] + 0.20 * out["atr14"]
    out["bottom_invalidation_price"] = out["bottom_reference_price"] - 0.20 * out["atr14"]

    # 动态观察线：不要在UI中把它写成“实际成交价”。
    top_atr_factor = np.where(out["trend_up_5m"], 1.35, 1.00)
    bottom_atr_factor = np.where(out["trend_down_5m"], 1.35, 1.00)
    out["sell_watch_line"] = out["vwap"] + top_atr_factor * out["atr14"]
    out["buy_watch_line"] = out["vwap"] - bottom_atr_factor * out["atr14"]

    return out


def _apply_cooldown_day(day: pd.DataFrame, cfg: SignalConfig) -> pd.DataFrame:
    out = day.copy()
    top_trigger = np.zeros(len(out), dtype=bool)
    bottom_trigger = np.zeros(len(out), dtype=bool)

    cooldown = 0
    top_armed = True
    bottom_armed = True
    top_low_score_count = 0
    bottom_low_score_count = 0

    for i, (_, row) in enumerate(out.iterrows()):
        if cooldown > 0:
            cooldown -= 1

        if float(row.get("top_score", 0.0)) < cfg.rearm_score:
            top_low_score_count += 1
        else:
            top_low_score_count = 0
        if float(row.get("bottom_score", 0.0)) < cfg.rearm_score:
            bottom_low_score_count += 1
        else:
            bottom_low_score_count = 0

        if top_low_score_count >= 2:
            top_armed = True
        if bottom_low_score_count >= 2:
            bottom_armed = True

        if cooldown == 0 and top_armed and bool(row["top_confirmed_raw"]):
            top_trigger[i] = True
            top_armed = False
            cooldown = cfg.signal_cooldown_bars
            continue

        if cooldown == 0 and bottom_armed and bool(row["bottom_confirmed_raw"]):
            bottom_trigger[i] = True
            bottom_armed = False
            cooldown = cfg.signal_cooldown_bars

    out["top_trigger"] = top_trigger
    out["bottom_trigger"] = bottom_trigger
    return out


def _assign_ui_fields(day: pd.DataFrame, cfg: SignalConfig) -> pd.DataFrame:
    out = day.copy()

    def level(score: pd.Series, trigger: pd.Series) -> pd.Series:
        values = np.select(
            [
                trigger,
                score >= cfg.candidate_score,
                score >= cfg.watch_score,
            ],
            [3, 2, 1],
            default=0,
        )
        return pd.Series(values, index=score.index, dtype=int)

    out["top_level"] = level(out["top_score"], out["top_trigger"])
    out["bottom_level"] = level(out["bottom_score"], out["bottom_trigger"])

    top_labels = {0: "正常", 1: "高位观察", 2: "疑似超买", 3: "卖出确认"}
    bottom_labels = {0: "正常", 1: "低位观察", 2: "疑似超卖", 3: "回补确认"}
    out["top_label"] = out["top_level"].map(top_labels)
    out["bottom_label"] = out["bottom_level"].map(bottom_labels)

    # 界面只显示一个主标签时，确认信号优先，其次选分数更高的一侧。
    dominant_top = (out["top_level"] > out["bottom_level"]) | (
        (out["top_level"] == out["bottom_level"])
        & (out["top_score"] >= out["bottom_score"])
    )
    out["ui_side"] = np.where(dominant_top, "TOP", "BOTTOM")
    out["ui_label"] = np.where(dominant_top, out["top_label"], out["bottom_label"])
    out["ui_score"] = np.where(dominant_top, out["top_score"], out["bottom_score"])
    out["ui_semantic"] = np.where(
        dominant_top,
        np.where(out["top_level"] == 3, "sell", "warning"),
        np.where(out["bottom_level"] == 3, "buyback", "info"),
    )
    out["ui_reference_price"] = np.where(
        dominant_top, out["top_reference_price"], out["bottom_reference_price"]
    )
    out["ui_invalidation_price"] = np.where(
        dominant_top, out["top_invalidation_price"], out["bottom_invalidation_price"]
    )
    return out


def calculate_intraday_signals(
    df: pd.DataFrame,
    config: Optional[SignalConfig] = None,
) -> pd.DataFrame:
    """计算完整的1分钟超买/超卖信号。

    输入：DatetimeIndex + open/high/low/close/volume。
    输出：原始数据、指标、评分、观察区、确认信号、UI字段。

    关键原则：
    1. top_zone/bottom_zone 只是观察区，不能直接成交；
    2. top_trigger/bottom_trigger 才是经过反转确认和冷却去重后的信号；
    3. 每个交易日独立计算，避免VWAP和滚动窗口跨日污染；
    4. 5分钟趋势只使用已完成K线，避免未来数据泄漏。
    """
    cfg = config or SignalConfig()
    source = _validate_input(df)

    pieces: List[pd.DataFrame] = []
    for _, day in source.groupby(source.index.normalize(), sort=True):
        one = _compute_1m_day(day, cfg)
        one = _add_completed_5m_context(one, cfg)
        one = _score_day(one, cfg)
        one = _apply_cooldown_day(one, cfg)
        one = _assign_ui_fields(one, cfg)
        pieces.append(one)

    result = pd.concat(pieces).sort_index()
    return result


def _parse_clock(value: str) -> time:
    try:
        hour, minute = value.split(":", maxsplit=1)
        return time(int(hour), int(minute))
    except Exception as exc:  # pragma: no cover - defensive validation
        raise ValueError("force_close_time 必须使用 HH:MM 格式") from exc


def simulate_t_cycle(
    signals: pd.DataFrame,
    mode: SignalMode = "sell_first",
    config: Optional[SignalConfig] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """把确认信号转换为完整的做T状态机与模拟成交。

    sell_first：持有底仓 -> 顶部确认卖出 -> 底部确认且价差足够时回补。
    buy_first：有可用资金 -> 底部确认加仓 -> 顶部确认且价差足够时卖出T仓。

    战绩只按完整配对计算：毛收益 - 估算成本。买回/卖出后的即时浮动不会反向修改
    已经完成的一笔T交易结果。
    """
    if mode not in {"sell_first", "buy_first"}:
        raise ValueError("mode 只能是 'sell_first' 或 'buy_first'")
    cfg = config or SignalConfig()
    required = {"top_trigger", "bottom_trigger", "atr14", "close"}
    missing = required.difference(signals.columns)
    if missing:
        raise ValueError(f"signals 缺少列: {sorted(missing)}，请先调用 calculate_intraday_signals")

    out = signals.copy()
    out["trade_state"] = ""
    out["trade_action"] = ""
    out["trade_reason"] = ""
    out["cycle_entry_price"] = np.nan
    out["required_spread_rate"] = np.nan
    out["current_cycle_spread_rate"] = np.nan
    out["cycle_net_return"] = np.nan

    records: List[TradeRecord] = []
    force_time = _parse_clock(cfg.force_close_time)

    # 每天独立运行，避免隔夜状态被误认为同一笔日内T。
    for _, day_index in out.groupby(out.index.normalize(), sort=True).groups.items():
        positions = out.index.get_indexer(day_index)
        state = "HOLDING" if mode == "sell_first" else "READY_BUY"
        entry_time: Optional[pd.Timestamp] = None
        entry_price: Optional[float] = None
        cooldown = 0

        for pos in positions:
            ts = out.index[pos]
            row = out.iloc[pos]
            close = float(row["close"])
            atr = float(row["atr14"]) if pd.notna(row["atr14"]) else 0.0

            if cooldown > 0:
                cooldown -= 1
                if cooldown == 0:
                    state = "HOLDING" if mode == "sell_first" else "READY_BUY"

            out.iat[pos, out.columns.get_loc("trade_state")] = state

            if mode == "sell_first":
                if state == "HOLDING" and bool(row["top_trigger"]):
                    entry_time = ts
                    entry_price = close
                    state = "WAIT_BUYBACK"
                    out.iat[pos, out.columns.get_loc("trade_action")] = "SELL_T"
                    out.iat[pos, out.columns.get_loc("trade_reason")] = "顶部反转确认，卖出T份额"
                    out.iat[pos, out.columns.get_loc("cycle_entry_price")] = entry_price
                    out.iat[pos, out.columns.get_loc("trade_state")] = state
                    continue

                if state == "WAIT_BUYBACK" and entry_price is not None and entry_time is not None:
                    required_spread = max(
                        cfg.min_profit_rate,
                        cfg.estimated_cycle_cost_rate * cfg.cost_buffer_multiple,
                        cfg.atr_profit_multiple * atr / max(close, 1e-12),
                    )
                    spread = (entry_price - close) / entry_price
                    risk_distance = max(
                        cfg.risk_reentry_rate * entry_price,
                        cfg.risk_reentry_atr_multiple * atr,
                    )
                    out.iat[pos, out.columns.get_loc("cycle_entry_price")] = entry_price
                    out.iat[pos, out.columns.get_loc("required_spread_rate")] = required_spread
                    out.iat[pos, out.columns.get_loc("current_cycle_spread_rate")] = spread

                    normal_exit = bool(row["bottom_trigger"]) and spread >= required_spread
                    risk_exit = close >= entry_price + risk_distance
                    time_exit = cfg.force_close_enabled and ts.time() >= force_time

                    if normal_exit or risk_exit or time_exit:
                        gross = (entry_price - close) / entry_price
                        net = gross - cfg.estimated_cycle_cost_rate
                        reason = (
                            "底部反转确认且价差达标"
                            if normal_exit
                            else "价格突破卖出价，触发风控回补"
                            if risk_exit
                            else "临近收盘，恢复底仓"
                        )
                        action = "BUYBACK" if normal_exit else "RISK_BUYBACK"
                        out.iat[pos, out.columns.get_loc("trade_action")] = action
                        out.iat[pos, out.columns.get_loc("trade_reason")] = reason
                        out.iat[pos, out.columns.get_loc("cycle_net_return")] = net
                        records.append(
                            TradeRecord(
                                mode=mode,
                                entry_time=entry_time,
                                exit_time=ts,
                                entry_price=entry_price,
                                exit_price=close,
                                gross_return=gross,
                                estimated_cost_rate=cfg.estimated_cycle_cost_rate,
                                net_return=net,
                                result="WIN" if net > 0 else "LOSS",
                                exit_reason=reason,
                            )
                        )
                        state = "COOLDOWN"
                        cooldown = cfg.signal_cooldown_bars
                        entry_time = None
                        entry_price = None
                        out.iat[pos, out.columns.get_loc("trade_state")] = state

            else:  # buy_first
                if state == "READY_BUY" and bool(row["bottom_trigger"]):
                    entry_time = ts
                    entry_price = close
                    state = "WAIT_SELL"
                    out.iat[pos, out.columns.get_loc("trade_action")] = "BUY_T"
                    out.iat[pos, out.columns.get_loc("trade_reason")] = "底部反转确认，买入T份额"
                    out.iat[pos, out.columns.get_loc("cycle_entry_price")] = entry_price
                    out.iat[pos, out.columns.get_loc("trade_state")] = state
                    continue

                if state == "WAIT_SELL" and entry_price is not None and entry_time is not None:
                    required_spread = max(
                        cfg.min_profit_rate,
                        cfg.estimated_cycle_cost_rate * cfg.cost_buffer_multiple,
                        cfg.atr_profit_multiple * atr / max(close, 1e-12),
                    )
                    spread = (close - entry_price) / entry_price
                    risk_distance = max(
                        cfg.risk_reentry_rate * entry_price,
                        cfg.risk_reentry_atr_multiple * atr,
                    )
                    out.iat[pos, out.columns.get_loc("cycle_entry_price")] = entry_price
                    out.iat[pos, out.columns.get_loc("required_spread_rate")] = required_spread
                    out.iat[pos, out.columns.get_loc("current_cycle_spread_rate")] = spread

                    normal_exit = bool(row["top_trigger"]) and spread >= required_spread
                    risk_exit = close <= entry_price - risk_distance
                    time_exit = cfg.force_close_enabled and ts.time() >= force_time

                    if normal_exit or risk_exit or time_exit:
                        gross = (close - entry_price) / entry_price
                        net = gross - cfg.estimated_cycle_cost_rate
                        reason = (
                            "顶部反转确认且价差达标"
                            if normal_exit
                            else "价格跌破买入价，触发风控卖出"
                            if risk_exit
                            else "临近收盘，卖出T仓"
                        )
                        action = "SELL_T" if normal_exit else "RISK_SELL"
                        out.iat[pos, out.columns.get_loc("trade_action")] = action
                        out.iat[pos, out.columns.get_loc("trade_reason")] = reason
                        out.iat[pos, out.columns.get_loc("cycle_net_return")] = net
                        records.append(
                            TradeRecord(
                                mode=mode,
                                entry_time=entry_time,
                                exit_time=ts,
                                entry_price=entry_price,
                                exit_price=close,
                                gross_return=gross,
                                estimated_cost_rate=cfg.estimated_cycle_cost_rate,
                                net_return=net,
                                result="WIN" if net > 0 else "LOSS",
                                exit_reason=reason,
                            )
                        )
                        state = "COOLDOWN"
                        cooldown = cfg.signal_cooldown_bars
                        entry_time = None
                        entry_price = None
                        out.iat[pos, out.columns.get_loc("trade_state")] = state

    trades = pd.DataFrame([asdict(record) for record in records])
    return out, trades


def latest_ui_payload(signals: pd.DataFrame) -> Dict[str, object]:
    """把最后一根K线转换成适合前端卡片/图标注的字典。"""
    if signals.empty:
        raise ValueError("signals 为空")
    row = signals.iloc[-1]
    side = str(row["ui_side"])

    reasons: List[str] = []
    if side == "TOP":
        if row.get("price_position", 0.0) >= 0.85:
            reasons.append("价格接近30分钟区间上沿")
        if row.get("vwap_atr_z", 0.0) >= 1.0:
            reasons.append("价格显著高于日内VWAP")
        if row.get("rsi6", 0.0) >= 75 or row.get("kdj_j", 0.0) >= 95:
            reasons.append("短周期动量过热")
        if row.get("top_reversal_count", 0) >= 2:
            reasons.append("已出现至少两项回落确认")
    else:
        if row.get("price_position", 1.0) <= 0.15:
            reasons.append("价格接近30分钟区间下沿")
        if row.get("vwap_atr_z", 0.0) <= -1.0:
            reasons.append("价格显著低于日内VWAP")
        if row.get("rsi6", 100.0) <= 25 or row.get("kdj_j", 100.0) <= 5:
            reasons.append("短周期动量过冷")
        if row.get("bottom_reversal_count", 0) >= 2:
            reasons.append("已出现至少两项止跌确认")

    if not reasons:
        reasons.append("尚未形成完整确认条件")

    return {
        "time": signals.index[-1].isoformat(),
        "side": side,
        "label": str(row["ui_label"]),
        "score": round(float(row["ui_score"]), 1),
        "semantic": str(row["ui_semantic"]),
        "reference_price": round(float(row["ui_reference_price"]), 4),
        "invalidation_price": round(float(row["ui_invalidation_price"]), 4)
        if pd.notna(row["ui_invalidation_price"])
        else None,
        "sell_watch_line": round(float(row["sell_watch_line"]), 4)
        if pd.notna(row["sell_watch_line"])
        else None,
        "buy_watch_line": round(float(row["buy_watch_line"]), 4)
        if pd.notna(row["buy_watch_line"])
        else None,
        "reasons": reasons[:3],
        "top_trigger": bool(row["top_trigger"]),
        "bottom_trigger": bool(row["bottom_trigger"]),
    }


__all__ = [
    "SignalConfig",
    "TradeRecord",
    "calculate_intraday_signals",
    "simulate_t_cycle",
    "latest_ui_payload",
]
