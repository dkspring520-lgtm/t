import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from services import paper_observation


class FakeCore:
    def __init__(self, root: str):
        self.BASE_DIR = Path(root)
        self.USER_DATA_DIR = self.BASE_DIR / "user_data"
        (self.BASE_DIR / "services").mkdir(parents=True, exist_ok=True)
        self.USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        for relative in (
            "smart_t_policy.py",
            "auction_direction.py",
            "stock_t_signal.py",
            "services/trade_engine.py",
        ):
            path = self.BASE_DIR / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {relative}\n", encoding="utf-8")

    def user_data_path(self, email, filename):
        safe = str(email).replace("@", "_").replace(".", "_")
        return self.USER_DATA_DIR / f"{safe}_{filename}"

    def account_strategy_path(self, email):
        return self.user_data_path(email, "strategy.json")

    def account_profile_strategy_path(self, email, profile):
        return self.user_data_path(email, f"strategy_{profile}.json")

    @staticmethod
    def _pair_trade_cycles(trades):
        opened = None
        cycles = []
        for trade in trades:
            if opened is None:
                opened = trade
                continue
            if trade.get("side") == opened.get("side"):
                continue
            shares = min(int(opened.get("shares") or 0), int(trade.get("shares") or 0))
            buy = opened if opened.get("side") == "buy" else trade
            sell = opened if opened.get("side") == "sell" else trade
            gross = (float(sell["price"]) - float(buy["price"])) * shares
            fees = float(opened.get("fee") or 0) + float(trade.get("fee") or 0)
            cycles.append({
                "code": trade.get("code") or opened.get("code"),
                "direction": "BUY_FIRST" if opened.get("side") == "buy" else "SELL_FIRST",
                "openTime": opened.get("time"),
                "closeTime": trade.get("time"),
                "shares": shares,
                "buyPrice": float(buy["price"]),
                "sellPrice": float(sell["price"]),
                "gross": gross,
                "fees": fees,
                "net": gross - fees,
            })
            opened = None
        return cycles, [opened] if opened else []


class PaperObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.core = FakeCore(self.temp.name)
        self.email = "tester@example.com"
        self.started = datetime(2026, 7, 13, 9, 30)

    def tearDown(self):
        self.temp.cleanup()

    def test_start_freezes_strategy_and_pauses_training(self):
        with patch.object(paper_observation, "_now", return_value=self.started):
            state = paper_observation.control(self.core, self.email, "start")
        self.assertTrue(state["enabled"])
        self.assertFalse(state["paused"])
        self.assertTrue(state["strategyFingerprint"])
        training = json.loads(self.core.user_data_path(self.email, "four_rabbits_status.json").read_text(encoding="utf-8"))
        self.assertTrue(training["paused"])

    def test_record_only_pairs_post_start_trades_within_same_day(self):
        with patch.object(paper_observation, "_now", return_value=self.started):
            paper_observation.control(self.core, self.email, "start")
        auto_state = {
            "trades": [
                {"date": "2026-07-12", "time": "14:55", "side": "buy", "shares": 100, "price": 8, "fee": 1, "code": "000001"},
                {"date": "2026-07-13", "time": "09:40", "side": "buy", "shares": 100, "price": 10, "fee": 1, "code": "000001"},
                {"date": "2026-07-13", "time": "10:00", "side": "sell", "shares": 100, "price": 11, "fee": 1, "code": "000001"},
            ],
            "decisionAudit": [{"date": "2026-07-13", "time": "09:45", "regime": "RANGE"}],
        }
        with patch.object(paper_observation, "_now", return_value=datetime(2026, 7, 13, 10, 10)):
            paper_observation.record(self.core, self.email, auto_state, [{"price": 11, "quoteStale": False}])
            state = paper_observation.status(self.core, self.email)
        self.assertEqual(state["metrics"]["tradingDays"], 1)
        self.assertEqual(state["metrics"]["completedCycles"], 1)
        self.assertEqual(state["cycles"][0]["date"], "2026-07-13")
        self.assertEqual(state["metrics"]["netPnl"], 98.0)

    def test_strategy_change_marks_sample_contaminated(self):
        with patch.object(paper_observation, "_now", return_value=self.started):
            paper_observation.control(self.core, self.email, "start")
        (self.core.BASE_DIR / "smart_t_policy.py").write_text("# changed\n", encoding="utf-8")
        with patch.object(paper_observation, "_now", return_value=self.started + timedelta(minutes=10)):
            state = paper_observation.status(self.core, self.email)
        self.assertTrue(state["contaminated"])
        self.assertTrue(state["paused"])
        self.assertEqual(state["verdict"], "CONTAMINATED")

    def test_sufficient_profitable_holdout_requires_manual_review(self):
        fingerprint, files = paper_observation._fingerprint(self.core, self.email)
        state = paper_observation._default_state()
        state.update({
            "enabled": True,
            "startedAt": self.started.isoformat(timespec="seconds"),
            "strategyFingerprint": fingerprint,
            "strategyFiles": files,
            "observedDates": [(self.started + timedelta(days=index)).strftime("%Y-%m-%d") for index in range(20)],
            "regimes": ["RANGE", "TREND_UP"],
            "cycles": [{"key": str(index), "net": 10.0 if index < 20 else -5.0} for index in range(30)],
        })
        path = self.core.user_data_path(self.email, "paper_observation.json")
        path.write_text(json.dumps(state), encoding="utf-8")
        observed = paper_observation.status(self.core, self.email)
        self.assertEqual(observed["verdict"], "READY_FOR_REVIEW")
        self.assertIn("人工评审", observed["message"])


if __name__ == "__main__":
    unittest.main()
