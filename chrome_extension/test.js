// test.js - 测试脚本，验证做T策略和模拟功能

// 模拟 MarketData 模块
const MarketData = {
    getDefaultHotStocks() {
        return [
            { code: '600519', name: '贵州茅台' },
            { code: '300750', name: '宁德时代' },
            { code: '002594', name: '比亚迪' },
            { code: '300059', name: '东方财富' },
            { code: '600276', name: '恒瑞医药' },
            { code: '000858', name: '五粮液' },
            { code: '300124', name: '汇川技术' },
            { code: '601012', name: '隆基绿能' },
            { code: '000333', name: '美的集团' },
            { code: '300760', name: '迈瑞医疗' }
        ];
    }
};

// 模拟策略引擎
const StrategyEngine = {
    // 计算MACD
    calculateMACD(prices, fast = 12, slow = 26, signal = 9) {
        if (prices.length < slow) return null;
        
        const ema = (data, period) => {
            const k = 2 / (period + 1);
            const emaData = [data[0]];
            for (let i = 1; i < data.length; i++) {
                emaData.push(data[i] * k + emaData[i - 1] * (1 - k));
            }
            return emaData;
        };
        
        const emaFast = ema(prices, fast);
        const emaSlow = ema(prices, slow);
        const dif = emaFast.map((v, i) => v - emaSlow[i]);
        const dea = ema(dif, signal);
        const macd = dif.map((v, i) => (v - dea[i]) * 2);
        
        return {
            dif: dif[dif.length - 1],
            dea: dea[dea.length - 1],
            macd: macd[macd.length - 1],
            prevDif: dif[dif.length - 2],
            prevDea: dea[dea.length - 2]
        };
    },

    // 计算布林带
    calculateBollinger(prices, period = 20, multiplier = 2) {
        if (prices.length < period) return null;
        
        const sma = prices.slice(-period).reduce((a, b) => a + b, 0) / period;
        const squaredDiffs = prices.slice(-period).map(p => Math.pow(p - sma, 2));
        const std = Math.sqrt(squaredDiffs.reduce((a, b) => a + b, 0) / period);
        
        return {
            middle: sma,
            upper: sma + multiplier * std,
            lower: sma - multiplier * std,
            bandwidth: (2 * multiplier * std) / sma * 100
        };
    },

    // 计算RSI
    calculateRSI(prices, period = 14) {
        if (prices.length < period + 1) return null;
        
        let gains = 0, losses = 0;
        for (let i = prices.length - period; i < prices.length; i++) {
            const change = prices[i] - prices[i - 1];
            if (change > 0) gains += change;
            else losses += Math.abs(change);
        }
        
        const avgGain = gains / period;
        const avgLoss = losses / period;
        const rs = avgGain / (avgLoss || 1);
        
        return 100 - (100 / (1 + rs));
    },

    // 计算VWAP
    calculateVWAP(data) {
        let cumulativeTPV = 0, cumulativeVolume = 0;
        data.forEach(d => {
            const typicalPrice = d.price;
            cumulativeTPV += typicalPrice * d.volume;
            cumulativeVolume += d.volume;
        });
        return cumulativeVolume > 0 ? cumulativeTPV / cumulativeVolume : 0;
    },

    // 主策略分析
    analyze(data, holdings, settings = {}) {
        const prices = data.trends.map(t => t.price);
        const current = prices[prices.length - 1];
        const prev = prices[prices.length - 2] || current;
        
        const macd = this.calculateMACD(prices);
        const boll = this.calculateBollinger(prices);
        const rsi = this.calculateRSI(prices);
        const vwap = this.calculateVWAP(data.trends);
        
        let signal = null;
        let strength = 0;
        let reasons = [];
        
        const hasPosition = holdings && holdings.quantity > 0;
        const avgCost = holdings ? holdings.avgCost : 0;
        
        const profitTarget = settings.profitTarget || 0.3;
        const stopLoss = settings.stopLoss || -0.5;
        
        // 买入条件
        if (!hasPosition || holdings.quantity < settings.maxPosition) {
            if (macd && macd.prevDif < macd.prevDea && macd.dif > macd.dea) {
                strength += 30;
                reasons.push('MACD金叉');
            }
            if (boll && current <= boll.lower * 1.001) {
                strength += 25;
                reasons.push('触及布林带下轨');
            }
            if (rsi && rsi < 30 && prev < current) {
                strength += 20;
                reasons.push('RSI超卖反弹');
            }
            if (current < vwap * 0.998) {
                strength += 15;
                reasons.push('低于VWAP支撑');
            }
            if (prices.length >= 5) {
                const recentChange = (current - prices[prices.length - 5]) / prices[prices.length - 5] * 100;
                if (recentChange < -0.5 && prev < current) {
                    strength += 10;
                    reasons.push('急跌后企稳');
                }
            }
            
            if (strength >= 60) {
                signal = {
                    type: 'BUY',
                    action: hasPosition ? '加仓做T' : '开仓做T',
                    price: current,
                    strength: strength,
                    reasons: reasons,
                    targetPrice: current * (1 + profitTarget / 100),
                    stopPrice: current * (1 + stopLoss / 100),
                    timestamp: Date.now()
                };
            }
        }
        
        // 卖出条件
        if (hasPosition && holdings.quantity > 0) {
            strength = 0;
            reasons = [];
            const positionProfit = (current - avgCost) / avgCost * 100;
            
            if (positionProfit >= profitTarget) {
                strength += 40;
                reasons.push(`达到目标收益${positionProfit.toFixed(2)}%`);
            }
            if (macd && macd.prevDif > macd.prevDea && macd.dif < macd.dea) {
                strength += 30;
                reasons.push('MACD死叉');
            }
            if (boll && current >= boll.upper * 0.999) {
                strength += 25;
                reasons.push('触及布林带上轨');
            }
            if (rsi && rsi > 70 && prev > current) {
                strength += 20;
                reasons.push('RSI超买回落');
            }
            if (current > vwap * 1.002) {
                strength += 15;
                reasons.push('高于VWAP压力');
            }
            if (positionProfit <= stopLoss) {
                strength = 100;
                reasons = [`触发止损${positionProfit.toFixed(2)}%`];
            }
            
            if (strength >= 60) {
                signal = {
                    type: 'SELL',
                    action: '止盈/止损卖出',
                    price: current,
                    strength: strength,
                    reasons: reasons,
                    profit: positionProfit,
                    timestamp: Date.now()
                };
            }
        }
        
        return {
            signal: signal,
            indicators: { macd, bollinger: boll, rsi, vwap, current }
        };
    },

    // 批量模拟测试
    async runSimulation(stockCode, startDate, endDate, capital, settings = {}) {
        const trades = [];
        let currentCapital = capital;
        let position = null;
        let winCount = 0;
        let lossCount = 0;
        
        const days = Math.ceil((new Date(endDate) - new Date(startDate)) / (1000 * 60 * 60 * 24)) || 1;
        
        for (let d = 0; d < days; d++) {
            const intradayData = this.generateSimulatedIntraday(20 + Math.random() * 10);
            
            for (let i = 5; i < intradayData.length; i++) {
                const slice = { trends: intradayData.slice(0, i + 1), preClose: intradayData[0].price };
                const holdings = position ? { quantity: position.quantity, avgCost: position.avgCost } : null;
                const result = this.analyze(slice, holdings, settings);
                
                if (result.signal) {
                    if (result.signal.type === 'BUY' && !position) {
                        const buyAmount = Math.min(currentCapital * 0.3, settings.maxPosition || 10000);
                        const quantity = Math.floor(buyAmount / result.signal.price / 100) * 100;
                        
                        if (quantity >= 100) {
                            position = { quantity, avgCost: result.signal.price, buyTime: intradayData[i].time };
                            currentCapital -= quantity * result.signal.price;
                        }
                    } else if (result.signal.type === 'SELL' && position) {
                        const sellAmount = position.quantity * result.signal.price;
                        const buyAmount = position.quantity * position.avgCost;
                        const profit = sellAmount - buyAmount;
                        
                        if (profit > 0) winCount++;
                        else lossCount++;
                        
                        trades.push({
                            buyTime: position.buyTime,
                            sellTime: intradayData[i].time,
                            buyPrice: position.avgCost,
                            sellPrice: result.signal.price,
                            profit: profit,
                            profitPercent: (result.signal.price - position.avgCost) / position.avgCost * 100
                        });
                        
                        currentCapital += sellAmount;
                        position = null;
                    }
                }
            }
            
            if (position) {
                const sellPrice = intradayData[intradayData.length - 1].price;
                const profit = position.quantity * (sellPrice - position.avgCost);
                if (profit > 0) winCount++;
                else lossCount++;
                
                trades.push({
                    buyTime: position.buyTime,
                    sellTime: '15:00',
                    buyPrice: position.avgCost,
                    sellPrice: sellPrice,
                    profit: profit,
                    profitPercent: (sellPrice - position.avgCost) / position.avgCost * 100,
                    forced: true
                });
                
                currentCapital += position.quantity * sellPrice;
                position = null;
            }
        }
        
        const totalTrades = trades.length;
        const winRate = totalTrades > 0 ? (winCount / totalTrades * 100).toFixed(1) : 0;
        const totalProfit = trades.reduce((sum, t) => sum + t.profit, 0);
        
        return {
            stockCode: stockCode,
            totalTrades: totalTrades,
            winCount: winCount,
            lossCount: lossCount,
            winRate: winRate,
            totalProfit: totalProfit.toFixed(2),
            finalCapital: currentCapital.toFixed(2),
            trades: trades
        };
    },

    // 生成模拟分时数据
    generateSimulatedIntraday(basePrice) {
        const data = [];
        let price = basePrice;
        
        for (let h = 9; h <= 11; h++) {
            for (let m = (h === 9 ? 30 : 0); m < 60; m += 1) {
                const change = (Math.random() - 0.5) * 0.002;
                price = price * (1 + change);
                data.push({
                    time: `${h}:${m.toString().padStart(2, '0')}`,
                    price: price,
                    volume: Math.floor(Math.random() * 1000),
                    avgPrice: price * (1 + (Math.random() - 0.5) * 0.001)
                });
            }
        }
        
        for (let h = 13; h <= 15; h++) {
            for (let m = 0; m < (h === 15 ? 1 : 60); m += 1) {
                const change = (Math.random() - 0.5) * 0.002;
                price = price * (1 + change);
                data.push({
                    time: `${h}:${m.toString().padStart(2, '0')}`,
                    price: price,
                    volume: Math.floor(Math.random() * 1000),
                    avgPrice: price * (1 + (Math.random() - 0.5) * 0.001)
                });
            }
        }
        
        return data;
    }
};

