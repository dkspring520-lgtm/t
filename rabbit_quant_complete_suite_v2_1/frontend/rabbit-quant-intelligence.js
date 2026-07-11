(function (global) {
  "use strict";

  const STORAGE_KEY = "rabbit-quant-intelligence-settings-v1";
  const DEFAULT_SETTINGS = {
    mode: "smart",
    sensitivity: "balanced",
    marketFilter: true,
    importantAlerts: true,
  };

  const state = {
    root: null,
    options: {},
    payload: null,
    settings: loadSettings(),
  };

  function loadSettings() {
    try {
      return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") };
    } catch (_) {
      return { ...DEFAULT_SETTINGS };
    }
  }

  function saveSettings() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.settings));
    global.dispatchEvent(new CustomEvent("rabbit-quant:settings-change", { detail: { ...state.settings } }));
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function injectStyle() {
    if (document.getElementById("rabbit-quant-intelligence-style")) return;
    const style = document.createElement("style");
    style.id = "rabbit-quant-intelligence-style";
    style.textContent = `
      .rqi-shell{--rqi-ink:#4a2d23;--rqi-muted:#9b7968;--rqi-paper:#fffdf9;--rqi-soft:#fff7ef;--rqi-line:#efd9c8;--rqi-peach:#ff9c67;--rqi-peach-deep:#ee7840;--rqi-mint:#45a77a;--rqi-mint-soft:#e9f7f0;--rqi-gold:#d99031;--rqi-gold-soft:#fff5dc;--rqi-red:#d9655d;--rqi-red-soft:#fff0ef;--rqi-lav:#8b78c9;--rqi-shadow:0 18px 45px rgba(105,67,43,.12);font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:var(--rqi-ink);position:relative;max-width:520px}
      .rqi-card{position:relative;overflow:hidden;background:linear-gradient(145deg,#fffdf9,#fff8f1);border:1px solid var(--rqi-line);border-radius:24px;box-shadow:var(--rqi-shadow);padding:18px}
      .rqi-card:before{content:"";position:absolute;right:-72px;top:-72px;width:190px;height:190px;border-radius:50%;background:radial-gradient(circle,rgba(255,177,156,.25),rgba(255,255,255,0) 70%);pointer-events:none}
      .rqi-head{display:flex;align-items:center;justify-content:space-between;gap:12px;position:relative;z-index:1}
      .rqi-brand{display:flex;align-items:center;gap:11px;min-width:0}.rqi-avatar{width:48px;height:48px;border-radius:16px;object-fit:cover;border:3px solid #fff2e6;box-shadow:0 7px 18px rgba(105,67,43,.13)}
      .rqi-title{font-weight:900;font-size:18px;line-height:1.2}.rqi-sub{font-size:11px;color:var(--rqi-muted);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:260px}
      .rqi-settings-btn{border:1px solid #ead4c3;background:#fffaf5;border-radius:12px;width:36px;height:36px;cursor:pointer;color:var(--rqi-muted);font-size:17px}
      .rqi-hero{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:center;margin-top:15px;padding:14px;border:1px solid #f1dfd1;background:rgba(255,255,255,.72);border-radius:18px;position:relative;z-index:1}
      .rqi-hero-label{font-size:12px;color:var(--rqi-muted);margin-bottom:4px}.rqi-hero-main{font-size:22px;font-weight:900;letter-spacing:.01em}.rqi-hero-note{font-size:12px;color:var(--rqi-muted);margin-top:5px;line-height:1.45}
      .rqi-score{width:72px;height:72px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-direction:column;background:linear-gradient(145deg,var(--rqi-peach),var(--rqi-peach-deep));color:#fff;box-shadow:0 10px 22px rgba(238,120,64,.24)}.rqi-score b{font-size:25px;line-height:1}.rqi-score small{font-size:10px;margin-top:3px}
      .rqi-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}.rqi-item{background:#fff;border:1px solid #f0dfd2;border-radius:16px;padding:12px;min-width:0}.rqi-item-top{display:flex;align-items:center;justify-content:space-between;gap:7px}.rqi-item-name{font-size:12px;color:var(--rqi-muted);font-weight:700}.rqi-pill{font-size:10px;padding:4px 7px;border-radius:999px;font-weight:800;white-space:nowrap}.rqi-item-value{font-size:15px;font-weight:900;margin-top:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.rqi-item-foot{font-size:10px;color:var(--rqi-muted);margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .rqi-positive{color:var(--rqi-mint)}.rqi-neutral{color:var(--rqi-gold)}.rqi-negative{color:var(--rqi-red)}.rqi-wait{color:var(--rqi-lav)}
      .rqi-pill.rqi-positive{background:var(--rqi-mint-soft)}.rqi-pill.rqi-neutral{background:var(--rqi-gold-soft)}.rqi-pill.rqi-negative{background:var(--rqi-red-soft)}.rqi-pill.rqi-wait{background:#f0edff}
      .rqi-summary{margin-top:10px;padding:12px 13px;border-radius:15px;background:#fffaf5;border:1px dashed #e9cfbc;font-size:12px;line-height:1.55;color:#6e4d3e}.rqi-summary b{color:var(--rqi-ink)}
      .rqi-levels{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px}.rqi-level{font-size:10px;padding:6px 8px;border-radius:999px;background:#f9f2ec;color:var(--rqi-muted)}
      .rqi-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:11px;font-size:10px;color:var(--rqi-muted)}.rqi-status-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--rqi-mint);margin-right:5px}.rqi-version{padding:4px 7px;border-radius:999px;background:#f5eee8}
      .rqi-panel{position:absolute;right:0;top:48px;width:min(340px,calc(100vw - 32px));z-index:20;background:#fffdf9;border:1px solid var(--rqi-line);border-radius:18px;box-shadow:0 22px 55px rgba(75,45,30,.20);padding:15px;display:none}.rqi-panel.open{display:block}.rqi-panel h3{font-size:15px;margin:0 0 5px}.rqi-panel p{font-size:11px;color:var(--rqi-muted);margin:0 0 12px;line-height:1.45}.rqi-segment{display:grid;grid-template-columns:1fr 1fr;background:#f6ede5;border-radius:13px;padding:4px;gap:4px}.rqi-segment button{border:0;background:transparent;border-radius:10px;padding:9px;cursor:pointer;font-weight:800;color:var(--rqi-muted)}.rqi-segment button.active{background:#fff;color:var(--rqi-ink);box-shadow:0 4px 12px rgba(90,55,35,.09)}
      .rqi-sensitivity{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}.rqi-sensitivity button{border:1px solid #edd9ca;background:#fffaf6;border-radius:12px;padding:8px 4px;cursor:pointer;font-size:11px;color:var(--rqi-muted)}.rqi-sensitivity button.active{border-color:var(--rqi-peach);color:var(--rqi-peach-deep);background:#fff3ea;font-weight:900}
      .rqi-toggle-row{display:flex;align-items:center;justify-content:space-between;gap:12px;border-top:1px dashed #ead6c8;margin-top:11px;padding-top:11px;font-size:12px}.rqi-toggle{appearance:none;width:40px;height:23px;background:#d9ccc3;border-radius:999px;padding:3px;cursor:pointer}.rqi-toggle:before{content:"";display:block;width:17px;height:17px;border-radius:50%;background:#fff;transition:.2s;box-shadow:0 2px 5px rgba(0,0,0,.15)}.rqi-toggle:checked{background:var(--rqi-mint)}.rqi-toggle:checked:before{transform:translateX(17px)}
      .rqi-toast{position:fixed;right:24px;bottom:24px;z-index:99999;display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:center;max-width:330px;background:#fffdf9;border:1px solid #d9eadf;border-radius:17px;padding:13px 14px;box-shadow:0 18px 45px rgba(53,87,67,.18);opacity:0;transform:translateY(14px);pointer-events:none;transition:.22s}.rqi-toast.show{opacity:1;transform:translateY(0)}.rqi-toast-icon{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;background:#eef9f3;font-size:22px}.rqi-toast strong{font-size:13px;display:block}.rqi-toast span{font-size:11px;color:var(--rqi-muted);display:block;margin-top:3px;line-height:1.4}
      @media(max-width:560px){.rqi-shell{max-width:none}.rqi-grid{grid-template-columns:1fr}.rqi-sub{max-width:180px}.rqi-hero-main{font-size:19px}}
    `;
    document.head.appendChild(style);
  }

  function toneFromScore(score) {
    const n = Number(score);
    if (!Number.isFinite(n)) return "rqi-wait";
    if (n >= 59) return "rqi-positive";
    if (n <= 41) return "rqi-negative";
    return "rqi-neutral";
  }

  function probabilityText(obj) {
    if (!obj || !obj.probabilities) return "概率待计算";
    const p = obj.probabilities;
    return `上 ${Math.round(p.up || 0)}% · 横 ${Math.round(p.range || 0)}% · 下 ${Math.round(p.down || 0)}%`;
  }

  function mount(target, options = {}) {
    injectStyle();
    const root = typeof target === "string" ? document.querySelector(target) : target;
    if (!root) throw new Error("RabbitQuantIntelligence: 找不到挂载容器");
    state.root = root;
    state.options = {
      title: "兔兔走势研判",
      subtitle: "大趋势 · 开盘预判 · 盘中修正 · 智能做T",
      avatarUrl: "assets/rabbit-avatar.png",
      ...options,
    };
    root.classList.add("rqi-shell");
    render();
    bindEvents();
    return api;
  }

  function render() {
    if (!state.root) return;
    const p = state.payload || {};
    const big = p.big_trend || {};
    const current = big.current || { label: "等待数据", score: 50 };
    const future20 = (big.forecasts || {})["20d"] || { label: "等待数据", score: 50 };
    const pre = p.preopen || { label: "等待数据", score: 50, confidence_label: "偏低" };
    const intra = p.intraday || { label: "等待开盘", score: 50, confidence_label: "偏低" };
    const smart = p.smart_t_context || { label: "等待数据", score: 50, action: "等待更多数据", mode: "WAIT" };
    const levels = big.levels || {};
    const heroScore = Number(future20.score ?? current.score ?? 50);
    const tone = toneFromScore(heroScore);
    const summary = p.summary || "导入行情数据后，系统会生成开盘、盘中和大趋势研判。";
    const modelStatus = big.model_status || "试运行";

    state.root.innerHTML = `
      <section class="rqi-card" aria-label="兔兔股票走势研判">
        <header class="rqi-head">
          <div class="rqi-brand">
            <img class="rqi-avatar" src="${esc(state.options.avatarUrl)}" alt="兔兔助手" />
            <div><div class="rqi-title">${esc(state.options.title)}</div><div class="rqi-sub">${esc(state.options.subtitle)}</div></div>
          </div>
          <button class="rqi-settings-btn" type="button" aria-label="策略设置" aria-expanded="false">⚙</button>
        </header>
        <section class="rqi-hero">
          <div>
            <div class="rqi-hero-label">未来20日大走势</div>
            <div class="rqi-hero-main ${tone}">${esc(future20.label || "方向不明")}</div>
            <div class="rqi-hero-note">${esc(probabilityText(future20))} · 置信度 ${esc(big.confidence_label || "偏低")}</div>
          </div>
          <div class="rqi-score"><b>${Math.round(heroScore)}</b><small>趋势分</small></div>
        </section>
        <div class="rqi-grid">
          ${itemHtml("当前大趋势", current.label, current.score, `强度 ${Math.round(current.trend_strength || 0)}%`)}
          ${itemHtml("开盘预判", pre.label, pre.score, `${pre.version || "盘前版"} · ${pre.confidence_label || "偏低"}`)}
          ${itemHtml("今日走势", intra.label, intra.score, `置信度 ${intra.confidence_label || "偏低"}`)}
          ${itemHtml("智能做T", smart.label, smart.score, smart.mode === "WAIT" ? "暂缓操作" : "环境过滤已启用")}
        </div>
        <div class="rqi-summary"><b>兔兔建议：</b>${esc(smart.action || summary)}<br><span>${esc(summary)}</span></div>
        <div class="rqi-levels">
          ${levels.support_reference != null ? `<span class="rqi-level">支撑参考 ${esc(levels.support_reference)}</span>` : ""}
          ${levels.resistance_reference != null ? `<span class="rqi-level">压力参考 ${esc(levels.resistance_reference)}</span>` : ""}
          ${levels.trend_invalidation_reference != null ? `<span class="rqi-level">趋势失效参考 ${esc(levels.trend_invalidation_reference)}</span>` : ""}
        </div>
        <footer class="rqi-foot">
          <span><i class="rqi-status-dot"></i>${esc(big.as_of ? "数据已更新" : "等待真实数据")}</span>
          <span class="rqi-version">${esc(modelStatus)} · V1.0</span>
        </footer>
        ${settingsHtml()}
      </section>
    `;
  }

  function itemHtml(name, value, score, foot) {
    const tone = toneFromScore(score);
    const n = Number(score);
    const pill = Number.isFinite(n) ? `${Math.round(n)}分` : "--";
    return `<div class="rqi-item"><div class="rqi-item-top"><span class="rqi-item-name">${esc(name)}</span><span class="rqi-pill ${tone}">${pill}</span></div><div class="rqi-item-value ${tone}">${esc(value || "等待数据")}</div><div class="rqi-item-foot">${esc(foot || "")}</div></div>`;
  }

  function settingsHtml() {
    const s = state.settings;
    return `
      <aside class="rqi-panel" aria-label="智能做T设置">
        <h3>🐰 智能做T设置</h3><p>前台只保留简单选项，复杂逻辑与风控在后台自动执行。</p>
        <div class="rqi-segment">
          <button type="button" data-mode="smart" class="${s.mode === "smart" ? "active" : ""}">智能做T</button>
          <button type="button" data-mode="custom" class="${s.mode === "custom" ? "active" : ""}">自定义</button>
        </div>
        <div class="rqi-sensitivity">
          <button type="button" data-sensitivity="steady" class="${s.sensitivity === "steady" ? "active" : ""}">稳健</button>
          <button type="button" data-sensitivity="balanced" class="${s.sensitivity === "balanced" ? "active" : ""}">平衡</button>
          <button type="button" data-sensitivity="sensitive" class="${s.sensitivity === "sensitive" ? "active" : ""}">灵敏</button>
        </div>
        <label class="rqi-toggle-row"><span>市场环境过滤</span><input class="rqi-toggle" type="checkbox" data-setting="marketFilter" ${s.marketFilter ? "checked" : ""}></label>
        <label class="rqi-toggle-row"><span>仅重要信号提醒</span><input class="rqi-toggle" type="checkbox" data-setting="importantAlerts" ${s.importantAlerts ? "checked" : ""}></label>
      </aside>`;
  }

  function bindEvents() {
    if (!state.root) return;
    const settingsBtn = state.root.querySelector(".rqi-settings-btn");
    const panel = state.root.querySelector(".rqi-panel");
    settingsBtn?.addEventListener("click", () => {
      const open = panel.classList.toggle("open");
      settingsBtn.setAttribute("aria-expanded", String(open));
    });
    state.root.querySelectorAll("[data-mode]").forEach((btn) => btn.addEventListener("click", () => {
      state.settings.mode = btn.dataset.mode;
      saveSettings();
      render();
      bindEvents();
    }));
    state.root.querySelectorAll("[data-sensitivity]").forEach((btn) => btn.addEventListener("click", () => {
      state.settings.sensitivity = btn.dataset.sensitivity;
      saveSettings();
      render();
      bindEvents();
    }));
    state.root.querySelectorAll("[data-setting]").forEach((input) => input.addEventListener("change", () => {
      state.settings[input.dataset.setting] = Boolean(input.checked);
      saveSettings();
    }));
  }

  function update(payload) {
    state.payload = payload || null;
    render();
    bindEvents();
    return api;
  }

  function notify(notification = {}) {
    if (!state.settings.importantAlerts && notification.important !== true) return;
    let toast = document.getElementById("rqi-global-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "rqi-global-toast";
      toast.className = "rqi-toast";
      document.body.appendChild(toast);
    }
    toast.innerHTML = `<div class="rqi-toast-icon">🐰</div><div><strong>${esc(notification.title || "发现确认信号")}</strong><span>${esc(notification.detail || "请查看当前股票的研判详情")}</span></div>`;
    requestAnimationFrame(() => toast.classList.add("show"));
    clearTimeout(toast._rqiTimer);
    toast._rqiTimer = setTimeout(() => toast.classList.remove("show"), Number(notification.duration || 4200));
  }

  const api = {
    mount,
    update,
    notify,
    getSettings() { return { ...state.settings }; },
    setSettings(next = {}) {
      state.settings = { ...state.settings, ...next };
      saveSettings();
      render();
      bindEvents();
      return api;
    },
  };

  global.RabbitQuantIntelligence = api;
})(window);
