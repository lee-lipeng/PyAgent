"""CLI Runtime 的 setup、run、teardown 必须在同一事件循环。"""

import asyncio
import importlib

cli = importlib.import_module("pyagent.cli.app")


def test_cli_run_keeps_runtime_in_one_loop(monkeypatch):
    loops = []

    class FakeResult:
        success = True
        final_response = ""
        turns = 1

    class FakeRuntime:
        def __init__(self, settings):
            pass

        async def setup(self):
            loops.append(asyncio.get_running_loop())

        async def run(self, query):
            loops.append(asyncio.get_running_loop())
            return FakeResult()

        async def teardown(self):
            loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(cli, "Runtime", FakeRuntime)
    monkeypatch.setattr(cli, "load_settings", lambda: object())
    cli.run("hello")
    assert len(loops) == 3
    assert loops[0] is loops[1] is loops[2]
