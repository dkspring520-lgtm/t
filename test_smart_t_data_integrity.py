import unittest

import simulate_t_random as sim
from stock_t_signal import MinuteBar, Quote, _minute_volumes, _vwap


class SmartTDataIntegrityTests(unittest.TestCase):
    def test_incremental_minutes_use_cumulative_vwap_without_second_difference(self):
        minutes = [
            MinuteBar("09:30", 10.0, 100.0, 100000.0),
            MinuteBar("09:31", 11.0, 300.0, 330000.0),
        ]
        quote = Quote("T", 11.0, 10.0, 11.0, 10.0, 10.0, 400.0, 43.0, "09:31")
        self.assertEqual(_minute_volumes(minutes), [100.0, 300.0])
        self.assertAlmostEqual(_vwap(quote, minutes), 10.75)

    def test_reverse_t_does_not_consume_cross_stock_cash_reserve(self):
        stock = sim.Stock("T", "000001", "sz000001")
        buy = sim.Result(stock, "正T止盈", "10:00", 10.0, "10:30", 10.1, 1.0, 10.0, 10000.0, 1000, "ok")
        reverse = sim.Result(stock, "反T买回", "10:10", 10.0, "10:25", 9.9, 1.0, 10.0, 10000.0, 1000, "ok")
        allowed = sim.apply_cash_constraints([buy, reverse], 10000.0)
        self.assertEqual(len(allowed), 2)
        self.assertEqual(allowed[1].action, "反T买回")


if __name__ == "__main__":
    unittest.main()
