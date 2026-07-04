// strategy_engine.js - 多策略引擎（支持正T/反T/策略切换）
const StrategyEngine = {
    currentStrategy: 'meanReversion',
    
    strategyPerformance: {
        meanReversion: { wins: 0, losses: 0, profit: 0, lastUsed: null },
        grid: { wins: 0, losses: 0, profit: 0, lastUsed: null },
        breakout: { wins: 0, losses: 0, profit: 0, lastUsed: null },
        momentum: { wins: 0, losses: 0, profit: 0, lastUsed: null },
        positiveT: { wins: 0, losses: 0, profit: 0, lastUsed: null },
        reverseT: { wins: 0, losses: 0, profit: 0, lastUsed: null }
    },

    historicalData: {},

    loadHistoricalData(stockCode) {
        try {
            const key = `intraday_${stockCode}_${new Date().toISOString().split('T')[0]}`;
            const data = localStorage.getItem(key);
            if (data) return JSON.parse(data);
        } catch (e) {}
        return null;
    },

    saveIntradayData(stockCode, trends) {
        try {
            const key = `intraday_${stockCode}_${new Date().toISOString().split('T')[0]}`;
            localStorage.setItem(key, JSON.stringify({
                code: stockCode,
                date: new Date().toISOString().split('T')[0],
                trends: trends,
                savedAt: Date.now()
            }));
        } catch (e) {}
    },

    getTimeWindow(currentTime) {
        const timeStr = currentTime || this.getCurrentTime();
        const [hours, minutes] = timeStr.split(':').map(Number);
        const totalMinutes = hours * 60 + minutes;

        if (totalMinutes >= 570 && totalMinutes <= 590) return 'EXCLUDE';
        if (totalMinutes >= 591 && totalMinutes <= 690) return 'TRADING';
        if (totalMinutes >= 780 && totalMinutes <= 840) return 'TRADING';
        if (totalMinutes >= 841 && totalMinutes <= 900) return 'CLOSING';
        return 'CLOSED';
    },

    getCurrentTime() {
        const now = new Date();
        return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
    },

    calculateIndicators(data) {
        const trends = data.trends || [];
        if (trends.length < 5) return null;

        let cumulativeTPV = 0, cumulativeVolume = 0;
        const validTrends = trends.filter(t => t.price > 0 && t.volume > 0);
        validTrends.forEach(t => {
            cumulativeTPV += t.price * t.volume;
            cumulativeVolume += t.volume;
        });
        
        const vwap = cumulativeVolume > 0 ? cumulativeTPV / cumulativeVolume : trends[trends.length - 1].price;
        const currentPrice = trends[trends.length - 1].price;
        const deviation = ((currentPrice - vwap) / vwap) * 100;

        const avgVolume = validTrends.length > 0 ? 
            validTrends.reduce((sum, t) => sum + t.volume, 0) / validTrends.length : 1;
        const currentVolume = validTrends[validTrends.length - 1]?.volume || avgVolume;
        const volumeRatio = avgVolume > 0 ? currentVolume / avgVolume : 1;

        const recentPrices = validTrends.slice(-10).map(t => t.price);
        const priceMean = recentPrices.reduce((a, b) => a + b, 0) / recentPrices.length;
        const priceStd = Math.sqrt(recentPrices.reduce((sq, n) => sq + Math.pow(n - priceMean, 2), 0) / recentPrices.length);
        const volatility = priceMean > 0 ? priceStd / priceMean : 0;

        const trend = recentPrices.length >= 2 ? 
            (recentPrices[recentPrices.length - 1] - recentPrices[0]) / recentPrices[0] : 0;

        return { currentPrice, vwap, deviation, volumeRatio, volatility, trend, orderBookRatio: volumeRatio > 1 ? 1.2 : 1.0 };
    },

    // ========== 正T策略: 先买底仓，后高位卖出 ==========
    positiveTStrategy: {
        analyze(indicators, holdings, window, todaySignal) {
            const { deviation, volumeRatio, currentPrice } = indicators;
            
            if (window === 'EXCLUDE' || window === 'CLOSED') {
                return { signal: null, action: 'hold', reason: `时间窗口: ${window}` };
            }
            
            if (todaySignal && (todaySignal === 'buy' || todaySignal === 'sell')) {
                return { signal: null, action: 'hold', reason: '今日已完成做T' };
            }
            
            // 没仓位：找买点（低买）
            if (!holdings || holdings.quantity <= 0) {
                let condition = null, confidence = 50;
                
                if (deviation <= -0.5 && volumeRatio >= 1.0) {
                    condition = 'A'; confidence = 75;
                } else if (deviation <= -0.3 && volumeRatio >= 0.8) {
                    condition = 'B'; confidence = 65;
                }
                
                if (condition) {
                    return {
                        signal: {
                            type: 'BUY',
                            action: `正T买入${condition}`,
                            price: currentPrice,
                            confidence,
                            tradeType: 'positiveT',
                            reason: `正T买点: 跌幅${deviation.toFixed(2)}% 量比${volumeRatio.toFixed(2)}`
                        },
                        action: 'buy'
                    };
                }
            } else {
                // 有仓位：找卖点（高卖）
                const positionProfit = (currentPrice - holdings.avgCost) / holdings.avgCost * 100;
                
                if (positionProfit >= 0.3 || (positionProfit > 0.1 && deviation >= 0.3)) {
                    return {
                        signal: {
                            type: 'SELL',
                            action: '正T卖出',
                            price: currentPrice,
                            profit: positionProfit,
                            tradeType: 'positiveT',
                            reason: `正T卖点: 盈利${positionProfit.toFixed(2)}% 跌幅${deviation.toFixed(2)}%`
                        },
                        action: 'sell'
                    };
                }
                
                if (positionProfit <= -0.3) {
                    return {
                        signal: {
                            type: 'SELL',
                            action: '正T止损',
                            price: currentPrice,
                            profit: positionProfit,
                            tradeType: 'positiveT',
                            reason: `正T止损: 亏损${positionProfit.toFixed(2)}%`
                        },
                        action: 'sell'
                    };
                }
            }
            
            return { signal: null, action: 'hold', reason: '未满足正T条件' };
        }
    },

    // ========== 反T策略: 先卖空仓，后低位补回 ==========
    reverseTStrategy: {
        analyze(indicators, holdings, window, todaySignal, settings = {}) {
            const { deviation, volumeRatio, currentPrice } = indicators;
            const basePosition = settings.basePosition || 0;
            const tCost = settings.tCost || 0;
            
            if (window === 'EXCLUDE' || window === 'CLOSED') {
                return { signal: null, action: 'hold', reason: `时间窗口: ${window}` };
            }
            
            if (todaySignal && (todaySignal === 'buy' || todaySignal === 'sell')) {
                return { signal: null, action: 'hold', reason: '今日已完成做T' };
            }
            
            // 需要有底仓才能做反T
            if (!basePosition || basePosition <= 0) {
                return { signal: null, action: 'hold', reason: '无底仓，无法做反T' };
            }
            
            // 无反T仓位：找卖点（高位先卖）
            if (!holdings || holdings.quantity <= 0) {
                let condition = null, confidence = 50;
                
                // 正偏离较大时卖出
                if (deviation >= 0.5 && volumeRatio >= 1.0) {
                    condition = 'A'; confidence = 75;
                } else if (deviation >= 0.3 && volumeRatio >= 0.8) {
                    condition = 'B'; confidence = 65;
                }
                
                if (condition) {
                    return {
                        signal: {
                            type: 'SELL',
                            action: `反T卖出${condition}`,
                            price: currentPrice,
                            confidence,
                            tradeType: 'reverseT',
                            reason: `反T卖点: 正偏离${deviation.toFixed(2)}% 量比${volumeRatio.toFixed(2)}`
                        },
                        action: 'sell'
                    };
                }
            } else {
                // 有反T仓位（已经卖出）：找买点（低位补回）
                // 计算差价收益 = 卖出价 - 当前价
                const spreadProfit = (tCost - currentPrice) / tCost * 100;
                
                // 回落到买回
                if (spreadProfit >= 0.3 || (spreadProfit > 0.1 && deviation <= -0.3)) {
                    return {
                        signal: {
                            type: 'BUY',
                            action: '反T买回',
                            price: currentPrice,
                            profit: spreadProfit,
                            tradeType: 'reverseT',
                            reason: `反T买点: 差价盈利${spreadProfit.toFixed(2)}% 跌幅${deviation.toFixed(2)}%`
                        },
                        action: 'buy'
                    };
                }
                
                // 反T止损（价格继续涨，必须补回）
                if (spreadProfit <= -0.3) {
                    return {
                        signal: {
                            type: 'BUY',
                            action: '反T止损补回',
                            price: currentPrice,
                            profit: spreadProfit,
                            tradeType: 'reverseT',
                            reason: `反T止损: 价格上涨${Math.abs(spreadProfit).toFixed(2)}%，必须补回底仓`
                        },
                        action: 'buy'
                    };
                }
            }
            
            return { signal: null, action: 'hold', reason: '未满足反T条件' };
        }
    },

    // ========== 网格交易 ==========
    gridStrategy: {
        gridSize: 0.3,
        lastGridPrice: null,
        
        analyze(indicators, holdings, basePrice) {
            const { currentPrice } = indicators;
            if (!this.lastGridPrice) this.lastGridPrice = basePrice;
            
            const gridDistance = Math.abs(currentPrice - this.lastGridPrice) / this.lastGridPrice * 100;
            
            if (!holdings || holdings.quantity <= 0) {
                if (gridDistance >= this.gridSize && currentPrice < this.lastGridPrice) {
                    this.lastGridPrice = currentPrice;
                    return {
                        signal: { type: 'BUY', action: '网格买入', price: currentPrice, confidence: 70, winRate: 65 },
                        action: 'buy'
                    };
                }
            } else {
                if (gridDistance >= this.gridSize && currentPrice > this.lastGridPrice) {
                    const profit = (currentPrice - holdings.avgCost) / holdings.avgCost * 100;
                    this.lastGridPrice = currentPrice;
                    return {
                        signal: { type: 'SELL', action: '网格卖出', price: currentPrice, profit, confidence: 70, winRate: profit > 0 ? 100 : 0 },
                        action: 'sell'
                    };
                }
            }
            return { signal: null, action: 'hold' };
        }
    },

    // ========== 突破策略 ==========
    breakoutStrategy: {
        lookback: 15,
        analyze(indicators, holdings, data) {
            const trends = data.trends || [];
            if (trends.length < this.lookback) return { signal: null, action: 'hold' };
            
            const { currentPrice, volumeRatio } = indicators;
            const recentPrices = trends.slice(-this.lookback).map(t => t.price);
            const high = Math.max(...recentPrices);
            const low = Math.min(...recentPrices);
            
            if (!holdings || holdings.quantity <= 0) {
                if (currentPrice > high * 0.995 && volumeRatio > 1.2) {
                    return { signal: { type: 'BUY', action: '突破买入', price: currentPrice, confidence: 65, winRate: 55 }, action: 'buy' };
                }
            } else {
                const profit = (currentPrice - holdings.avgCost) / holdings.avgCost * 100;
                if (currentPrice < low * 1.005 || profit >= 0.5 || profit <= -0.3) {
                    return { signal: { type: 'SELL', action: profit > 0 ? '突破止盈' : '突破止损', price: currentPrice, profit, confidence: 75, winRate: profit > 0 ? 100 : 0 }, action: 'sell' };
                }
            }
            return { signal: null, action: 'hold' };
        }
    },

    // ========== 动量策略 ==========
    momentumStrategy: {
        analyze(indicators, holdings, data) {
            const { currentPrice, volumeRatio, trend } = indicators;
            
            if (!holdings || holdings.quantity <= 0) {
                if (trend > 0.002 && volumeRatio > 1.0) {
                    return { signal: { type: 'BUY', action: '动量追涨', price: currentPrice, confidence: 60, winRate: 55 }, action: 'buy' };
                }
            } else {
                const profit = (currentPrice - holdings.avgCost) / holdings.avgCost * 100;
                if (volumeRatio < 0.8 || profit >= 0.5 || profit <= -0.3) {
                    return { signal: { type: 'SELL', action: volumeRatio < 0.8 ? '动量衰竭卖出' : (profit > 0 ? '止盈' : '止损'), price: currentPrice, profit, confidence: 70, winRate: profit > 0 ? 100 : 0 }, action: 'sell' };
                }
            }
            return { signal: null, action: 'hold' };
        }
    },

    // ========== 乖离率回归策略 ==========
    meanReversionStrategy: {
        analyze(indicators, holdings, window, todaySignal) {
            const { deviation, volumeRatio, currentPrice } = indicators;

            if (todaySignal && (todaySignal === 'buy' || todaySignal === 'sell')) {
                return { signal: null, action: 'hold', reason: '今日已出信号' };
            }

            if (window === 'EXCLUDE' || window === 'CLOSED') {
                return { signal: null, action: 'hold', reason: `时间窗口: ${window}` };
            }

            if (!holdings || holdings.quantity <= 0) {
                let condition = null, confidence = 50, winRate = 50;

                if (deviation <= -0.8 && volumeRatio >= 1.3) {
                    condition = 'A'; winRate = 70; confidence = 75;
                } else if (deviation <= -0.5 && deviation > -0.8 && volumeRatio >= 1.0) {
                    condition = 'B'; winRate = 60; confidence = 65;
                } else if (deviation <= -0.3 && volumeRatio >= 0.8) {
                    condition = 'C'; winRate = 55; confidence = 60;
                }

                if (condition) {
                    return {
                        signal: { type: 'BUY', action: `乖离买入${condition}`, price: currentPrice, confidence, winRate, condition, targetProfit: condition === 'A' ? 0.8 : (condition === 'B' ? 0.5 : 0.3), stopLoss: -0.3 },
                        action: 'buy',
                        confidence, winRate,
                        reason: `条件${condition}: 乖离${deviation.toFixed(2)}% 量比${volumeRatio.toFixed(2)}`
                    };
                }
            } else {
                const positionProfit = (currentPrice - holdings.avgCost) / holdings.avgCost * 100;
                
                if (positionProfit <= -0.3) {
                    return { signal: { type: 'SELL', action: '止损卖出', price: currentPrice, profit: positionProfit }, action: 'sell', reason: `止损 ${positionProfit.toFixed(2)}%` };
                }
                if (positionProfit >= 0.5) {
                    return { signal: { type: 'SELL', action: '止盈卖出', price: currentPrice, profit: positionProfit }, action: 'sell', reason: `止盈 ${positionProfit.toFixed(2)}%` };
                }
                if (deviation >= 0.5 && positionProfit > 0.1) {
                    return { signal: { type: 'SELL', action: '乖离回归卖出', price: currentPrice, profit: positionProfit }, action: 'sell', reason: `乖离回归 +${deviation.toFixed(2)}%` };
                }
            }

            return { signal: null, action: 'hold', reason: '条件不足' };
        }
    },

    // ========== 主分析函数 ==========
    analyze(data, holdings, settings = {}) {
        const currentTime = settings.currentTime || this.getCurrentTime();
        const window = this.getTimeWindow(currentTime);
        const todaySignal = settings.todaySignal || null;
        const indicators = this.calculateIndicators(data);
        
        if (!indicators) {
            return { signal: null, action: 'hold', reason: '数据不足', timeWindow: window };
        }

        const strategy = settings.strategy || this.currentStrategy || 'meanReversion';
        
        switch (strategy) {
            case 'grid':
                return this.gridStrategy.analyze(indicators, holdings, data.preClose || indicators.currentPrice);
            case 'breakout':
                return this.breakoutStrategy.analyze(indicators, holdings, data);
            case 'momentum':
                return this.momentumStrategy.analyze(indicators, holdings, data);
            case 'positiveT':
                return this.positiveTStrategy.analyze(indicators, holdings, window, todaySignal);
            case 'reverseT':
                return this.reverseTStrategy.analyze(indicators, holdings, window, todaySignal, settings);
            case 'meanReversion':
            default:
                return this.meanReversionStrategy.analyze(indicators, holdings, window, todaySignal);
        }
    },

    // ========== 基于真实数据的日内回测 ==========
    async runIntradayBacktest(stockCode, intradayData, capital, settings = {}) {
        if (!intradayData || intradayData.length < 30) {
            return { stockCode, totalTrades: 0, winCount: 0, lossCount: 0, winRate: 0, totalProfit: '0.00', finalCapital: capital.toFixed(2), reason: '数据不足（需要至少30分钟数据）', trades: [] };
        }

        const trades = [];
        let position = null;
        let cash = capital;
        let winCount = 0;
        let lossCount = 0;
        let totalProfit = 0;
        
        const strategy = settings.strategy || 'meanReversion';
        const maxPosition = settings.maxPosition || capital * 0.3;
        const basePosition = settings.basePosition || 0;
        let todaySignal = null;
        
        for (let i = 20; i < intradayData.length; i++) {
            const current = intradayData[i];
            const slice = { trends: intradayData.slice(0, i + 1), preClose: intradayData[0].price };
            
            const holdings = position ? { quantity: position.quantity, avgCost: position.avgCost } : null;
            
            const result = this.analyze(slice, holdings, {
                ...settings,
                currentTime: current.time,
                todaySignal: todaySignal,
                strategy
            });
            
            if (result.signal) {
                if (result.signal.type === 'BUY' && !position) {
                    const buyAmount = Math.min(cash * 0.3, maxPosition);
                    const quantity = Math.max(100, Math.floor(buyAmount / current.price / 100) * 100);
                    
                    if (quantity >= 100) {
                        const cost = quantity * current.price;
                        position = { quantity, avgCost: current.price, buyTime: current.time, strategy };
                        cash -= cost;
                        todaySignal = 'buy';
                        trades.push({ day: 1, type: 'BUY', time: current.time, price: current.price, quantity, amount: cost, reason: result.signal.action, tradeType: result.signal.tradeType || 'normal' });
                    }
                } else if (result.signal.type === 'SELL' && position) {
                    const sellAmount = position.quantity * current.price;
                    const profit = sellAmount - position.quantity * position.avgCost;
                    
                    if (profit > 0) winCount++;
                    else lossCount++;
                    
                    totalProfit += profit;
                    cash += sellAmount;
                    todaySignal = 'sell';
                    trades.push({ day: 1, type: 'SELL', time: current.time, price: current.price, quantity: position.quantity, amount: sellAmount, profit, reason: result.signal.action, tradeType: result.signal.tradeType || 'normal', holdTime: this.calculateHoldTime(position.buyTime, current.time) });
                    position = null;
                }
            }
        }
        
        if (position) {
            const lastPrice = intradayData[intradayData.length - 1].price;
            const sellAmount = position.quantity * lastPrice;
            const profit = sellAmount - position.quantity * position.avgCost;
            
            if (profit > 0) winCount++;
            else lossCount++;
            
            totalProfit += profit;
            cash += sellAmount;
            trades.push({ day: 1, type: 'SELL', time: '15:00', price: lastPrice, quantity: position.quantity, amount: sellAmount, profit, reason: '强制平仓', tradeType: position.strategy, holdTime: this.calculateHoldTime(position.buyTime, '15:00'), forced: true });
        }
        
        const totalTrades = winCount + lossCount;
        
        return {
            stockCode,
            totalTrades,
            winCount,
            lossCount,
            winRate: totalTrades > 0 ? ((winCount / totalTrades) * 100).toFixed(1) : 0,
            totalProfit: totalProfit.toFixed(2),
            finalCapital: cash.toFixed(2),
            returnRate: ((totalProfit / capital) * 100).toFixed(2),
            strategy,
            trades: trades.slice(-10),
            dataPoints: intradayData.length,
            tradeDetails: trades
        };
    },

    calculateHoldTime(buyTime, sellTime) {
        const [buyH, buyM] = buyTime.split(':').map(Number);
        const [sellH, sellM] = sellTime.split(':').map(Number);
        return (sellH * 60 + sellM) - (buyH * 60 + buyM);
    },

    generateSimulatedIntraday(basePrice, volatility = 0.008) {
        const data = [];
        let price = basePrice;
        let trend = (Math.random() - 0.5) * 0.01;
        
        for (let h = 9; h <= 11; h++) {
            for (let m = (h === 9 ? 30 : 0); m < 60; m++) {
                const change = (Math.random() - 0.5) * volatility * 2 + (trend * 0.005);
                price = price * (1 + change);
                const isOpen = h === 9 && m < 50;
                const volume = isOpen ? Math.floor(Math.random() * 8000 + 3000) : Math.floor(Math.random() * 4000 + 1000);
                data.push({ time: `${h}:${m.toString().padStart(2, '0')}`, price, volume });
                if (Math.random() < 0.25) trend = (Math.random() - 0.5) * 0.02;
            }
        }
        
        for (let h = 13; h <= 15; h++) {
            for (let m = 0; m < (h === 15 ? 1 : 60); m++) {
                const change = (Math.random() - 0.5) * volatility * 2 + (trend * 0.005);
                price = price * (1 + change);
                const volume = Math.floor(Math.random() * 3500 + 800);
                data.push({ time: `${h}:${m.toString().padStart(2, '0')}`, price, volume });
                if (Math.random() < 0.25) trend = (Math.random() - 0.5) * 0.02;
            }
        }
        
        return data;
    },

    async runSimulation(stockCode, startDate, endDate, capital, settings = {}) {
        let intradayData = this.loadHistoricalData(stockCode);
        
        if (!intradayData || !intradayData.trends) {
            const basePrice = 50 + Math.random() * 50;
            intradayData = { trends: this.generateSimulatedIntraday(basePrice), preClose: basePrice };
        }
        
        return this.runIntradayBacktest(stockCode, intradayData.trends, capital, settings);
    }
};

if (typeof window !== 'undefined') {
    window.StrategyEngine = StrategyEngine;
}
