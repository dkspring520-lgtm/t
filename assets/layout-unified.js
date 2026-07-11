(() => {
  "use strict";

  function classifyViewport() {
    const width = window.innerWidth;
    document.body.classList.toggle("rq-wide", width >= 1600);
    document.body.classList.toggle("rq-standard", width >= 1180 && width < 1600);
    document.body.classList.toggle("rq-compact", width >= 900 && width < 1180);
    document.body.classList.toggle("rq-mobile", width < 900);
  }

  function bindPremarketCollapse() {
    const panel = document.querySelector(".premarket");
    if (!panel || panel.dataset.rqLayoutBound === "1") return;
    panel.dataset.rqLayoutBound = "1";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "rq-layout-toggle";
    const apply = (collapsed) => {
      panel.classList.toggle("rq-layout-collapsed", collapsed);
      toggle.textContent = collapsed ? "\u5c55\u5f00\u76d8\u524d" : "\u6536\u8d77\u76d8\u524d";
      toggle.setAttribute("aria-expanded", String(!collapsed));
      try { sessionStorage.setItem("rq-premarket-collapsed", collapsed ? "1" : "0"); } catch (_) {}
    };
    let saved = null;
    try { saved = sessionStorage.getItem("rq-premarket-collapsed"); } catch (_) {}
    panel.append(toggle);
    apply(saved === "1");
    toggle.addEventListener("click", () => apply(!panel.classList.contains("rq-layout-collapsed")));
  }

  function start() {
    classifyViewport();
    bindPremarketCollapse();
    addEventListener("resize", classifyViewport, { passive: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
