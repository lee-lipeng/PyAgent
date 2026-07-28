"""browser_execute_js在当前 tab 中执行任意 JS 表达式,返回求值结果。

可选 monitor 参数,GenericAgent 风格的"前后变化检测"

monitor 取值:
- "auto": 默认,轻量级富反馈 — 自动采集 URL/title/scroll 变化 + 页面文本 diff 摘要。
  无需 LLM 额外调用就能感知页面变化(借鉴 GenericAgent execute_js_rich)。
- "off": 不监控,只返回 JS 求值结果。
- "dom": 执行前采集精简 HTML 快照,执行后再次采集,输出 find_changed_elements。
  适合 "我点击了某按钮,页面哪些地方变了?"
- "network": 启动 window.__pyagent_api_mon,执行用户代码后返回捕获的 fetch / XHR 请求。
  适合 "我滚动到底部触发了哪些 API?"
- "full": dom + network 同时。

monitor 字段输出 details.monitor,供 LLM 在 next turn 引用。

API 监控独立模式
若 LLM 不想跑自定义 JS,只想"等几秒,收集所有 fetch / XHR",
可以走专门的 mode="api_capture" 入口(其实是 monitor="network" + code="await new Promise(r=>setTimeout(r,1500))" 的快捷方式)。
"""

from __future__ import annotations

import contextlib
import json
from typing import Literal

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.browser._htmlopt import (
    JS_API_MONITOR_CLEAR,
    JS_API_MONITOR_QUERY,
    JS_API_MONITOR_START,
    JS_CAPTURE_TRANSIENTS,
    JS_FIND_CHANGED_ELEMENTS,
    JS_LIGHT_SNAPSHOT,
    JS_OPTHTML,
    JS_OPTIMIZE_FOR_TOKENS,
    wrap_iife,
)
from pyagent.tools.decorators import tool


class BrowserExecuteJsArgs(BaseModel):
    code: str = Field(description="要执行的 JS 表达式。推荐 IIFE 形式:(() => { ... })()")
    timeout: float = Field(
        default=15.0,
        ge=1,
        le=120,
        description="超时秒数 (默认 15, 范围 1-120)",
    )
    tab_id: str | None = Field(default=None, description="目标 tab id,空则用默认 tab")
    monitor: Literal["auto", "off", "dom", "network", "full"] = Field(
        default="auto",
        description=(
            "监控模式:"
            "auto=默认,轻量富反馈(URL/title/scroll 变化 + 文本 diff 摘要);"
            "off=不监控,只返回 JS 结果;"
            "dom=前后 DOM diff(返回 changed_count + top_change);"
            "network=捕获 fetch/XHR 请求;"
            "full=dom+network 同时。"
        ),
    )


@tool(
    "browser_execute_js",
    description=(
        "在当前页面执行任意 JS 表达式并返回结果。"
        "可以用此工具读写 DOM、调用 fetch、操作 cookies 等一切浏览器内可做之事。"
        "返回值为 JSON 字符串,复杂对象会被自动序列化。\n"
        "默认 monitor=auto:自动采集 URL/title/scroll 变化 + 页面文本 diff 摘要,"
        "无需额外调用就能感知页面变化(类似 GenericAgent execute_js_rich)。\n"
        "monitor=dom 时返回 changed_count + top_change (前后 HTML diff);"
        "monitor=network 时返回所有 fetch/XHR 请求 (含 method/url/status/body);"
        "monitor=full 同时返回两者。\n"
        "常用模式: monitor=network + code='await new Promise(r=>setTimeout(r,2000))'"
        "用于采集滚动加载的 API 调用。"
    ),
)
class BrowserExecuteJsTool(Tool):
    """执行 JS 表达式 (可选 DOM / 网络监控)。"""

    parameters_model = BrowserExecuteJsArgs
    execution_mode = "sequential"

    async def execute(self, tool_call_id, args, signal=None, on_update=None) -> ToolResult:
        from pyagent.tools.browser.tool_helpers import (
            async_ensure_bridge,
        )

        bridge, err = await async_ensure_bridge()
        if err is not None:
            return err

        if signal is not None and signal.is_set():
            return ToolResult(
                content="用户已中止执行",
                is_error=True,
                details={"error": "aborted"},
            )

        monitor = args.get("monitor", "auto")
        code = args["code"]

        # 装配最终 JS: 监控分两阶段 (baseline + run + after)
        # 1. 启监控 + 收集 baseline
        # 2. 跑用户 JS + 收 after + diff。
        # monitor=network 用单次更简洁(启动 → 用户代码 → 查询)。

        if monitor == "off":
            return await _run_user_code(bridge, code, args)

        if monitor == "auto":
            return await _run_with_auto_feedback(bridge, code, args)

        if monitor == "network":
            return await _run_with_network_monitor(bridge, code, args)

        if monitor in ("dom", "full"):
            return await _run_with_dom_monitor(bridge, code, args, with_network=(monitor == "full"))

        # 不可达
        return await _run_user_code(bridge, code, args)


