"""讨论会话持久化存储。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

from app.models.session import SessionResponse

logger = logging.getLogger(__name__)


def _safe_session_filename(session_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", (session_id or "").strip())
    return normalized or "unknown-session"


def _extract_numeric_suffix(session_id: str) -> int:
    match = re.match(r"^session-(\d+)$", (session_id or "").strip())
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


class SessionStore:
    """基于 JSON 文件的轻量会话存储。"""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict[str, SessionResponse]:
        sessions: dict[str, SessionResponse] = {}
        if not self.storage_dir.exists():
            return sessions

        for file_path in sorted(self.storage_dir.glob("*.json")):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                session = SessionResponse.model_validate(payload)
                sessions[session.session_id] = session
            except Exception as exc:
                logger.warning("跳过损坏会话文件 %s: %s", file_path, exc)
        return sessions

    def save(self, session: SessionResponse) -> None:
        file_path = self._path_for_session(session.session_id)
        tmp_path = file_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(session.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(file_path)

    def delete(self, session_id: str) -> None:
        file_path = self._path_for_session(session_id)
        if file_path.exists():
            file_path.unlink()

    def next_session_id(self, existing_session_ids: Iterable[str]) -> str:
        max_index = 0
        for session_id in existing_session_ids:
            max_index = max(max_index, _extract_numeric_suffix(session_id))
        return f"session-{max_index + 1}"

    def _path_for_session(self, session_id: str) -> Path:
        return self.storage_dir / f"{_safe_session_filename(session_id)}.json"
