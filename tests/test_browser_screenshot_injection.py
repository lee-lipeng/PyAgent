"""浏览器截图辅助脚本必须按需注入到 MAIN 执行世界。"""

from pathlib import Path


def test_screenshot_script_and_background_injection_are_present():
    extension = (
        Path(__file__).resolve().parents[1]
        / "src/pyagent/tools/browser/extension"
    )
    main_script = extension / "screenshot_main.js"
    background = (extension / "background.js").read_text(encoding="utf-8")
    code = main_script.read_text(encoding="utf-8")
    assert "window.__pyagent_screenshotHtml = async" in code
    assert "world: 'MAIN'" in background
    assert "files: ['vendor/html2canvas.min.js', 'screenshot_main.js']" in background
