from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.v1 import auth_api
from app.store import user_store


@pytest.fixture()
def isolated_auth_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Isolate auth tests from the real runtime SQLite/session state."""
    existing_db = user_store._DB
    if existing_db is not None:
        existing_db.close()
    user_store._DB = None

    monkeypatch.setattr(user_store, "_db_path", lambda: tmp_path / "users.db")
    auth_api._sessions.clear()
    try:
        yield
    finally:
        db = user_store._DB
        if db is not None:
            db.close()
        user_store._DB = None
        auth_api._sessions.clear()


def test_register_rejects_non_phone_username(isolated_auth_runtime) -> None:
    with pytest.raises(HTTPException) as exc_info:
        auth_api.register(
            auth_api.RegisterRequest(
                username="abcdefghijk",
                password="123456",
                nickname="测试昵称",
            )
        )

    assert exc_info.value.status_code == 422
    assert "11 位手机号" in str(exc_info.value.detail)


def test_profile_update_and_logout_flow(isolated_auth_runtime) -> None:
    register_result = auth_api.register(
        auth_api.RegisterRequest(
            username="13900000001",
            password="123456",
            nickname="初始昵称",
        )
    )
    token = register_result.token

    current_user = auth_api.get_current_user(f"Bearer {token}")
    profile_result = auth_api.profile(current_user=current_user)

    assert profile_result["user"]["display_name"] == "初始昵称"
    assert profile_result["login_detail"]["session_count"] == 0
    assert profile_result["login_detail"]["total_speech_count"] == 0
    assert profile_result["medals"] == []
    assert profile_result["billing"]["enabled"] is False

    updated_profile = auth_api.update_profile(
        auth_api.UpdateProfileRequest(nickname="昵称改后"),
        current_user=current_user,
    )
    assert updated_profile["user"]["display_name"] == "昵称改后"

    me_result = auth_api.me(
        current_user=auth_api.get_current_user(f"Bearer {token}"),
    )
    assert me_result["user"]["display_name"] == "昵称改后"

    logout_result = auth_api.logout(f"Bearer {token}")
    assert logout_result["ok"] is True

    with pytest.raises(HTTPException) as exc_info:
        auth_api.get_current_user(f"Bearer {token}")
    assert exc_info.value.status_code == 401
