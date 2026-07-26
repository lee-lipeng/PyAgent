"""browser_status:浏览器桥状态/tab 管理一体化工具。

模式 (mode 参数):
    - "status" (默认):返回连接状态、WS 地址、已知 tab 列表
    - "list_tabs":列出 tab,可按 url_pattern 过滤
    - "switch_tab":把默认 tab 切换到 tab_id
"""

from __future__ import annotations

import contextlib

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool


class BrowserStatusArgs(BaseModel):
    mode: str = Field(
        default="status",
        description="操作模式:status(查询状态,默认)/list_tabs(列出 tab)/switch_tab(切换默认 tab)。",
    )
    url_pattern: str = Field(
        default="",
        description="[list_tabs]URL 子串过滤,空字符串表示全部",
    )
    tab_id: str = Field(
        default="",
        description="[switch_tab]目标 tab id(从 list_tabs 获取)",
    )


@tool(
    "browser_status",
    description=(
        "浏览器桥状态/tab 管理一体化工具。mode=status(默认) 查询连接状态和已知 tab;"
        "mode=list_tabs 列出所有 tab(可按 url_pattern 过滤);"
        "mode=switch_tab 把默认 tab 切到 tab_id。"
        "未连接时返回引导,可用 browser_install_hint 获取安装指引。"
    ),
)
class BrowserStatusTool(Tool):
    """浏览器桥状态/tab 管理。"""

    parameters_model = BrowserStatusArgs
    execution_mode = "sequential"

    async def execute(
        self,
        tool_call_id: str,
        args: dict,
        signal=None,
        on_update=None,
    ) -> ToolResult:
        # 使用统一的 async_ensure_bridge — 保证 server ready
        from pyagent.tools.browser.tool_helpers import async_ensure_bridge

        bridge, err = await async_ensure_bridge()
        if err is not None:
            return err

        mode = args.get("mode", "status") or "status"

        if mode == "status":
            # 防御性握手:server 已起但 bg 还没连时,等 3 秒给 MV3 SW 冷启动。
            # LLM 第一次调 status 经常是 Runtime 刚启动后的首次交互,
            # 此时 bg.js 还在 SW 唤醒阶段。若直接报"未连接",LLM 会错误地走
            # browser_install_hint,实际只是 handshake 还没完成。
            if not bridge._is_remote:
                bg_connected = any(getattr(ws, "_pyagent_role", "unknown") != "popup" for ws in bridge._ws_clients)
                if not bg_connected:
                    # 超时也不报错 — status 本来就是只读
                    with contextlib.suppress(Exception):
                        await bridge._wait_for_clients(timeout=3.0)

            tabs = bridge.list_tabs()
            # 区分真扩展(bg)客户端 vs popup-only(只发 ping 然后 close)
            bg_count = sum(1 for ws in bridge._ws_clients if getattr(ws, "_pyagent_role", "unknown") != "popup")
            popup_count = len(bridge._ws_clients) - bg_count
            client_count = bg_count
            lines = [
                f"浏览器桥已就绪 ({bridge.settings.ws_url})",
                f"服务端: {'监听中' if bridge._server else '未启动'}",
                f"扩展 background 连接数: {bg_count}" + (f" (另有 {popup_count} 个 popup-only)" if popup_count else ""),
                f"远程模式: {'是' if bridge._is_remote else '否'}",
                f"默认 tab: {bridge.default_tab_id or '(无)'}",
                f"已知 tab 数量: {len(tabs)}",
            ]
            if client_count == 0 and not bridge._is_remote:
                bg_hint = []
                if popup_count > 0:
                    bg_hint.append(
                        f"  ⚠ 检测到 {popup_count} 个 popup-only WS 连入后立刻断开 — "
                        "说明 WS 服务端可达,但 Chrome 扩展 background.js 没有维持连接。"
                    )
                lines.append(
                    "\n⚠ 没有任何 Chrome 扩展 background 连进来。WS 服务端已在监听,但"
                    " background.js 可能尚未成功连接 (常见原因:扩展未加载 / "
                    "service worker 被 Chrome 回收 / popup 显示的 '已连接' "
                    "≠ background 真的建立了 ws)。请按以下步骤排查:\n"
                    "  1. 确认 chrome://extensions/ 中 PyAgent Browser Bridge 已启用\n"
                    "  2. 点击扩展的 '服务工作进程' → '检查视图',看是否有报错\n"
                    "  3. 必要时点扩展 popup 唤醒一次 background service worker"
                )
                lines.extend(bg_hint)
            if tabs:
                lines.append("Tab 列表:")
                for t in tabs:
                    lines.append(f"  - [{t.id}] {t.title or '(无标题)'} - {t.url or '(无 URL)'} ({t.type})")
            else:
                lines.append("(无已知 tab,可调用 browser_navigate 打开页面)")
            return ToolResult(
                content="\n".join(lines),
                details={
                    "mode": mode,
                    "ws_url": bridge.settings.ws_url,
                    "is_remote": bridge._is_remote,
                    "default_tab_id": bridge.default_tab_id,
                    "tab_count": len(tabs),
                    "client_count": client_count,
                    "popup_count": popup_count,
                    "server_listening": bridge._server is not None,
                },
            )

        if mode == "list_tabs":
            pattern = args.get("url_pattern", "") or ""
            tabs = bridge.list_tabs(url_pattern=pattern)
            if not tabs:
                return ToolResult(
                    content=(
                        f"未找到匹配 tab (pattern={pattern!r})。可能尚未打开任何 tab,可调用 browser_navigate 打开。"
                    ),
                    details={
                        "mode": mode,
                        "tab_count": 0,
                        "pattern": pattern,
                        "default_tab_id": bridge.default_tab_id,
                    },
                )
            lines = [f"匹配 tab ({len(tabs)} 个):"]
            for t in tabs:
                lines.append(f"  - [{t.id}] {t.title or '(无标题)'}\n      URL: {t.url or '(无)'} | type: {t.type}")
            return ToolResult(
                content="\n".join(lines),
                details={
                    "mode": mode,
                    "tab_count": len(tabs),
                    "tabs": [t.to_dict() for t in tabs],
                    "pattern": pattern,
                    "default_tab_id": bridge.default_tab_id,
                },
            )

        if mode == "switch_tab":
            tab_id = args.get("tab_id", "") or ""
            if not tab_id:
                available = [t.id for t in bridge.list_tabs()]
                return ToolResult(
                    content=(
                        "switch_tab 模式需要 tab_id 参数。"
                        f"当前已知 tab: {available or '(无)'}。"
                        "可先用 mode=list_tabs 查询。"
                    ),
                    is_error=True,
                    details={
                        "error": "missing_tab_id",
                        "available": available,
                    },
                )
            tab = bridge.get_tab(tab_id)
            if tab is None:
                available = [t.id for t in bridge.list_tabs()]
                return ToolResult(
                    content=(
                        f"tab_id={tab_id!r} 不存在。当前已知 tab: {available or '(无)'}。请用 mode=list_tabs 重新查询。"
                    ),
                    is_error=True,
                    details={
                        "error": "tab_not_found",
                        "tab_id": tab_id,
                        "available": available,
                    },
                )
            bridge._default_tab_id = tab_id
            return ToolResult(
                content=f"已切换默认 tab 到 [{tab_id}]: {tab.title or '(无标题)'} - {tab.url}",
                details={"mode": mode, "tab": tab.to_dict()},
            )

        valid = ["status", "list_tabs", "switch_tab"]
        return ToolResult(
            content=f"未知 mode={mode!r}。可选值: {valid}。默认 mode=status (查询连接状态)。",
            is_error=True,
            details={"error": "unknown_mode", "valid_modes": valid},
        )
