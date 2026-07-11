"""Source-level guards for the simulation page's request lifecycle."""

from pathlib import Path
import re
import unittest

import app_core


ROOT = Path(__file__).resolve().parent


class SimulationFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = app_core.SIMULATION_HTML
        cls.script = (ROOT / "assets" / "simulation.js").read_text(encoding="utf-8")

    def test_only_run_buttons_are_busy_locked(self):
        self.assertIn("querySelectorAll('[data-sim-run]')", self.html)
        self.assertNotIn("document.querySelectorAll('button').forEach(b=>b.disabled=on)", self.html)
        self.assertIn('id="simStartButton"', self.html)
        self.assertIn('id="simRetryButton"', self.html)
        self.assertIn('data-busy="0"', self.html)

    def test_run_button_has_no_inline_handler_and_is_bound_once(self):
        self.assertNotIn('onclick="runSim', self.html)
        self.assertIn('simStartButton")?.addEventListener("click"', self.script)
        self.assertIn('dataset.busy === "1"', self.script)

    def test_error_paths_restore_and_offer_retry(self):
        self.assertIn("setTimeout(()=>ctrl.abort(),60000)", self.html)
        self.assertIn("finally{", self.html)
        self.assertIn("setBusy(false)", self.html)
        self.assertIn("res.status===401", self.html)
        self.assertIn("res.status===403", self.html)
        self.assertIn("JSON.parse(raw)", self.html)
        self.assertIn("!res.ok", self.html)
        self.assertIn("simRetryButton", self.html)

    def test_navigation_and_parameter_buttons_are_not_run_buttons(self):
        for button_id in ("simSyncWatchlist", "simRefreshHistory", "simClearView"):
            match = re.search(rf'<button\b[^>]*\bid="{button_id}"[^>]*>', self.html)
            self.assertIsNotNone(match, button_id)
            self.assertNotIn("data-sim-run", match.group(0))

    def test_result_layout_has_list_and_detail_with_explicit_states(self):
        for token in ("simResultList", "simResultDetail", "已完成闭环", "未触发", "数据不足", "资金不足", "底仓不足", "测试失败"):
            self.assertIn(token, self.script)


if __name__ == "__main__":
    unittest.main()
