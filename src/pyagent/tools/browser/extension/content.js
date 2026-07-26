// PyAgent Bridge — Content Script
// 职责:
//   1. 标记扩展已加载 (window.__pyagent_bridge_loaded)
//   2. 注入 html2canvas 并暴露 window.__pyagent_screenshotHtml 给 MAIN 世界调用
//   3. 失败时给出明确错误,而不是 silent fail
//
// html2canvas 加载策略:
//   优先级1: 扩展 web_accessible_resources (vendor/html2canvas.min.js)
//   优先级2: chrome.runtime.getURL('vendor/html2canvas.min.js')
//   优先级3: CDN (HHTML2CANVAS_CDN_URL) — 离线不可用
//
// 注意:html2canvas 必须在 ISOLATED (content script) 世界加载,
// 然后把函数引用挂到 window; MAIN 世界通过 window.__pyagent_screenshotHtml 调用。

(function () {
  const TAG = '[PyAgent Bridge Content]';
  console.info(TAG, '已注入', location.href);

  // 立即标记 — 用于 DevTools 验证扩展是否生效
  window.__pyagent_bridge_loaded = true;

  const H2C_CDN =
    'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
  // 本地 vendor 路径(在 manifest.json 的 web_accessible_resources 中暴露)
  const H2C_LOCAL = (() => {
    try {
      return chrome.runtime.getURL('vendor/html2canvas.min.js');
    } catch (e) {
      return '';
    }
  })();

  let _h2cLoadPromise = null;

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.async = false;
      s.onload = () => resolve();
      s.onerror = (e) => reject(new Error('script load failed: ' + src));
      (document.head || document.documentElement).appendChild(s);
    });
  }

  async function ensureHtml2Canvas() {
    if (window.html2canvas) return window.html2canvas;
    if (_h2cLoadPromise) return _h2cLoadPromise;
    _h2cLoadPromise = (async () => {
      // 优先本地副本
      if (H2C_LOCAL) {
        try {
          await loadScript(H2C_LOCAL);
          if (window.html2canvas) {
            console.info(TAG, '已加载本地 html2canvas');
            return window.html2canvas;
          }
        } catch (e) {
          console.warn(TAG, '本地 html2canvas 加载失败,尝试 CDN:', e);
        }
      }
      // 兜底 CDN
      try {
        await loadScript(H2C_CDN);
        if (window.html2canvas) {
          console.info(TAG, '已通过 CDN 加载 html2canvas');
          return window.html2canvas;
        }
      } catch (e) {
        throw new Error('html2canvas 加载失败:本地副本不可用 + CDN 不通');
      }
      throw new Error('html2canvas 加载完成但 window.html2canvas 仍未定义');
    })();
    return _h2cLoadPromise;
  }

  // 截图函数:target 元素 → canvas → dataURL
  // 接受 opts = {selector, format, quality, fullPage, scale, bgColor}
  // 返回 {data_url, width, height, format, fullPage}
  window.__pyagent_screenshotHtml = async function (opts) {
    opts = opts || {};
    const h2c = await ensureHtml2Canvas();
    const target = opts.selector
      ? document.querySelector(opts.selector)
      : document.body;
    if (!target) {
      throw new Error('截图目标元素未找到: ' + (opts.selector || 'document.body'));
    }
    const canvas = await h2c(target, {
      scale: opts.scale || 1,
      useCORS: true,
      allowTaint: false,
      backgroundColor: opts.bgColor || null,
      // 整页截图:设置 windowWidth/Height 让 html2canvas 重绘整个滚动区域
      windowWidth: document.documentElement.scrollWidth,
      windowHeight: document.documentElement.scrollHeight,
      width: opts.fullPage
        ? document.documentElement.scrollWidth
        : window.innerWidth,
      height: opts.fullPage
        ? document.documentElement.scrollHeight
        : window.innerHeight,
      scrollX: 0,
      scrollY: 0,
      // 跳过一些会失败的元素 (svg foreignObject / object 标签)
      ignoreElements: (el) => el.tagName === 'SCRIPT' || el.tagName === 'NOSCRIPT',
    });
    const fmt = opts.format === 'jpeg' ? 'image/jpeg' : 'image/png';
    const q = typeof opts.quality === 'number' ? opts.quality / 100 : 0.8;
    const dataUrl =
      fmt === 'image/jpeg' ? canvas.toDataURL(fmt, q) : canvas.toDataURL(fmt);
    return {
      data_url: dataUrl,
      width: canvas.width,
      height: canvas.height,
      format: opts.format || 'png',
      fullPage: !!opts.fullPage,
    };
  };

  // 提前异步加载,首次截图时大概率已完成,降低首次截图延迟
  // 用 setTimeout 0 让其他逻辑先跑,失败不影响 content script 注册
  setTimeout(() => {
    ensureHtml2Canvas().catch((e) =>
      console.warn(TAG, '预加载 html2canvas 失败(将在首次截图时重试):', e),
    );
  }, 0);

  // 通知 page-world 桥已就绪(扩展点)
  window.dispatchEvent(new Event('pyagent-bridge-ready'));
})();