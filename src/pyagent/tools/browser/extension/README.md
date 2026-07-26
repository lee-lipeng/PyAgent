# PyAgent Browser Bridge — Chrome 扩展

本目录是 Chrome / Edge 扩展源码,把当前浏览器 DOM 暴露给本地的 PyAgent
(默认 `ws://127.0.0.1:18787`)。

## 文件清单

| 文件 | 用途 |
|------|------|
| `manifest.json` | MV3 清单 |
| `background.js` | WS 服务端客户端,接收执行请求并用 `chrome.scripting.executeScript` 注入到目标 tab |
| `content.js` | content script: 注入 `__pyagent_screenshotHtml` (基于 html2canvas) 给 `browser_screenshot` 用 |
| `popup.html` + `popup.js` | 工具栏图标点开的小窗,心跳检测 WS 服务 |
| `vendor/html2canvas.min.js` | 本地 html2canvas (优先用本地副本,失败回退 CDN) |

## 安装

1. 打开 `chrome://extensions/`(Edge 是 `edge://extensions/`)
2. 打开右上角「开发者模式」开关
3. 点击「加载未打包的扩展程序」,选中本目录 (`extension/`)
4. 安装成功后,工具栏会出现一个 PyAgent 图标
5. 保持 Chrome 运行 — 扩展会连接到 PyAgent 的 WS 服务

> 首次安装后,`service_worker` 可能进入休眠。点击 PyAgent 图标打开 popup,
> 或在 PyAgent 调用 `browser_status` / `browser_install_hint` 即可触发重新连接。


## 安全提示

- WS 服务默认绑定 `127.0.0.1` — 仅本机可连
- `host_permissions: ["<all_urls>"]` 让扩展能在所有 http/https 页面注入 JS
  (这是浏览器自动化的必要权限)
- 扩展不发起任何外网请求,完全离线工作