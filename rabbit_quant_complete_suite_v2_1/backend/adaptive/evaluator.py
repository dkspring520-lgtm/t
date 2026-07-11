from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .models import AdaptiveParams, EvaluationMetrics, LearningConfig


def _max_drawdown(returns: Sequence[float]) -> float:
    if not returns:
        return 0.0
    equity = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    peaks = np.maximum.accumulate(equity)
    drawdowns = (peaks - equity) / np.maximum(peaks, 1e-12)
    return float(np.max(drawdowns))


def _profit_factor(returns: Sequence[float]) -> float:
    wins = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    if losses <= 1e-12:
        return 9.99 if wins > 0 else 0.0
    return float(wins / losses)


def _threshold_for(item: Mapping[str, object], params: AdaptiveParams) -> float:
    threshold = params.confirmed_score
    regime = str(item.get("regime", "UNKNOWN"))
    direction = str(item.get("direction", ""))
    # 强势上涨中卖出、弱势下跌中买入需要更高门槛。
    if (regime == "UPTREND" and direction == "SELL_FIRST") or (
        regime == "DOWNTREND" and direction == "BUY_FIRST"
    ):
        threshold += params.strong_trend_extra_score
    return threshold


def select_signals(
    rows: Sequence[Mapping[str, object]],
    params: AdaptiveParams,
    horizon_bars: int,
) -> List[Tuple[int, str, float]]:
    selected: List[Tuple[int, str, float]] = []
    last_accept: Dict[Tuple[str, str, str], int] = {}
    ordered = sorted(rows, key=lambda item: str(item["signal_time"]))
    per_key_counter: Dict[Tuple[str, str, str], int] = {}
    for item in ordered:
        key = (str(item["symbol"]), str(item["trading_date"]), str(item["direction"]))
        counter = per_key_counter.get(key, 0)
        per_key_counter[key] = counter + 1
        score = float(item["score"])
        volume_ratio = float(item.get("volume_ratio", 0.0))
        if score < _threshold_for(item, params):
            continue
        if volume_ratio < params.min_volume_ratio:
            continue
        if counter - last_accept.get(key, -10_000) < params.cooldown_bars:
            continue
        outcome = item.get("outcome", {})
        return_key = f"net_return_{horizon_bars}m"
        if return_key not in outcome:
            continue
        net_return = float(outcome[return_key])
        # 不允许用事后 MFE 决定当时是否接纳信号；这会造成未来数据泄漏。
        # min_expected_net_rate 应在完整成交模拟的退出层评估，而不是在此处看答案筛样本。
        last_accept[key] = counter
        selected.append((int(item["signal_id"]), str(item["trading_date"]), net_return))
    return selected


def evaluate_params(
    version_id: str,
    rows: Sequence[Mapping[str, object]],
    params: AdaptiveParams,
    config: LearningConfig,
) -> EvaluationMetrics:
    selected = select_signals(rows, params, config.primary_horizon_bars)
    returns = [item[2] for item in selected]
    days = len({item[1] for item in selected})
    expectancy = float(np.mean(returns)) if returns else 0.0
    win_rate = float(np.mean(np.asarray(returns) > 0)) if returns else 0.0
    profit_factor = _profit_factor(returns)
    max_dd = _max_drawdown(returns)
    losses = [value for value in returns if value < 0]
    downside = float(np.mean(losses)) if losses else 0.0

    # 组合分只用于候选排序，晋级仍需逐项硬门槛。
    pf_component = min(profit_factor, 3.0) / 3.0
    composite = (
        expectancy * 10_000 * 0.50
        + win_rate * 100 * 0.20
        + pf_component * 100 * 0.15
        - max_dd * 10_000 * 0.15
    )
    return EvaluationMetrics(
        version_id=version_id,
        sample_count=len(returns),
        trading_days=days,
        expectancy=expectancy,
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown=max_dd,
        downside_mean=downside,
        composite_score=float(composite),
        accepted_signal_ids=tuple(item[0] for item in selected),
        returns=tuple(returns),
    )


def paired_bootstrap_probability(
    champion: EvaluationMetrics,
    challenger: EvaluationMetrics,
    iterations: int = 600,
    seed: int = 20260710,
) -> float:
    champion_map = dict(zip(champion.accepted_signal_ids, champion.returns))
    challenger_map = dict(zip(challenger.accepted_signal_ids, challenger.returns))
    shared = sorted(set(champion_map).intersection(challenger_map))
    if len(shared) < 15:
        # 没有足够配对样本时采用保守的非配对bootstrap。
        if not champion.returns or not challenger.returns:
            return 0.0
        rng = np.random.default_rng(seed)
        better = 0
        c = np.asarray(champion.returns)
        h = np.asarray(challenger.returns)
        for _ in range(iterations):
            delta = rng.choice(h, len(h), replace=True).mean() - rng.choice(c, len(c), replace=True).mean()
            better += int(delta > 0)
        return better / iterations

    diffs = np.asarray([challenger_map[key] - champion_map[key] for key in shared])
    rng = np.random.default_rng(seed)
    better = 0
    for _ in range(iterations):
        better += int(rng.choice(diffs, len(diffs), replace=True).mean() > 0)
    return better / iterations
