"""用户认证 API — 当前使用用户名+密码，预留 Authing.cn SSO 接入点"""

import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field

from app.store import user_store

router = APIRouter(prefix="/auth")

# 会话 token → user_id 映射 (内存缓存 + 定期清理)
_sessions: dict[str, dict] = {}
_PHONE_USERNAME_RE = re.compile(r"^1\d{10}$")

# ============================================================
# Authing.cn 预留接入点
# 将来接入时，添加以下路由：
#   GET  /auth/authing/login-url  → 返回 Authing.cn 授权URL
#   POST /auth/authing/callback   → 处理 Authing.cn OAuth 回调
#   POST /auth/authing/link       → 绑定已有账号到 Authing.cn
# 配置从环境变量读取：
#   AUTHING_APP_ID, AUTHING_APP_SECRET, AUTHING_REDIRECT_URI
# ============================================================


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=11, max_length=11)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=32)
    nickname: str = Field(default="", max_length=32)


class LoginRequest(BaseModel):
    username: str
    password: str


class UpdateProfileRequest(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=32)


class AuthResponse(BaseModel):
    token: str
    user: dict


def _create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"user_id": user_id, "created_at": time.time()}
    # 清理过期 session（超过7天）
    now = time.time()
    expired = [t for t, s in _sessions.items() if now - s["created_at"] > 604800]
    for t in expired:
        _sessions.pop(t, None)
    return token


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """从 Authorization header 解析当前用户"""
    if not authorization:
        raise HTTPException(status_code=401, detail="请先登录")
    token = authorization.replace("Bearer ", "")
    session = _sessions.get(token)
    if not session:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = user_store.get_user_by_id(session["user_id"])
    if not user:
        _sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """从 Authorization header 解析当前用户（可选，未登录返回 None）"""
    if not authorization:
        return None
    token = authorization.replace("Bearer ", "")
    session = _sessions.get(token)
    if not session:
        return None
    user = user_store.get_user_by_id(session["user_id"])
    return user


def get_admin_user(current_user: dict = Depends(get_current_user)) -> dict:
    """管理员检查（当前阶段所有已登录用户均为管理员）"""
    return current_user


@router.post("/register")
def register(body: RegisterRequest) -> AuthResponse:
    normalized_username = (body.username or "").strip()
    if not _PHONE_USERNAME_RE.fullmatch(normalized_username):
        raise HTTPException(status_code=422, detail="用户名必须为 11 位手机号")
    nickname = (body.nickname or body.display_name or "").strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="昵称不能为空")
    try:
        user = user_store.create_user(normalized_username, body.password, nickname)
    except ValueError as e:
        detail = str(e)
        status_code = 409 if "已存在" in detail else 422
        raise HTTPException(status_code=status_code, detail=detail)
    user_store.record_event(user["id"], "register")
    token = _create_session(user["id"])
    return AuthResponse(token=token, user=user)


@router.post("/login")
def login(body: LoginRequest) -> AuthResponse:
    user = user_store.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user_store.record_event(user["id"], "login")
    token = _create_session(user["id"])
    return AuthResponse(token=token, user=user)


@router.post("/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization:
        token = authorization.replace("Bearer ", "")
        _sessions.pop(token, None)
    return {"ok": True}


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)) -> dict:
    return {"user": current_user}


@router.get("/profile")
def profile(current_user: dict = Depends(get_current_user)) -> dict:
    user = user_store.get_user_by_id(current_user["id"])
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {
        "user": user,
        "login_detail": {
            "created_at": user.get("created_at"),
            "last_login": user.get("last_login"),
            "session_count": user.get("session_count", 0),
            "total_speech_count": user.get("total_speech_count", 0),
            "total_online_ms": user.get("total_online_ms", 0),
        },
        "medals": [],
        "billing": {
            "enabled": False,
            "currency": "CNY",
            "balance": 0,
            "message": "账单系统尚未启用",
        },
    }


@router.patch("/profile")
def update_profile(
    body: UpdateProfileRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    nickname = (body.nickname or "").strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="昵称不能为空")
    try:
        updated = user_store.update_display_name(current_user["id"], nickname)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="用户不存在")
    user_store.record_event(current_user["id"], "nickname_update", nickname)
    return {"user": updated}
