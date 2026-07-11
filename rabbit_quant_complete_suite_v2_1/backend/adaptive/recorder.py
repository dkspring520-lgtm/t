from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from .models import SignalObservation, TradeObservation
from .storage import LearningStore

_FEATURE_COLUMNS = (
    "close", "atr14", "vwap", "vwap_atr_z", "rsi6", "rsi14", "kdj_j",
    "price_position", "volume_ratio", "ema5", "ema13", "trend_up_5m",
    "trend_down_5m", "strong_trend_5m", "top_score", "bottom_score",
    "top_trigger", "bottom_trigger", "smart_regime", "smart_trade_action",
)


def _json_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def record_signal_frame(
    store: LearningStore,
    symbol: str,
    signals: pd.DataFrame,
    parameter_version: str,
    include_watch_candidates: bool = True,
) -> int:
    """记录所有候选信号，而不只记录真实成交。

    这是避免幸存者偏差的关键：系统必须知道“没执行的信号后来怎样”。
    """
    if signals.empty:
        return 0
    count = 0
    for ts, row in signals.iterrows():
        top_score = float(row.get("top_score", 0.0) or 0.0)
        bottom_score = float(row.get("bottom_score", 0.0) or 0.0)
        top_candidate = bool(row.get("top_zone", False) or row.get("top_trigger", False))
        bottom_candidate = bool(row.get("bottom_zone", False) or row.get("bottom_trigger", False))
        if include_watch_candidates:
            top_candidate = top_candidate or top_score >= 45.0
            bottom_candidate = bottom_candidate or bottom_score >= 45.0

        features = {
            name: _json_value(row.get(name)) for name in _FEATURE_COLUMNS if name in signals.columns
        }
        action = str(row.get("smart_trade_action", ""))
        regime = str(row.get("smart_regime", "UNKNOWN"))
        price = float(row.get("close"))
        volume_ratio = float(row.get("volume_ratio", 0.0) or 0.0)

        candidates = []
        if top_candidate:
            candidates.append(("SELL_FIRST", top_score, bool(row.get("top_trigger", False))))
        if bottom_candidate:
            candidates.append(("BUY_FIRST", bottom_score, bool(row.get("bottom_trigger", False))))

        for direction, score, confirmed in candidates:
            executed = (
                direction == "SELL_FIRST" and action == "SELL_T"
            ) or (
                direction == "BUY_FIRST" and action == "BUY_T"
            )
            decision = "EXECUTED" if executed else ("CONFIRMED" if confirmed else "WATCH")
            signal = SignalObservation(
                symbol=symbol,
                signal_time=pd.Timestamp(ts).isoformat(),
                trading_date=str(pd.Timestamp(ts).date()),
                direction=direction,
                regime=regime,
                score=float(score),
                price=price,
                volume_ratio=volume_ratio,
                top_score=top_score,
                bottom_score=bottom_score,
                executed=executed,
                decision=decision,
                parameter_version=parameter_version,
                features=features,
            )
            if store.insert_signal(signal) is not None:
                count += 1
    return count


def record_trade_frame(
    store: LearningStore,
    symbol: str,
    trades: pd.DataFrame,
    parameter_version: str,
    regime_lookup: Optional[Mapping[str, str]] = None,
) -> int:
    if trades is None or trades.empty:
        return 0
    count = 0
    for _, row in trades.iterrows():
        entry_time = str(row["entry_time"])
        regime = "UNKNOWN"
        if regime_lookup:
            regime = regime_lookup.get(entry_time, "UNKNOWN")
        observation = TradeObservation(
            symbol=symbol,
            trading_date=str(row.get("date", entry_time[:10])),
            direction=str(row["direction"]),
            entry_time=entry_time,
            exit_time=str(row["exit_time"]),
            entry_price=float(row["entry_price"]),
            exit_price=float(row["exit_price"]),
            net_return=float(row["net_return"]),
            result=str(row["result"]),
            exit_reason=str(row["exit_reason"]),
            parameter_version=parameter_version,
            regime=regime,
            holding_bars=int(row.get("holding_bars", 0)),
        )
        store.insert_trade(observation)
        count += 1
    return count
