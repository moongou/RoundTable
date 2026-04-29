"""管理接口访问控制。"""

from __future__ import annotations

import ipaddress

from fastapi import Header, HTTPException, Request, status

from app.config import settings


def _is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    if not normalized:
        return False
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_loopback_request(request: Request) -> bool:
    # 若部署在反向代理后，优先使用首个 X-Forwarded-For。
    forwarded_for = str(request.headers.get("x-forwarded-for", "") or "").split(",")[0].strip()
    if forwarded_for:
        return _is_loopback_host(forwarded_for)
    client_host = request.client.host if request.client else ""
    return _is_loopback_host(client_host)


def _is_same_origin_request(request: Request) -> bool:
    origin = (request.headers.get("origin", "") or "").strip()
    host = (request.headers.get("host", "") or "").strip()
    if not origin or not host:
        return False
    return origin == f"{request.url.scheme}://{host}"


async def require_management_token(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """保护高风险管理接口。"""
    if not settings.management_auth_required:
        return

    expected_token = (settings.management_api_token or "").strip()
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理接口鉴权已启用，但未配置 MANAGEMENT_API_TOKEN",
        )

    if (x_admin_token or "").strip() == expected_token:
        return

    # 本地开发允许同源或回环请求免 token，避免阻断本机调试流程。
    if settings.debug and (_is_loopback_request(request) or _is_same_origin_request(request)):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="管理接口鉴权失败，请在请求头提供有效的 X-Admin-Token",
    )
