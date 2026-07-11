import unittest

from smart_t_policy import evaluate_smart_t, market_regime


def points(values, start=570):
    return [
        {"time": f"{(start+i)//60:02d}:{(start+i)%60:02d}", "price": value}
        for i, value in enumerate(values)
    ]


class SmartTPolicyTests(unittest.TestCase):
    def base(self, **overrides):
        data = dict(
            profile="balanced",
            time_text="10:10",
            price=10.0,
            average=10.0,
            high=10.2,
            low=9.8,
            points=points([9.90 + i * 0.003 for i in range(41)]),
            signal_action="BUY_FIRST",
            signal_score=9,
            strict_signal=True,
            market_status="交易中",
        )
        data.update(overrides)
        return evaluate_smart_t(**data)

    def test_opening_noise_is_blocked(self):
        result = self.base(time_text="09:40")
        self.assertEqual(result["state"], "OPENING_OBSERVE")
        self.assertFalse(result["confirmed"])

    def test_observation_never_becomes_trade(self):
        result = self.base(strict_signal=False, signal_action="低位机会")
        self.assertEqual(result["state"], "WAIT_CONFIRMATION")

    def test_uptrend_blocks_sell_first(self):
        rising = points([9.80 + i * 0.01 for i in range(41)])
        result = self.base(points=rising, average=9.9, price=10.2, signal_action="SELL_FIRST")
        self.assertEqual(market_regime(rising, 9.9, 610), "UPTREND")
        self.assertEqual(result["state"], "TREND_BLOCKED")

    def test_confirmed_signal_passes_all_gates(self):
        rising = points([9.80 + i * 0.01 for i in range(41)])
        result = self.base(points=rising, average=10.1, price=10.0, high=10.25, low=9.75)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["state"], "READY")

    def test_cutoff_and_force_close(self):
        self.assertEqual(self.base(time_text="14:35")["state"], "ENTRY_CUTOFF")
        forced = self.base(time_text="14:52")
        self.assertEqual(forced["state"], "FORCE_CLOSE")
        self.assertTrue(forced["forceClose"])

    def test_quantbrain_uses_learned_params_and_blocks_overheated_buy(self):
        rising = points([9.6 + i * 0.02 for i in range(41)])
        result = self.base(
            profile="quantbrain",
            points=rising,
            price=10.4,
            average=10.0,
            high=10.5,
            low=9.6,
            learned_params={"confirmed_score": 80, "cooldown_bars": 6, "min_expected_net_rate": 0.004, "version_id": "v-test"},
        )
        self.assertEqual(result["profile"]["label"], "量化学习")
        self.assertEqual(result["experienceVersion"], "v-test")
        self.assertEqual(result["state"], "QUANT_FACTOR_BLOCKED")
        self.assertGreaterEqual(result["quantFeatures"]["rsi"], 78)


if __name__ == "__main__":
    unittest.main()
