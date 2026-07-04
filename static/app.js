// 导出函数供HTML调用
if (typeof removePosition === 'function') {
    window.removePosition = removePosition;
}
if (typeof removeStock === 'function') {
    window.removeStock = removeStock;
}

// ========== 一键自动抓取热股 + 多次验算功能 ==========
async function runAutoHotStocksTest() {
    const btn = document.getElementById('auto-hot-stocks-btn');
    const amountInput = document.getElementById('auto-amount');
    const strategySelect = document.getElementById('auto-strategy');
    const runsSelect = document.getElementById('validation-runs');
    const progressDiv = document.getElementById('validation-progress');
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');
    const resultsPanel = document.getElementById('validation-results-panel');
    
    const amount = parseFloat(amountInput?.value) || 100000;
    const strategy = strategySelect?.value || 'zijin_special';
    const validationRuns = parseInt(runsSelect?.value) || 10;
    
    // 显示加载状态
    const originalText = btn.innerHTML;
    btn.innerHTML = '<span class="loading"></span> 正在获取热股...';
    btn.disabled = true;
    progressDiv.style.display = 'block';
    resultsPanel.style.display = 'none';
    
    showToast('正在获取市场热门股票并进行' + validationRuns + '次验算...', 'info');
    
    try {
        const response = await fetch('/api/simulate/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                amount, 
                strategy, 
                count: 10,  // 固定10只热门股
                validation_runs: validationRuns 
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            displayValidationResults(result.data, validationRuns);
            const summary = result.data.summary;
            const consistency = result.data.consistency_report;
            showToast(`验算完成！平均胜率: ${summary.stock_win_rate}%, 一致性得分: ${consistency.consistency_score}/100`, 'success');
        } else {
            showToast(result.error || '自动抓取热股测试失败', 'error');
        }
    } catch (error) {
        console.error('自动抓取热股测试失败:', error);
        showToast('自动抓取热股测试失败，请检查网络', 'error');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
        progressDiv.style.display = 'none';
    }
}

function displayValidationResults(data, totalRuns) {
    const resultsPanel = document.getElementById('validation-results-panel');
    const summaryDiv = document.getElementById('validation-summary');
    const detailsDiv = document.getElementById('validation-details');
    const previewDiv = document.getElementById('hot-stocks-preview');
    
    // 显示测试股票，并突出龙虎榜席位/净买入信息。
    if (data.stocks && data.stocks.length > 0) {
        previewDiv.innerHTML = renderSelectedStocks(data.stocks);
    } else if (data.stock_codes && data.stock_codes.length > 0) {
        let stocksHtml = '<div class="hot-stocks-list"><h4>📊 测试股票</h4><div class="stocks-grid">';
        data.stock_codes.forEach(code => {
            stocksHtml += `<span class="stock-tag">${escapeHtml(code)}</span>`;
        });
        stocksHtml += '</div></div>';
        previewDiv.innerHTML = stocksHtml;
    }
    
    // 显示汇总结果
    const summary = data.summary;
    const consistency = data.consistency_report;
    const isGoodConsistency = consistency.consistency_score >= 70;
    
    summaryDiv.innerHTML = `
        <div class="validation-summary-grid">
            <div class="summary-item">
                <div class="summary-label">测试股票数</div>
                <div class="summary-value">${summary.total_stocks}只</div>
            </div>
            <div class="summary-item">
                <div class="summary-label">验算次数</div>
                <div class="summary-value">${summary.validation_runs}次</div>
            </div>
            <div class="summary-item">
                <div class="summary-label">平均股票胜率</div>
                <div class="summary-value" style="color: ${summary.stock_win_rate >= 50 ? '#51cf66' : '#ff6b6b'}">${summary.stock_win_rate}%</div>
            </div>
            <div class="summary-item">
                <div class="summary-label">平均收益率</div>
                <div class="summary-value" style="color: ${summary.avg_profit_rate >= 0 ? '#51cf66' : '#ff6b6b'}">${summary.avg_profit_rate >= 0 ? '+' : ''}${summary.avg_profit_rate}%</div>
            </div>
            <div class="summary-item highlight">
                <div class="summary-label">一致性得分</div>
                <div class="summary-value" style="color: ${isGoodConsistency ? '#51cf66' : '#ffa500'}">${consistency.consistency_score}/100</div>
            </div>
            <div class="summary-item">
                <div class="summary-label">收益率波动范围</div>
                <div class="summary-value">${consistency.profit_rate_min}% ~ ${consistency.profit_rate_max}%</div>
            </div>
        </div>
    `;
    
    // 显示每次验算的详细结果
    if (data.validation_results && data.validation_results.length > 0) {
        let detailsHtml = '<h4>📊 每次验算详情</h4><div class="validation-runs-table">';
        detailsHtml += '<table class="runs-table"><thead><tr><th>次数</th><th>平均收益率</th><th>股票胜率</th><th>交易胜率</th><th>总交易数</th></tr></thead><tbody>';
        
        data.validation_results.forEach(run => {
            const profitClass = run.avg_profit_rate >= 0 ? 'positive' : 'negative';
            detailsHtml += `
                <tr>
                    <td>第${run.run}次</td>
                    <td class="${profitClass}">${run.avg_profit_rate >= 0 ? '+' : ''}${run.avg_profit_rate}%</td>
                    <td>${run.stock_win_rate}%</td>
                    <td>${run.trade_win_rate}%</td>
                    <td>${run.total_trades}笔</td>
                </tr>
            `;
        });
        
        detailsHtml += '</tbody></table></div>';
        detailsDiv.innerHTML = detailsHtml;
    }
    
    resultsPanel.style.display = 'block';
    resultsPanel.scrollIntoView({ behavior: 'smooth' });
}

