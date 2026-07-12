"""Small, account-isolated coordinator for continuous Smart-T shadow training."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
_WORKERS: dict[str, threading.Thread] = {}


def _now() -> datetime:
    return datetime.now()


def _market_time(now: datetime | None = None) -> bool:
    now = now or _now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 925 <= hm <= 1505


def _default_state() -> dict[str, Any]:
    return {
        "ok": True,
        "enabled": False,
        "paused": False,
        "running": False,
        "profile": "quantbrain",
        "promotionMode": "manual",
        "nextRunAt": "",
        "lastRunAt": "",
        "runStartedAt": "",
        "phase": "idle",
        "progress": 0,
        "batch": {},
        "lastResult": {},
        "events": [],
        "message": "持续训练尚未启动",
        "agents": {
            "training": {"label": "训练兔", "state": "idle", "message": "等待后台复盘"},
            "challenger": {"label": "挑战兔", "state": "idle", "message": "等待候选参数"},
            "official": {"label": "正式兔", "state": "ready", "message": "仅使用已晋升冠军"},
            "risk": {"label": "风控兔", "state": "ready", "message": "自动监控异常与回退"},
        },
    }


def _state_path(core, email: str) -> Path:
    return core.user_data_path(email, "four_rabbits_status.json")


def _read(core, email: str) -> dict[str, Any]:
    state = _default_state()
    try:
        saved = json.loads(_state_path(core, email).read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            state.update(saved)
            state["agents"] = {**_default_state()["agents"], **dict(saved.get("agents") or {})}
    except Exception:
        pass
    state["ok"] = True
    return state


def _write(core, email: str, state: dict[str, Any]) -> dict[str, Any]:
    path = _state_path(core, email)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return state


def _update_agent(state: dict[str, Any], name: str, status: str, message: str, **metrics: Any) -> None:
    agent = dict(state["agents"].get(name) or {})
    agent.update({"state": status, "message": message, "updatedAt": _now().isoformat(timespec="seconds")})
    if metrics:
        agent["metrics"] = metrics
    state["agents"][name] = agent


def _set_phase(state: dict[str, Any], phase: str, progress: int, message: str) -> None:
    """Persist truthful coarse-grained progress around the blocking replay."""
    state.update({"phase": phase, "progress": max(0, min(100, int(progress))), "message": message})
    events = list(state.get("events") or [])
    events.append({"time": _now().isoformat(timespec="seconds"), "phase": phase, "message": message})
    state["events"] = events[-12:]


def status(core, email: str) -> dict[str, Any]:
    with _LOCK:
        state = _read(core, email)
        thread = _WORKERS.get(email)
        state["workerAlive"] = bool(thread and thread.is_alive())
        if state.get("running") and state.get("runStartedAt"):
            try:
                state["elapsedSeconds"] = max(0, int((_now() - datetime.fromisoformat(state["runStartedAt"])).total_seconds()))
            except (TypeError, ValueError):
                state["elapsedSeconds"] = 0
        return state


def run_once(core, email: str, force: bool = False) -> dict[str, Any]:
    with _LOCK:
        state = _read(core, email)
        if state.get("running"):
            return {**state, "ok": False, "message": "训练批次正在运行，请勿重复提交"}
        if _market_time() and not force:
            state["message"] = "盘中保护：训练兔暂停，收盘后再运行"
            _update_agent(state, "training", "idle", state["message"])
            return _write(core, email, state)
        started = _now()
        state["running"] = True
        state["runStartedAt"] = started.isoformat(timespec="seconds")
        state["batch"] = {"id": started.strftime("%Y%m%d-%H%M%S"), "days": 5, "sample": 10, "profile": "量化学习"}
        _set_phase(state, "replaying", 20, "正在获取10只股票并进行近5日影子回放")
        _update_agent(state, "training", "running", state["message"], days=5, sample=10)
        _write(core, email, state)

    token = core.REQUEST_EMAIL.set(email)
    try:
        result = core.run_task("simulate5", {
            "sample": 10,
            "cash": 100000,
            "trade": 20000,
            "days": 5,
            "random": True,
            "simMode": "strict",
            "smartTProfile": "quantbrain",
            "baseShares": 6000,
            "shadowTraining": True,
        })
        with _LOCK:
            state = _read(core, email)
            stats = dict(result.get("stats") or {})
            adaptive = dict(stats.get("adaptiveLearning") or {})
            started_text = str(state.get("runStartedAt") or "")
            try:
                duration = max(0, int((_now() - datetime.fromisoformat(started_text)).total_seconds()))
            except (TypeError, ValueError):
                duration = 0
            state["lastResult"] = {
                "tested": len(result.get("stocks") or []),
                "trigger": stats.get("trigger") or "--",
                "winRate": stats.get("win") or "--",
                "pnl": stats.get("pnl") or "--",
                "fees": stats.get("fees") or "--",
                "signals": adaptive.get("recordedSignals", 0),
                "trades": adaptive.get("recordedTrades", 0),
                "skipped": adaptive.get("skippedPriceOnly", 0),
                "durationSeconds": duration,
            }
            if result.get("ok"):
                win_rate = str(stats.get("win") or "--")
                trigger = str(stats.get("trigger") or "--")
                triggered, _total = core.parse_trigger(trigger)
                if triggered <= 0:
                    result_message = f"影子复盘完成：{trigger}，本轮没有满足条件的交易，不计为亏损"
                elif win_rate.startswith("0"):
                    result_message = f"影子复盘完成：触发 {trigger}，本轮有成交但没有盈利，候选不得晋升"
                else:
                    result_message = f"影子复盘完成：触发 {trigger}，胜率 {win_rate}"
                _update_agent(
                    state, "training", "success", result_message,
                    signals=adaptive.get("recordedSignals", 0), trades=adaptive.get("recordedTrades", 0),
                )
                proposal = dict(adaptive.get("proposal") or {})
                if proposal and proposal.get("version_id"):
                    _update_agent(state, "challenger", "pending", "发现候选版本，等待人工晋升", version=proposal.get("version_id"))
                else:
                    _update_agent(state, "challenger", "idle", "本批次未产生更优候选")
                profile = core.profile_status(core.profile_learning_path(email, "quantbrain"), "quantbrain")
                champion = dict(profile.get("champion") or {})
                _update_agent(state, "official", "ready", "正式策略保持冠军版本", version=champion.get("version_id", "默认"))
                try:
                    from adaptive_profiles import _service

                    service = _service(core.profile_learning_path(email, "quantbrain"), "quantbrain")
                    risk = dict(service.monitor_and_rollback() or {})
                    if risk.get("rolledBack"):
                        _update_agent(state, "risk", "warning", "检测到退化，已自动回退", **risk)
                    else:
                        _update_agent(state, "risk", "ready", "风险检查通过", **risk)
                except Exception as exc:
                    _update_agent(state, "risk", "warning", f"风险检查暂不可用：{exc}")
                _set_phase(state, "completed", 100, f"{result_message}，净盈亏 {stats.get('pnl') or '--'}")
            else:
                _update_agent(state, "training", "error", result.get("summary") or "训练失败")
                _set_phase(state, "error", 100, result.get("summary") or "训练失败")
            state["lastRunAt"] = _now().isoformat(timespec="seconds")
            state["nextRunAt"] = (_now() + timedelta(minutes=60)).isoformat(timespec="seconds")
            state["running"] = False
            return _write(core, email, state)
    except Exception as exc:
        with _LOCK:
            state = _read(core, email)
            state["running"] = False
            _set_phase(state, "error", 100, f"训练失败：{exc}")
            _update_agent(state, "training", "error", state["message"])
            return _write(core, email, state)
    finally:
        core.REQUEST_EMAIL.reset(token)


def _worker(core, email: str) -> None:
    while True:
        with _LOCK:
            state = _read(core, email)
        if not state.get("enabled"):
            break
        if not state.get("paused") and not _market_time():
            due_text = str(state.get("nextRunAt") or "")
            try:
                due = datetime.fromisoformat(due_text) if due_text else _now()
            except ValueError:
                due = _now()
            if _now() >= due:
                run_once(core, email)
        time.sleep(30)


def control(core, email: str, action: str) -> dict[str, Any]:
    action = str(action or "status")
    if action == "run":
        threading.Thread(target=run_once, args=(core, email, True), daemon=True, name=f"four-rabbits-run-{email}").start()
        return {**status(core, email), "message": "训练任务已提交"}
    if action == "promote":
        result = core.promote_profile(core.profile_learning_path(email, "quantbrain"), "quantbrain")
        with _LOCK:
            state = _read(core, email)
            _update_agent(state, "official", "ready" if result.get("ok") else "warning", result.get("message") or ("候选已晋升" if result.get("ok") else "当前没有可晋升候选"))
            _write(core, email, state)
        return {**status(core, email), "promotion": result}
    with _LOCK:
        state = _read(core, email)
        if action == "start":
            state["enabled"] = True
            state["paused"] = False
            state["nextRunAt"] = state.get("nextRunAt") or _now().isoformat(timespec="seconds")
            state["message"] = "持续训练已启动，盘中自动暂停"
        elif action == "pause":
            state["paused"] = True
            state["message"] = "持续训练已暂停"
        elif action == "resume":
            state["enabled"] = True
            state["paused"] = False
            state["message"] = "持续训练已恢复"
        elif action == "stop":
            state["enabled"] = False
            state["paused"] = False
            state["message"] = "持续训练已停止"
        _write(core, email, state)
        if state.get("enabled") and not (_WORKERS.get(email) and _WORKERS[email].is_alive()):
            thread = threading.Thread(target=_worker, args=(core, email), daemon=True, name=f"four-rabbits-{email}")
            _WORKERS[email] = thread
            thread.start()
        return status(core, email)
