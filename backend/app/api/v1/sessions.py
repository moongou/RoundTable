"""讨论会话 API

创建和管理讨论会话。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.agents.character_templates import load_all_templates
from app.agents.human_proxy import clear_human_queues, create_human_proxy
from app.agents.moderator import create_moderator
from app.agents.virtual_character import create_virtual_character, create_thinker_agent
from app.core.floor_manager import FloorManager
from app.core.llm_factory import create_character_client, create_moderator_client
from app.core.safety_filter import SafetyFilter
from app.core.thinkers import get_thinker
from app.core.topics import get_topic_by_id
from app.core.turn_scheduler import create_discussion_team
from app.models.session import (
    CreateSessionRequest,
    DiscussionStatus,
    Participant,
    ParticipantType,
    SessionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])

# 内存中的会话存储（Phase 1 简单实现，Phase 3 迁移到数据库）
_sessions: dict[str, SessionResponse] = {}
_floor_managers: dict[str, FloorManager] = {}


@router.post("/", response_model=SessionResponse)
async def create_session(request: CreateSessionRequest):
    """创建一个新的讨论会话。"""
    # 验证话题存在
    topic = get_topic_by_id(request.topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail=f"话题 '{request.topic_id}' 不存在")

    # 验证角色存在
    templates = load_all_templates()
    for char_id in request.character_ids:
        if char_id not in templates:
            raise HTTPException(status_code=404, detail=f"角色 '{char_id}' 不存在")

    # 验证思想家存在
    for tid in request.thinker_ids:
        if not get_thinker(tid):
            raise HTTPException(status_code=404, detail=f"思想家 '{tid}' 不存在")

    # 创建参与者列表
    participants: list[Participant] = []

    # 主持人
    moderator_template = templates["moderator"]
    participants.append(
        Participant(
            name=moderator_template.name,
            type=ParticipantType.MODERATOR,
            description=moderator_template.description,
            avatar=moderator_template.avatar,
        )
    )

    # AI 虚拟角色
    for char_id in request.character_ids:
        template = templates[char_id]
        participants.append(
            Participant(
                name=template.name,
                type=ParticipantType.AI_CHARACTER,
                description=template.description,
                avatar=template.avatar,
            )
        )

    # 思想家角色
    for tid in request.thinker_ids:
        thinker = get_thinker(tid)
        if thinker:
            participants.append(
                Participant(
                    name=thinker.get("name", tid),
                    type=ParticipantType.AI_CHARACTER,
                    description=thinker.get("description", ""),
                    avatar=thinker.get("avatar", "🧠"),
                )
            )

    # 人类参与者
    for human_name in request.human_names:
        participants.append(
            Participant(
                name=human_name,
                type=ParticipantType.HUMAN,
                description=f"学生{human_name}",
            )
        )

    session_id = f"session-{len(_sessions) + 1}"

    session = SessionResponse(
        session_id=session_id,
        topic=topic,
        participants=participants,
        status=DiscussionStatus.WAITING,
    )
    _sessions[session_id] = session

    return session


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """获取会话详情。"""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")
    return _sessions[session_id]


@router.get("/", response_model=list[SessionResponse])
async def list_sessions():
    """列出所有会话。"""
    return list(_sessions.values())


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """删除会话。"""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")

    # 清理资源
    clear_human_queues()
    if session_id in _floor_managers:
        del _floor_managers[session_id]
    del _sessions[session_id]

    return {"message": "会话已删除"}