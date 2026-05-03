"""管理后台 API — 用户管理与使用统计"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.auth_api import get_current_user
from app.store import user_store

router = APIRouter(prefix="/admin")


@router.get("/users")
def list_users(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    users = user_store.list_users()
    return {"users": users[offset : offset + limit], "total": len(users)}


@router.get("/users/{user_id}")
def get_user_detail(
    user_id: int,
    current_user: dict = Depends(get_current_user),
):
    user = user_store.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    events = user_store.get_user_events(user_id, limit=50)
    return {"user": user, "events": events}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_user: dict = Depends(get_current_user),
):
    ok = user_store.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"ok": True}


@router.get("/stats")
def get_stats(current_user: dict = Depends(get_current_user)):
    return user_store.get_stats()
