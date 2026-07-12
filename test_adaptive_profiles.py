import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_profiles import _bar_frame, profile_status, promote_profile, record_profile_run, runtime_profile_params


class _Payload:
    def to_dict(self):
        return {}


class _CapturingService:
    def __init__(self):
        self.signals = []
        self.trades = []
        self.end_of_day_bars = []

    def record_intraday(self, _symbol, signal, trades):
        self.signals.append(signal.copy())
        self.trades.append(trades.copy())
        return {"signals": len(signal), "trades": len(trades)}

    def end_of_day(self, _symbol, bars):
        self.end_of_day_bars.append(bars.copy())
        return {"labeledSignals": len(bars)}

    def create_weekly_challenger(self):
        return _Payload()

    def review_challenger(self, apply=False):
        return _Payload()

    def growth_payload(self):
        return {}


class AdaptiveProfileTests(unittest.TestCase):
    @staticmethod
    def _bar(date, time, price, volume=100):
        return {
            "date": date,
            "time": time,
            "price": price,
            "volumeDelta": volume,
            "amountDelta": price * volume * 100 if isinstance(price, (int, float)) else 100_000,
            "dataQuality": "full",
        }

    def test_bar_frame_keeps_only_clean_unique_a_share_session_rows(self):
        prices = [
            self._bar("2026-07-10", "13:00", 10.5),
            self._bar("2026-07-09", "09:30", 10.0),
            self._bar("2026-07-09", "09:30", 10.1),
            self._bar("2026-07-09", "12:00", 10.2),
            self._bar("2026-07-09", "15:01", 10.3),
            self._bar("2026-07-09", "10:00", float("nan")),
            self._bar("2026-07-09", "10:01", 10.0, volume=0),
            {**self._bar("2026-07-09", "10:02", 10.0), "amountDelta": 10.0},
        ]

        frame = _bar_frame({"prices": prices})

        self.assertEqual(
            [stamp.strftime("%Y-%m-%d %H:%M") for stamp in frame.index],
            ["2026-07-09 09:30", "2026-07-10 13:00"],
        )
        self.assertEqual(float(frame.iloc[0]["close"]), 10.1)
        self.assertTrue((frame[["close", "volume", "amount"]]> 0).all().all())

    def test_record_run_uses_daily_result_date_and_daily_vwap(self):
        service = _CapturingService()
        prices = [
            self._bar("2026-07-09", "09:30", 10.0),
            self._bar("2026-07-09", "09:31", 10.2),
            self._bar("2026-07-09", "09:32", 10.3),
            self._bar("2026-07-10", "09:30", 20.0),
            self._bar("2026-07-10", "09:31", 20.2),
            self._bar("2026-07-10", "09:32", 20.3),
        ]
        row = {
            "code": "000001",
            "action": "正T止盈",
            "buyTime": "09:31",
            "buyPrice": 10.2,
            "sellTime": "09:32",
            "sellPrice": 10.3,
            "pnl": 0.8,
            "prices": prices,
            "dailyResults": [
                {"date": "2026-07-09", "action": "正T止盈"},
                {"date": "2026-07-10", "action": "未触发"},
            ],
        }

        with tempfile.TemporaryDirectory() as folder, patch("adaptive_profiles._service", return_value=service):
            payload = record_profile_run(Path(folder) / "balanced.sqlite3", "balanced", [row])

        self.assertEqual(payload["recordedSignals"], 1)
        signal = service.signals[0]
        self.assertEqual(signal.index[0].strftime("%Y-%m-%d %H:%M"), "2026-07-09 09:31")
        self.assertAlmostEqual(float(signal.iloc[0]["vwap"]), 10.1, places=6)
        self.assertTrue(all(math.isfinite(float(signal.iloc[0][key])) for key in ("atr14", "vwap", "rsi14", "volume_ratio")))
        self.assertEqual(
            [stamp.strftime("%Y-%m-%d") for stamp in service.end_of_day_bars[0].index.unique()],
            ["2026-07-09", "2026-07-09", "2026-07-09"],
        )
        self.assertEqual(service.trades[0].iloc[0]["date"], "2026-07-09")

    def test_each_cycle_is_bound_to_its_explicit_trade_date(self):
        service = _CapturingService()
        prices = [
            self._bar("2026-07-09", "09:30", 10.0),
            self._bar("2026-07-09", "09:31", 10.2),
            self._bar("2026-07-09", "09:32", 10.3),
            self._bar("2026-07-10", "09:30", 20.0),
            self._bar("2026-07-10", "09:31", 20.2),
            self._bar("2026-07-10", "09:32", 20.3),
        ]
        cycles = [
            {"date": "2026-07-09", "action": "正T止盈", "buyTime": "09:31", "buyPrice": 10.2, "sellTime": "09:32", "sellPrice": 10.3, "pnl": 0.8},
            {"date": "2026-07-10", "action": "正T止盈", "buyTime": "09:31", "buyPrice": 20.2, "sellTime": "09:32", "sellPrice": 20.3, "pnl": 0.3},
        ]
        row = {"code": "000001", "prices": prices, "cycles": cycles}

        with tempfile.TemporaryDirectory() as folder, patch("adaptive_profiles._service", return_value=service):
            payload = record_profile_run(Path(folder) / "balanced.sqlite3", "balanced", [row])

        self.assertEqual(payload["recordedSignals"], 2)
        self.assertEqual(
            [signal.index[0].strftime("%Y-%m-%d") for signal in service.signals],
            ["2026-07-09", "2026-07-10"],
        )
        self.assertAlmostEqual(float(service.signals[0].iloc[0]["vwap"]), 10.1, places=6)
        self.assertAlmostEqual(float(service.signals[1].iloc[0]["vwap"]), 20.1, places=6)

    def test_profiles_have_independent_champions(self):
        with tempfile.TemporaryDirectory() as folder:
            steady = profile_status(Path(folder) / "steady.sqlite3", "steady")
            sensitive = profile_status(Path(folder) / "sensitive.sqlite3", "sensitive")
            self.assertNotEqual(steady["currentVersion"], sensitive["currentVersion"])
            self.assertGreater(steady["championParams"]["confirmed_score"], sensitive["championParams"]["confirmed_score"])
            self.assertTrue(steady["manualPromotionOnly"])

    def test_simulation_is_recorded_but_not_auto_promoted(self):
        with tempfile.TemporaryDirectory() as folder:
            prices = [
                {
                    "time": f"09:{30 + index:02d}",
                    "price": 10 + index * 0.03,
                    "volumeDelta": 100,
                    "amountDelta": (10 + index * 0.03) * 100 * 100,
                    "date": "2026-07-10",
                    "dataQuality": "full",
                }
                for index in range(16)
            ]
            payload = record_profile_run(Path(folder) / "balanced.sqlite3", "balanced", [{
                "code": "000001",
                "action": "正T止盈",
                "buyTime": "09:32",
                "buyPrice": 10.06,
                "sellTime": "09:40",
                "sellPrice": 10.30,
                "pnl": 2.38,
                "reason": "测试样本",
                "prices": prices,
            }])
            self.assertEqual(payload["recordedSignals"], 1)
            self.assertEqual(payload["recordedTrades"], 1)
            self.assertEqual(payload["labeledSignalsThisRun"], 1)
            self.assertTrue(payload["manualPromotionOnly"])
            self.assertFalse(promote_profile(Path(folder) / "balanced.sqlite3", "balanced")["ok"])

    def test_quantbrain_has_an_independent_experience_database(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "quantbrain.sqlite3"
            status = profile_status(database, "quantbrain")
            params = runtime_profile_params(database, "quantbrain")
            self.assertEqual(status["profileLabel"], "量化学习")
            self.assertEqual(params["version_id"], status["currentVersion"])
            self.assertEqual(params["confirmed_score"], 82)


if __name__ == "__main__":
    unittest.main()
