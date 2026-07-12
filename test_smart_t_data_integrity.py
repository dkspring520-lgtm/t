import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import simulate_t_random as sim
from services.market_data_quality import normalise_volume_lots
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

    def test_provider_share_volume_is_normalised_to_lots(self):
        lots = normalise_volume_lots(116.76, 57647, 6730864)
        self.assertAlmostEqual(lots, 576.47, places=2)
        self.assertEqual(normalise_volume_lots(8.69, 2147, 1865743), 2147)

    def test_dirty_cache_rows_are_isolated_before_replay(self):
        rows = [
            sim.Bar("09:30", 10.0, 100.0, 100000.0, "2026-07-11"),
            sim.Bar("09:31", 10.1, 100.0, 101000.0, "2026-07-11"),
            sim.Bar("09:31", 10.2, 100.0, 102000.0, "2026-07-11"),
            sim.Bar("11:45", 10.2, 100.0, 102000.0, "2026-07-11"),
            sim.Bar("15:01", 10.2, 100.0, 102000.0, "2026-07-11"),
            sim.Bar("14:00", 10.2, 0.0, 0.0, "2026-07-11"),
            sim.Bar("14:01", 10.2, 100.0, 10200.0, "2026-07-11"),
        ]
        clean = sim._sanitize_bars(rows)
        self.assertEqual([bar.hm for bar in clean], ["09:30", "09:31"])
        self.assertEqual(clean[-1].price, 10.2)
        self.assertEqual({bar.date for bar in clean}, {"2026-07-10"})

    def test_lunch_minutes_are_not_tradable(self):
        self.assertFalse(sim._in_trade_window("11:45"))
        self.assertTrue(sim._in_trade_window("13:00"))

    def test_one_session_cannot_masquerade_as_five_day_replay(self):
        one_day = [
            sim.Bar(f"09:{30 + index:02d}", 10.0, 100.0, 100000.0, "2026-07-06")
            for index in range(30)
        ]
        five_days = [
            sim.Bar(f"09:{30 + index:02d}", 10.0, 100.0, 100000.0, date)
            for date in ("2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10")
            for index in range(30)
        ]
        self.assertTrue(sim._bars_cover_requested_days(one_day, 1))
        self.assertFalse(sim._bars_cover_requested_days(one_day, 5))
        self.assertTrue(sim._bars_cover_requested_days(five_days, 5))

    def test_five_day_fetch_never_falls_back_to_single_day_provider(self):
        one_day = [
            sim.Bar(f"09:{30 + index:02d}", 10.0, 100.0, 100000.0, "2026-07-06")
            for index in range(30)
        ]
        with (
            patch.object(sim, "load_cached_minutes", return_value=one_day),
            patch.object(sim, "minute_cache_is_fresh", return_value=False),
            patch.object(sim, "fetch_minutes_eastmoney", return_value=one_day),
            patch.object(sim, "_get") as current_day_provider,
        ):
            self.assertEqual(sim.fetch_minutes("sh600000", days=5), [])
        current_day_provider.assert_not_called()

    def test_tencent_quote_metadata_supplies_previous_close(self):
        payload = {"data": {"sh600000": {"qt": {"sh600000": ["1", "T", "600000", "9.06", "8.98"]}}}}
        self.assertEqual(sim._extract_tencent_previous_close(payload, "sh600000"), 8.98)
        self.assertEqual(sim._extract_tencent_previous_close({}, "sh600000"), 0.0)

    def test_previous_close_survives_minute_cache_round_trip(self):
        original_dir = sim.MINUTE_CACHE_DIR
        symbol = "sh600000"
        bars = [
            sim.Bar(f"09:{30 + index:02d}", 10.0, 100.0, 100000.0, "2026-07-10")
            for index in range(30)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sim.MINUTE_CACHE_DIR = Path(tmp)
            try:
                sim.PREV_CLOSE_BY_SYMBOL[symbol] = 9.8
                sim.save_cached_minutes(symbol, bars)
                sim.PREV_CLOSE_BY_SYMBOL.pop(symbol, None)
                loaded = sim.load_cached_minutes(symbol)
                self.assertEqual(len(loaded), 30)
                self.assertEqual(sim.PREV_CLOSE_BY_SYMBOL[symbol], 9.8)
            finally:
                sim.PREV_CLOSE_BY_SYMBOL.pop(symbol, None)
                sim.MINUTE_CACHE_DIR = original_dir


if __name__ == "__main__":
    unittest.main()
