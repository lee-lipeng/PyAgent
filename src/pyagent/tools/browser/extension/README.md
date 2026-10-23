# PyAgent Browser Bridge — Chrome 扩展

本目录是 Chrome / Edge 扩展源码,把当前浏览器 DOM 暴露给本地的 PyAgent
(默认 `ws://127.0.0.1:18787`)。

## 文件清单

| 文件 | 用途 |
|------|------|
| `manifest.json` | MV3 清单 |
| `background.js` | WS 服务端客户端,接收执行请求并用 `chrome.scripting.executeScript` 注入到目标 tab |
| `content.js` | isolated content script，只标记扩展已注入 |
| `screenshot_main.js` | 注册 `window.__pyagent_screenshotHtml`；与截图执行代码处于同一 MAIN world |
| `popup.html` + `popup.js` | 工具栏图标弹窗及连接状态检测 |
| `vendor/html2canvas.min.js` | 本地 html2canvas，截图时按需注入到 MAIN world |

## 安装

1. 打开 `chrome://extensions/`(Edge 是 `edge://extensions/`)
2. 打开右上角「开发者模式」开关
3. 点击「加载未打包的扩展程序」,选中本目录 (`extension/`)
4. 安装成功后,工具栏会出现一个 PyAgent 图标
5. 保持 Chrome 运行 — 扩展会连接到 PyAgent 的 WS 服务

> 首次安装后,`service_worker` 可能进入休眠。点击 PyAgent 图标打开 popup,
> 或在 PyAgent 调用 `browser_status` / `browser_install_hint` 即可触发重新连接。


## 安全提示

- WS 服务默认绑定 `127.0.0.1`；服务端拒绝常见网页来源的 Origin，但**未做客户端密钥认证**，本地进程仍可能连接。
- `host_permissions: ["<all_urls>"]` 意味着扩展可以在所有获准的 http/https 页面执行代码，谨慎使用。
- `browser_execute_js` 和网页本身可能访问网络；不要把“本地 WebSocket 连接”理解为严格离线沙箱。
- 更改 `background.js` / `screenshot_main.js` 后，在 `chrome://extensions/` 点击「重新加载」，再重新打开目标网页测试。