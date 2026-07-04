// market_data.js - 市场数据获取模块（连接本地服务器）

const API_BASE_URL = 'http://localhost:5001';


// 联机状态管理
const ConnectionStatus = {
    isOnline: false,
    lastCheck: null,
    checkInterval: null,
    
    async checkServer() {
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 3000);
            
            const response = await fetch(`${API_BASE_URL}/api/quote/000001`, {
                method: 'GET',
                signal: controller.signal
            });
            
            clearTimeout(timeoutId);
            this.isOnline = response.ok;
            this.lastCheck = Date.now();
            return this.isOnline;
        } catch (error) {
            this.isOnline = false;
            this.lastCheck = Date.now();
            return false;
        }
    },
    
    startAutoCheck() {
        if (this.checkInterval) clearInterval(this.checkInterval);
        this.checkInterval = setInterval(() => this.checkServer(), 30000);
    },
    
    stopAutoCheck() {
        if (this.checkInterval) {
            clearInterval(this.checkInterval);
            this.checkInterval = null;
        }
    },
    
    getStatus() {
        return {
            isOnline: this.isOnline,
            lastCheck: this.lastCheck,
            mode: this.isOnline ? '联机模式' : '离线模式',
            modeIcon: this.isOnline ? '📡' : '👁️',
            color: this.isOnline ? '#00d46a' : '#ffa502'
        };
    }
};

