from __future__ import annotations

import json

import pytest

from app.core import meeting_history as meeting_history_module
from app.core.meeting_history import MeetingHistoryStore


@pytest.mark.asyncio
async def test_meeting_history_store_persists_summary_and_timeline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    store = MeetingHistoryStore("session-123")
    await store.start(
        topic={"title": "习惯是怎么形成的"},
        config={"human_names": ["豆苗"]},
    )
    await store.update_context(
        participants=["李老师", "巴甫洛夫", "豆苗"],
        agent_display_map={"moderator": "李老师", "pavlov": "巴甫洛夫"},
    )
    await store.append_entry(
        "outbound",
        "message",
        {"source": "李老师", "content": "我们先从生活里的小习惯说起。"},
        event_seq=1,
    )
    await store.append_entry(
        "inbound",
        "human_input",
        {"speaker": "豆苗", "content": "我每天放学都会先洗手。"},
    )
    await store.finish(status="completed", reason="ended")

    summary = json.loads((tmp_path / "session-123" / "summary.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (tmp_path / "session-123" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert summary["status"] == "completed"
    assert summary["event_count"] == 2
    assert summary["outbound_count"] == 1
    assert summary["inbound_count"] == 1
    assert summary["participants"] == ["李老师", "巴甫洛夫", "豆苗"]
    assert summary["last_event_type"] == "human_input"
    assert len(events) == 2
    assert events[0]["event_seq"] == 1
    assert events[0]["entry_type"] == "message"
    assert events[1]["entry_type"] == "human_input"