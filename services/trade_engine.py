"""Execution assumptions shared by simulation and future account adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeCostModel:
    """A-share order costs; rates are configurable per simulation run."""

    commission_rate: float = 0.00025
    min_commission: float = 0.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.0
    slippage_bps: float = 2.0

    def execution_price(self, price: float, side: str) -> float:
        multiplier = 1.0 + self.slippage_bps / 10000.0 if side == "buy" else 1.0 - self.slippage_bps / 10000.0
        return max(0.0, price * multiplier)

    def fee(self, side: str, amount: float, code: str) -> float:
        commission = max(self.min_commission, amount * self.commission_rate)
        stamp_duty = amount * self.stamp_duty_rate if side == "sell" else 0.0
        transfer = amount * self.transfer_fee_rate if str(code).startswith("6") else 0.0
        return commission + stamp_duty + transfer


@dataclass
class PositionState:
    """Same-day A-share inventory: only yesterday's shares are sellable."""

    base_shares: int
    sellable_shares: int | None = None
    completed_cycles: int = 0
    sold_today: int = 0
    bought_today: int = 0
    base_budget: float = 0.0
    base_reference_price: float = 0.0

    def __post_init__(self) -> None:
        self.base_shares = max(0, int(self.base_shares // 100 * 100))
        if self.sellable_shares is None:
            self.sellable_shares = self.base_shares
        self.sellable_shares = max(0, min(self.base_shares, int(self.sellable_shares // 100 * 100)))

    def executable_shares(self, requested: int) -> int:
        return max(0, min(int(requested // 100 * 100), int(self.sellable_shares or 0)))

    def settle_closed_t(self, shares: int) -> None:
        shares = self.executable_shares(shares)
        self.sellable_shares = max(0, int(self.sellable_shares or 0) - shares)
        self.sold_today += shares
        self.bought_today += shares
        self.completed_cycles += 1

    def snapshot(self) -> dict[str, int | float | str]:
        return {
            "baseShares": self.base_shares,
            "baseBudget": round(max(0.0, float(self.base_budget or 0.0)), 2),
            "baseReferencePrice": round(max(0.0, float(self.base_reference_price or 0.0)), 4),
            "baseAmount": round(self.base_shares * max(0.0, float(self.base_reference_price or 0.0)), 2),
            "sellableShares": int(self.sellable_shares or 0),
            "soldToday": self.sold_today,
            "boughtToday": self.bought_today,
            "completedCycles": self.completed_cycles,
            "status": "已恢复底仓" if self.completed_cycles else "待执行",
        }
