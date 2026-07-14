from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
MIN_TRADING_DAYS = 20
MAX_TRADING_DAYS = 60
MIN_COMPLETED_CYCLES = 30
MIN_MARKET_REGIMES = 2


def _now() -> datetime:
    return datetime.now()


def _default_state() -> dict[str, Any]:
    return {
        "ok": True,
        "enabled": False,
        "paused": False,
        "contaminated": False,
        "startedAt": "",
        "stoppedAt": "",
        "strategyFingerprint": "",
        "currentFingerprint": "",
        "strategyFiles": {},
        "observedDates": [],
        "cycles": [],
        "regimes": [],
        "criteria": {
            "minTradingDays": MIN_TRADING_DAYS,
            "maxTradingDays": MAX_TRADING_DAYS,
            "minCompletedCycles": MIN_COMPLETED_CYCLES,
            "minMarketRegimes": MIN_MARKET_REGIMES,
        },
        "verdict": "NOT_STARTED",
        "message": "尚未开始前向模拟观察",
        "updatedAt": "",
    }


def _path(core: Any, email: str) -> Path:
    return core.user_data_path(email, "paper_observation.json")


def _read(core: Any, email: str) -> dict[str, Any]:
    state = _default_state()
    try:
        saved = json.loads(_path(core, email).read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            state.update(saved)
            state["criteria"] = {**_default_state()["criteria"], **dict(saved.get("criteria") or {})}
    except Exception:
        pass
    state["ok"] = True
    return state


def _write(core: Any, email: str, state: dict[str, Any]) -> dict[str, Any]:
    path = _path(core, email)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    state["updatedAt"] = _now().isoformat(timespec="seconds")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return state


def _strategy_paths(core: Any, email: str) -> list[Path]:
    paths = [
        core.BASE_DIR / "smart_t_policy.py",
        core.BASE_DIR / "auction_direction.py",
        core.BASE_DIR / "stock_t_signal.py",
        core.BASE_DIR / "services" / "trade_engine.py",
        core.account_strategy_path(email),
    ]
    for profile in ("steady", "balanced", "sensitive", "quantbrain"):
        paths.append(core.account_profile_strategy_path(email, profile))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _fingerprint(core: Any, email: str) -> tuple[str, dict[str, str]]:
    files: dict[str, str] = {}
    digest = hashlib.sha256()
    for path in _strategy_paths(core, email):
        if not path.is_file():
            continue
        data = path.read_bytes()
        rel = str(path.relative_to(core.BASE_DIR)).replace("\\", "/")
        file_digest = hashlib.sha256(data).hexdigest()
        files[rel] = file_digest
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest(), files


def _after_start(item: dict[str, Any], started_at: str) -> bool:
    date_text = str(item.get("date") or "")[:10]
    time_text = str(item.get("time") or "00:00")[:5]
    try:
        return datetime.fromisoformat(f"{date_text}T{time_text}:00") >= datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return False


def _cycle_key(item: dict[str, Any]) -> str:
    payload = "|".join(str(item.get(key) or "") for key in (
        "date", "code", "direction", "openTime", "closeTime", "shares", "buyPrice", "sellPrice",
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _metrics(state: dict[str, Any]) -> dict[str, Any]:
    cycles = [item for item in list(state.get("cycles") or []) if isinstance(item, dict)]
    wins = sum(1 for item in cycles if float(item.get("net") or 0) > 0)
    losses = len(cycles) - wins
    net = round(sum(float(item.get("net") or 0) for item in cycles), 2)
    gross_profit = sum(max(0.0, float(item.get("net") or 0)) for item in cycles)
    gross_loss = abs(sum(min(0.0, float(item.get("net") or 0)) for item in cycles))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    observed_dates = sorted({str(value)[:10] for value in list(state.get("observedDates") or []) if value})
    regimes = sorted({str(value) for value in list(state.get("regimes") or []) if value and value != "OBSERVE"})
    return {
        "tradingDays": len(observed_dates),
        "completedCycles": len(cycles),
        "wins": wins,
        "losses": losses,
        "winRate": round(wins / len(cycles) * 100.0, 1) if cycles else 0.0,
        "netPnl": net,
        "averageNet": round(net / len(cycles), 2) if cycles else 0.0,
        "profitFactor": profit_factor,
        "marketRegimes": regimes,
        "marketRegimeCount": len(regimes),
    }


def _evaluate(state: dict[str, Any]) -> None:
    metrics = _metrics(state)
    state["metrics"] = metrics
    if state.get("contaminated"):
        state["verdict"] = "CONTAMINATED"
        state["message"] = "观察期内策略发生变化，样本已污染；需冻结新版本后重新开始"
        return
    if not state.get("enabled"):
        state["verdict"] = "STOPPED" if state.get("startedAt") else "NOT_STARTED"
        return
    if state.get("paused"):
        state["verdict"] = "PAUSED"
        state["message"] = "前向模拟观察已暂停"
        return
    days = metrics["tradingDays"]
    cycles = metrics["completedCycles"]
    regimes = metrics["marketRegimeCount"]
    enough = days >= MIN_TRADING_DAYS and cycles >= MIN_COMPLETED_CYCLES and regimes >= MIN_MARKET_REGIMES
    if not enough and days < MAX_TRADING_DAYS:
        state["verdict"] = "COLLECTING"
        state["message"] = (
            f"只记录未参与调参的新样本：{days}/{MIN_TRADING_DAYS}个交易日，"
            f"{cycles}/{MIN_COMPLETED_CYCLES}个闭环，{regimes}/{MIN_MARKET_REGIMES}种市场状态"
        )
        return
    passes = metrics["netPnl"] > 0 and metrics["profitFactor"] >= 1.2 and metrics["winRate"] >= 50.0
    if enough and passes:
        state["verdict"] = "READY_FOR_REVIEW"
        state["message"] = "样本量与收益质量达到人工评审门槛；仅建议评审，不会自动提交默认策略"
    elif days >= MAX_TRADING_DAYS:
        state["verdict"] = "DO_NOT_PROMOTE"
        state["message"] = "已达到最长观察期但未通过收益质量门槛，不建议提交默认策略"
    else:
        state["verdict"] = "KEEP_OBSERVING"
        state["message"] = "样本量已达到最低要求，但跨样本收益质量不足，继续观察至60个交易日"


def status(core: Any, email: str) -> dict[str, Any]:
    with _LOCK:
        state = _read(core, email)
        current, _files = _fingerprint(core, email)
        state["currentFingerprint"] = current
        if state.get("enabled") and state.get("strategyFingerprint") and current != state.get("strategyFingerprint"):
            state["contaminated"] = True
            state["paused"] = True
        _evaluate(state)
        return _write(core, email, state)


def record(core: Any, email: str, auto_state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Accumulate forward-only paper observations; never tune or promote a strategy."""
    if not email:
        return
    with _LOCK:
        state = _read(core, email)
        if not state.get("enabled") or state.get("paused") or state.get("contaminated"):
            return
        current, _files = _fingerprint(core, email)
        state["currentFingerprint"] = current
        if current != state.get("strategyFingerprint"):
            state["contaminated"] = True
            state["paused"] = True
            _evaluate(state)
            _write(core, email, state)
            return

        now = _now()
        fresh_rows = [row for row in rows if isinstance(row, dict) and not row.get("quoteStale") and row.get("price")]
        hm = now.hour * 100 + now.minute
        if now.weekday() < 5 and 925 <= hm <= 1505 and fresh_rows:
            dates = set(state.get("observedDates") or [])
            dates.add(now.strftime("%Y-%m-%d"))
            state["observedDates"] = sorted(dates)

        started_at = str(state.get("startedAt") or "")
        trades = [
            item for item in list(auto_state.get("trades") or [])
            if isinstance(item, dict) and _after_start(item, started_at)
        ]
        trades_by_date: dict[str, list[dict[str, Any]]] = {}
        for trade in trades:
            trade_date = str(trade.get("date") or "")[:10]
            if trade_date:
                trades_by_date.setdefault(trade_date, []).append(trade)
        existing = {str(item.get("key") or "") for item in list(state.get("cycles") or []) if isinstance(item, dict)}
        cycles = list(state.get("cycles") or [])
        for trade_date, daily_trades in sorted(trades_by_date.items()):
            paired, _open = core._pair_trade_cycles(daily_trades)
            for item in paired:
                item = {"date": trade_date, **item}
                key = _cycle_key(item)
                if key not in existing:
                    cycles.append({"key": key, **item})
                    existing.add(key)
        state["cycles"] = cycles[-2000:]

        regimes = set(state.get("regimes") or [])
        for item in list(auto_state.get("decisionAudit") or []):
            if isinstance(item, dict) and _after_start(item, started_at):
                regime = str(item.get("regime") or "")
                if regime:
                    regimes.add(regime)
        state["regimes"] = sorted(regimes)
        _evaluate(state)
        _write(core, email, state)


def control(core: Any, email: str, action: str) -> dict[str, Any]:
    action = str(action or "status")
    with _LOCK:
        state = _read(core, email)
        if action in {"start", "restart"}:
            fingerprint, files = _fingerprint(core, email)
            state = _default_state()
            state.update({
                "enabled": True,
                "paused": False,
                "startedAt": _now().isoformat(timespec="seconds"),
                "strategyFingerprint": fingerprint,
                "currentFingerprint": fingerprint,
                "strategyFiles": files,
                "verdict": "COLLECTING",
                "message": "已冻结当前策略，只累计启动后的前向模拟样本",
            })
            # Historical replay can create candidates and contaminate the holdout.
            try:
                from services.four_rabbits import control as control_training

                control_training(core, email, "pause")
            except Exception:
                pass
        elif action == "pause":
            state["paused"] = True
        elif action == "resume":
            current, _files = _fingerprint(core, email)
            if current != state.get("strategyFingerprint"):
                state["contaminated"] = True
                state["paused"] = True
            else:
                state["enabled"] = True
                state["paused"] = False
        elif action == "stop":
            state["enabled"] = False
            state["paused"] = False
            state["stoppedAt"] = _now().isoformat(timespec="seconds")
        _evaluate(state)
        return _write(core, email, state)
