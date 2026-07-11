"""Profile-isolated, manual-promotion wrapper around the repaired V2.1 learner."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SUITE_DIR = Path(__file__).resolve().parent / "rabbit_quant_complete_suite_v2_1"
if str(SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(SUITE_DIR))


def _dependencies():
    import pandas as pd
    from backend.adaptive.models import AdaptiveParams, LearningConfig
    from backend.adaptive.service import AdaptiveLearningService

    return pd, AdaptiveParams, LearningConfig, AdaptiveLearningService


def _service(database_path: Path, profile: str):
    _, AdaptiveParams, LearningConfig, AdaptiveLearningService = _dependencies()
    presets = {
        "steady": AdaptiveParams(confirmed_score=86, watch_score=55, candidate_score=72, cooldown_bars=7, min_expected_net_rate=0.0050, min_volume_ratio=0.25),
        "balanced": AdaptiveParams(),
        "sensitive": AdaptiveParams(confirmed_score=78, watch_score=48, candidate_score=64, cooldown_bars=3, min_expected_net_rate=0.0025, min_volume_ratio=0.15),
        "quantbrain": AdaptiveParams(confirmed_score=82, watch_score=50, candidate_score=66, cooldown_bars=5, zone_memory_bars=8, min_expected_net_rate=0.0035, strong_trend_extra_score=6.0, min_volume_ratio=0.18),
    }
    config = LearningConfig(
        mode="manual",
        database_path=str(database_path),
        min_labeled_signals=30,
        min_trading_days=5,
        min_shadow_days=3,
        min_live_trades_for_rollback=20,
    )
    return AdaptiveLearningService(config, presets.get(profile, presets["balanced"]))


def _bar_frame(row: dict):
    pd, _, _, _ = _dependencies()
    date = datetime.now().strftime("%Y-%m-%d")
    records = []
    index = []
    previous_volume = 0.0
    for item in row.get("prices") or []:
        time_text = str(item.get("time") or "")[:5]
        price = float(item.get("price") or 0)
        if len(time_text) != 5 or price <= 0:
            continue
        total_volume = float(item.get("volume") or 0)
        minute_volume = max(0.0, total_volume - previous_volume) if total_volume >= previous_volume else max(0.0, total_volume)
        previous_volume = total_volume
        index.append(pd.Timestamp(f"{date} {time_text}"))
        records.append({"open": price, "high": price, "low": price, "close": price, "volume": minute_volume})
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records, index=pd.DatetimeIndex(index))
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def record_profile_run(database_path: Path, profile: str, stocks: list[dict]) -> dict:
    """Record one simulation as shadow-learning evidence; never auto-promote."""
    pd, _, _, _ = _dependencies()
    service = _service(database_path, profile)
    recorded_signals = recorded_trades = labeled = 0
    today = datetime.now().strftime("%Y-%m-%d")
    expanded_rows = []
    for row in stocks:
        cycles = row.get("cycles") if isinstance(row.get("cycles"), list) else []
        if cycles:
            expanded_rows.extend({**row, **cycle} for cycle in cycles)
        else:
            expanded_rows.append(row)
    for row in expanded_rows:
        action = str(row.get("action") or "")
        if not action or action == "未触发":
            continue
        direction = "SELL_FIRST" if action.startswith("反T") else "BUY_FIRST"
        entry_time = str(row.get("sellTime") if direction == "SELL_FIRST" else row.get("buyTime") or "")[:5]
        exit_time = str(row.get("buyTime") if direction == "SELL_FIRST" else row.get("sellTime") or "")[:5]
        entry_price = float(row.get("sellPrice") if direction == "SELL_FIRST" else row.get("buyPrice") or 0)
        exit_price = float(row.get("buyPrice") if direction == "SELL_FIRST" else row.get("sellPrice") or 0)
        bars = _bar_frame(row)
        timestamp = pd.Timestamp(f"{today} {entry_time}") if len(entry_time) == 5 else None
        if timestamp is None or timestamp not in bars.index or entry_price <= 0:
            continue
        history = bars.loc[:timestamp]
        close = history["close"]
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi14 = float((100 - 100 / (1 + rs)).iloc[-1]) if len(history) > 1 else 50.0
        volume = history["volume"].clip(lower=0)
        volume_ma = float(volume.tail(20).mean() or 0)
        volume_ratio = float(volume.iloc[-1] / volume_ma) if volume_ma > 0 else 1.0
        total_volume = float(volume.sum())
        learned_vwap = float((close * volume).sum() / total_volume) if total_volume > 0 else entry_price
        atr14 = float(close.diff().abs().ewm(alpha=1 / 14, adjust=False).mean().iloc[-1] or 0)
        recent = close.tail(20)
        position = float((entry_price - recent.min()) / max(float(recent.max() - recent.min()), 1e-9))
        signal = pd.DataFrame([{
            "close": entry_price,
            "atr14": atr14,
            "vwap": learned_vwap,
            "vwap_atr_z": (entry_price - learned_vwap) / max(atr14, 1e-9),
            "rsi14": rsi14,
            "price_position": position,
            "volume_ratio": volume_ratio,
            "ema5": float(close.ewm(span=5, adjust=False).mean().iloc[-1]),
            "ema13": float(close.ewm(span=13, adjust=False).mean().iloc[-1]),
            "top_score": 85.0 if direction == "SELL_FIRST" else 0.0,
            "bottom_score": 85.0 if direction == "BUY_FIRST" else 0.0,
            "top_trigger": direction == "SELL_FIRST",
            "bottom_trigger": direction == "BUY_FIRST",
            "smart_regime": "SIMULATION",
            "smart_trade_action": "SELL_T" if direction == "SELL_FIRST" else "BUY_T",
        }], index=pd.DatetimeIndex([timestamp]))
        trade_frame = pd.DataFrame()
        if len(exit_time) == 5 and exit_price > 0:
            trade_frame = pd.DataFrame([{
                "date": today,
                "direction": direction,
                "entry_time": f"{today}T{entry_time}:00",
                "exit_time": f"{today}T{exit_time}:00",
                "entry_price": entry_price,
                "exit_price": exit_price,
                "net_return": float(row.get("pnl") or 0) / 100.0,
                "result": "WIN" if float(row.get("pnl") or 0) > 0 else "LOSS",
                "exit_reason": str(row.get("reason") or action),
            }])
        counts = service.record_intraday(str(row.get("code") or "SIM"), signal, trade_frame)
        recorded_signals += counts["signals"]
        recorded_trades += counts["trades"]
        labeled += service.end_of_day(str(row.get("code") or "SIM"), bars)["labeledSignals"]

    proposal = service.create_weekly_challenger()
    review = service.review_challenger(apply=False)
    growth = service.growth_payload()
    growth.update({
        "ok": True,
        "profile": profile,
        "profileLabel": {"steady": "稳健", "balanced": "平衡", "sensitive": "灵敏", "quantbrain": "量化学习"}.get(profile, "平衡"),
        "recordedSignals": recorded_signals,
        "recordedTrades": recorded_trades,
        "labeledSignalsThisRun": labeled,
        "proposal": proposal.to_dict(),
        "review": review.to_dict(),
        "manualPromotionOnly": True,
    })
    return growth


def profile_status(database_path: Path, profile: str) -> dict:
    service = _service(database_path, profile)
    payload = service.growth_payload()
    payload.update({
        "ok": True,
        "profile": profile,
        "profileLabel": {"steady": "稳健", "balanced": "平衡", "sensitive": "灵敏", "quantbrain": "量化学习"}.get(profile, "平衡"),
        "versions": [
            {**item, "params": item["params"].to_dict()}
            for item in service.store.list_versions(limit=20)
        ],
        "manualPromotionOnly": True,
    })
    return payload


def runtime_profile_params(database_path: Path, profile: str) -> dict:
    """Read only the manually promoted champion; never apply a challenger."""
    if profile != "quantbrain" or not database_path.exists():
        return {}
    conn = None
    try:
        conn = sqlite3.connect(database_path)
        row = conn.execute(
            "SELECT version_id,params_json FROM parameter_versions WHERE status='champion' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        params = json.loads(row[1])
        return {**params, "version_id": str(row[0])} if isinstance(params, dict) else {}
    except Exception:
        return {}
    finally:
        if conn is not None:
            conn.close()


def promote_profile(database_path: Path, profile: str) -> dict:
    service = _service(database_path, profile)
    result = service.review_challenger(apply=True)
    payload = result.to_dict()
    payload["ok"] = result.status == "promoted"
    return payload


def rollback_profile(database_path: Path, profile: str, version_id: str = "") -> dict:
    service = _service(database_path, profile)
    versions = service.store.list_versions(limit=20)
    target = next((item for item in versions if item["version_id"] == version_id and item["status"] in {"archived", "rolled_back"}), None)
    if target is None and not version_id:
        target = next((item for item in versions if item["status"] in {"archived", "rolled_back"}), None)
    if target is None:
        return {"ok": False, "message": "没有可回滚的历史稳定版本。"}
    service.store.rollback_to(target["version_id"], "用户在策略设置中手动回滚")
    return {"ok": True, "message": "已回滚到历史稳定版本。", "version": target["version_id"], "status": service.growth_payload()}
