"""Windows 进程树中止：不能只终止 cmd.exe 而留下子进程。"""

import asyncio
import ctypes
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pyagent.tools.builtin import bash
from pyagent.tools.builtin.bash import RunBashTool


@pytest.mark.asyncio
async def test_windows_tree_cleanup_calls_taskkill(monkeypatch):
    class Proc:
        pid = 123
        returncode = None

        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True
            self.returncode = 1

        async def wait(self):
            return self.returncode

    process = Proc()
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)

        class Killer:
            async def wait(self):
                process.returncode = 1

        return Killer()

    monkeypatch.setattr(bash, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await RunBashTool._terminate_process(process)
    assert calls == [("taskkill", "/F", "/T", "/PID", "123")]
    assert not process.killed


@pytest.mark.skipif(os.name != "nt", reason="Windows 实机子进程测试")
@pytest.mark.asyncio
async def test_windows_terminate_process_kills_child_process(tmp_path):
    pid_file = tmp_path / "child.pid"
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        "import subprocess, sys\n"
        f"p = subprocess.Popen([sys.executable, '-c', {('import time;time.sleep(60)')!r}])\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        "p.wait()\n",
        encoding="utf-8",
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(parent_script),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    pid = None
    try:
        for _ in range(160):
            if pid_file.exists() and pid_file.read_text():
                pid = int(pid_file.read_text())
                break
            await asyncio.sleep(0.05)
        assert pid is not None, "子进程 PID 文件未生成"

        await asyncio.wait_for(
            RunBashTool._terminate_process(process), timeout=15
        )
        assert process.returncode is not None

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        running = False
        for _ in range(40):
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                running = False
                break
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(exit_code))
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            running = exit_code.value == 259  # STILL_ACTIVE
            if not running:
                break
            await asyncio.sleep(0.05)
        assert not running, "Windows 子进程树未完整终止"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if pid is not None:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, check=False,
            )
