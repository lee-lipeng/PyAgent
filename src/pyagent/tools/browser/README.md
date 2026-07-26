# PyAgent Browser Tools

让 LLM Agent 通过 Chrome 扩展操作浏览器页面 (读取 DOM、执行 JS、导航、截图)。

## 架构

```
┌─────────────────┐      WS (JSON 消息)       ┌─────────────────┐
│  PyAgent 进程   │  ◀──────────────────────▶  │ Chrome 扩展 SW   │
│  BrowserBridge  │   ws://127.0.0.1:18787    │  background.js  │
│  + 6 个工具     │                            │  (MV3 service    │
└─────────────────┘                            │   worker)       │
       ▲                                       └────────┬────────┘
       │ tools/browser/tools/*.py                        │ chrome.scripting
       │ Tool 抽象 + Pydantic args                       │ .executeScript
       │                                                  ▼
       │                                          ┌─────────────────┐
       │                                          │ 当前活跃 tab     │
       │                                          │ (MAIN world JS) │
       │                                          └─────────────────┘
```

**两层职责清晰**:
- `bridge.py` / `settings.py` / `exceptions.py` 零 PyAgent 内部依赖,
  任何 Python 项目都能直接 `BrowserBridge.connect()` / `.execute_js()` 复用
- `tools/*` 工具层 (`Tool` ABC + `@tool` 装饰器)

## 6 个工具

| 工具 | 用途 |
|------|------|
| `browser_status` | 连接状态 + tab 管理 (`mode=status/list_tabs/switch_tab`) |
| `browser_navigate` | 在指定 tab 打开 URL,支持复用当前 tab + wait 事件 |
| `browser_scan` | 读取页面 DOM (text/html/snapshot/lists 四种 mode,可选 simplify) |
| `browser_execute_js` | 在 MAIN world 跑任意 JS,可启用 DOM/network 监控 |
| `browser_screenshot` | 当前页面截图 (base64,真实像素,html2canvas 注入) |
| `browser_install_hint` | 首次未连接时返回完整安装指引 |

### 工具选择经验法则 (给 LLM)

| 想做什么 | 调用 |
|---------|------|
| 查连接状态 / 列 tab / 切默认 tab | `browser_status(mode='status' \| 'list_tabs' \| 'switch_tab')` |
| 打开新页面 / 当前 tab 跳转 | `browser_navigate(url=..., reuse_tab=False/True, wait='domcontentloaded')` |
| 读页面文本(已 token 优化) | `browser_scan(mode='text', simplify='full')` |
| 读页面 HTML(已优化) | `browser_scan(mode='html', simplify='full')` |
| 找列表/重复结构 | `browser_scan(mode='lists', find_lists=True)` |
| 带元素 ref 的 snapshot | `browser_scan(mode='snapshot')` |
| 只看页面某个区域 | `browser_scan(mode='html', selector='.job-list')` |
| 找元素 / 操作页面 (点击/输入/滚动/填表) | `browser_execute_js(code='...')` |
| 跑 JS 并监控 DOM 变化 | `browser_execute_js(code='...', monitor='dom')` |
| 跑 JS 并捕获 XHR/Fetch | `browser_execute_js(code='await new Promise(r=>setTimeout(r,2000))', monitor='network')` |
| DOM + 网络同时监控 | `browser_execute_js(code='...', monitor='full')` |
| 截屏存档 | `browser_screenshot(full_page=True, format='jpeg', quality=70)` |
| 桥未连接 | `browser_install_hint` 看指引 |

### 新功能要点

#### `browser_scan` 四种 mode

- **`text`**: 提取纯文本(token 高效),适合阅读正文
- **`html`**: 优化过的 HTML(自动折叠 iframe/shadow DOM、剥离无用属性、URL 短化)
  - `simplify='none'`: 原始 outerHTML
  - `simplify='light'`: 基础清理(删 script/style/comment 标签)
  - `simplify='full'`(**默认**): 完整优化(`optHTML` 整页精简 + `optimize_html_for_tokens` 属性瘦身),
    token 节省 60%+。算法在 `_htmlopt.py`
