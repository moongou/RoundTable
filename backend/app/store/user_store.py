"""用户存储 — SQLite 持久化，为将来 Authing.cn 接入做准备"""

import hashlib
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DB: Optional[sqlite3.Connection] = None
_PHONE_USERNAME_RE = re.compile(r"^1\d{10}$")
_ADMIN_NO_PASSWORD_SENTINEL = "__admin_no_password__"
_LOCAL_TEST_USERNAME = "a"
_LOCAL_TEST_NO_PASSWORD_SENTINEL = "__local_test_no_password__"

# 将来接入 Authing.cn 时，在此处添加 OAuth/OIDC 配置
# AUTHING_APP_ID = ""
# AUTHING_APP_SECRET = ""
# AUTHING_REDIRECT_URI = ""


def _db_path() -> Path:
    base = Path(__file__).resolve().parent.parent.parent / "runtime"
    base.mkdir(parents=True, exist_ok=True)
    return base / "users.db"


def _get_db() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        _DB = sqlite3.connect(str(_db_path()), check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        _DB.execute("PRAGMA journal_mode=WAL")
        _DB.execute("PRAGMA foreign_keys=ON")
        _ensure_schema(_DB)
    return _DB


def _ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT    NOT NULL UNIQUE,
            password    TEXT    NOT NULL,
            display_name TEXT   NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL,
            last_login  TEXT,
            -- 使用统计
            session_count   INTEGER NOT NULL DEFAULT 0,
            total_speech_count INTEGER NOT NULL DEFAULT 0,
            total_online_ms  INTEGER NOT NULL DEFAULT 0,
            -- Authing.cn 预留字段
            authing_uid  TEXT,
            authing_linked INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            event_type  TEXT    NOT NULL,
            event_data  TEXT,
            created_at  TEXT    NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    db.commit()
    _ensure_builtin_users(db)


def _ensure_builtin_user(
    db: sqlite3.Connection,
    *,
    username: str,
    password: str,
    display_name: str,
) -> None:
    row = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row:
        return
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO users (username, password, display_name, created_at) VALUES (?, ?, ?, ?)",
        (username, password, display_name, now),
    )


def _ensure_builtin_users(db: sqlite3.Connection) -> None:
    """Auto-create local development users that can bypass passwords."""
    _ensure_builtin_user(
        db,
        username="admin",
        password=_ADMIN_NO_PASSWORD_SENTINEL,
        display_name="管理员",
    )
    _ensure_builtin_user(
        db,
        username=_LOCAL_TEST_USERNAME,
        password=_LOCAL_TEST_NO_PASSWORD_SENTINEL,
        display_name="测试用户a",
    )
    db.commit()


def _hash_password(password: str) -> str:
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return salt.hex() + ":" + key.hex()


def _verify_password(password: str, stored: str) -> bool:
    salt_hex, key_hex = stored.split(":")
    salt = bytes.fromhex(salt_hex)
    key = bytes.fromhex(key_hex)
    new_key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return new_key == key


def _normalize_phone_username(username: str) -> str:
    normalized = (username or "").strip()
    if not _PHONE_USERNAME_RE.fullmatch(normalized):
        raise ValueError("用户名必须为 11 位手机号")
    return normalized


def create_user(username: str, password: str, display_name: str = "") -> dict:
    db = _get_db()
    normalized_username = _normalize_phone_username(username)
    normalized_display_name = (display_name or normalized_username).strip()
    if not normalized_display_name:
        raise ValueError("昵称不能为空")
    now = datetime.now(timezone.utc).isoformat()
    hashed = _hash_password(password)
    try:
        cur = db.execute(
            "INSERT INTO users (username, password, display_name, created_at) VALUES (?, ?, ?, ?)",
            (normalized_username, hashed, normalized_display_name, now),
        )
        db.commit()
        return {
            "id": cur.lastrowid,
            "username": normalized_username,
            "display_name": normalized_display_name,
            "created_at": now,
        }
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在")


def authenticate(username: str, password: str) -> Optional[dict]:
    db = _get_db()
    normalized_username = (username or "").strip()
    row = db.execute("SELECT * FROM users WHERE username = ?", (normalized_username,)).fetchone()
    if not row:
        return None
    # Built-in local development users bypass passwords.
    if row["password"] in {
        _ADMIN_NO_PASSWORD_SENTINEL,
        _LOCAL_TEST_NO_PASSWORD_SENTINEL,
    }:
        pass
    elif not _verify_password(password, row["password"]):
        return None
    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, row["id"]))
    db.commit()
    return _row_to_dict(row)


def update_display_name(user_id: int, display_name: str) -> Optional[dict]:
    db = _get_db()
    normalized_display_name = (display_name or "").strip()
    if not normalized_display_name:
        raise ValueError("昵称不能为空")
    db.execute(
        "UPDATE users SET display_name = ? WHERE id = ?",
        (normalized_display_name, user_id),
    )
    db.commit()
    return get_user_by_id(user_id)


def get_user_by_id(user_id: int) -> Optional[dict]:
    db = _get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_user_by_username(username: str) -> Optional[dict]:
    db = _get_db()
    row = db.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
    return _row_to_dict(row) if row else None


def list_users() -> list[dict]:
    db = _get_db()
    rows = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def increment_session(user_id: int) -> None:
    db = _get_db()
    db.execute("UPDATE users SET session_count = session_count + 1 WHERE id = ?", (user_id,))
    db.commit()


def add_speech_count(user_id: int, count: int = 1) -> None:
    db = _get_db()
    db.execute("UPDATE users SET total_speech_count = total_speech_count + ? WHERE id = ?", (count, user_id))
    db.commit()


def add_online_time(user_id: int, ms_: int) -> None:
    db = _get_db()
    db.execute("UPDATE users SET total_online_ms = total_online_ms + ? WHERE id = ?", (ms_, user_id))
    db.commit()


def delete_user(user_id: int) -> bool:
    db = _get_db()
    db.execute("DELETE FROM user_events WHERE user_id = ?", (user_id,))
    cur = db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return cur.rowcount > 0


def record_event(user_id: int, event_type: str, event_data: str = "") -> None:
    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO user_events (user_id, event_type, event_data, created_at) VALUES (?, ?, ?, ?)",
        (user_id, event_type, event_data, now),
    )
    db.commit()


def get_user_events(user_id: int, limit: int = 50) -> list[dict]:
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM user_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    db = _get_db()
    total = db.execute("SELECT COUNT(*) as n FROM users").fetchone()["n"]
    active = db.execute("SELECT COUNT(*) as n FROM users WHERE last_login > datetime('now', '-7 days')").fetchone()["n"]
    total_sessions = db.execute("SELECT COALESCE(SUM(session_count), 0) as n FROM users").fetchone()["n"]
    return {"total_users": total, "active_users_7d": active, "total_sessions": total_sessions}


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("password", None)
    return d
