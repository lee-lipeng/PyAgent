// PyAgent Browser Bridge — Background Service Worker
// 职责:
//   1. 维护到本地 PyAgent 的 WS 连接 (ws://127.0.0.1:18787)
//   2. 接收 Python -> Browser 的执行请求,通过 chrome.tabs.executeScript 注入到目标 tab
//   3. 上报 tab 元信息 (url/title) 和连接状态

const WS_URL = 'ws://127.0.0.1:18787';
const RECONNECT_DELAY_MS = 3000;
const HEARTBEAT_INTERVAL_MS = 30000;

// sessionId 是浏览器侧的 tab id 字符串 — Python 端用此路由到具体 tab
function tabToSessionId(tab) {
  return String(tab.id);
}

let ws = null;
let reconnectTimer = null;
let heartbeatTimer = null;

// 通过 chrome.scripting.executeScript 拿到的注入结果
// 在 MV3 下,Promise 化的注入 API 返回 [{frameId, result}]
// 这样 LLM 调用 navigate 时即使服务端没有 tab 缓存,也能在当前页面运行 JS。
async function runCodeOnTab(tabId, code) {
  let numericId = Number(tabId);
  if (!Number.isFinite(numericId) || numericId <= 0) {
    // 找当前活跃的 http/https tab
    const tabs = await chrome.tabs.query({ active: true });
    const candidate = tabs.find((t) => t.id !== undefined && t.url && /^https?:/i.test(t.url));
    if (!candidate) {
      throw new Error(
        `tabId=${JSON.stringify(tabId)} 解析失败,且当前浏览器没有可用的 http/https tab。` +
          `请先在 Chrome 中打开一个 http/https 页面(比如 https://www.baidu.com)再调用本工具。`,
      );
    }
    numericId = candidate.id;
  }
  const injectionResults = await chrome.scripting.executeScript({
    target: { tabId: numericId, allFrames: false },
    func: (codeString) => {
      // 把字符串当作表达式 eval;用户代码应使用 IIFE 形式
      // eslint-disable-next-line no-eval
      return eval(codeString);
    },
    args: [code],
    world: 'MAIN', // 在主世界执行,能访问页面 JS 变量
  });
  // 注入结果是 [{frameId, documentId, result}]
  const first = injectionResults[0];
  if (!first) {
    throw new Error(`tab ${numericId} 没有返回结果`);
  }
  return first.result;
}