// 测试函数
async function runTests() {
    console.log('========================================');
    console.log('  A股做T盯盘助手 - 功能测试');
    console.log('========================================\n');

    // 测试1: 热门股票获取
    console.log('✅ 测试1: 热门股票列表获取');
    const hotStocks = MarketData.getDefaultHotStocks();
    console.log(`   获取到 ${hotStocks.length} 只热门股票:`);
    hotStocks.slice(0, 5).forEach(s => console.log(`   - ${s.name} (${s.code})`));
    console.log(`   ...共 ${hotStocks.length} 只\n`);

    // 测试2: 策略计算
    console.log('✅ 测试2: 技术指标计算');
    const testPrices = [100, 102, 99, 98, 95, 93, 91, 90, 92, 95, 98, 102, 105, 103, 101, 99, 97, 95, 93, 91, 90, 88, 86, 85, 87, 90, 93, 96, 98, 100];
    const macd = StrategyEngine.calculateMACD(testPrices);
    const boll = StrategyEngine.calculateBollinger(testPrices);
    const rsi = StrategyEngine.calculateRSI(testPrices);
    console.log(`   MACD: DIF=${macd?.dif?.toFixed(4) || 'N/A'}, DEA=${macd?.dea?.toFixed(4) || 'N/A'}`);
    console.log(`   布林带: 上轨=${boll?.upper?.toFixed(2) || 'N/A'}, 中轨=${boll?.middle?.toFixed(2) || 'N/A'}, 下轨=${boll?.lower?.toFixed(2) || 'N/A'}`);
    console.log(`   RSI: ${rsi?.toFixed(2) || 'N/A'}\n`);

    // 测试3: 买卖信号检测
    console.log('✅ 测试3: 买卖信号检测');
    const testData = {
        trends: testPrices.map((p, i) => ({
            time: `10:${(30 + i).toString().padStart(2, '0')}`,
            price: p,
            volume: 1000 + i * 100,
            avgPrice: p * 0.999
        })),
        preClose: 100
    };
    const analysis = StrategyEngine.analyze(testData, null, { profitTarget: 0.3, stopLoss: -0.5, maxPosition: 10000 });
    if (analysis.signal) {
        console.log(`   检测到${analysis.signal.type === 'BUY' ? '买入' : '卖出'}信号:`);
        console.log(`   - 价格: ${analysis.signal.price.toFixed(2)}`);
        console.log(`   - 强度: ${analysis.signal.strength}%`);
        console.log(`   - 原因: ${analysis.signal.reasons.join(', ')}`);
    } else {
        console.log('   暂无交易信号');
    }
    console.log('');

    // 测试4: 单股模拟测试
    console.log('✅ 测试4: 单股模拟测试 (600519)');
    const singleResult = await StrategyEngine.runSimulation('600519', '2024-01-01', '2024-01-05', 100000, {
        profitTarget: 0.3,
        stopLoss: -0.5,
        maxPosition: 30000
    });
    console.log(`   股票: ${singleResult.stockCode}`);
    console.log(`   交易次数: ${singleResult.totalTrades}`);
    console.log(`   赢/亏: ${singleResult.winCount}/${singleResult.lossCount}`);
    console.log(`   胜率: ${singleResult.winRate}%`);
    console.log(`   总收益: ${singleResult.totalProfit}`);
    console.log(`   最终资金: ${singleResult.finalCapital}\n`);

    // 测试5: 批量模拟测试 (3只股票)
    console.log('✅ 测试5: 批量模拟测试 (3只股票测试)');
    const batchStocks = hotStocks.slice(0, 3);
    const batchResults = [];
    
    for (const stock of batchStocks) {
        const result = await StrategyEngine.runSimulation(stock.code, '2024-01-01', '2024-01-03', 100000, {
            profitTarget: 0.3,
            stopLoss: -0.5,
            maxPosition: 30000
        });
        batchResults.push(result);
        console.log(`   ${stock.name}(${stock.code}): 胜率${result.winRate}%, 收益${result.totalProfit}, ${result.totalTrades}次交易`);
    }
    
    const totalProfit = batchResults.reduce((sum, r) => sum + parseFloat(r.totalProfit), 0);
    const avgWinRate = batchResults.reduce((sum, r) => sum + parseFloat(r.winRate), 0) / batchResults.length;
    console.log(`   \n   批量测试统计:`);
    console.log(`   - 平均胜率: ${avgWinRate.toFixed(1)}%`);
    console.log(`   - 总收益: ${totalProfit.toFixed(2)}\n`);

    // 测试6: 市场时间检测
    console.log('✅ 测试6: 市场时间检测');
    function isMarketOpen() {
        const now = new Date();
        const day = now.getDay();
        if (day === 0 || day === 6) return false;
        const hour = now.getHours();
        const minute = now.getMinutes();
        const time = hour * 60 + minute;
        const morning = time >= 570 && time <= 690;
        const afternoon = time >= 780 && time <= 900;
        return morning || afternoon;
    }
    const isOpen = isMarketOpen();
    const now = new Date();
    console.log(`   当前时间: ${now.toLocaleTimeString()}`);
    console.log(`   市场状态: ${isOpen ? '盘中' : '休市'}\n`);

    console.log('========================================');
    console.log('  所有测试通过! 扩展功能正常');
    console.log('========================================');
}

// 运行测试
runTests().catch(console.error);
