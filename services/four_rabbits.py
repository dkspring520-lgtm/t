"""Small, account-isolated coordinator for continuous Smart-T shadow training."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
_WORKERS: dict[str, threading.Thread] = {}
TRAIN_INTERVAL_MINUTES = 5
WORKER_POLL_SECONDS = 15


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
        "intervalMinutes": TRAIN_INTERVAL_MINUTES,
        "totalBatches": 0,
        "nextRunAt": "",
        "lastRunAt": "",
        "runStartedAt": "",
        "phase": "idle",
        "progress": 0,
        "batch": {},
        "batchManifest": {},
        "lastResult": {},
        "events": [],
        "message": "持续训练尚未启动",
        "agents": {
            "training": {"label": "训练兔", "state": "idle", "message": "等待后台复盘"},
            "challenger": {"label": "挑战兔", "state": "wait", "message": "WAIT｜等待候选参数"},
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
    challenger = dict(state["agents"].get("challenger") or {})
    if challenger.get("state") == "idle" and not dict(state.get("lastResult") or {}).get("promotionEligible"):
        challenger.update({"state": "wait", "message": "WAIT｜本批次未产生可晋升候选"})
        state["agents"]["challenger"] = challenger
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


def _number(value: object) -> float:
    """Parse the compact percentages/money strings returned by the simulator."""
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    try:
        return float(match.group(0)) if match else 0.0
    except (TypeError, ValueError):
        return 0.0


def _stable_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stock_dates(stock: dict[str, Any]) -> list[str]:
    dates: set[str] = set()
    for key in ("date", "tradeDate"):
        text = str(stock.get(key) or "").strip()
        if text:
            dates.add(text[:10])
    for key in ("dailyResults", "cycles", "prices"):
        for item in list(stock.get(key) or []):
            if not isinstance(item, dict):
                continue
            for date_key in ("date", "tradeDate", "tradingDate"):
                text = str(item.get(date_key) or "").strip()
                if text:
                    dates.add(text[:10])
    return sorted(dates)


def _batch_manifest(batch: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Describe exactly what can (and cannot) be reproduced from a shadow run."""
    stats = dict(result.get("stats") or {})
    stocks = [dict(item) for item in list(result.get("stocks") or []) if isinstance(item, dict)]
    selected_stocks = [{
        "code": str(item.get("code") or "").strip(),
        "name": str(item.get("name") or "").strip(),
    } for item in stocks if str(item.get("code") or "").strip()]
    codes = [str(item.get("code") or "").strip() for item in stocks]
    codes = [code for code in codes if code]
    dates = {str(item.get("code") or ""): _stock_dates(item) for item in stocks if item.get("code")}
    evidence = [{
        "code": str(item.get("code") or ""),
        "dates": dates.get(str(item.get("code") or ""), []),
        "prices": item.get("prices") or [],
        "dailyResults": item.get("dailyResults") or [],
        "cycles": item.get("cycles") or [],
        "action": item.get("action"),
        "pnl": item.get("pnl"),
        "money": item.get("money"),
        "detail": item.get("detail"),
    } for item in stocks]
    if not evidence:
        evidence = [{
            "trigger": stats.get("trigger"),
            "win": stats.get("win"),
            "pnl": stats.get("pnl"),
            "fees": stats.get("fees"),
            "summary": result.get("summary"),
        }]
    reported_seed = result.get("seed")
    if reported_seed is None:
        reported_seed = stats.get("seed")
    requested_seed = batch.get("seed")
    seed_applied = reported_seed is not None and str(reported_seed) == str(requested_seed)
    has_market_evidence = any(
        item.get("prices") or item.get("dailyResults") or item.get("cycles")
        for item in evidence
    )
    fingerprint_type = "data" if has_market_evidence else "result"
    limitations: list[str] = []
    if not seed_applied:
        limitations.append("当前随机选股器未回传已应用种子，不能保证精确复跑同一批股票。")
    if not codes:
        limitations.append("任务结果未返回入选股票代码，只能核对汇总结果指纹。")
    if not has_market_evidence:
        limitations.append("任务结果未返回逐分钟行情，只能核对结果，不能还原原始数据。")
    return {
        "batchId": str(batch.get("id") or ""),
        "seed": requested_seed,
        "seedApplied": seed_applied,
        "selectedStocks": selected_stocks,
        "selectedCodes": codes,
        "selectedDates": dates,
        "fingerprintType": fingerprint_type,
        "fingerprint": _stable_digest(evidence),
        "reproducibility": "exact" if seed_applied and codes and has_market_evidence else "limited",
        "limitations": limitations,
    }