function renderSelectedStocks(stocks) {
    const items = stocks.map(stock => {
        const isLhb = Boolean(stock.is_longhubang);
        const netBuy = formatAmount(stock.lhb_net_buy || 0);
        const reason = stock.lhb_reason || '热度/成交活跃';
        const score = stock.lhb_score || 0;
        const meta = isLhb
            ? `<span class="lhb-chip">龙虎榜 ${score}</span><span>净买入 ${netBuy}</span>`
            : '<span class="stock-source">热股池</span>';

        return `
            <div class="selected-stock ${isLhb ? 'is-lhb' : ''}">
                <div class="selected-stock-main">
                    <strong>${escapeHtml(stock.name || stock.code)}</strong>
                    <span>${escapeHtml(stock.code)}</span>
                </div>
                <div class="selected-stock-meta">${meta}</div>
                <div class="selected-stock-reason">${escapeHtml(reason)}</div>
            </div>
        `;
    }).join('');

    return `<div class="hot-stocks-list"><h4>📊 测试股票</h4><div class="selected-stocks-grid">${items}</div></div>`;
}

function formatAmount(value) {
    const amount = Number(value) || 0;
    const sign = amount > 0 ? '+' : '';
    if (Math.abs(amount) >= 100000000) {
        return `${sign}${(amount / 100000000).toFixed(2)}亿`;
    }
    if (Math.abs(amount) >= 10000) {
        return `${sign}${(amount / 10000).toFixed(1)}万`;
    }
    return `${sign}${amount.toFixed(0)}`;
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
}

async function loadLonghubangCandidates() {
    const list = document.getElementById('longhubang-list');
    if (!list) {
        return;
    }

    list.innerHTML = '<div class="empty-state"><p>正在加载龙虎榜数据...</p></div>';

    try {
        const response = await fetch('/api/longhubang?limit=12&days=5');
        const result = await response.json();
        if (!result.success) {
            throw new Error(result.error || '龙虎榜数据加载失败');
        }
        list.innerHTML = renderCandidateList(result.data || [], '暂无龙虎榜候选');
    } catch (error) {
        console.error('龙虎榜加载失败:', error);
        list.innerHTML = '<div class="empty-state"><p>龙虎榜数据加载失败，请稍后刷新</p></div>';
    }
}

async function loadResearchHotStocks() {
    const list = document.getElementById('research-hot-stocks-list');
    if (!list) {
        return;
    }

    list.innerHTML = '<div class="empty-state"><p>正在加载热股数据...</p></div>';

    try {
        const response = await fetch('/api/hot_stocks?limit=12');
        const result = await response.json();
        if (!result.success) {
            throw new Error(result.error || '热股数据加载失败');
        }
        list.innerHTML = renderCandidateList(result.data || [], '暂无热股候选');
    } catch (error) {
        console.error('热股加载失败:', error);
        list.innerHTML = '<div class="empty-state"><p>热股数据加载失败，请稍后刷新</p></div>';
    }
}

function renderCandidateList(stocks, emptyText) {
    if (!stocks.length) {
        return `<div class="empty-state"><p>${escapeHtml(emptyText)}</p></div>`;
    }
    return `<div class="selected-stocks-grid">${stocks.map(renderCandidateCard).join('')}</div>`;
}

function renderCandidateCard(stock) {
    const isLhb = Boolean(stock.is_longhubang);
    const netBuy = formatAmount(stock.lhb_net_buy || 0);
    const score = stock.lhb_score || 0;
    const reason = stock.lhb_reason || (stock.type === 'volume' ? '成交活跃' : '热股池候选');
    const change = Number(stock.change_percent || 0);
    const changeText = change ? `${change > 0 ? '+' : ''}${change.toFixed(2)}%` : '--';
    const meta = isLhb
        ? `<span class="lhb-chip">龙虎榜 ${score}</span><span>净买入 ${netBuy}</span>`
        : '<span class="stock-source">热股池</span>';

    return `
        <div class="selected-stock ${isLhb ? 'is-lhb' : ''}">
            <div class="selected-stock-main">
                <strong>${escapeHtml(stock.name || stock.code)}</strong>
                <span>${escapeHtml(stock.code)}</span>
            </div>
            <div class="selected-stock-meta">
                ${meta}
                <span>涨跌 ${escapeHtml(changeText)}</span>
            </div>
            <div class="selected-stock-reason">${escapeHtml(reason)}</div>
        </div>
    `;
}

function initTabs() {
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.addEventListener('click', event => {
            event.preventDefault();
            const tabName = tab.dataset.tab;

            document.querySelectorAll('.nav-tab').forEach(item => item.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

            tab.classList.add('active');
            const panel = document.getElementById(`${tabName}-tab`);
            if (panel) {
                panel.classList.add('active');
            }

            if (tabName === 'research') {
                loadLonghubangCandidates();
                loadResearchHotStocks();
            }
        });
    });
}

function showToast(message, type = 'info') {
    console.log(`[${type}] ${message}`);
}

// 初始化一键自动抓取热股功能
document.addEventListener('DOMContentLoaded', () => {
    initTabs();

    const autoBtn = document.getElementById('auto-hot-stocks-btn');
    if (autoBtn) {
        autoBtn.addEventListener('click', runAutoHotStocksTest);
    }

    const refreshLonghubangBtn = document.getElementById('refresh-longhubang-btn');
    if (refreshLonghubangBtn) {
        refreshLonghubangBtn.addEventListener('click', loadLonghubangCandidates);
    }

    const refreshHotBtn = document.getElementById('refresh-research-hot-btn');
    if (refreshHotBtn) {
        refreshHotBtn.addEventListener('click', loadResearchHotStocks);
    }
});
