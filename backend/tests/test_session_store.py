from __future__ import annotations

from app.core.session_store import SessionStore
from app.models.session import DiscussionStatus, Participant, ParticipantType, SessionResponse, Topic


def _sample_session(session_id: str) -> SessionResponse:
    return SessionResponse(
        session_id=session_id,
        topic=Topic(
            id="topic-1",
            title="在家上学好不好",
            description="测试话题",
            category="education",
        ),
        participants=[
            Participant(name="李老师", type=ParticipantType.MODERATOR),
            Participant(name="豆苗", type=ParticipantType.HUMAN),
        ],
        status=DiscussionStatus.WAITING,
        max_turns=24,
    )


def test_session_store_round_trip(tmp_path) -> None:
    store = SessionStore(tmp_path)
    created = _sample_session("session-7")
    store.save(created)

    loaded = store.load_all()
    assert set(loaded.keys()) == {"session-7"}
    assert loaded["session-7"].topic.title == "在家上学好不好"


def test_session_store_delete(tmp_path) -> None:
    store = SessionStore(tmp_path)
    store.save(_sample_session("session-2"))
    store.delete("session-2")

    loaded = store.load_all()
    assert loaded == {}


def test_session_store_next_session_id_uses_max_suffix(tmp_path) -> None:
    store = SessionStore(tmp_path)
    next_id = store.next_session_id(["session-1", "session-9", "custom-id", "session-3"])
    assert next_id == "session-10"
