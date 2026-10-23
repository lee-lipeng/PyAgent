"""会话原子写入回归：失败不能破坏先前有效的存储内容。"""

import pytest

from pyagent.session.store import SessionStore
from pyagent.session.types import Session


def test_atomic_save_replaces_previous_session(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.create_new("valid-1")
    store.save(session)
    session.metadata.title = "更新后"
    result = store.save(session)

    assert result == tmp_path / "sessions" / "valid-1.json"
    assert store.load("valid-1").metadata.title == "更新后"
    assert [p.name for p in result.parent.iterdir()] == ["valid-1.json"]


def test_atomic_save_preserves_existing_data_on_failed_serialization(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    session = Session.create_new("valid-2")
    path = store.save(session)
    original = path.read_bytes()

    def write_partial_and_fail(self, destination):
        destination.write_text('{"broken":', encoding="utf-8")
        raise OSError("write failed")

    monkeypatch.setattr(Session, "to_file", write_partial_and_fail)
    with pytest.raises(OSError, match="write failed"):
        store.save(session)

    assert path.read_bytes() == original
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_atomic_save_cleans_temp_file_when_replace_fails(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    session = Session.create_new("valid-3")
    path = store.save(session)
    original = path.read_bytes()

    def fail_replace(*args):
        raise OSError("rename failed")

    monkeypatch.setattr("pyagent.session.store.os.replace", fail_replace)
    with pytest.raises(OSError, match="rename failed"):
        store.save(session)

    assert path.read_bytes() == original
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_ephemeral_session_does_not_touch_disk(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = Session.create_new("ephemeral", mode="ephemeral")
    assert store.save(session) is None
    assert list((tmp_path / "sessions").iterdir()) == []
