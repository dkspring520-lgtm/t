from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.adaptive import (
    AdaptiveLearningService,
    LearningConfig,
    run_adaptive_smart_t,
)


def make_demo_day(seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2026-07-10 09:30", periods=240, freq="min")
    close = 28 + np.cumsum(rng.normal(0, 0.025, len(index)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.005, 0.04, len(index))
    low = np.minimum(open_, close) - rng.uniform(0.005, 0.04, len(index))
    volume = rng.integers(40_000, 300_000, len(index))
    return pd.DataFrame({"open":open_,"high":high,"low":low,"close":close,"volume":volume}, index=index)


if __name__ == "__main__":
    db = ROOT / "data" / "demo_learning.sqlite3"
    if db.exists():
        db.unlink()
    learning = AdaptiveLearningService(
        LearningConfig(database_path=str(db), mode="manual", min_labeled_signals=20, min_trading_days=1)
    )
    bars = make_demo_day()
    signals, trades, payload = run_adaptive_smart_t("601899", bars, learning)
    print("页面数据:", payload["learning"])
    print("收盘标注:", learning.end_of_day("601899", bars))
    print("学习库统计:", learning.store.counts())
