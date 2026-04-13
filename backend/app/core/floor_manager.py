"""Floor Manager - 讨论流程状态机

核心组件：桥接 AutoGen 的文本轮次模型和实时通信层。
管理讨论状态转换、人类输入队列、AI 输出流。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Optional

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.messages import (
    ModelClientStreamingChunkEvent,
    SelectSpeakerEvent,
    TextMessage,
    UserInputRequestedEvent,
)
from autogen_agentchat.teams import SelectorGroupChat

from app.agents.human_proxy import get_human_queue, put_human_input
from app.core.safety_filter import SafetyFilter
from app.core.turn_scheduler import create_discussion_team

logger = logging.getLogger(__name__)


class FloorState(str, Enum):
    """讨论状态"""

    INIT = "init"
    MODERATOR_OPENING = "moderator_opening"
    SELECTING_SPEAKER = "selecting_speaker"
    AI_SPEAKING = "ai_speaking"
    HUMAN_TURN_WAITING = "human_turn_waiting"
    HUMAN_SPEAKING = "human_speaking"
    CLOSING = "closing"
    ENDED = "ended"


class FloorManager:
    """管理圆桌讨论的流程状态机。

    职责：
    1. 驱动 AutoGen team.run_stream() 事件流
    2. 在 AI 发言和人类发言之间切换
    3. 管理人类输入的 asyncio.Queue
    4. 通过回调向外推送状态变更和消息
    5. 处理超时和安全过滤
    """

    def __init__(
        self,
        team: SelectorGroupChat,
        ai_agents: list[AssistantAgent],
        human_agents: list[UserProxyAgent],
        safety_filter: SafetyFilter,
        human_timeout: int = 15,
    ):
        self.team = team
        self.ai_agents = ai_agents
        self.human_agents = human_agents
        self.safety_filter = safety_filter
        self.human_timeout = human_timeout

        self.ai_names = {agent.name for agent in ai_agents}
        self.human_names = {agent.name for agent in human_agents}
        self.all_names = self.ai_names | self.human_names

        self.state = FloorState.INIT
        self.current_speaker: Optional[str] = None
        self.session_id = str(uuid.uuid4())

        # 消息回调：外部注册以接收事件
        self._on_message: Optional[Callable] = None
        self._on_turn_change: Optional[Callable] = None
        self._on_state_change: Optional[Callable] = None
        self._on_error: Optional[Callable] = None

        # 流式文本缓冲
        self._streaming_buffer: dict[str, list[str]] = {}
        self._current_streaming_source: Optional[str] = None

    def on_message(self, callback: Callable) -> "FloorManager":
        """注册消息回调。callback(source, content, msg_type)"""
        self._on_message = callback
        return self

    def on_turn_change(self, callback: Callable) -> "FloorManager":
        """注册轮次变更回调。callback(speaker, is_human)"""
        self._on_turn_change = callback
        return self

    def on_state_change(self, callback: Callable) -> "FloorManager":
        """注册状态变更回调。callback(old_state, new_state)"""
        self._on_state_change = callback
        return self

    def on_error(self, callback: Callable) -> "FloorManager":
        """注册错误回调。callback(error_msg)"""
        self._on_error = callback
        return self

    async def _emit_message(self, source: str, content: str, msg_type: str = "text") -> None:
        """发送消息事件。"""
        if self._on_message:
            await self._on_message(source, content, msg_type)

    async def _emit_turn_change(self, speaker: str, is_human: bool) -> None:
        """发送轮次变更事件。"""
        self.current_speaker = speaker
        if self._on_turn_change:
            await self._on_turn_change(speaker, is_human)

    async def _set_state(self, new_state: FloorState) -> None:
        """更新状态并发送事件。"""
        old_state = self.state
        self.state = new_state
        if self._on_state_change:
            await self._on_state_change(old_state, new_state)

    async def _emit_error(self, error_msg: str) -> None:
        """发送错误事件。"""
        logger.error(f"FloorManager 错误: {error_msg}")
        if self._on_error:
            await self._on_error(error_msg)

    async def run(self, topic: str) -> AsyncGenerator[dict, None]:
        """运行讨论，产出事件流。

        Args:
            topic: 讨论主题。

        Yields:
            事件字典，包含 event_type 和 data。
        """
        await self._set_state(FloorState.MODERATOR_OPENING)

        try:
            stream = self.team.run_stream(task=topic)

            async for event in stream:
                result = await self._process_event(event)
                if result:
                    yield result

        except Exception as e:
            await self._emit_error(f"讨论运行错误: {e}")
            yield {"event_type": "error", "data": {"message": str(e)}}
        finally:
            await self._set_state(FloorState.ENDED)
            yield {"event_type": "ended", "data": {"session_id": self.session_id}}

    async def _process_event(self, event: Any) -> Optional[dict]:
        """处理 AutoGen 事件流中的单个事件。"""

        # 发言者选择事件
        if isinstance(event, SelectSpeakerEvent):
            speaker = event.content if hasattr(event, "content") else str(event)
            is_human = speaker in self.human_names

            if is_human:
                await self._set_state(FloorState.HUMAN_TURN_WAITING)
            else:
                await self._set_state(FloorState.AI_SPEAKING)

            await self._emit_turn_change(speaker, is_human)
            return {
                "event_type": "turn_change",
                "data": {"speaker": speaker, "is_human": is_human},
            }

        # 流式文本块
        if isinstance(event, ModelClientStreamingChunkEvent):
            source = event.source if hasattr(event, "source") else self.current_speaker
            content = event.content if hasattr(event, "content") else str(event)

            if self._current_streaming_source != source:
                self._current_streaming_source = source
                self._streaming_buffer[source] = []

            self._streaming_buffer.setdefault(source, []).append(content)

            return {
                "event_type": "stream",
                "data": {"source": source, "content": content},
            }

        # 完整文本消息
        if isinstance(event, TextMessage):
            source = event.source
            content = event.content

            # 安全过滤 AI 输出
            if source in self.ai_names:
                content = await self.safety_filter.filter_or_rewrite(content)

            # 清空该发言者的流式缓冲
            self._streaming_buffer.pop(source, None)
            self._current_streaming_source = None

            await self._emit_message(source, content, "text")

            return {
                "event_type": "message",
                "data": {"source": source, "content": content},
            }

        # 人类输入请求
        if isinstance(event, UserInputRequested):
            # 等待人类输入，带超时
            speaker = self.current_speaker or ""
            await self._set_state(FloorState.HUMAN_SPEAKING)
            return {
                "event_type": "human_input_requested",
                "data": {"speaker": speaker},
            }

        # 忽略其他事件类型
        return None

    async def submit_human_input(self, name: str, text: str) -> None:
        """提交人类参与者的输入文本。

        从 WebSocket/STT 收到人类消息时调用此方法。

        Args:
            name: 参与者名字。
            text: 转录文本。
        """
        # 安全过滤人类输入
        is_safe, reason = await self.safety_filter.check_human_input(text)
        if not is_safe:
            await self._emit_message("系统", "你的发言包含不适当的内容，请换一种方式表达。", "system")
            return

        await put_human_input(name, text)