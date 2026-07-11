(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const modes = {
    "strict-day": { days: "1", simMode: "strict", label: "当日严格测试" },
    "review-5d": { days: "5", simMode: "strict", label: "近5日复盘" },
    scan: { days: "1", simMode: "scan", label: "随机机会扫描", source: "random" },
  };
  const plans = {
    steady: { profile: "steady", strategy: "官方默认策略" },
    balanced: { profile: "balanced", strategy: "官方默认策略" },
    sensitive: { profile: "sensitive", strategy: "官方默认策略" },
    quantbrain: { profile: "quantbrain", strategy: "官方默认策略" },
    custom: { profile: "balanced", strategy: "自定义策略" },
    "ai-review": { profile: "balanced", strategy: "AI复核优先" },
  };

  function applyPlan() {
    const plan = plans[$("simPlan")?.value] || plans.balanced;
    if ($("simSmartTProfile")) $("simSmartTProfile").value = plan.profile;
    if ($("simStrategyMode")) $("simStrategyMode").value = plan.strategy;
    window.syncCards?.();
    window.loadAdaptiveStatus?.();
  }

  function syncPlan() {
    const profile = $("simSmartTProfile")?.value || "balanced";
    const strategy = $("simStrategyMode")?.value || "官方默认策略";
    const matched = Object.entries(plans).find(([, plan]) => plan.profile === profile && plan.strategy === strategy);
    if ($("simPlan")) $("simPlan").value = matched ? matched[0] : "balanced";
  }
  window.syncSimulationPlan = syncPlan;

  function selectMode(mode) {
    const preset = modes[mode] || modes["strict-day"];
    $("testDaysInput").value = preset.days;
    $("simMode").value = preset.simMode;
    if (preset.source) $("simStockSource").value = preset.source;
    document.querySelectorAll("[data-sim-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.simMode === mode));
    updateSource();
    $("status").textContent = `已选择${preset.label}`;
  }

  function updateSource() {
    const source = $("simStockSource").value;
    $("simCustomStocks").hidden = source !== "custom";
    $("simSampleSize").hidden = source !== "random";
    const start = $("simStartButton");
    if (start) start.textContent = source === "random" ? "开始随机股票测试" : "开始模拟测试";
  }

  function relabelProgress() {
    const labels = [
      ["准备参数", "校验股票、资金与策略"],
      ["获取行情", "读取测试所需行情"],
      ["计算信号", "识别正T、反T与确认条件"],
      ["扣除费用", "计入佣金、税费与滑点"],
      ["汇总结果", "生成收益、状态与复盘"],
    ];
    document.querySelectorAll("#progress .step").forEach((step, index) => {
      const pair = labels[index];
      if (!pair) return;
      const title = step.querySelector("b");
      const detail = step.querySelector("span");
      if (title) title.textContent = pair[0];
      if (detail) detail.textContent = pair[1];
    });
  }

  function bind() {
    relabelProgress();
    $("simPlan")?.addEventListener("change", applyPlan);
    document.querySelectorAll("[data-sim-mode]").forEach((button) => button.addEventListener("click", () => selectMode(button.dataset.simMode)));
    $("simStockSource")?.addEventListener("change", updateSource);
    $("simStartButton")?.addEventListener("click", () => {
      if ($("simStartButton").dataset.busy === "1") return;
      const source = $("simStockSource").value;
      window.runSim?.("simulate", { random: source === "random", watchlist: source === "watchlist" });
    });
    $("simRetryButton")?.addEventListener("click", () => $("simStartButton")?.click());
    syncPlan();
    updateSource();
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
