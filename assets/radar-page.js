(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));

  function profile(score) {
    if (score >= 80) return { state: "强势或过热", summary: "市场强度很高，但不追已经快速拉升的股票，等待回踩或高位滞涨确认。", buy: "正T不追高，只在回踩 VWAP 并止跌确认后执行。", sell: "反T必须确认高位滞涨、冲高回落并跌破 VWAP。", position: "仓位：正常偏谨慎，注意市场过热。" };
    if (score >= 65) return { state: "震荡偏强", summary: "多数股票有所修复，但上涨广度仍需确认；适合回踩做正T，不适合追高。", buy: "正T门槛正常，等待价格回踩 VWAP 并确认止跌。", sell: "反T提高门槛，强势市场容易卖飞；只在冲高回落并跌破 VWAP 后考虑。", position: "仓位：正常，优先做有承接的回踩。" };
    if (score >= 45) return { state: "震荡市场", summary: "多空反复，重点做高抛低吸；不追单边行情，也不在信号不清时强行做T。", buy: "正反T按震荡规则，低吸前确认止跌。", sell: "反T正常执行，重点看冲高回落与均价线。", position: "仓位：正常偏轻，控制单次频率。" };
    if (score >= 25) return { state: "弱势修复", summary: "市场仍偏弱但出现修复，优先控制风险；正T需要更明确的止跌确认。", buy: "正T提高门槛，必须出现明确止跌与 VWAP 修复。", sell: "反T可以正常执行，反弹无力时优先观察减仓机会。", position: "仓位：偏轻，避免越跌越买。" };
    return { state: "弱势防守", summary: "市场风险较高，优先等待；不使用激进正T去接下跌。", buy: "正T严格限制，防止越跌越买。", sell: "仅在冲高转弱后考虑强确认反T，否则保持观察。", position: "仓位：降低单次做T数量，以风险控制为先。" };
  }

  function dimensionRows(data) {
    const metrics = data.metrics || [];
    const get = (key) => metrics.find((item) => item.key === key) || {};
    const scoreOf = (metric) => metric.max ? Math.round(Number(metric.score || 0) / Number(metric.max) * 100) : 0;
    const breadth = Number((data.breadth || {}).upRatio || 0);
    return [
      ["趋势强度", scoreOf(get("trend")), "主要指数与个股涨跌结构的合成趋势。"],
      ["资金活跃", scoreOf(get("funds")), "成交活跃与上涨资金占比的合成观察。"],
      ["赚钱效应", scoreOf(get("breadth")), "强势上涨与弱势下跌股票比例的合成结果。"],
      ["市场广度", Math.round(breadth), "当前全市场上涨家数占样本的比例。"],
    ];
  }

  function renderDimensions(data) {
    return dimensionRows(data).map(([name, value, note]) => `<article class="rq-radar-dimension"><strong>${name}</strong><b>${value}</b><span class="rq-radar-track"><i class="rq-radar-fill" style="width:${Math.max(0, Math.min(100, value))}%"></i></span><p>${note}</p></article>`).join("");
  }

  function trendSvg(history) {
    const points = (history || []).slice(-7).map((row) => Number(row.score || 0));
    if (!points.length) return '<text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#a17d67" font-size="12">等待历史快照积累</text>';
    const width = 540, height = 94, pad = 10;
    const path = points.map((value, index) => {
      const x = pad + (points.length === 1 ? 0 : index * (width - pad * 2) / (points.length - 1));
      const y = height - pad - Math.max(0, Math.min(100, value)) * (height - pad * 2) / 100;
      return `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(" ");
    return `<line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#f0ddd1"/><path d="${path}" fill="none" stroke="#ff7d7c" stroke-width="3" stroke-linecap="round"/><text x="${pad}" y="12" fill="#a17d67" font-size="11">${points[0]}分</text><text x="${width - pad}" y="12" text-anchor="end" fill="#a17d67" font-size="11">${points.at(-1)}分</text>`;
  }

  function riskItems(data, dimensions) {
    const score = Number(data.score || 0), breadth = Number((data.breadth || {}).upRatio || 0);
    const trend = dimensions[0][1];
    const risks = [];
    if (breadth < trend) risks.push("市场广度低于趋势强度，上涨可能尚未全面扩散。");
    if (score >= 80) risks.push("雷达分数处于高位，注意冲高回落与高位股分化。");
    if (Number(data.dayChange || 0) >= 8) risks.push("分数短期上升较快，避免把情绪拉升当作持续趋势。");
    if (score < 45) risks.push("市场偏弱，逆势正T需要更严格的止跌确认。");
    if (!risks.length) risks.push("当前未见单一极端风险，仍需按个股 VWAP、量价与仓位规则确认。");
    return risks;
  }

  function shell() {
    const content = $("content");
    if (!content) return;
    content.innerHTML = `<div class="rq-radar-page">
      <section class="rq-radar-hero"><div><p class="rq-radar-kicker" id="radarDataState">正在读取全市场快照</p><h1 class="rq-radar-state" id="radarState">当前市场：等待数据</h1><p class="rq-radar-human" id="radarSummary">数据加载后将展示市场判断与做T门槛说明。</p></div><div class="rq-radar-score"><b id="radarScore">--<small>分</small></b><span id="radarChange">较上次：等待数据</span></div></section>
      <div class="rq-radar-grid"><section class="rq-radar-card"><h2>四维市场判断</h2><div class="rq-radar-dimensions" id="radarDimensions"></div></section><aside class="rq-radar-card"><h2>兔兔操作建议</h2><div class="rq-radar-advice"><article class="rq-radar-action buy"><h3>🐰 买兔 · 正T</h3><p id="radarBuyAdvice">等待雷达数据。</p></article><article class="rq-radar-action sell"><h3>🐰 卖兔 · 反T</h3><p id="radarSellAdvice">等待雷达数据。</p></article><div class="rq-radar-position" id="radarPosition">仓位建议：等待数据。</div></div></aside></div>
      <section class="rq-radar-card rq-radar-trend"><h2>市场温度变化</h2><svg id="radarTrend" viewBox="0 0 540 94" role="img" aria-label="市场雷达历史趋势"></svg><div class="rq-radar-trend-note" id="radarTrendNote">展示已保存的近7次全市场快照，不把日内波动误作趋势。</div></section>
      <div class="rq-radar-lower"><section class="rq-radar-card"><h2>市场结构</h2><div class="rq-radar-structure" id="radarStructure"></div></section><section class="rq-radar-card"><h2>当前风险</h2><ul class="rq-radar-risk" id="radarRisks"></ul></section></div>
      <details class="rq-radar-card rq-radar-evidence"><summary>查看完整依据</summary><div class="rq-radar-evidence-content" id="radarEvidence"></div></details>
    </div>`;
  }

  function render(data) {
    const score = Math.max(0, Math.min(100, Number(data.score || 0)));
    const state = profile(score), dimensions = dimensionRows(data), breadth = data.breadth || {};
    $("radarState").textContent = `当前市场：${state.state}`;
    $("radarScore").innerHTML = `${score}<small>分</small>`;
    $("radarSummary").textContent = state.summary;
    $("radarDataState").textContent = `${data.coverage === "degraded" ? "数据降级" : "数据正常"} · ${data.updatedAt || "刚刚更新"} · 样本 ${data.sampleSize || "--"} 只`;
    const change = data.dayChange;
    $("radarChange").innerHTML = change == null ? "较上次：首次记录" : `较上次：<b class="${change >= 0 ? "up" : "down"}">${change >= 0 ? "+" : ""}${change}分</b>`;
    $("radarDimensions").innerHTML = renderDimensions(data);
    $("radarBuyAdvice").textContent = state.buy;
    $("radarSellAdvice").textContent = state.sell;
    $("radarPosition").textContent = state.position;
    $("radarTrend").innerHTML = trendSvg(data.history);
    $("radarTrendNote").textContent = `近${Math.min((data.history || []).length, 7)}次快照 · ${data.dataSource || "全市场行情快照"}。`;
    const stats = [["上涨家数", breadth.up], ["下跌家数", breadth.down], ["平盘家数", breadth.flat], ["上涨广度", breadth.upRatio == null ? "--" : `${breadth.upRatio}%`], ["中位涨跌", breadth.medianChange == null ? "--" : `${breadth.medianChange}%`], ["样本数量", data.sampleSize]];
    $("radarStructure").innerHTML = stats.map(([name, value]) => `<article class="rq-radar-stat"><span>${name}</span><b>${escapeHtml(value ?? "--")}</b></article>`).join("");
    $("radarRisks").innerHTML = riskItems(data, dimensions).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    $("radarEvidence").innerHTML = `<p><b>综合计算：</b>${escapeHtml(data.conclusion || "基于全市场趋势、活跃度与广度快照综合判断。")}</p><p><b>覆盖说明：</b>${escapeHtml(data.coverageMessage || "仅使用可获取的全市场行情字段；板块、涨停跌停等未覆盖字段不会臆造展示。")}</p><p><b>使用边界：</b>雷达只调整正T、反T的风险门槛；具体执行仍需个股 VWAP、量价、仓位和交易时段确认。</p>`;
    $("updated").textContent = `数据${data.coverage === "degraded" ? "降级" : "正常"} · ${data.updatedAt || "刚刚更新"}`;
  }

  async function loadRadar() {
    const content = $("content");
    try {
      const response = await fetch(`/api/market_radar?_=${Date.now()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || `雷达服务异常（${response.status}）`);
      render(data);
    } catch (error) {
      if (content) content.innerHTML = `<div class="rq-radar-card rq-radar-error">市场雷达读取失败：${escapeHtml(error.message || error)}<br><button class="refresh" type="button" id="radarRetry">重新加载</button></div>`;
      $("radarRetry")?.addEventListener("click", () => { shell(); loadRadar(); });
    }
  }

  // The compatibility template registered its old loader as a DOM-ready
  // listener. Remove that exact listener so this page performs one request.
  if (typeof window.loadRadar === "function") {
    document.removeEventListener("DOMContentLoaded", window.loadRadar);
  }
  window.loadRadar = loadRadar;
  document.addEventListener("DOMContentLoaded", () => { shell(); $("content") && loadRadar(); });
})();
