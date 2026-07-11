import tempfile
import unittest
from pathlib import Path

import dashboard_app
from rabbit_market_radar import calculate_market_radar, update_radar_history


class MarketRadarTests(unittest.TestCase):
    def test_empty_snapshot_is_safe(self):
        result = calculate_market_radar([])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["sampleSize"], 0)

    def test_strong_breadth_scores_above_weak_breadth(self):
        strong = calculate_market_radar([
            {"price": 10, "change": 2.1, "signal": "低位确认", "strictSignal": True},
            {"price": 12, "change": 1.4, "signal": "继续观察"},
            {"price": 8, "change": 0.9, "signal": "继续观察"},
        ])
        weak = calculate_market_radar([
            {"price": 10, "change": -2.1, "signal": "继续观察"},
            {"price": 12, "change": -1.4, "signal": "继续观察"},
            {"price": 8, "change": -0.9, "signal": "继续观察"},
        ])
        self.assertGreater(strong["score"], weak["score"])
        self.assertEqual(len(strong["metrics"]), 3)

    def test_history_deduplicates_same_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "radar.json"
            first = update_radar_history(calculate_market_radar([]), path)
            second = update_radar_history({**first, "score": 50}, path)
            self.assertEqual(len(second["history"]), 1)
            self.assertEqual(second["history"][0]["score"], 50)

    def test_volume_normalizer_handles_cumulative_and_minute_sources(self):
        cumulative = dashboard_app.normalized_market_points([
            {"time": "09:30", "price": 10, "volume_lot": 100, "amount_yuan": 100000},
            {"time": "09:31", "price": 10.1, "volume_lot": 250, "amount_yuan": 251500},
            {"time": "09:32", "price": 10.2, "volume_lot": 450, "amount_yuan": 455500},
        ], {"volume_lot": 450, "amount_wan": 45.55})
        self.assertEqual([row["volumeDelta"] for row in cumulative], [100.0, 150.0, 200.0])
        self.assertEqual(cumulative[-1]["volume"], 450.0)

        minute = dashboard_app.normalized_market_points([
            {"time": "09:30", "price": 10, "volume_lot": 100, "amount_yuan": 100000},
            {"time": "09:31", "price": 10.1, "volume_lot": 150, "amount_yuan": 151500},
            {"time": "09:32", "price": 10.2, "volume_lot": 200, "amount_yuan": 204000},
        ], {"volume_lot": 450, "amount_wan": 45.55})
        self.assertEqual([row["volumeDelta"] for row in minute], [100.0, 150.0, 200.0])
        self.assertEqual(minute[-1]["volume"], 450.0)


if __name__ == "__main__":
    unittest.main()