async def _run_user_code(bridge, code: str, args: dict) -> ToolResult:
    """直接跑用户代码。"""
    try:
        result = await bridge.execute_js(
            code=code,
            tab_id=args.get("tab_id"),
            timeout=args["timeout"],
        )
    except Exception as exc:
        return _translate(exc)

    data = result.data
    content = _format_value(data)
    return ToolResult(
        content=content,
        details={
            "code_length": len(code),
            "tab_id": args.get("tab_id") or bridge.default_tab_id,
            "new_tabs": result.new_tabs,
            "monitor": "off",
        },
    )


async def _run_with_auto_feedback(bridge, code: str, args: dict) -> ToolResult:
    """auto 模式:轻量级富反馈(借鉴 GenericAgent execute_js_rich)。

    在用户代码前后各采集一次 transients + lightSnapshot,
    自动报告:
    - URL/title 变化(检测 SPA 跳转)
    - scroll 变化(检测滚动加载)
    - 页面文本 diff 摘要(检测内容变化)
    - newTabs(检测新开 tab)

    全部在一次 execute_js 调用内完成,不增加 WS 往返。
    """
    combined = wrap_iife(
        JS_CAPTURE_TRANSIENTS,
        JS_LIGHT_SNAPSHOT,
        expression=f"""
            (async () => {{
                const __before_t = captureTransients();
                const __before_s = lightSnapshot();
                let __user_result = undefined;
                let __user_error = null;
                try {{
                    __user_result = await (async () => {{ {code} }})();
                }} catch (e) {{
                    __user_error = String(e && e.message || e);
                }}
                const __after_t = captureTransients();
                const __after_s = lightSnapshot();
                return {{
                    user_result: __user_result,
                    user_error: __user_error,
                    before: __before_t,
                    after: __after_t,
                    before_snapshot: __before_s,
                    after_snapshot: __after_s,
                }};
            }})()
        """,
    )
    try:
        result = await bridge.execute_js(
            code=combined,
            tab_id=args.get("tab_id"),
            timeout=args["timeout"],
        )
    except Exception as exc:
        return _translate(exc)

    data = result.data if isinstance(result.data, dict) else {}
    user_result = data.get("user_result")
    user_error = data.get("user_error")
    before_t = data.get("before") or {}
    after_t = data.get("after") or {}
    before_s = data.get("before_snapshot") or {}
    after_s = data.get("after_snapshot") or {}

    # 构建 content
    sections: list[str] = []

    # 1. 用户代码返回值
    if user_error is not None:
        sections.append(f"⚠️ 用户代码抛错: {user_error}")
    elif user_result is not None:
        sections.append("--- JS 返回值 ---")
        sections.append(_format_value(user_result))

    # 2. URL/title 变化
    url_changed = before_t.get("url") != after_t.get("url")
    title_changed = before_t.get("title") != after_t.get("title")
    if url_changed or title_changed:
        sections.append("--- 页面导航变化 ---")
        if url_changed:
            sections.append(f"  URL: {before_t.get('url', '?')[:120]} → {after_t.get('url', '?')[:120]}")
        if title_changed:
            sections.append(f"  title: {before_t.get('title', '?')[:80]} → {after_t.get('title', '?')[:80]}")

    # 3. scroll 变化
    scroll_delta = after_t.get("scrollY", 0) - before_t.get("scrollY", 0)
    if abs(scroll_delta) > 50:
        sections.append(
            f"--- 滚动变化: Y {before_t.get('scrollY', 0)} → {after_t.get('scrollY', 0)} (Δ{scroll_delta:+d}) ---"
        )

    # 4. 文本 diff 摘要
    before_chars = before_s.get("chars", 0)
    after_chars = after_s.get("chars", 0)
    char_delta = after_chars - before_chars
    if abs(char_delta) > 100 or url_changed:
        sections.append(f"--- 页面文本变化: {before_chars} → {after_chars} 字符 (Δ{char_delta:+d}) ---")
        # 如果文本变了,展示 after 的前 500 字符作为摘要
        if abs(char_delta) > 100 and not url_changed:
            after_head = (after_s.get("head") or "")[:500]
            if after_head:
                sections.append(f"  当前页面文本摘要: {after_head}")

    # 5. activeElement 变化
    before_active = before_t.get("activeElement", "")
    after_active = after_t.get("activeElement", "")
    if before_active != after_active and after_active:
        sections.append(f"  焦点元素: {before_active or '(无)'} → {after_active}")

    # 6. newTabs
    if result.new_tabs:
        sections.append(f"--- 新开 tab: {len(result.new_tabs)} 个 ---")
        for nt in result.new_tabs[:5]:
            sections.append(f"  {nt.get('url', '?')[:120]}")

    # 如果没有任何变化,给一个简洁的"无变化"提示
    if not sections:
        sections.append("(页面无变化)")

    content = "\n".join(sections)
    return ToolResult(
        content=content,
        details={
            "code_length": len(code),
            "tab_id": args.get("tab_id") or bridge.default_tab_id,
            "new_tabs": result.new_tabs,
            "monitor": "auto",
            "url_changed": url_changed,
            "title_changed": title_changed,
            "scroll_delta": scroll_delta,
            "text_delta": char_delta,
            "user_error": user_error,
        },
    )


