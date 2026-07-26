"""浏览器端 HTML 优化算法 —— GenericAgent(https://github.com/lsdefine/GenericAgent) simphtml.py 的 JS 适配版。

设计目标
- 不引入 BeautifulSoup 等 Python 端 HTML 解析依赖。
- 所有"页面优化"操作都在**浏览器 JS 端**完成,通过 chrome.scripting.executeScript
  在 MAIN 世界执行。
- Python 端只负责:组装 JS 模板 + 解析 JS 返回结果。

主要算法
1. optHTML (GenericAgent 核心):
   - createEnhancedDOMCopy 深度拷贝页面 DOM,跳过 script/style/meta 等无意义标签。
   - 对 iframe 内容 / Shadow DOM 递归提取。
   - 移除 autofill 受保护的 input value。
   - 通过 analyzeNode 标记 K:container / K:partitionParent / K:overlayParent,
     最终删除 R:* 标记的"覆盖/广告"元素。
   - 压缩"只有一个子元素的链式容器",降低层级。
   - 返回 outerHTML。

2. findMainList:
   - 在容器内找子元素数量 >= 5 的列表候选。
   - 用 scoreContainer 评分:面积比 (40) + 数量 (40) + 均匀性 (20) + 布局 (20) + 尺寸 (15)。
   - 返回 [{containerTag, containerId, selector, itemCount, score, firstItemPreview}]。

3. smartTruncate (in-page JS):
   - 当结果超过 budget 时,按子树大小比例截断。
   - 保护 [FAKE ELEMENT] 标记不被吃掉。
   - 递归:单子元素穿透 / 多子元素按 top3 大小分摊 over。

4. optimizeHtmlForTokens (in-page):
   - 删 style 属性。
   - 把 > 30 字符的 src/href 替换成 __url__ / __img__。
   - 把 > 100 字符的 value/title/alt 截断到 50+省略号。
   - 删非白名单单属性(白名单: id/class/name/href/alt/value/type/placeholder/role 等)。

- GenericAgent 的 simphtml.py 把 JS 直接写在 Python 字符串里 (r'''...''')。
- PyAgent 抽出来独立模块,便于 browser_scan / browser_execute_js 复用,
  也方便单测直接 inject JS 到 fake bridge 看输出。
"""

from __future__ import annotations

# ------------------------------------------------------------------
# optHTML —— 整页 DOM 精简 (GenericAgent core)
# ------------------------------------------------------------------

