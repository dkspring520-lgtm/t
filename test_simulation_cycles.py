import unittest
from unittest.mock import patch

import dashboard_app
import simulate_t_random as sim


class SimulationCycleTests(unittest.TestCase):
    def setUp(self):
        self.stock = sim.Stock("测试股", "000001", "sz000001")
        self.bars = [sim.Bar("09:30", 10.0, 100.0, 100000.0, "2026-07-10")]

    def result(self, index: int) -> sim.Result:
        start = 10 * 60 + index * 25
        buy = f"{start // 60:02d}:{start % 60:02d}"
        end = start + 15
        sell = f"{end // 60:02d}:{end % 60:02d}"
        return sim.Result(self.stock, "正T止盈", buy, 10.0, sell, 10.1, 1.0, 100.0, 10000.0, 1000, "闭环")

    def test_balanced_profile_can_complete_three_cycles(self):
        sim.ACTIVE_STRATEGY = sim.apply_smart_t_profile(sim.load_adaptive_strategy(), "balanced")
        with patch.object(sim, "_simulate_one_cycle", side_effect=[self.result(0), self.result(1), self.result(2)]) as mocked:
            result = sim.simulate_one(self.stock, self.bars, 10000.0)
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(len(result.cycles), 3)
        self.assertEqual(result.action, "智能做T3轮")
        self.assertEqual(result.pnl_yuan, 300.0)
        self.assertNotEqual(sim.apply_daily_trade_limit([result], 1)[0].action, "未触发")
        self.assertEqual(result.entry_time, "10:00")
        self.assertEqual(result.exit_time, "11:05")

    def test_profiles_define_two_three_five_cycle_caps(self):
        limits = {
            name: sim.apply_smart_t_profile(sim.load_adaptive_strategy(), name)["max_daily_cycles"]
            for name in ("steady", "balanced", "sensitive")
        }
        self.assertEqual(limits, {"steady": 2, "balanced": 3, "sensitive": 5})

    def test_dashboard_does_not_mistake_cycle_cap_for_stock_cap(self):
        command = dashboard_app.build_commands("simulate", {"sample": 6, "smartTProfile": "steady"})[0]
        index = command.index("--max-trades")
        self.assertEqual(command[index + 1], "6")


if __name__ == "__main__":
    unittest.main()
