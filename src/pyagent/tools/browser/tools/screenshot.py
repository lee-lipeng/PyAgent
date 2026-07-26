"""browser_screenshot:截取当前页面 (整页或可视区域),返回 base64。

实现机制
1. Chrome 扩展的 content.js 在每个页面上异步注入 html2canvas (优先使用
   扩展本地 web_accessible_resources 副本,失败回退 CDN)。
2. 注入完成后挂全局 window.__pyagent_screenshotHtml({...}) —
   接受 selector / format / quality / fullPage / scale / bgColor 参数,
   返回 {data_url, width, height, format, fullPage}。
3. Python 侧把工具参数打包成 ({async () => await window.__pyagent_screenshotHtml({...})})()
   通过 chrome.scripting.executeScript(..., world: 'MAIN') 执行。
4. 返回的 data URL 是 data:image/png;base64,... 形式,解码后写 details。

为什么不用 chrome.tabs.captureVisibleTab:
captureVisibleTab 是 Chrome 扩展 API,只能截**视口**且受权限限制;
整页截图需要滚动拼接(对 lazy-load 不友好)。
html2canvas 把 DOM 重绘成 canvas,对 SVG/SVG foreignObject/CSS 动画支持更完整,
也避开了截屏在某些反爬场景(指纹检测)被识别的问题。
"""

from __future__ import annotations

import base64
import json

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool


class BrowserScreenshotArgs(BaseModel):
    full_page: bool = Field(
        default=False,
        description="是否截整页: true=完整滚动高度; false=只截可视区域。整页截图体积大,默认 False。",
    )
    quality: int = Field(
        default=80,
        ge=1,
        le=100,
        description="JPEG 质量 (仅 format=jpeg 生效),值越小体积越小",
    )
    format: str = Field(
        default="png",
        description="图片格式: png (无损但大) / jpeg (可调 quality,适合大页面)",
    )
    scale: float = Field(
        default=1.0,
        ge=0.1,
        le=3.0,
        description="缩放系数 (1=原尺寸,2=2x 高清,0.5=压缩)。注意:高 scale 会显著增加体积。",
    )
    selector: str | None = Field(
        default=None,
        description="可选 CSS selector,只截匹配元素 (而非整页)。适合『只看某个表单/组件』的场景,可避开页面其他噪音。",
    )
    tab_id: str | None = Field(default=None, description="目标 tab id,空则用默认 tab")


