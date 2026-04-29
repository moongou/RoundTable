from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.admin_guard import require_management_token
from app.config import settings


def _make_request(*, client_host: str, host: str = "localhost:8001", origin: str = "") -> Request:
    headers = [(b"host", host.encode("utf-8"))]
    if origin:
        headers.append((b"origin", origin.encode("utf-8")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/config/update",
        "headers": headers,
        "scheme": "http",
        "query_string": b"",
        "server": ("localhost", 8001),
        "client": (client_host, 53210),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _restore_settings() -> None:
    tracked = {
        "management_api_token": getattr(settings, "management_api_token"),
        "management_auth_enforced": getattr(settings, "management_auth_enforced"),
        "debug": getattr(settings, "debug"),
    }
    try:
        yield
    finally:
        for key, value in tracked.items():
            object.__setattr__(settings, key, value)


@pytest.mark.asyncio
async def test_guard_allows_when_auth_not_required() -> None:
    object.__setattr__(settings, "management_api_token", "")
    object.__setattr__(settings, "management_auth_enforced", False)

    request = _make_request(client_host="203.0.113.10")
    await require_management_token(request, x_admin_token=None)


@pytest.mark.asyncio
async def test_guard_requires_token_for_remote_request() -> None:
    object.__setattr__(settings, "management_api_token", "rt-admin-token")
    object.__setattr__(settings, "management_auth_enforced", True)
    object.__setattr__(settings, "debug", False)

    request = _make_request(client_host="203.0.113.10")
    with pytest.raises(HTTPException) as exc:
        await require_management_token(request, x_admin_token="wrong-token")

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_guard_allows_debug_loopback_without_token() -> None:
    object.__setattr__(settings, "management_api_token", "rt-admin-token")
    object.__setattr__(settings, "management_auth_enforced", True)
    object.__setattr__(settings, "debug", True)

    request = _make_request(
        client_host="127.0.0.1",
        host="127.0.0.1:8001",
        origin="http://127.0.0.1:8001",
    )
    await require_management_token(request, x_admin_token=None)


@pytest.mark.asyncio
async def test_guard_allows_valid_token() -> None:
    object.__setattr__(settings, "management_api_token", "rt-admin-token")
    object.__setattr__(settings, "management_auth_enforced", True)
    object.__setattr__(settings, "debug", False)

    request = _make_request(client_host="203.0.113.10")
    await require_management_token(request, x_admin_token="rt-admin-token")
