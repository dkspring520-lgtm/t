(function (global) {
  const STYLE_ID = "rabbit-strategy-growth-style";
  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .rabbit-growth-card{font-family:Inter,"Microsoft YaHei",sans-serif;background:linear-gradient(145deg,#fffdf9,#fff7f5);border:1px solid #f2ddd7;border-radius:18px;padding:16px;box-shadow:0 10px 28px rgba(112,75,65,.08);color:#513f3a;max-width:360px}
      .rabbit-growth-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
      .rabbit-growth-title{display:flex;align-items:center;gap:8px;font-size:16px;font-weight:800}
      .rabbit-growth-badge{font-size:12px;padding:5px 9px;border-radius:999px;background:#fff0ea;color:#bd6652}
      .rabbit-growth-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
      .rabbit-growth-stat{background:#fff;border:1px solid #f4e6e1;border-radius:13px;padding:10px}
      .rabbit-growth-label{font-size:12px;color:#9d8179;margin-bottom:3px}
      .rabbit-growth-value{font-size:17px;font-weight:800;color:#5b4540}
      .rabbit-growth-note{margin-top:11px;padding:10px 11px;border-radius:12px;background:#fff4e8;font-size:12px;line-height:1.55;color:#7b6058}
      .rabbit-growth-actions{display:flex;gap:8px;margin-top:12px}
      .rabbit-growth-btn{border:0;border-radius:11px;padding:8px 11px;font-size:12px;cursor:pointer;background:#f7dfd9;color:#7a4d43}
      .rabbit-growth-btn.primary{background:#d9826d;color:white}
    `;
    document.head.appendChild(style);
  }

  function value(v, suffix) {
    return v === null || v === undefined ? "样本不足" : `${v}${suffix || ""}`;
  }

  function mount(selector, options) {
    injectStyle();
    const root = typeof selector === "string" ? document.querySelector(selector) : selector;
    if (!root) throw new Error("RabbitStrategyGrowth: mount target not found");
    const cfg = Object.assign({ onReview: null, onApprove: null }, options || {});
    root.innerHTML = `
      <section class="rabbit-growth-card">
        <div class="rabbit-growth-head">
          <div class="rabbit-growth-title"><span>🐰</span><span>策略成长</span></div>
          <span class="rabbit-growth-badge" data-role="status">积累样本中</span>
        </div>
        <div class="rabbit-growth-grid">
          <div class="rabbit-growth-stat"><div class="rabbit-growth-label">当前版本</div><div class="rabbit-growth-value" data-role="version">--</div></div>
          <div class="rabbit-growth-stat"><div class="rabbit-growth-label">已学习信号</div><div class="rabbit-growth-value" data-role="signals">0</div></div>
          <div class="rabbit-growth-stat"><div class="rabbit-growth-label">近30次净胜率</div><div class="rabbit-growth-value" data-role="winrate">样本不足</div></div>
          <div class="rabbit-growth-stat"><div class="rabbit-growth-label">挑战版本</div><div class="rabbit-growth-value" data-role="challenger">暂无</div></div>
        </div>
        <div class="rabbit-growth-note" data-role="message">盘中只记录，收盘后学习；未经验证不会修改正式参数。</div>
        <div class="rabbit-growth-actions">
          <button class="rabbit-growth-btn" data-action="review">查看学习报告</button>
          <button class="rabbit-growth-btn primary" data-action="approve" hidden>确认升级</button>
        </div>
      </section>`;
    root.querySelector('[data-action="review"]').onclick = () => cfg.onReview && cfg.onReview();
    root.querySelector('[data-action="approve"]').onclick = () => cfg.onApprove && cfg.onApprove();
    root.__rabbitGrowth = { cfg };
    return root;
  }

  function update(selector, data) {
    const root = typeof selector === "string" ? document.querySelector(selector) : selector;
    if (!root) return;
    root.querySelector('[data-role="status"]').textContent = data.status || "稳定运行";
    root.querySelector('[data-role="version"]').textContent = data.currentVersion || "--";
    root.querySelector('[data-role="signals"]').textContent = data.signalsLearned || 0;
    root.querySelector('[data-role="winrate"]').textContent = value(data.recentWinRate, "%");
    root.querySelector('[data-role="challenger"]').textContent = data.challengerVersion || "暂无";
    root.querySelector('[data-role="message"]').textContent = data.message || "";
    root.querySelector('[data-action="approve"]').hidden = data.status !== "待确认升级";
  }

  global.RabbitStrategyGrowth = { mount, update };
})(window);
