"""Deterministic market-radar scoring for the Rabbit Quant dashboard.

The realtime quote loop must stay fast and predictable, so this module uses
only the already-fetched watchlist snapshot.  It deliberately has no network
or LLM dependency.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import math
from typing import Iterable


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _state(score: int) -> tuple[str, str, str]:
    if score >= 80:
        return "牛市过热", "warning", "市场很强，但追高风险正在增大"
    if score >= 60:
        return "牛市偏强", "strong", "市场结构偏强，回踩确认优先"
    if score >= 40:
        return "震荡观察", "neutral", "多空分化，等待量价形成共振"
    return "市场偏弱", "weak", "优先控制仓位，避免逆势抢反弹"


def _metric_light(ratio: float) -> str:
    if ratio >= 0.72:
        return "green"
    if ratio >= 0.45:
        return "yellow"
    return "red"


def calculate_market_radar(stocks: Iterable[dict]) -> dict:
    """Return a stable 0-100 radar summary from a broad or degraded snapshot."""

    rows = [row for row in stocks if _number(row.get("price")) > 0]
    changes = [_clamp(_number(row.get("change")), -10.0, 10.0) for row in rows]
    if not rows:
        trend = funds = breadth = 0
    else:
        sorted_changes = sorted(changes)
        avg_change = sum(changes) / len(changes)
        median_change = sorted_changes[len(sorted_changes) // 2]
        positive_ratio = sum(change > 0 for change in changes) / len(changes)
        strong_up_ratio = sum(change >= 2.0 for change in changes) / len(changes)
        strong_down_ratio = sum(change <= -2.0 for change in changes) / len(changes)
        active_ratio = sum(abs(change) >= 0.8 for change in changes) / len(changes)
        confirmed_ratio = sum(
            bool(row.get("strictSignal"))
            or any(word in str(row.get("signal") or "") for word in ("确认", "低吸", "高抛", "买入", "卖出"))
            for row in rows
        ) / len(rows)

        amounts = [max(0.0, _number(row.get("amount"))) for row in rows]
        total_amount = sum(amounts)
        advancing_amount = sum(amount for amount, change in zip(amounts, changes) if change > 0)
        advancing_amount_ratio = advancing_amount / total_amount if total_amount > 0 else positive_ratio
        trend = round(_clamp(20 + avg_change * 4 + median_change * 4 + (positive_ratio - 0.5) * 12, 0, 40))
        funds = round(_clamp(6 + active_ratio * 10 + advancing_amount_ratio * 10 + confirmed_ratio * 4, 0, 30))
        breadth = round(_clamp(positive_ratio * 24 + (strong_up_ratio - strong_down_ratio) * 12 + 3, 0, 30))

    score = int(_clamp(trend + funds + breadth, 0, 100))
    label, tone, conclusion = _state(score)
    metrics = [
        {"key": "trend", "name": "趋势", "score": trend, "max": 40, "light": _metric_light(trend / 40 if trend else 0)},
        {"key": "funds", "name": "活跃度", "score": funds, "max": 30, "light": _metric_light(funds / 30 if funds else 0)},
        {"key": "breadth", "name": "赚钱效应", "score": breadth, "max": 30, "light": _metric_light(breadth / 30 if breadth else 0)},
    ]
    return {
        "ok": True,
        "score": score,
        "status": label,
        "tone": tone,
        "conclusion": conclusion,
        "metrics": metrics,
        "sampleSize": len(rows),
        "breadth": {
            "up": sum(change > 0 for change in changes),
            "flat": sum(change == 0 for change in changes),
            "down": sum(change < 0 for change in changes),
            "upRatio": round(sum(change > 0 for change in changes) / len(changes) * 100.0, 1) if changes else 0.0,
            "medianChange": round(sorted(changes)[len(changes) // 2], 3) if changes else 0.0,
        },
    }


def update_radar_history(payload: dict, path: Path, keep_days: int = 7) -> dict:
    """Persist at most one score per day and add yesterday/trend fields."""

    try:
        history = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(history, list):
            history = []
    except Exception:
        history = []

    today = date.today().isoformat()
    point = {"date": today, "score": int(payload.get("score") or 0), "status": str(payload.get("status") or "")}
    history = [item for item in history if isinstance(item, dict) and item.get("date") != today]
    history.append(point)
    history = sorted(history, key=lambda item: str(item.get("date") or ""))[-max(2, keep_days):]

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    except OSError:
        pass

    yesterday = history[-2]["score"] if len(history) > 1 else None
    change = payload["score"] - yesterday if yesterday is not None else None
    result = dict(payload)
    result.update({"history": history, "yesterdayScore": yesterday, "dayChange": change})
    return result
