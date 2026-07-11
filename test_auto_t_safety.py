import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import dashboard_app


class AutoTSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_user_data_dir = dashboard_app.USER_DATA_DIR
        dashboard_app.USER_DATA_DIR = Path(self.temp.name)
        self.email = "smart-t-test@example.com"

    def tearDown(self):
        dashboard_app.USER_DATA_DIR = self.original_user_data_dir
        self.temp.cleanup()

    def save_state(self, **updates):
        today = datetime.now().strftime("%Y-%m-%d")
        state = {
            "version": 2,
            "initialCash": 100000.0,
            "cash": 50000.0,
            "positions": {"000001": {"name": "测试股", "shares": 1000, "avgCost": 10.0, "lastPrice": 10.0}},
            "trades": [],
            "marks": [],
            "lastSignalKeys": {},
            "lastTradeTimes": {},
            "dailyTradeDate": today,
            "dailyTradeCount": 0,
            "dailyTradeSides": {},
            "dailyReferencePrices": {},
            "dailyReferenceFees": {},
            "dailyReferenceShares": {},
            "dailyBaseShares": {"000001": 1000},
            "dailyCycleCounts": {},
            "dailyRealizedT": {},
            "totalFees": 0.0,
        }
        state.update(updates)
        dashboard_app._save_auto_t_state(self.email, state)

    @staticmethod
    def row(smart_t, reminder=None):
        return {
            "name": "测试股",
            "code": "000001",
            "time": "14:51",
            "price": 10.5,
            "marketStatus": "交易中",
            "quoteStale": False,
            "smartT": smart_t,
            "intradayReminder": reminder or {},
        }

    def test_force_close_restores_only_the_position_difference(self):
        self.save_state(
            positions={"000001": {"name": "测试股", "shares": 1200, "avgCost": 10.0, "lastPrice": 10.0}},
            dailyTradeSides={"000001": "buy"},
            dailyReferencePrices={"000001": 10.0},
            dailyReferenceFees={"000001": 5.0},
            dailyReferenceShares={"000001": 200},
        )
        rows = [self.row({"confirmed": False, "forceClose": True, "profile": {"cooldown_minutes": 5, "max_daily_cycles": 3}})]
        dashboard_app.auto_t_update(rows, self.email)
        self.assertEqual(rows[0]["paperT"]["shares"], 1000)
        self.assertEqual(rows[0]["paperT"]["trades"][-1]["shares"], 200)
        self.assertEqual(rows[0]["paperT"]["decisionAudit"][-1]["state"], "EXECUTED")
        self.assertTrue(rows[0]["paperT"]["decisionAudit"][-1]["executed"])
        self.assertEqual(rows[0]["paperT"]["restoreShares"], 0)
        self.assertEqual(rows[0]["paperT"]["lifecycle"], "已完成配对")

    def test_daily_loss_lock_blocks_a_new_cycle(self):
        self.save_state(dailyRealizedT={"000001": -301.0})
        rows = [self.row(
            {"confirmed": True, "forceClose": False, "profile": {"cooldown_minutes": 5, "max_daily_cycles": 3}},
            {"actionable": True, "tone": "buy", "label": "低吸确认"},
        )]
        dashboard_app.auto_t_update(rows, self.email)
        dashboard_app.auto_t_update(rows, self.email)
        self.assertEqual(rows[0]["paperT"]["shares"], 1000)
        self.assertEqual(rows[0]["paperT"]["trades"], [])
        self.assertTrue(rows[0]["paperT"]["dailyLossLocked"])
        self.assertEqual(rows[0]["paperT"]["decisionAudit"][-1]["state"], "LOSS_LOCKED")
        review = dashboard_app.smart_t_review_payload(self.email)
        self.assertEqual(review["decisionCount"], 1)
        self.assertEqual(review["blocked"][0], {"state": "LOSS_LOCKED", "count": 1})

    def test_trade_pairing_is_fifo_and_allocates_fees(self):
        cycles, open_lots = dashboard_app._pair_trade_cycles([
            {"code": "000001", "side": "buy", "time": "10:00", "price": 10.0, "shares": 200, "fee": 5.0},
            {"code": "000001", "side": "sell", "time": "10:30", "price": 11.0, "shares": 100, "fee": 5.0},
        ])
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["shares"], 100)
        self.assertEqual(cycles[0]["gross"], 100.0)
        self.assertEqual(cycles[0]["fees"], 7.5)
        self.assertEqual(cycles[0]["net"], 92.5)
        self.assertEqual(open_lots[0]["remaining"], 100)


if __name__ == "__main__":
    unittest.main()
