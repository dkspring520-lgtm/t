import unittest

from auction_direction import evaluate_auction_gate
from smart_t_policy import evaluate_smart_t


def points(values, start_minute=30):
    return [
        {"time": f"09:{start_minute + index:02d}", "price": value}
        for index, value in enumerate(values)
    ]


class AuctionDirectionTests(unittest.TestCase):
    def test_small_low_gap_enters_buy_first_plan_but_same_high_gap_stays_neutral(self):
        low_gate = evaluate_auction_gate(
            pre_close=100,
            open_price=99.94,
            current_price=99.94,
            average=99.94,
            points=[{"time": "09:30", "price": 99.94}],
            time_text="09:30",
        )
        high_gate = evaluate_auction_gate(
            pre_close=100,
            open_price=100.21,
            current_price=100.21,
            average=100.21,
            points=[{"time": "09:30", "price": 100.21}],
            time_text="09:30",
        )
        self.assertEqual(low_gate["preferredDirection"], "BUY_FIRST")
        self.assertEqual(high_gate["preferredDirection"], "")

    def test_high_open_weakness_confirms_reverse_t(self):
        gate = evaluate_auction_gate(
            pre_close=100,
            open_price=101,
            current_price=100.5,
            average=100.8,
            points=points([101.2, 101.1, 100.9, 100.7, 100.5]),
            time_text="09:36",
            auction_price=101,
        )
        self.assertEqual(gate["state"], "CONFIRMED")
        self.assertEqual(gate["preferredDirection"], "SELL_FIRST")
        self.assertGreaterEqual(gate["confirmationCount"], 3)

    def test_high_open_reverse_confirms_two_conditions_before_execution_gate(self):
        gate = evaluate_auction_gate(
            pre_close=100,
            open_price=101,
            current_price=100.8,
            average=100.7,
            points=points([101.0, 101.0, 100.9, 100.8]),
            time_text="09:36",
            auction_price=101,
        )
        self.assertEqual(gate["preferredDirection"], "SELL_FIRST")
        self.assertEqual(gate["confirmationCount"], 2)
        self.assertEqual(gate["state"], "CONFIRMED")
        self.assertTrue(gate["confirmed"])

    def test_low_open_recovery_confirms_positive_t(self):
        gate = evaluate_auction_gate(
            pre_close=100,
            open_price=98,
            current_price=99.1,
            average=98.7,
            points=points([98.0, 98.2, 98.4, 98.8, 99.1]),
            time_text="09:36",
            auction_price=98,
        )
        self.assertEqual(gate["state"], "CONFIRMED")
        self.assertEqual(gate["preferredDirection"], "BUY_FIRST")

    def test_high_open_plan_can_be_invalidated(self):
        gate = evaluate_auction_gate(
            pre_close=100,
            open_price=101,
            current_price=102,
            average=101.3,
            points=points([101.0, 101.1, 101.2, 101.3, 101.4, 101.7, 102.0]),
            time_text="09:37",
            auction_price=101,
        )
        self.assertEqual(gate["state"], "INVALIDATED")
        self.assertTrue(gate["invalidated"])

    def test_confirmed_auction_blocks_opposite_smart_t_direction(self):
        policy = evaluate_smart_t(
            profile="balanced",
            time_text="10:00",
            price=100,
            average=99.8,
            high=101,
            low=98,
            points=[
                {"time": "09:40", "price": 99.0},
                {"time": "09:45", "price": 99.2},
                {"time": "09:50", "price": 99.5},
                {"time": "09:55", "price": 100.0},
            ],
            signal_action="反T卖出确认",
            signal_score=10,
            strict_signal=True,
            auction_direction="BUY_FIRST",
            auction_state="CONFIRMED",
        )
        self.assertEqual(policy["state"], "AUCTION_DIRECTION_BLOCKED")
        self.assertFalse(policy["confirmed"])


if __name__ == "__main__":
    unittest.main()