const MarketData = {
    // 股票代码映射
    getEastMoneyCode(stockCode) {
        if (stockCode.startsWith('6')) {
            return `1.${stockCode}`;
        } else if (stockCode.startsWith('3') || stockCode.startsWith('0')) {
            return `0.${stockCode}`;
        }
        return `0.${stockCode}`;
    },

    // 获取实时行情（通过本地服务器）
    async fetchQuote(stockCode) {
        try {
            const url = `${API_BASE_URL}/api/quote/${stockCode}`;
            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json'
                }
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            // 防止空响应导致JSON解析失败
            const text = await response.text();
            if (!text || text.trim() === '') {
                throw new Error('服务器返回空响应');
            }
            const data = JSON.parse(text);
            
            if (data.success) {
                return {
                    code: data.code,
                    name: data.name,
                    current: data.current,
                    open: data.open,
                    high: data.high,
                    low: data.low,
                    prevClose: data.prevClose,
                    volume: data.volume,
                    turnover: data.turnover,
                    change: data.change,
                    timestamp: Date.now()
                };
            }
            
            console.warn('服务器返回无数据，使用备选方案');
            return this.fetchQuoteBackup(stockCode);
        } catch (error) {
            console.error('连接本地服务器失败:', error);
            return this.fetchQuoteBackup(stockCode);
        }
    },

    // 备选方案：直接调用东方财富API
    async fetchQuoteBackup(stockCode) {
        try {
            const eastCode = this.getEastMoneyCode(stockCode);
            const url = `https://push2.eastmoney.com/api/qt/stock/get?secid=${eastCode}&fields=f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f170`;
            
            const response = await fetch(url);
            const text = await response.text();
            
            const match = text.match(/"data":({[^}]+})/);
            if (match) {
                const data = JSON.parse(match[1]);
                return {
                    code: data.f57 || stockCode,
                    name: data.f58 || '',
                    current: parseFloat(data.f43) / 100,
                    open: parseFloat(data.f46) / 100,
                    high: parseFloat(data.f44) / 100,
                    low: parseFloat(data.f45) / 100,
                    prevClose: parseFloat(data.f60) / 100,
                    volume: parseInt(data.f47) || 0,
                    turnover: parseFloat(data.f48) || 0,
                    change: parseFloat(data.f170) / 100,
                    timestamp: Date.now()
                };
            }
            return null;
        } catch (error) {
            console.error('备选行情获取也失败:', error);
            return null;
        }
    },

    // 获取分时数据（通过本地服务器）
    async fetchIntradayData(stockCode) {
        try {
            const url = `${API_BASE_URL}/api/intraday/${stockCode}`;
            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json'
                }
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            // 检查响是否为空
            const text = await response.text();
            if (!text || text.trim() === '') {
                throw new Error('服务器返回空响应');
            }
            
            const result = JSON.parse(text);
            
            if (result.success && result.data) {
                // 保存到 localStorage 供模拟测试使用
                this.saveIntradayToCache(stockCode, result.data);
                
                return {
                    trends: result.data.trends || [],
                    preClose: result.data.preClose || 0,
                    timestamp: Date.now()
                };
            }
            
            console.warn('服务器分时数据无效，使用备选方案');
            return this.fetchIntradayBackup(stockCode);
        } catch (error) {
            console.error('连接服务器获取分时失败:', error);
            return this.fetchIntradayBackup(stockCode);
        }
    },

    // 备选方案：直接调用东方财富API
    async fetchIntradayBackup(stockCode) {
        try {
            const eastCode = this.getEastMoneyCode(stockCode);
            const url = `https://push2.eastmoney.com/api/qt/stock/trends2?secid=${eastCode}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&_=${Date.now()}`;
            
            const response = await fetch(url);
            // 防止空响应导致JSON解析失败
            const text = await response.text();
            if (!text || text.trim() === '') {
                throw new Error('服务器返回空响应');
            }
            const data = JSON.parse(text);
            
            if (data && data.data && data.data.trends) {
                const trends = data.data.trends.split(';').map(item => {
                    const parts = item.split(',');
                    if (parts.length >= 4) {
                        const timeStr = parts[0];
                        return {
                            time: `${timeStr.substring(0, 2)}:${timeStr.substring(2, 4)}`,
                            price: parseFloat(parts[1]),
                            volume: parseInt(parts[2]),
                            avgPrice: parseFloat(parts[3])
                        };
                    }
                    return null;
                }).filter(t => t !== null);
                
                const result = {
                    trends: trends,
                    preClose: data.data.preClose || (trends.length > 0 ? trends[0].price : 0),
                    timestamp: Date.now()
                };
                
                // 保存到缓存
                this.saveIntradayToCache(stockCode, result);
                
                return result;
            }
            return { trends: [], preClose: 0, timestamp: Date.now() };
        } catch (error) {
            console.error('备选分时获取失败:', error);
            return { trends: [], preClose: 0, timestamp: Date.now() };
        }
    },

    // 保存分时数据到缓存
    saveIntradayToCache(stockCode, data) {
        try {
            const key = `intraday_${stockCode}_${new Date().toISOString().split('T')[0]}`;
            localStorage.setItem(key, JSON.stringify({
                code: stockCode,
                date: new Date().toISOString().split('T')[0],
                trends: data.trends || data,
                preClose: data.preClose || 0,
                savedAt: Date.now()
            }));
        } catch (e) {
            console.warn('无法保存到缓存:', e);
        }
    },

    // 从缓存载入分时数据
    loadIntradayFromCache(stockCode) {
        try {
            const key = `intraday_${stockCode}_${new Date().toISOString().split('T')[0]}`;
            const data = localStorage.getItem(key);
            if (data) {
                return JSON.parse(data);
            }
        } catch (e) {
            console.warn('无法从缓存载入:', e);
        }
        return null;
    },

    // 获取历史分时数据（用于模拟测试）
    async fetchHistoryIntraday(stockCode, dateStr) {
        try {
            const url = `${API_BASE_URL}/api/history/${stockCode}/${dateStr}?simulate=true`;
            const response = await fetch(url);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            // 防止空响应导致JSON解析失败
            const text = await response.text();
            if (!text || text.trim() === '') {
                throw new Error('服务器返回空响应');
            }
            const result = JSON.parse(text);
            
            if (result.success && result.data) {
                return {
                    trends: result.data.trends || [],
                    preClose: result.data.preClose || 0,
                    source: result.source || 'api'
                };
            }
            
            return null;
        } catch (error) {
            console.error('获取历史分时失败:', error);
            return null;
        }
    },

    // 获取热门股票（通过本地服务器）
    async getTopHotStocks(limit = 10) {
        try {
            const url = `${API_BASE_URL}/api/hot_stocks`;
            const response = await fetch(url);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            // 防止空响应导致JSON解析失败
            const text = await response.text();
            if (!text || text.trim() === '') {
                throw new Error('服务器返回空响应');
            }
            const result = JSON.parse(text);
            
            if (result.success && result.stocks) {
                return result.stocks.slice(0, limit).map(s => ({
                    code: s.code,
                    name: s.name,
                    current: s.current,
                    change: s.change,
                    source: '实时热门'
                }));
            }
            
            return this.getDefaultHotStocks();
        } catch (error) {
            console.error('获取热门股票失败:', error);
            return this.getDefaultHotStocks();
        }
    },

    // 股票池
    getStockPool() {
        return [
            { code: '600519', name: '贵州茅台', sector: '白酒' },
            { code: '000858', name: '五粮液', sector: '白酒' },
            { code: '601398', name: '工商银行', sector: '银行' },
            { code: '300750', name: '宁德时代', sector: '电池' },
            { code: '002594', name: '比亚迪', sector: '汽车' },
            { code: '601012', name: '隆基绿能', sector: '光伏' },
            { code: '600276', name: '恒瑞医药', sector: '医药' },
            { code: '300059', name: '东方财富', sector: '券商' },
            { code: '000333', name: '美的集团', sector: '家电' },
            { code: '300760', name: '迈瑞医疗', sector: '医疗' },
            { code: '601899', name: '紫金矿业', sector: '有色' }
        ];
    },

    // 随机打乱
    shuffleArray(array) {
        const shuffled = [...array];
        for (let i = shuffled.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
        }
        return shuffled;
    },

    // 默认热门股票
    getDefaultHotStocks() {
        const pool = this.getStockPool();
        return this.shuffleArray(pool).slice(0, 10).map(s => ({
            code: s.code,
            name: s.name,
            source: '本地股票池'
        }));
    }
};

// 浏览器环境导出
if (typeof window !== 'undefined') {
    window.MarketData = MarketData;
    window.ConnectionStatus = ConnectionStatus;
}
