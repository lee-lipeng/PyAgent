"""Shell 任务取消及超时后不得遗留活跃子进程。"""

import asyncio
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from pyagent.tools.builtin.bash import RunBashTool


def _python_command(source):
    if os.name == "nt":
        return subprocess.list2cmdline([sys.executable, "-c", source])
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


@pytest.mark.asyncio
async def test_pre_cancelled_does_not_spawn(monkeypatch):
    signal = asyncio.Event()
    signal.set()

    async def must_not_spawn(*args, **kwargs):
        raise AssertionError("cancelled task spawned shell")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", must_not_spawn)
    result = await RunBashTool().execute("id", {"command": "echo test"}, signal=signal)
    assert result.details["error"] == "aborted"


@pytest.mark.asyncio
async def test_normal_shell_command():
    result = await RunBashTool().execute("id", {"command": _python_command("print(42)")})
    assert not result.is_error
    assert "42" in result.content
    assert "退出码: 0" in result.content


@pytest.mark.skipif(os.name == "nt", reason="POSIX 独立进程组测试")
@pytest.mark.asyncio
async def test_abort_terminates_process_group(tmp_path):
    pid_file = tmp_path / "child.pid"
    cmd = _python_command(
        f"import subprocess; p=subprocess.Popen(['sleep','60']);"
        f"open({str(pid_file)!r},'w').write(str(p.pid));p.wait()"
    )
    signal = asyncio.Event()
    task = asyncio.create_task(RunBashTool().execute("id", {"command": cmd}, signal=signal))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    assert pid_file.exists()
    signal.set()
    result = await asyncio.wait_for(task, 3)
    assert result.details["error"] == "aborted"
    # os.kill(pid, 0) 对僵尸进程仍可能成功，检查 /proc 状态更准确。
    pid = int(pid_file.read_text())
    status = Path(f"/proc/{pid}/stat")
    if status.exists():
        assert status.read_text().split()[2] == "Z"


@pytest.mark.skipif(os.name == "nt", reason="POSIX 独立进程组测试")
@pytest.mark.asyncio
async def test_timeout_terminates_group():
    result = await RunBashTool().execute("id", {"command": "sleep 60 & wait", "timeout": 1})
    assert result.details["error"] == "timeout"


@pytest.mark.skipif(os.name == "nt", reason="POSIX 独立进程组测试")
@pytest.mark.asyncio
async def test_cancelled_task_reaps_process(monkeypatch):
    original_spawn = asyncio.create_subprocess_exec
    processes = []

    async def record_spawn(*args, **kwargs):
        proc = await original_spawn(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    task = asyncio.create_task(RunBashTool().execute("id", {"command": "sleep 60"}))
    for _ in range(200):
        if processes:
            break
        await asyncio.sleep(0.01)
    assert processes
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert processes[0].returncode is not None