async def _run_with_network_monitor(bridge, code: str, args: dict) -> ToolResult:
    """启动 API 监控 → 跑用户代码 → 查询捕获请求。"""
    settings = _get_settings()
    timeout = min(args["timeout"], settings.network_capture_timeout)
    try:
        # 用户代码与监控共存: 用一次 execute_js 把启动监控 + 用户代码 + 查询打包
        combined = wrap_iife(
            JS_API_MONITOR_START,
            JS_API_MONITOR_QUERY,
            expression=f"""
                (async () => {{
                    startApiMonitor({{maxBody: 4096, captureBodies: true}});
                    try {{
                        await (async () => {{ {code} }})();
                    }} catch (e) {{
                        return {{ __user_error__: String(e && e.message || e),
                                 __query__: queryApiMonitor() }};
                    }}
                    return {{ __user_error__: null, __query__: queryApiMonitor() }};
                }})()
            """,
        )
        result = await bridge.execute_js(
            code=combined,
            tab_id=args.get("tab_id"),
            timeout=timeout,
        )
    except Exception as exc:
        return _translate(exc)

    data = result.data if isinstance(result.data, dict) else {}
    user_error = data.get("__user_error__")
    monitor_data = data.get("__query__") or {}
    # 截图收尾: 清监控(避免后续扫描带回无意义 fetch)
    with contextlib.suppress(Exception):
        await bridge.execute_js(
            code=wrap_iife(JS_API_MONITOR_CLEAR, expression="clearApiMonitor()"),
            tab_id=args.get("tab_id"),
            timeout=5.0,
        )

    text = _format_network_report(monitor_data)
    if user_error:
        text = f"⚠️ 用户代码抛错: {user_error}\n\n{text}"

    return ToolResult(
        content=text,
        details={
            "code_length": len(code),
            "tab_id": args.get("tab_id") or bridge.default_tab_id,
            "new_tabs": result.new_tabs,
            "monitor": "network",
            "request_count": monitor_data.get("count", 0),
        },
    )


async def _run_with_dom_monitor(bridge, code: str, args: dict, with_network: bool) -> ToolResult:
    """前后 DOM diff (可选叠加 network monitor)。"""
    # 阶段 1: 收集 baseline HTML (精简)
    baseline_js = wrap_iife(
        JS_OPTHTML,
        JS_OPTIMIZE_FOR_TOKENS,
        expression="optimizeHtmlForTokens(optHTML(false) || '')",
    )
    # 阶段 2: 跑用户代码 + 收 after HTML + 收 changed + (可选)网络
    after_expr_parts = [
        "(() => {",
        "    __code_result = await (async () => { " + code + " })();",
        "    const __after = optimizeHtmlForTokens(optHTML(false) || '');",
        "    const __changed = findChangedElements(__baseline, __after);",
        "    let __net = null;",
    ]
    if with_network:
        after_expr_parts.extend(
            [
                "    if (window.__pyagent_api_mon) {",
                "        __net = queryApiMonitor();",
                "        clearApiMonitor();",
                "    }",
            ]
        )
    after_expr_parts.extend(
        [
            "    return {",
            "        __user_error__: null,",
            "        __baseline_chars: __baseline.length,",
            "        __after_chars: __after.length,",
            "        __changed: __changed,",
            "        __user_result: __code_result,",
            "        __net: __net,",
            "    };",
            "})()",
        ]
    )
    after_js = wrap_iife(
        JS_OPTHTML,
        JS_OPTIMIZE_FOR_TOKENS,
        JS_FIND_CHANGED_ELEMENTS,
        JS_API_MONITOR_START,
        JS_API_MONITOR_QUERY,
        JS_API_MONITOR_CLEAR,
        expression="\n".join(after_expr_parts),
    )

    try:
        baseline_result = await bridge.execute_js(
            code=baseline_js,
            tab_id=args.get("tab_id"),
            timeout=10.0,
        )
        baseline_html = baseline_result.data if isinstance(baseline_result.data, str) else ""
        # 把 baseline HTML 嵌入 after_js 的 __baseline 变量
        after_js_with_baseline = after_js.replace(
            "findChangedElements(__baseline, __after)",
            f"findChangedElements({json.dumps(baseline_html[:200_000])}, __after)",
        )
        # 顺便把"全局 __baseline"改成局部常量(用变量名混淆避免冲突)
        after_js_final = after_js_with_baseline.replace(
            "const __after = optimizeHtmlForTokens(optHTML(false) || '');",
            "const __baseline = "
            + json.dumps(baseline_html[:200_000])
            + ";\n    const __after = optimizeHtmlForTokens(optHTML(false) || '');",
        )
        if with_network:
            # 在 baseline_js 之前先启监控
            start_js = wrap_iife(
                JS_API_MONITOR_START,
                expression="startApiMonitor({maxBody: 4096, captureBodies: true})",
            )
            with contextlib.suppress(Exception):
                await bridge.execute_js(code=start_js, tab_id=args.get("tab_id"), timeout=5.0)
        result = await bridge.execute_js(
            code=after_js_final,
            tab_id=args.get("tab_id"),
            timeout=args["timeout"],
        )
    except Exception as exc:
        return _translate(exc)

    data = result.data if isinstance(result.data, dict) else {}
    user_error = data.get("__user_error__")
    changed = data.get("__changed") or {}
    user_result = data.get("__user_result")
    net_data = data.get("__net")
    base_chars = data.get("__baseline_chars", 0)
    after_chars = data.get("__after_chars", 0)

    sections: list[str] = []
    sections.append(
        f"--- DOM diff (baseline {base_chars} → after {after_chars} chars) ---\n"
        f"changed_count: {changed.get('changed', 0)}"
    )
    top_change = changed.get("top_change", "")
    if top_change:
        sections.append(f"\ntop_change: {top_change[:1500]}")
    if user_result is not None:
        sections.append("\n--- 用户代码返回值 ---\n" + _format_value(user_result))
    if net_data is not None:
        sections.append("\n" + _format_network_report(net_data))
    if user_error:
        sections.insert(0, f"⚠️ 用户代码抛错: {user_error}")

    return ToolResult(
        content="\n".join(sections),
        details={
            "code_length": len(code),
            "tab_id": args.get("tab_id") or bridge.default_tab_id,
            "new_tabs": result.new_tabs,
            "monitor": "full" if with_network else "dom",
            "baseline_chars": base_chars,
            "after_chars": after_chars,
            "changed_count": changed.get("changed", 0),
            "request_count": (net_data or {}).get("count", 0),
        },
    )


def _format_value(data) -> str:
    """把 JS 返回值整理成可读字符串。"""
    if data is None:
        return "(JS 返回 undefined / null)"
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(data)


def _format_network_report(monitor_data: dict) -> str:
    """把 API monitor 的查询结果整理成可读报告。"""
    if not monitor_data:
        return "(无网络捕获数据)"
    if not monitor_data.get("installed"):
        return "(监控未安装)"
    count = monitor_data.get("count", 0)
    status_counts = monitor_data.get("statusCounts", {})
    total_bytes = monitor_data.get("totalBytes", 0)
    reqs = monitor_data.get("requests", [])

    lines = [f"--- API 捕获 ({count} 个请求, 共 {total_bytes:,} 字节) ---"]
    if status_counts:
        lines.append(
            "状态码分布: " + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items(), key=lambda kv: -kv[1])[:10])
        )
    for i, r in enumerate(reqs[:50], 1):
        method = r.get("method", "?")
        url = r.get("url", "?")
        status = r.get("status", 0)
        kind = r.get("kind", "?")
        body_len = len(r.get("responseBody") or "")
        flag = "✅" if status and status < 400 else ("❌" if r.get("error") else "⏳")
        lines.append(f"  [{i:3d}] {flag} {method:6s} {status:>4} {kind:5s} ({body_len:>6}B) {url[:120]}")
        if r.get("error"):
            lines.append(f"        error: {r['error'][:200]}")
        body = r.get("responseBody")
        if body and len(body) < 500:
            lines.append(f"        body: {body[:500]}")
    if count > 50:
        lines.append(f"... 另有 {count - 50} 个请求未列出")
    return "\n".join(lines)


def _translate(exc: Exception) -> ToolResult:
    from pyagent.tools.browser.tool_helpers import translate_exception

    return translate_exception(exc)


def _get_settings():
    from pyagent.tools.browser.tool_helpers import get_settings

    return get_settings()


__all__ = ["BrowserExecuteJsTool", "BrowserExecuteJsArgs"]
