from __future__ import annotations

from dataclasses import replace
from itertools import product
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .evaluator import evaluate_params
from .guardrails import bounded_update, clamp_params
from .models import AdaptiveParams, EvaluationMetrics, LearningConfig


def split_train_validation(rows: Sequence[Mapping[str, object]], ratio: float = 0.70):
    dates = sorted({str(item["trading_date"]) for item in rows})
    if len(dates) < 2:
        return list(rows), []
    cut = max(1, min(len(dates) - 1, int(len(dates) * ratio)))
    train_dates = set(dates[:cut])
    train = [item for item in rows if str(item["trading_date"]) in train_dates]
    validation = [item for item in rows if str(item["trading_date"]) not in train_dates]
    return train, validation


def guided_candidates(
    base: AdaptiveParams,
    rows: Sequence[Mapping[str, object]],
    config: LearningConfig,
) -> List[AdaptiveParams]:
    """只在稳定参数附近做小步搜索，防止每天大幅漂移。"""
    candidates = {tuple(sorted(base.to_dict().items())): base}
    steps = [
        {"confirmed_score": 2.0},
        {"confirmed_score": -2.0},
        {"cooldown_bars": 1},
        {"cooldown_bars": -1},
        {"strong_trend_extra_score": 2.0},
        {"strong_trend_extra_score": -2.0},
        {"min_volume_ratio": 0.05},
        {"min_volume_ratio": -0.05},
        {"zone_memory_bars": 1},
        {"zone_memory_bars": -1},
        {"confirmed_score": 2.0, "cooldown_bars": 1},
        {"strong_trend_extra_score": 2.0, "min_volume_ratio": 0.05},
        {"confirmed_score": -2.0, "cooldown_bars": -1},
    ]
    for changes in steps:
        candidate = bounded_update(base, changes, config)
        candidates[tuple(sorted(candidate.to_dict().items()))] = candidate
    return list(candidates.values())


def choose_challenger(
    champion_version: str,
    base: AdaptiveParams,
    rows: Sequence[Mapping[str, object]],
    config: LearningConfig,
) -> Tuple[AdaptiveParams, EvaluationMetrics, EvaluationMetrics]:
    train, validation = split_train_validation(rows)
    if not validation:
        raise ValueError("交易日不足，无法建立样本外验证区间")

    candidates = guided_candidates(base, train, config)
    train_ranked = []
    for index, candidate in enumerate(candidates):
        metrics = evaluate_params(f"candidate-{index}", train, candidate, config)
        train_ranked.append((metrics.composite_score, candidate))
    train_ranked.sort(key=lambda item: item[0], reverse=True)

    # 只让训练集排名前5的候选进入验证集，降低多重试验过拟合。
    finalists = train_ranked[:5]
    validation_ranked = []
    for index, (_, candidate) in enumerate(finalists):
        metrics = evaluate_params(f"validation-{index}", validation, candidate, config)
        validation_ranked.append((metrics.composite_score, candidate, metrics))
    validation_ranked.sort(key=lambda item: item[0], reverse=True)

    best_params = validation_ranked[0][1]
    champion_metrics = evaluate_params(champion_version, validation, base, config)
    challenger_metrics = validation_ranked[0][2]
    return best_params, champion_metrics, challenger_metrics
