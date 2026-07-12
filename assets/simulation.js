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
  let rabbitPollTimer = 0;
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));

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
      return `<article data-state="${escapeHtml(state)}"><span class="rabbit-dot" aria-hidden="true"></span><div><b>${escapeHtml(agent.label || key)}</b><small>${escapeHtml(agent.message || "等待状态")}</small></div></article>`;
    }).join("");

    const phaseLabels = { idle: "等待训练", replaying: "影子回放中", completed: "本轮已完成", error: "训练异常", paused: "已暂停" };
    const progress = Math.max(0, Math.min(100, Number(data.progress) || 0));
    if ($("fourRabbitsPhase")) $("fourRabbitsPhase").textContent = phaseLabels[data.phase] || data.phase || "等待训练";
    if ($("fourRabbitsProgressText")) $("fourRabbitsProgressText").textContent = data.running ? `${progress}% · ${Number(data.elapsedSeconds) || 0}秒` : `${progress}%`;
    if ($("fourRabbitsProgressBar")) $("fourRabbitsProgressBar").style.width = `${progress}%`;
    if ($("fourRabbitsProgress")) $("fourRabbitsProgress").dataset.running = data.running ? "1" : "0";

    const result = data.lastResult || {};
    const batch = data.batch || {};
    const nextRun = data.nextRunAt ? String(data.nextRunAt).slice(11, 16) : "--";
    const metricItems = [
      ["批次", batch.id || "--"], ["样本", result.tested ? `${result.tested}只 / ${batch.days || 5}日` : `${batch.sample || 10}只 / ${batch.days || 5}日`],
      ["触发", result.trigger || "--"], ["胜率", result.winRate || "--"], ["净盈亏", result.pnl || "--"],
      ["学习记录", `${Number(result.signals) || 0}信号 / ${Number(result.trades) || 0}成交`], ["下轮", data.enabled && !data.paused ? `${nextRun} · 第${Number(data.totalBatches || 0) + 1}轮` : "未启用"],
    ];
    if ($("fourRabbitsMetrics")) $("fourRabbitsMetrics").innerHTML = metricItems.map(([label, value]) => `<span><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></span>`).join("");
    const events = Array.isArray(data.events) ? data.events.slice().reverse() : [];
    if ($("fourRabbitsEvents")) $("fourRabbitsEvents").innerHTML = events.length ? events.map((event) => `<li><time>${escapeHtml(String(event.time || "").slice(11, 19))}</time><span>${escapeHtml(event.message || event.phase || "状态更新")}</span></li>`).join("") : "<li>尚无训练记录</li>";
  }

  async function loadFourRabbits() {
    let delay = 12000;
    try {
      const response = await fetch("/api/four-rabbits/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      renderFourRabbits(data);
      delay = data.running ? 2000 : 12000;
    } catch (error) {
      if ($("fourRabbitsMessage")) $("fourRabbitsMessage").textContent = `状态读取失败：${error.message}`;
    } finally {
      window.clearTimeout(rabbitPollTimer);
      rabbitPollTimer = window.setTimeout(loadFourRabbits, delay);
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
      window.clearTimeout(rabbitPollTimer);
      rabbitPollTimer = window.setTimeout(loadFourRabbits, action === "run" ? 500 : 1500);
    } catch (error) {
      if ($("fourRabbitsMessage")) $("fourRabbitsMessage").textContent = `操作失败：${error.message}`;
    } finally {
      if (button) button.disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
