"""管理后台 API — 用户管理与使用统计"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.api.v1.admin_guard import require_management_token
from app.api.v1.auth_api import get_current_user
from app.store import user_store

router = APIRouter(prefix="/admin")


async def require_admin_access(
    request: Request,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """管理员访问控制：支持登录会话或管理令牌。"""
    if authorization:
        try:
            return get_current_user(authorization)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise

    try:
        await require_management_token(request=request, x_admin_token=x_admin_token)
        return {"id": 0, "username": "ops-admin", "display_name": "运维"}
    except HTTPException as exc:
        raise HTTPException(
            status_code=401,
            detail="请先登录，或提供有效的 X-Admin-Token",
        ) from exc


@router.get("/users")
def list_users(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin_user: dict = Depends(require_admin_access),
):
    users = user_store.list_users()
    return {"users": users[offset : offset + limit], "total": len(users)}


@router.get("/users/{user_id}")
def get_user_detail(
    user_id: int,
    admin_user: dict = Depends(require_admin_access),
):
    user = user_store.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    events = user_store.get_user_events(user_id, limit=50)
    return {"user": user, "events": events}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin_user: dict = Depends(require_admin_access),
):
    ok = user_store.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"ok": True}


@router.get("/stats")
def get_stats(admin_user: dict = Depends(require_admin_access)):
    return user_store.get_stats()
