# PyAgent

AI Agent 工具包，借鉴 [Pi](https://github.com/earendil-works/pi) 设计理念。

## 设计理念

- **核心极简**：只做 Agent loop + 工具执行 + 事件流
- **LLM 是最强大的工具**：不用规则包装死，让 LLM 自己看页面、自己写代码
- **事件驱动**：所有状态变化通过事件总线暴露，Logging / Tracing / Permission / Metrics 全是 Hook
- **零配置自动加载**：模块扫描 + 反射 + 装饰器，Tool / Skill / Hook 自动发现注册
- **litellm 做 LLM 抽象**，不重复造轮子

## 快速开始

```bash
# 安装
uv pip install -e .

# 配置 API Key
export OPENAI_API_KEY=sk-...

# 启动 CLI REPL
pyagent
```

PyAgent 也可以直接在代码里调用：

```python
import asyncio
from pyagent.config.loader import load_settings
from pyagent.core.runtime import Runtime

async def main():
    runtime = Runtime(load_settings())
    result = await runtime.run("用一句话介绍你自己。")
    print(result.final_response)

asyncio.run(main())
```

## 架构概览

PyAgent 的代码按职责切成 9 个子包，依赖方向自上而下、严格单向。核心入口是 `pyagent.core.runtime.Runtime`，它把配置、LLM 客户端、工具注册器、会话存储、事件总线、Agent 与 Loop 编排在一起，对外只暴露 `run / steer / abort` 等少量方法。

```text
┌─────────────────────────────────────────────────────────────────┐
│                          CLI / SDK 调用方                         │
│                  pyagent.cli.app · examples/*.py                 │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  pyagent.core.runtime.Runtime   「容器 + 生命周期」                  │
│  - 持有所有单例（llm / tool_registry / skill_manager /            │
│    hooks / session_store / agent / loop）                         │
│  - load_settings → setup() → run() → teardown()                  │
└──┬─────────────┬─────────────┬─────────────┬─────────────┬────────┘
   │             │             │             │             │
   ▼             ▼             ▼             ▼             ▼
┌───────┐   ┌────────┐   ┌────────────┐  ┌────────┐  ┌────────────┐
│config │   │  llm   │   │   tools    │  │skills  │  │  session   │
│loader │   │ client │   │  registry  │  │manager │  │   store    │
└───┬───┘   └───┬────┘   └──────┬─────┘  └───┬────┘  └──────┬─────┘
    │           │               │            │              │
    ▼           ▼               ▼            ▼              ▼
┌─────────┐ ┌─────────┐  ┌──────────────┐ ┌────────────┐ ┌──────────┐
│Settings │ │LLMClient│  │ builtin/*.py │ │builtin/*.py│ │ Session  │
│(pydantic│ │ litellm │  │  + discovery │ │ + discovery│ │(messages │
│-settings│ │ 包装 +  │  │  + executor  │ │  + loader  │ │  + meta) │
│  v2 )   │ │streaming│  │              │ │            │ │          │
└─────────┘ └─────────┘  └──────────────┘ └────────────┘ └──────────┘
                │                │                 │
                └────────────────┴─────────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │   pyagent.core.agent.Agent    │ 「高层有状态：消息累积」
                  │   - 维护 session.messages     │
                  │   - 调 AgentLoop 处理单轮     │
                  │   - 调 CompactionManager 瘦身 │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │  pyagent.core.loop.AgentLoop  │ 「低层无状态：单轮驱动」
                  │  - 调 LLMClient 流式生成      │
                  │  - 调 ToolExecutor 执行工具    │
                  │  - 在每个关键节点 emit 事件   │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │  pyagent.hooks.manager        │ 「事件总线：唯一订阅入口」
                  │  HookManager.on / emit        │
                  └──────────────────────────────┘
```

### 一次 `runtime.run(query)` 的数据流

```text
用户 query
   │
   ▼
Runtime.run(query, session?, on_chunk?)
   ├─ if not setup: Runtime.setup()        # 懒初始化
   ├─ session ??= create_session()
   ├─ Agent.handle(query, session, ctx)
   │     │
   │     └─ for turn in range(max_turns):
   │           │
   │           ├─ ctx.is_cancelled() → break
   │           ├─ CompactionManager.maybe_compact(session)
   │           ├─ ContextBuilder.build(session) → messages
   │           ├─ hooks.emit(LOOP_START / BEFORE_LLM)
   │           ├─ LLMClient.stream_and_collect(messages, tools)
   │           │     └─ for delta in stream: on_chunk(delta)
   │           ├─ hooks.emit(AFTER_LLM)
   │           │
   │           ├─ response.has_tool_calls?
   │           │     │
   │           │     ├─ yes → ToolExecutor.execute_batch(tool_calls)
   │           │     │           └─ for tc: hooks.emit(BEFORE_TOOL / AFTER_TOOL)
   │           │     │           └─ tool_results → session.messages
   │           │     │           └─ if any terminate: stop_reason=terminated, break
   │           │     │
   │           │     └─ no  → drain steering queue → stop_reason=completed, break
   │           │
   │           └─ if steering queue non-empty: pop → 注入下一轮 user message
   │
   ├─ hooks.emit(LOOP_END)
   └─ return LoopResult(success, final_response, turns, stop_reason, error)
```

### 设计原则

1. **单向依赖**：`core` 不 import `cli`；`tools/skills/hooks` 不 import `core`；所有人都可以 import `utils`。
2. **零硬编码 selector**：工具注册表只暴露 OpenAI schema，LLM 自己决定调用哪个工具、传什么参数。
3. **可替换的 LLM**：所有 LLM 调用走 `LLMClient`，换模型只改 `settings.llm.model`，业务代码无感。
4. **Hook 即插即用**：日志 / 监控 / 权限 / 限流全部以 Hook 形式挂到 `HookManager`，启动时注册，退出时反注册。
5. **Session 是唯一可变状态**：除了 Session 里的 `messages`，其它组件都是无状态的，方便单实例多任务。

## 配置

PyAgent 支持多级配置，优先级从低到高：

```
默认值 → ~/.pyagent/settings.json（用户全局）→ .pyagent/settings.json（项目本地）→ 环境变量(PYAGENT_*) → CLI 参数
```

### 配置文件

在项目根目录创建 `.pyagent/settings.json` 即可覆盖默认配置：

```json
{
  "llm": {
    "model": "openai/gpt-4o-mini",
    "api_key": null,
    "base_url": null,
    "temperature": 0.7,
    "max_tokens": null,
    "timeout": 120.0
  },
  "agent": {
    "system_prompt": "You are a helpful assistant.",
    "max_turns": 20,
    "tool_execution": "parallel"
  },
  "session": {
    "enabled": true,
    "dir": null
  },
  "enable_builtin_tools": true,
  "enable_builtin_skills": true,
  "log_level": "INFO"
}
```

### 字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `llm.model` | `str` | `openai/gpt-4o-mini` | litellm 模型标识，支持 `openai/`、`anthropic/`、`gemini/` 等前缀 |
| `llm.api_key` | `str\|null` | `null` | API 密钥。为 `null` 时从环境变量读取（如 `OPENAI_API_KEY`） |
| `llm.base_url` | `str\|null` | `null` | 自定义 API 端点，用于代理或兼容接口 |
| `llm.temperature` | `float` | `0.7` | 采样温度，越高越随机 |
| `llm.max_tokens` | `int\|null` | `null` | 单次生成的最大 token 数，`null` 表示不限制 |
| `llm.timeout` | `float` | `120.0` | 请求超时时间（秒） |
| `agent.system_prompt` | `str` | `You are a helpful assistant.` | 系统提示词 |
| `agent.max_turns` | `int` | `20` | Agent 循环最大轮次，防止无限循环 |
| `agent.tool_execution` | `str` | `parallel` | 工具执行模式：`parallel`（并行）或 `sequential`（串行） |
| `session.enabled` | `bool` | `true` | 是否启用会话持久化 |
| `session.dir` | `str\|null` | `null` | 会话存储目录，`null` 时用 `~/.pyagent/sessions/` |
| `enable_builtin_tools` | `bool` | `true` | 是否加载内置工具（read/write/edit/bash/grep/find/ls） |
| `enable_builtin_skills` | `bool` | `true` | 是否加载内置技能（coding/research/testing/debugging/planning/git-workflow/create-prompt/create-skill） |
| `log_level` | `str` | `INFO` | 日志级别：`DEBUG`/`INFO`/`WARNING`/`ERROR` |

### 环境变量

所有配置项均可通过环境变量覆盖，前缀为 `PYAGENT_`，嵌套层级用 `__` 分隔：

```bash
# 覆盖 llm.model
export PYAGENT_LLM__MODEL="anthropic/claude-3-5-sonnet"

# 覆盖 agent.max_turns
export PYAGENT_AGENT__MAX_TURNS=10

# 覆盖 log_level
export PYAGENT_LOG_LEVEL=DEBUG
```

## 作为 Python SDK 使用

CLI 只是 PyAgent 的一种入口。整个 Runtime 是纯 Python 库，导出的核心 API 在 `pyagent.core.runtime.Runtime`：

| API | 用途 |
|-----|------|
| `Runtime(settings)` | 用 `Settings` 构造运行时 |
| `load_settings(cwd=None)` | 按多级优先级加载配置 |
| `runtime.setup()` | 主动触发 LLMClient / Tool / Skill / Session / Agent / Loop 的初始化 |
| `await runtime.run(query, session, on_chunk)` | 执行一轮 Agent 循环，返回 `LoopResult` |
| `runtime.create_session()` / `save_session()` / `load_session()` | 会话 CRUD |
| `runtime.steer(text)` / `runtime.abort()` | 在 Agent 运行期间注入改向 / 中止 |
| `runtime.hooks.on(EventType.X, handler)` | 订阅事件，返回 unsubscribe 函数 |


### 最简调用

```python
import asyncio
from pyagent.config.loader import load_settings
from pyagent.core.runtime import Runtime

async def main():
    runtime = Runtime(load_settings())  # 第一次 run() 会自动 setup()
    result = await runtime.run("写一个 Python 装饰器示例。")
    print(result.final_response)        # final_response 是 LLM 最后一轮纯文本
    print(result.stop_reason)           # "completed" / "max_turns" / "cancelled" / ...

asyncio.run(main())
```

### 自定义 Settings

```python
from pathlib import Path
from pyagent.config.settings import Settings

settings = Settings(
    llm={"model": "openai/gpt-4o-mini", "temperature": 0.3, "timeout": 60.0},
    agent={
        "system_prompt": "你是一名资深 Python 工程师。",
        "max_turns": 10,
        "tool_execution": "parallel",   # 或 "sequential"
    },
    session={"enabled": True, "dir": Path("./.pyagent_sessions")},
    enable_builtin_tools=True,
    enable_builtin_skills=True,
)
runtime = Runtime(settings)
runtime.setup()
```

`api_key` 不传时由 `litellm` 自动从环境变量读取（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 等）。

### 流式输出 + 会话持久化

```python
session = runtime.create_session(title="演示会话")
print("会话 ID:", session.metadata.id)

# on_chunk 是每个文本 chunk 的回调，可直接接到 rich / websocket / SSE
result = await runtime.run(
    "读取 examples/minimal_repl.py 并解释。",
    session=session,
    on_chunk=lambda delta: print(delta, end="", flush=True),
)
print("\nstop_reason =", result.stop_reason)
runtime.save_session(session)
```

第二次 `runtime.run(query, session=same_session)` 时，LLM 能看到上一轮的工具结果和对话历史。

### 自定义 Hook

```python
from pyagent.hooks.types import Event, EventType

def on_before_llm(event: Event) -> None:
    print(f"[hook] turn={event.get('turn')} messages={event.get('message_count')}")

def on_after_tool(event: Event) -> None:
    print(f"[hook] tool={event.payload.get('tool_name')}")

unsub_llm = runtime.hooks.on(EventType.BEFORE_LLM, on_before_llm)
unsub_tool = runtime.hooks.on(EventType.AFTER_TOOL, on_after_tool)

try:
    await runtime.run("...")
finally:
    unsub_llm(); unsub_tool()
```

### 运行时人工介入（steer / abort）

```python
import asyncio

task = asyncio.create_task(runtime.run("写一首长诗。"))
await asyncio.sleep(1.0)
runtime.steer("改成五言绝句。")          # 在 turn 边界注入新用户输入
# runtime.abort()                       # 需要立即中止时调用
result = await task
```

### 一次请求的完整流程

```text
Runtime.run(query, session, on_chunk)
    └─ AgentLoop.run(query, session, on_chunk, ctx)
        ├─ emit loop_start
        ├─ while turns < max_turns:
        │   ├─ ctx.is_cancelled()? → cancelled
        │   ├─ 阈值/overflow? → CompactionManager.compact_session(force=True)
        │   ├─ ContextBuilder.build()  # system + 历史 + query
        │   ├─ emit before_llm
        │   ├─ LLMClient.stream_and_collect()  # 流式
        │   │   └─ on_chunk(delta) → 用户回调
        │   ├─ emit after_llm
        │   ├─ 无 tool_calls → drain steering → completed
        │   └─ 有 tool_calls → ToolExecutor.execute_batch()
        │       ├─ emit before_tool / after_tool
        │       ├─ turn 边界 drain steering
        │       └─ 全部 terminate? → terminated
        └─ emit loop_end
```

返回值 `LoopResult`：

| 字段 | 含义 |
|------|------|
| `success` | 循环是否成功结束（completed / terminated 视为成功） |
| `final_response` | LLM 最后一条纯文本 / `terminated` 时是最后一个工具的 content |
| `turns` | 实际循环轮次 |
| `stop_reason` | `completed` / `terminated` / `cancelled` / `max_turns` / `permission_denied` / `llm_error` / `error` |
| `error` | 失败时的错误描述 |

### 把 PyAgent 嵌入 Web 服务 / 桌面应用

因为 `Runtime.run()` 是 `async` 方法，可以直接接到 FastAPI / Starlette / WebSocket / aiohttp：

```python
from fastapi import FastAPI
from pyagent.config.loader import load_settings
from pyagent.core.runtime import Runtime

app = FastAPI()
runtime = Runtime(load_settings())
runtime.setup()

@app.post("/chat")
async def chat(query: str, session_id: str | None = None):
    session = runtime.load_session(session_id) if session_id else runtime.
    result = await runtime.run(query, session=session)
    runtime.save_session(session)
    return {"reply": result.final_response, "session_id": session.metadata.id}
```

流式场景推荐用 `AgentLoop.run_stream(query, session, ctx)` —— 它直接 `yield` LLM 文本 chunk，可以边生成边推送给客户端。

## License

MIT