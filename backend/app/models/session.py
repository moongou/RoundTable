"""RoundTable 数据模型"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DiscussionStatus(str, Enum):
    """讨论会话状态"""

    WAITING = "waiting"  # 等待参与者
    ACTIVE = "active"  # 讨论进行中
    PAUSED = "paused"  # 暂停
    ENDED = "ended"  # 已结束


class ParticipantType(str, Enum):
    """参与者类型"""

    MODERATOR = "moderator"  # 主持人
    AI_CHARACTER = "ai_character"  # AI 虚拟角色
    HUMAN = "human"  # 人类参与者


class Participant(BaseModel):
    """参与者"""

    name: str
    type: ParticipantType
    description: str = ""
    avatar: str = ""


class Topic(BaseModel):
    """讨论话题"""

    id: str
    title: str
    description: str
    category: str  # science/ethics/society/literature/health/education/tech/life
    age_range: str = "8-12"  # 适合年龄
    guide_questions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    story: str = ""  # 话题背景故事（3-4分钟可讲完）
    story_source: str = ""  # 故事来源
    suggested_thinkers: list[str] = Field(default_factory=list)  # 推荐思想家ID


class Thinker(BaseModel):
    """思想家模板"""

    id: str
    name: str  # 中文名，如"苏格拉底"
    display_name: str  # 显示名，如"哲学家苏格拉底"
    era: str = ""  # 时代，如"古希腊"
    avatar: str = ""  # emoji 头像
    color: str = ""  # UI 主题色
    domain: list[str] = Field(default_factory=list)  # 所属领域
    description: str = ""  # 简短描述
    system_message: str = ""  # 系统提示词
    voice: str = ""  # TTS 声音
    suggested_questions: list[str] = Field(default_factory=list)  # 推荐讨论问题


class CharacterTemplate(BaseModel):
    """AI 角色模板"""

    id: str
    name: str
    display_name: str
    avatar: str
    personality_type: str
    system_message: str
    description: str
    voice: str = ""  # Azure TTS 声音名称，Phase 2 使用


class CreateSessionRequest(BaseModel):
    """创建讨论会话请求"""

    topic_id: str
    character_ids: list[str] = Field(default_factory=lambda: ["explorer", "skeptic"])
    thinker_ids: list[str] = Field(default_factory=list)  # 额外的思想家角色
    human_names: list[str] = Field(default_factory=lambda: ["同学"])
    max_turns: int = 30


class SessionResponse(BaseModel):
    """讨论会话响应"""

    session_id: str
    topic: Topic
    participants: list[Participant]
    status: DiscussionStatus


class ChatMessage(BaseModel):
    """聊天消息"""

    source: str  # 发言者名字
    content: str
    type: str = "text"  # text / system / error


class TurnChangeEvent(BaseModel):
    """轮次变更事件"""

    speaker: str
    is_human: bool
    type: str = "turn_change"


class WebSocketEvent(BaseModel):
    """WebSocket 事件"""

    event_type: str  # turn_change / message / system / error / ended
    data: Any = None