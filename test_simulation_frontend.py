"""Source-level guards for the simulation page's request lifecycle."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import app_core
from simulate_t_random import Result, Stock, apply_daily_trade_limit


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

    def test_inline_simulation_script_has_valid_javascript(self):
        start = self.html.rfind("<script>") + len("<script>")
        end = self.html.index("</script>", start)
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(self.html[start:end])
            script_path = Path(handle.name)
        try:
            check = subprocess.run(["node", "--check", str(script_path)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
        finally:
            script_path.unlink(missing_ok=True)

    def test_only_primary_run_controls_remain_visible(self):
        self.assertNotIn('id="simSyncWatchlist"', self.html)
        self.assertNotIn('id="simRefreshHistory"', self.html)
        self.assertNotIn('id="simClearView"', self.html)
        self.assertIn('id="simStockSource"', self.html)

    def test_random_source_exposes_a_bounded_sample_size(self):
        self.assertIn('id="simSampleSize"', self.html)
        self.assertIn('id="sampleInput"', self.html)
        self.assertIn('value="10">10', self.html)
        self.assertIn('value="20">20', self.html)
        self.assertIn('$("simSampleSize").hidden = source !== "random"', self.script)

    def test_native_intraday_chart_renderer_is_not_replaced(self):
        self.assertIn("function chart(row)", self.html)
        self.assertIn("正T·买", self.html)
        self.assertIn("反T·买回", self.html)
        self.assertNotIn("installResults", self.script)
        self.assertNotIn("window.renderRows", self.script)

    def test_confirmed_trade_is_not_rewritten_as_not_triggered_without_a_cap(self):
        result = Result(
            Stock("测试股", "000001", "sz000001"), "正T卖出", "11:20", 10.0,
            "11:35", 10.1, 1.0, 100.0, 10000.0, 1000, "VWAP确认",
        )
        kept = apply_daily_trade_limit([result], 0)
        self.assertEqual(kept[0].action, "正T卖出")


if __name__ == "__main__":
    unittest.main()
