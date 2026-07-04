// ===== Fixed loadSavedData =====
function loadSavedData() {
    if (typeof chrome !== 'undefined' && chrome.storage) {
        chrome.storage.local.get(['holdings', 'currentStock'], (result) => {
            if (result.holdings) {
                holdings = result.holdings;
                const nameEl = document.getElementById('holdingName');
                const qtyEl = document.getElementById('holdingQuantity') || document.getElementById('holdingQty');
                const costEl = document.getElementById('holdingAvgCost') || document.getElementById('holdingCost');
                const maxPosEl = document.getElementById('maxPosition');
                if (nameEl) nameEl.value = holdings.name || '';
                if (qtyEl) qtyEl.value = holdings.quantity || '';
                if (costEl) costEl.value = holdings.avgCost || '';
                if (maxPosEl) maxPosEl.value = holdings.maxPosition || 10000;
            }
            if (result.currentStock) {
                document.getElementById('stockCode').value = result.currentStock;
                document.getElementById('simStockCode').value = result.currentStock;
            }
        });
    }
}
