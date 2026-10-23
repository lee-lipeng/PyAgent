// Background 会在截图请求到达时按需注入 MAIN 世界的 html2canvas 和截图助手。
// 此隔离世界仅保留扩展加载信号，避免在所有页面上预加载截图依赖。
(() => {
  console.info('[PyAgent Bridge Content] 已注入', location.href);
  window.__pyagent_bridge_loaded = true;
  window.dispatchEvent(new Event('pyagent-bridge-ready'));
})();
