from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.complete_integration import build_complete_payload

rng = np.random.default_rng(7)
dates = pd.date_range("2025-12-01", periods=160, freq="B")
close = 25 + np.cumsum(rng.normal(0.04, 0.35, len(dates)))
daily = pd.DataFrame({
    "open": close + rng.normal(0, .15, len(dates)),
    "high": close + rng.uniform(.1, .5, len(dates)),
    "low": close - rng.uniform(.1, .5, len(dates)),
    "close": close,
    "volume": rng.integers(1_000_000, 8_000_000, len(dates)),
}, index=dates)
previous_close = float(daily["close"].iloc[-1])

aidx = pd.date_range("2026-07-10 09:15:00", periods=11, freq="1min")
auction = pd.DataFrame({
    "virtual_price": np.linspace(previous_close*1.009, previous_close*1.006, len(aidx)),
    "matched_volume": np.linspace(200_000, 1_300_000, len(aidx)),
    "unmatched_buy": np.linspace(700_000, 350_000, len(aidx)),
    "unmatched_sell": np.linspace(300_000, 950_000, len(aidx)),
}, index=aidx)

midx = pd.date_range("2026-07-10 09:30:00", periods=35, freq="1min")
base = previous_close*1.006 + np.cumsum(rng.normal(-0.002, 0.015, len(midx)))
minute = pd.DataFrame({
    "open": base + rng.normal(0, .01, len(midx)),
    "high": base + rng.uniform(.01, .04, len(midx)),
    "low": base - rng.uniform(.01, .04, len(midx)),
    "close": base,
    "volume": rng.integers(50_000, 400_000, len(midx)),
}, index=midx)

signals, trades, payload = build_complete_payload(
    symbol="601899",
    daily_df=daily,
    minute_df=minute,
    previous_close=previous_close,
    auction_df=auction,
    benchmark_auction={"change_pct": 0.001},
    sector_auction={"change_pct": -0.002},
    avg_auction_volume_20d=800_000,
)
print(payload["auction_radar"]["prediction"])
print(payload["summary"])
