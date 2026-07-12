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


class _Thread:
    def __init__(self, *args, **kwargs):
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started


class FourRabbitsContinuousTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
