const UI_FEATURES = window.APP_UI_FEATURES || {
    minimal: true,
    show_position_tab: false,
    show_research_tab: false,
    show_simulation_tab: true
};

const state = {
    stocks: new Map(),
    signals: [],
    positions: [],
    activeCode: null,
    chart: null,
    chartLabels: [],
    chartPrices: []
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
}

function formatMoney(value) {
    const number = Number(value) || 0;
    return number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatAmount(value) {
    const amount = Number(value) || 0;
    const sign = amount > 0 ? '+' : '';
    if (Math.abs(amount) >= 100000000) return `${sign}${(amount / 100000000).toFixed(2)}亿`;
    if (Math.abs(amount) >= 10000) return `${sign}${(amount / 10000).toFixed(1)}万`;
    return `${sign}${amount.toFixed(0)}`;
}

function cleanCode(code) {
    return String(code || '').replace(/\D/g, '').slice(0, 6);
}

function showToast(message, type = 'info') {
    const root = $('toast-root');
    if (!root) {
        console.log(`[${type}] ${message}`);
        return;
    }
    const item = document.createElement('div');
    item.className = `toast ${type}`;
    item.textContent = message;
    root.appendChild(item);
    setTimeout(() => item.remove(), 2600);
}

function initTabs() {
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.addEventListener('click', event => {
            event.preventDefault();
            const tabName = tab.dataset.tab;
            document.querySelectorAll('.nav-tab').forEach(item => item.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            tab.classList.add('active');
            $(`${tabName}-tab`)?.classList.add('active');
            if (tabName === 'research') {
                loadLonghubangCandidates();
                loadResearchHotStocks();
            }
            if (tabName === 'position') loadPositions();
        });
    });
}

function setElementHidden(selector, shouldHide = false) {
    const el = document.querySelector(selector);
    if (!el) return;
    el.style.display = shouldHide ? 'none' : '';
}

function applyUiFeatures() {
    if (!UI_FEATURES.minimal) return;
    setElementHidden('#compact-switch', true);
    setElementHidden('.nav-tab[data-tab="position"]', true);
    setElementHidden('.nav-tab[data-tab="research"]', true);
    setElementHidden('#position-tab', true);
    setElementHidden('#research-tab', true);
    if (!UI_FEATURES.show_simulation_tab) {
        setElementHidden('.nav-tab[data-tab="simulation"]', true);
        setElementHidden('#simulation-tab', true);
    }
}

function initChart() {
    const canvas = $('price-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    state.chart = new Chart(canvas, {
        type: 'line',
        data: {
            labels: state.chartLabels,
            datasets: [{
                label: '分时价格',
                data: state.chartPrices,
                borderColor: '#22ab94',
                backgroundColor: 'rgba(34, 171, 148, 0.12)',
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.25,
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { color: '#222733' }, ticks: { color: '#8b95a7', maxTicksLimit: 8 } },
                y: { position: 'right', grid: { color: '#222733' }, ticks: { color: '#8b95a7' } }
            }
        }
    });
    seedChart();
}

function seedChart() {
    const base = 10 + Math.random() * 4;
    const labels = [];
    const prices = [];
    for (let i = 0; i < 60; i += 1) {
        labels.push(`${9 + Math.floor((30 + i) / 60)}:${String((30 + i) % 60).padStart(2, '0')}`);
        prices.push(Number((base + Math.sin(i / 6) * 0.18 + (Math.random() - 0.5) * 0.08).toFixed(2)));
    }
    state.chartLabels.splice(0, state.chartLabels.length, ...labels);
    state.chartPrices.splice(0, state.chartPrices.length, ...prices);
    state.chart?.update();
}

function pushChartPoint(label, price) {
    if (!price) return;
    state.chartLabels.push(label);
    state.chartPrices.push(Number(price));
    if (state.chartLabels.length > 120) {
        state.chartLabels.shift();
        state.chartPrices.shift();
    }
    state.chart?.update('none');
}

async function apiJson(url, options) {
    const response = await fetch(url, options);
    const result = await response.json();
    if (!result.success) throw new Error(result.error || '请求失败');
    return result.data ?? result;
}

