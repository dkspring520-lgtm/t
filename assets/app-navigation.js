(function(){
  const items=[
    {label:'操盘台',icon:'◉',href:'/app'},
    {label:'市场雷达',icon:'◌',href:'/market-radar'},
    {label:'选股研究',icon:'⌁',href:'/research'},
    {label:'模拟测试',icon:'◈',href:'/simulation'}
  ];
  function render(){document.querySelectorAll('[data-app-navigation]').forEach(nav=>{nav.innerHTML=items.map(item=>{const active=location.pathname===item.href;return `<a class="app-nav-item${active?' is-active':''}" href="${item.href}"${active?' aria-current="page"':''}><span class="app-nav-icon">${item.icon}</span><span>${item.label}</span></a>`}).join('')})}
  document.readyState==='loading'?document.addEventListener('DOMContentLoaded',render):render();
})();
