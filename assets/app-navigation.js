(() => {
  "use strict";

  // The only navigation definition used by every work page.  Labels are
  // escaped so this asset remains valid even when opened by a legacy shell.
  const NAV_ITEMS = Object.freeze([
    { key: "dashboard", label: "\u64cd\u76d8\u53f0", icon: "\u25c9", path: "/app" },
    { key: "market-radar", label: "\u5e02\u573a\u96f7\u8fbe", icon: "\u25ce", path: "/market-radar" },
    { key: "research", label: "\u9009\u80a1\u7814\u7a76", icon: "\u2315", path: "/research" },
    { key: "simulation", label: "\u6a21\u62df\u6d4b\u8bd5", icon: "\u25b7", path: "/simulation" },
  ]);

  const normalizePath = (value) => {
    const path = String(value || "/").split("?")[0].split("#")[0].replace(/\/+$/, "");
    return path || "/";
  };

  function makeItem(item) {
    const active = normalizePath(location.pathname) === item.path;
    const link = document.createElement("a");
    link.className = `app-nav-item${active ? " is-active" : ""}`;
    link.href = item.path;
    link.dataset.navKey = item.key;
    link.title = item.label;
    if (active) link.setAttribute("aria-current", "page");

    const icon = document.createElement("span");
    icon.className = "app-nav-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = item.icon;
    const label = document.createElement("span");
    label.textContent = item.label;
    link.append(icon, label);
    return link;
  }

  function findHost() {
    const existing = document.querySelector("[data-app-navigation]");
    if (existing) return existing;

    const sidebar = document.querySelector(".side-nav, aside.side, .suite-side");
    if (!sidebar) return null;
    const host = document.createElement("nav");
    host.className = "app-navigation";
    host.dataset.appNavigation = "";
    host.setAttribute("aria-label", "\u4e3b\u5bfc\u822a");
    const brand = sidebar.querySelector(".side-brand, .brand, .suite-brand");
    if (brand?.nextSibling) sidebar.insertBefore(host, brand.nextSibling);
    else sidebar.appendChild(host);
    return host;
  }

  function removeLegacyDuplicates(sidebar) {
    if (!sidebar) return;
    sidebar.querySelectorAll("[data-market-radar], .nav").forEach((node) => {
      if (!node.closest("[data-app-navigation]")) node.remove();
    });
  }

  function render() {
    const host = findHost();
    if (!host) return false;
    const sidebar = host.closest(".side-nav, aside.side, .suite-side");
    removeLegacyDuplicates(sidebar);
    host.classList.add("app-navigation");
    host.setAttribute("aria-label", "\u4e3b\u5bfc\u822a");
    host.replaceChildren(...NAV_ITEMS.map(makeItem));
    document.body.classList.add("rq-has-app-navigation");
    return true;
  }

  function start() {
    if (render()) return;
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (render() || attempts >= 12) clearInterval(timer);
    }, 150);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();

  window.RabbitNavigation = Object.freeze({ items: NAV_ITEMS, render });
})();
