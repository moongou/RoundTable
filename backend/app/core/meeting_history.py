"""会议历史持久化。

为每个 session 持久化一份摘要和完整事件时间线，便于事后诊断。
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

MEETING_HISTORY_DIR = Path(__file__).resolve().parents[2] / "runtime" / "meeting_history"
MEETING_HISTORY_RETENTION_LIMIT = 10_000
MEETING_RECORDINGS_DIRNAME = "recordings"
MEETING_RECORDINGS_MANIFEST = "recordings.json"
MEETING_SCRIPT_FILENAME = "script.json"
MEETING_RUNNING_STALE_TIMEOUT_SECONDS = 600


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_summary_status(summary: dict[str, Any]) -> dict[str, Any]:
    status = str(summary.get("status") or "").strip().lower()
    if status != "running":
        return summary

    marker = _parse_iso_datetime(
        summary.get("updated_at")
        or summary.get("ended_at")
        or summary.get("started_at")
    )
    if marker is None:
        normalized = dict(summary)
        normalized["status"] = "disconnected"
        if not normalized.get("finish_reason"):
            normalized["finish_reason"] = "stale_running_session"
        if not normalized.get("ended_at"):
            normalized["ended_at"] = normalized.get("updated_at") or _utc_now_iso()
        return normalized

    age_seconds = (datetime.now(timezone.utc) - marker).total_seconds()
    if age_seconds <= MEETING_RUNNING_STALE_TIMEOUT_SECONDS:
        return summary

    normalized = dict(summary)
    normalized["status"] = "disconnected"
    if not normalized.get("finish_reason"):
        normalized["finish_reason"] = "stale_running_session"
    if not normalized.get("ended_at"):
        normalized["ended_at"] = normalized.get("updated_at") or _utc_now_iso()
    return normalized


def _sanitize_session_id(session_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", (session_id or "").strip())
    return normalized or "unknown-session"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "dict"):
        return _jsonable(value.dict())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _safe_json_load(path: Path, *, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json_file(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _sanitize_artifact_segment(value: str, *, default: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    normalized = normalized.strip("._-")
    return normalized or default


def _detect_audio_extension(extension: str | None, content_type: str | None = None) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "", (extension or "").strip().lower())
    if normalized:
        return normalized

    mapping = {
        "audio/webm": "webm",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/ogg": "ogg",
        "audio/mp4": "m4a",
        "audio/aac": "aac",
    }
    normalized_content_type = str(content_type or "").split(";", 1)[0].strip().lower()
    return mapping.get(normalized_content_type, "bin")


def _recordings_manifest_path(session_dir: Path) -> Path:
    return session_dir / MEETING_RECORDINGS_MANIFEST


def _script_path(session_dir: Path) -> Path:
    return session_dir / MEETING_SCRIPT_FILENAME


def _load_recordings_manifest(session_dir: Path) -> dict[str, Any]:
    payload = _safe_json_load(_recordings_manifest_path(session_dir), default={})
    if not isinstance(payload, dict):
        return {"recordings": []}
    recordings = payload.get("recordings")
    if not isinstance(recordings, list):
        payload["recordings"] = []
    return payload


def _recording_summary(recordings: list[dict[str, Any]]) -> tuple[int, int]:
    total_duration_ms = 0
    for item in recordings:
        duration_ms = item.get("duration_ms")
        if isinstance(duration_ms, int):
            total_duration_ms += max(0, duration_ms)
    return len(recordings), total_duration_ms


def _build_preview(entry_type: str, data: Any) -> str:
    payload = _jsonable(data)
    if isinstance(payload, dict):
        if entry_type == "message":
            source = str(payload.get("source", "") or "").strip()
            content = str(payload.get("content", "") or "").strip()
            preview = f"{source}: {content}".strip(": ")
        elif entry_type in {"human_input", "stream"}:
            speaker = str(payload.get("speaker", "") or payload.get("source", "") or "").strip()
            content = str(payload.get("content", "") or payload.get("text", "") or "").strip()
            preview = f"{speaker}: {content}".strip(": ")
        elif entry_type == "turn_change":
            preview = f"轮到 {payload.get('speaker', '')}".strip()
        elif entry_type == "human_input_requested":
            preview = f"等待 {payload.get('speaker', '')} 发言".strip()
        elif entry_type == "client_metric":
            preview = (
                f"指标 {payload.get('name', '')}={payload.get('value_ms', '')}ms"
            ).strip()
        elif entry_type == "state_change":
            preview = f"状态 {payload.get('old_label', payload.get('old_state', ''))} → {payload.get('new_label', payload.get('new_state', ''))}".strip()
        else:
            preview = json.dumps(payload, ensure_ascii=False)
    else:
        preview = str(payload)
    preview = preview.replace("\n", " ").strip()
    return preview[:180]


def _apply_timeout_fallback_from_script(
    final_stats: Any,
    script_lines: list[dict[str, Any]],
) -> Any:
    stats = _jsonable(final_stats) if final_stats is not None else {}
    if not isinstance(stats, dict):
        return stats

    floor_manager = stats.get("floor_manager")
    if not isinstance(floor_manager, dict):
        return stats

    human_skip_stats = floor_manager.get("human_skip_stats")
    if not isinstance(human_skip_stats, dict):
        return stats

    last_requested_speaker = ""
    timeout_notice_speakers: list[str] = []

    for line in script_lines:
        if not isinstance(line, dict):
            continue
        entry_type = str(line.get("entry_type", "") or "").strip()
        if entry_type == "human_input_requested":
            speaker = str(line.get("speaker", "") or "").strip()
            if speaker:
                last_requested_speaker = speaker
            continue
        if entry_type == "message":
            speaker = str(line.get("speaker", "") or "").strip()
            text = str(line.get("text", "") or "").strip()
            if speaker == "系统" and "系统不会替你跳过" in text:
                timeout_notice_speakers.append(last_requested_speaker)

    if not timeout_notice_speakers:
        return stats

    existing_timeout_count = int(human_skip_stats.get("timeout_count") or 0)
    if len(timeout_notice_speakers) > existing_timeout_count:
        human_skip_stats["timeout_count"] = len(timeout_notice_speakers)

    speaker_statuses = floor_manager.get("speaker_utterance_statuses")
    if not isinstance(speaker_statuses, dict):
        return stats

    for speaker in timeout_notice_speakers:
        display = str(speaker or "").strip()
        if not display:
            continue
        current = str(speaker_statuses.get(display, "") or "").strip()
        if current in {"", "nominated_only"}:
            speaker_statuses[display] = "timed_out"

    return stats


def _normalize_script_text(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip())


def _build_script_line(
    direction: str,
    entry_type: str,
    payload: dict[str, Any],
    *,
    timestamp: str,
    event_seq: int | None,
) -> dict[str, Any] | None:
    base_line: dict[str, Any] = {
        "timestamp": timestamp,
        "direction": direction,
        "entry_type": entry_type,
        "event_seq": event_seq,
    }

    if entry_type == "message":
        source = str(payload.get("source", "") or "").strip()
        content = str(payload.get("content", "") or "").strip()
        if not source or not content:
            return None
        agent_source = str(payload.get("agent_source", "") or "").strip()
        if source == "系统" or str(payload.get("msg_type", "") or "").strip() == "system":
            line = {
                **base_line,
                "kind": "note",
                "speaker": source,
                "text": content,
            }
        else:
            line = {
                **base_line,
                "kind": "speech",
                "speaker": source,
                "text": content,
            }
        if agent_source:
            line["agent_source"] = agent_source
        return line

    if entry_type == "human_input":
        speaker = str(payload.get("speaker", "") or "").strip()
        content = str(payload.get("content", "") or "").strip()
        if not speaker or not content:
            return None
        line = {
            **base_line,
            "kind": "speech",
            "speaker": speaker,
            "text": content,
        }
        recording = payload.get("recording")
        if isinstance(recording, dict) and recording.get("recording_id"):
            line["recording"] = _jsonable(recording)
        request_wait_ms = payload.get("request_wait_ms")
        if isinstance(request_wait_ms, int):
            line["request_wait_ms"] = request_wait_ms
        request_id = str(payload.get("request_id", "") or "").strip()
        if request_id:
            line["request_id"] = request_id
        return line

    if entry_type == "turn_change":
        speaker = str(payload.get("speaker", "") or "").strip() or "未知角色"
        line = {
            **base_line,
            "kind": "note",
            "speaker": speaker,
            "text": f"轮到 {speaker} 发言",
        }
        agent_speaker = str(payload.get("agent_speaker", "") or "").strip()
        if agent_speaker:
            line["agent_speaker"] = agent_speaker
        return line

    if entry_type == "human_input_requested":
        speaker = str(payload.get("speaker", "") or "").strip() or "用户"
        line = {
            **base_line,
            "kind": "note",
            "speaker": speaker,
            "text": f"等待 {speaker} 发言",
        }
        agent_speaker = str(payload.get("agent_speaker", "") or "").strip()
        request_reason = str(payload.get("reason", "") or "").strip()
        request_id = str(payload.get("request_id", "") or "").strip()
        request_state = str(payload.get("state", "") or "").strip()
        if agent_speaker:
            line["agent_speaker"] = agent_speaker
        if request_reason:
            line["request_reason"] = request_reason
        if request_id:
            line["request_id"] = request_id
        if request_state:
            line["request_state"] = request_state
        return line

    if entry_type == "client_metric":
        metric_name = str(payload.get("name", "") or "").strip()
        value_ms = payload.get("value_ms")
        if not metric_name or not isinstance(value_ms, int):
            return None
        speaker = str(payload.get("speaker", "") or "").strip()
        phase = str(payload.get("phase", "") or "").strip()
        detail = str(payload.get("detail", "") or "").strip()
        linked_event_seq = payload.get("event_seq")
        line = {
            **base_line,
            "kind": "note",
            "speaker": speaker or "前端指标",
            "text": f"指标 {metric_name} = {value_ms}ms",
            "metric_name": metric_name,
            "metric_value_ms": value_ms,
        }
        if phase:
            line["metric_phase"] = phase
        if detail:
            line["metric_detail"] = detail
        if isinstance(linked_event_seq, int):
            line["linked_event_seq"] = linked_event_seq
        return line

    if entry_type == "designate_speaker":
        target = str(payload.get("target", "") or payload.get("speaker", "") or "").strip() or "未知角色"
        return {
            **base_line,
            "kind": "note",
            "text": f"指定下一位发言者：{target}",
        }

    if entry_type == "end_discussion":
        speaker = str(payload.get("speaker", "") or "").strip() or "用户"
        reason = str(payload.get("reason", "") or "").strip()
        text = f"{speaker}请求结束本次讨论"
        if reason:
            text = f"{text}（{reason}）"
        return {
            **base_line,
            "kind": "note",
            "speaker": speaker,
            "text": text,
        }

    if entry_type in {"system", "error", "api_error", "ended", "pause", "resume"}:
        text = str(payload.get("message", "") or "").strip()
        if not text:
            if entry_type == "ended":
                text = "讨论结束"
            elif entry_type == "pause":
                text = "讨论已暂停"
            elif entry_type == "resume":
                text = "讨论已恢复"
            else:
                return None
        return {
            **base_line,
            "kind": "note",
            "text": text,
        }

    return None


def store_meeting_recording(
    session_id: str,
    *,
    speaker: str,
    audio_bytes: bytes,
    extension: str | None = None,
    content_type: str = "application/octet-stream",
    duration_ms: int | None = None,
    transcript: str = "",
) -> dict[str, Any]:
    safe_session_id = _sanitize_session_id(session_id)
    session_dir = MEETING_HISTORY_DIR / safe_session_id
    if not (session_dir / "summary.json").exists():
        raise FileNotFoundError("meeting_history_not_found")

    recordings_dir = session_dir / MEETING_RECORDINGS_DIRNAME
    recordings_dir.mkdir(parents=True, exist_ok=True)

    normalized_speaker = _sanitize_artifact_segment(speaker, default="speaker")
    suffix = _detect_audio_extension(extension, content_type)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    recording_id = f"{normalized_speaker}-{stamp}"
    filename = f"{recording_id}.{suffix}"
    relative_path = f"{MEETING_RECORDINGS_DIRNAME}/{filename}"
    (session_dir / relative_path).write_bytes(audio_bytes)

    entry = {
        "recording_id": recording_id,
        "speaker": (speaker or "").strip() or "用户",
        "created_at": _utc_now_iso(),
        "relative_path": relative_path,
        "content_type": content_type or "application/octet-stream",
        "extension": suffix,
        "size_bytes": len(audio_bytes),
        "duration_ms": duration_ms if isinstance(duration_ms, int) and duration_ms > 0 else None,
        "transcript_preview": (transcript or "").strip()[:180],
    }

    manifest = _load_recordings_manifest(session_dir)
    recordings = list(manifest.get("recordings") or [])
    recordings.append(entry)
    manifest.update(
        {
            "session_id": session_id,
            "safe_session_id": safe_session_id,
            "updated_at": entry["created_at"],
            "recordings": recordings,
        }
    )
    _write_json_file(_recordings_manifest_path(session_dir), manifest)
    return entry


def load_meeting_script(session_id: str) -> dict[str, Any]:
    safe_session_id = _sanitize_session_id(session_id)
    session_dir = MEETING_HISTORY_DIR / safe_session_id
    summary = _read_meeting_summary(session_id)

    script_payload = _safe_json_load(_script_path(session_dir), default={})
    if not isinstance(script_payload, dict):
        script_payload = {}

    lines = script_payload.get("lines")
    if not isinstance(lines, list):
        lines = []

    manifest = _load_recordings_manifest(session_dir)
    recordings = [item for item in manifest.get("recordings", []) if isinstance(item, dict)]

    return {
        "session_id": str(script_payload.get("session_id") or summary.get("session_id") or session_id),
        "safe_session_id": safe_session_id,
        "updated_at": script_payload.get("updated_at") or summary.get("updated_at") or summary.get("ended_at") or summary.get("started_at"),
        "topic": script_payload.get("topic") if isinstance(script_payload.get("topic"), dict) else summary.get("topic", {}),
        "participants": script_payload.get("participants") if isinstance(script_payload.get("participants"), list) else summary.get("participants", []),
        "line_count": len(lines),
        "recording_count": len(recordings),
        "lines": lines,
    }


def _sanitize_export_filename(value: str, *, default: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    normalized = normalized.strip("-._")
    return normalized or default


def _read_meeting_summary(session_id: str) -> dict[str, Any]:
    safe_session_id = _sanitize_session_id(session_id)
    summary_path = MEETING_HISTORY_DIR / safe_session_id / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError("meeting_history_not_found")
    payload = _safe_json_load(summary_path, default={})
    if not isinstance(payload, dict):
        return {}
    return _normalize_summary_status(payload)


def _export_line_record(line: dict[str, Any], *, index: int) -> dict[str, Any]:
    speaker = str(line.get("speaker", "") or "").strip()
    content = str(line.get("text", "") or "").strip()
    record = {
        "index": index,
        "timestamp": line.get("timestamp"),
        "speaker": speaker or ("系统注记" if line.get("kind") == "note" else "未知人物"),
        "content": content,
        "kind": line.get("kind") or "speech",
        "direction": line.get("direction") or "unknown",
        "entry_type": line.get("entry_type") or "unknown",
        "event_seq": line.get("event_seq"),
    }
    for key in ("agent_source", "agent_speaker", "request_reason"):
        if line.get(key) is not None:
            record[key] = line.get(key)
    for key in ("metric_name", "metric_value_ms", "metric_phase", "metric_detail"):
        if line.get(key) is not None:
            record[key] = line.get(key)
    recording = line.get("recording")
    if isinstance(recording, dict):
        record["recording"] = recording
    if line.get("echo_event_seq") is not None:
        record["echo_event_seq"] = line.get("echo_event_seq")
    if line.get("echo_timestamp") is not None:
        record["echo_timestamp"] = line.get("echo_timestamp")
    if line.get("linked_event_seq") is not None:
        record["linked_event_seq"] = line.get("linked_event_seq")
    return record


def build_meeting_script_export_payload(session_id: str) -> dict[str, Any]:
    summary = _read_meeting_summary(session_id)
    script = load_meeting_script(session_id)
    transcript = [
        _export_line_record(line, index=index)
        for index, line in enumerate(script.get("lines", []), start=1)
        if isinstance(line, dict)
    ]
    return {
        "export_version": 1,
        "exported_at": _utc_now_iso(),
        "format": "roundtable-script-json",
        "session": {
            "session_id": script.get("session_id"),
            "safe_session_id": script.get("safe_session_id"),
            "status": summary.get("status") or "unknown",
            "started_at": summary.get("started_at"),
            "ended_at": summary.get("ended_at"),
            "updated_at": script.get("updated_at") or summary.get("updated_at"),
            "topic": script.get("topic", {}),
            "participants": script.get("participants", []),
            "line_count": len(transcript),
            "recording_count": script.get("recording_count", 0),
            "recording_duration_ms": summary.get("recording_duration_ms", 0),
        },
        "transcript": transcript,
    }


def render_meeting_script_markdown(session_id: str) -> str:
    summary = _read_meeting_summary(session_id)
    script = load_meeting_script(session_id)
    topic = script.get("topic") if isinstance(script.get("topic"), dict) else {}
    title = str(topic.get("title") or script.get("session_id") or session_id)
    participants = script.get("participants") if isinstance(script.get("participants"), list) else []
    lines = [line for line in script.get("lines", []) if isinstance(line, dict)]

    output_lines = [
        f"# {title}",
        "",
        "## 会议信息",
        f"- Session ID: {script.get('session_id') or session_id}",
        f"- 状态: {summary.get('status') or 'unknown'}",
        f"- 开始时间: {summary.get('started_at') or '-'}",
        f"- 结束时间: {summary.get('ended_at') or '-'}",
        f"- 最后更新时间: {script.get('updated_at') or summary.get('updated_at') or '-'}",
        f"- 参与者: {' · '.join(str(item) for item in participants) if participants else '-'}",
        f"- 剧本行数: {len(lines)}",
        f"- 录音留存: {script.get('recording_count', 0)} 段",
        "",
        "## 完整剧本",
        "",
    ]

    if not lines:
        output_lines.append("- 当前会议没有可导出的剧本行。")
        return "\n".join(output_lines).strip() + "\n"

    for index, line in enumerate(lines, start=1):
        timestamp = str(line.get("timestamp") or "-")
        speaker = str(line.get("speaker") or "系统注记")
        content = str(line.get("text") or "").strip() or "（空内容）"
        kind = str(line.get("kind") or "speech")
        output_lines.append(f"### {index}. [{timestamp}] {speaker}")
        output_lines.append("")
        output_lines.append(content)
        extras: list[str] = [f"kind={kind}"]
        if line.get("event_seq") is not None:
            extras.append(f"event_seq={line.get('event_seq')}")
        if line.get("agent_source"):
            extras.append(f"agent_source={line.get('agent_source')}")
        if line.get("agent_speaker"):
            extras.append(f"agent_speaker={line.get('agent_speaker')}")
        if line.get("request_reason"):
            extras.append(f"request_reason={line.get('request_reason')}")
        if line.get("echo_event_seq") is not None:
            extras.append(f"echo_event_seq={line.get('echo_event_seq')}")
        recording = line.get("recording")
        if isinstance(recording, dict) and recording.get("recording_id"):
            extras.append(f"recording_id={recording.get('recording_id')}")
            if recording.get("duration_ms") is not None:
                extras.append(f"recording_duration_ms={recording.get('duration_ms')}")
        output_lines.append("")
        output_lines.append("- 元数据: " + " ｜ ".join(str(item) for item in extras))
        output_lines.append("")

    return "\n".join(output_lines).strip() + "\n"


def build_meeting_script_export(session_id: str, export_format: str) -> tuple[str, str, str]:
    normalized_format = str(export_format or "markdown").strip().lower()
    if normalized_format not in {"json", "markdown"}:
        raise ValueError("unsupported_export_format")

    script = load_meeting_script(session_id)
    topic = script.get("topic") if isinstance(script.get("topic"), dict) else {}
    base_name = _sanitize_export_filename(
        str(topic.get("title") or script.get("session_id") or session_id),
        default=_sanitize_session_id(session_id),
    )

    if normalized_format == "json":
        payload = build_meeting_script_export_payload(session_id)
        return (
            "application/json; charset=utf-8",
            f"{base_name}-script.json",
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    return (
        "text/markdown; charset=utf-8",
        f"{base_name}-script.md",
        render_meeting_script_markdown(session_id),
    )


def build_meeting_script_package(
    session_id: str,
    *,
    include_markdown: bool = True,
    include_json: bool = False,
    include_recording_manifest: bool = False,
    include_recording_audio: bool = False,
) -> tuple[str, str, bytes]:
    if not any(
        [
            include_markdown,
            include_json,
            include_recording_manifest,
            include_recording_audio,
        ]
    ):
        raise ValueError("no_export_content_selected")

    safe_session_id = _sanitize_session_id(session_id)
    session_dir = MEETING_HISTORY_DIR / safe_session_id
    if not (session_dir / "summary.json").exists():
        raise FileNotFoundError("meeting_history_not_found")

    summary = _read_meeting_summary(session_id)
    script = load_meeting_script(session_id)
    topic = script.get("topic") if isinstance(script.get("topic"), dict) else {}
    base_name = _sanitize_export_filename(
        str(topic.get("title") or script.get("session_id") or session_id),
        default=safe_session_id,
    )
    manifest = _load_recordings_manifest(session_dir)
    recordings = [item for item in manifest.get("recordings", []) if isinstance(item, dict)]

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        if include_markdown:
            archive.writestr(
                f"script/{base_name}-script.md",
                render_meeting_script_markdown(session_id),
            )

        if include_json:
            archive.writestr(
                f"script/{base_name}-script.json",
                json.dumps(
                    build_meeting_script_export_payload(session_id),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )

        if include_recording_manifest:
            archive.writestr(
                "recordings/recordings-manifest.json",
                json.dumps(
                    {
                        "export_version": 1,
                        "exported_at": _utc_now_iso(),
                        "format": "roundtable-recordings-json",
                        "session": {
                            "session_id": script.get("session_id"),
                            "safe_session_id": script.get("safe_session_id"),
                            "status": summary.get("status") or "unknown",
                            "updated_at": script.get("updated_at") or summary.get("updated_at"),
                            "recording_count": len(recordings),
                        },
                        "recordings": recordings,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )

        if include_recording_audio:
            for recording in recordings:
                relative_path = Path(str(recording.get("relative_path") or ""))
                if not relative_path.parts or relative_path.is_absolute() or ".." in relative_path.parts:
                    continue
                audio_path = session_dir / relative_path
                if not audio_path.exists() or not audio_path.is_file():
                    continue
                archive.write(audio_path, arcname=relative_path.as_posix())

    return (
        "application/zip",
        f"{base_name}-review-package.zip",
        archive_buffer.getvalue(),
    )


def _history_sort_key(summary_path: Path) -> tuple[str, float]:
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return ("", summary_path.stat().st_mtime)
    updated_at = str(payload.get("updated_at") or payload.get("ended_at") or payload.get("started_at") or "")
    return (updated_at, summary_path.stat().st_mtime)


def _prune_meeting_histories(*, keep_safe_session_id: str | None = None) -> None:
    if MEETING_HISTORY_RETENTION_LIMIT <= 0 or not MEETING_HISTORY_DIR.exists():
        return

    session_dirs: list[tuple[Path, tuple[str, float]]] = []
    for entry in MEETING_HISTORY_DIR.iterdir():
        if not entry.is_dir():
            continue
        summary_path = entry / "summary.json"
        session_dirs.append((entry, _history_sort_key(summary_path) if summary_path.exists() else ("", entry.stat().st_mtime)))

    session_dirs.sort(key=lambda item: item[1], reverse=True)
    retained = 0
    for session_dir, _sort_key in session_dirs:
        if session_dir.name == keep_safe_session_id:
            retained += 1
            continue
        retained += 1
        if retained <= MEETING_HISTORY_RETENTION_LIMIT:
            continue
        shutil.rmtree(session_dir, ignore_errors=True)


class MeetingHistoryStore:
    """持久化单场会议历史。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = (session_id or "").strip() or "unknown-session"
        self.safe_session_id = _sanitize_session_id(self.session_id)
        self.session_dir = MEETING_HISTORY_DIR / self.safe_session_id
        self.summary_path = self.session_dir / "summary.json"
        self.events_path = self.session_dir / "events.jsonl"
        self.script_path = _script_path(self.session_dir)
        self._lock = asyncio.Lock()
        self._summary: dict[str, Any] | None = None
        self._script_lines: list[dict[str, Any]] = []
        self._recordings_manifest_mtime_ns: int | None = None
        self._recording_count_cache = 0
        self._recording_duration_ms_cache = 0

    async def start(self, *, topic: Any = None, config: Any = None) -> None:
        async with self._lock:
            now = _utc_now_iso()
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self._summary = {
                "session_id": self.session_id,
                "safe_session_id": self.safe_session_id,
                "status": "running",
                "started_at": now,
                "updated_at": now,
                "ended_at": None,
                "topic": _jsonable(topic) if topic is not None else {},
                "config": _jsonable(config) if config is not None else {},
                "participants": [],
                "agent_display_map": {},
                "event_count": 0,
                "inbound_count": 0,
                "outbound_count": 0,
                "internal_count": 0,
                "last_event_type": "",
                "last_event_preview": "",
                "recording_count": 0,
                "recording_duration_ms": 0,
                "script_line_count": 0,
                "final_stats": {},
            }
            self._script_lines = []
            self._recordings_manifest_mtime_ns = None
            self._recording_count_cache = 0
            self._recording_duration_ms_cache = 0
            self.events_path.write_text("", encoding="utf-8")
            self._write_snapshot_locked()
            _prune_meeting_histories(keep_safe_session_id=self.safe_session_id)

    async def update_context(
        self,
        *,
        topic: Any = None,
        config: Any = None,
        participants: Any = None,
        agent_display_map: Any = None,
    ) -> None:
        async with self._lock:
            if self._summary is None:
                return
            if topic is not None:
                self._summary["topic"] = _jsonable(topic)
            if config is not None:
                self._summary["config"] = _jsonable(config)
            if participants is not None:
                self._summary["participants"] = _jsonable(participants)
            if agent_display_map is not None:
                self._summary["agent_display_map"] = _jsonable(agent_display_map)
            self._summary["updated_at"] = _utc_now_iso()
            self._write_snapshot_locked()

    async def get_review_messages(self) -> list[dict[str, str]]:
        """Return speech messages in user-review-compatible format."""
        async with self._lock:
            lines = list(self._script_lines)

        messages: list[dict[str, str]] = []
        for line in lines:
            kind = str(line.get("kind", "") or "").strip()
            if kind != "speech":
                continue
            speaker = str(line.get("speaker", "") or "").strip()
            text = str(line.get("text", "") or "").strip()
            if not speaker or not text:
                continue
            messages.append({
                "source": speaker,
                "content": text,
                "type": "text",
            })
        return messages

    async def append_entry(
        self,
        direction: str,
        entry_type: str,
        data: Any,
        *,
        event_seq: int | None = None,
    ) -> None:
        async with self._lock:
            if self._summary is None:
                return
            now = _utc_now_iso()
            payload = _jsonable(data)
            entry = {
                "timestamp": now,
                "direction": direction,
                "entry_type": entry_type,
                "event_seq": event_seq,
                "data": payload,
            }
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

            self._summary["event_count"] += 1
            count_key = f"{direction}_count"
            if count_key in self._summary:
                self._summary[count_key] += 1
            self._summary["updated_at"] = now
            self._summary["last_event_type"] = entry_type
            self._summary["last_event_preview"] = _build_preview(entry_type, payload)
            self._append_script_line_locked(
                direction,
                entry_type,
                payload,
                timestamp=now,
                event_seq=event_seq,
            )
            self._write_snapshot_locked()

    async def finish(self, *, status: str, reason: str = "", final_stats: Any = None) -> None:
        async with self._lock:
            if self._summary is None:
                return
            now = _utc_now_iso()
            self._summary["status"] = status
            self._summary["updated_at"] = now
            self._summary["ended_at"] = now
            if reason:
                self._summary["finish_reason"] = reason
            if final_stats is not None:
                self._summary["final_stats"] = _apply_timeout_fallback_from_script(
                    final_stats,
                    self._script_lines,
                )
            self._write_snapshot_locked()
            _prune_meeting_histories(keep_safe_session_id=self.safe_session_id)

    def _append_script_line_locked(
        self,
        direction: str,
        entry_type: str,
        payload: dict[str, Any],
        *,
        timestamp: str,
        event_seq: int | None,
    ) -> None:
        line = _build_script_line(
            direction,
            entry_type,
            payload,
            timestamp=timestamp,
            event_seq=event_seq,
        )
        if line is None:
            return

        if (
            line.get("kind") == "speech"
            and entry_type == "message"
            and self._script_lines
        ):
            last_line = self._script_lines[-1]
            if (
                last_line.get("kind") == "speech"
                and last_line.get("entry_type") == "human_input"
                and _normalize_script_text(str(last_line.get("speaker", "")))
                == _normalize_script_text(str(line.get("speaker", "")))
                and _normalize_script_text(str(last_line.get("text", "")))
                == _normalize_script_text(str(line.get("text", "")))
            ):
                last_line["echo_event_seq"] = event_seq
                last_line["echo_timestamp"] = timestamp
                return

        self._script_lines.append(line)

    def _refresh_recording_stats_locked(self) -> None:
        manifest_path = _recordings_manifest_path(self.session_dir)
        if not manifest_path.exists():
            self._recordings_manifest_mtime_ns = None
            self._recording_count_cache = 0
            self._recording_duration_ms_cache = 0
            return

        mtime_ns = manifest_path.stat().st_mtime_ns
        if self._recordings_manifest_mtime_ns == mtime_ns:
            return

        manifest = _load_recordings_manifest(self.session_dir)
        recordings = [item for item in manifest.get("recordings", []) if isinstance(item, dict)]
        recording_count, recording_duration_ms = _recording_summary(recordings)

        self._recordings_manifest_mtime_ns = mtime_ns
        self._recording_count_cache = recording_count
        self._recording_duration_ms_cache = recording_duration_ms

    def _write_snapshot_locked(self) -> None:
        self._refresh_recording_stats_locked()
        self._write_summary_locked()
        self._write_script_locked()

    def _write_summary_locked(self) -> None:
        if self._summary is None:
            return
        self._summary["recording_count"] = self._recording_count_cache
        self._summary["recording_duration_ms"] = self._recording_duration_ms_cache
        self._summary["script_line_count"] = len(self._script_lines)
        _write_json_file(self.summary_path, self._summary)

    def _write_script_locked(self) -> None:
        if self._summary is None:
            return
        payload = {
            "session_id": self.session_id,
            "safe_session_id": self.safe_session_id,
            "updated_at": self._summary.get("updated_at"),
            "topic": self._summary.get("topic", {}),
            "participants": self._summary.get("participants", []),
            "line_count": len(self._script_lines),
            "recording_count": self._recording_count_cache,
            "lines": self._script_lines,
        }
        _write_json_file(self.script_path, payload)