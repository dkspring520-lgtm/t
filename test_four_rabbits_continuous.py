import contextvars
import hashlib
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import simulate_t_random as simulator
from services import four_rabbits


class _Core:
    REQUEST_EMAIL = contextvars.ContextVar("test_email", default="")

    def __init__(self, root: Path):
        self.root = root
        self.USER_DATA_DIR = root

    def user_data_path(self, email: str, name: str) -> Path:
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:24]
        return self.root / f"{digest}_{name}"

    def run_task(self, _name, _options):
        return {"ok": False, "summary": "test failure", "stats": {}, "stocks": []}


class _ResultCore(_Core):
    def __init__(self, root: Path, result: dict):
        super().__init__(root)
        self.result = result
        self.last_options = {}
        self.promote_calls = 0

    def run_task(self, _name, options):
        self.last_options = dict(options)
        return json.loads(json.dumps(self.result, ensure_ascii=False))

    @staticmethod
    def parse_trigger(value):
        left, _, right = str(value or "").partition("/")
        return int(left or 0), int(right or 0)

    def profile_learning_path(self, _email, _profile):
        return self.root / "learning.sqlite3"

    @staticmethod
    def profile_status(_path, _profile):
        return {"champion": {"version_id": "champion-v1"}}

    def promote_profile(self, _path, _profile):
        self.promote_calls += 1
        return {"ok": True, "message": "promoted"}


class _Thread:
    def __init__(self, *args, **kwargs):
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started


