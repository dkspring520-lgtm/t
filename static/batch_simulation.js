// ========== 批量模拟测试功能 ==========

// 一键批量模拟测试多只热门股票
async function runBatchSimulation() {
    const amountInput = document.getElementById('test-amount');
    const strategySelect = document.getElementById('test-strategy');
    const countSelect = document.getElementById('test-stock-count');
    
    const amount = parseFloat(amountInput?.value) || 100000;
    const strategy = strategySelect?.value || 'simple_bias';
    const stockCount = parseInt(countSelect?.value) || 10;
    
    showToast(`正在批量模拟测试 ${stockCount} 只热门股票...`, 'info');
    
    try {
        // 调用后端API进行批量模拟测试
        const response = await fetch('/api/simulate/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                amount: amount,
                strategy: strategy,
                count: stockCount
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            displayBatchSimulationResult(result.data);
            showToast(`批量模拟测试完成！平均胜率: ${result.data.summary.avg_win_rate}%`, 'success');
        } else {
            showToast(result.error || '批量模拟测试失败', 'error');
        }
    } catch (error) {
        console.error('批量模拟测试失败:', error);
        showToast('批量模拟测试失败，请检查网络连接', 'error');
    }
}

// 获取策略显示名称
function getStrategyName(strategy) {
    const names = {
        'simple_bias': '简单乖离策略',
        'advanced_bias': '进阶乖离策略',
        'zijin_standard': '紫金专用版',
        'zijin_profit': '紫金高利润版'
    };
    return names[strategy] || strategy;
}

// 在页面加载完成后初始化批量测试事件
function initBatchSimulationListeners() {
    const batchBtn = document.getElementById('batch-test-btn');
    const closeBtn = document.getElementById('close-batch-test');
    
    if (batchBtn) {
        batchBtn.addEventListener('click', runBatchSimulation);
    }
    
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            const panel = document.getElementById('batch-test-result-panel');
            if (panel) panel.style.display = 'none';
        });
    }
}

// ========== 一键自动抓取热股 + 多次验算功能 ==========
async function runAutoHotStocksTest() {
    const btn = document.getElementById('auto-hot-stocks-btn');
    const amountInput = document.getElementById('auto-amount');
    const strategySelect = document.getElementById('auto-strategy');
    const runsSelect = document.getElementById('validation-runs');
    const progressDiv = document.getElementById('validation-progress');
    const resultsPanel = document.getElementById('validation-results-panel');
    
    const amount = parseFloat(amountInput?.value) || 100000;
    const strategy = strategySelect?.value || 'zijin_special';
    const validationRuns = parseInt(runsSelect?.value) || 10;
    
    const originalText = btn.innerHTML;
    btn.innerHTML = '正在获取热股...';
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
                count: 10,
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
    
    if (data.stock_codes && data.stock_codes.length > 0) {
        let stocksHtml = '<div class="hot-stocks-list"><h4>测试股票</h4><div class="stocks-grid">';
        data.stock_codes.forEach(code => {
            stocksHtml += `<span class="stock-tag">${code}</span>`;
        });
        stocksHtml += '</div></div>';
        previewDiv.innerHTML = stocksHtml;
    }
    
    const summary = data.summary;
    const consistency = data.consistency_report;
    
    summaryDiv.innerHTML = `
        <div class="validation-summary-grid">
            <div class="summary-item"><div class="summary-label">测试股票数</div><div class="summary-value">${summary.total_stocks}只</div></div>
            <div class="summary-item"><div class="summary-label">验算次数</div><div class="summary-value">${summary.validation_runs}次</div></div>
            <div class="summary-item"><div class="summary-label">平均股票胜率</div><div class="summary-value">${summary.stock_win_rate}%</div></div>
            <div class="summary-item"><div class="summary-label">平均收益率</div><div class="summary-value">${summary.avg_profit_rate}%</div></div>
            <div class="summary-item highlight"><div class="summary-label">一致性得分</div><div class="summary-value">${consistency.consistency_score}/100</div></div>
            <div class="summary-item"><div class="summary-label">收益率波动范围</div><div class="summary-value">${consistency.profit_rate_min}% ~ ${consistency.profit_rate_max}%</div></div>
        </div>
    `;
    
    if (data.validation_results && data.validation_results.length > 0) {
        let detailsHtml = '<h4>每次验算详情</h4><table class="runs-table"><thead><tr><th>次数</th><th>平均收益率</th><th>股票胜率</th><th>交易胜率</th><th>总交易数</th></tr></thead><tbody>';
        data.validation_results.forEach(run => {
            detailsHtml += `<tr><td>第${run.run}次</td><td>${run.avg_profit_rate}%</td><td>${run.stock_win_rate}%</td><td>${run.trade_win_rate}%</td><td>${run.total_trades}笔</td></tr>`;
        });
        detailsHtml += '</tbody></table>';
        detailsDiv.innerHTML = detailsHtml;
    }
    
    resultsPanel.style.display = 'block';
}

// 初始化一键自动抓取热股功能
function initAutoHotStocksListeners() {
    const autoBtn = document.getElementById('auto-hot-stocks-btn');
    if (autoBtn) {
        autoBtn.addEventListener('click', runAutoHotStocksTest);
    }
}

// 在页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    initBatchSimulationListeners();
    initAutoHotStocksListeners();
});