- **`snapshot`**: 类似 Playwright `getAccessibilitySnapshot`,给元素 ref (`e1`/`e2`...)
  - LLM 可用 ref 直接 `document.querySelector('[data-pyagent-ref="e3"]')` 二次定位
  - snapshot 模式固定 `simplify='light'`(仅删 script/style)
- **`lists`**: 探测重复结构(商品列表/搜索结果),输出 `selector / itemCount / score / tag`
  不返回 DOM 内容,只返回 selector 让 LLM 自己精准再扫

可选参数:
- `selector='.job-list'`: CSS 选择器,只扫描指定元素 (避免扫描整页)
- `max_chars=20000`: 返回文本最大字符数;HTML 模式超过会用 `smartTruncate` 按子树比例截断
  (保护 `[FAKE ELEMENT]` 提示不被吃掉)
- `find_lists=True`: 在 text/html/snapshot 末尾追加 `lists` 段
- `cutlist=True`: 当 `find_lists` 找到高分列表时,自动从主输出里裁掉它,替换为
  `[FAKE ELEMENT] N more items hidden, selector: "..."` 提示,大量节省 token
- `instruction='关键词'`: 给 cutlist 用,优先保留文本里含此关键字的列表项(最多 6 个)

#### `browser_execute_js` 监控参数

- `monitor='off'`(**默认**): 只执行 JS
- `monitor='dom'`: 跑前取 baseline HTML signature,跑后 diff 出新增/删除的元素节点
  - 返回 `details.dom_diff = {changed: N, top_change: "..."}`
- `monitor='network'`: 在 IIFE 里包一层 `window.__pyagent_api_mon`,捕获 XHR/fetch
  (status / method / url / request body / response body 前 maxBody 字节,默认 4096)
  - 返回 `details.api_monitor = {installed, count, statusCounts, totalBytes, requests}`
- `monitor='full'`: 同时启用 DOM + network

常用捕获模式: `monitor='network' + code='await new Promise(r=>setTimeout(r,2000))'`
可"等几秒,收集所有 fetch / XHR"。**该模式下超时自动提升到 `network_capture_timeout`(默认 60s)**,
避免撞到普通 15s 默认超时。

`browser_execute_js` 还支持:
- `tab_id='123'`: 指定目标 tab (默认用 `bridge.default_tab_id`)
- `timeout=15.0`: 普通模式超时秒数 (1-120)
- `timeout=60.0` 配合 `monitor=network|full` 时建议放大

#### `browser_navigate` 参数

- `url`: 目标 URL (http/https/ftp/data 等)
- `reuse_tab=False`(**默认**): 创建新 background tab,与旧页面隔离
- `reuse_tab=True`: 复用当前 active http/https tab,原地跳转(适合 SPA 内连续浏览)
- `wait='domcontentloaded'`(**默认**): DOM 解析完即返回
  - `wait='load'`: 等所有图片/资源加载完
  - `wait='networkidle'`: 500ms 内无网络请求
- `tab_id='123'`: 显式指定要导航的 tab (默认用 `bridge.default_tab_id`)

#### `browser_screenshot` 真实实现

通过 `content.js` 注入 `html2canvas`(优先本地 `vendor/html2canvas.min.js`,否则 CDN 兜底),
将当前页面 DOM 渲染为 PNG/JPEG,直接返回 base64 dataURL。

参数:
- `full_page=False`: 是否截整页 (True=完整滚动高度,False=仅可视区域)
- `format='png'`: 'png' / 'jpeg',JPEG 可用 quality 控制体积
- `quality=80`(1-100): JPEG 质量,值越小体积越小
- `scale=1.0`(0.1-3.0): 缩放系数,高 scale 会显著增加体积
- `selector='.job-list'`: 只截匹配元素 (而非整页)
- `tab_id='123'`: 目标 tab

若结果超过 `screenshot_max_bytes`(默认 200KB)且格式是 JPEG,自动降采样 (quality 40, scale 0.75),
并在 content 末尾追加 `⚠️ 截图仍超过 N bytes` 提示。

**为什么不用 `chrome.tabs.captureVisibleTab`**: 该 API 只能截**视口**且受权限限制;
整页截图需要滚动拼接(对 lazy-load 不友好)。html2canvas 把 DOM 重绘成 canvas,对 SVG / 动画支持更完整,
也避开了截屏在某些反爬场景(指纹检测)被识别的问题。