class FourRabbitsContinuousTests(unittest.TestCase):
    @staticmethod
    def _success_result(pnl="+12.50元", challenger="challenger-v2"):
        return {
            "ok": True,
            "summary": "done",
            "stats": {
                "trigger": "1/1",
                "win": "100%" if str(pnl).startswith("+") else "0%",
                "pnl": pnl,
                "fees": "5.00元",
                "adaptiveLearning": {
                    "recordedSignals": 2,
                    "recordedTrades": 1,
                    "proposal": {
                        "status": "challenger_created" if challenger else "no_change",
                        "challengerVersion": challenger,
                    },
                },
            },
            "stocks": [{
                "code": "600000",
                "action": "正T",
                "pnl": 0.12,
                "money": pnl,
                "prices": [{"time": "09:35", "price": 10.0}],
                "dailyResults": [{"date": "2026-07-10", "pnl": 0.12}],
                "cycles": [{"date": "2026-07-10", "action": "正T"}],
            }],
        }

    def test_default_interval_is_five_minutes(self):
        state = four_rabbits._default_state()
        self.assertEqual(state["intervalMinutes"], 5)

    def test_start_runs_immediately_and_restores_worker(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(four_rabbits.threading, "Thread", _Thread):
            core = _Core(Path(temp))
            four_rabbits._WORKERS.clear()
            state = four_rabbits.control(core, "rabbit@example.com", "start")
            self.assertTrue(state["enabled"])
            self.assertTrue(state["workerAlive"])
            due = datetime.fromisoformat(state["nextRunAt"])
            self.assertLess(abs((due - datetime.now()).total_seconds()), 3)

    def test_completed_batch_schedules_next_in_five_minutes(self):
        with tempfile.TemporaryDirectory() as temp:
            core = _Core(Path(temp))
            state = four_rabbits.run_once(core, "rabbit@example.com", force=True)
            due = datetime.fromisoformat(state["nextRunAt"])
            delay = (due - datetime.now()).total_seconds()
            self.assertGreater(delay, 295)
            self.assertLessEqual(delay, 300)
            self.assertEqual(state["totalBatches"], 1)

    def test_enabled_worker_is_restored_after_restart(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(four_rabbits.threading, "Thread", _Thread):
            core = _Core(Path(temp))
            four_rabbits._WORKERS.clear()
            path = core.user_data_path("rabbit@example.com", "four_rabbits_status.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"ownerEmail": "rabbit@example.com", "enabled": True, "paused": False}), encoding="utf-8")
            restored = four_rabbits.resume_enabled_workers(core)
            self.assertEqual(restored, 1)
            self.assertTrue(four_rabbits._WORKERS["rabbit@example.com"].is_alive())

    def test_market_cache_expires_after_four_minutes(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_dir = Path(temp)
            path = cache_dir / "sh600000.json"
            path.write_text(json.dumps({"bars": []}), encoding="utf-8")
            market_now = datetime(2026, 7, 13, 10, 0)
            with patch.object(simulator, "MINUTE_CACHE_DIR", cache_dir):
                self.assertTrue(simulator.minute_cache_is_fresh("sh600000", market_now))
                old = time.time() - 301
                os.utime(path, (old, old))
                self.assertFalse(simulator.minute_cache_is_fresh("sh600000", market_now))

    def test_camel_case_challenger_and_manifest_are_exposed(self):
        with tempfile.TemporaryDirectory() as temp, patch("adaptive_profiles._service") as service_factory:
            service_factory.return_value.monitor_and_rollback.return_value = {"rolledBack": False}
            core = _ResultCore(Path(temp), self._success_result())
            state = four_rabbits.run_once(core, "rabbit@example.com", force=True)
            challenger = state["agents"]["challenger"]
            self.assertEqual(challenger["state"], "pending")
            self.assertEqual(challenger["metrics"]["version"], "challenger-v2")
            self.assertTrue(state["lastResult"]["promotionEligible"])
            manifest = state["batchManifest"]
            self.assertEqual(manifest["selectedCodes"], ["600000"])
            self.assertEqual(manifest["selectedStocks"], [{"code": "600000", "name": ""}])
            self.assertEqual(manifest["selectedDates"]["600000"], ["2026-07-10"])
            self.assertEqual(len(manifest["fingerprint"]), 64)
            self.assertEqual(manifest["reproducibility"], "limited")
            self.assertFalse(manifest["seedApplied"])
            self.assertIn("seed", core.last_options)
            persisted = json.loads(core.user_data_path("rabbit@example.com", "four_rabbits_status.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["batchManifest"], manifest)

    def test_losing_batch_with_existing_challenger_is_wait_not_pending(self):
        with tempfile.TemporaryDirectory() as temp, patch("adaptive_profiles._service") as service_factory:
            service_factory.return_value.monitor_and_rollback.return_value = {"rolledBack": False}
            core = _ResultCore(Path(temp), self._success_result("-25.00元"))
            state = four_rabbits.run_once(core, "rabbit@example.com", force=True)
            self.assertEqual(state["agents"]["challenger"]["state"], "wait")
            self.assertIn("WAIT", state["agents"]["challenger"]["message"])
            self.assertFalse(state["lastResult"]["promotionEligible"])
            promotion = four_rabbits.control(core, "rabbit@example.com", "promote")["promotion"]
            self.assertFalse(promotion["ok"])
            self.assertEqual(core.promote_calls, 0)

    def test_no_candidate_has_explicit_wait_status(self):
        with tempfile.TemporaryDirectory() as temp, patch("adaptive_profiles._service") as service_factory:
            service_factory.return_value.monitor_and_rollback.return_value = {"rolledBack": False}
            core = _ResultCore(Path(temp), self._success_result(challenger=""))
            state = four_rabbits.run_once(core, "rabbit@example.com", force=True)
            self.assertEqual(state["lastResult"]["promotionStatus"], "WAIT")
            self.assertEqual(state["agents"]["challenger"]["state"], "wait")
            self.assertIn("未产生可晋升候选", state["agents"]["challenger"]["message"])

    def test_manifest_reports_exact_only_when_seed_is_echoed(self):
        batch = {"id": "batch-1", "seed": 7}
        result = self._success_result()
        result["stats"]["seed"] = 7
        first = four_rabbits._batch_manifest(batch, result)
        second = four_rabbits._batch_manifest(batch, result)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertTrue(first["seedApplied"])
        self.assertEqual(first["reproducibility"], "exact")
        self.assertEqual(first["limitations"], [])


if __name__ == "__main__":
    unittest.main()
