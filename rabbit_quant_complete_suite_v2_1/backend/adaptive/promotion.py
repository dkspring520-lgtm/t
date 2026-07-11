from __future__ import annotations

from datetime import datetime
from typing import Optional

from .evaluator import paired_bootstrap_probability
from .models import EvaluationMetrics, LearningConfig, PromotionDecision


def decide_promotion(
    champion: EvaluationMetrics,
    challenger: EvaluationMetrics,
    config: LearningConfig,
) -> PromotionDecision:
    probability = paired_bootstrap_probability(
        champion,
        challenger,
        iterations=config.bootstrap_iterations,
    )
    reasons = []
    if challenger.sample_count < config.min_labeled_signals:
        reasons.append("挑战版有效样本不足")
    if challenger.trading_days < config.min_shadow_days:
        reasons.append("影子观察交易日不足")
    if challenger.expectancy < champion.expectancy + config.required_expectancy_improvement:
        reasons.append("净期望改善不足")
    base_score = max(abs(champion.composite_score), 1.0)
    score_improvement = (challenger.composite_score - champion.composite_score) / base_score
    if score_improvement < config.required_composite_improvement:
        reasons.append("综合评分改善不足")
    if challenger.win_rate < champion.win_rate - config.max_win_rate_drop:
        reasons.append("胜率下降过多")
    allowed_dd = max(champion.max_drawdown * config.max_drawdown_worsening_ratio, 0.003)
    if challenger.max_drawdown > allowed_dd:
        reasons.append("最大回撤恶化")
    if champion.sample_count > 0:
        ratio = challenger.sample_count / champion.sample_count
        if ratio < config.min_trade_count_ratio or ratio > config.max_trade_count_ratio:
            reasons.append("信号数量变化过大")
    if probability < config.bootstrap_probability_threshold:
        reasons.append("bootstrap置信度不足")

    approved = not reasons
    reason = "验证通过，可晋级" if approved else "；".join(reasons)
    return PromotionDecision(
        approved=approved,
        reason=reason,
        champion=champion,
        challenger=challenger,
        bootstrap_probability=probability,
    )