def _proposal_version(adaptive: dict[str, Any]) -> str:
    proposal = dict(adaptive.get("proposal") or {})
    # LearningRunResult.to_dict() exposes camelCase challengerVersion.  The old
    # version_id lookup silently hid real challengers from the coordinator.
    return str(proposal.get("challengerVersion") or "").strip()


def status(core, email: str) -> dict[str, Any]:
    with _LOCK:
        state = _read(core, email)
        if state.get("enabled") and not state.get("ownerEmail"):
            state["ownerEmail"] = email
            _write(core, email, state)
        thread = _WORKERS.get(email)
        if state.get("enabled") and not state.get("paused") and not (thread and thread.is_alive()):
            thread = threading.Thread(target=_worker, args=(core, email), daemon=True, name=f"four-rabbits-{email}")
            _WORKERS[email] = thread
            thread.start()
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
        batch_id = started.strftime("%Y%m%d-%H%M%S")
        batch_seed = int(hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:8], 16)
        state["running"] = True
        state["runStartedAt"] = started.isoformat(timespec="seconds")
        state["batch"] = {
            "id": batch_id,
            "seed": batch_seed,
            "days": 5,
            "sample": 10,
            "profile": "量化学习",
        }
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
            # Kept in the manifest even on older simulators that do not yet
            # consume it.  seedApplied remains false until the task echoes it.
            "seed": batch_seed,
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
            manifest = _batch_manifest(dict(state.get("batch") or {}), result)
            state["batchManifest"] = manifest
            state["batch"] = {**dict(state.get("batch") or {}), "manifest": manifest}
            if result.get("ok"):
                win_rate = str(stats.get("win") or "--")
                trigger = str(stats.get("trigger") or "--")
                triggered, _total = core.parse_trigger(trigger)
                net_pnl = _number(stats.get("pnl"))
                if triggered <= 0:
                    result_message = f"影子复盘完成：{trigger}，本轮没有满足条件的交易，不计为亏损"
                elif net_pnl <= 0:
                    result_message = f"影子复盘完成：触发 {trigger}，本轮有成交但没有盈利，候选不得晋升"
                else:
                    result_message = f"影子复盘完成：触发 {trigger}，胜率 {win_rate}"
                _update_agent(
                    state, "training", "success", result_message,
                    signals=adaptive.get("recordedSignals", 0), trades=adaptive.get("recordedTrades", 0),
                )
                proposal_version = _proposal_version(adaptive)
                promotion_eligible = bool(triggered > 0 and net_pnl > 0 and proposal_version)
                state["lastResult"].update({
                    "promotionEligible": promotion_eligible,
                    "promotionStatus": "CANDIDATE" if promotion_eligible else "WAIT",
                    "challengerVersion": proposal_version,
                })
                if promotion_eligible:
                    _update_agent(
                        state,
                        "challenger",
                        "pending",
                        "候选已产生；本轮为正收益，等待人工晋升",
                        version=proposal_version,
                        promotionEligible=True,
                    )
                elif proposal_version and net_pnl <= 0:
                    _update_agent(
                        state,
                        "challenger",
                        "wait",
                        "WAIT｜本轮净收益不为正，现有候选不得晋升",
                        version=proposal_version,
                        promotionEligible=False,
                    )
                else:
                    _update_agent(
                        state,
                        "challenger",
                        "wait",
                        "WAIT｜本批次未产生可晋升候选",
                        promotionEligible=False,
                    )
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
                state["lastResult"].update({"promotionEligible": False, "promotionStatus": "WAIT"})
                _update_agent(state, "training", "error", result.get("summary") or "训练失败")
                _update_agent(
                    state,
                    "challenger",
                    "wait",
                    "WAIT｜本批次失败，没有可晋升候选",
                    promotionEligible=False,
                )
                _set_phase(state, "error", 100, result.get("summary") or "训练失败")
            state["lastRunAt"] = _now().isoformat(timespec="seconds")
            state["totalBatches"] = int(state.get("totalBatches") or 0) + 1
            state["nextRunAt"] = (_now() + timedelta(minutes=TRAIN_INTERVAL_MINUTES)).isoformat(timespec="seconds")
            state["running"] = False
            return _write(core, email, state)
    except Exception as exc:
        with _LOCK:
            state = _read(core, email)
            state["running"] = False
            state["totalBatches"] = int(state.get("totalBatches") or 0) + 1
            state["lastRunAt"] = _now().isoformat(timespec="seconds")
            state["nextRunAt"] = (_now() + timedelta(minutes=TRAIN_INTERVAL_MINUTES)).isoformat(timespec="seconds")
            _set_phase(state, "error", 100, f"训练失败：{exc}")
            _update_agent(state, "training", "error", state["message"])
            state["lastResult"] = {
                **dict(state.get("lastResult") or {}),
                "promotionEligible": False,
                "promotionStatus": "WAIT",
            }
            _update_agent(
                state,
                "challenger",
                "wait",
                "WAIT｜训练异常，没有可晋升候选",
                promotionEligible=False,
            )
            return _write(core, email, state)
    finally:
        core.REQUEST_EMAIL.reset(token)


