import unittest

from smart_t_policy import (
    _opening_rsi_extremes,
    evaluate_smart_t,
    intraday_reversal_context,
    market_regime,
    market_regime_details,
)


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

    def test_opening_trial_requires_auction_and_uses_one_sixth(self):
        waiting = self.base(time_text="09:50")
        self.assertEqual(waiting["state"], "OPENING_TRIAL_WAIT")

        recovering = points(
            [9.55, 9.50, 9.54, 9.58, 9.62, 9.68, 9.74, 9.82, 9.91, 10.02, 10.08, 10.12],
            start=570,
        )
        result = self.base(
            time_text="09:50",
            points=recovering,
            average=10.05,
            price=10.12,
            high=10.12,
            low=9.50,
            signal_score=10,
            auction_direction="BUY_FIRST",
            auction_state="CONFIRMED",
        )
        self.assertTrue(result["confirmed"])
        self.assertTrue(result["openingTrial"])
        self.assertAlmostEqual(result["positionFraction"], 1 / 6)

    def test_opening_trial_requires_material_five_minute_follow_through(self):
        weak_recovery = points(
            [10.00, 10.01, 9.99, 10.01, 10.00, 10.015, 10.00, 10.02,
             10.01, 10.02, 10.025, 10.01, 10.03, 10.02, 10.03, 10.025],
            start=570,
        )
        result = self.base(
            time_text="09:45",
            points=weak_recovery,
            average=10.01,
            price=10.025,
            high=10.03,
            low=9.99,
            signal_score=10,
            auction_direction="BUY_FIRST",
            auction_state="CONFIRMED",
        )
        self.assertEqual(result["state"], "OPENING_FOLLOW_THROUGH_BLOCKED")
        self.assertFalse(result["confirmed"])

    def test_auction_direction_does_not_lock_normal_trading_after_1000(self):
        rising = points([9.80 + i * 0.01 for i in range(41)])
        result = self.base(
            time_text="10:01",
            points=rising,
            average=10.1,
            price=10.0,
            high=10.25,
            low=9.75,
            signal_action="BUY_FIRST",
            auction_direction="SELL_FIRST",
            auction_state="CONFIRMED",
        )
        self.assertEqual(result["state"], "READY")
        self.assertTrue(result["confirmed"])

    def test_opening_buy_blocks_an_exhausted_reclaim(self):
        rising = points([9.50 + i * 0.06 for i in range(18)], start=570)
        result = self.base(
            time_text="09:50",
            points=rising,
            price=rising[-1]["price"],
            average=10.0,
            high=rising[-1]["price"],
            low=9.50,
            signal_action="BUY_FIRST",
            signal_score=10,
            auction_direction="BUY_FIRST",
            auction_state="CONFIRMED",
        )
        self.assertEqual(result["state"], "OPENING_EXHAUSTION_BLOCKED")
        self.assertFalse(result["confirmed"])

    def test_opening_sell_blocks_a_late_oversold_exit(self):
        falling = points([10.60 - i * 0.06 for i in range(18)], start=570)
        result = self.base(
            time_text="09:50",
            points=falling,
            price=falling[-1]["price"],
            average=10.0,
            high=10.60,
            low=falling[-1]["price"],
            signal_action="SELL_FIRST",
            signal_score=10,
            auction_direction="SELL_FIRST",
            auction_state="CONFIRMED",
        )
        self.assertEqual(result["state"], "OPENING_EXHAUSTION_BLOCKED")
        self.assertFalse(result["confirmed"])

    def test_opening_exhaustion_uses_current_rsi_not_a_reversed_historical_peak(self):
        recovered = points(
            [9.0 + index * 0.05 for index in range(16)]
            + [9.70 - index * 0.04 for index in range(8)],
            start=570,
        )
        peak, trough = _opening_rsi_extremes(recovered)
        self.assertEqual(peak, trough)
        self.assertLess(peak, 85)

    def test_opening_exhaustion_still_blocks_a_current_rsi_extreme(self):
        rising = points([10.0 + index * 0.04 for index in range(18)], start=570)
        peak, trough = _opening_rsi_extremes(rising)
        self.assertEqual(peak, trough)
        self.assertGreaterEqual(peak, 85)

    def test_low_gap_opening_buy_can_pass_after_reclaiming_vwap(self):
        recovering = points([9.55, 9.50, 9.54, 9.58, 9.62, 9.68, 9.74, 9.82, 9.91, 10.02, 10.08, 10.12], start=570)
        result = self.base(
            time_text="09:50",
            points=recovering,
            price=10.12,
            average=10.05,
            high=10.12,
            low=9.50,
            signal_action="BUY_FIRST",
            signal_score=10,
            auction_direction="BUY_FIRST",
            auction_state="CONFIRMED",
        )
        self.assertEqual(result["state"], "READY")
        self.assertTrue(result["confirmed"])
        self.assertGreater(result["availableSpreadPct"], result["requiredGrossSpreadPct"])

    def test_high_gap_opening_sell_can_pass_after_losing_vwap(self):
        fading = points([10.55, 10.60, 10.58, 10.52, 10.45, 10.36, 10.28, 10.20, 10.12, 10.05, 10.00, 9.95], start=570)
        result = self.base(
            time_text="09:50",
            points=fading,
            price=9.95,
            average=10.05,
            high=10.60,
            low=9.95,
            signal_action="SELL_FIRST",
            signal_score=10,
            auction_direction="SELL_FIRST",
            auction_state="CONFIRMED",
        )
        self.assertEqual(result["state"], "READY")
        self.assertTrue(result["confirmed"])
        self.assertGreater(result["availableSpreadPct"], result["requiredGrossSpreadPct"])

    def test_observation_never_becomes_trade(self):
        result = self.base(strict_signal=False, signal_action="低位机会")
        self.assertEqual(result["state"], "WAIT_CONFIRMATION")

    def test_uptrend_blocks_sell_first(self):
        rising = points([9.80 + i * 0.01 for i in range(41)])
        result = self.base(points=rising, average=9.9, price=10.2, signal_action="SELL_FIRST")
        self.assertEqual(market_regime(rising, 9.9, 610), "UPTREND")
        self.assertEqual(result["state"], "TREND_BLOCKED")

    def test_strong_recovery_above_vwap_blocks_reverse_t_after_1000(self):
        values = [value for close in (10.00, 10.20, 10.40, 10.60, 10.50, 10.55) for value in [close] * 5]
        recovering = points(values)
        result = self.base(
            time_text="10:10",
            points=recovering,
            price=10.55,
            average=10.30,
            high=10.60,
            low=10.00,
            signal_action="SELL_FIRST",
            signal_score=10,
        )
        self.assertEqual(market_regime(recovering, 10.30, 610), "RANGE")
        self.assertEqual(result["state"], "STRONG_RECOVERY_BLOCKED")
        self.assertGreaterEqual(result["fromOpenPct"], 3.0)
        self.assertGreaterEqual(result["vwapGapPct"], 0.5)

    def test_spike_and_fade_is_not_misclassified_as_uptrend(self):
        values = [value for close in (10.00, 10.20, 10.04) for value in [close] * 5]
        choppy = points(values)
        details = market_regime_details(choppy, 10.0, 9 * 60 + 45)
        self.assertEqual(details["state"], "RANGE")
        self.assertLess(details["pathEfficiency"], 0.20)
        # The richer classifier remains shadow-only until it wins an
        # out-of-sample comparison; execution keeps the proven legacy gate.
        self.assertEqual(market_regime(choppy, 10.0, 9 * 60 + 45), "UPTREND")

    def test_regime_audit_uses_volume_without_requiring_it(self):
        rising = points([9.80 + i * 0.01 for i in range(41)])
        without_volume = market_regime_details(rising, 9.9, 610)
        self.assertEqual(without_volume["state"], "UPTREND")
        self.assertIsNone(without_volume["volumeParticipationRatio"])

        with_volume = [dict(item, volumeDelta=1000 + index * 20) for index, item in enumerate(rising)]
        volume_details = market_regime_details(with_volume, 9.9, 610)
        self.assertEqual(volume_details["state"], "UPTREND")
        self.assertIsNotNone(volume_details["volumeParticipationRatio"])
        self.assertGreaterEqual(volume_details["confidence"], 75)

        cumulative_only = [dict(item, volume=1000 + index * 100, volumeMode="cumulative") for index, item in enumerate(rising)]
        cumulative_details = market_regime_details(cumulative_only, 9.9, 610)
        self.assertIsNone(cumulative_details["volumeParticipationRatio"])

    def test_three_times_volume_near_high_blocks_countertrend_sell_while_rising(self):
        rising = points([10.00 + index * 0.02 for index in range(20)])
        with_volume = [
            dict(item, volumeDelta=3200 if index == 19 else 1000)
            for index, item in enumerate(rising)
        ]
        result = self.base(
            time_text="10:10",
            points=with_volume,
            price=10.38,
            average=10.20,
            high=10.38,
            low=10.00,
            signal_action="SELL_FIRST",
            signal_score=10,
        )
        self.assertEqual(result["volumeContext"]["phase"], "UP_CONTINUATION")
        self.assertGreaterEqual(result["volumeContext"]["volumeRatio"], 3.0)
        self.assertEqual(result["volumeContext"]["spikeBarOffset"], 0)
        self.assertEqual(result["volumeScoreAdjustment"], -2)
        self.assertEqual(result["state"], "VOLUME_CONTINUATION_BLOCKED")

    def test_three_times_volume_near_high_adds_confirmation_after_price_turns(self):
        values = [10.00 + index * 0.02 for index in range(17)] + [10.34, 10.30, 10.26]
        fading = points(values)
        with_volume = [
            dict(item, volumeDelta=3200 if index == 17 else 1000)
            for index, item in enumerate(fading)
        ]
        result = self.base(
            time_text="10:10",
            points=with_volume,
            price=10.26,
            average=10.20,
            high=10.34,
            low=10.00,
            signal_action="SELL_FIRST",
            signal_score=7,
        )
        self.assertEqual(result["volumeContext"]["phase"], "TOP_EXHAUSTION")
        self.assertGreaterEqual(result["volumeContext"]["volumeRatio"], 3.0)
        self.assertEqual(result["volumeContext"]["spikeBarOffset"], 2)
        self.assertEqual(result["volumeScoreAdjustment"], 1)
        self.assertEqual(result["effectiveScore"], result["rawScore"] + 1)

    def test_volume_below_three_times_is_not_a_climax(self):
        rising = points([10.00 + index * 0.02 for index in range(20)])
        with_volume = [
            dict(item, volumeDelta=2990 if index == 19 else 1000)
            for index, item in enumerate(rising)
        ]
        result = self.base(
            points=with_volume,
            price=10.38,
            average=10.20,
            high=10.38,
            low=10.00,
            signal_action="SELL_FIRST",
            signal_score=10,
        )
        self.assertFalse(result["volumeContext"]["climax"])
        self.assertEqual(result["volumeContext"]["phase"], "NEUTRAL")
        self.assertEqual(result["volumeScoreAdjustment"], 0)

    def test_three_times_volume_near_low_blocks_buy_while_still_falling(self):
        falling = points([10.38 - index * 0.02 for index in range(20)])
        with_volume = [
            dict(item, volumeDelta=3200 if index == 19 else 1000)
            for index, item in enumerate(falling)
        ]
        result = self.base(
            time_text="10:10",
            points=with_volume,
            price=10.00,
            average=10.20,
            high=10.38,
            low=10.00,
            signal_action="BUY_FIRST",
            signal_score=10,
        )
        self.assertEqual(result["volumeContext"]["phase"], "DOWN_CONTINUATION")
        self.assertEqual(result["volumeContext"]["spikeBarOffset"], 0)
        self.assertEqual(result["state"], "VOLUME_CONTINUATION_BLOCKED")

    def test_three_times_volume_near_low_adds_confirmation_after_rebound(self):
        values = [10.38 - index * 0.02 for index in range(17)] + [10.04, 10.08, 10.12]
        rebounding = points(values)
        with_volume = [
            dict(item, volumeDelta=3200 if index == 17 else 1000)
            for index, item in enumerate(rebounding)
        ]
        result = self.base(
            time_text="10:10",
            points=with_volume,
            price=10.12,
            average=10.20,
            high=10.38,
            low=10.04,
            signal_action="BUY_FIRST",
            signal_score=7,
        )
        self.assertEqual(result["volumeContext"]["phase"], "BOTTOM_EXHAUSTION")
        self.assertEqual(result["volumeContext"]["spikeBarOffset"], 2)
        self.assertEqual(result["volumeScoreAdjustment"], 1)

    def test_super_volume_exhaustion_has_a_separate_tier(self):
        values = [10.00 + index * 0.02 for index in range(17)] + [10.34, 10.30, 10.26]
        with_volume = [
            dict(item, volumeDelta=5200 if index == 17 else 1000)
            for index, item in enumerate(points(values))
        ]
        result = self.base(
            points=with_volume,
            price=10.26,
            average=10.20,
            high=10.34,
            low=10.00,
            signal_action="SELL_FIRST",
            signal_score=7,
        )
        self.assertEqual(result["volumeContext"]["tier"], "SUPER_5X")
        self.assertEqual(result["volumeScoreAdjustment"], 2)
        self.assertEqual(result["contextScoreAdjustment"], 2)

    def test_shared_reversal_candidate_marks_a_volume_top_after_right_side_turn(self):
        values = [10.00 + index * 0.02 for index in range(17)] + [10.34, 10.30, 10.26]
        rows = [
            dict(item, volumeDelta=5200 if index == 17 else 1000)
            for index, item in enumerate(points(values))
        ]
        context = intraday_reversal_context(rows, values[-1], max(values), min(values))
        self.assertEqual(context["direction"], "SELL_FIRST")
        self.assertEqual(context["quality"], "EXTREME")
        self.assertEqual(context["recommendedBasePositionFraction"], 0.33)
        self.assertIn("高位", context["reason"])

    def test_shared_reversal_candidate_marks_a_volume_bottom_after_right_side_turn(self):
        values = [10.38 - index * 0.02 for index in range(17)] + [10.04, 10.08, 10.12]
        rows = [
            dict(item, volumeDelta=3200 if index == 17 else 1000)
            for index, item in enumerate(points(values))
        ]
        context = intraday_reversal_context(rows, values[-1], max(values), min(values))
        self.assertEqual(context["direction"], "BUY_FIRST")
        self.assertEqual(context["quality"], "STRONG")
        self.assertEqual(context["recommendedBasePositionFraction"], 0.20)
        self.assertIn("低位", context["reason"])

    def test_straight_up_impulse_is_blocked_until_it_stalls(self):
        quiet = [10.00, 10.01, 10.00, 10.02, 10.01, 10.03, 10.02, 10.04, 10.03, 10.05, 10.04, 10.06]
        live = quiet + [10.18, 10.32, 10.48]
        result = self.base(
            points=points(live),
            price=live[-1],
            average=10.20,
            high=max(live),
            low=min(live),
            signal_action="SELL_FIRST",
            signal_score=10,
        )
        self.assertEqual(result["priceImpulseContext"]["phase"], "UP_IMPULSE_CONTINUATION")
        self.assertEqual(result["state"], "IMPULSE_CONTINUATION_BLOCKED")

        stalled = live + [10.47, 10.44]
        confirmed = self.base(
            points=points(stalled),
            price=stalled[-1],
            average=10.20,
            high=max(stalled),
            low=min(stalled),
            signal_action="SELL_FIRST",
            signal_score=7,
        )
        self.assertEqual(confirmed["priceImpulseContext"]["phase"], "TOP_IMPULSE_EXHAUSTION")
        self.assertEqual(confirmed["priceImpulseScoreAdjustment"], 1)

    def test_straight_down_impulse_is_blocked_until_it_rebounds(self):
        quiet = [10.00, 10.01, 10.00, 10.02, 10.01, 10.03, 10.02, 10.04, 10.03, 10.05, 10.04, 10.06]
        live = quiet + [9.92, 9.78, 9.62]
        result = self.base(
            points=points(live),
            price=live[-1],
            average=9.90,
            high=max(live),
            low=min(live),
            signal_action="BUY_FIRST",
            signal_score=10,
        )
        self.assertEqual(result["priceImpulseContext"]["phase"], "DOWN_IMPULSE_CONTINUATION")
        self.assertEqual(result["state"], "IMPULSE_CONTINUATION_BLOCKED")

        rebounded = live + [9.63, 9.67]
        confirmed = self.base(
            points=points(rebounded),
            price=rebounded[-1],
            average=9.90,
            high=max(rebounded),
            low=min(rebounded),
            signal_action="BUY_FIRST",
            signal_score=7,
        )
        self.assertEqual(confirmed["priceImpulseContext"]["phase"], "BOTTOM_IMPULSE_EXHAUSTION")
        self.assertEqual(confirmed["priceImpulseScoreAdjustment"], 1)

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

    def test_strong_market_raises_reverse_t_confirmation_threshold(self):
        flat = points([10.2] * 41)
        result = self.base(
            points=flat,
            price=10.2,
            average=10.0,
            high=10.3,
            signal_action="SELL_FIRST",
            signal_score=9,
            market_radar_score=80,
        )
        self.assertEqual(result["marketRadarBand"], "STRONG")
        self.assertEqual(result["requiredScore"], 10)
        self.assertEqual(result["state"], "SCORE_BLOCKED")

    def test_weak_market_blocks_aggressive_buy_first(self):
        result = self.base(market_radar_score=20)
        self.assertEqual(result["marketRadarBand"], "RISK_OFF")
        self.assertEqual(result["state"], "RADAR_RISK_OFF_BUY_BLOCKED")
        self.assertFalse(result["confirmed"])

    def test_overheated_market_requires_pullback_before_reverse_t(self):
        rising = points([10.05 + i * 0.006 for i in range(40)] + [10.31])
        result = self.base(
            points=rising,
            price=10.31,
            average=10.0,
            high=10.35,
            signal_action="SELL_FIRST",
            signal_score=10,
            market_radar_score=90,
        )
        self.assertEqual(result["marketRadarBand"], "OVERHEATED")
        self.assertEqual(result["state"], "RADAR_OVERHEAT_WAIT_PULLBACK")

    def test_reward_risk_gate_blocks_poor_payoff_even_when_signal_is_confirmed(self):
        rising = points([9.80 + i * 0.01 for i in range(41)])
        result = self.base(
            points=rising,
            price=10.0,
            average=10.2,
            high=10.25,
            low=9.75,
            structural_stop_price=9.8,
            min_reward_risk_ratio=1.25,
        )
        self.assertEqual(result["state"], "REWARD_RISK_BLOCKED")
        self.assertAlmostEqual(result["rewardRiskRatio"], 1.0, places=2)

    def test_reward_risk_gate_allows_same_signal_with_tighter_real_stop(self):
        rising = points([9.80 + i * 0.01 for i in range(41)])
        result = self.base(
            points=rising,
            price=10.0,
            average=10.2,
            high=10.25,
            low=9.75,
            structural_stop_price=9.9,
            min_reward_risk_ratio=1.25,
        )
        self.assertEqual(result["state"], "READY")
        self.assertGreaterEqual(result["rewardRiskRatio"], 1.25)


if __name__ == "__main__":
    unittest.main()