# 注意: 全部 JS 在 MAIN 世界执行,可以用 document.* / WeakMap / CSS.escape。
JS_OPTHTML = r"""
function optHTML(textOnly) {
    const __ignoreTags = ['SCRIPT', 'STYLE', 'NOSCRIPT', 'META', 'LINK',
        'COLGROUP', 'COL', 'TEMPLATE', 'PARAM', 'SOURCE'];
    const __ignoreIds = ['ljq-ind'];
    const __nodeInfo = new WeakMap();

    function __cloneNode(src, keep) {
        if (src.nodeType === 8) return null;
        if (src.nodeType === 1 && (
            __ignoreTags.includes(src.tagName) ||
            (src.id && __ignoreIds.includes(src.id))
        )) return null;
        if (src.nodeType === 3) return src.cloneNode(false);

        const clone = src.cloneNode(false);

        // input/textarea 保留 value
        if ((src.tagName === 'INPUT' || src.tagName === 'TEXTAREA') && src.value) {
            clone.setAttribute('value', src.value);
        }
        if (src.tagName === 'INPUT' && (src.type === 'radio' || src.type === 'checkbox') && src.checked) {
            clone.setAttribute('checked', '');
        } else if (src.tagName === 'SELECT' && src.value) {
            clone.setAttribute('data-selected', src.value);
        }
        // autofill 警告 —— GenericAgent 模式
        try {
            if (src.matches && src.matches(':-webkit-autofill')) {
                clone.setAttribute('data-autofilled', 'true');
                if (!src.value) clone.setAttribute('value', '⚠️受保护的,tmwebdriver_sop的autofill字节提取');
            }
        } catch (e) { /* ignore */ }

        const childNodes = [];
        for (const child of src.childNodes) {
            const cc = __cloneNode(child, keep);
            if (cc) childNodes.push(cc);
        }

        // iframe 内容
        if (src.tagName === 'IFRAME') {
            try {
                const iDoc = src.contentDocument || (src.contentWindow && src.contentWindow.document);
                if (iDoc && iDoc.body && iDoc.body.children.length > 0) {
                    const wrapper = document.createElement('div');
                    wrapper.setAttribute('data-iframe-content', src.src || '');
                    for (const ch of iDoc.body.childNodes) {
                        const c = __cloneNode(ch, keep);
                        if (c) wrapper.appendChild(c);
                    }
                    if (wrapper.childNodes.length) childNodes.push(wrapper);
                }
            } catch (e) { /* ignore */ }
        }
        // Shadow DOM
        if (src.shadowRoot) {
            for (const sch of src.shadowRoot.childNodes) {
                const sc = __cloneNode(sch, keep);
                if (sc) childNodes.push(sc);
            }
        }

        // 计算可见性 / 面积 / z-index
        const rect = src.getBoundingClientRect();
        const style = window.getComputedStyle(src);
        const area = (style.display === 'none' || style.visibility === 'hidden' ||
            parseFloat(style.opacity) <= 0) ? 0 : rect.width * rect.height;
        const isVisible = (rect.width > 1 && rect.height > 1 &&
            style.display !== 'none' && style.visibility !== 'hidden' &&
            parseFloat(style.opacity) > 0 &&
            Math.abs(rect.left) < 5000 && Math.abs(rect.top) < 5000);
        const zIndex = style.position !== 'static' ? (parseInt(style.zIndex) || 0) : 0;
        let info = { rect, area, isVisible, zIndex,
            style: { display: style.display, visibility: style.visibility,
                opacity: style.opacity, position: style.position } };
        __nodeInfo.set(clone, info);

        const nonText = childNodes.filter(c => c.nodeType !== 3);
        const hasValidChildren = nonText.length > 0;
        if (hasValidChildren) {
            const cInfos = nonText.map(c => __nodeInfo.get(c)).filter(i => i && i.rect && i.rect.width > 0 && i.rect.height > 0);
            if (cInfos.length > 0) {
                const flowChildren = cInfos.filter(c => c.style && c.style.position !== 'fixed' && c.style.position !== 'absolute');
                if (flowChildren.length > 0) {
                    let minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity;
                    for (const c of flowChildren) {
                        minL = Math.min(minL, c.rect.left);
                        minT = Math.min(minT, c.rect.top);
                        maxR = Math.max(maxR, c.rect.right);
                        maxB = Math.max(maxB, c.rect.bottom);
                    }
                    info.rect = { left: minL, top: minT, right: maxR, bottom: maxB,
                        width: maxR - minL, height: maxB - minT };
                    info.area = info.rect.width * info.rect.height;
                }
                const maxC = cInfos.filter(i => i.isVisible).sort((a, b) => b.area - a.area)[0];
                if (maxC && maxC.area > 10000 && (!isVisible || maxC.area > info.area * 5)) info = maxC;
            }
        }
        if (src.nodeType === 1 && src.tagName === 'DIV') {
            if (!hasValidChildren && !src.textContent.trim()) return null;
        }
        if (src.getAttribute && src.getAttribute('aria-hidden') === 'true' && !isVisible) {
            return null;
        }
        if (isVisible || hasValidChildren || keep) {
            childNodes.forEach(c => clone.appendChild(c));
            return clone;
        }
        return null;
    }

    const domCopy = __cloneNode(document.body);
    if (!domCopy) return '';

    // textOnly 模式: 只输出可见文本(带表单、链接的简略标记)
    if (textOnly) {
        const blocks = new Set(['DIV', 'P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
            'LI', 'TR', 'SECTION', 'ARTICLE', 'HEADER', 'FOOTER', 'NAV',
            'BLOCKQUOTE', 'PRE', 'HR', 'BR', 'DT', 'DD', 'FIGCAPTION',
            'DETAILS', 'SUMMARY']);
        domCopy.querySelectorAll('*').forEach(el => {
            if (blocks.has(el.tagName)) el.insertAdjacentText('beforebegin', '\n');
        });
        domCopy.querySelectorAll('input:not([type=hidden]), textarea, select').forEach(el => {
            const parts = [el.tagName];
            if (el.id) parts.push('#' + el.id);
            const name = el.getAttribute('name');
            if (name) parts.push('name=' + name);
            if (el.tagName === 'INPUT') parts.push('type=' + (el.getAttribute('type') || 'text'));
            const ph = el.getAttribute('placeholder');
            if (ph) parts.push('"' + ph + '"');
            if (el.getAttribute('data-autofilled')) parts.push('autofilled');
            if (el.disabled) parts.push('disabled');
            if (el.tagName === 'SELECT' && el.getAttribute('data-selected')) parts.push('="' + el.getAttribute('data-selected') + '"');
            el.insertAdjacentText('beforebegin', '\n[' + parts.filter(Boolean).join(' ') + ']\n');
        });
        domCopy.querySelectorAll('button[disabled]').forEach(el => {
            el.insertAdjacentText('beforebegin', '[DISABLED] ');
        });
        return domCopy.textContent;
    }

    // ---- HTML 模式: 标记并删除 overlay 容器 ----
    const __hasOverlap = (items) => {
        return items.some((a, i) => items.slice(i + 1).some(b => {
            const r1 = a.rect, r2 = b.rect;
            if (!r1.width || !r2.width || !r1.height || !r2.height) return false;
            const x1 = r1.left, y1 = r1.top, x2 = r2.left, y2 = r2.top;
            const eps = 1;
            return !(x1 + r1.width <= x2 + eps || x1 >= x2 + r2.width - eps ||
                y1 + r1.height <= y2 + eps || y1 >= y2 + r2.height - eps);
        }));
    };
    const __containsButton = (el) => {
        return el.querySelector('button, input[type="button"], input[type="submit"], [role="button"]') !== null;
    };

    function __analyzeNode(node, pPathType) {
        if (node.nodeType !== 1 || !node.children.length) return;
        const nodeInfoData = __nodeInfo.get(node);
        if (!nodeInfoData || !nodeInfoData.rect) return;
        const rectn = nodeInfoData.rect;
        if (rectn.width < window.innerWidth * 0.8 && rectn.height < window.innerHeight * 0.8) return node;
        if (node.tagName === 'TABLE') return;
        const children = Array.from(node.children);
        if (children.length === 1) {
            node.dataset.mark = 'K:container';
            return __analyzeNode(children[0], pPathType);
        }
        if (children.length > 10) return;
        const cInfo = children.map(child => {
            const info = __nodeInfo.get(child) || { rect: {}, style: {} };
            return { node: child, rect: info.rect, style: info.style,
                area: info.area, zIndex: (info.zIndex || 0), isVisible: info.isVisible };
        });
        cInfo.sort((a, b) => b.area - a.area);
        const isOverlay = __hasOverlap(cInfo);
        node.dataset.mark = isOverlay ? 'K:overlayParent' : 'K:partitionParent';
        for (const child of children) {
            if (!child.dataset.mark || child.dataset.mark[0] !== 'R') __analyzeNode(child, pPathType);
        }
        // 简化版 partition: 把非主元素标记为 R:nonEssential
        if (!isOverlay) {
            const totalArea = cInfo.reduce((s, i) => s + i.area, 0);
            if (cInfo.length >= 1 && (cInfo[0].area / totalArea > 0.5) &&
                (cInfo.length === 1 || cInfo[0].area > cInfo[1].area * 2)) {
                cInfo[0].node.dataset.mark = 'K:main';
                for (let i = 1; i < cInfo.length; i++) {
                    const child = cInfo[i].node;
                    const cls = (child.getAttribute('class') || '').toLowerCase();
                    let secondary = cls.includes('nav') || cls.includes('breadcrumbs') ||
                        cls.includes('header') || cls.includes('footer') ||
                        cls.includes('sidebar') || cls.includes('table') ||
                        __containsButton(child) ||
                        (child.innerHTML.trim().replace(/\s+/g, '').length < 500);
                    if (child.style.visibility === 'hidden') secondary = false;
                    child.dataset.mark = secondary ? 'K:secondary' : 'R:nonEssential';
                }
            }
        } else {
            // overlay: 找出 z-index 最高的 visible 子元素
            const sorted = [...cInfo].sort((a, b) => b.zIndex - a.zIndex);
            if (sorted.length === 0) return;
            sorted[0].node.dataset.mark = 'K:mainInteractive';
            sorted.slice(1).forEach(e => {
                e.node.dataset.mark = (parseInt(e.zIndex) || 0) <= (parseInt(sorted[0].zIndex) || 0)
                    ? 'R:covered' : 'K:noncovered';
            });
        }
    }

    __analyzeNode(domCopy, 'main');

    // 删除所有 R:* 标记
    domCopy.querySelectorAll('[data-mark^="R:"]').forEach(el => el.parentNode && el.parentNode.removeChild(el));
    // 压缩链式单子元素
    let root = domCopy;
    while (root.children.length === 1) root = root.children[0];
    // 移除空 div
    for (let ii = 0; ii < 3; ii++) {
        root.querySelectorAll('div').forEach(div => {
            if (!div.textContent.trim() && div.children.length === 0) div.remove();
        });
    }
    root.querySelectorAll('[data-mark]').forEach(e => e.removeAttribute('data-mark'));
    root.removeAttribute('data-mark');
    // iframe → data-tag=iframe (GenericAgent 风格,让 optimize 阶段识别)
    root.querySelectorAll('iframe').forEach(f => {
        if (f.children.length) {
            const d = document.createElement('div');
            for (const a of f.attributes) d.setAttribute(a.name, a.value);
            d.setAttribute('data-tag', 'iframe');
            while (f.firstChild) d.appendChild(f.firstChild);
            f.parentNode.replaceChild(d, f);
        }
    });
    return root.outerHTML;
}
"""