// 从服务端 navigate 注入代码里提取 URL — 简单字符串匹配,代码模板固定。
// 服务端 navigate.py 生成的 code 含 "window.location.href = \"<url>\"" 或 '"<url>"' 字面量。
function extractNavigateUrl(code) {
  if (!code || typeof code !== 'string') return null;
  // 只匹配 navigate 工具的 JS 模板特征 — 防止误抓普通 JS 字符串
  if (!code.includes('window.location.href')) return null;
  const m = code.match(/window\.location\.href\s*=\s*(["'])([^"']+)\1/);
  if (m && m[2]) return m[2];
  return null;
}

// navigate 处理:支持 new tab (默认) 与 reuse tab (复用当前 active http/https tab)。
//
// 默认 new tab 决策:
// 1. 复用旧 tab 时 chrome.tabs.update 触发整页 reload,旧页面的 JS context 被销毁,
//    executeScript 注入会报 "Frame with ID 0 was removed"。
// 2. 新 tab 让 navigate 与旧页面彻底隔离,LLM 也能清楚知道新页面在哪。
// 3. Chrome 现在每代用户都允许无限 tab,不是稀缺资源。
//
// reuse_tab 路径:
// - LLM 显式 reuse_tab=true 时,在当前 active http/https tab 上原地跳转
// - 适合 SPA 内连续翻页(如已登录态)
async function handleNavigate(execId, url, code, reuseTab) {
  if (reuseTab) {
    // 找当前 active http/https tab;不存在则降级到 new tab
    const tabs = await chrome.tabs.query({ active: true });
    const candidate = tabs.find(
      (t) => t.id !== undefined && t.url && /^https?:/i.test(t.url),
    );
    if (!candidate) {
      sendProgress(
        execId,
        'reuse_tab_fallback',
        'reuse_tab=true 但当前没有 active http/https tab,降级为新开 tab',
      );
      return await handleNavigate(execId, url, code, false);
    }
    const tabId = candidate.id;
    sendProgress(execId, 'tab_reused', `复用 tab #${tabId} (url=${url})`);
    // 整页 reload — 等加载完后再注入
    await chrome.tabs.update(tabId, { url });
    // wait load/DOMContentLoaded
    await waitForTabComplete(tabId, 15_000);
    await runCodeOnTab(String(tabId), code);
    sendProgress(execId, 'page_ready', `复用 tab 已就绪,提取 title/url`);
    const after = await chrome.tabs.get(tabId);
    sendProgress(execId, 'navigated', `导航完成 → ${after.title || '(无标题)'}`);
    return { url: after.url, title: after.title, target: String(after.id), created: false, reused: true };
  }
  // 默认 new tab
  const created = await chrome.tabs.create({ url, active: false });
  sendProgress(execId, 'tab_created', `已创建新 tab #${created.id} (url=${url})`);
  await runCodeOnTab(created.id, code);
  sendProgress(execId, 'page_ready', `页面脚本已返回,提取 title/url`);
  const after = await chrome.tabs.get(created.id);
  sendProgress(execId, 'navigated', `导航完成 → ${after.title || '(无标题)'}`);
  return { url: after.url, title: after.title, target: String(after.id), created: true, reused: false };
}

// 轮询等 tab status === 'complete' 或超时
async function waitForTabComplete(tabId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === 'complete') return true;
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

function sendProgress(execId, phase, text) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try {
    ws.send(JSON.stringify({ type: 'progress', id: execId, phase, text }));
  } catch (e) { /* 静默失败 — progress 是 best-effort */ }
}

async function listAllTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs
    .filter((t) => t.id !== undefined && t.url)
    .filter((t) => /^https?:/i.test(t.url)) // 排除 chrome:// / file://
    .map((t) => ({
      id: tabToSessionId(t),
      url: t.url,
      title: t.title || '',
      type: 'ext_ws',
      active: t.active,
    }));
}

async function sendTabsUpdate() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const tabs = await listAllTabs();
  ws.send(JSON.stringify({ type: 'tabs_update', tabs }));
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, RECONNECT_DELAY_MS);
}

