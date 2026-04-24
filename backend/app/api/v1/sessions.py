"""讨论会话 API

创建和管理讨论会话。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autogen_core.models import UserMessage

from app.agents.character_templates import load_all_templates
from app.agents.human_proxy import clear_human_queues, create_human_proxy
from app.agents.moderator import create_moderator
from app.agents.virtual_character import create_virtual_character, create_thinker_agent
from app.core.floor_manager import FloorManager
from app.core.golden_quotes import (
    build_golden_quotes_prompt,
    has_enough_quote_material,
    parse_golden_quotes_response,
)
from app.core.llm_factory import create_character_client, create_moderator_client
from app.core.safety_filter import SafetyFilter
from app.core.thinkers import get_thinker
from app.core.topics import FREE_TOPIC_CATEGORY_ID, FREE_TOPIC_CATEGORY_NAME, get_topic_by_id
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


class GoldenQuoteMessage(BaseModel):
    source: str
    content: str
    type: str = 'text'


class GenerateGoldenQuotesRequest(BaseModel):
    topic: str
    messages: list[GoldenQuoteMessage] = Field(default_factory=list)
    max_quotes: int = 4


@router.post("/", response_model=SessionResponse)
async def create_session(request: CreateSessionRequest):
    """创建一个新的讨论会话。

    支持两种模式：
    1. 预设话题模式：提供 topic_id，使用系统内置话题
    2. 自由话题模式：提供 free_topic，用户自己发起讨论话题
    """
    from app.models.session import Topic as TopicModel

    # 确定话题来源
    topic: Optional[TopicModel] = None
    # 需求18：如果 topic_id 是占位符 'free_topic'，自动走自由话题路径
    effective_topic_id = request.topic_id.strip() if request.topic_id else ""
    is_free_placeholder = effective_topic_id in ("", "free_topic", "free")
    if effective_topic_id and not is_free_placeholder:
        topic = get_topic_by_id(effective_topic_id)
        if not topic:
            raise HTTPException(status_code=404, detail=f"话题 '{effective_topic_id}' 不存在")
    elif request.free_topic:
        # 用户自由发起的话题
        free_topic_detail = request.free_topic_detail.strip() or request.free_topic
        topic = TopicModel(
            id="free_topic",
            title=request.free_topic,
            description=f"由用户发起的自由讨论话题：{free_topic_detail}",
            category=FREE_TOPIC_CATEGORY_ID,
            age_range="8-12",
            guide_questions=[],
            tags=[FREE_TOPIC_CATEGORY_NAME, "自由话题"],
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="必须提供 topic_id（预设话题）或 free_topic（自由话题）之一",
        )

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
        max_turns=request.max_turns,
    )
    _sessions[session_id] = session

    return session


@router.post("/golden-quotes")
async def generate_golden_quotes(request: GenerateGoldenQuotesRequest):
    """用当前配置的 LLM 对讨论记录进行提炼，生成展示用金句。"""
    max_quotes = max(1, min(request.max_quotes, 4))
    messages = [message.model_dump() for message in request.messages]
    if not has_enough_quote_material(messages):
        return {
            'eligible': False,
            'quotes': [],
            'message': '讨论材料不足，暂不生成金句',
        }

    prompt = build_golden_quotes_prompt(
        request.topic,
        messages,
        max_quotes=max_quotes,
    )
    try:
        model_client = create_character_client()
        response = await model_client.create([UserMessage(content=prompt, source='user')])
        raw_content = response.content if isinstance(response.content, str) else str(response.content)
        quotes = parse_golden_quotes_response(raw_content, max_quotes=max_quotes)
        return {
            'eligible': True,
            'quotes': quotes,
            'message': 'ok' if quotes else '模型未返回可用金句',
        }
    except Exception as exc:
        logger.exception('generate golden quotes failed')
        return {
            'eligible': True,
            'quotes': [],
            'message': f'生成失败: {exc}',
        }


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
    clear_human_queues(session_scope=session_id)
    if session_id in _floor_managers:
        del _floor_managers[session_id]
    del _sessions[session_id]

    return {"message": "会话已删除"}