# ------------------------------------------------------------------
# findMainList —— 找页面里"看着像列表"的容器 (GenericAgent findMainList)
# ------------------------------------------------------------------

JS_FINDMAINLIST = r"""
function findMainList(startElement) {
    const root = startElement || document.body;
    const MIN_CHILDREN = 8;
    const MAX_CONTAINERS = 20;

    const candidates = [];
    const allEls = root.querySelectorAll('*');
    for (const node of allEls) {
        if (node.closest('svg')) continue;
        const l1 = node.children.length;
        if (l1 < 5) continue;
        let l2 = 0;
        for (const child of node.children) l2 += child.children.length;
        const score = l1 + l2 * 0.1;
        if (score >= MIN_CHILDREN) candidates.push({ node, score });
    }
    candidates.sort((a, b) => b.score - a.score);
    const toProcess = candidates.slice(0, MAX_CONTAINERS).map(c => c.node);

    const __findTopGroups = (container, limit) => {
        const children = Array.from(container.children).filter(c => !c.closest('svg'));
        const totalChildren = children.length;
        if (totalChildren < 3) return [];
        const minGroupSize = Math.max(3, Math.floor(totalChildren * 0.2));
        const groups = [];
        const tagFreq = {}, classFreq = {}, tagMap = {}, classMap = {};
        children.forEach(child => {
            const tag = child.tagName.toLowerCase();
            if (tag === 'td') return;
            tagFreq[tag] = (tagFreq[tag] || 0) + 1;
            if (!tagMap[tag]) tagMap[tag] = [];
            tagMap[tag].push(child);
            if (child.className) {
                child.className.trim().split(/\s+/).forEach(cls => {
                    if (cls) {
                        classFreq[cls] = (classFreq[cls] || 0) + 1;
                        if (!classMap[cls]) classMap[cls] = [];
                        classMap[cls].push(child);
                    }
                });
            }
        });
        const scoreGroup = (selector, elements) => {
            const coverage = elements.length / totalChildren;
            let specificity = selector.startsWith('.')
                ? (0.6 + (selector.match(/\./g).length - 1) * 0.1)
                : (selector.includes('.')
                    ? (0.7 + (selector.match(/\./g).length) * 0.1)
                    : 0.3);
            return (coverage * 0.5) + (specificity * 0.5);
        };
        Object.keys(tagFreq).forEach(tag => {
            if (tag !== 'div' && tagFreq[tag] >= minGroupSize) {
                groups.push({ selector: tag, elements: tagMap[tag],
                    score: scoreGroup(tag, tagMap[tag]) - 0.5 });
            }
        });
        Object.keys(classFreq).forEach(cls => {
            if (classFreq[cls] >= minGroupSize) {
                const selector = '.' + CSS.escape(cls);
                groups.push({ selector, elements: classMap[cls],
                    score: scoreGroup(selector, classMap[cls]) });
            }
        });
        return groups.sort((a, b) => b.score - a.score).slice(0, limit);
    };

    const __findMatchingElements = (container, selector) => {
        try { return Array.from(container.querySelectorAll(selector)); }
        catch (e) { return []; }
    };

    const __scoreContainer = (container, items) => {
        if (!container || items.length < 3) return 0;
        const cRect = container.getBoundingClientRect();
        const cArea = cRect.width * cRect.height;
        if (cArea < 10000) return 0;
        const itemAreas = [];
        let totalItemArea = 0;
        let visibleItems = 0;
        items.forEach(item => {
            const r = item.getBoundingClientRect();
            const a = r.width * r.height;
            if (a > 0) { totalItemArea += a; itemAreas.push(a); visibleItems++; }
        });
        if (visibleItems < 3) return 0;
        totalItemArea = Math.min(totalItemArea, cArea * 0.98);
        const areaRatio = totalItemArea / cArea;
        const areaScore = 40 / (1 + Math.exp(-12 * (areaRatio - 0.4)));
        let uniformityScore = 0;
        if (itemAreas.length >= 3) {
            const mean = itemAreas.reduce((s, a) => s + a, 0) / itemAreas.length;
            const variance = itemAreas.reduce((s, a) => s + Math.pow(a - mean, 2), 0) / itemAreas.length;
            const cv = mean > 0 ? Math.sqrt(variance) / mean : 1;
            uniformityScore = 20 * Math.exp(-2.5 * cv);
        }
        const baseScore = Math.log2(visibleItems) * 5 + Math.floor(visibleItems / 5) * 0.25;
        const rawCountScore = Math.min(40, baseScore);
        const countScore = rawCountScore * Math.max(0.1, uniformityScore / 20);
        const viewportArea = window.innerWidth * window.innerHeight;
        const containerViewportRatio = cArea / viewportArea;
        const sizeScore = 2 * (1 - 1 / (1 + Math.exp(-10 * (containerViewportRatio - 0.25))));
        let layoutScore = 0;
        if (items.length >= 3) {
            const uniqueRows = new Set(items.map(it => Math.round(it.getBoundingClientRect().top / 5) * 5)).size;
            const uniqueCols = new Set(items.map(it => Math.round(it.getBoundingClientRect().left / 5) * 5)).size;
            if (uniqueRows === 1 || uniqueCols === 1) layoutScore = 20;
            else {
                const coverage = Math.min(1, items.length / (uniqueRows * uniqueCols));
                const efficiency = Math.max(0, 1 - (uniqueRows + uniqueCols) / (2 * items.length));
                layoutScore = 20 * (0.7 * coverage + 0.3 * efficiency);
            }
        }
        return countScore + areaScore + uniformityScore + layoutScore + sizeScore;
    };

    let allCandidates = [];
    for (const container of toProcess) {
        const topGroups = __findTopGroups(container, 3);
        for (const groupInfo of topGroups) {
            const items = __findMatchingElements(container, groupInfo.selector);
            if (items.length >= 5) {
                const score = __scoreContainer(container, items) + groupInfo.score;
                if (score >= 30) allCandidates.push({ container, selector: groupInfo.selector, items, score });
            }
        }
    }
    allCandidates.sort((a, b) => b.score - a.score);
    const kept = [];
    for (const cand of allCandidates) {
        let dominated = false;
        for (const k of kept) {
            if (k.container.contains(cand.container) || cand.container.contains(k.container)) {
                const kSet = new Set(k.items);
                const overlap = cand.items.filter(it => kSet.has(it)).length;
                if (overlap > cand.items.length * 0.5) { dominated = true; break; }
            }
        }
        if (!dominated) kept.push(cand);
    }

    function __describe(container, items, selector, score) {
        if (container && !container.id) {
            container.id = '_pyag_' + (window._pyagI = (window._pyagI || 0) + 1);
        }
        const result = {
            containerTag: container ? container.tagName : null,
            containerId: container ? (container.id || '') : '',
            containerClass: container ? (String(container.className || '').trim()) : '',
            itemCount: items.length,
        };
        let prefix = '';
        if (container && container.id) prefix = '#' + CSS.escape(container.id);
        if (selector) result.selector = prefix ? (prefix + ' > ' + selector) : selector;
        if (score !== undefined) result.score = Math.round(score);
        if (items.length > 0) {
            result.firstItemPreview = items[0].outerHTML.substring(0, 200);
            result.itemTags = items.slice(0, 10).map(el =>
                el.tagName + (el.className ? '.' + String(el.className).trim().split(/\s+/)[0] : ''));
        }
        return result;
    }

    return kept.map(c => __describe(c.container, c.items, c.selector, c.score));
}
"""


