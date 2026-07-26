"""browser_scan:获取当前页面的可读快照。

支持 4 种返回格式 + 3 档 HTML 精简度

mode (返回格式):
- text: 纯文本,适合阅读
- html: 简化 HTML (保留标签结构,去 script/style)
- snapshot: 结构化 DOM 数组,适合精准提取
- lists: 列出页面里"看着像列表"的容器 + selector (不返回 DOM 内容)

simplify (HTML/text 模式下的页面精简度,GenericAgent 启发式):
- none: 不做精简,直接 rawMode 输出 (适合精准提取某区域)
- light: 原 PyAgent 实现 — 删 script/style/comment,保留标签
- full: GenericAgent optHTML + iframe/Shadow DOM 处理 + overlay 删除 + 链式压缩

find_lists: 在 light / full 同时返回候选列表,LLM 可用其 selector
进一步精准提取,避免再次扫描整页。

cutlist: 启用 GenericAgent cutlist — 对检测到的列表保留前 3 个 + instruction 命中
的 6 个,其余替换为 [FAKE ELEMENT] N more items hidden 提示。可节省大量 token。

instruction: 配合 cutlist 使用,优先保留文本里含此关键字的列表项。
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.browser._htmlopt import (
    JS_APPLY_CUTLIST,
    JS_FINDMAINLIST,
    JS_OPTHTML,
    JS_OPTIMIZE_FOR_TOKENS,
    JS_SMART_TRUNCATE,
    wrap_iife,
)
from pyagent.tools.decorators import tool


class BrowserScanArgs(BaseModel):
    mode: Literal["text", "html", "snapshot", "lists"] = Field(
        default="text",
        description=(
            "返回格式:text=纯文本 / html=简化 HTML / snapshot=结构化 DOM / "
            "lists=页面里'看着像列表'的容器候选(不返回内容,只返回 selector)"
        ),
    )
    simplify: Literal["none", "light", "full"] = Field(
        default="full",
        description=(
            "页面精简度 (text/html 模式生效):"
            "none=不动 DOM 直接 raw 模式;light=删 script/style/comment 标签;"
            "full=GenericAgent optHTML 处理(去 iframe/overlay/链式压缩,token 节省 60%+)。"
            "snapshot 模式固定 light(仅删 script/style,保留结构)。"
        ),
    )
    selector: str | None = Field(
        default=None,
        description="CSS 选择器,只扫描匹配元素 (为空则整页)",
    )
    max_chars: int = Field(
        default=20_000,
        ge=1,
        le=200_000,
        description=(
            "返回文本最大字符数 (默认 20000, 范围 1-200000)。"
            "HTML 模式下超过会用 smart_truncate 按子树比例截断(保护 FAKE ELEMENT 提示)。"
        ),
    )
    find_lists: bool = Field(
        default=False,
        description="是否同时返回 findMainList 候选列表 (含 selector + score),LLM 可用 selector 精准再扫。",
    )
    cutlist: bool = Field(
        default=False,
        description=(
            "启用 GenericAgent cutlist:对 findMainList 命中的列表,保留前 3 项 + "
            "instruction 命中项,其余替换为 [FAKE ELEMENT] 提示。大量节省 token。"
        ),
    )
    instruction: str = Field(
        default="",
        description="cutlist 优先级关键字:列表项文本含此关键字的会被保留(最多 6 个)。cutlist=False 时此参数无效。",
    )


@tool(
    "browser_scan",
    description=(
        "获取当前活跃 tab 页面快照。\n"
        "mode=text: 返回 innerText (适合阅读);\n"
        "mode=html: 返回简化 HTML (保留标签结构,去 script/style);\n"
        "mode=snapshot: 返回结构化 DOM 数组 (元素 + 子元素 + 文本);\n"
        "mode=lists: 只返回 findMainList 候选 (selector + score + firstItemPreview),不返回内容。\n"
        "simplify=none|light|full 控制精简度 (full=GenericAgent optHTML)。\n"
        "find_lists=true 时在 text/html/snapshot 同时返回列表候选。\n"
        "cutlist=true + instruction=关键词 时启用列表压缩(节省 token)。\n"
        "可指定 CSS selector 限定范围。"
    ),
)
class BrowserScanTool(Tool):
    """扫描页面内容。"""

    parameters_model = BrowserScanArgs
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
        # 截断上限保护 (即便 Pydantic le 仍做运行时 clamp)
        max_chars = min(args["max_chars"], settings.scan_max_chars)
        selector = args.get("selector")
        mode = args["mode"]
        simplify = args.get("simplify", "full")
        find_lists = bool(args.get("find_lists"))
        cutlist = bool(args.get("cutlist"))
        instruction = args.get("instruction", "") or ""

        # 构造页面内 JS
        js = _build_scan_js(
            selector=selector,
            mode=mode,
            simplify=simplify,
            max_chars=max_chars,
            find_lists=find_lists,
            cutlist=cutlist,
            instruction=instruction,
        )

        try:
            result = await bridge.execute_js(js, timeout=settings.default_timeout)
        except Exception as exc:  # noqa: BLE001
            return translate_exception(exc)

        # data 可能是 dict (snapshot/lists) 或 str (text/html)
        data = result.data
        lists_section: list | None = None
        main_data = None
        if isinstance(data, dict):
            if data.get("__error__"):
                return ToolResult(
                    content=f"扫描失败: {data['__error__']}",
                    is_error=True,
                    details={"error": "selector_not_found", "selector": selector},
                )
            # 分离 lists + 主要输出
            if "main" in data:
                main_data = data["main"]
                lists_section = data.get("lists")
            else:
                main_data = data
            if isinstance(main_data, dict):
                text = json.dumps(main_data, ensure_ascii=False, indent=2)
            elif isinstance(main_data, str):
                text = main_data
            else:
                text = str(main_data) if main_data is not None else ""
        elif isinstance(data, str):
            text = data
        else:
            text = str(data) if data is not None else ""

        truncated = len(text) > max_chars
        if truncated:
            head = text[: max_chars // 2]
            tail = text[-(max_chars // 2) :]
            text = f"{head}\n\n... (已截断中间 {len(text) - max_chars} 字符,原长度 {len(text)}) ...\n\n{tail}"

        # 拼接 find_lists 输出
        if lists_section is not None and isinstance(lists_section, list) and lists_section:
            lines = [f"\n\n--- 候选列表 (findMainList, {len(lists_section)} 个) ---"]
            for i, lst in enumerate(lists_section[:10], 1):
                sel = lst.get("selector", "?")
                cnt = lst.get("itemCount", "?")
                score = lst.get("score", "?")
                ctag = lst.get("containerTag", "?")
                preview = (lst.get("firstItemPreview") or "")[:120].replace("\n", " ")
                lines.append(f"  [{i}] selector=`{sel}`  itemCount={cnt}  score={score}  tag={ctag}")
                if preview:
                    lines.append(f"      preview: {preview}...")
            text += "\n".join(lines)

        return ToolResult(
            content=text,
            details={
                "mode": mode,
                "simplify": simplify,
                "selector": selector,
                "chars": len(text),
                "truncated": truncated,
                "find_lists": find_lists,
                "lists_count": len(lists_section) if isinstance(lists_section, list) else 0,
                "cutlist": cutlist,
            },
        )


def _build_scan_js(
    selector: str | None,
    mode: str,
    simplify: str,
    max_chars: int,
    find_lists: bool,
    cutlist: bool,
    instruction: str,
) -> str:
    """生成页面内执行的扫描 JS。

    - mode=text + simplify=full → 用 optHTML(true) 提取可见文本
    - mode=html + simplify=full → optHTML(false) → optimizeHtmlForTokens → smartTruncate
    - mode=html + simplify=light → 旧实现(只删 script/style/comment)
    - mode=snapshot → 始终 light 模式(保留 DOM 结构)
    - mode=lists → 只跑 findMainList,不返回 DOM
    - find_lists=true → 在 text/html/snapshot 输出末尾附加 lists 数组
    - cutlist=true → 在 simplify=full 之后对 lists 应用 applyCutlist
    """
    target_expr = f"document.querySelector({json.dumps(selector)})" if selector else "document.body"

    # ===== mode=lists (专用,不走 optHTML) =====
    if mode == "lists":
        return wrap_iife(
            JS_FINDMAINLIST,
            expression=f"findMainList({target_expr})",
        )

    # ===== snapshot 模式: 始终 light (只删 script/style) =====
    if mode == "snapshot":
        return _build_snapshot_js(selector, max_chars)

    # ===== text 模式 =====
    if mode == "text":
        if simplify == "full":
            # optHTML(true) 返回的是 textContent,带表单/链接标识
            return wrap_iife(
                JS_OPTHTML,
                expression="optHTML(true) || ''",
            )
        # light / none → 原 text 模式
        return _build_text_js(selector, max_chars)

    # ===== html 模式 =====
    if mode == "html":
        if simplify == "full":
            return _build_html_full_js(
                selector=selector,
                max_chars=max_chars,
                find_lists=find_lists,
                cutlist=cutlist,
                instruction=instruction,
            )
        if simplify == "light":
            return _build_html_light_js(selector, max_chars)
        # simplify=none
        return _build_html_raw_js(selector, max_chars)

    # 不可达
    return _build_text_js(selector, max_chars)


# ------------------------------------------------------------------
# text 模式
# ------------------------------------------------------------------


def _build_text_js(selector: str | None, max_chars: int) -> str:
    """light/none 文本模式 — 与 PyAgent 原实现兼容。"""
    target_expr = f"document.querySelector({json.dumps(selector)})" if selector else "document.body"
    sel_json = json.dumps(selector)
    return f"""
    (() => {{
        const TARGET = {target_expr};
        const SELECTOR = {sel_json};
        const MAX_CHARS = {max_chars};
        const el = TARGET;
        if (!el) return {{ __error__: 'selector 未匹配任何元素: ' + (SELECTOR || 'document.body') }};
        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        return text.slice(0, MAX_CHARS);
    }})()
    """


# ------------------------------------------------------------------
# html 模式 (light / none)
# ------------------------------------------------------------------


def _build_html_light_js(selector: str | None, max_chars: int) -> str:
    """light HTML: 删除 script/style/comment,保留标签结构。"""
    target_expr = f"document.querySelector({json.dumps(selector)})" if selector else "document.body"
    sel_json = json.dumps(selector)
    return f"""
    (() => {{
        const TARGET = {target_expr};
        const SELECTOR = {sel_json};
        const MAX_CHARS = {max_chars};
        const el = TARGET;
        if (!el) return {{ __error__: 'selector 未匹配任何元素: ' + (SELECTOR || 'document.body') }};
        const clone = el.cloneNode(true);
        clone.querySelectorAll('script, style, noscript, iframe').forEach(n => n.remove());
        const walker = document.createTreeWalker(clone, NodeFilter.SHOW_COMMENT);
        const comments = [];
        while (walker.nextNode()) comments.push(walker.currentNode);
        comments.forEach(c => c.remove());
        let html = clone.innerHTML;
        if (html.length > MAX_CHARS) html = html.slice(0, MAX_CHARS);
        return html;
    }})()
    """


def _build_html_raw_js(selector: str | None, max_chars: int) -> str:
    """none HTML: 直接 outerHTML (不做任何简化)。"""
    target_expr = f"document.querySelector({json.dumps(selector)})" if selector else "document.body"
    sel_json = json.dumps(selector)
    return f"""
    (() => {{
        const TARGET = {target_expr};
        const SELECTOR = {sel_json};
        const MAX_CHARS = {max_chars};
        const el = TARGET;
        if (!el) return {{ __error__: 'selector 未匹配任何元素: ' + (SELECTOR || 'document.body') }};
        return el.outerHTML.slice(0, MAX_CHARS);
    }})()
    """


# ------------------------------------------------------------------
# html 模式 (full — optHTML + optimize + smartTruncate + cutlist)
# ------------------------------------------------------------------


def _build_html_full_js(
    selector: str | None,
    max_chars: int,
    find_lists: bool,
    cutlist: bool,
    instruction: str,
) -> str:
    """full HTML: GenericAgent 风格的精简管线。

    管线:
        1. optHTML(false) → DOM 精简 (去 iframe/overlay/链式压缩)
        2. optimizeHtmlForTokens → 属性瘦身
        3. (cutlist) applyCutlist → 列表项压缩
        4. smartTruncate → 字符预算
        5. (find_lists) findMainList → 附加到返回 dict
    """
    sel_json = json.dumps(selector)
    instruction_json = json.dumps(instruction)

    # 组合 JS 段
    blocks = [JS_OPTHTML, JS_OPTIMIZE_FOR_TOKENS, JS_SMART_TRUNCATE]
    if cutlist:
        blocks.append(JS_FINDMAINLIST)
        blocks.append(JS_APPLY_CUTLIST)

    return f"""
    (() => {{
        const TARGET_SELECTOR = {sel_json};
        const MAX_CHARS = {max_chars};
        const FIND_LISTS = {json.dumps(find_lists)};
        const CUTLIST = {json.dumps(cutlist)};
        const INSTRUCTION = {instruction_json};
        // selector 限定时直接拿 outerHTML (不走 optHTML — optHTML 写死 document.body)
        // 整页模式才走完整管线。
        let __html;
        if (TARGET_SELECTOR) {{
            const el = document.querySelector(TARGET_SELECTOR);
            if (!el) return {{ __error__: 'selector 未匹配任何元素: ' + TARGET_SELECTOR }};
            __html = el.outerHTML;
            __html = optimizeHtmlForTokens(__html);
            if (__html.length > MAX_CHARS) __html = smartTruncate(__html, MAX_CHARS);
        }} else {{
            __html = optHTML(false) || '';
            __html = optimizeHtmlForTokens(__html);
            if (CUTLIST) {{
                const __lists = findMainList(document.body);
                const __result = applyCutlist(__html, __lists, INSTRUCTION);
                __html = __result.html;
            }}
            if (__html.length > MAX_CHARS) __html = smartTruncate(__html, MAX_CHARS);
        }}
        const __out = {{ main: __html, chars: __html.length }};
        if (FIND_LISTS || CUTLIST) {{
            try {{ __out.lists = findMainList(document.body); }} catch (e) {{ __out.lists = []; }}
        }}
        return __out;
    }})()
    """


# ------------------------------------------------------------------
# snapshot 模式
# ------------------------------------------------------------------


def _build_snapshot_js(selector: str | None, max_chars: int) -> str:
    """snapshot 模式: 递归抽 tag/attrs/text/children,固定 light(只删 script/style)。"""
    target_expr = f"document.querySelector({json.dumps(selector)})" if selector else "document.body"
    sel_json = json.dumps(selector)
    return f"""
    (() => {{
        const TARGET = {target_expr};
        const SELECTOR = {sel_json};
        const MAX_CHARS = {max_chars};
        const el = TARGET;
        if (!el) return {{ __error__: 'selector 未匹配任何元素: ' + (SELECTOR || 'document.body') }};
        // 浅 clone 然后删 script/style/noscript/iframe,避免 snapshot 渗入噪音
        const clone = el.cloneNode(true);
        clone.querySelectorAll('script, style, noscript, iframe').forEach(n => n.remove());
        const MAX_DEPTH = 6;
        const MAX_NODES = 500;
        let nodeCount = 0;
        function snap(node, depth) {{
            if (depth > MAX_DEPTH || nodeCount >= MAX_NODES) return null;
            nodeCount++;
            if (node.nodeType === Node.TEXT_NODE) {{
                const t = node.textContent.trim();
                return t ? {{ tag: '#text', text: t }} : null;
            }}
            if (node.nodeType !== Node.ELEMENT_NODE) return null;
            const attrs = {{}};
            const interestingAttrs = ['id', 'class', 'href', 'name', 'type',
                'role', 'aria-label', 'data-testid', 'placeholder'];
            for (const a of node.attributes) {{
                if (interestingAttrs.includes(a.name)) {{
                    attrs[a.name] = a.value.slice(0, 200);
                }}
            }}
            const children = [];
            for (const c of node.childNodes) {{
                const s = snap(c, depth + 1);
                if (s) children.push(s);
            }}
            return {{ tag: node.tagName.toLowerCase(), attrs, children }};
        }}
        const tree = snap(clone, 0);
        return {{ main: {{ root: tree, nodeCount }}, chars: JSON.stringify(tree).length }};
    }})()
    """
