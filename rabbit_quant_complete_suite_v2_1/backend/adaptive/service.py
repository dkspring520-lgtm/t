from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .evaluator import evaluate_params
from .guardrails import clamp_params
from .labeler import label_pending_signals
from .models import (
    AdaptiveParams,
    LearningConfig,
    LearningRunResult,
    PromotionDecision,
)
from .optimizer import choose_challenger
from .promotion import decide_promotion
from .recorder import record_signal_frame, record_trade_frame
from .storage import LearningStore


class AdaptiveLearningService:
    """兔兔策略成长总控。

    盘中：只记录。
    收盘后：补标签。
    每周：生成挑战版。
    影子期后：验证、人工确认或自动晋级。
    实盘异常：可自动回滚到上一稳定版本。
    """

    def __init__(
        self,
        config: Optional[LearningConfig] = None,
        initial_params: Optional[AdaptiveParams] = None,
    ):
        self.config = config or LearningConfig()
        self.store = LearningStore(self.config.database_path)
        self.store.ensure_initial_champion(initial_params or AdaptiveParams())

    @property
    def champion(self) -> Dict[str, Any]:
        champion = self.store.get_champion()
        if not champion:
            raise RuntimeError("没有可用的正式参数版本")
        return champion

    def current_params(self) -> AdaptiveParams:
        return self.champion["params"]

    def record_intraday(
        self,
        symbol: str,
        signals: pd.DataFrame,
        trades: Optional[pd.DataFrame] = None,
    ) -> Dict[str, int]:
        version = self.champion["version_id"]
        signal_count = record_signal_frame(self.store, symbol, signals, version)
        trade_count = record_trade_frame(
            self.store,
            symbol,
            trades if trades is not None else pd.DataFrame(),
            version,
        )
        return {"signals": signal_count, "trades": trade_count}

    def end_of_day(self, symbol: str, minute_df: pd.DataFrame) -> Dict[str, int]:
        labeled = label_pending_signals(self.store, symbol, minute_df, self.config)
        return {"labeledSignals": labeled}

    def create_weekly_challenger(self) -> LearningRunResult:
        if self.config.mode == "off":
            return LearningRunResult("disabled", "学习功能已关闭", self.champion["version_id"])
        existing = self.store.get_challenger()
        if existing:
            return LearningRunResult(
                "waiting_shadow",
                "已有挑战版正在影子测试",
                self.champion["version_id"],
                existing["version_id"],
                proposal=existing["params"].to_dict(),
            )

        rows = self.store.labeled_signals()
        dates = {str(item["trading_date"]) for item in rows}
        if len(rows) < self.config.min_labeled_signals or len(dates) < self.config.min_trading_days:
            return LearningRunResult(
                "insufficient_samples",
                f"样本不足：已标注{len(rows)}个信号、{len(dates)}个交易日",
                self.champion["version_id"],
            )

        champion = self.champion
        best_params, champion_metrics, candidate_metrics = choose_challenger(
            champion["version_id"], champion["params"], rows, self.config
        )
        if best_params == champion["params"]:
            return LearningRunResult(
                "no_change",
                "当前稳定参数仍是样本外表现最好的版本",
                champion["version_id"],
            )

        challenger_id = self.store.create_version(
            best_params,
            status="challenger",
            parent_version=champion["version_id"],
            note="每周收盘后生成，先进入影子模式",
        )
        result = LearningRunResult(
            "challenger_created",
            "已生成挑战版，仅影子运行，不影响正式提醒",
            champion["version_id"],
            challenger_id,
            proposal={
                "old": champion["params"].to_dict(),
                "new": best_params.to_dict(),
                "validationChampion": champion_metrics.to_dict(),
                "validationChallenger": candidate_metrics.to_dict(),
            },
        )
        self.store.save_learning_run(
            "CREATE_CHALLENGER", champion["version_id"], challenger_id, result.to_dict()
        )
        return result

    def review_challenger(self, apply: Optional[bool] = None) -> LearningRunResult:
        challenger = self.store.get_challenger()
        champion = self.champion
        if not challenger:
            return LearningRunResult("no_challenger", "当前没有待验证挑战版", champion["version_id"])

        rows = self.store.labeled_signals()
        # 影子期只评估挑战版创建后的样本。
        shadow_start = str(challenger["shadow_started_at"])[:10]
        shadow_rows = [item for item in rows if str(item["trading_date"]) >= shadow_start]
        champion_metrics = evaluate_params(
            champion["version_id"], shadow_rows, champion["params"], self.config
        )
        challenger_metrics = evaluate_params(
            challenger["version_id"], shadow_rows, challenger["params"], self.config
        )
        decision = decide_promotion(champion_metrics, challenger_metrics, self.config)

        should_apply = apply is True or (apply is None and self.config.mode == "auto")
        auto_applied = False
        if decision.approved and should_apply:
            self.store.promote(challenger["version_id"])
            auto_applied = self.config.mode == "auto" and apply is None
            decision = replace(decision, auto_applied=auto_applied)
            status = "promoted"
            message = "挑战版验证通过，已升级为正式版"
        elif decision.approved:
            status = "approval_required"
            message = "挑战版验证通过，等待你手动确认升级"
        else:
            status = "shadow_continues"
            message = f"挑战版暂未晋级：{decision.reason}"

        result = LearningRunResult(
            status,
            message,
            self.champion["version_id"],
            challenger["version_id"],
            proposal={"params": challenger["params"].to_dict()},
            decision=decision,
        )
        self.store.save_learning_run(
            "REVIEW_CHALLENGER", champion["version_id"], challenger["version_id"], result.to_dict()
        )
        return result

    def monitor_and_rollback(self) -> Dict[str, Any]:
        champion = self.champion
        trades = self.store.recent_trades(
            champion["version_id"], self.config.min_live_trades_for_rollback
        )
        if len(trades) < self.config.min_live_trades_for_rollback:
            return {"rolledBack": False, "reason": "实盘样本不足"}
        returns = [float(item["net_return"]) for item in reversed(trades)]
        expectancy = float(np.mean(returns))
        equity = np.cumprod(1.0 + np.asarray(returns))
        peaks = np.maximum.accumulate(equity)
        max_dd = float(np.max((peaks - equity) / np.maximum(peaks, 1e-12)))
        if expectancy >= self.config.rollback_expectancy_floor and max_dd <= self.config.rollback_max_drawdown:
            return {"rolledBack": False, "expectancy": expectancy, "maxDrawdown": max_dd}

        versions = self.store.list_versions(limit=20)
        fallback = next(
            (
                item
                for item in versions
                if item["version_id"] != champion["version_id"]
                and item["status"] in {"archived", "rolled_back"}
            ),
            None,
        )
        if not fallback:
            return {
                "rolledBack": False,
                "reason": "触发回滚条件，但没有历史稳定版本",
                "expectancy": expectancy,
                "maxDrawdown": max_dd,
            }
        reason = f"近期净期望{expectancy:.4%}，最大回撤{max_dd:.2%}"
        self.store.rollback_to(fallback["version_id"], reason)
        return {
            "rolledBack": True,
            "toVersion": fallback["version_id"],
            "reason": reason,
        }

    def growth_payload(self) -> Dict[str, Any]:
        champion = self.champion
        challenger = self.store.get_challenger()
        counts = self.store.counts()
        recent = self.store.recent_trades(champion["version_id"], 30)
        returns = [float(item["net_return"]) for item in recent]
        win_rate = (
            sum(value > 0 for value in returns) / len(returns) if returns else None
        )
        status = "稳定运行"
        if challenger:
            status = "影子测试中"
        elif counts["labeledSignals"] < self.config.min_labeled_signals:
            status = "积累样本中"
        return {
            "title": "策略成长",
            "currentVersion": champion["version_id"],
            "status": status,
            "mode": self.config.mode,
            "signalsLearned": counts["labeledSignals"],
            "recentTradeCount": len(returns),
            "recentWinRate": None if win_rate is None else round(win_rate * 100, 1),
            "challengerVersion": None if not challenger else challenger["version_id"],
            "championParams": champion["params"].to_dict(),
            "message": (
                "挑战版正在模拟验证，不影响正式信号"
                if challenger
                else "盘中只记录，收盘后学习；未经验证不会修改正式参数"
            ),
        }