def _worker(core, email: str) -> None:
    while True:
        with _LOCK:
            state = _read(core, email)
        if not state.get("enabled"):
            break
        if not state.get("paused"):
            due_text = str(state.get("nextRunAt") or "")
            try:
                due = datetime.fromisoformat(due_text) if due_text else _now()
            except ValueError:
                due = _now()
            if _now() >= due:
                # This is shadow training only.  It may replay during market
                # hours, but promotion remains manual and cannot affect live
                # execution until the challenger passes the risk gate.
                run_once(core, email, force=True)
        time.sleep(WORKER_POLL_SECONDS)


def resume_enabled_workers(core) -> int:
    """Restore persistent continuous-training workers after a server restart."""
    restored = 0
    root = getattr(core, "USER_DATA_DIR", None)
    if not root or not Path(root).exists():
        return restored
    for path in Path(root).glob("*_four_rabbits_status.json"):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            email = str(saved.get("ownerEmail") or "").strip().lower()
            if not email or not saved.get("enabled") or saved.get("paused"):
                continue
            with _LOCK:
                thread = _WORKERS.get(email)
                if thread and thread.is_alive():
                    continue
                thread = threading.Thread(target=_worker, args=(core, email), daemon=True, name=f"four-rabbits-{email}")
                _WORKERS[email] = thread
                thread.start()
                restored += 1
        except Exception:
            continue
    return restored


def control(core, email: str, action: str) -> dict[str, Any]:
    action = str(action or "status")
    if action == "run":
        threading.Thread(target=run_once, args=(core, email, True), daemon=True, name=f"four-rabbits-run-{email}").start()
        return {**status(core, email), "message": "训练任务已提交"}
    if action == "promote":
        with _LOCK:
            state = _read(core, email)
            eligible = bool(dict(state.get("lastResult") or {}).get("promotionEligible"))
        if not eligible:
            message = "WAIT｜最近批次未通过正收益门槛，当前没有可晋升候选"
            with _LOCK:
                state = _read(core, email)
                _update_agent(state, "challenger", "wait", message, promotionEligible=False)
                _update_agent(state, "official", "ready", "正式策略保持冠军版本")
                _write(core, email, state)
            return {**status(core, email), "promotion": {"ok": False, "message": message}}
        result = core.promote_profile(core.profile_learning_path(email, "quantbrain"), "quantbrain")
        with _LOCK:
            state = _read(core, email)
            _update_agent(state, "official", "ready" if result.get("ok") else "warning", result.get("message") or ("候选已晋升" if result.get("ok") else "当前没有可晋升候选"))
            _write(core, email, state)
        return {**status(core, email), "promotion": result}
    with _LOCK:
        state = _read(core, email)
        if action == "start":
            state["ownerEmail"] = email
            state["enabled"] = True
            state["paused"] = False
            state["intervalMinutes"] = TRAIN_INTERVAL_MINUTES
            state["nextRunAt"] = _now().isoformat(timespec="seconds")
            state["message"] = "持续影子训练已启动，每5分钟一轮；盘中只学习，不自动晋升"
        elif action == "pause":
            state["paused"] = True
            state["message"] = "持续训练已暂停"
        elif action == "resume":
            state["ownerEmail"] = email
            state["enabled"] = True
            state["paused"] = False
            state["nextRunAt"] = _now().isoformat(timespec="seconds")
            state["message"] = "持续影子训练已恢复，每5分钟一轮"
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
