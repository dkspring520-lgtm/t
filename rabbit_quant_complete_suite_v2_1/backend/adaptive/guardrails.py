from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, Mapping

from .models import AdaptiveParams, LearningConfig

# 学习模块永远不能修改这些硬风控字段。
IMMUTABLE_RISK_FIELDS = {
    "daily_loss_limit_rate",
    "max_daily_cycles",
    "max_position_ratio",
    "max_trade_quantity",
    "base_holding_available",
    "cash_available",
    "estimated_cycle_cost_rate",
    "slippage_rate_per_side",
    "new_cycle_cutoff_time",
    "force_close_time",
    "force_close_enabled",
    "max_holding_bars",
    "max_adverse_rate",
    "max_adverse_atr_multiple",
    "tradable",
    "suspended",
    "limit_up",
    "limit_down",
}

PARAM_BOUNDS = {
    "confirmed_score": (74.0, 92.0),
    "watch_score": (45.0, 65.0),
    "candidate_score": (60.0, 82.0),
    "cooldown_bars": (3, 12),
    "zone_memory_bars": (3, 10),
    "min_expected_net_rate": (0.0020, 0.0080),
    "strong_trend_extra_score": (4.0, 14.0),
    "min_volume_ratio": (0.10, 0.65),
}


def assert_no_hard_risk_changes(values: Mapping[str, object]) -> None:
    blocked = IMMUTABLE_RISK_FIELDS.intersection(values)
    if blocked:
        raise ValueError(f"学习模块禁止修改硬风控字段: {sorted(blocked)}")


def clamp_params(params: AdaptiveParams) -> AdaptiveParams:
    values = params.to_dict()
    for key, (low, high) in PARAM_BOUNDS.items():
        value = values[key]
        value = min(max(value, low), high)
        if key in {"cooldown_bars", "zone_memory_bars"}:
            value = int(round(value))
        values[key] = value

    # 保持层级关系，避免观察阈值与确认阈值互相穿越。
    values["candidate_score"] = min(
        values["candidate_score"], values["confirmed_score"] - 4.0
    )
    values["watch_score"] = min(
        values["watch_score"], values["candidate_score"] - 4.0
    )
    return AdaptiveParams.from_mapping(values)


def bounded_update(
    base: AdaptiveParams,
    changes: Mapping[str, float],
    config: LearningConfig,
) -> AdaptiveParams:
    assert_no_hard_risk_changes(changes)
    values = base.to_dict()
    for key, delta in changes.items():
        if key not in values:
            raise ValueError(f"不支持学习参数: {key}")
        if key in {"confirmed_score", "watch_score", "candidate_score", "strong_trend_extra_score"}:
            delta = max(-config.max_score_step, min(config.max_score_step, float(delta)))
        elif key == "min_expected_net_rate":
            delta = max(-config.max_rate_step, min(config.max_rate_step, float(delta)))
        elif key in {"cooldown_bars", "zone_memory_bars"}:
            delta = max(-config.max_cooldown_step, min(config.max_cooldown_step, int(delta)))
        elif key == "min_volume_ratio":
            delta = max(-config.max_volume_step, min(config.max_volume_step, float(delta)))
        values[key] = values[key] + delta
    return clamp_params(AdaptiveParams.from_mapping(values))
