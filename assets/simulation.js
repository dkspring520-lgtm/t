(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const modes = {
    "strict-day": { days: "1", simMode: "strict", label: "当日严格测试" },
    "review-5d": { days: "5", simMode: "strict", label: "近5日复盘" },
    scan: { days: "1", simMode: "scan", label: "机会扫描" },
  };

  function selectMode(mode) {
    const preset = modes[mode] || modes["strict-day"];
    $("testDaysInput").value = preset.days;
    $("simMode").value = preset.simMode;
    document.querySelectorAll("[data-sim-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.simMode === mode));
    $("status").textContent = `已选择${preset.label}`;
  }

  function updateSource() {
    const source = $("simStockSource").value;
    $("simCustomStocks").hidden = source !== "custom";
    $("simSyncWatchlist").hidden = source !== "watchlist";
  }

  function statusFor(row) {
    const text = `${row.action || ""} ${row.reason || ""} ${row.detail || ""}`;
    if (/数据.*不足|行情.*不足/.test(text)) return "数据不足";
    if (/资金不足/.test(text)) return "资金不足";
    if (/底仓不足|可卖.*不足/.test(text)) return "底仓不足";
    if (/失败|异常/.test(text)) return "测试失败";
    if (!row.action || /未触发|等待/.test(text)) return "未触发";
    return "已完成闭环";
  }

  function value(row, keys, fallback = "--") {
    for (const key of keys) if (row[key] !== undefined && row[key] !== null && row[key] !== "") return row[key];
    return fallback;
  }

  function cycleText(row) {
    const cycles = Array.isArray(row.cycles) ? row.cycles : [];
    if (!cycles.length) return "没有完整买卖闭环";
    return cycles.map((cycle, index) => `<li><b>第${index + 1}轮</b> 买入 ${esc(value(cycle, ["buyTime"], "--"))} / ${esc(value(cycle, ["buyPrice"], "--"))} · 卖出 ${esc(value(cycle, ["sellTime"], "--"))} / ${esc(value(cycle, ["sellPrice"], "--"))} · 数量 ${esc(value(cycle, ["shares", "quantity"], "--"))}</li>`).join("");
  }

  function renderDetail(row) {
    const detail = $("simResultDetail");
    if (!detail) return;
    const cycles = Array.isArray(row.cycles) ? row.cycles : [];
    detail.innerHTML = `<header><div><b>${esc(row.name || "未命名股票")}</b><span>${esc(row.code || "--")}</span></div><em>${esc(statusFor(row))}</em></header>
      <dl><div><dt>方向</dt><dd>${esc(row.action || "--")}</dd></div><div><dt>轮数</dt><dd>${cycles.length}</dd></div><div><dt>毛收益</dt><dd>${esc(value(row, ["grossPnlText", "grossPnl", "money"]))}</dd></div><div><dt>费用</dt><dd>${esc(value(row, ["feesText", "fees", "fee"]))}</dd></div><div><dt>净利润</dt><dd>${esc(value(row, ["pnlText", "pnl"]))}</dd></div><div><dt>仓位恢复</dt><dd>${esc(value(row, ["positionRecovery", "positionStatus"], cycles.length ? "已闭环" : "--"))}</dd></div></dl>
      <h3>买卖明细</h3><ul>${cycleText(row)}</ul><h3>触发原因</h3><p>${esc(row.reason || row.detail || "本次未提供额外原因。")}</p>`;
  }

  function renderRows(rows) {
    const list = $("simResultList");
    const detail = $("simResultDetail");
    const count = $("count");
    if (!list || !detail) return;
    if (!rows.length) {
      list.innerHTML = '<div class="sim-result-empty">暂无结果。开始模拟后会在这里列出每只股票的状态。</div>';
      detail.innerHTML = '<div class="sim-result-empty">选择一只股票查看买卖时间、价格、费用和仓位恢复状态。</div>';
      if (count) count.textContent = "等待运行";
      return;
    }
    if (count) count.textContent = `${rows.length} 只`;
    list.innerHTML = rows.map((row, index) => `<button type="button" class="sim-result-row${index ? "" : " is-selected"}" data-sim-result="${index}"><span><b>${esc(row.name || "--")}</b><small>${esc(row.code || "--")}</small></span><span>${esc(row.action || "--")}</span><span>${(row.cycles || []).length}轮</span><span>${esc(value(row, ["pnlText", "pnl"]))}</span><span>${esc(value(row, ["feesText", "fees", "fee"]))}</span><em>${esc(statusFor(row))}</em></button>`).join("");
    list.querySelectorAll("[data-sim-result]").forEach((button) => button.addEventListener("click", () => {
      list.querySelectorAll("[data-sim-result]").forEach((item) => item.classList.toggle("is-selected", item === button));
      renderDetail(rows[Number(button.dataset.simResult)] || {});
    }));
    renderDetail(rows[0]);
  }

  function installResults() {
    const main = document.querySelector("#simLayout > main.panel");
    if (!main || $("simResultList")) return;
    main.innerHTML = `<div class="panel-head"><div><b>测试结果</b><span id="count">等待运行</span></div></div><div class="sim-results-split"><section id="simResultList" class="sim-result-list"><div class="sim-result-empty">暂无结果。开始模拟后会在这里列出每只股票的状态。</div></section><article id="simResultDetail" class="sim-result-detail"><div class="sim-result-empty">选择一只股票查看买卖时间、价格、费用和仓位恢复状态。</div></article></div>`;
    window.renderRows = renderRows;
    window.setResultVisible = (on, message) => {
      document.getElementById("simLayout")?.classList.toggle("is-empty", !on);
      if (!on) renderRows([]);
      if (message) $("status").textContent = message;
    };
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
    installResults();
    document.querySelectorAll("[data-sim-mode]").forEach((button) => button.addEventListener("click", () => selectMode(button.dataset.simMode)));
    $("simStockSource")?.addEventListener("change", updateSource);
    $("simStartButton")?.addEventListener("click", () => {
      if ($("simStartButton").dataset.busy === "1") return;
      const source = $("simStockSource").value;
      window.runSim?.("simulate", { random: source === "random", watchlist: source === "watchlist" });
    });
    $("simRetryButton")?.addEventListener("click", () => $("simStartButton")?.click());
    $("simSyncWatchlist")?.addEventListener("click", () => window.syncWatchlistStocks?.(true));
    $("simRefreshHistory")?.addEventListener("click", () => window.loadHistory?.());
    $("simClearView")?.addEventListener("click", () => window.clearView?.());
    updateSource();
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
