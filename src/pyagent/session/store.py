"""SessionStore — 会话文件管理。

管理会话文件的保存、加载、列表、删除。
每个会话保存为 ``<sessions_dir>/<id>.json``。

借鉴 Pi Agent 设计：
- SessionStore 负责 persistent 会话的落盘 / 恢复
- ephemeral session（``mode="ephemeral"``）不经过本 store，纯内存单次任务
- 所有 ``save/load/delete`` 调用对 ephemeral 会话都是 no-op
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from pyagent.session.types import Session, SessionMetadata
from pyagent.utils.logger import get_logger

logger = get_logger(__name__)


class SessionStore:
    """会话存储管理器。

    Args:
        sessions_dir: 会话文件保存目录。
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def generate_id(self) -> str:
        """生成新的会话 ID。"""
        return uuid.uuid4().hex[:12]

    def _session_path(self, session_id: str) -> Path:
        """约束会话 ID 为安全文件名，防止越界读取或删除文件。"""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            raise ValueError("无效会话 ID：仅允许字母、数字、下划线及连字符")
        return self._dir / f"{session_id}.json"

    def save(self, session: Session) -> Path | None:
        """保存会话到文件。

        ephemeral 会话不落盘，直接返回 None。

        Returns:
            保存的文件路径；ephemeral 会话返回 None。
        """
        if session.mode == "ephemeral":
            logger.debug("ephemeral 会话跳过保存: %s", session.metadata.id)
            return None

        path = self._session_path(session.metadata.id)
        # 临时文件与目标文件处于同一目录，写入成功后原子替换旧会话。
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{session.metadata.id}.", suffix=".tmp", dir=self._dir
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            session.to_file(temp_path)
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

        logger.debug("保存会话: %s", path)
        return path

    def load(self, session_id: str) -> Session | None:
        """加载会话，不存在返回 None。"""
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            return Session.from_file(path)
        except Exception as exc:
            logger.warning("加载会话失败 %s: %s", path, exc)
            return None

    def delete(self, session_id: str) -> bool:
        """删除会话文件。"""
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            logger.debug("删除会话: %s", path)
            return True
        return False

    def list_sessions(self) -> list[SessionMetadata]:
        """列出所有会话的元数据，按更新时间降序。"""
        metadatas: list[SessionMetadata] = []
        for path in self._dir.glob("*.json"):
            try:
                session = Session.from_file(path)
                metadatas.append(session.metadata)
            except Exception as exc:
                logger.warning("读取会话元数据失败 %s: %s", path, exc)
        # 按更新时间降序
        metadatas.sort(key=lambda m: m.updated_at, reverse=True)
        return metadatas

    def exists(self, session_id: str) -> bool:
        """检查会话是否存在。"""
        return self._session_path(session_id).exists()

    def create(
        self,
        model: str = "",
        system_prompt: str = "",
        title: str = "",
        session_id: str | None = None,
        context_window: int = 0,
        compaction_threshold: float = 0.8,
        mode: Literal["persistent", "ephemeral"] = "persistent",
    ) -> Session:
        """创建新会话。

        Args:
            model: 使用的模型名。
            system_prompt: 系统提示词。
            title: 会话标题。
            session_id: 指定 ID，不传则自动生成。
            context_window: 模型上下文窗口大小（token 数）。
            compaction_threshold: 压缩触发阈值（0~1）。
            mode: 持久化模式（默认 ``persistent``）。
                ``ephemeral`` 时不落盘，只返回内存对象。

        Returns:
            新创建的 Session 对象。
        """
        sid = session_id or self.generate_id()
        self._session_path(sid)
        session = Session.create_new(
            session_id=sid,
            model=model,
            system_prompt=system_prompt,
            title=title,
            context_window=context_window,
            compaction_threshold=compaction_threshold,
            mode=mode,
        )
        return session
