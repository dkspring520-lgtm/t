from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, Literal, Mapping, Optional, Sequence

LearningMode = Literal["manual", "auto", "off"]
VersionStatus = Literal["champion", "challenger", "archived", "rolled_back"]


@dataclass(frozen=True)
class AdaptiveParams:
    """允许学习模块微调的参数。

    硬风控（仓位、每日亏损、收盘时间、可卖数量、费用规则）不在这里，
    因此学习模块无法绕过硬风控。
    """

    confirmed_score: float = 82.0
    watch_score: float = 52.0
    candidate_score: float = 68.0
    cooldown_bars: int = 5
    zone_memory_bars: int = 5
    min_expected_net_rate: float = 0.0035
    strong_trend_extra_score: float = 8.0
    min_volume_ratio: float = 0.18

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "AdaptiveParams":
        valid = set(cls.__dataclass_fields__)
        unknown = set(values) - valid
        if unknown:
            raise ValueError(f"存在不可学习或未知参数: {sorted(unknown)}")
        return cls(**dict(values))


@dataclass(frozen=True)
class LearningConfig:
    mode: LearningMode = "manual"
    database_path: str = "data/rabbit_learning.sqlite3"

    # 样本门槛
    min_labeled_signals: int = 60
    min_trading_days: int = 10
    min_shadow_days: int = 5
    min_live_trades_for_rollback: int = 20

    # 评估窗口
    primary_horizon_bars: int = 5
    outcome_horizons: tuple[int, ...] = (3, 5, 10)
    estimated_cycle_cost_rate: float = 0.0010

    # 候选版本与晋级门槛
    required_expectancy_improvement: float = 0.00015
    required_composite_improvement: float = 0.03
    max_win_rate_drop: float = 0.02
    max_drawdown_worsening_ratio: float = 1.10
    min_trade_count_ratio: float = 0.50
    max_trade_count_ratio: float = 1.60
    bootstrap_probability_threshold: float = 0.85
    bootstrap_iterations: int = 600

    # 自动回滚
    rollback_expectancy_floor: float = -0.0005
    rollback_max_drawdown: float = 0.02

    # 每次只允许小幅变化
    max_score_step: float = 3.0
    max_rate_step: float = 0.0010
    max_cooldown_step: int = 2
    max_volume_step: float = 0.10


@dataclass(frozen=True)
class SignalObservation:
    symbol: str
    signal_time: str
    trading_date: str
    direction: str
    regime: str
    score: float
    price: float
    volume_ratio: float
    top_score: float = 0.0
    bottom_score: float = 0.0
    executed: bool = False
    decision: str = "CANDIDATE"
    parameter_version: str = ""
    features: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeObservation:
    symbol: str
    trading_date: str
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    net_return: float
    result: str
    exit_reason: str
    parameter_version: str
    regime: str = "UNKNOWN"
    holding_bars: int = 0


@dataclass(frozen=True)
class EvaluationMetrics:
    version_id: str
    sample_count: int
    trading_days: int
    expectancy: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    downside_mean: float
    composite_score: float
    accepted_signal_ids: tuple[int, ...] = ()
    returns: tuple[float, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["accepted_signal_ids"] = list(self.accepted_signal_ids)
        data["returns"] = list(self.returns)
        return data


@dataclass(frozen=True)
class PromotionDecision:
    approved: bool
    reason: str
    champion: EvaluationMetrics
    challenger: EvaluationMetrics
    bootstrap_probability: float
    auto_applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "bootstrapProbability": self.bootstrap_probability,
            "autoApplied": self.auto_applied,
            "champion": self.champion.to_dict(),
            "challenger": self.challenger.to_dict(),
        }


@dataclass(frozen=True)
class LearningRunResult:
    status: str
    message: str
    champion_version: str
    challenger_version: Optional[str] = None
    proposal: Optional[Dict[str, Any]] = None
    decision: Optional[PromotionDecision] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "championVersion": self.champion_version,
            "challengerVersion": self.challenger_version,
            "proposal": self.proposal,
            "decision": None if self.decision is None else self.decision.to_dict(),
        }
