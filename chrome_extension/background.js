// background.js - 后台服务工作线程
// 插件安裝時初始化
chrome.runtime.onInstalled.addListener(() => {
    console.log('A股做T監控助手已安裝');

    // 設置側邊欄行為：點擊圖標打開側邊欄
    if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
        chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
    }

    // 設置默認存儲
    chrome.storage.local.set({
        holdings: {
            name: '',
            quantity: 0,
            avgCost: 0,
            maxPosition: 10000
        },
        signals: []
    });
});

// === Keepalive: 防止 Service Worker 被回收 ===
chrome.alarms.create('keepalive', { periodInMinutes: 0.33 });
chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === 'keepalive') {
        chrome.storage.local.get(['keepalive'], () => {
            chrome.storage.local.set({ keepalive: Date.now() });
        });
    }
});

// 定時清理舊數據
chrome.alarms?.create?.('cleanup', { periodInMinutes: 60 });
chrome.alarms?.onAlarm?.addListener((alarm) => {
    if (alarm.name === 'cleanup') {
        chrome.storage.local.get(['signals'], (result) => {
            if (result.signals) {
                const oneWeekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
                const filtered = result.signals.filter(s => s.timestamp > oneWeekAgo);
                chrome.storage.local.set({ signals: filtered });
            }
        });
    }
});
