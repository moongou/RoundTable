from __future__ import annotations

import asyncio
import io
import json
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.history_api import router as history_router
from app.core import meeting_history as meeting_history_module
from app.core.meeting_history import MeetingHistoryStore, store_meeting_recording


def _make_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(history_router, prefix="/api/v1")
    return TestClient(app)


def test_upload_meeting_recording_persists_manifest_and_audio(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    store = MeetingHistoryStore("session-upload")
    asyncio.run(
        store.start(
            topic={"title": "测试录音上传"},
            config={"human_names": ["豆苗"]},
        )
    )

    with _make_test_client() as client:
        response = client.post(
            "/api/v1/history/sessions/session-upload/recordings",
            data={
                "speaker": "豆苗",
                "transcript": "我想把这一段录音留下来。",
                "duration_ms": "840",
            },
            files={
                "audio": ("sample.webm", b"webm-audio-bytes", "audio/webm"),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True

    recording = payload["recording"]
    manifest = json.loads(
        (tmp_path / "session-upload" / "recordings.json").read_text(encoding="utf-8")
    )

    assert manifest["recordings"][0]["recording_id"] == recording["recording_id"]
    assert manifest["recordings"][0]["speaker"] == "豆苗"
    assert (
        tmp_path / "session-upload" / recording["relative_path"]
    ).read_bytes() == b"webm-audio-bytes"


def test_get_meeting_script_returns_persisted_lines_and_recordings(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    store = MeetingHistoryStore("session-script")
    asyncio.run(
        store.start(
            topic={"title": "测试完整剧本"},
            config={"human_names": ["豆苗"]},
        )
    )

    recording = store_meeting_recording(
        "session-script",
        speaker="豆苗",
        audio_bytes=b"webm-audio-bytes",
        extension="webm",
        content_type="audio/webm",
        duration_ms=640,
        transcript="我想看看完整剧本是否能读出来。",
    )

    asyncio.run(
        store.append_entry(
            "inbound",
            "human_input",
            {
                "speaker": "豆苗",
                "content": "我想看看完整剧本是否能读出来。",
                "recording": recording,
            },
        )
    )
    asyncio.run(
        store.append_entry(
            "outbound",
            "message",
            {
                "source": "李老师",
                "content": "当然可以，我们会完整保存这段对话。",
            },
            event_seq=2,
        )
    )

    with _make_test_client() as client:
        response = client.get("/api/v1/history/sessions/session-script/script")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-script"
    assert payload["line_count"] == 2
    assert payload["recording_count"] == 1
    assert payload["lines"][0]["speaker"] == "豆苗"
    assert payload["lines"][0]["recording"]["recording_id"] == recording["recording_id"]
    assert payload["lines"][1]["speaker"] == "李老师"


def test_get_meeting_script_falls_back_when_legacy_script_file_is_missing(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    session_dir = tmp_path / "legacy-session"
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "session_id": "legacy-session",
                "safe_session_id": "legacy-session",
                "updated_at": "2026-04-26T12:00:00+00:00",
                "topic": {"title": "旧会话"},
                "participants": ["李老师", "豆苗"],
                "script_line_count": 0,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with _make_test_client() as client:
        response = client.get("/api/v1/history/sessions/legacy-session/script")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "legacy-session"
    assert payload["topic"]["title"] == "旧会话"
    assert payload["participants"] == ["李老师", "豆苗"]
    assert payload["line_count"] == 0
    assert payload["lines"] == []


def test_export_meeting_script_as_json_keeps_speaker_content_and_timestamp(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    store = MeetingHistoryStore("session-export-json")
    asyncio.run(
        store.start(
            topic={"title": "导出 JSON"},
            config={"human_names": ["豆苗"]},
        )
    )
    asyncio.run(
        store.append_entry(
            "outbound",
            "message",
            {
                "source": "李老师",
                "content": "请记下时间、人物和内容。",
            },
            event_seq=3,
        )
    )

    with _make_test_client() as client:
        response = client.get(
            "/api/v1/history/sessions/session-export-json/script/export?format=json"
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment;" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["format"] == "roundtable-script-json"
    assert payload["transcript"][0]["speaker"] == "李老师"
    assert payload["transcript"][0]["content"] == "请记下时间、人物和内容。"
    assert payload["transcript"][0]["timestamp"]


def test_export_meeting_script_as_markdown_keeps_speaker_content_and_timestamp(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    store = MeetingHistoryStore("session-export-md")
    asyncio.run(
        store.start(
            topic={"title": "导出 Markdown"},
            config={"human_names": ["豆苗"]},
        )
    )
    asyncio.run(
        store.append_entry(
            "inbound",
            "human_input",
            {
                "speaker": "豆苗",
                "content": "这是要分享给老师看的复盘稿。",
            },
            event_seq=4,
        )
    )

    with _make_test_client() as client:
        response = client.get(
            "/api/v1/history/sessions/session-export-md/script/export?format=markdown"
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment;" in response.headers["content-disposition"]
    body = response.text
    assert "[" in body and "] 豆苗" in body
    assert "这是要分享给老师看的复盘稿。" in body


def test_export_meeting_script_rejects_unsupported_format(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    session_dir = tmp_path / "session-bad-export"
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "session_id": "session-bad-export",
                "safe_session_id": "session-bad-export",
                "updated_at": "2026-04-26T12:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with _make_test_client() as client:
        response = client.get(
            "/api/v1/history/sessions/session-bad-export/script/export?format=csv"
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported_export_format"


def test_export_meeting_script_marks_stale_running_session_as_disconnected(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    session_dir = tmp_path / "stale-running-session"
    session_dir.mkdir(parents=True)
    updated_at = "2026-04-26T12:00:00+00:00"
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "session_id": "stale-running-session",
                "safe_session_id": "stale-running-session",
                "status": "running",
                "started_at": "2026-04-26T11:55:00+00:00",
                "updated_at": updated_at,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (session_dir / "script.json").write_text(
        json.dumps(
            {
                "session_id": "stale-running-session",
                "safe_session_id": "stale-running-session",
                "updated_at": updated_at,
                "topic": {"title": "陈旧运行态测试"},
                "participants": ["李老师"],
                "line_count": 0,
                "lines": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with _make_test_client() as client:
        response = client.get(
            "/api/v1/history/sessions/stale-running-session/script/export?format=json"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["status"] == "disconnected"
    assert payload["session"]["ended_at"] == updated_at


def test_export_meeting_script_package_respects_selected_contents(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    store = MeetingHistoryStore("session-export-package")
    asyncio.run(
        store.start(
            topic={"title": "打包导出"},
            config={"human_names": ["豆苗"]},
        )
    )

    recording = store_meeting_recording(
        "session-export-package",
        speaker="豆苗",
        audio_bytes=b"webm-audio-bytes",
        extension="webm",
        content_type="audio/webm",
        duration_ms=920,
        transcript="我想导出一个可选内容的复盘包。",
    )
    asyncio.run(
        store.append_entry(
            "inbound",
            "human_input",
            {
                "speaker": "豆苗",
                "content": "我想导出一个可选内容的复盘包。",
                "recording": recording,
            },
            event_seq=7,
        )
    )

    with _make_test_client() as client:
        response = client.get(
            "/api/v1/history/sessions/session-export-package/script/package"
            "?include_markdown=true"
            "&include_json=false"
            "&include_recording_manifest=false"
            "&include_recording_audio=true"
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment;" in response.headers["content-disposition"]

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    markdown_name = next(name for name in names if name.endswith("-script.md"))
    audio_name = next(name for name in names if name.endswith(".webm"))

    assert not any(name.endswith("-script.json") for name in names)
    assert "recordings/recordings-manifest.json" not in names
    assert archive.read(audio_name) == b"webm-audio-bytes"

    markdown = archive.read(markdown_name).decode("utf-8")
    assert "豆苗" in markdown
    assert "我想导出一个可选内容的复盘包。" in markdown


def test_export_meeting_script_package_rejects_empty_selection(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(meeting_history_module, "MEETING_HISTORY_DIR", tmp_path)

    session_dir = tmp_path / "session-empty-package"
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "session_id": "session-empty-package",
                "safe_session_id": "session-empty-package",
                "updated_at": "2026-04-26T12:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with _make_test_client() as client:
        response = client.get(
            "/api/v1/history/sessions/session-empty-package/script/package"
            "?include_markdown=false"
            "&include_json=false"
            "&include_recording_manifest=false"
            "&include_recording_audio=false"
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "no_export_content_selected"