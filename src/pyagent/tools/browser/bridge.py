"""浏览器桥核心 — 与 Chrome 扩展 / 注入脚本通信。

设计目标
1. 任何 LLM Agent 项目均可直接
   from pyagent.tools.browser.bridge import BrowserBridge 使用。
2. 协议 (ack / result / error / ready / tabs_update 三阶段)。
3. 可降级:WS 不可用时自动探测反向代理 HTTP 端口,转发给已运行的 master。

协议概览
- Python -> Browser:  {"id": "<uuid>", "tabId": "<sessionId>", "code": "<js>"}
- Browser -> Python:
    - {"type": "ack", "id": "<uuid>"}
    - {"type": "result", "id": "<uuid>", "data": ..., "newTabs": [...]}
    - {"type": "error", "id": "<uuid>", "error": "..."}
    - {"type": "ready" | "ext_ready", "sessionId": "1", "url": "...", "title": "..."}
    - {"type": "tabs_update", "tabs": [{"id": 1, "url": "...", "title": "...", "type": "..."}]}

ACK + 结果两阶段超时 借鉴GenericAgent(https://github.com/lsdefine/GenericAgent):
- 收到 ACK 后才真算 timeout,避免 WS 慢启动误判
- 未收到 ACK: BrowserDeliveryError  (指令未送达)
- 收到 ACK 但无结果: BrowserTimeoutError  (浏览器在跑)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import aiohttp
import websockets

from .exceptions import (
    BrowserDeliveryError,
    BrowserExecutionError,
    BrowserNotConnectedError,
    BrowserProtocolError,
    BrowserTimeoutError,
)
from .settings import BrowserSettings

logger = logging.getLogger(__name__)

SessionType = Literal["ws", "ext_ws", "http"]


@dataclass
class TabInfo:
    """一个浏览器 tab 的元信息。

    Attributes:
        id: 浏览器侧 sessionId 字符串 (Chrome debug 通常为 "1", "2"...)
        url: 当前页面 URL
        title: 当前页面标题
        type: 注入方式 (ws / ext_ws / http)
    """

    id: str
    url: str = ""
    title: str = ""
    type: SessionType = "ext_ws"

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "url": self.url, "title": self.title, "type": self.type}


@dataclass
class ExecuteResult:
    """BrowserBridge.execute_js() / execute() 的返回值。

    Attributes:
        data: 浏览器侧 JS 表达式的最终值 (字符串 / dict / list / 基本类型均可)
        new_tabs: 指令执行过程中新增的 tab (浏览器侧主动报告)
        raw: 浏览器返回的原始 result 字典 (调试用)
    """

    data: Any = None
    new_tabs: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class BrowserBridge:
    """浏览器桥客户端 — 单例由调用方维护 (见 __init__.py:get_bridge)。

    典型生命周期::

        bridge = BrowserBridge(BrowserSettings())
        await bridge.connect()
        result = await bridge.execute_js("document.title")
        await bridge.disconnect()

    不需要手动传 tab_id:桥会记住 default_tab_id (通常是第一个 ready /
    ext_ready 消息到达时锁定的 tab)。navigate 后会自动迁移到新页面。
    """

    def __init__(self, settings: BrowserSettings | None = None) -> None:
        self.settings = settings or BrowserSettings()
        # 多个浏览器扩展 WS 客户端连进来 (一个扩展 = 一个 ws 连接)
        self._ws_clients: set[Any] = set()
        self._ws: Any | None = None  # 兼容远端模式 (单连接)
        self._server: Any | None = None  # 本地 WS server 句柄
        self._tabs: dict[str, TabInfo] = {}
        self._default_tab_id: str | None = None
        self._is_remote: bool = False
        self._remote_url: str | None = None
        # 请求 -> 收到 ACK 的时间戳 (用于"ACK 后才算 timeout"判定)
        self._acks: dict[str, bool] = {}
        # 请求 -> 浏览器返回的最终 result/error
        self._results: dict[str, dict[str, Any]] = {}
        # 请求 -> progress 回调队列 (bg 推送的导航进度通过这里流式传给 LLM)
        self._progress: dict[str, list[dict[str, Any]]] = {}
        # 请求 -> 等待 progress 的 Event
        self._progress_events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()  # 序列化 WS send
        self._recv_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self) -> None:
        """启动本地 WS 服务端,等待 Chrome 扩展连进来。

        - PyAgent 端是 WS 服务端 (监听 ws_host:ws_port)
        - Chrome 扩展是 WS 客户端 (background.js 主动 new WebSocket(URL))
        """
        if self._closed:
            raise BrowserNotConnectedError("桥已关闭,无法重连 (请新建 BrowserBridge 实例)")
        if self._server is not None or self._is_remote:
            return

        # 启动本地 WS 服务端 (Chrome 扩展会主动连进来)
        host = self.settings.ws_host
        port = self.settings.ws_port
        try:
            server = await websockets.serve(
                self._on_client_connect,
                host,
                port,
                max_size=10 * 1024 * 1024,
            )
            self._server = server
            # 输出 server 真在 listen 的所有 socket (本地 + 远程扩展的 IP 都接受)
            for sock in server.sockets:
                ls = sock.getsockname()
                logger.info(
                    "WS server 已在 %s:%s 监听 (socket family=%s)",
                    ls[0],
                    ls[1],
                    sock.family.name,
                )
        except OSError as exc:
            # 端口已被占用
            self._server = None
            # 回退探测 HTTP 反向代理 master
            if self._probe_remote_master():
                self._is_remote = True
                self._remote_url = f"http://{self.settings.ws_host}:{self.settings.effective_http_port}/link"
                logger.info(f"端口被占用,检测到远程 master,走反向代理: {self._remote_url}")
                return

            raise BrowserNotConnectedError(
                f"无法启动浏览器桥 WS 服务端 {host}:{port}: {exc}。"
                f"可能另一个 PyAgent 实例已在监听该端口。"
                f"请确认 Chrome 扩展已加载并运行 (见 browser_install_hint 工具)"
            ) from exc
        logger.info(f"浏览器桥 WS 服务端已启动: ws://{host}:{port}")

    async def disconnect(self) -> None:
        """主动断开。"""
        self._closed = True
        # 关闭所有客户端连接
        for ws in list(self._ws_clients):
            with contextlib.suppress(Exception):
                await ws.close()
        self._ws_clients.clear()
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        # 关闭服务端
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
            self._recv_task = None
        # 清空未完成的请求 (抛错给调用方)
        for pending_id in list(self._results.keys()):
            self._results.pop(pending_id, None)
        for pending_id in list(self._acks.keys()):
            self._acks.pop(pending_id, None)

    def is_connected(self) -> bool:
        """当前是否可用 (本地 WS 服务端在听 / 远端 master 已配置)。

        本地模式:server 起着 → 可用 (client 还没连也没关系,LLM 调 status
        这种"查询"工具不需要 client,只有 execute_* 才需要)。
        远端模式:URL 已配置 → 可用。
        """
        if self._is_remote:
            return self._remote_url is not None and not self._closed
        return self._server is not None and not self._closed

    def has_clients(self) -> bool:
        """是否有至少一个"真扩展"客户端连进来 (background,非 popup-only)。

        用于 execute_* 路径:有客户端才能发指令;没客户端 = 提前报"未连接"。
        区分原则:只有 background 会发 ready/tabs_update/ack/result,
        popup-only 客户端只发 ping 然后立刻 close,标记为 popup 不算数。
        """
        return any(getattr(ws, "_pyagent_role", "unknown") != "popup" for ws in self._ws_clients)

    async def _wait_for_clients(self, timeout: float = 3.0) -> None:
        """等扩展客户端连进来,超时抛 BrowserNotConnectedError。

        解决"navigate 在 Runtime 启动后第一次调用时,扩展 bg.js
        MV3 service worker 还在冷启动"的竞态 — 给 3 秒握手窗口。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.has_clients():
                return
            await asyncio.sleep(0.1)
        raise BrowserNotConnectedError("等待扩展连接超时")

    async def ensure_extension_connected(self, total_timeout: float = 10.0) -> None:
        """确保扩展最终连上:先 3 秒等冷启动,失败后启 Chrome 再等 7 秒。

        设计目标:
            用户首次调 browser_navigate 时,即使 Chrome 没开 / 扩展
            还在冷启动,这个方法也会自动拉起 Chrome 并等扩展就绪,
            LLM 全程无感知,直接拿到 "navigate 成功" 的结果。

        实现策略(分层超时):
        1. _wait_for_clients(3s) — 覆盖 MV3 service worker 冷启动
        2. 若超时且 auto_launch_chrome:调 chrome_launcher 启 Chrome
        3. 再等 7 秒,覆盖"启 Chrome → 加载扩展 → bg.js reconnect"全程

        抛 BrowserNotConnectedError 表示彻底连不上(Chrome 没装 / 扩
        展没装),LLM 此时应改调 browser_install_hint 拿安装指引。
        """
        # 第一轮:3 秒握手窗口
        try:
            await self._wait_for_clients(timeout=3.0)
            return
        except BrowserNotConnectedError:
            pass  # 进入"自动拉起 Chrome"分支

        # 第二轮:启 Chrome + 再等 7 秒
        if self.settings.auto_launch_chrome:
            # 顶层 import 让 monkeypatch 能改 bridge 模块命名空间
            from pyagent.tools.browser import chrome_launcher

            launch_result = await chrome_launcher.async_launch_chrome_if_needed(self.settings.chrome_path)
            if launch_result in ("started", "already_running"):
                # 等 Chrome 自身就绪(debug 端口 listen)
                await chrome_launcher.wait_for_chrome_ready(timeout=5.0)
                # 再给 bg.js 一点时间 reconnect
                remaining = max(0.0, total_timeout - 3.0 - 5.0)
                if remaining > 0:
                    try:
                        await self._wait_for_clients(timeout=remaining)
                        return
                    except BrowserNotConnectedError:
                        pass
            elif launch_result == "not_found":
                raise BrowserNotConnectedError(
                    "Chrome 浏览器未找到。请安装 Chrome 浏览器,或在 settings 配置 chrome_path 指向 Chrome.exe 路径。"
                )

        # 第三轮:10 秒总超时仍未连上,彻底失败
        raise BrowserNotConnectedError(
            f"""
            等待 Chrome 扩展连接超时 ({total_timeout}s)。请确认:
            1.Chrome 已安装并启动;
            2.PyAgent 扩展已在chrome://extensions/ 加载;
            3.扩展没有报错(查看 service worker 控制台)。
            """
        )

    async def _on_client_connect(self, ws: Any) -> None:
        """新客户端连接 — 注册并启动该连接的 recv loop。"""
        peer = "unknown"
        try:
            with contextlib.suppress(Exception):
                peer = f"{ws.remote_address[0]}:{ws.remote_address[1]}"

            # 客户端角色:unknown(初始)→ bg(发过 ready/tabs_update/ack/result)→
            # popup(只发过 ping 然后 close,不算可用客户端)
            ws._pyagent_role = "unknown"
            logger.info(f"新 WS 客户端连入:{peer} (role=unknown)")
            self._ws_clients.add(ws)
            logger.info(f"浏览器扩展已注册 (当前{len(self._ws_clients)}个客户端) — 等扩展发 ready/tabs_update...")
            clean_close = True  # 默认正常关闭;ConnectionClosed 异常路径置 False
            try:
                async for msg in ws:
                    clean_close = False
                    logger.debug(
                        "收到扩展消息 (来自 %s, role=%s): %s", peer, getattr(ws, "_pyagent_role", "?"), msg[:200]
                    )
                    try:
                        data = json.loads(msg)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("收到非 JSON 消息,忽略: %r", msg)
                        continue
                    self._dispatch_message(data, ws)
            except websockets.ConnectionClosed:
                clean_close = False
                logger.info(
                    "浏览器扩展断开连接 (code=%s, role=%s)",
                    ws.close_code,
                    getattr(ws, "_pyagent_role", "?"),
                )
        except Exception as exc:
            # 顶层兜底:handshake 失败、协议层异常等 — 全部吞掉记 debug,
            # 让 websockets 库别把 traceback 打到 console。
            logger.debug("客户端 %s 连接处理异常 (已忽略): %s", peer, exc)
            clean_close = False
        finally:
            self._ws_clients.discard(ws)
            # MV3 service_worker 会周期性重启 (~30s+),popup 也是 ping 一下就走,
            # 这些都是 client 主动 close (clean_close=True),刷 INFO 污染 console。
            if clean_close:
                logger.debug(
                    "客户端正常关闭 (剩余 %d 个客户端): %s",
                    len(self._ws_clients),
                    peer,
                )
            else:
                logger.info(
                    "扩展断开 (剩余 %d 个客户端)",
                    len(self._ws_clients),
                )

    async def execute_js(
        self,
        code: str,
        tab_id: str | None = None,
        timeout: float | None = None,
    ) -> ExecuteResult:
        """执行 JS 表达式并返回结果。

        Args:
            code: 在页面上下文执行的 JS 表达式 (可以是 () => {...} 箭头函数)
            tab_id: 目标 tab,None 时用默认 tab
            timeout: 超时秒数,None 时用 settings.default_timeout

        Returns:
            ExecuteResult: data 为 JS 表达式的求值结果
        """
        await self.connect()
        timeout = timeout if timeout is not None else self.settings.default_timeout
        target = self._resolve_tab(tab_id)
        exec_id = str(uuid.uuid4())
        if self._is_remote:
            return await self._execute_remote(exec_id, code, target, timeout)

        # 本地模式:确保至少有一个扩展 WS 客户端连进来(冷启动 + 自动拉 Chrome)
        if not self.has_clients():
            await self.ensure_extension_connected(total_timeout=10.0)
        return await self._execute_ws(exec_id, code, target, timeout)

    async def navigate(
        self,
        url: str,
        tab_id: str | None = None,
        wait: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded",
        on_progress=None,
        reuse_tab: bool = False,
    ) -> ExecuteResult:
        """在指定 tab 中导航到 URL。

        通过 window.location.href = url + wait 事件监听实现。
        导航完成后会刷新 default_tab_id。

        :param on_progress: 可选回调 Callable[[str, str], None],
            浏览器推送的进度事件 (phase, text) 会实时转发。
        :param reuse_tab: True 时 background.js 走 chrome.tabs.update 在当前
            active tab 上原地跳转 (而非 chrome.tabs.create 新开 tab)。
            通过 meta.reuse_tab 字段告知浏览器。
        """
        await self.connect()
        # 给 Chrome 扩展一个短暂的"握手窗口" — bg.js 启动后才会
        # new WebSocket(URL),MV3 service worker 也可能冷启动。立刻 fail
        # 会让 LLM 误判"扩展没装"。若 3 秒还没连上且开启了 auto_launch_chrome,
        # 则自动启 Chrome 再等 — 用户体验上 navigate 首次调用永不失败
        # (除非 Chrome 真的没装)。
        if not self.has_clients():
            await self.ensure_extension_connected(total_timeout=10.0)
        target = self._resolve_tab(tab_id)
        code = f"""
        (() => {{
            return new Promise((resolve) => {{
                const target = {json.dumps(target)};
                if (window.location.href !== {json.dumps(url)}) {{
                    window.location.href = {json.dumps(url)};
                }}
                const event = {json.dumps(wait)};
                const handler = () => {{
                    window.removeEventListener(event, handler);
                    resolve({{
                        url: window.location.href,
                        title: document.title,
                        target: target,
                    }});
                }};
                if (document.readyState === 'complete' && event === 'load') {{
                    // 已经在目标页 (可能同步 SPA 跳转)
                    resolve({{
                        url: window.location.href,
                        title: document.title,
                        target: target,
                    }});
                    return;
                }}
                // Fast path: 即使 event 是 'load',SPA/搜索结果常不发 load,
                // 只要 DOM 已 interactive 就返回(URL 已 match 时尤其有意义)
                if (document.readyState === 'interactive' || document.readyState === 'complete') {{
                    setTimeout(() => resolve({{
                        url: window.location.href,
                        title: document.title,
                        target: target,
                        fastpath: true,
                    }}), 300);
                    return;
                }}
                window.addEventListener(event, handler, {{ once: true }});
                setTimeout(() => resolve({{
                    url: window.location.href,
                    title: document.title,
                    target: target,
                    timeout: true,
                }}), 8000);
            }});
        }})()
        """
        exec_id = str(uuid.uuid4())
        meta = {"reuse_tab": bool(reuse_tab)}
        if self._is_remote:
            result = await self._execute_remote(exec_id, code, target, timeout=30.0, meta=meta)
        else:
            result = await self._execute_ws(exec_id, code, target, timeout=30.0, on_progress=on_progress, meta=meta)
        return result

    def list_tabs(self, url_pattern: str = "") -> list[TabInfo]:
        """列出已知 tab;url_pattern 是简单的 substring 过滤。"""
        tabs = list(self._tabs.values())
        if url_pattern:
            tabs = [t for t in tabs if url_pattern in t.url]
        return tabs

    def get_tab(self, tab_id: str) -> TabInfo | None:
        return self._tabs.get(tab_id)

    @property
    def default_tab_id(self) -> str | None:
        return self._default_tab_id

    def _resolve_tab(self, tab_id: str | None) -> str:
        """解析目标 tab id;空 / 无效时回退到默认 / 第一个。

        注意:在没有任何已知 tab 的早期(navigate 第一次调用前),
        不抛 BrowserNotConnectedError — 返回 "bg" 哨兵值,
        让扩展端 runCodeOnTab 自己挑当前 active http/https tab 或新开一个。
        这样 LLM 调 navigate 不会因为"服务端没 tab 缓存"就失败。
        """
        if tab_id and tab_id in self._tabs:
            return tab_id
        if self._default_tab_id and self._default_tab_id in self._tabs:
            return self._default_tab_id
        alive = [t for t in self._tabs.values() if t.url]
        if alive:
            self._default_tab_id = alive[0].id
            return alive[0].id
        # 无已知 tab — 哨兵 "bg" 触发扩展端 fallback (新开 tab 或 active tab)
        return "bg"

    async def _execute_ws(
        self,
        exec_id: str,
        code: str,
        tab_id: str,
        timeout: float,
        on_progress=None,
        meta: dict[str, Any] | None = None,
    ) -> ExecuteResult:
        """执行 JS 并等待结果。

        :param on_progress: 可选回调 Callable[[str, str], None],
            bg 推送的 progress 事件 (phase, text) 被转交调用方
            (典型用途:让 LLM 实时看到导航/执行进度)。
        :param meta: 可选附加字段 (e.g. {"reuse_tab": True}),由 background.js
            在分发指令时读取以决定走 chrome.tabs.create 还是 update。
        """
        if not self._ws_clients:
            raise BrowserNotConnectedError("WS 服务端在跑,但尚无浏览器扩展连进来")

        payload_dict: dict[str, Any] = {"id": exec_id, "tabId": tab_id, "code": code}
        if meta:
            payload_dict["meta"] = meta
        payload = json.dumps(payload_dict)
        # 选一个目标 ws 发送 — 优先 role=bg 客户端。
        # 即使 _ws_clients 集合里因 SW 唤醒残留了多个 ws,只挑第一个role=bg 的发,
        # 避免同一 payload 被两个 ws 各处理一次(双 tab_created、双 result)。
        async with self._lock:
            target_ws = None
            for ws in list(self._ws_clients):
                if getattr(ws, "_pyagent_role", "unknown") != "popup":
                    target_ws = ws
                    break
            if target_ws is None:
                # 全是 popup-only,真扩展还没升级 role — 挑第一个 unknown
                target_ws = next(iter(self._ws_clients))
            try:
                await target_ws.send(payload)
            except websockets.ConnectionClosed as exc:
                self._ws_clients.discard(target_ws)
                raise BrowserNotConnectedError(f"WS 已断开: {exc}") from exc

        # 启动 progress 监听(如果有回调)
        progress_task: asyncio.Task | None = None
        if on_progress is not None:
            progress_task = asyncio.create_task(self._drain_progress(exec_id, on_progress))

        # 两阶段等待:ACK 后才真算 timeout
        acked = False
        deadline = time.monotonic() + timeout
        try:
            while exec_id not in self._results:
                await asyncio.sleep(0.05)
                if not acked and exec_id in self._acks:
                    acked = True
                    # ACK 后重新计时 (浏览器开始执行 JS)
                    deadline = time.monotonic() + timeout
                    self._acks.pop(exec_id, None)
                if time.monotonic() >= deadline:
                    # 清理
                    self._acks.pop(exec_id, None)
                    self._results.pop(exec_id, None)
                    if acked:
                        raise BrowserTimeoutError(f"浏览器已 ACK 但 {timeout}s 内未返回结果")
                    raise BrowserDeliveryError("指令未送达浏览器 (未收到 ACK,可能扩展未运行 / WS 断开)")
        finally:
            if progress_task is not None:
                progress_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await progress_task
            self._progress.pop(exec_id, None)
            self._progress_events.pop(exec_id, None)

        result = self._results.pop(exec_id)
        self._acks.pop(exec_id, None)
        if not result.get("success", True):
            raise BrowserExecutionError(result.get("data") or "unknown error")

        # 浏览器侧可能随 result 推送 newTabs
        new_tabs = result.get("newTabs") or []
        return ExecuteResult(data=result.get("data"), new_tabs=new_tabs, raw=result)

    async def _drain_progress(self, exec_id: str, on_progress) -> None:
        """持续把 progress 事件转发给 on_progress,直到收到 result/error。"""
        # 先把"已经在队列里"的 progress 推完,再等新事件
        while True:
            ev = self._progress_events.setdefault(exec_id, asyncio.Event())
            # 先看有没有已堆积的事件
            queue = self._progress.get(exec_id, [])
            if queue:
                for item in queue:
                    with contextlib.suppress(Exception):
                        on_progress(item.get("phase", ""), item.get("text", ""))
                self._progress[exec_id] = []
            # 检查 result 是否到了
            if exec_id in self._results:
                return
            # 等新事件
            try:
                await asyncio.wait_for(ev.wait(), timeout=0.5)
                ev.clear()
            except TimeoutError:
                if exec_id in self._results:
                    return

    async def _execute_remote(
        self,
        exec_id: str,
        code: str,
        tab_id: str,
        timeout: float,
        meta: dict[str, Any] | None = None,
    ) -> ExecuteResult:
        assert self._remote_url is not None
        body: dict[str, Any] = {
            "cmd": "execute_js",
            "execId": exec_id,
            "sessionId": tab_id,
            "code": code,
            "timeout": timeout,
        }
        if meta:
            body["meta"] = meta
        client_timeout = aiohttp.ClientTimeout(total=timeout + 5)
        try:
            async with (
                aiohttp.ClientSession(timeout=client_timeout) as session,
                session.post(self._remote_url, json=body) as resp,
            ):
                text = await resp.text()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise BrowserNotConnectedError(f"远端 master 通信失败: {exc}") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BrowserProtocolError(f"远端 master 返回非 JSON: {text[:200]}") from exc
        # 远端 master 通常返回 {"r": ...} 格式
        r = payload.get("r")
        if isinstance(r, dict) and "error" in r:
            err = r["error"]
            if isinstance(err, dict):
                # 未 ACK / 超时分类
                err_type = err.get("type", "")
                if "timeout" in err_type:
                    raise BrowserTimeoutError(err.get("message", str(err)))
                if "delivery" in err_type or "not_delivered" in err_type:
                    raise BrowserDeliveryError(err.get("message", str(err)))
                raise BrowserExecutionError(err.get("message", str(err)))
            raise BrowserExecutionError(str(err))
        return ExecuteResult(data=r, raw=payload)

    def _dispatch_message(self, msg: dict[str, Any], ws: Any | None = None) -> None:
        """处理一条浏览器消息。

        :param ws: 发送此消息的 ws 连接 — 用于标记客户端角色
                   (background vs popup-only)。
        """
        mtype = msg.get("type")
        if mtype == "ping":
            # 只有 popup-only 客户端会主动发 ping 然后 close;
            # background 心跳也在 open 30s 后才发,这里统一标记 popup。
            if ws is not None:
                ws._pyagent_role = "popup"
        elif mtype == "ack":
            exec_id = msg.get("id")
            if exec_id:
                self._acks[exec_id] = True
            if ws is not None:
                ws._pyagent_role = "bg"
        elif mtype == "progress":
            # 浏览器推送的中间进度(tab 已创建 / DOM 已 ready / 重定向等),
            # 让 LLM 在 navigate 还没返回时就能看到状态变化。
            exec_id = msg.get("id")
            if exec_id:
                self._progress.setdefault(exec_id, []).append(
                    {"phase": msg.get("phase", ""), "text": msg.get("text", "")}
                )
                ev = self._progress_events.get(exec_id)
                if ev is not None:
                    ev.set()
            if ws is not None:
                ws._pyagent_role = "bg"
        elif mtype == "result":
            exec_id = msg.get("id")
            if exec_id:
                # 浏览器协议:`type=result` 是 wrapper,内层 success 决定成败
                self._results[exec_id] = {
                    "success": bool(msg.get("success", True)),
                    "data": msg.get("data"),
                    "newTabs": msg.get("newTabs") or [],
                }
            if ws is not None:
                ws._pyagent_role = "bg"
        elif mtype == "error":
            exec_id = msg.get("id")
            if exec_id:
                self._results[exec_id] = {
                    "success": False,
                    "data": msg.get("error"),
                    "newTabs": msg.get("newTabs") or [],
                }
            if ws is not None:
                ws._pyagent_role = "bg"
        elif mtype in ("ready", "ext_ready"):
            if ws is not None:
                ws._pyagent_role = "bg"
            sid = str(msg.get("sessionId") or "")
            if sid:
                tab = TabInfo(
                    id=sid,
                    url=msg.get("url", ""),
                    title=msg.get("title", ""),
                    type="ws" if mtype == "ready" else "ext_ws",
                )
                self._tabs[sid] = tab
                if self._default_tab_id is None:
                    self._default_tab_id = sid
        elif mtype == "tabs_update":
            if ws is not None:
                ws._pyagent_role = "bg"
            new_tabs = msg.get("tabs") or []
            seen: set[str] = set()
            for t in new_tabs:
                tid = str(t.get("id"))
                if not tid:
                    continue
                seen.add(tid)
                self._tabs[tid] = TabInfo(
                    id=tid,
                    url=t.get("url", ""),
                    title=t.get("title", ""),
                    type=t.get("type", "ext_ws"),
                )
            # 删除已消失的 tab
            for tid in list(self._tabs.keys()):
                if tid not in seen:
                    self._tabs.pop(tid, None)
            # 默认 tab 失效 → 迁移到第一个
            if self._default_tab_id not in self._tabs:
                self._default_tab_id = next(iter(self._tabs), None)
        else:
            logger.debug("浏览器桥忽略未知消息类型: %s", mtype)

    def _probe_remote_master(self) -> bool:
        """探测本机 HTTP 端口是否被反向代理 master 占用。"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                return s.connect_ex((self.settings.ws_host, self.settings.effective_http_port)) == 0
        except OSError:
            return False
