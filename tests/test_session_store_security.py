"""会话 ID 必须只映射到会话目录内部的 JSON 文件。"""

import pytest

from pyagent.session.store import SessionStore
from pyagent.session.types import Session


def test_reject_invalid_session_ids(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    for session_id in ("../outside", "..", "a/b", r"a\\b", "", "a.json/../../outside"):
        with pytest.raises(ValueError, match="无效会话 ID"):
            store.load(session_id)
        with pytest.raises(ValueError, match="无效会话 ID"):
            store.delete(session_id)
        with pytest.raises(ValueError, match="无效会话 ID"):
            store.exists(session_id)

    with pytest.raises(ValueError, match="无效会话 ID"):
        store.save(Session.create_new("../outside"))


def test_valid_session_id_can_be_saved_and_loaded(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = store.create(session_id="run_01-a")
    path = store.save(session)
    assert path == tmp_path / "sessions" / "run_01-a.json"
    assert store.load("run_01-a").metadata.id == "run_01-a"
    assert store.delete("run_01-a")