#### `_htmlopt.py` 共享 JS 算法库

9 个 JS 字符串常量 + `wrap_iife(*blocks, expression)` 组合 helper:

| 常量 | 作用 |
|------|------|
| `JS_OPTHTML` | GenericAgent 核心: 深度克隆 DOM、剥离 iframe/shadow DOM、autofill 保护、标记 K:container/R:* 后删 overlay |
| `JS_FINDMAINLIST` | 在容器内找候选列表,按面积/数量/均匀性/布局评分 |
| `JS_OPTIMIZE_FOR_TOKENS` | 属性瘦身: 删 style、长 URL → `__url__`/`__img__`、长 value → 截断 |
| `JS_SMART_TRUNCATE` | 子树按大小比例截断,保护 `[FAKE ELEMENT]` |
| `JS_APPLY_CUTLIST` | 列表项压缩: 保留前 3 项 + instruction 命中项,其余替换为 `[FAKE ELEMENT]` 提示 |
| `JS_FIND_CHANGED_ELEMENTS` | 简化版 DOM diff: tag+attrs+首段文本三段签名 |
| `JS_API_MONITOR_START` | 拦截 `window.fetch` + `XMLHttpRequest`,记录请求/响应 (含 body 截断) |
| `JS_API_MONITOR_QUERY` | 按 urlPattern / method / onlyDone / onlyError 过滤查询 |
| `JS_API_MONITOR_CLEAR` | 清空已捕获记录 |

`wrap_iife()` 把多段 JS 拼成 `(...)()` 形式,末尾 `return <expression>`,这样
`chrome.scripting.executeScript(..., world: 'MAIN')` 能直接拿到表达式的值。

## 安装

### 1. 安装 Python 依赖

```bash
pip install websockets aiohttp
# 或用 uv
uv add websockets aiohttp
```

`pyproject.toml` 已声明这两个依赖。

### 2. 安装 Chrome 扩展

扩展源码在 `extension/` 目录,加载步骤:

1. 打开 `chrome://extensions/`(Edge 是 `edge://extensions/`)
2. 右上角打开「开发者模式」开关
3. 点击「加载未打包的扩展程序」,选中 `src/pyagent/tools/browser/extension/`
4. 安装成功后,工具栏会出现 PyAgent 图标
5. 保持 Chrome 运行 — 扩展 service_worker 会自动连 `ws://127.0.0.1:18787`

> 详见 `extension/README.md`

### 3. 验证

启动 PyAgent,调用:

```python
await tool_registry.execute("browser_status")
# → "浏览器桥已连接 (ws://127.0.0.1:18787) ..."
```

或通过 LLM 提示工程让 agent 自己调用 `browser_status` 自检。

## 配置

`pyproject.toml` → `[tool.pyagent]` / `settings.json` / 环境变量 `PYAGENT_BROWSER__*`

```python
BrowserSettings(
    enabled=True,                # 总开关 (False 时所有 browser_* 工具返回 not_connected)
    ws_host="127.0.0.1",         # 仅本机,安全
    ws_port=18787,               # WS 端口 (Chrome 扩展连接的目标)
    http_port=None,              # HTTP 反向代理端口; None 时自动用 ws_port + 1

    auto_connect=True,           # Runtime.setup() 时初始化单例 (连接是 lazy 的)
    default_timeout=15.0,        # JS 执行默认超时 (秒)
    auto_launch_chrome=True,    # bg.js 连不上时自动拉起 Chrome
    chrome_path=None,            # Chrome 二进制路径; None 时由 chrome_launcher 自动检测

    scan_default_max_chars=20_000,  # scan mode=text 默认截断阈值
    scan_max_chars=100_000,         # scan 绝对上限
    screenshot_max_bytes=200_000,   # 截图最大字节 (base64)
    network_capture_timeout=60.0,   # execute_js(monitor=network/full) 单次最大超时 (秒)

    install_doc_url="https://github.com/lee-lipeng/PyAgent/tree/main/src/pyagent/tools/browser",
)
```