# ------------------------------------------------------------------
# optimizeHtmlForTokens —— 把 HTML 进一步瘦身 (GenericAgent optimize_html_for_tokens)
# ------------------------------------------------------------------

JS_OPTIMIZE_FOR_TOKENS = r"""
function optimizeHtmlForTokens(html) {
    if (typeof html !== 'string') return '';
    const __ALLOWED = new Set(['id', 'class', 'name', 'src', 'href', 'alt',
        'value', 'type', 'placeholder', 'disabled', 'checked', 'selected',
        'readonly', 'required', 'multiple', 'role', 'aria-label',
        'aria-expanded', 'aria-hidden', 'contenteditable', 'title', 'for',
        'action', 'method', 'target', 'colspan', 'rowspan', 'data-tag',
        'data-iframe-content', 'data-selected', 'data-autofilled']);

    const doc = new DOMParser().parseFromString(html, 'text/html');
    const root = doc.body;

    // SVG 全清
    root.querySelectorAll('svg').forEach(svg => {
        svg.replaceWith(doc.createTextNode('[SVG]'));
    });

    // 所有标签: 删 style 属性; 长 src/href 替换; 长 value/title/alt 截断
    root.querySelectorAll('*').forEach(tag => {
        tag.removeAttribute('style');
        if (tag.hasAttribute('src')) {
            if (tag.getAttribute('src').startsWith('data:')) tag.setAttribute('src', '__img__');
            else if (tag.getAttribute('src').length > 30) tag.setAttribute('src', '__url__');
        }
        if (tag.hasAttribute('href') && tag.getAttribute('href').length > 30) {
            tag.setAttribute('href', '__link__');
        }
        if (tag.hasAttribute('action') && tag.getAttribute('action').length > 30) {
            tag.setAttribute('action', '__url__');
        }
        for (const a of ['value', 'title', 'alt']) {
            if (tag.hasAttribute(a)) {
                const v = tag.getAttribute(a);
                if (typeof v === 'string' && v.length > 100) tag.setAttribute(a, v.slice(0, 50) + ' ...');
            }
        }
        // 非白名单属性:删
        for (const attr of Array.from(tag.attributes)) {
            if (__ALLOWED.has(attr.name)) continue;
            if (attr.name.startsWith('data-v')) tag.removeAttribute(attr.name);
            else if (attr.name.startsWith('data-') && attr.value.length > 20) {
                tag.setAttribute(attr.name, '__data__');
            } else if (!attr.name.startsWith('data-')) {
                tag.removeAttribute(attr.name);
            }
        }
    });
    return root.innerHTML;
}
"""


