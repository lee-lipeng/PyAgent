"""Session会话持久化。

保存和恢复 Agent 的对话历史，支持跨会话恢复上下文。

设计要点：
- Session 用 JSON 文件存储，每个 session 一个文件
- 包含元数据（创建时间、模型、token 用量、上下文窗口配置）和消息列表
- 消息支持 AgentMessage 丰富类型（压缩摘要、分支摘要等）
- SessionStore 管理文件的读写和列表
"""

from pyagent.session.messages import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    Message,
    ToolResultMessage,
    UserMessage,
    message_to_llm,
)
from pyagent.session.store import SessionStore
from pyagent.session.types import Session, SessionMetadata

__all__ = [
    "AgentMessage",
    "AssistantMessage",
    "BranchSummaryMessage",
    "CompactionSummaryMessage",
    "CustomMessage",
    "Message",
    "Session",
    "SessionMetadata",
    "SessionStore",
    "ToolResultMessage",
    "UserMessage",
    "message_to_llm",
]
