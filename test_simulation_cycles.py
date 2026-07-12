import unittest
from unittest.mock import patch

import dashboard_app
import simulate_t_random as sim


class SimulationCycleTests(unittest.TestCase):
    def test_reward_floor_matches_entry_structural_risk(self):
        strategy = dict(sim.DEFAULT_STRATEGY)
        strategy["min_reward_risk_ratio"] = 1.25
        strategy["min_structural_risk_pct"] = 0.35
        strategy["max_structural_risk_pct"] = 0.80

        buy_floor = sim._entry_reward_floor_pct(10.0, 9.94, "BUY_FIRST", strategy)
        sell_floor = sim._entry_reward_floor_pct(10.0, 10.06, "SELL_FIRST", strategy)

        self.assertAlmostEqual(buy_floor, 0.75, places=6)
        self.assertAlmostEqual(sell_floor, 0.75, places=6)

    def test_reward_floor_uses_bounded_risk_when_stop_is_missing(self):
        strategy = dict(sim.DEFAULT_STRATEGY)
        strategy["normal_stop_pct"] = -0.60
        strategy["min_reward_risk_ratio"] = 1.25

        floor = sim._entry_reward_floor_pct(10.0, None, "BUY_FIRST", strategy)

        self.assertAlmostEqual(floor, 0.75, places=6)

    def test_time_decay_exit_requires_persistent_wrong_side_of_vwap(self):
        strategy = dict(sim.DEFAULT_STRATEGY)
        bars = [
            sim.Bar("10:13", 9.96, 100.0, 99600.0),
            sim.Bar("10:14", 9.95, 100.0, 99500.0),
            sim.Bar("10:15", 9.94, 100.0, 99400.0),
        ]
        reason = sim._time_decay_exit_reason(
            "BUY_FIRST", bars, 2, [10.0, 9.99, 9.98], 15, -0.60, strategy
        )
        self.assertIn("VWAP", reason)
        self.assertEqual(
            sim._time_decay_exit_reason("BUY_FIRST", bars, 2, [10.0, 9.99, 9.98], 14, -0.60, strategy),
            "",
        )
        self.assertEqual(
            sim._time_decay_exit_reason("BUY_FIRST", bars, 2, [10.0, 9.93, 9.92], 15, -0.60, strategy),
            "",
        )

    def test_reverse_time_decay_exit_is_symmetric(self):
        strategy = dict(sim.DEFAULT_STRATEGY)
        bars = [
            sim.Bar("13:43", 10.04, 100.0, 100400.0),
            sim.Bar("13:44", 10.05, 100.0, 100500.0),
            sim.Bar("13:45", 10.06, 100.0, 100600.0),
        ]
        reason = sim._time_decay_exit_reason(
            "SELL_FIRST", bars, 2, [10.0, 10.01, 10.02], 15, -0.60, strategy
        )
        self.assertIn("VWAP", reason)

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

    def test_profiles_define_two_three_five_and_four_cycle_caps(self):
        limits = {
            name: sim.apply_smart_t_profile(sim.load_adaptive_strategy(), name)["max_daily_cycles"]
            for name in ("steady", "balanced", "sensitive", "quantbrain")
        }
        self.assertEqual(limits, {"steady": 2, "balanced": 3, "sensitive": 5, "quantbrain": 4})

    def test_profiles_preserve_deviation_selectivity_order(self):
        strategy = sim.load_adaptive_strategy()
        profiles = {
            name: sim.apply_smart_t_profile(strategy, name)
            for name in ("steady", "balanced", "sensitive", "quantbrain")
        }
        self.assertLessEqual(profiles["steady"]["buy_min_dev"], profiles["balanced"]["buy_min_dev"])
        self.assertEqual(profiles["balanced"]["buy_min_dev"], profiles["quantbrain"]["buy_min_dev"])
        self.assertGreater(profiles["sensitive"]["buy_min_dev"], profiles["balanced"]["buy_min_dev"])
        self.assertGreaterEqual(profiles["steady"]["sell_min_dev"], profiles["balanced"]["sell_min_dev"])
        self.assertEqual(profiles["balanced"]["sell_min_dev"], profiles["quantbrain"]["sell_min_dev"])
        self.assertLess(profiles["sensitive"]["sell_min_dev"], profiles["balanced"]["sell_min_dev"])

    def test_large_order_reserves_sellable_base_for_four_cycles(self):
        sim.SIM_BASE_SHARES = 6000
        sim.ACTIVE_STRATEGY = sim.apply_smart_t_profile(sim.load_adaptive_strategy(), "quantbrain")
        amounts = []

        def close_cycle(stock, bars, amount, previous_close, entry_after, position, opening_legs_used, planned_trade_amount=None):
            amounts.append(amount)
            position.settle_closed_t(1500)
            return self.result(len(amounts) - 1)

        with patch.object(sim, "_simulate_one_cycle", side_effect=close_cycle):
            result = sim.simulate_one(self.stock, self.bars, 1_000_000.0)
        self.assertEqual(amounts, [15000.0] * 4)
        self.assertEqual(len(result.cycles), 4)

    def test_opening_layer_is_active_only_from_0935_through_1000(self):
        self.assertFalse(sim._is_opening_trade_window("09:34"))
        self.assertTrue(sim._is_opening_trade_window("09:35"))
        self.assertTrue(sim._is_opening_trade_window("10:00"))
        self.assertFalse(sim._is_opening_trade_window("10:01"))

    def test_opening_setup_is_not_blocked_by_generic_intraday_range(self):
        bars = []
        for index in range(11):
            minute = 9 * 60 + 30 + index
            price = 10.0 + (index % 2) * 0.01
            bars.append(sim.Bar(f"{minute // 60:02d}:{minute % 60:02d}", price, 100.0, price * 10000.0, "2026-07-10"))
        sim.ACTIVE_STRATEGY = sim.apply_smart_t_profile(sim.load_adaptive_strategy(), "balanced")
        gate = {"state": "CONFIRMED", "preferredDirection": "BUY_FIRST"}
        with (
            patch.object(sim, "evaluate_auction_gate", return_value=gate),
            patch.object(sim, "_is_opening_buy_setup", return_value=False) as opening_setup,
        ):
            sim._simulate_one_cycle(self.stock, bars, 20000.0, previous_close=10.4)
        self.assertTrue(opening_setup.called)

    def test_small_trade_profit_target_covers_cost_and_slippage(self):
        sim.SIM_COST_MODEL = sim.TradeCostModel()
        floor = sim._minimum_profitable_move_pct(self.stock, 10.0, 1000.0)
        self.assertGreater(floor, 1.0)

    def test_one_lot_never_exceeds_single_round_budget(self):
        self.assertEqual(sim._executable_trade_budget(20000.0, 620.0, 20000.0), 0.0)
        self.assertEqual(sim._executable_trade_budget(20000.0 / 6.0, 79.0, 20000.0), 7900.0)
        position = sim.PositionState(6000)
        result = sim._trade_result(
            self.stock,
            "正T止损",
            sim.Bar("09:45", 620.0, 100.0, 6200000.0, "2026-07-10"),
            sim.Bar("09:50", 610.0, 100.0, 6100000.0, "2026-07-10"),
            20000.0,
            "test",
            position,
        )
        self.assertEqual(result.shares, 0)
        self.assertEqual(position.sellable_shares, 6000)

    def test_existing_cycle_keeps_risk_management_after_entry_cutoff(self):
        bars = []
        for index in range(31):
            minute = 13 * 60 + 40 + index
            price = (9.9 if index % 2 == 0 else 10.0) if index < 8 else 10.0 + (index - 8) * 0.012
            bars.append(sim.Bar(f"{minute // 60:02d}:{minute % 60:02d}", price, 100.0, price * 10000.0, "2026-07-10"))
        sim.ACTIVE_STRATEGY = sim.apply_smart_t_profile(sim.load_adaptive_strategy(), "balanced")
        with (
            patch.object(sim, "evaluate_auction_gate", return_value={"state": "NEUTRAL", "preferredDirection": ""}),
            patch.object(sim, "_is_better_buy_setup", return_value=False),
            patch.object(sim, "_is_better_reverse_t_setup", side_effect=lambda _bars, _idx, bar, *_args: bar.hm == "13:48"),
            patch.object(sim, "_shared_policy_allows", return_value=True),
        ):
            result = sim._simulate_one_cycle(self.stock, bars, 20000.0, previous_close=10.0)
        self.assertIn("止损", result.action)
        self.assertLessEqual(result.buy_time, "14:30")

    def test_low_gap_opening_strategy_reaches_a_real_cycle(self):
        prices = [9.60, 9.52, 9.50, 9.54, 9.58, 9.62, 9.68, 9.74, 9.82, 9.91, 10.02, 10.08]
        prices.extend([10.10 + index * 0.012 for index in range(24)])
        bars = []
        for index, price in enumerate(prices):
            minute = 9 * 60 + 30 + index
            bars.append(sim.Bar(f"{minute // 60:02d}:{minute % 60:02d}", price, 1000.0, price * 100000.0, "2026-07-10"))
        sim.SIM_BASE_SHARES = 6000
        sim.SMART_T_PROFILE = "balanced"
        sim.ACTIVE_STRATEGY = sim.apply_smart_t_profile(sim.load_adaptive_strategy(), "balanced")
        result = sim.simulate_one(self.stock, bars, 20000.0, previous_close=10.0)
        self.assertNotEqual(result.action, "未触发", result.reason)
        self.assertLessEqual(result.entry_time, "10:00")
        self.assertTrue(result.cycles)

    def test_high_gap_opening_strategy_reaches_a_real_cycle(self):
        prices = [10.40, 10.52, 10.60, 10.58, 10.54, 10.48, 10.40, 10.32, 10.24, 10.15, 10.08, 10.00]
        prices.extend([9.98 - index * 0.012 for index in range(24)])
        bars = []
        for index, price in enumerate(prices):
            minute = 9 * 60 + 30 + index
            bars.append(sim.Bar(f"{minute // 60:02d}:{minute % 60:02d}", price, 1000.0, price * 100000.0, "2026-07-10"))
        sim.SIM_BASE_SHARES = 6000
        sim.SMART_T_PROFILE = "balanced"
        sim.ACTIVE_STRATEGY = sim.apply_smart_t_profile(sim.load_adaptive_strategy(), "balanced")
        result = sim.simulate_one(self.stock, bars, 20000.0, previous_close=10.0)
        self.assertNotEqual(result.action, "未触发", result.reason)
        self.assertLessEqual(result.entry_time, "10:00")
        self.assertTrue(result.cycles)

    def test_dashboard_does_not_mistake_cycle_cap_for_stock_cap(self):
        command = dashboard_app.build_commands("simulate", {"sample": 6, "smartTProfile": "steady"})[0]
        index = command.index("--max-trades")
        self.assertEqual(command[index + 1], "6")

    def test_opening_setup_exits_when_vwap_confirmation_fails(self):
        bars = [
            sim.Bar("09:45", 10.00, 100, 100000, "2026-07-10"),
            sim.Bar("09:46", 9.98, 100, 99800, "2026-07-10"),
            sim.Bar("09:47", 9.94, 100, 99400, "2026-07-10"),
        ]
        reason = sim._invalidation_reason(
            direction="BUY_FIRST",
            bars=bars,
            idx=2,
            entry_idx=0,
            avg_prices=[10.01, 10.00, 9.99],
            pnl_pct=-0.6,
            structural_stop=9.90,
            opening_entry=True,
            strategy=sim.DEFAULT_STRATEGY,
        )
        self.assertIn("开盘试探方向失效", reason)

    def test_normal_setup_exits_only_after_structure_and_vwap_both_fail(self):
        bars = [
            sim.Bar("10:10", 10.00, 100, 100000, "2026-07-10"),
            sim.Bar("10:11", 9.98, 100, 99800, "2026-07-10"),
            sim.Bar("10:12", 9.94, 100, 99400, "2026-07-10"),
        ]
        reason = sim._invalidation_reason(
            direction="BUY_FIRST",
            bars=bars,
            idx=2,
            entry_idx=0,
            avg_prices=[10.01, 10.00, 9.99],
            pnl_pct=-0.6,
            structural_stop=9.95,
            opening_entry=False,
            strategy=sim.DEFAULT_STRATEGY,
        )
        self.assertIn("结构与VWAP同时失效", reason)


if __name__ == "__main__":
    unittest.main()
