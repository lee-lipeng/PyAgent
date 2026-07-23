"""SessionStore — 会话文件管理。

管理会话文件的保存、加载、列表、删除。
每个会话保存为 `<sessions_dir>/<id>.json`。
"""

from __future__ import annotations

import uuid
from pathlib import Path

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

    def save(self, session: Session) -> Path:
        """保存会话到文件。

        Returns:
            保存的文件路径。
        """
        path = self._dir / f"{session.metadata.id}.json"
        session.to_file(path)
        logger.debug("保存会话: %s", path)
        return path

    def load(self, session_id: str) -> Session | None:
        """加载会话，不存在返回 None。"""
        path = self._dir / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            return Session.from_file(path)
        except Exception as exc:
            logger.warning("加载会话失败 %s: %s", path, exc)
            return None

    def delete(self, session_id: str) -> bool:
        """删除会话文件。"""
        path = self._dir / f"{session_id}.json"
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
        return (self._dir / f"{session_id}.json").exists()

    def create(
        self,
        model: str = "",
        system_prompt: str = "",
        title: str = "",
        session_id: str | None = None,
        context_window: int = 0,
        compaction_threshold: float = 0.8,
    ) -> Session:
        """创建并保存新会话。

        Args:
            model: 使用的模型名。
            system_prompt: 系统提示词。
            title: 会话标题。
            session_id: 指定 ID，不传则自动生成。
            context_window: 模型上下文窗口大小（token 数）。
            compaction_threshold: 压缩触发阈值（0~1）。

        Returns:
            新创建的 Session 对象。
        """
        sid = session_id or self.generate_id()
        session = Session.create_new(
            session_id=sid,
            model=model,
            system_prompt=system_prompt,
            title=title,
            context_window=context_window,
            compaction_threshold=compaction_threshold,
        )
        self.save(session)
        return session
