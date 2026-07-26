// Popup 脚本 — 向 background 查询 WS 真实状态。
// 通过 chrome.runtime.sendMessage 问 background。

(async function() {
  const statusEl = document.getElementById('status');

  function setStatus(connected, text) {
    statusEl.className = 'status ' + (connected ? 'connected' : 'disconnected');
    statusEl.textContent = text;
  }

  let responded = false;
  const timeout = setTimeout(() => {
    if (responded) return;
    responded = true;
    setStatus(false, 'background 未响应 (可能 service worker 未启动)');
  }, 1500);

  try {
    chrome.runtime.sendMessage({ type: 'popup_status' }, (resp) => {
      if (responded) return;
      responded = true;
      clearTimeout(timeout);
      if (chrome.runtime.lastError) {
        setStatus(false, 'background 通信失败');
        return;
      }
      if (resp && resp.wsState === 'OPEN') {
        setStatus(true, '已连接到 PyAgent');
      } else {
        setStatus(false, 'background WS 未连接 (' + (resp ? resp.wsState : 'unknown') + ')');
      }
    });
  } catch (err) {
    clearTimeout(timeout);
    setStatus(false, '查询失败: ' + err.message);
  }
})();