# ------------------------------------------------------------------
# smartTruncate —— 按子树大小比例截断 (GenericAgent smart_truncate, JS 实现)
# ------------------------------------------------------------------

JS_SMART_TRUNCATE = r"""
function smartTruncate(html, budget) {
    const CUT_THRESHOLD = 8000;
    if (!html || budget <= 0) return html || '';
    const doc = new DOMParser().parseFromString('<div id="__root__">' + html + '</div>', 'text/html');
    const root = doc.getElementById('__root__');

    function __isFakeElement(node) {
        return node && node.tagName === 'DIV' && node.textContent &&
            node.textContent.indexOf('[FAKE ELEMENT]') !== -1;
    }

    function __cut(ele, keep) {
        const s = ele.innerHTML;
        let over = s.length - keep;
        if (over <= 0) return;
        // 提取 FAKE ELEMENT 保护
        const protectedNodes = [];
        ele.querySelectorAll('div').forEach(c => {
            if (__isFakeElement(c)) { protectedNodes.push(c); c.remove(); }
        });
        const s2 = ele.innerHTML;
        over = s2.length - keep;
        if (over <= 0) { protectedNodes.forEach(p => ele.appendChild(p)); return; }
        const marker = ' [TRUNCATED ' + Math.floor(over / 1000) + 'k chars]';
        ele.innerHTML = s2.slice(0, Math.max(keep - marker.length, 0)) + marker;
        protectedNodes.forEach(p => ele.appendChild(p));
    }

    function __truncate(ele) {
        const total = ele.innerHTML.length;
        if (total <= budget) return;
        const kids = [];
        Array.from(ele.children).forEach(c => {
            if (!__isFakeElement(c)) kids.push({ node: c, len: c.innerHTML.length });
        });
        if (kids.length === 0) return;
        const selfLen = total - kids.reduce((s, k) => s + k.len, 0);
        const remaining = Math.max(budget - selfLen, 0);
        // 单子元素穿透
        if (kids.length === 1) { __truncate(kids[0].node); return; }
        const over = kids.reduce((s, k) => s + k.len, 0) - remaining;
        if (over <= 0) return;
        // top3 是否能盖 over
        const ranked = kids.map((k, i) => i).sort((a, b) => kids[b].len - kids[a].len);
        const tops = ranked.slice(0, Math.min(3, ranked.length));
        const topTotal = tops.reduce((s, i) => s + kids[i].len, 0);
        if (topTotal < over) {
            // tail-cut
            let removed = 0;
            while (kids.length > 0 && removed < over) {
                const k = kids.pop();
                removed += k.len;
                k.node.remove();
            }
            return;
        }
        // 按比例分摊
        const maxSize = kids[ranked[0]].len;
        const filtered = tops.filter(i => kids[i].len >= maxSize * 0.1);
        const finalTops = filtered.length > 0 ? filtered : tops;
        const finalTotal = finalTops.reduce((s, i) => s + kids[i].len, 0);
        finalTops.forEach(i => {
            const c = kids[i].node;
            const l = kids[i].len;
            const share = Math.floor(over * l / finalTotal);
            const newKeep = l - share;
            if (newKeep <= 0) c.remove();
            else if (newKeep > CUT_THRESHOLD) __truncate(c);
            else __cut(c, newKeep);
        });
    }
    __truncate(root);
    return root.innerHTML;
}
"""