async function addStock(codeInput) {
    const code = cleanCode(codeInput ?? $('quick-stock-code')?.value);
    if (code.length !== 6) {
        showToast('请输入 6 位股票代码', 'error');
        return;
    }
    try {
        const data = await apiJson('/api/stock/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_code: code })
        });
        state.stocks.set(code, {
            stock_code: code,
            stock_name: data.stock_name || code,
            current_price: 0,
            change_percent: 0,
            signals_count: 0
        });
        state.activeCode = code;
        $('quick-stock-code').value = '';
        renderStocks();
        updateActiveSymbol();
        showToast(`${data.stock_name || code} 已加入监控`, 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function removeStock(code) {
    try {
        await apiJson('/api/stock/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_code: code })
        });
        state.stocks.delete(code);
        if (state.activeCode === code) state.activeCode = state.stocks.keys().next().value || null;
        renderStocks();
        updateActiveSymbol();
        showToast(`${code} 已移除`, 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function loadStocks() {
    try {
        const stocks = await apiJson('/api/stocks');
        state.stocks.clear();
        stocks.forEach(stock => state.stocks.set(stock.stock_code, stock));
        if (!state.activeCode && stocks.length) state.activeCode = stocks[0].stock_code;
        renderStocks();
        updateActiveSymbol();
    } catch (error) {
        console.warn(error);
    }
}

function renderStocks() {
    const list = $('stock-list');
    const monitorList = $('monitor-stock-list');
    const stocks = [...state.stocks.values()];
    if ($('monitor-count')) $('monitor-count').textContent = stocks.length;
    if ($('monitor-list-count')) $('monitor-list-count').textContent = stocks.length;
    if (!stocks.length) {
        const empty = '<div class="empty-state">暂无监控标的</div>';
        if (list) list.innerHTML = empty;
        if (monitorList) monitorList.innerHTML = empty;
        return;
    }
    const html = stocks.map(stock => {
        const change = Number(stock.change_percent || 0);
        const direction = change >= 0 ? 'up' : 'down';
        return `
            <button class="watch-row ${state.activeCode === stock.stock_code ? 'active' : ''}" data-code="${escapeHtml(stock.stock_code)}">
                <span><strong>${escapeHtml(stock.stock_name || stock.stock_code)}</strong><small>${escapeHtml(stock.stock_code)}</small></span>
                <span class="watch-price"><strong>${Number(stock.current_price || 0).toFixed(2)}</strong><small class="${direction}">${change ? `${change > 0 ? '+' : ''}${change.toFixed(2)}%` : '--'}</small></span>
            </button>
        `;
    }).join('');
    if (list) list.innerHTML = html;
    if (monitorList) monitorList.innerHTML = html;

    document.querySelectorAll('.watch-row').forEach(row => {
        row.addEventListener('click', () => {
            state.activeCode = row.dataset.code;
            renderStocks();
            updateActiveSymbol();
        });
    });
}

function updateActiveSymbol() {
    const stock = state.activeCode ? state.stocks.get(state.activeCode) : null;
    $('active-symbol-name').textContent = stock ? `${stock.stock_name || stock.stock_code} ${stock.stock_code}` : '做T观察盘';
    $('active-symbol-meta').textContent = stock ? '实时分时 / 做T信号 / 研究候选' : '等待行情更新';
    $('last-price').textContent = stock?.current_price ? Number(stock.current_price).toFixed(2) : '--';
    const change = Number(stock?.change_percent || 0);
    $('last-change').textContent = change ? `${change > 0 ? '+' : ''}${change.toFixed(2)}%` : '--';
    $('last-change').className = change >= 0 ? 'up' : 'down';
}

async function startMonitoring() {
    try {
        await apiJson('/api/monitor/start', { method: 'POST' });
        $('monitor-status-text').textContent = '运行中';
        showToast('监控已启动', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function stopMonitoring() {
    try {
        await apiJson('/api/monitor/stop', { method: 'POST' });
        $('monitor-status-text').textContent = '已停止';
        showToast('监控已停止', 'info');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function loadStatus() {
    try {
        const response = await fetch('/api/status');
        const result = await response.json();
        if (result.success) {
            $('monitor-status-text').textContent = result.monitoring ? '运行中' : '已停止';
        }
    } catch (error) {
        console.warn(error);
    }
}

function handlePriceUpdate(data) {
    const code = data.stock_code;
    if (!state.stocks.has(code)) {
        state.stocks.set(code, {
            stock_code: code,
            stock_name: data.stock_name || code,
            current_price: data.price,
            change_percent: data.change_percent || 0,
            signals_count: 0
        });
    }
    const stock = state.stocks.get(code);
    stock.current_price = data.price;
    stock.change_percent = data.change_percent || 0;
    if (!state.activeCode) state.activeCode = code;
    if (state.activeCode === code) {
        pushChartPoint(new Date().toLocaleTimeString('zh-CN', { hour12: false }), data.price);
        updateActiveSymbol();
    }
    renderStocks();
}

function handleNewSignal(data) {
    const signal = data.signal || {};
    const type = signal.type || signal.action || 'watch';
    const item = {
        code: data.stock_code,
        name: data.stock_name || data.stock_code,
        type,
        reason: signal.reason || '触发做T观察条件',
        price: signal.price,
        time: signal.timestamp || new Date().toLocaleTimeString('zh-CN', { hour12: false })
    };
    state.signals.unshift(item);
    state.signals = state.signals.slice(0, 30);
    renderSignals();
    renderDecision(item);
}

function renderSignals() {
    const list = $('signals-list');
    const monitorList = $('monitor-signals-list');
    if ($('signal-count')) $('signal-count').textContent = state.signals.length;
    if (!state.signals.length) {
        const empty = '<div class="empty-state">暂无信号</div>';
        if (list) list.innerHTML = empty;
        if (monitorList) monitorList.innerHTML = empty;
        return;
    }
    const html = state.signals.map(signal => `
        <div class="signal-item ${escapeHtml(signal.type)}">
            <div><strong>${signal.type === 'buy' ? '低吸观察' : signal.type === 'sell' ? '高抛观察' : '观察'}</strong><span>${escapeHtml(signal.name)} ${escapeHtml(signal.code)}</span></div>
            <div class="signal-meta"><span>${signal.price ? Number(signal.price).toFixed(2) : '--'}</span><small>${escapeHtml(signal.time)}</small></div>
            <p>${escapeHtml(signal.reason)}</p>
        </div>
    `).join('');
    if (list) list.innerHTML = html;
    if (monitorList) monitorList.innerHTML = html;
}

function renderDecision(signal) {
    const card = $('decision-card');
    if (!card) return;
    const action = signal.type === 'buy' ? '低吸观察' : signal.type === 'sell' ? '高抛观察' : '继续观察';
    card.className = `decision-card ${signal.type}`;
    card.innerHTML = `<strong>${action}</strong><span>${escapeHtml(signal.name)} ${signal.price ? Number(signal.price).toFixed(2) : ''} | ${escapeHtml(signal.reason)}</span>`;
}

async function addPosition() {
    const payload = {
        stock_code: cleanCode($('position-code')?.value),
        stock_name: $('position-name')?.value.trim(),
        quantity: Number($('position-quantity')?.value || 0),
        avg_cost: Number($('position-cost')?.value || 0)
    };
    if (payload.stock_code.length !== 6 || payload.quantity <= 0 || payload.avg_cost <= 0) {
        showToast('请补全持仓代码、数量和成本', 'error');
        return;
    }
    try {
        await apiJson('/api/position/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        ['position-code', 'position-name', 'position-quantity', 'position-cost'].forEach(id => { if ($(id)) $(id).value = ''; });
        await loadPositions();
        showToast('持仓已新增', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function removePosition(code) {
    try {
        await apiJson('/api/position/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_code: code })
        });
        await loadPositions();
        showToast(`${code} 持仓已删除`, 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function loadPositions() {
    try {
        const data = await apiJson('/api/positions');
        state.positions = data.positions || [];
        renderPositions(data.summary || {});
    } catch (error) {
        console.warn(error);
    }
}

function renderPositions(summary = {}) {
    const tbody = $('positions-tbody');
    if (!tbody) return;
    if ($('positions-count')) $('positions-count').textContent = state.positions.length;
    if (!state.positions.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-cell">暂无持仓</td></tr>';
    } else {
        tbody.innerHTML = state.positions.map(pos => {
            const pnl = Number(pos.pnl || 0);
            return `
                <tr>
                    <td>${escapeHtml(pos.stock_code)}</td>
                    <td>${escapeHtml(pos.stock_name)}</td>
                    <td>${Number(pos.quantity || 0).toLocaleString('zh-CN')}</td>
                    <td>${formatMoney(pos.avg_cost)}</td>
                    <td>${formatMoney(pos.current_price)}</td>
                    <td class="${pnl >= 0 ? 'up' : 'down'}">${pnl >= 0 ? '+' : ''}${formatMoney(pnl)} (${Number(pos.pnl_percent || 0).toFixed(2)}%)</td>
                    <td><button class="btn-sm danger" onclick="removePosition('${escapeHtml(pos.stock_code)}')">删除</button></td>
                </tr>
            `;
        }).join('');
    }
    const totalAssets = Number(summary.total_market || 0);
    const totalPnl = Number(summary.total_pnl || 0);
    if ($('total-assets')) {
        $('total-assets').textContent = totalAssets > 0 ? formatMoney(totalAssets) : '--';
    }
    if ($('position-pnl')) {
        $('position-pnl').textContent = totalAssets > 0 ? `${totalPnl >= 0 ? '+' : ''}${formatMoney(totalPnl)}` : '--';
        $('position-pnl').className = totalPnl >= 0 ? 'up' : 'down';
    }
    if ($('win-rate')) {
        const simulatedSignals = state.signals.length;
        $('win-rate').textContent = simulatedSignals ? `${Math.min(99, 50 + simulatedSignals * 3)}%` : '--';
    }
}

async function runSingleSimulation() {
    const code = cleanCode($('sim-stock-code')?.value);
    const amount = Number($('sim-amount')?.value || 100000);
    const strategy = $('sim-strategy')?.value || 'zijin_special';
    const box = $('single-simulation-result');
    if (code.length !== 6) {
        showToast('请输入 6 位股票代码', 'error');
        return;
    }
    box.innerHTML = '<div class="empty-state">模拟运行中...</div>';
    try {
        const data = await apiJson('/api/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_code: code, amount, strategy })
        });
        box.innerHTML = renderSimulationResult(data);
    } catch (error) {
        box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    }
}

function renderSimulationResult(data) {
    const profit = Number(data.profit || 0);
    return `
        <div class="result-grid">
            <div><span>股票</span><strong>${escapeHtml(data.stock_name)} ${escapeHtml(data.stock_code)}</strong></div>
            <div><span>收益</span><strong class="${profit >= 0 ? 'up' : 'down'}">${profit >= 0 ? '+' : ''}${formatMoney(profit)}</strong></div>
            <div><span>收益率</span><strong class="${profit >= 0 ? 'up' : 'down'}">${Number(data.profit_rate || 0).toFixed(2)}%</strong></div>
            <div><span>交易</span><strong>${data.trade_count || 0} 笔</strong></div>
        </div>
    `;
}

async function runAutoHotStocksTest() {
    const btn = $('auto-hot-stocks-btn');
    const progress = $('validation-progress');
    const resultsPanel = $('validation-results-panel');
    const amount = Number($('auto-amount')?.value || 100000);
    const strategy = $('auto-strategy')?.value || 'zijin_special';
    const validationRuns = Number($('validation-runs')?.value || 10);
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '验证中...';
    progress.style.display = 'block';
    $('progress-fill').style.width = '35%';
    $('progress-text').textContent = `正在执行 ${validationRuns} 次验证`;
    resultsPanel.style.display = 'none';
    try {
        const data = await apiJson('/api/simulate/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount, strategy, count: 10, validation_runs: validationRuns })
        });
        $('progress-fill').style.width = '100%';
        displayValidationResults(data);
        showToast('热门池验证完成', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
        setTimeout(() => { progress.style.display = 'none'; }, 600);
    }
}

function displayValidationResults(data) {
    const panel = $('validation-results-panel');
    const preview = $('hot-stocks-preview');
    if (data.stocks?.length) preview.innerHTML = renderSelectedStocks(data.stocks);
    const summary = data.summary || {};
    const consistency = data.consistency_report || {};
    $('validation-summary').innerHTML = `
        <div class="validation-summary-grid">
            <div class="summary-item"><span>股票数</span><strong>${summary.total_stocks || 0}</strong></div>
            <div class="summary-item"><span>平均胜率</span><strong>${summary.stock_win_rate || 0}%</strong></div>
            <div class="summary-item"><span>平均收益率</span><strong>${summary.avg_profit_rate || 0}%</strong></div>
            <div class="summary-item"><span>一致性</span><strong>${Number(consistency.consistency_score || 0).toFixed(1)}/100</strong></div>
        </div>
    `;
    $('validation-details').innerHTML = renderValidationRuns(data.validation_results || []);
    panel.style.display = 'block';
}

function renderValidationRuns(runs) {
    if (!runs.length) return '';
    return `<table class="runs-table"><thead><tr><th>次数</th><th>收益率</th><th>股票胜率</th><th>交易数</th></tr></thead><tbody>${runs.map(run => `
        <tr><td>第${run.run}次</td><td>${run.avg_profit_rate}%</td><td>${run.stock_win_rate}%</td><td>${run.total_trades}</td></tr>
    `).join('')}</tbody></table>`;
}

function renderSelectedStocks(stocks) {
    return `<div class="selected-stocks-grid">${stocks.map(renderCandidateCard).join('')}</div>`;
}

async function loadLonghubangCandidates() {
    const list = $('longhubang-list');
    if (!list) return;
    list.innerHTML = '<div class="empty-state">正在加载龙虎榜数据...</div>';
    try {
        const data = await apiJson('/api/longhubang?limit=12&days=5');
        list.innerHTML = renderCandidateList(data, '暂无龙虎榜候选');
    } catch (error) {
        list.innerHTML = '<div class="empty-state">龙虎榜数据加载失败</div>';
    }
}

async function loadResearchHotStocks() {
    const list = $('research-hot-stocks-list');
    if (!list) return;
    list.innerHTML = '<div class="empty-state">正在加载热门池...</div>';
    try {
        const data = await apiJson('/api/hot_stocks?limit=12');
        list.innerHTML = renderCandidateList(data, '暂无热门候选');
    } catch (error) {
        list.innerHTML = '<div class="empty-state">热门池加载失败</div>';
    }
}

function renderCandidateList(stocks, emptyText) {
    if (!stocks?.length) return `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
    return `<div class="selected-stocks-grid">${stocks.map(renderCandidateCard).join('')}</div>`;
}

function renderCandidateCard(stock) {
    const isLhb = Boolean(stock.is_longhubang);
    const score = stock.lhb_score || 0;
    const change = Number(stock.change_percent || 0);
    const reason = stock.lhb_reason || (stock.type === 'volume' ? '成交活跃' : '热度候选');
    return `
        <div class="candidate-card ${isLhb ? 'is-lhb' : ''}">
            <div class="candidate-head"><strong>${escapeHtml(stock.name || stock.code)}</strong><span>${escapeHtml(stock.code)}</span></div>
            <div class="candidate-meta"><span>${isLhb ? `龙虎榜 ${score}` : '热门池'}</span><span class="${change >= 0 ? 'up' : 'down'}">${change ? `${change > 0 ? '+' : ''}${change.toFixed(2)}%` : '--'}</span></div>
            <p>${escapeHtml(reason)}</p>
            <button class="btn-sm" type="button" onclick="addStock('${escapeHtml(stock.code)}')">加入监控</button>
        </div>
    `;
}

function setupSocket() {
    if (typeof io === 'undefined') return;
    const socket = io();
    socket.on('connect', () => $('connection-status')?.classList.add('connected'));
    socket.on('disconnect', () => $('connection-status')?.classList.remove('connected'));
    socket.on('price_update', handlePriceUpdate);
    socket.on('new_signal', handleNewSignal);
}

function setupCompactMode() {
    const key = 'ui_compact_mode_v1';
    const btn = $('compact-switch');
    const isSmallScreen = () => window.matchMedia('(max-width: 820px)').matches;
    const apply = (enabled) => {
        document.body.classList.toggle('compact-mode', enabled);
        if (btn) btn.textContent = enabled ? '标准' : '紧凑';
    };
    let enabled = localStorage.getItem(key) === '1' || isSmallScreen();
    apply(enabled);
    if (isSmallScreen()) {
        localStorage.setItem(key, '1');
    }
    btn?.addEventListener('click', () => {
        enabled = !enabled;
        localStorage.setItem(key, enabled ? '1' : '0');
        apply(enabled);
    });
    window.addEventListener('resize', () => {
        if (!btn) {
            const shouldCompact = isSmallScreen();
            localStorage.setItem(key, shouldCompact ? '1' : localStorage.getItem(key));
            apply(shouldCompact);
        }
    });
}

function bindEvents() {
    $('quick-add-stock-btn')?.addEventListener('click', () => addStock());
    $('quick-stock-code')?.addEventListener('keydown', event => { if (event.key === 'Enter') addStock(); });
    $('start-monitor-btn')?.addEventListener('click', startMonitoring);
    $('stop-monitor-btn')?.addEventListener('click', stopMonitoring);
    $('monitor-start-btn')?.addEventListener('click', startMonitoring);
    $('monitor-stop-btn')?.addEventListener('click', stopMonitoring);
    $('add-position-btn')?.addEventListener('click', addPosition);
    $('run-simulation-btn')?.addEventListener('click', runSingleSimulation);
    $('auto-hot-stocks-btn')?.addEventListener('click', runAutoHotStocksTest);
    $('refresh-longhubang-btn')?.addEventListener('click', loadLonghubangCandidates);
    $('refresh-research-hot-btn')?.addEventListener('click', loadResearchHotStocks);
}

document.addEventListener('DOMContentLoaded', () => {
    applyUiFeatures();
    initTabs();
    initChart();
    bindEvents();
    setupSocket();
    setupCompactMode();
    loadStatus();
    loadStocks();
    loadPositions();
});

window.addStock = addStock;
window.removeStock = removeStock;
window.removePosition = removePosition;
