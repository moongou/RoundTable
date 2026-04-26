from __future__ import annotations

import json

import pytest

from app.core import meeting_history as meeting_history_module
from app.core.meeting_history import MeetingHistoryStore, store_meeting_recording


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

    script = json.loads((tmp_path / "session-123" / "script.json").read_text(encoding="utf-8"))

    assert summary["script_line_count"] == 2
    assert script["line_count"] == 2
    assert script["lines"][0]["speaker"] == "李老师"
    assert script["lines"][1]["speaker"] == "豆苗"


@pytest.mark.asyncio
async def test_meeting_history_store_retains_only_latest_ten_sessions(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_RETENTION_LIMIT", 10)

    for index in range(10):
      session_dir = tmp_path / f"session-{index}"
      session_dir.mkdir(parents=True)
      payload = {
          "session_id": f"session-{index}",
          "safe_session_id": f"session-{index}",
          "status": "completed",
          "started_at": f"2026-04-26T10:{index:02d}:00+00:00",
          "updated_at": f"2026-04-26T10:{index:02d}:30+00:00",
      }
      (session_dir / "summary.json").write_text(
          json.dumps(payload, ensure_ascii=False, indent=2),
          encoding="utf-8",
      )

    store = MeetingHistoryStore("session-new")
    await store.start(topic={"title": "新会议"}, config={})

    session_names = sorted(path.name for path in tmp_path.iterdir() if path.is_dir())

    assert len(session_names) == 10
    assert "session-new" in session_names
    assert "session-0" not in session_names


@pytest.mark.asyncio
async def test_meeting_history_store_persists_recordings_and_merges_human_echo(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    store = MeetingHistoryStore("session-456")
    await store.start(topic={"title": "在家上学"}, config={"human_names": ["豆苗"]})

    recording = store_meeting_recording(
        "session-456",
        speaker="豆苗",
        audio_bytes=b"webm-audio-bytes",
        extension="webm",
        content_type="audio/webm",
        duration_ms=840,
        transcript="我觉得学校里更容易交朋友。",
    )

    await store.append_entry(
        "inbound",
        "human_input",
        {
            "speaker": "豆苗",
            "content": "我觉得学校里更容易交朋友。",
            "recording": recording,
        },
    )
    await store.append_entry(
        "outbound",
        "message",
        {
            "source": "豆苗",
            "content": "我觉得学校里更容易交朋友。",
            "msg_type": "text",
        },
        event_seq=12,
    )
    await store.finish(status="completed", reason="ended")

    summary = json.loads((tmp_path / "session-456" / "summary.json").read_text(encoding="utf-8"))
    script = json.loads((tmp_path / "session-456" / "script.json").read_text(encoding="utf-8"))
    recordings = json.loads((tmp_path / "session-456" / "recordings.json").read_text(encoding="utf-8"))

    assert summary["recording_count"] == 1
    assert summary["recording_duration_ms"] == 840
    assert summary["script_line_count"] == 1

    assert len(recordings["recordings"]) == 1
    assert recordings["recordings"][0]["recording_id"] == recording["recording_id"]
    assert (tmp_path / "session-456" / recording["relative_path"]).read_bytes() == b"webm-audio-bytes"

    assert script["line_count"] == 1
    assert script["lines"][0]["speaker"] == "豆苗"
    assert script["lines"][0]["recording"]["recording_id"] == recording["recording_id"]
    assert script["lines"][0]["echo_event_seq"] == 12