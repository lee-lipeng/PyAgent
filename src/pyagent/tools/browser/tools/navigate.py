"""browser_navigate:打开 URL。

两种模式
1.new_tab (默认):  在新 background tab 中打开 URL,保持旧页面不变。
LLM 可以同时在多个页面间切换。
2.reuse_tab: 在当前 active http/https tab 上原地跳转。
适合"我已经在 BOSS 直聘的搜索页,想直接看下一页"的场景。
背景里走 chrome.tabs.update(tabId, {url}),避免反复开 tab。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool


class BrowserNavigateArgs(BaseModel):
    url: str = Field(description="目标 URL (http/https/ftp/data 等)")
    tab_id: str | None = Field(default=None, description="目标 tab id,空则用默认 tab")
    wait: Literal["load", "domcontentloaded", "networkidle"] = Field(
        default="domcontentloaded",
        description=(
            "等待事件: domcontentloaded=DOM 解析完(SPA/搜索结果通常此时已可读,推荐)/"
            " load=完全加载(含图片) / networkidle=无网络请求 500ms"
        ),
    )
    reuse_tab: bool = Field(
        default=False,
        description=(
            "True=在当前 active http/https tab 上原地跳转 (chrome.tabs.update);"
            "False (默认)=新开一个 background tab,与旧页面隔离。"
            "适合需要保持上下文(如已登录的 SPA 状态)的连续浏览场景。"
        ),
    )


@tool(
    "browser_navigate",
    description=(
        "在浏览器中打开 URL 并等待页面就绪。\n"
        "默认新开 background tab (与旧页面隔离)。\n"
        "reuse_tab=true 时在当前 active tab 原地跳转(适合 SPA 内连续浏览)。\n"
        "返回新页面的 title/url,如发生重定向返回最终 URL。"
    ),
)
class BrowserNavigateTool(Tool):
    """导航到 URL。"""

    parameters_model = BrowserNavigateArgs
    execution_mode = "sequential"

    async def execute(self, tool_call_id, args, signal=None, on_update=None) -> ToolResult:
        from pyagent.tools.browser.tool_helpers import (
            async_ensure_bridge,
            translate_exception,
        )

        bridge, err = await async_ensure_bridge()
        if err is not None:
            return err

        if signal is not None and signal.is_set():
            return ToolResult(
                content="用户已中止导航",
                is_error=True,
                details={"error": "aborted"},
            )

        try:

            def _on_progress(phase: str, text: str) -> None:
                if on_update is None:
                    return
                on_update(
                    ToolResult(
                        content=f"[{phase}] {text}",
                        is_error=False,
                        details={"phase": phase, "text": text, "kind": "browser_progress"},
                    )
                )

            result = await bridge.navigate(
                url=args["url"],
                tab_id=args.get("tab_id"),
                wait=args["wait"],
                reuse_tab=bool(args.get("reuse_tab", False)),
                on_progress=_on_progress if on_update else None,
            )
        except Exception as exc:  # noqa: BLE001
            return translate_exception(exc)

        data = result.data if isinstance(result.data, dict) else {}
        return ToolResult(
            content=f"已导航到 {data.get('url', args['url'])}\n页面标题: {data.get('title', '(无标题)')}",
            details={
                "url": data.get("url"),
                "title": data.get("title"),
                "timeout_flag": data.get("timeout", False),
                "new_tabs": result.new_tabs,
                "reuse_tab": bool(args.get("reuse_tab", False)),
            },
        )