@tool(
    "browser_screenshot",
    description=(
        "截取当前页面截图,返回 base64 编码 (data URL)。\n"
        "通过 html2canvas 在浏览器内重绘 DOM 得到真实像素 (非系统截屏,无指纹)。\n"
        "full_page=true 截整页;false 只截可视区域。\n"
        "selector 可限定截图范围 (CSS 选择器)。\n"
        "截图超过体积上限会自动降采样并提示。"
    ),
)
class BrowserScreenshotTool(Tool):
    """截图工具。"""

    parameters_model = BrowserScreenshotArgs
    execution_mode = "sequential"

    async def execute(self, tool_call_id, args, signal=None, on_update=None) -> ToolResult:
        from pyagent.tools.browser.tool_helpers import (
            async_ensure_bridge,
            get_settings,
            translate_exception,
        )

        bridge, err = await async_ensure_bridge()
        if err is not None:
            return err

        settings = get_settings()
        max_bytes = settings.screenshot_max_bytes
        fmt = args["format"].lower()
        quality = max(1, min(100, args["quality"]))
        full_page = bool(args["full_page"])
        scale = float(args.get("scale") or 1.0)
        selector = args.get("selector")
        tab_id = args.get("tab_id")

        js = _build_screenshot_js(
            full_page=full_page,
            fmt=fmt,
            quality=quality,
            scale=scale,
            selector=selector,
        )

        try:
            result = await bridge.execute_js(js, tab_id=tab_id, timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            return translate_exception(exc)

        if not isinstance(result.data, dict):
            return ToolResult(
                content=f"截图失败: 浏览器返回非预期格式 ({type(result.data).__name__})",
                is_error=True,
                details={"error": "unexpected_format"},
            )

        if result.data.get("__error__"):
            err_msg = result.data["__error__"]
            hint = "请确认扩展已正确加载且 content.js 已注入 (见 browser_install_hint)。"
            return ToolResult(
                content=f"截图失败: {err_msg}\n提示: {hint}",
                is_error=True,
                details={
                    "error": "screenshot_failed",
                    "selector": selector,
                    "full_page": full_page,
                },
            )

        data_url = result.data.get("data_url", "")
        if not data_url.startswith("data:image/"):
            return ToolResult(
                content=f"截图失败: 返回非 data URL ({data_url[:80]})",
                is_error=True,
                details={"error": "bad_data_url"},
            )

        # 提取 base64
        try:
            _, b64 = data_url.split(",", 1)
        except ValueError:
            return ToolResult(
                content="截图失败: data URL 格式异常",
                is_error=True,
                details={"error": "bad_data_url"},
            )

        b64_bytes = len(b64.encode("ascii"))
        truncated = b64_bytes > max_bytes
        downgrade_note = ""

        if truncated and fmt == "jpeg":
            # 自动降级到 quality=40 重截
            js_lowq = _build_screenshot_js(
                full_page=full_page,
                fmt="jpeg",
                quality=40,
                scale=max(scale * 0.75, 0.5),
                selector=selector,
            )
            try:
                r2 = await bridge.execute_js(js_lowq, tab_id=tab_id, timeout=60.0)
                if (
                    isinstance(r2.data, dict)
                    and r2.data.get("data_url")
                    and r2.data["data_url"].startswith("data:image/")
                ):
                    data_url = r2.data["data_url"]
                    _, b64 = data_url.split(",", 1)
                    b64_bytes = len(b64.encode("ascii"))
                    quality = 40
                    fmt = "jpeg"
                    scale = max(scale * 0.75, 0.5)
            except Exception:  # noqa: BLE001
                pass

        if b64_bytes > max_bytes:
            downgrade_note = (
                f"\n\n⚠️ 截图仍超过 {max_bytes} bytes (实际 {b64_bytes}),"
                "请考虑:缩小 scale / 只截可视区域 (full_page=false)"
                " / 改用 selector 限定范围 / 改用 JPEG + quality=40"
            )

        try:
            base64.b64decode(b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                content=f"截图 base64 解码失败: {exc}",
                is_error=True,
                details={"error": "bad_base64"},
            )

        content_summary = (
            f"截图完成 ({fmt.upper()}, quality={quality}, scale={scale}, "
            f"full_page={full_page}, selector={selector or 'document.body'})\n"
            f"base64 长度: {b64_bytes} bytes\n"
            f"页面尺寸: {result.data.get('width', '?')}x{result.data.get('height', '?')}\n"
            f"data URL 前 80 字符: {data_url[:80]}..."
            f"{downgrade_note}"
        )

        return ToolResult(
            content=content_summary,
            details={
                "format": fmt,
                "quality": quality,
                "full_page": full_page,
                "scale": scale,
                "selector": selector,
                "b64_bytes": b64_bytes,
                "truncated": truncated,
                "data_url": data_url,
                "width": result.data.get("width"),
                "height": result.data.get("height"),
            },
        )


def _build_screenshot_js(
    full_page: bool,
    fmt: str,
    quality: int,
    scale: float,
    selector: str | None,
) -> str:
    """构造截图 JS — 调用 content.js 注入的 window.__pyagent_screenshotHtml。"""
    opts = {
        "fullPage": full_page,
        "format": fmt,
        "quality": quality,
        "scale": scale,
    }
    if selector:
        opts["selector"] = selector
    opts_json = json.dumps(opts, ensure_ascii=False)
    # 双层 async 包裹 — html2canvas 内部是 Promise 链,executeScript 等待
    # outermost promise resolve 即可。
    return f"""
    (async () => {{
        try {{
            if (typeof window.__pyagent_screenshotHtml !== 'function') {{
                return {{ __error__: 'window.__pyagent_screenshotHtml 不存在 — ' +
                    'Chrome 扩展 content.js 还未注入 html2canvas。' }};
            }}
            const result = await window.__pyagent_screenshotHtml({opts_json});
            if (!result || !result.data_url) {{
                return {{ __error__: 'screenshot 返回空 result' }};
            }}
            return result;
        }} catch (e) {{
            return {{ __error__: String(e && e.message || e) }};
        }}
    }})()
    """
