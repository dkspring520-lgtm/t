from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .models import LearningConfig
from .storage import LearningStore


def _outcome_for_signal(
    bars: pd.DataFrame,
    timestamp: pd.Timestamp,
    direction: str,
    entry_price: float,
    horizons: Sequence[int],
    cost_rate: float,
) -> Optional[Dict[str, float]]:
    if timestamp not in bars.index:
        return None
    position = bars.index.get_loc(timestamp)
    if isinstance(position, slice) or isinstance(position, np.ndarray):
        return None
    outcome: Dict[str, float] = {}
    for horizon in horizons:
        future = bars.iloc[position + 1 : position + 1 + horizon]
        if len(future) < horizon:
            continue
        end_price = float(future["close"].iloc[-1])
        future_high = float(future["high"].max())
        future_low = float(future["low"].min())
        if direction == "BUY_FIRST":
            gross = (end_price - entry_price) / entry_price
            mfe = (future_high - entry_price) / entry_price
            mae = max(0.0, (entry_price - future_low) / entry_price)
        else:
            gross = (entry_price - end_price) / entry_price
            mfe = (entry_price - future_low) / entry_price
            mae = max(0.0, (future_high - entry_price) / entry_price)
        outcome[f"gross_return_{horizon}m"] = gross
        outcome[f"net_return_{horizon}m"] = gross - cost_rate
        outcome[f"mfe_{horizon}m"] = mfe
        outcome[f"mae_{horizon}m"] = mae
    return outcome or None


def label_pending_signals(
    store: LearningStore,
    symbol: str,
    minute_df: pd.DataFrame,
    config: LearningConfig,
) -> int:
    """收盘后为候选信号补上未来结果标签。

    只在未来K线已经真实发生后执行，盘中不会用未来数据修改信号。
    """
    if not isinstance(minute_df.index, pd.DatetimeIndex):
        raise TypeError("minute_df 必须使用 DatetimeIndex")
    bars = minute_df.sort_index()
    pending = store.pending_signals(symbol)
    count = 0
    for item in pending:
        ts = pd.Timestamp(item["signal_time"])
        outcome = _outcome_for_signal(
            bars,
            timestamp=ts,
            direction=str(item["direction"]),
            entry_price=float(item["price"]),
            horizons=config.outcome_horizons,
            cost_rate=config.estimated_cycle_cost_rate,
        )
        if outcome:
            store.save_outcome(int(item["signal_id"]), outcome)
            count += 1
    return count
