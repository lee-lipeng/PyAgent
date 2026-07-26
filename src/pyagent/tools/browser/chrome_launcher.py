"""Chrome 启动器。

当 browser_status / browser_navigate 工具被调用时,如果发现浏览器
没启动(没有 chrome.exe 进程在跑 + debug 端口没人监听),自动启动 Chrome
并开启 --remote-debugging-port,让用户无需手动操作浏览器。

设计哲学
- 平台无关:Windows / macOS / Linux 各自有"找 chrome 二进制"的策略
- 不阻塞 LLM:启动 Chrome 失败不抛,只 logger.warning + 提示用户
- 幂等:已经启动过 Chrome(或别的进程占着 debug 端口)就不再启
- 可选:`auto_launch_chrome=False` 时完全不介入,纯提示

CDP 端口策略
Chrome 的 --remote-debugging-port=N 会让 Chrome 在 localhost:N 上
暴露 DevTools Protocol HTTP API。WS server 跟 CDP 是两套事:
- PyAgent WS server: ws://127.0.0.1:18787 — Chrome 扩展连这个
- Chrome DevTools:  http://127.0.0.1:9222  — 可用于远程操控 Chrome

启动 Chrome 时不开 CDP(扩展不通过 CDP 通信),只确保 Chrome自身启动 + 加载已安装的扩展。
debug 端口的存在仅是"确认 Chrome 在跑"的健康信号。
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import socket
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

LaunchResult = Literal["started", "already_running", "not_found", "failed"]


def is_chrome_running() -> bool:
    """检测是否有 chrome.exe / Google Chrome 进程在跑。

    实现策略:
    1. 探测 debug 端口(默认 9222)— 有人 listen = 已有 Chrome 在跑
    2. fallback:Windows 用 tasklist / macOS 用 ps / Linux 用 pgrep
    """
    # 1. 端口探测 — 优先(覆盖"别的进程在跑 Chrome 的 CDP 端口"场景)
    if _port_listening("127.0.0.1", 9222, timeout=0.2):
        return True

    # 2. 进程探测
    return _check_chrome_process()


def _check_chrome_process() -> bool:
    """平台特定的进程探测 """
    system = platform.system()
    try:
        if system == "Windows":
            import subprocess

            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout
            return "chrome.exe" in out.lower()
        if system == "Darwin":
            import subprocess

            out = subprocess.run(
                ["pgrep", "-lf", "Google Chrome"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout
            return "Google Chrome" in out
        # Linux + 其他
        import subprocess

        out = subprocess.run(
            ["pgrep", "-af", "chromium|chrome"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
        return "chrom" in out.lower()
    except Exception as exc:
        logger.debug(f"Chrome 进程探测失败: {exc}")
        return False


def _port_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    """探测 TCP 端口是否在 listen。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, OSError):
        return False


def find_chrome_binary() -> str | None:
    """查找 Chrome / Chromium 可执行文件。

    优先级:
    1. shutil.which("chrome"|"google-chrome"|"chromium")
    2. 平台默认安装路径(Windows: Program Files, macOS: /Applications)
    """
    # 1. PATH 里搜
    for name in ("google-chrome", "chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path

    # 2. 平台默认路径
    system = platform.system()
    if system == "Windows":
        # Windows 环境变量名大小写不敏感,但 ruff SIM112 建议大写 — 实际
        # 探测时 os.environ.get 不区分大小写,这里用大写保持静态检查通过
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chrome.app/Contents/MacOS/Chrome"),
        ]
    else:
        candidates = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/snap/bin/chromium"),
        ]

    for c in candidates:
        if c.exists():
            return str(c)
    return None


def launch_chrome(chrome_path: str | None = None) -> LaunchResult:
    """同步启动 Chrome (非阻塞,Chrome 自己后台跑)。

    平台差异:
    - Windows: subprocess.Popen(["start", "", chrome, ...]) — start 是
      cmd 内置命令,负责"打开文件/URL 关联",且让 Chrome 完全脱离父进程。
    - macOS:   open -a "Google Chrome" — 用 launchd 接管。
    - Linux:   subprocess.Popen([chrome, ...]) + start_new_session=True。
    """
    binary = chrome_path or find_chrome_binary()
    if binary is None:
        logger.warning("Chrome 未找到,请安装 Chrome 或在 settings 配置 chrome_path")
        return "not_found"

    # debug 端口 + 加载已装扩展的 user-data-dir (避免多 Chrome 实例冲突)
    args = [
        binary,
        # remote-debugging-port 让"是否在跑"探测更可靠 — 不影响 PyAgent WS
        "--remote-debugging-port=9222",
        # 已有默认 user-data-dir 即可,扩展已加载;不传 --user-data-dir
        # 是为了尊重用户的 Chrome 配置(书签、扩展、Cookie 都在)
    ]

    system = platform.system()
    try:
        if system == "Windows":
            # start "" 让 Chrome 与父进程完全解耦(脱离 job object)
            # 用 cmd.exe 调 start,避免 Popen 直接启动时 Chrome 跟随 PyAgent 退出
            import subprocess

            subprocess.Popen(
                ["cmd", "/c", "start", "", *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
        elif system == "Darwin":
            import subprocess

            # open 会通过 launchd 启动,完全脱离 PyAgent
            subprocess.Popen(
                ["open", "-a", binary, "--args", *args[1:]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            # Linux
            import subprocess

            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        logger.info(f"已启动 Chrome: {binary}")
        return "started"
    except Exception as exc:  # noqa: BLE001
        logger.warning("启动 Chrome 失败 (%s): %s", binary, exc)
        return "failed"


async def async_launch_chrome_if_needed(chrome_path: str | None = None) -> LaunchResult:
    """异步包装 + 自动跳过"已在跑"的场景。

    - 若 Chrome 已在跑 → 返回 "already_running"
    - 若 Chrome 没跑 → 调用 launch_chrome,返回启动结果
    """
    if is_chrome_running():
        return "already_running"

    # 移到后台线程,避免阻塞(虽然 launch_chrome 本身非阻塞,但仍走 sync I/O)
    return await asyncio.to_thread(launch_chrome, chrome_path)


async def wait_for_chrome_ready(timeout: float = 5.0) -> bool:
    """等 Chrome debug 端口 listen(证明 Chrome 已完全启动)。

    不严格必要 — bg.js 在 Chrome 启动后会自动重连(PyAgent WS server),
    但给个"Chrome 真的开了"的健康信号能避免 LLM 紧接着调 navigate 时扩展还没拿到 SW 句柄。
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if _port_listening("127.0.0.1", 9222, timeout=0.2):
            return True
        await asyncio.sleep(0.2)
    return False


__all__ = [
    "LaunchResult",
    "async_launch_chrome_if_needed",
    "find_chrome_binary",
    "is_chrome_running",
    "launch_chrome",
    "wait_for_chrome_ready",
]
