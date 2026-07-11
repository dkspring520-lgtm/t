import numpy as np
import pandas as pd

from backend.auction_radar import calculate_auction_radar
from backend.smart_t_controller import SmartTOptions, apply_auction_direction_gate


def make_auction(start, end, buy=300000, sell=900000):
    idx = pd.date_range("2026-07-10 09:15", periods=11, freq="1min")
    return pd.DataFrame({
        "virtual_price": np.linspace(start, end, len(idx)),
        "matched_volume": np.linspace(100000, 1200000, len(idx)),
        "unmatched_buy": np.linspace(500000, buy, len(idx)),
        "unmatched_sell": np.linspace(400000, sell, len(idx)),
    }, index=idx)


def test_high_open_fade():
    out = calculate_auction_radar(10.0, make_auction(10.12, 10.06), benchmark_auction={"change_pct":0}, sector_auction={"change_pct":-0.003}, avg_auction_volume_20d=700000)
    assert out["available"] is True
    assert out["gap"]["type"] == "HIGH_OPEN"
    assert out["prediction"]["code"] in {"HIGH_OPEN_FADE", "HIGH_OPEN_RANGE"}


def test_low_open_recovery():
    out = calculate_auction_radar(10.0, make_auction(9.88, 9.95, buy=1000000, sell=300000), benchmark_auction={"change_pct":0.002}, sector_auction={"change_pct":0.004}, avg_auction_volume_20d=700000)
    assert out["gap"]["type"] == "LOW_OPEN"
    assert out["prediction"]["code"] in {"LOW_OPEN_RECOVERY", "LOW_OPEN_RANGE"}


def test_no_direct_trade_instruction():
    out = calculate_auction_radar(10.0, make_auction(10.10, 10.04))
    assert "等待" in out["strategy_action"] or "避免" in out["strategy_action"] or "禁止" in out["strategy_action"]
    assert "不得直接" in out["risk_note"]


def test_ignores_snapshots_after_0925():
    auction = make_auction(10.12, 10.06)
    auction.loc[pd.Timestamp("2026-07-10 09:26")] = auction.iloc[-1]
    auction.loc[pd.Timestamp("2026-07-10 09:26"), "virtual_price"] = 11.0
    out = calculate_auction_radar(10.0, auction)
    assert out["gap"]["auction_price"] == 10.06
    assert out["prediction"]["probability_calibrated"] is False


def test_auction_bias_waits_until_0935_and_then_confirms_sell_first():
    idx = pd.date_range("2026-07-10 09:30", periods=6, freq="1min")
    bars = pd.DataFrame({
        "open": [10.10, 10.08, 10.06, 10.04, 10.02, 10.00],
        "high": [10.12, 10.10, 10.08, 10.06, 10.04, 10.02],
        "low": [10.06, 10.04, 10.02, 10.00, 9.98, 9.96],
        "close": [10.08, 10.06, 10.04, 10.02, 10.00, 9.98],
        "volume": [100] * 6,
    }, index=idx)
    options = SmartTOptions(auction_bias="SELL_FIRST")
    before, before_state = apply_auction_direction_gate("SELL_FIRST", bars.iloc[3], bars.iloc[:4], options)
    after, after_state = apply_auction_direction_gate("SELL_FIRST", bars.iloc[-1], bars, options)
    assert before is None and before_state == "WAIT_0935"
    assert after == "SELL_FIRST" and after_state == "CONFIRMED"


def test_auction_bias_can_be_invalidated_by_opening_structure():
    idx = pd.date_range("2026-07-10 09:30", periods=6, freq="1min")
    bars = pd.DataFrame({
        "open": [10.00, 10.02, 10.04, 10.06, 10.08, 10.10],
        "high": [10.02, 10.04, 10.06, 10.08, 10.10, 10.12],
        "low": [9.98, 10.00, 10.02, 10.04, 10.06, 10.08],
        "close": [10.01, 10.03, 10.05, 10.07, 10.09, 10.11],
        "volume": [100] * 6,
    }, index=idx)
    chosen, state = apply_auction_direction_gate(
        "BUY_FIRST", bars.iloc[-1], bars, SmartTOptions(auction_bias="SELL_FIRST")
    )
    assert chosen == "BUY_FIRST" and state == "INVALIDATED"