function connect() {
  // 防御:若已有 ws 在 OPEN / CONNECTING,先关掉旧的再开新的 —
  // MV3 SW 唤醒时会重新执行文件,可能产生 2 条并发连接。
  // 旧 ws 不主动 close 会让服务端以为是两个独立客户端,
  // 同一 payload 被两个 ws 各处理一次 → 双开 tab、双 result。
  if (ws) {
    try {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close(1000, 'replacing');
      }
    } catch (e) { /* ignore */ }
    ws = null;
  }
  try {
    ws = new WebSocket(WS_URL);
  } catch (err) {
    console.warn('[PyAgent Bridge] WebSocket 构造失败', err);
    scheduleReconnect();
    return;
  }

  ws.addEventListener('open', async () => {
    console.info('[PyAgent Bridge] WS 已连接', WS_URL);
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: 'ping' })); } catch (e) { /* ignore */ }
      }
    }, HEARTBEAT_INTERVAL_MS);
    // 先无条件发 ready(不依赖任何 chrome API) — 让服务端立刻知道 bg 已连,
    // 避免"连接成功但 30s 后才有消息"的诊断盲区。
    try {
      ws.send(JSON.stringify({ type: 'ready', sessionId: 'bg', url: '', title: '' }));
      console.info('[PyAgent Bridge] 已发 ready');
    } catch (err) {
      console.error('[PyAgent Bridge] 发 ready 失败', err);
    }
    // 再异步上报 tabs(失败不影响连接存活)
    try {
      await sendTabsUpdate();
    } catch (err) {
      console.error('[PyAgent Bridge] sendTabsUpdate 失败', err);
    }
  });

  ws.addEventListener('message', async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (err) {
      console.warn('[PyAgent Bridge] 非 JSON 消息,忽略', event.data);
      return;
    }
    // 协议:Python -> Browser {id, tabId, code}
    if (msg.id !== undefined && msg.tabId !== undefined && msg.code !== undefined) {
      const execId = msg.id;
      const tabIdRaw = msg.tabId;
      const code = msg.code;
      // 立刻 ACK
      try {
        ws.send(JSON.stringify({ type: 'ack', id: execId }));
      } catch (err) {
        // WS 已断开 — 等 onclose 处理
        return;
      }
      // === navigate 特殊路径 ===
      // 服务端 navigate 工具发来的 code 形如:
      //   "(...){ window.location.href = '<url>'; ... }"
      // 这种"用 location.href 导航"的策略在 chrome-extension:// 注入到 http 页面时
      // 通常能 work,但**在 SW 上下文**第一次调用 navigate 时,active tab 可能还是
      // chrome://newtab/ (没 http/https 页面可注入) → JS 执行失败。
      // 检测:从 code 提取 url 后,如果没合适的 http/https tab,
      // 直接 chrome.tabs.create 一个新 tab 导航过去。
      const navigateUrl = extractNavigateUrl(code);
      if (navigateUrl) {
        const reuseTab = !!(msg.meta && msg.meta.reuse_tab);
        try {
          const result = await handleNavigate(execId, navigateUrl, code, reuseTab);
          try {
            ws.send(JSON.stringify({ type: 'result', id: execId, data: result }));
          } catch (err) { /* ignore */ }
          try { await sendTabsUpdate(); } catch (e) { /* ignore */ }
          return;
        } catch (err) {
          const errMsg = err && err.message ? err.message : String(err);
          try {
            ws.send(JSON.stringify({ type: 'error', id: execId, error: errMsg }));
          } catch (e) { /* ignore */ }
          return;
        }
      }
      try {
        const result = await runCodeOnTab(tabIdRaw, code);
        try {
          ws.send(JSON.stringify({
            type: 'result',
            id: execId,
            data: result,
          }));
        } catch (err) {
          console.warn('[PyAgent Bridge] 发送 result 失败', err);
        }
      } catch (err) {
        const errMsg = err && err.message ? err.message : String(err);
        try {
          ws.send(JSON.stringify({
            type: 'error',
            id: execId,
            error: errMsg,
          }));
        } catch (e) { /* ignore */ }
      }
      // 执行后通知 Python 一次 tabs 变化 (可能有新开 tab)
      try {
        await sendTabsUpdate();
      } catch (err) {
        console.error('[PyAgent Bridge] sendTabsUpdate 失败', err);
      }
      return;
    }
    console.debug('[PyAgent Bridge] 忽略未知消息', msg);
  });

  ws.addEventListener('close', (event) => {
    console.info('[PyAgent Bridge] WS 已断开 code=' + event.code + ' reason=' + (event.reason || '(空)'));
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    scheduleReconnect();
  });

  ws.addEventListener('error', (err) => {
    console.warn('[PyAgent Bridge] WS 错误', err);
  });
}

// 启动 + 监听 tab 变化
chrome.runtime.onInstalled.addListener(() => {
  connect();
});
chrome.runtime.onStartup.addListener(() => {
  connect();
});
// service worker 被唤醒时立刻尝试连 (MV3 有时会休眠)
connect();

chrome.tabs.onCreated.addListener(() => sendTabsUpdate());
chrome.tabs.onRemoved.addListener(() => sendTabsUpdate());
chrome.tabs.onUpdated.addListener((_tabId, _change, tab) => {
  if (tab.active) sendTabsUpdate();
});
chrome.tabs.onActivated.addListener(() => sendTabsUpdate());

// 响应 popup 查询:报告当前 WS 真实状态。
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === 'popup_status') {
    let wsState = 'NONE';
    if (ws) {
      // WebSocket readyState: 0=CONNECTING 1=OPEN 2=CLOSING 3=CLOSED
      wsState = ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][ws.readyState] || 'UNKNOWN';
    }
    sendResponse({ wsState, wsUrl: WS_URL });
    return true; // 异步响应
  }
  return false;
});