# ------------------------------------------------------------------
# applyCutlist —— 列表项压缩 (GenericAgent cutlist 逻辑, JS 实现)
# ------------------------------------------------------------------

JS_APPLY_CUTLIST = r"""
function applyCutlist(html, lists, instruction) {
    if (!lists || lists.length === 0) return html;
    const doc = new DOMParser().parseFromString(
        '<div id="__root__">' + html + '</div>', 'text/html');
    const root = doc.getElementById('__root__');
    let totalRemoved = 0;
    for (const entry of lists) {
        const sel = entry.selector;
        if (!sel) continue;
        let items;
        try { items = Array.from(root.querySelectorAll(sel)); }
        catch (e) { continue; }
        if (items.length < 5) continue;
        // 用 entry.containerId 重置 selector scope
        // items 直接源自当前 root,已是 relative
        const totalLen = items.reduce((s, it) => s + it.innerHTML.length, 0);
        const avgLen = totalLen / items.length;
        if (avgLen < 200 || (avgLen < 700 && totalLen < 2500)) continue;
        // instruction 优先匹配前 6 个
        let keep = null;
        if (instruction && instruction.trim()) {
            const hit = items.filter(it => it.textContent && it.textContent.indexOf(instruction) !== -1);
            if (hit.length > 0) keep = hit.slice(0, 6);
        }
        if (!keep) keep = items.slice(0, 3);
        const removed = items.filter(it => keep.indexOf(it) === -1);
        const sampleTexts = [];
        removed.slice(0, 5).forEach(rm => {
            const t = (rm.textContent || '').trim().slice(0, 40);
            if (t) sampleTexts.push(t);
        });
        const hintParts = ['[FAKE ELEMENT] ' + removed.length + ' more items hidden, selector: "' + sel + '"'];
        if (sampleTexts.length > 0) hintParts.push('Hidden items: ' + sampleTexts.map(t => '"' + t + '"').join(','));
        const hintTag = doc.createElement('div');
        hintTag.textContent = hintParts.join(' ');
        if (keep.length > 0) keep[keep.length - 1].insertAdjacentElement('afterend', hintTag);
        removed.forEach(it => it.remove());
        totalRemoved += removed.length;
    }
    return { html: root.innerHTML, removed: totalRemoved };
}
"""


