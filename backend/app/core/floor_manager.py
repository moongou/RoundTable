"""Floor Manager - 讨论流程总调度师（Master Scheduler）

作为整个讨论系统的**总指挥**，统一协调所有并行任务：
- AutoGen 的 AI 文本轮次生成
- 人类输入的等待与超时管理
- TTS/ASR 语音管道的时序控制
- WebSocket 实时通信事件推送
- 安全过滤与内容修正

设计原则：
- 后端可以有并行任务（AI生成、安全检查、语音合成），但所有任务
  的启动和完成都由 FloorManager 统一调度，确保不会出现竞态条件。
- 人类的发言环节是最高优先级，任何并行任务不得干扰用户发言。
- 通过状态机（FloorState）严格管理转换，避免非法状态跃迁。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Optional

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.base import TaskResult
from autogen_agentchat.messages import (
    ModelClientStreamingChunkEvent,
    SelectSpeakerEvent,
    TextMessage,
    UserInputRequestedEvent,
)
from autogen_agentchat.teams import SelectorGroupChat

from app.agents.human_proxy import put_human_input
from app.core.safety_filter import SafetyFilter
from app.core.turn_scheduler import create_discussion_team, set_designated_speaker, parse_speaker_designation

logger = logging.getLogger(__name__)


class FloorState(str, Enum):
    """讨论状态"""

    INIT = "init"
    MODERATOR_OPENING = "moderator_opening"
    SELECTING_SPEAKER = "selecting_speaker"
    AI_SPEAKING = "ai_speaking"
    HUMAN_TURN_WAITING = "human_turn_waiting"
    HUMAN_SPEAKING = "human_speaking"
    INTERRUPTED = "interrupted"  # 有人请求打断
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
        human_timeout: int = 120,
        designated_speaker_setter: Optional[Callable[[Optional[str]], None]] = None,
    ):
        self.team = team
        self.ai_agents = ai_agents
        self.human_agents = human_agents
        self.safety_filter = safety_filter
        self.human_timeout = human_timeout
        self._set_designated_speaker = designated_speaker_setter or set_designated_speaker

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
        self._on_interrupt: Optional[Callable] = None

        # 打断请求队列
        self._interrupt_queue: list[str] = []

        # 流式文本缓冲
        self._streaming_buffer: dict[str, list[str]] = {}
        self._current_streaming_source: Optional[str] = None

        # display name → agent name 映射（需求4：用于指定发言者解析）
        self._display_name_to_agent: dict[str, str] = {}
        self._agent_to_display_name: dict[str, str] = {}
        self._speaker_message_count: dict[str, int] = {name: 0 for name in self.all_names}
        self._recent_display_speakers: list[str] = []

        # Stalled watchdog: detect no-progress windows and auto-recover human wait stalls.
        self._watchdog_task: Optional[asyncio.Task] = None
        self._watchdog_stop = asyncio.Event()
        self._last_progress_ts = time.monotonic()
        self._last_watchdog_action_ts = 0.0
        self._stall_check_interval_sec = 2.0
        self._general_stall_timeout_sec = max(30.0, float(human_timeout) * 2.0)
        # 给用户语音识别与重试留足窗口，避免“刚说完就被系统判跳过”。
        self._human_stall_timeout_sec = max(45.0, float(human_timeout) + 15.0)
        self._min_human_turn_window_sec = 10.0
        self._human_turn_started_mono = 0.0

    def _sanitize_opening_reference(self, source: str, content: str) -> str:
        """首轮发言兜底规整：避免开场阶段出现不当引用。"""
        text = (content or "").strip()
        if not text:
            return text

        count = self._speaker_message_count.get(source, 0)
        is_moderator = source == "moderator"
        is_first_turn = count == 0

        if is_moderator and is_first_turn:
            # 老师开场不引用任何人
            text = re.sub(
                r"(刚才|前面|上一位|某位同学|有同学).*?(说|提到|讲到)[^。！？!?]*[。！？!?]",
                "",
                text,
            ).strip()
            if not text:
                text = "同学们，我们先一起梳理一下这个问题的背景，再逐一发表观点。"
            return text

        if (source in self.ai_names or source in self.human_names) and is_first_turn:
            # 同学首轮发言去掉“上一位同学”式互引
            text = re.sub(r"(上一位同学|刚才.*同学|某位同学)", "这个问题", text)
            text = re.sub(r"(你说得对|他说得对|她说得对)", "我先说说我的看法", text)
            text = re.sub(r"^(基于|根据).{0,12}(发言|观点)[，,]", "", text)
            return text

        return text

    def _sanitize_reference_attribution(self, source: str, content: str) -> str:
        """修正明显错误的“刚才/上一位”引用归属。

        规则：若文本出现“刚才X/上一位X/前面X”，且 X 不是最近一位实际发言者，
        则改写为最近一位发言者，避免 A/B 错置。
        """
        text = (content or "").strip()
        if not text or not self._recent_display_speakers:
            return text

        current_display = self._agent_to_display_name.get(source, source)
        last_display = ""
        for name in reversed(self._recent_display_speakers):
            if name != current_display:
                last_display = name
                break
        if not last_display:
            return text

        # 构建"已实际发言过"的名字集合
        spoke_set = set(self._recent_display_speakers)

        display_names = sorted(set(self._agent_to_display_name.values()), key=len, reverse=True)
        for name in display_names:
            if name == current_display:
                continue
            # 修正"刚才/上一位/前面 + 错误名字" → 替换为真正的上一位
            if name != last_display:
                text = re.sub(rf"(刚才|上一位|前面)\s*{re.escape(name)}", rf"\1{last_display}", text)
            # 检测对从未发言者的引用（"X说/X提到/X认为"） → 替换为中性表述
            if name not in spoke_set:
                text = re.sub(
                    rf"{re.escape(name)}\s*(说得?对?|提到|认为|觉得|讲到|说过)",
                    r"有同学\1",
                    text,
                )
        return text

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

    def on_interrupt(self, callback: Callable) -> "FloorManager":
        """注册打断回调。callback(interrupter, current_speaker, approved_by)"""
        self._on_interrupt = callback
        return self

    def set_display_name_map(self, agent_to_display: dict[str, str]) -> None:
        """设置 agent name → display name 映射，用于指定发言者解析。"""
        self._agent_to_display_name = dict(agent_to_display)
        self._display_name_to_agent = {v: k for k, v in agent_to_display.items()}

    async def _emit_message(self, source: str, content: str, msg_type: str = "text") -> None:
        """发送消息事件。"""
        self._touch_progress("emit_message")
        if self._on_message:
            await self._on_message(source, content, msg_type)

    async def _emit_turn_change(self, speaker: str, is_human: bool) -> None:
        """发送轮次变更事件。"""
        self.current_speaker = speaker
        self._touch_progress("emit_turn_change")
        if self._on_turn_change:
            await self._on_turn_change(speaker, is_human)

    async def _set_state(
        self,
        new_state: FloorState,
        *,
        reason: str = "",
        recovery: bool = False,
    ) -> None:
        """更新状态并发送事件。"""
        old_state = self.state
        self.state = new_state
        if new_state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
            if old_state not in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
                self._human_turn_started_mono = time.monotonic()
        else:
            self._human_turn_started_mono = 0.0
        self._touch_progress("set_state")
        if self._on_state_change:
            await self._on_state_change(old_state, new_state, reason, recovery)

    async def _emit_error(self, error_msg: str) -> None:
        """发送错误事件。"""
        logger.error(f"FloorManager 错误: {error_msg}")
        self._touch_progress("emit_error")
        if self._on_error:
            await self._on_error(error_msg)

    def _touch_progress(self, reason: str = "") -> None:
        self._last_progress_ts = time.monotonic()
        if reason:
            logger.debug("[FloorManager] progress touch: %s", reason)

    async def _watchdog_loop(self) -> None:
        """Watchdog loop: auto-recover stuck human-turn windows and emit diagnostics."""
        while not self._watchdog_stop.is_set():
            await asyncio.sleep(self._stall_check_interval_sec)
            now = time.monotonic()
            idle_sec = now - self._last_progress_ts

            if self.state in (FloorState.ENDED, FloorState.CLOSING):
                continue

            # Prefer explicit auto-recovery for human wait stalls.
            if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
                if idle_sec >= self._human_stall_timeout_sec and self.current_speaker:
                    entered = self._human_turn_started_mono
                    if entered > 0:
                        elapsed_in_human_turn = now - entered
                        if elapsed_in_human_turn < self._min_human_turn_window_sec:
                            continue
                    # Rate-limit watchdog actions to avoid repeated queue writes.
                    if now - self._last_watchdog_action_ts < 6.0:
                        continue
                    self._last_watchdog_action_ts = now
                    speaker = self.current_speaker
                    display = self._agent_to_display_name.get(speaker, speaker)
                    logger.warning(
                        "[FloorManager] human turn stalled for %.1fs, auto-skip speaker=%s",
                        idle_sec,
                        speaker,
                    )
                    await put_human_input(speaker, "（跳过）")
                    await self._emit_message(
                        "系统",
                        f"{display} 同学输入超时，系统已自动跳过并继续讨论。",
                        "system",
                    )
                    await self._set_state(
                        FloorState.SELECTING_SPEAKER,
                        reason="watchdog_human_autoskip",
                        recovery=True,
                    )
                    self._touch_progress("watchdog_human_autoskip")
                continue

            # Non-human hard stalls: publish a diagnostic system message for observability.
            if idle_sec >= self._general_stall_timeout_sec:
                if now - self._last_watchdog_action_ts < 12.0:
                    continue
                self._last_watchdog_action_ts = now
                logger.warning(
                    "[FloorManager] general stall detected state=%s idle=%.1fs",
                    self.state,
                    idle_sec,
                )
                await self._emit_message(
                    "系统",
                    "检测到流程停滞，系统正在自动恢复调度。",
                    "system",
                )
                self._touch_progress("watchdog_general_notice")

    async def run(self, topic: str) -> AsyncGenerator[dict, None]:
        """运行讨论，产出事件流。

        Args:
            topic: 讨论主题。

        Yields:
            事件字典，包含 event_type 和 data。
        """
        await self._set_state(FloorState.MODERATOR_OPENING, reason="discussion_start")
        self._watchdog_stop.clear()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

        try:
            stream = self.team.run_stream(task=topic)

            async for event in stream:
                result = await self._process_event(event)
                if result:
                    yield result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"讨论运行异常: {type(e).__name__}: {error_msg}", exc_info=True)
            # 区分 API 配置错误和运行时错误
            if "api_key" in error_msg.lower() or "authentication" in error_msg.lower() or "401" in error_msg:
                await self._emit_error(f"API 密钥无效或未配置: {error_msg}")
                yield {
                    "event_type": "api_error",
                    "data": {
                        "message": "API 密钥无效或未配置，请在设置中检查 API Key",
                        "original_error": error_msg,
                        "recoverable": False,
                    },
                }
            elif "connection" in error_msg.lower() or "connect" in error_msg.lower() or "timeout" in error_msg.lower():
                await self._emit_error(f"无法连接到 LLM 服务: {error_msg}")
                yield {
                    "event_type": "api_error",
                    "data": {
                        "message": "无法连接到 AI 服务，请检查网络和服务器地址",
                        "original_error": error_msg,
                        "recoverable": False,
                    },
                }
            elif "rate" in error_msg.lower() or "429" in error_msg or "quota" in error_msg.lower():
                await self._emit_error(f"API 调用频率受限: {error_msg}")
                yield {
                    "event_type": "api_error",
                    "data": {
                        "message": "AI 服务调用频率受限或配额已用完，请稍后再试",
                        "original_error": error_msg,
                        "recoverable": True,
                    },
                }
            elif "model" in error_msg.lower() and ("not found" in error_msg.lower() or "not exist" in error_msg.lower()):
                await self._emit_error(f"模型不存在: {error_msg}")
                yield {
                    "event_type": "api_error",
                    "data": {
                        "message": "指定的模型不存在，请在设置中检查模型名称",
                        "original_error": error_msg,
                        "recoverable": False,
                    },
                }
            else:
                await self._emit_error(f"讨论运行错误: {error_msg}")
                yield {"event_type": "error", "data": {"message": f"讨论出现异常: {error_msg}"}}
        finally:
            self._watchdog_stop.set()
            if self._watchdog_task:
                self._watchdog_task.cancel()
                try:
                    await self._watchdog_task
                except asyncio.CancelledError:
                    pass
                self._watchdog_task = None
            if self.state != FloorState.ENDED:
                await self._set_state(FloorState.ENDED, reason="discussion_end")
            yield {"event_type": "ended", "data": {"session_id": self.session_id}}

    async def _process_event(self, event: Any) -> Optional[dict]:
        """处理 AutoGen 事件流中的单个事件。"""

        # 发言者选择事件
        if isinstance(event, SelectSpeakerEvent):
            speaker = event.content if hasattr(event, "content") else str(event)
            is_human = speaker in self.human_names

            logger.info("[FloorManager] 选择发言者: %s (is_human=%s)", speaker, is_human)

            if is_human:
                await self._set_state(FloorState.HUMAN_TURN_WAITING, reason="speaker_selected_human")
            else:
                await self._set_state(FloorState.AI_SPEAKING, reason="speaker_selected_ai")

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

            # 过滤 AutoGen 内部任务注入消息（source="user" 是 AutoGen 框架内部产生的）
            if source == "user":
                return None

            logger.info("[FloorManager] 完整消息: source=%s, content_len=%d", source, len(content))

            # 首轮发言兜底规整：避免不当引用
            content = self._sanitize_opening_reference(source, content)
            content = self._sanitize_reference_attribution(source, content)

            # 点名解析应尽量基于原始语义，先于安全改写尝试。
            designated_pre_filter: Optional[str] = None
            display_source = self._agent_to_display_name.get(source, source)
            all_display_names = list(self._display_name_to_agent.keys())
            if all_display_names and (source in self.ai_names or source in self.human_names):
                designated_pre_filter = parse_speaker_designation(content, all_display_names)

            # 安全过滤 AI 输出
            if source in self.ai_names:
                content = await self.safety_filter.filter_or_rewrite(content)

            # 如果是老师或用户的发言，检查是否指定了下一位发言者（需求4）
            designated: Optional[str] = designated_pre_filter
            if all_display_names and (source in self.ai_names or source in self.human_names):
                designated = designated or parse_speaker_designation(content, all_display_names)
                if designated:
                    agent_name = self._display_name_to_agent.get(designated, designated)
                    logger.info("[FloorManager] %s 指定下一位发言者: %s (agent: %s)", display_source, designated, agent_name)
                    self._set_designated_speaker(agent_name)

            # 清空该发言者的流式缓冲
            self._streaming_buffer.pop(source, None)
            self._current_streaming_source = None

            await self._emit_message(source, content, "text")
            self._speaker_message_count[source] = self._speaker_message_count.get(source, 0) + 1
            self._recent_display_speakers.append(display_source)
            if len(self._recent_display_speakers) > 16:
                self._recent_display_speakers = self._recent_display_speakers[-16:]

            return {
                "event_type": "message",
                "data": {"source": source, "content": content},
            }

        # 人类输入请求
        if isinstance(event, UserInputRequestedEvent):
            # 等待人类输入，带超时
            speaker = self.current_speaker or ""
            await self._set_state(FloorState.HUMAN_SPEAKING, reason="human_input_requested")
            return {
                "event_type": "human_input_requested",
                "data": {"speaker": speaker},
            }

        # 任务完成结果
        if isinstance(event, TaskResult):
            logger.info(f"讨论任务完成: stop_reason={event.stop_reason}")
            return None  # 任务完成后 run() 的 finally 会发送 ended

        # 忽略其他事件类型
        logger.debug(f"忽略未知事件类型: {type(event).__name__}")
        return None

    async def submit_human_input(self, name: str, text: str) -> None:
        """提交人类参与者的输入文本。

        从 WebSocket/STT 收到人类消息时调用此方法。
        同时检查用户发言中是否指定了下一位发言者。

        Args:
            name: 参与者名字。
            text: 转录文本。
        """
        normalized_name = (name or "").strip()
        normalized_text = (text or "").strip()
        self._touch_progress("submit_human_input")

        logger.info("[FloorManager] 收到人类输入: name=%s, text_len=%d", normalized_name, len(normalized_text))

        # 空输入直接按跳过处理，保证流程继续。
        if not normalized_text:
            logger.info("[FloorManager] 空输入，自动跳过: %s", normalized_name)
            await put_human_input(normalized_name, "（跳过）")
            await self._emit_message("系统", f"{normalized_name or '该同学'}未输入有效内容，已自动跳过本轮。", "system")
            return

        # 跳过指令不需要安全过滤
        if normalized_text in ("（跳过）", "(跳过)", "跳过"):
            logger.info("[FloorManager] 用户主动跳过: %s", normalized_name)
            await put_human_input(normalized_name, "（跳过）")
            return

        # 安全过滤人类输入
        is_safe, reason = await self.safety_filter.check_human_input(normalized_text)
        if not is_safe:
            logger.warning("[FloorManager] 人类输入被安全过滤: %s, reason=%s", normalized_name, reason)
            await self._emit_message("系统", "你的发言包含不适当的内容，请换一种方式表达。", "system")
            return

        # 检查用户是否指定了下一位发言者（需求4）
        all_participant_names = list(self.all_names)
        # 使用 display name map if available
        designated = parse_speaker_designation(normalized_text, list(self._display_name_to_agent.keys()) if hasattr(self, '_display_name_to_agent') else all_participant_names)
        if designated:
            logger.info("[FloorManager] 用户 %s 指定下一位发言者: %s", normalized_name, designated)
            # 转换为 agent name
            agent_name = self._display_name_to_agent.get(designated, designated) if hasattr(self, '_display_name_to_agent') else designated
            self._set_designated_speaker(agent_name)

        try:
            await put_human_input(normalized_name, normalized_text)
            logger.info("[FloorManager] 人类输入已提交到队列: %s", normalized_name)
        except Exception as e:
            logger.warning("[FloorManager] 提交人类输入失败，自动跳过。name=%s, err=%s", normalized_name, e)
            await put_human_input(normalized_name, "（跳过）")
            await self._emit_message("系统", f"{normalized_name or '该同学'}输入处理异常，系统已自动跳过并继续讨论。", "system")

    async def request_interrupt(self, speaker: str) -> None:
        """处理打断请求。

        当参与者请求打断当前发言者时调用。
        记录打断者，切换到 INTERRUPTED 状态，
        通知主持人进行下一轮选择。

        Args:
            speaker: 请求打断的参与者名字。
        """
        logger.info(f"打断请求: {speaker} 请求发言 (当前发言者: {self.current_speaker})")
        self._interrupt_queue.append(speaker)

        await self._set_state(FloorState.INTERRUPTED, reason="interrupt_requested")

        moderator_display = self._agent_to_display_name.get("moderator", "李老师")
        # 让举手者成为下一位优先发言，避免被其他角色插队。
        self._set_designated_speaker(speaker)
        # 由老师口吻发布同意插话通知。
        await self._emit_message(
            moderator_display,
            f"{speaker} 同学，我同意你先发言，其他同学稍后继续。",
            "interrupt",
        )

        # 通知打断事件
        if self._on_interrupt:
            await self._on_interrupt(speaker, self.current_speaker or "", moderator_display)

        # 短暂暂停后恢复到选择发言者状态
        await asyncio.sleep(0.5)
        await self._set_state(
            FloorState.SELECTING_SPEAKER,
            reason="interrupt_resolved",
            recovery=True,
        )

    async def handle_push_to_talk_start(self, speaker: str) -> None:
        """处理 Push-to-Talk 开始事件。

        标记参与者开始发言，切换到人类发言状态。

        Args:
            speaker: 开始发言的参与者名字。
        """
        logger.info(f"PTT 开始: {speaker} 开始发言")
        if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
            await self._set_state(FloorState.HUMAN_SPEAKING, reason="ptt_start")
            await self._emit_turn_change(speaker, is_human=True)

    async def handle_push_to_talk_end(self, speaker: str) -> None:
        """处理 Push-to-Talk 结束事件。

        标记参与者结束发言。

        Args:
            speaker: 结束发言的参与者名字。
        """
        normalized = (speaker or "").strip()
        logger.info(f"PTT 结束: {normalized or speaker} 结束发言")
        self._touch_progress("ptt_end")

        # 从“正在讲话”切换回“等待提交文本”，避免状态长期停留 HUMAN_SPEAKING。
        if self.state == FloorState.HUMAN_SPEAKING:
            await self._set_state(
                FloorState.HUMAN_TURN_WAITING,
                reason="ptt_end_waiting_input",
                recovery=True,
            )