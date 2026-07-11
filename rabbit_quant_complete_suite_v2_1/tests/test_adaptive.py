from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.adaptive.guardrails import bounded_update
from backend.adaptive.evaluator import select_signals
from backend.adaptive.labeler import label_pending_signals
from backend.adaptive.models import AdaptiveParams, LearningConfig, SignalObservation
from backend.adaptive.service import AdaptiveLearningService
from backend.adaptive.storage import LearningStore


def make_bars(date="2026-07-10", n=40):
    idx = pd.date_range(f"{date} 09:30", periods=n, freq="min")
    close = np.linspace(10, 10.4, n)
    return pd.DataFrame({
        "open": close - 0.01,
        "high": close + 0.03,
        "low": close - 0.03,
        "close": close,
        "volume": np.full(n, 100000),
    }, index=idx)


def test_guardrail_blocks_hard_risk():
    cfg = LearningConfig()
    with pytest.raises(ValueError):
        bounded_update(AdaptiveParams(), {"daily_loss_limit_rate": 0.1}, cfg)


def test_bounded_update_is_small():
    cfg = LearningConfig(max_score_step=3)
    out = bounded_update(AdaptiveParams(), {"confirmed_score": 20}, cfg)
    assert out.confirmed_score == 85.0


def test_store_and_label(tmp_path):
    db = tmp_path / "learn.sqlite3"
    store = LearningStore(str(db))
    version = store.ensure_initial_champion(AdaptiveParams())
    bars = make_bars()
    ts = bars.index[5]
    store.insert_signal(SignalObservation(
        symbol="TEST", signal_time=ts.isoformat(), trading_date=str(ts.date()),
        direction="BUY_FIRST", regime="UPTREND", score=85, price=float(bars.loc[ts,"close"]),
        volume_ratio=1.2, parameter_version=version,
    ))
    count = label_pending_signals(store, "TEST", bars, LearningConfig(database_path=str(db)))
    assert count == 1
    rows = store.labeled_signals()
    assert "net_return_5m" in rows[0]["outcome"]


def test_service_initializes(tmp_path):
    cfg = LearningConfig(database_path=str(tmp_path / "x.sqlite3"))
    service = AdaptiveLearningService(cfg)
    payload = service.growth_payload()
    assert payload["currentVersion"]
    assert payload["status"] == "积累样本中"


def test_invalid_promote_and_rollback_keep_champion(tmp_path):
    store = LearningStore(str(tmp_path / "safe.sqlite3"))
    champion = store.ensure_initial_champion(AdaptiveParams())
    with pytest.raises(ValueError):
        store.promote("missing-version")
    assert store.get_champion()["version_id"] == champion
    with pytest.raises(ValueError):
        store.rollback_to("missing-version", "test")
    assert store.get_champion()["version_id"] == champion


def test_duplicate_signal_is_not_counted_again(tmp_path):
    store = LearningStore(str(tmp_path / "dedupe.sqlite3"))
    version = store.ensure_initial_champion(AdaptiveParams())
    signal = SignalObservation(
        symbol="TEST", signal_time="2026-07-10T09:31:00", trading_date="2026-07-10",
        direction="BUY_FIRST", regime="RANGE", score=90, price=10,
        volume_ratio=1.2, parameter_version=version,
    )
    assert store.insert_signal(signal) is not None
    assert store.insert_signal(signal) is None


def test_signal_selection_does_not_use_future_mfe():
    base = {
        "signal_id": 1, "symbol": "TEST", "trading_date": "2026-07-10",
        "signal_time": "2026-07-10T09:31:00", "direction": "BUY_FIRST",
        "regime": "RANGE", "score": 90, "volume_ratio": 1.2,
        "outcome": {"net_return_5m": 0.001, "mfe_5m": 0.0},
    }
    selected = select_signals([base], AdaptiveParams(min_expected_net_rate=0.008), 5)
    assert [item[0] for item in selected] == [1]
