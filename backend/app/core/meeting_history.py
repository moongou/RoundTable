"""会议历史持久化。

为每个 session 持久化一份摘要和完整事件时间线，便于事后诊断。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

MEETING_HISTORY_DIR = Path(__file__).resolve().parents[2] / "runtime" / "meeting_history"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        elif entry_type == "state_change":
            preview = f"状态 {payload.get('old_label', payload.get('old_state', ''))} → {payload.get('new_label', payload.get('new_state', ''))}".strip()
        else:
            preview = json.dumps(payload, ensure_ascii=False)
    else:
        preview = str(payload)
    preview = preview.replace("\n", " ").strip()
    return preview[:180]


class MeetingHistoryStore:
    """持久化单场会议历史。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = (session_id or "").strip() or "unknown-session"
        self.safe_session_id = _sanitize_session_id(self.session_id)
        self.session_dir = MEETING_HISTORY_DIR / self.safe_session_id
        self.summary_path = self.session_dir / "summary.json"
        self.events_path = self.session_dir / "events.jsonl"
        self._lock = asyncio.Lock()
        self._summary: dict[str, Any] | None = None

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
                "final_stats": {},
            }
            self.events_path.write_text("", encoding="utf-8")
            self._write_summary_locked()

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
            self._write_summary_locked()

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
            self._write_summary_locked()

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
                self._summary["final_stats"] = _jsonable(final_stats)
            self._write_summary_locked()

    def _write_summary_locked(self) -> None:
        if self._summary is None:
            return
        self.summary_path.write_text(
            json.dumps(self._summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )