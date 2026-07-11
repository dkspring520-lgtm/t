import tempfile
import unittest
from pathlib import Path

from adaptive_profiles import profile_status, promote_profile, record_profile_run


class AdaptiveProfileTests(unittest.TestCase):
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
                {"time": f"09:{30 + index:02d}", "price": 10 + index * 0.03, "volume": (index + 1) * 100}
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


if __name__ == "__main__":
    unittest.main()