# ------------------------------------------------------------------
# findChangedElements —— 简化版 DOM diff (GenericAgent find_changed_elements 的 JS 适配)
# ------------------------------------------------------------------

JS_FIND_CHANGED_ELEMENTS = r"""
function findChangedElements(beforeHtml, afterHtml) {
    if (!beforeHtml || !afterHtml) return { changed: 0, top_change: '' };
    const beforeDoc = new DOMParser().parseFromString(beforeHtml, 'text/html');
    const afterDoc = new DOMParser().parseFromString(afterHtml, 'text/html');
    const beforeEls = Array.from(beforeDoc.querySelectorAll('*'));
    const afterEls = Array.from(afterDoc.querySelectorAll('*'));

    function directText(el) {
        let s = '';
        for (const t of el.childNodes) {
            if (t.nodeType === 3) s += t.textContent.trim();
        }
        return s;
    }
    function sig(el) {
        const attrs = {};
        for (const a of el.attributes || []) attrs[a.name] = a.value;
        return el.tagName + ':' + JSON.stringify(attrs) + ':' + directText(el).slice(0, 100);
    }
    const beforeSigs = {};
    beforeEls.forEach(el => {
        const s = sig(el);
        (beforeSigs[s] = beforeSigs[s] || []).push(el);
    });
    const afterSigs = {};
    afterEls.forEach(el => {
        const s = sig(el);
        (afterSigs[s] = afterSigs[s] || []).push(el);
    });
    const changed = [];
    for (const s of Object.keys(afterSigs)) {
        const b = beforeSigs[s] || [];
        const a = afterSigs[s];
        if (b.length === 0) changed.push(...a);
        else if (a.length > b.length) changed.push(...a.slice(a.length - b.length));
    }
    if (changed.length === 0 && beforeHtml !== afterHtml) {
        for (let i = 0; i < Math.min(beforeEls.length, afterEls.length); i++) {
            if (sig(beforeEls[i]) !== sig(afterEls[i])) changed.push(afterEls[i]);
        }
    }
    const result = { changed: changed.length };
    if (changed.length > 0) {
        const top = changed.reduce((a, b) => (a.outerHTML || '').length > (b.outerHTML || '').length ? a : b);
        const h = top.outerHTML || '';
        result.top_change = h.length <= 2000 ? h : h.slice(0, 2000) + '...[TRUNCATED]';
    }
    return result;
}
"""


# ------------------------------------------------------------------
# apiMonitor —— fetch + XHR 拦截,记录请求/响应 (PyAgent 专属)
# ------------------------------------------------------------------

