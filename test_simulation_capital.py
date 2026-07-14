import contextlib
import io
import unittest
from unittest.mock import patch

import app_core
import simulate_t_random as sim


class SimulationCapitalTests(unittest.TestCase):
    def setUp(self):
        self.old_base_amount = sim.SIM_BASE_AMOUNT
        self.old_base_shares = sim.SIM_BASE_SHARES
        self.old_active_position = sim.ACTIVE_POSITION
        sim.SIM_BASE_AMOUNT = 10_000.0
        sim.SIM_BASE_SHARES = 0
        sim.ACTIVE_POSITION = None

    def tearDown(self):
        sim.SIM_BASE_AMOUNT = self.old_base_amount
        sim.SIM_BASE_SHARES = self.old_base_shares
        sim.ACTIVE_POSITION = self.old_active_position

    @staticmethod
    def bars(price: float) -> list[sim.Bar]:
        return [sim.Bar("09:30", price, 100.0, price * 10_000.0, "2026-07-10")]

    def test_each_stock_derives_its_own_exchange_lot_base_position(self):
        low = sim._base_position_allocation(self.bars(10.0), 10.0)
        medium = sim._base_position_allocation(self.bars(33.0), 33.0)
        expensive = sim._base_position_allocation(self.bars(100.01), 100.01)

        self.assertEqual(low, (1000, 10.0, 10_000.0, 10_000.0))
        self.assertEqual(medium, (300, 33.0, 9_900.0, 10_000.0))
        self.assertEqual(expensive, (0, 100.01, 0.0, 10_000.0))

    def test_twenty_stocks_have_twenty_independent_base_ledgers(self):
        allocations = [sim._base_position_allocation(self.bars(10.0 + index), 10.0 + index) for index in range(20)]
        self.assertEqual(len(allocations), 20)
        self.assertEqual(sum(item[3] for item in allocations), 200_000.0)
        self.assertTrue(all(item[2] <= item[3] for item in allocations))

    def test_opening_probes_never_exceed_one_third_of_base_inventory(self):
        first = sim._opening_layer_share_cap(1000, 0)
        second = sim._opening_layer_share_cap(1000, 1)
        third = sim._opening_layer_share_cap(1000, 2)
        self.assertEqual((first, second, third), (100, 100, 0))
        self.assertLessEqual(first + second, 1000 // 3)

    def test_dashboard_passes_per_stock_budgets_without_fixed_shares(self):
        command = app_core.build_commands(
            "simulate",
            {"sample": 20, "perStockBaseAmount": 10_000, "perStockTLimit": 5_000},
        )[0]
        self.assertEqual(command[command.index("--base-amount") + 1], "10000")
        self.assertEqual(command[command.index("--per-trade") + 1], "5000")
        self.assertEqual(command[command.index("--max-trades") + 1], "20")
        self.assertNotIn("--base-shares", command)
        self.assertNotIn("--cash", command)

    def test_main_does_not_apply_cross_stock_cash_contention(self):
        stocks = [
            sim.Stock("甲", "000001", "sz000001"),
            sim.Stock("乙", "000002", "sz000002"),
        ]
        candidates = [(stock, self.bars(10.0 + index)) for index, stock in enumerate(stocks)]

        def result_for(stock, bars, trade_amount, days):
            return sim.Result(
                stock, "正T止盈", "10:00", bars[0].price, "10:20", bars[0].price * 1.01,
                1.0, 30.0, trade_amount, 300, "闭环", gross_pnl_yuan=40.0,
                fees_yuan=10.0, base_amount=10_000.0,
                base_reference_price=bars[0].price,
            )

        with (
            patch.object(sim, "build_random_pool", return_value=stocks),
            patch.object(sim, "fetch_simulation_candidates", return_value=candidates),
            patch.object(sim, "simulate_across_days", side_effect=result_for),
            patch.object(sim, "apply_cash_constraints", side_effect=AssertionError("shared cash must not run")),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            code = sim.main([
                "simulate_t_random.py", "2", "--base-amount", "10000",
                "--per-trade", "5000", "--max-trades", "2", "--seed", "20260712",
            ])

        self.assertEqual(code, 0)
        self.assertIn("触发 2/2", output.getvalue())
        self.assertIn("目标底仓总额 20,000.00元", output.getvalue())
        self.assertNotIn("资金不足未执行", output.getvalue())

    def test_simulation_page_uses_per_stock_budget_labels(self):
        html = app_core.SIMULATION_HTML
        self.assertIn("每股底仓金额", html)
        self.assertIn("每股T额度", html)
        self.assertIn("perStockBaseAmount", html)
        self.assertIn("perStockTLimit", html)
        self.assertNotIn('id="baseSharesInput"', html)
        self.assertNotIn("$('cashInput').value=ending", html)

    def test_summary_parser_reads_independent_ledger_totals(self):
        raw = "\n".join([
            "每股底仓 10,000元  每股T额度 5,000元",
            "实际底仓总额 198,800.00元  目标底仓总额 200,000.00元",
            "模拟盈亏 +320.50元  组合收益 +0.16%",
        ])
        stats = app_core.parse_sim_stats(raw)
        self.assertEqual(stats["cash"], "10,000元")
        self.assertEqual(stats["trade"], "5,000元")
        self.assertEqual(stats["allocatedBase"], "198,800.00元")
        self.assertEqual(stats["targetBase"], "200,000.00元")
        self.assertEqual(stats["pnl"], "+320.50元")


if __name__ == "__main__":
    unittest.main()
