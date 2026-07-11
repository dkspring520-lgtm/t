(function(){
  'use strict';
  const byId=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const today=()=>new Date().toISOString().slice(0,10);
  const historyKey=()=>`rabbit-alert-history:${today()}`;
  const classify=row=>{
    const smart=row?.smartT;
    if(smart?.state==='DATA_RISK')return 'risk';
    if(smart&&smart.confirmed)return 'confirm';
    if(smart&&smart.state&&!smart.confirmed)return row?.rawStrictSignal?'watch':'none';
    const signal=String(row?.signal||'');
    if(row?.quoteStale||/异常|延迟|跌破止损|数据中断|接口异常/.test(signal))return 'risk';
    if(row?.strictSignal||row?.opening?.actionable||row?.intradayReminder?.actionable||/确认|低吸|高抛|买入|卖出/.test(signal))return 'confirm';
    return /观察|超买|超卖|放量|接近/.test(signal)?'watch':'none';
  };
  const direction=row=>{const text=String(row?.signal||'');if(/低吸|买入|回补|超卖|局部低点/.test(text))return 'buy';if(/高抛|卖出|超买|局部顶部/.test(text))return 'sell';return 'risk'};
  const readHistory=()=>{try{return JSON.parse(localStorage.getItem(historyKey())||'[]')}catch(_){return []}};
  function recordAlert(row,level){
    if(level==='none')return;
    const side=direction(row),now=Date.now(),key=`${row.code||''}:${side}:${level}`;
    const list=readHistory();
    if(list.some(item=>item.key===key&&now-Number(item.ts||0)<5*60000))return;
    list.unshift({key,ts:now,time:String(row.time||new Date().toTimeString().slice(0,5)).slice(0,5),code:row.code||'',name:row.name||row.code||'股票',level,side,signal:row.signal||'观察',price:Number(row.price||0)});
    localStorage.setItem(historyKey(),JSON.stringify(list.slice(0,80)));
  }
  function renderHistory(){
    const list=readHistory(),side=document.querySelector('.rqf-side');if(!side)return;side.style.setProperty('overflow','visible','important');
    let card=byId('rabbitAlertHistory');if(!card){card=document.createElement('details');card.id='rabbitAlertHistory';card.className='rqf-side-card rabbit-alert-history';card.innerHTML='<summary title="展开当天提醒记录"><span>今日提醒</span><span class="rabbit-alert-count">0</span></summary><div class="rabbit-alert-list"></div>';card.open=sessionStorage.getItem('rabbit-alert-drawer-open')==='1';card.ontoggle=()=>sessionStorage.setItem('rabbit-alert-drawer-open',card.open?'1':'0');side.appendChild(card)}
    card.querySelector('.rabbit-alert-count').textContent=String(list.length);card.querySelector('.rabbit-alert-list').innerHTML=list.length?list.slice(0,12).map(item=>`<div class="rabbit-alert-item" data-code="${esc(item.code)}"><em>${esc(item.time)}</em><b>${esc(item.name)} · ${esc(item.signal)}</b><span>${item.price?item.price.toFixed(2):'--'}</span></div>`).join(''):'<div class="rabbit-alert-empty">今天还没有提醒</div>';
    card.querySelectorAll('.rabbit-alert-item').forEach(item=>item.onclick=()=>{if(typeof window.selectPremarket==='function')window.selectPremarket(item.dataset.code)});
  }
  function prefs(){return typeof signalPrefs!=='undefined'?signalPrefs:{signalCooldown:5,alertMode:'important',maxSignalsPerDay:2}}
  window.claimDailySignalAlert=function(row){
    const level=classify(row);recordAlert(row,level);renderHistory();
    if(level!=='confirm'||prefs().alertMode==='silent')return false;
    const side=direction(row),key=`rabbit-alert-cooldown:${row.code}:${side}`,now=Date.now(),last=Number(localStorage.getItem(key)||0),minutes=Math.max(5,Number(prefs().signalCooldown||5));
    if(now-last<minutes*60000)return false;
    const daily=`rabbit-alert-daily:${today()}:${row.code}:${side}`,count=Number(localStorage.getItem(daily)||0);
    if(count>=Math.max(1,Number(prefs().maxSignalsPerDay||2)))return false;
    localStorage.setItem(key,String(now));localStorage.setItem(daily,String(count+1));return true;
  };
  window.showSignalToast=function(row){
    const box=byId('signalToasts');if(!box)return;const side=direction(row),score=Number(row.intradayReminder?.score||row.opening?.score||(row.strictSignal?86:78));const reason=row.reason||row.intradayReminder?.reason||row.opening?.reason||'量价与位置形成确认';
    const el=document.createElement('article');el.className=`signal-toast rabbit-alert confirm-${side}`;el.innerHTML=`<div class="ra-head"><span class="ra-icon">🐰</span><div class="ra-copy"><b>${esc(row.signal||(side==='buy'?'发现低位机会':'冲高回落确认'))}</b><span>${esc(row.name)} ${esc(row.code)}</span></div><button class="ra-close" title="忽略">×</button></div><div class="ra-grid"><div><span>当前价</span><b>${Number(row.price||0).toFixed(2)}</b></div><div><span>信号评分</span><b>${Math.round(score)} 分</b></div><div><span>有效时间</span><b>3 分钟</b></div></div><p class="ra-reason">原因：${esc(reason)}</p><div class="ra-actions"><button class="ra-ignore">忽略</button><button class="primary ra-view">查看详情</button></div>`;
    const close=()=>el.remove();el.querySelector('.ra-close').onclick=close;el.querySelector('.ra-ignore').onclick=close;el.querySelector('.ra-view').onclick=()=>{close();if(typeof window.selectPremarket==='function')window.selectPremarket(String(row.code||''))};box.prepend(el);[...box.children].slice(2).forEach(node=>node.remove());setTimeout(close,180000);
  };
  const oldBeep=window.maybeBeep;
  window.maybeBeep=function(row){if(prefs().alertMode==='silent'||classify(row)!=='confirm')return;if(typeof oldBeep==='function')oldBeep(row)};
  function showRisk(row){
    const level=classify(row);if(level!=='risk')return;recordAlert(row,level);const key=`rabbit-risk-closed:${today()}:${row.code}:${row.signal}`;if(sessionStorage.getItem(key))return;
    let banner=byId('rabbitRiskBanner');if(!banner){banner=document.createElement('div');banner.id='rabbitRiskBanner';banner.className='rabbit-risk-banner';document.body.appendChild(banner)}
    banner.innerHTML=`<span>⚠️</span><b>${esc(row.name)}：${esc(row.signal||'数据异常')}｜${esc(row.reason||'请检查行情连接')}</b><button>我知道了</button>`;banner.querySelector('button').onclick=()=>{sessionStorage.setItem(key,'1');banner.remove()};
  }
  let marketRadarCache=null,marketRadarAt=0,marketRadarPending=false;
  async function refreshMarketCapsule(market){
    if(!market)return;
    const now=Date.now();
    if(marketRadarCache&&now-marketRadarAt<90000){applyMarketRadarCapsule(market,marketRadarCache);return}
    if(marketRadarPending)return;
    marketRadarPending=true;
    try{const data=await(await fetch('/api/market_radar',{cache:'no-store'})).json();if(data?.ok){marketRadarCache=data;marketRadarAt=Date.now();applyMarketRadarCapsule(market,data)}}catch(_){/* keep visible watchlist fallback */}finally{marketRadarPending=false}
  }
  function applyMarketRadarCapsule(market,data){const score=Number(data.score||0),status=String(data.status||'市场观察'),tone=score>=80?'warning':score>=60?'strong':score<40?'weak':'neutral';market.className=`rabbit-market-capsule ${tone}`;market.innerHTML=`<span class="dot"></span>🐰 市场${score} · ${esc(status.replace('牛市',''))}`;market.title=`${data.dataSource||'市场快照'}｜样本 ${Number(data.sampleSize||0)} 只｜${data.coverageMessage||''}`}
  function updateCapsules(rows){
    const actions=document.querySelector('.top-actions');if(!actions)return;let market=byId('rabbitMarketCapsule'),state=byId('rabbitStateCapsule');
    if(!market){market=document.createElement('button');market.id='rabbitMarketCapsule';market.className='rabbit-market-capsule';market.onclick=()=>location.href='/market-radar';actions.prepend(market)}
    if(!state){state=document.createElement('span');state.id='rabbitStateCapsule';state.className='rabbit-state-capsule';actions.insertBefore(state,market.nextSibling)}
    const valid=rows.filter(row=>Number(row.price)>0),avg=valid.length?valid.reduce((sum,row)=>sum+Number(row.change||0),0)/valid.length:0,pos=valid.length?valid.filter(row=>Number(row.change)>0).length/valid.length:0,active=valid.length?valid.filter(row=>Math.abs(Number(row.change||0))>=.8).length/valid.length:0,confirmed=valid.length?valid.filter(row=>row.strictSignal||/确认|低吸|高抛|买入|卖出/.test(String(row.signal||''))).length/valid.length:0;const trend=Math.max(0,Math.min(40,Math.round(20+avg*6+(pos-.5)*12))),funds=Math.max(0,Math.min(30,Math.round(9+active*13+confirmed*8))),breadth=Math.max(0,Math.min(30,Math.round(pos*30))),score=trend+funds+breadth;const status=score>=80?'牛市过热':score>=60?'牛市偏强':score>=40?'震荡观察':'市场偏弱';const tone=score>=80?'warning':score>=60?'strong':score<40?'weak':'neutral';market.className=`rabbit-market-capsule ${tone}`;market.innerHTML=`<span class="dot"></span>🐰 牛市${score} · ${status.replace('牛市','')}`;
    const focus=rows.find(row=>String(row.code)===String(window.premarketTargetCode||''))||rows[0]||{};const level=classify(focus),label=level==='risk'?'数据异常':level==='confirm'?(direction(focus)==='buy'?'发现机会':'等待兑现'):level==='watch'?'等待确认':'平静观察';state.className=`rabbit-state-capsule ${level==='confirm'||level==='risk'?'has-alert':''}`;state.textContent=`🐰 ${label}`;refreshMarketCapsule(market);
  }
  function updateSmartCapsule(rows){
    const state=byId('rabbitStateCapsule');if(!state)return;
    const focus=(rows||[]).find(row=>String(row.code)===String(window.premarketTargetCode||''))||(rows||[])[0];
    const smart=focus?.smartT;if(!smart)return;
    const labels={READY:'准备执行',OPENING_OBSERVE:'开盘观察',WAIT_CONFIRMATION:'等待确认',SCORE_BLOCKED:'评分不足',REGIME_OBSERVE:'等待5分钟K',TREND_BLOCKED:'趋势过滤',EDGE_BLOCKED:'价差不足',ENTRY_CUTOFF:'停止新循环',FORCE_CLOSE:'收盘恢复',DATA_RISK:'数据保护',MARKET_CLOSED:'休市观察'};
    const profile=smart.profile?.label||'平衡';
    const label=labels[smart.state]||'智能观察';
    state.className=`rabbit-state-capsule ${smart.confirmed||smart.state==='DATA_RISK'?'has-alert':''}`;
    state.textContent=`🐰 ${profile}档｜${label}`;
    state.title=smart.reason||'';
  }
  const smartStateLabels={READY:'准备执行',EXECUTED:'已模拟成交',OPENING_OBSERVE:'开盘观察',AUCTION_WAIT_CONFIRMATION:'等待09:35确认',AUCTION_DIRECTION_BLOCKED:'竞价方向拦截',WAIT_CONFIRMATION:'等待确认',SCORE_BLOCKED:'评分不足',REGIME_OBSERVE:'等待5分钟K',TREND_BLOCKED:'趋势过滤',EDGE_BLOCKED:'价差不足',ENTRY_CUTOFF:'停止新循环',FORCE_CLOSE:'收盘恢复',FORCE_CLOSE_READY:'准备恢复仓位',DATA_RISK:'数据保护',MARKET_CLOSED:'休市观察',COOLDOWN:'信号冷却',DAILY_LIMIT:'达到日限额',LOSS_LOCKED:'亏损保护',SIDE_LOCKED:'等待反向完成',CAPACITY_BLOCKED:'资金/持仓不足',DUPLICATE:'已处理',NO_ACTION:'方向不明确'};
  const regimeLabels={UPTREND:'上涨趋势',DOWNTREND:'下跌趋势',RANGE:'震荡区间',OBSERVE:'观察中'};
  function renderSmartTPanel(rows){
    const focus=(rows||[]).find(row=>String(row.code)===String(window.premarketTargetCode||''))||(rows||[])[0];
    const main=document.querySelector('.rqf-main');if(!main||!focus?.smartT)return;
    const smart=focus.smartT,paper=focus.paperT||{},profile=smart.profile||{},auction=focus.auctionRadar||focus.opening?.auctionGate||{},audit=(paper.decisionAudit||[]).slice(-3).reverse();
    let panel=byId('rabbitSmartTPanel');if(!panel){panel=document.createElement('section');panel.id='rabbitSmartTPanel';panel.className='rabbit-smart-t-panel';const chart=main.querySelector('.rqf-chart');chart?main.insertBefore(panel,chart):main.appendChild(panel)}
    const net=Number(paper.dailyRealizedT||0),available=Number(smart.availableSpreadPct||0),required=Number(smart.requiredGrossSpreadPct||0);
    const direction=auction.preferredDirection==='BUY_FIRST'?'正T · 先买后卖':auction.preferredDirection==='SELL_FIRST'?'反T · 先卖后买':'盘中趋势决定';
    const auctionState={PENDING_CONFIRMATION:'等待09:35',CONFIRMED:'已确认',INVALIDATED:'已失效',WAIT_DATA:'数据不足',NEUTRAL:'中性'}[auction.state]||auction.state||'等待数据';
    const conditions=Array.isArray(auction.conditions)&&auction.conditions.length?auction.conditions.join('、'):'尚无确认项';
    panel.innerHTML=`<div class="rabbit-smart-head"><div><b>智能做T审计</b><span>${esc(profile.label||'平衡')}档 · ${esc(smartStateLabels[smart.state]||smart.state||'观察')}</span></div><em class="${smart.confirmed?'ready':''}">${smart.confirmed?'条件确认':'暂不执行'}</em></div><div class="rabbit-auction-audit ${auction.state==='CONFIRMED'?'confirmed':auction.state==='INVALIDATED'?'invalid':''}"><span>本股开盘预案</span><b>${esc(auction.label||'等待个股竞价数据')} · ${esc(direction)}</b><em>${esc(auctionState)}｜确认 ${Number(auction.confirmationCount||0)} 项｜${esc(conditions)}</em><p>${esc(auction.reason||'按该股自身昨收、开盘、VWAP与第一波结构独立判断。')}</p></div><div class="rabbit-smart-metrics"><span>趋势<b>${esc(regimeLabels[smart.regime]||smart.regime||'--')}</b></span><span>预计价差<b>${available.toFixed(2)}% / ${required.toFixed(2)}%</b></span><span>今日循环<b>${Number(paper.dailyCycleCount||0)} / ${Number(profile.max_daily_cycles||0)}</b></span><span>做T净额<b class="${net>=0?'gain':'loss'}">${net>=0?'+':''}${net.toFixed(2)}元</b></span></div><p>${esc(smart.reason||'等待策略判断。')}</p>${audit.length?`<div class="rabbit-smart-audit">${audit.map(item=>`<span><time>${esc(item.time||'--:--')}</time><b>${esc(smartStateLabels[item.state]||item.state||'观察')}</b><em>${esc(item.reason||'')}</em></span>`).join('')}</div>`:''}`;
  }
  function enhanceTags(rows){const map=Object.fromEntries(rows.map(row=>[String(row.code),row]));document.querySelectorAll('.monitor-tag').forEach(tag=>{const code=tag.querySelector('em')?.textContent||'',row=map[code],level=classify(row),side=direction(row),name=String(row?.name||'').trim();if(name&&name!==code&&tag.querySelector('strong'))tag.querySelector('strong').textContent=name;tag.dataset.signal=level==='confirm'?side:(level==='watch'?'watch':'none');tag.title=`${name||tag.title||code}${row?.signal?' · '+row.signal:''}`})}
  function enhanceSmartTags(rows){const map=Object.fromEntries((rows||[]).map(row=>[String(row.code),row]));document.querySelectorAll('.monitor-tag').forEach(tag=>{const code=tag.querySelector('em')?.textContent||'',row=map[code],smart=row?.smartT;if(!smart)return;let chip=tag.querySelector('.rabbit-smart-tag');if(!chip){chip=document.createElement('i');chip.className='rabbit-smart-tag';tag.insertBefore(chip,tag.querySelector('.tag-remove'))}chip.textContent=smart.confirmed?'可执行':(smartStateLabels[smart.state]||'观察');chip.classList.toggle('ready',!!smart.confirmed);chip.title=smart.reason||''})}
  function addRadarNav(){const menu=document.querySelector('.side-menu');if(!menu||menu.querySelector('[data-market-radar],button[onclick*="/market-radar"]'))return;const button=document.createElement('button');button.dataset.marketRadar='1';button.innerHTML='<small>◉</small>市场雷达';button.onclick=()=>location.href='/market-radar';menu.insertBefore(button,menu.children[1]||null)}
  const baseRender=window.renderRealtime;
  if(typeof baseRender==='function')window.renderRealtime=function(rows){baseRender(rows);(rows||[]).forEach(row=>{const level=classify(row);if(level==='watch')recordAlert(row,level);if(level==='risk')showRisk(row)});updateCapsules(rows||[]);updateSmartCapsule(rows||[]);renderSmartTPanel(rows||[]);enhanceTags(rows||[]);enhanceSmartTags(rows||[]);renderHistory()};
  document.addEventListener('DOMContentLoaded',()=>{addRadarNav();renderHistory()});
})();
