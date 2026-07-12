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
    loadFourRabbits();
    document.querySelectorAll("[data-rabbit-action]").forEach((button) => button.addEventListener("click", () => controlFourRabbits(button.dataset.rabbitAction, button)));
  }

  function renderFourRabbits(data) {
    const message = $("fourRabbitsMessage");
    if (message) message.textContent = data.message || "四兔状态正常";
    const grid = $("fourRabbitsGrid");
    if (!grid) return;
    const agents = data.agents || {};
    const order = ["training", "challenger", "official", "risk"];
    grid.innerHTML = order.map((key) => {
      const agent = agents[key] || {};
      const state = agent.state || "idle";
      return `<article data-state="${state}"><span class="rabbit-dot" aria-hidden="true"></span><div><b>${agent.label || key}</b><small>${agent.message || "等待状态"}</small></div></article>`;
    }).join("");
  }

  async function loadFourRabbits() {
    try {
      const response = await fetch("/api/four-rabbits/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      renderFourRabbits(await response.json());
    } catch (error) {
      if ($("fourRabbitsMessage")) $("fourRabbitsMessage").textContent = `状态读取失败：${error.message}`;
    }
  }

  async function controlFourRabbits(action, button) {
    if (button?.disabled) return;
    if (button) button.disabled = true;
    try {
      const response = await fetch("/api/four-rabbits/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
      renderFourRabbits(data);
      if (action === "run") window.setTimeout(loadFourRabbits, 1200);
    } catch (error) {
      if ($("fourRabbitsMessage")) $("fourRabbitsMessage").textContent = `操作失败：${error.message}`;
    } finally {
      if (button) button.disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