便捷属性:
- `settings.ws_url`: 完整 WS URL (如 `ws://127.0.0.1:18787`)
- `settings.effective_http_port`: 实际 HTTP 端口 (auto-fill `ws_port + 1`)

> 配置单一来源:`BrowserSettings` 只在 `src/pyagent/tools/browser/settings.py` 定义,
> `pyagent.config.settings.Settings` 通过 `from pyagent.tools.browser.settings import BrowserSettings`
> 直接复用。任何字段增减只需要改 `BrowserSettings` 一处。

## 协议 (扩展 ↔ Python)

5 种消息类型,JSON 格式:

```jsonc
// Python → Browser (执行请求,可选 meta 携带 reuse_tab 等控制位)
{"id": "exec-001", "tabId": "123", "code": "() => document.title",
 "meta": {"reuse_tab": false}}

// Browser → Python (立即 ACK)
{"type": "ack", "id": "exec-001"}

// Browser → Python (成功结果,异步到达;data 可为任意 JSON 可序列化值)
{"type": "result", "id": "exec-001", "data": "GitHub"}

// Browser → Python (执行错误)
{"type": "error", "id": "exec-001", "error": "TypeError: ..."}

// Browser → Python (tab 列表变化;SW 启动 / tab create/update/remove/activate 时触发)
{"type": "tabs_update", "tabs": [{"id": "123", "url": "...", "title": "...", ...}]}

// 另:SW 连上服务端时立刻发 (不依赖任何 chrome API)
{"type": "ready", "sessionId": "bg", "url": "", "title": ""}
```

ACK + 结果两阶段超时:Python 等 ACK 超时 (`delivery_timeout`,默认 5s) 报 `delivery`,
ACK 后等 result 超时 (`default_timeout`) 报 `timeout`。
**网络监控模式 (`monitor=network|full`) 自动用 `network_capture_timeout` (默认 60s)**。

异常类型 (`pyagent.tools.browser.exceptions`):

| 异常 | 含义 |
|------|------|
| `BrowserNotConnectedError` | 桥未连接 / WS 服务未起 |
| `BrowserDeliveryError` | 请求未送达浏览器 (ACK 超时) |
| `BrowserTimeoutError` | 浏览器已收到但执行超时 |
| `BrowserExecutionError` | 浏览器内 JS 抛错 |
| `BrowserProtocolError` | 协议层错误 (非法 JSON / 未知消息类型) |

## 复用给其他 Agent 项目

只需复制 `bridge.py` + `settings.py` + `exceptions.py` 三个文件:

```python
from browser_bridge import BrowserBridge, BrowserSettings

bridge = BrowserBridge(BrowserSettings(ws_port=18787))
await bridge.connect()
result = await bridge.execute_js("() => document.title")
# → ExecuteResult(value="...", elapsed_ms=12)
await bridge.disconnect()
```

`TabInfo` / `ExecuteResult` 是 dataclass,序列化友好。

可选:再加 `chrome_launcher.py` 可在 WS 连不上时自动拉起 Chrome(否则需要用户手动打开 Chrome)。

完整 public API(`BrowserBridge`):

| 方法 | 用途 |
|------|------|
| `await connect()` | 启动 WS 服务端 + 等客户端连接 |
| `await disconnect()` | 关掉 WS 服务端 + 通知所有客户端 |
| `await execute_js(code, tab_id=None, timeout=15.0)` | 在指定 tab 跑 JS,返回 `ExecuteResult` |
| `await execute_js_with_progress(code, ..., on_progress)` | 实时回报 navigate 各阶段 progress |
| `list_tabs(url_pattern="")` | 同步列出已知 tab (可按 url_pattern 过滤) |
| `switch_tab(tab_id)` | 切换默认 tab |
| `set_default_tab(tab_id)` | 显式设默认 tab |
| `is_connected` / `default_tab_id` / `settings` | 只读属性 |

## 安全

- WS 默认 `127.0.0.1` — 仅本机进程可连
- 扩展 `host_permissions: ["<all_urls>"]` 是浏览器自动化的必要权限
- 扩展不发起任何外网请求
- LLM 通过 `browser_execute_js` 跑的 JS 等同于在用户浏览器里执行 — 慎用,
  不要在不受信任的 prompt 下使用此工具