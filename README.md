# PyAgent

AI Agent 工具包，借鉴 [Pi](https://github.com/earendil-works/pi) 设计理念。

## 设计理念

- **核心极简**：只做 Agent loop + 工具执行 + 事件流
- **LLM 是最强大的工具**：不用规则包装死，让 LLM 自己看页面、自己写代码
- **事件驱动**：所有状态变化通过事件总线暴露，Logging / Permission 全是 Hook
- **零配置自动加载**：模块扫描 + 反射 + 装饰器，Tool / Skill / Hook 自动发现注册
- **litellm 做 LLM 抽象**，不重复造轮子

## 快速开始

推荐 Python 3.12 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --group dev
# 按所选模型 Provider 配置 API Key，并设置 llm.model（默认模型是 MiniMax）。
# 示例：export OPENAI_API_KEY="your-api-key"
#       export PYAGENT_LLM__MODEL="openai/gpt-4o-mini"
uv run pyagent
```

Python SDK 应在同一事件循环里创建、使用和关闭 Runtime：

```python
import asyncio

from pyagent.config.loader import load_settings
from pyagent.core.runtime import Runtime

async def main():
    runtime = Runtime(load_settings())
    try:
        result = await runtime.run("用一句话介绍你自己。")
        print(result.final_response)
    finally:
        await runtime.teardown()

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
                  │  - 在每个关键节点 dispatch 事件 │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │  pyagent.hooks.manager        │ 「事件总线：唯一订阅入口」
                  │  HookManager.on / dispatch   │
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
   │           ├─ hooks.dispatch(BEFORE_LLM)
   │           ├─ LLMClient.stream_and_collect(messages, tools)
   │           │     └─ for delta in stream: on_chunk(delta)
   │           ├─ hooks.dispatch(AFTER_LLM)
   │           │
   │           ├─ response.has_tool_calls?
   │           │     │
   │           │     ├─ yes → ToolExecutor.execute_batch(tool_calls)
   │           │     │           └─ for tc: hooks.dispatch(BEFORE_TOOL / AFTER_TOOL)
   │           │     │           └─ tool_results → session.messages
   │           │     │           └─ if any terminate: stop_reason=terminated, break
   │           │     │
   │           │     └─ no  → drain steering queue → stop_reason=completed, break
   │           │
   │           └─ if steering queue non-empty: pop → 注入下一轮 user message
   │
   └─ return LoopResult(success, final_response, turns, stop_reason, error)
```

### 设计原则

1. **单向依赖**：`core` 不 import `cli`；`tools/skills/hooks` 不 import `core`；所有人都可以 import `utils`。
2. **零硬编码 selector**：工具注册表只暴露 OpenAI schema，LLM 自己决定调用哪个工具、传什么参数。
3. **可替换的 LLM**：所有 LLM 调用走 `LLMClient`，换模型只改 `settings.llm.model`，业务代码无感。
4. **Hook 即插即用**：日志 / 监控 / 权限 / 限流全部以 Hook 形式挂到 `HookManager`，启动时注册，退出时反注册。
5. **运行状态隔离**：Session 保存对话历史，Runtime 管理当前运行状态和异步资源。同一个 Runtime 只能同时执行一个 `run()`；多任务需串行化或独立实例。

## 配置

PyAgent 支持配置字段递归合并；优先级从低到高：

```
默认值 → ~/.pyagent/settings.json（用户全局）→ .pyagent/settings.json（项目本地）→ 环境变量(PYAGENT_*) → CLI 参数
```

### 配置文件

在项目根目录创建 `.pyagent/settings.json` 即可覆盖用户级同名字段（保留其它字段）：

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
  "log": {"level": "INFO"}
}
```

### 字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `llm.model` | `str` | `minimax/MiniMax-M3` | litellm 模型标识，支持 `openai/`、`anthropic/`、`gemini/` 等前缀 |
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
| `log.level` | `str` | `DEBUG` | 日志级别：`DEBUG`/`INFO`/`WARNING`/`ERROR` |

### 环境变量

所有配置项均可通过环境变量覆盖，前缀为 `PYAGENT_`，嵌套层级用 `__` 分隔：

```bash
# 覆盖 llm.model
export PYAGENT_LLM__MODEL="anthropic/claude-3-5-sonnet"

# 覆盖 agent.max_turns
export PYAGENT_AGENT__MAX_TURNS=10

# 覆盖 log.level
export PYAGENT_LOG__LEVEL=DEBUG
```

## 作为 Python SDK 使用

CLI 只是 PyAgent 的一种入口。整个 Runtime 是纯 Python 库，可以直接 `import` 后当成普通 async API 用。

### 一次性速查表

PyAgent 的公开 API 集中在 5 个模块，全部从 各子包按需导出（`pyagent` 包顶层目前仅提供 `__version__`）：

| 模块 | 代表导出 | 用途 |
|------|---------|------|
| `pyagent.config.settings` / `pyagent.config.loader` | `Settings` / `load_settings` | 多级配置加载 |
| `pyagent.core.runtime` / `pyagent.core.loop` | `Runtime` / `LoopResult`, `AgentLoop` | Agent 运行时主入口 |
| `pyagent.hooks` | `HookManager`, `Event`, `EventType`, `HookControl`, `DispatchResult` | 事件总线 |
| `pyagent.tools` / `pyagent.tools.decorators` | `Tool`, `ToolResult` / `tool` | 工具基类与装饰器 |
| `pyagent.skills` | `SkillManager`, `Skill` | 技能（prompt 片段）注册中心 |
| `pyagent.session` | `Session`, `SessionStore` | 会话与持久化 |
| `pyagent.llm.client` / `pyagent.llm.token_estimator` | `LLMClient` / `ContextUsage` | LLM 客户端 + token 监控 |

### 核心 API：`Runtime`

`Runtime` 是 SDK 唯一的主入口类：

| API | 签名 | 说明 |
|-----|------|------|
| `Runtime(settings)` | `__init__` | 用 `Settings` 构造实例 |
| `load_settings(cwd=None)` | 函数 | 按多级优先级加载 `Settings`（默认值 → 用户全局 → 项目本地 → 环境变量） |
| `await runtime.setup()` | 异步方法 | 主动触发组件初始化（LLMClient / Tool / Skill / Session / Agent / Loop） |
| `await runtime.run(query, session?, on_chunk?)` | 方法 | 执行一轮 Agent 循环，返回 `LoopResult` |
| `runtime.create_session(title, system_prompt, mode)` | 方法 | 建新会话（`mode="persistent"\|"ephemeral"`） |
| `runtime.load_session(id)` / `save_session(s)` / `delete_session(id)` | 方法 | 会话 CRUD |
| `runtime.list_sessions()` | 方法 | 列出所有持久化会话（用于 UI/CLI 列表） |
| `runtime.steer(text)` | 方法 | 在 Agent 运行期间注入改向输入（`bool` 返回是否入队） |
| `runtime.abort()` | 方法 | 立即中止当前 Agent 运行（`bool` 返回是否发送信号） |
| `runtime.is_running` | 属性 | 当前是否在执行 Agent |
| `runtime.hooks.on(EventType.X, handler)` | 方法 | 订阅事件，返回 `unsubscribe` 函数 |
| `runtime.tool_registry.register(tool)` | 方法 | 注册自定义工具 |
| `runtime.skill_manager.register(skill)` | 方法 | 注册自定义技能 |
| `await runtime.teardown()` | 异步方法 | 关闭浏览器桥并回收后台连接任务；建议放在 `finally` 中 |

### 1. 最简调用

```python
import asyncio
from pyagent.config.loader import load_settings
from pyagent.core.runtime import Runtime

async def main():
    runtime = Runtime(load_settings())
    try:
        result = await runtime.run("写一个 Python 装饰器示例")
        print(result.final_response)
        print(result.stop_reason)
    finally:
        await runtime.teardown()

asyncio.run(main())
```

第一次 `run()` 会自动 `await setup()`；也可以在同一事件循环中显式 `await runtime.setup()`。

### 2. 自定义 `Settings`

```python
from pathlib import Path
from pyagent.config.settings import Settings
from pyagent.core.runtime import Runtime

settings = Settings(
    llm={"model": "openai/gpt-4o-mini", "temperature": 0.3, "timeout": 60},
    agent={"max_turns": 10, "tool_execution": "parallel"},
    session={"enabled": True, "dir": Path("./.pyagent_sessions")},
    hooks={"enabled": True, "blocked_tools": {"run_bash"}},
)
runtime = Runtime(settings)
# 在异步应用生命周期中调用 await runtime.setup() / await runtime.teardown()
```

### 3. 流式输出与 Session

在已有异步 Runtime 中：

```python
session = runtime.create_session(title="演示会话")
result = await runtime.run(
    "解释 Python 装饰器",
    session=session,
    on_chunk=lambda delta: print(delta, end="", flush=True),
)
print(result.stop_reason)
```

`on_chunk` 是普通**同步**回调，不是协程/异步生成器。persistent 会话执行后由 Runtime 尝试自动保存；文件采用原子替换，ephemeral 会话不落盘。

### 4. Session 与并发限制

```python
persistent = runtime.create_session(title="长期对话", mode="persistent")
ephemeral = runtime.create_session(title="临时任务", mode="ephemeral")
```

同一 Runtime 同时只允许一个 `run()`：多个请求应串行执行，或使用独立 Runtime；不同 Session 不意味着能在同一个 Runtime 并发运行。

### 5. 运行期间 steer / abort

```python
import asyncio

task = asyncio.create_task(runtime.run("写一首关于秋天的短诗"))
await asyncio.sleep(1)
runtime.steer("改成五言绝句")  # 下一轮请求前注入
# runtime.abort()             # 尝试中断当前流式等待/工具执行
result = await task
```

Abort 是协作式取消，无法回滚已发生的文件系统或外部操作。LLM 输出超过 `max_tokens` 时返回 `output_truncated`，不会误报 `completed`。

### 6. 自定义工具

使用 `Tool` + `@tool` 声明工具。以下为演示，不提供真实天气数据。

```python
import asyncio
from pydantic import BaseModel, Field
from pyagent.config.loader import load_settings
from pyagent.core.runtime import Runtime
from pyagent.tools.base import Tool, ToolResult
from pyagent.tools.decorators import tool

class WeatherArgs(BaseModel):
    city: str = Field(description="城市名")

@tool("weather_demo", description="返回示例天气文本")
class WeatherDemo(Tool):
    parameters_model = WeatherArgs

    async def execute(self, tool_call_id, args, signal=None, on_update=None):
        return ToolResult(content=f"{args['city']}：这是占位信息，不是实时天气")

async def main():
    runtime = Runtime(load_settings())
    try:
        await runtime.setup()
        runtime.tool_registry.register(WeatherDemo())
        result = await runtime.run("查询北京天气")
        print(result.final_response)
    finally:
        await runtime.teardown()

asyncio.run(main())
```

自定义工具也可以放在 `~/.pyagent/tools/*.py`，由自动发现加载。工具调用会通过 BEFORE_TOOL / AFTER_TOOL Hook；权限检查失败和 Abort 会阻止新工具执行，并维护消息配对。

### 7. 自定义 Skill

推荐在项目目录 `.pyagent/skills/code-review/SKILL.md` 编写：

```markdown
---
name: code-review
description: 对代码进行审查并提出改进意见
disable-model-invocation: false
---

# 代码审查
优先核查正确性、安全和边界条件。
```

`disable-model-invocation` 与 `disable_model_invocation` 都支持。同名技能按照 **project > user > builtin** 优先级选择。手动构造时 `Skill` 需要 `frontmatter=SkillFrontmatter(...)`、`body`、`file_path`、`base_dir` 字段。

### 8. 自定义 Hook

Hook 是事件总线，统一入口 `runtime.hooks.on(EventType.X, handler)`，handler 同步/异步皆可。

#### 8.1 简单观察

```python
from pyagent.hooks.types import Event, EventType

def on_before_llm(event: Event) -> None:
    print(f"[hook] BEFORE_LLM turn={event.get('turn')} msgs={event.get('message_count')}")

def on_after_tool(event: Event) -> None:
    print(f"[hook] AFTER_TOOL {event.payload.get('tool_name')}")

unsub_llm  = runtime.hooks.on(EventType.BEFORE_LLM, on_before_llm)
unsub_tool = runtime.hooks.on(EventType.AFTER_TOOL, on_after_tool)
try:
    await runtime.run("...")
finally:
    unsub_llm(); unsub_tool()
```

#### 8.2 改写值（transform 语义）

Handler 返回非 `None` 的值，会替换当前值并继续派发；返回 `DispatchResult.value` 拿到最终值：

```python
from pyagent.hooks.types import Event, EventType

async def filter_dangerous_args(event: Event) -> dict | None:
    """若 LLM 想用 bash 执行 rm 命令，注入告警文本。"""
    args = event.get("value") or {}      # 当前 args 是 dict
    if event.get("tool_name") == "bash" and "rm -rf" in args.get("command", ""):
        return {**args, "command": "echo 'blocked: dangerous rm command'"}
    return None  # 不影响继续派发

runtime.hooks.on(EventType.BEFORE_TOOL, filter_dangerous_args)
```

#### 8.3 取消后续流程（cancel 语义）

Handler 返回 `HookControl(cancel=True)` 时，HookManager 会终止派发，且通过 `DispatchResult.cancelled` 把信号抛回 Runtime。

```python
from pyagent.hooks.types import Event, EventType, HookControl

def block_bash(event: Event) -> HookControl | None:
    if event.get("tool_name") == "bash":
        return HookControl.cancel_with("生产环境禁用 bash 工具")
    return None

runtime.hooks.on(EventType.BEFORE_TOOL, block_bash)
```

更复杂的"如果取消就把工具 result 替换成错误"用法（executor 路径）见 `pyagent.tools.executor`。

#### 8.4 订阅表（按 EventType）

事件名 / 触发时机 / payload 字段：

| EventType | 触发时机 | `payload` 关键字段 |
|-----------|---------|-------------------|
| `AGENT_START` | `runtime.run()` 入口 | `query` |
| `AGENT_END` | `runtime.run()` 出口（`finally`） | `stop_reason` |
| `AGENT_STEER` | `runtime.steer()` 被调用 | `text` |
| `AGENT_ABORT` | `runtime.abort()` 被调用 | — |
| `BEFORE_LLM` | 每轮 LLM 调用前 | `turn`, `message_count`, `value=messages` |
| `AFTER_LLM` | 每轮 LLM 调用后 | `response`, `value=response` |
| `LLM_REQUEST_ERROR` | LLM 请求失败 | `error`, `turn` |
| `BEFORE_TOOL` | 每个工具执行前 | `tool_name`, `tool_call_id`, `value=args` |
| `AFTER_TOOL` | 每个工具执行后 | `tool_name`, `tool_call_id`, `result`, `value=result` |
| `TOOL_BATCH_START` | 一批工具开始执行 | `count` |
| `TOOL_BATCH_END` | 一批工具执行完成 | `count`, `value=results` |
| `SESSION_BEFORE_COMPACT` | 上下文压缩前 | `value={"cut_point", "previous_summary", "prompt"}` |
| `SESSION_COMPACT` | 压缩完成 | `summary_tokens`, `before_usage`, `after_usage` |
| `ERROR` | 顶层未捕获异常 | `error`, `stage` |

> 所有状态变化都通过事件总线暴露。Hook 可以观察、也可以改值/取消 — SDK 使用者不必修改 Runtime 内部代码即可插入自定义横切关注点（日志、监控、权限、限流、审计、metrics 全部一套机制）。

### 9. 嵌入到 Web 服务 / 后台任务

FastAPI 为可选依赖，以下示例使用 lifespan 管理异步资源。单个 Runtime 不能并发运行，为保证示例正确，使用 `asyncio.Lock` 串行化。

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pyagent.config.loader import load_settings
from pyagent.core.runtime import Runtime

runtime = Runtime(load_settings())
run_lock = asyncio.Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await runtime.setup()
    try:
        yield
    finally:
        await runtime.teardown()

app = FastAPI(lifespan=lifespan)

@app.post("/chat")
async def chat(query: str, session_id: str | None = None):
    async with run_lock:
        session = (
            runtime.load_session(session_id)
            if session_id else runtime.create_session(title="API 对话")
        )
        result = await runtime.run(query, session=session)
    return {
        "reply": result.final_response,
        "session_id": session.metadata.id,
        "stop_reason": result.stop_reason,
    }
```

SSE 等流式接口中，`on_chunk` 必须是同步函数。可用 `queue.put_nowait(delta)` 把 chunk 投递至 `asyncio.Queue`，再从异步生成器读取并输出；不能将含 `yield` 的 async generator 直接作为 `on_chunk`。客户端断开时，应调用 `runtime.abort()` 并回收请求任务。

### 10. 一次 `run()` 的完整流程

```text
Runtime.run(query, session?, on_chunk?)
    ├─ setup() 首次自动触发
    ├─ create ephemeral session if session is None
    ├─ Agent + Loop 单轮驱动
    │     ├─ CompactionManager.maybe_compact()        按阈值触发
    │     ├─ ContextBuilder.build()                   拼 messages
    │     ├─ hooks.dispatch(BEFORE_LLM, initial=messages)
    │     ├─ LLMClient.stream_and_collect()           流式 → on_chunk
    │     ├─ hooks.dispatch(AFTER_LLM,  initial=response)
    │     ├─ 有 tool_calls → ToolExecutor.execute_batch
    │     │     ├─ dispatch(BEFORE_TOOL)               handler 可改 args
    │     │     ├─ tool.execute()
    │     │     └─ dispatch(AFTER_TOOL)
    │     ├─ 无 tool_calls → drain steering → completed
    │     └─ turn 边界检查 abort / steer
    ├─ hooks.dispatch(AGENT_END)
    └─ 自动 save_session()    # finally 兜底，失败仅 logger.warning
```

### 11. `LoopResult` 字段

`runtime.run()` 返回 `LoopResult`：

| 字段 | 类型 | 含义 |
|------|------|------|
| `success` | `bool` | 循环是否成功结束（`completed` / `terminated` 视为 True） |
| `final_response` | `str` | LLM 最后一条纯文本；`terminated` 时为最后一个工具的 content |
| `turns` | `int` | 实际循环轮次 |
| `stop_reason` | `str` | 包括 `completed`、`terminated`、`cancelled`、`max_turns`、`agent_cancelled`、`llm_cancelled`、`output_truncated`、`tool_error`、`permission_denied`、`llm_error`、`error` 等 |
| `error` | `str` | 失败时的错误描述 |

## License

MIT