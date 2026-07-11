(function(global){
  "use strict";
  const state={root:null,options:{},payload:null};
  function mount(target,options={}){
    const root=typeof target==="string"?document.querySelector(target):target;
    if(!root)throw new Error("RabbitQuantComplete: 找不到挂载容器");
    state.root=root;state.options={avatarUrl:"assets/rabbit-avatar.png",...options};
    root.innerHTML='<div class="rqc-stack"><div data-rqc="auction"></div><div data-rqc="intelligence"></div><div data-rqc="growth"></div></div>';
    const s=document.createElement("style");s.textContent='.rqc-stack{display:grid;gap:14px;max-width:520px}.rqc-stack>div:empty{display:none}';root.prepend(s);
    global.RabbitAuctionRadar?.mount(root.querySelector('[data-rqc="auction"]'),state.options);
    global.RabbitQuantIntelligence?.mount(root.querySelector('[data-rqc="intelligence"]'),state.options);
    if(global.RabbitStrategyGrowth)global.RabbitStrategyGrowth.mount(root.querySelector('[data-rqc="growth"]'),state.options.growth||{});
    return api;
  }
  function update(payload){state.payload=payload||{};global.RabbitAuctionRadar?.update(payload.auction_radar||{});global.RabbitQuantIntelligence?.update(payload);if(payload.learning&&global.RabbitStrategyGrowth)global.RabbitStrategyGrowth.update('[data-rqc="growth"]',payload.learning);return api}
  const api={mount,update};global.RabbitQuantComplete=api;
})(window);