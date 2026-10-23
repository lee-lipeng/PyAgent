(() => {
  // 在 MAIN 执行世界注册，供 chrome.scripting.executeScript 的页面代码调用。
  window.__pyagent_screenshotHtml = async function (opts = {}) {
    if (typeof window.html2canvas !== 'function') {
      throw new Error('html2canvas 未加载');
    }
    const target = opts.selector ? document.querySelector(opts.selector) : document.body;
    if (!target) {
      throw new Error('截图目标元素未找到: ' + (opts.selector || 'document.body'));
    }
    const canvas = await window.html2canvas(target, {
      scale: opts.scale || 1,
      useCORS: true,
      allowTaint: false,
      backgroundColor: opts.bgColor || null,
      windowWidth: document.documentElement.scrollWidth,
      windowHeight: document.documentElement.scrollHeight,
      width: opts.fullPage ? document.documentElement.scrollWidth : window.innerWidth,
      height: opts.fullPage ? document.documentElement.scrollHeight : window.innerHeight,
      scrollX: 0,
      scrollY: 0,
      ignoreElements: (el) => el.tagName === 'SCRIPT' || el.tagName === 'NOSCRIPT',
    });
    const format = opts.format === 'jpeg' ? 'jpeg' : 'png';
    const mime = format === 'jpeg' ? 'image/jpeg' : 'image/png';
    const quality = typeof opts.quality === 'number' ? opts.quality / 100 : 0.8;
    const dataUrl = format === 'jpeg' ? canvas.toDataURL(mime, quality) : canvas.toDataURL(mime);
    return {
      data_url: dataUrl,
      width: canvas.width,
      height: canvas.height,
      format,
      fullPage: !!opts.fullPage,
    };
  };
})();