JS_API_MONITOR_START = r"""
function startApiMonitor(opts) {
    opts = opts || {};
    if (window.__pyagent_api_mon && window.__pyagent_api_mon.installed) return;
    window.__pyagent_api_mon = {
        installed: true,
        maxBody: opts.maxBody || 4096,
        captureBodies: opts.captureBodies !== false,
        requests: [],
        _idx: 0,
    };

    const __record = (entry) => {
        window.__pyagent_api_mon.requests.push(entry);
        if (window.__pyagent_api_mon.requests.length > 200) {
            window.__pyagent_api_mon.requests.shift();
        }
    };
    const __safeStr = (v) => {
        if (v == null) return '';
        if (typeof v === 'string') return v;
        try { return JSON.stringify(v); } catch (e) { return String(v); }
    };
    const __truncate = (s) => {
        const max = window.__pyagent_api_mon.maxBody;
        if (typeof s !== 'string') s = __safeStr(s);
        return s.length > max ? s.slice(0, max) + '...[TRUNCATED]' : s;
    };

    // === fetch ===
    const origFetch = window.fetch;
    if (origFetch) {
        window.fetch = function(input, init) {
            const idx = ++window.__pyagent_api_mon._idx;
            const url = typeof input === 'string' ? input : (input && input.url) || String(input);
            const method = (init && init.method) || (input && input.method) || 'GET';
            const reqHeaders = (init && init.headers) ? __safeStr(init.headers) : '';
            const reqBody = (init && init.body) ? __truncate(init.body) : '';
            const entry = { idx, kind: 'fetch', method, url, requestHeaders: reqHeaders,
                requestBody: reqBody, status: 0, responseBody: '', startedAt: Date.now(), done: false };
            __record(entry);
            return origFetch.apply(this, arguments).then(async (resp) => {
                entry.status = resp.status;
                entry.responseHeaders = __safeStr(resp.headers);
                if (window.__pyagent_api_mon.captureBodies) {
                    try {
                        const clone = resp.clone();
                        const txt = await clone.text();
                        entry.responseBody = __truncate(txt);
                    } catch (e) { entry.responseBody = '[unreadable]'; }
                }
                entry.done = true;
                return resp;
            }).catch((err) => {
                entry.error = String(err && err.message || err);
                entry.done = true;
                throw err;
            });
        };
    }

    // === XMLHttpRequest ===
    const OrigXHR = window.XMLHttpRequest;
    if (OrigXHR) {
        const origOpen = OrigXHR.prototype.open;
        const origSend = OrigXHR.prototype.send;
        OrigXHR.prototype.open = function(method, url) {
            this.__pyagent_meta = { method, url };
            return origOpen.apply(this, arguments);
        };
        OrigXHR.prototype.send = function(body) {
            const meta = this.__pyagent_meta || {};
            const idx = ++window.__pyagent_api_mon._idx;
            const entry = { idx, kind: 'xhr', method: meta.method, url: meta.url,
                requestBody: body ? __truncate(body) : '', status: 0, responseBody: '',
                startedAt: Date.now(), done: false };
            __record(entry);
            this.addEventListener('loadend', () => {
                try {
                    entry.status = this.status;
                    entry.responseHeaders = this.getAllResponseHeaders ? this.getAllResponseHeaders() : '';
                    if (window.__pyagent_api_mon.captureBodies) {
                        entry.responseBody = __truncate(this.responseText || '');
                    }
                } catch (e) { entry.error = String(e); }
                entry.done = true;
            });
            return origSend.apply(this, arguments);
        };
    }
    return true;
}
"""

JS_API_MONITOR_QUERY = r"""
function queryApiMonitor(filter) {
    if (!window.__pyagent_api_mon) return { installed: false, requests: [], count: 0 };
    filter = filter || {};
    let reqs = window.__pyagent_api_mon.requests;
    if (filter.urlPattern) {
        const p = filter.urlPattern;
        reqs = reqs.filter(r => r.url && r.url.indexOf(p) !== -1);
    }
    if (filter.method) {
        const m = filter.method.toUpperCase();
        reqs = reqs.filter(r => (r.method || '').toUpperCase() === m);
    }
    if (filter.onlyDone === true) reqs = reqs.filter(r => r.done);
    if (filter.onlyError === true) reqs = reqs.filter(r => r.error || (r.status >= 400 && r.status > 0));

    const statusCounts = {};
    let totalBytes = 0;
    reqs.forEach(r => {
        const sk = r.status || (r.error ? 'ERR' : 0);
        statusCounts[sk] = (statusCounts[sk] || 0) + 1;
        totalBytes += (r.responseBody || '').length + (r.requestBody || '').length;
    });
    return {
        installed: true,
        count: reqs.length,
        statusCounts,
        totalBytes,
        requests: reqs.slice(-100), // 限制返回条数
    };
}
"""

JS_API_MONITOR_CLEAR = r"""
function clearApiMonitor() {
    if (!window.__pyagent_api_mon) return { cleared: 0 };
    const n = window.__pyagent_api_mon.requests.length;
    window.__pyagent_api_mon.requests = [];
    return { cleared: n };
}
"""


# ------------------------------------------------------------------
# 公共组合 helper
# ------------------------------------------------------------------


def wrap_iife(*js_blocks: str, expression: str) -> str:
    """把多段 JS 代码按顺序合并,末尾 return <expression>。

    用法::

        wrap_iife(JS_OPTHTML, JS_OPTIMIZE_FOR_TOKENS,
                  expression='optHTML(false)')

    返回形如::

        (() => {
            <js_blocks joined>
            return <expression>;
        })()

    这样在 chrome.scripting.executeScript 的 func: (code) => eval(code) 中,
    可以直接拿到 optHTML(false) 的返回值。
    """
    body = "\n;\n".join(js_blocks)
    return f"(() => {{\n{body}\n; return ({expression});\n}})()"
