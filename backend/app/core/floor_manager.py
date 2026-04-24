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
from app.core.rolling_summary_memory import RollingSummaryMemory
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
        summary_memory: Optional[RollingSummaryMemory] = None,
        human_queue_scope: str | None = None,
    ):
        self.team = team
        self.ai_agents = ai_agents
        self.human_agents = human_agents
        self.safety_filter = safety_filter
        self.human_timeout = human_timeout
        self._set_designated_speaker = designated_speaker_setter or set_designated_speaker
        self.summary_memory = summary_memory
        self._human_queue_scope = human_queue_scope

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
        self._recent_turn_summaries: list[tuple[str, str]] = []

        # Stalled watchdog: detect no-progress windows and auto-recover human wait stalls.
        self._watchdog_task: Optional[asyncio.Task] = None
        self._watchdog_stop = asyncio.Event()
        self._last_progress_ts = time.monotonic()
        self._last_watchdog_action_ts = 0.0
        self._stall_check_interval_sec = 2.0
        self._general_stall_timeout_sec = max(30.0, float(human_timeout) * 2.0)
        # 需求11：用户要求 30 秒未发言自动跳过。与前端倒计时保持一致。
        self._human_stall_timeout_sec = 30.0
        self._min_human_turn_window_sec = 8.0
        self._human_turn_started_mono = 0.0
        self._last_human_input_requested_speaker = ""
        self._human_turn_idle_notice_sent = False

        # 暂停状态
        self._paused = False
        self._resume_gate = asyncio.Event()
        self._resume_gate.set()

    def set_paused(self, paused: bool) -> None:
        """设置暂停状态。暂停时 watchdog 停止检查，恢复时重置进度时间戳。"""
        self._paused = paused
        if paused:
            self._resume_gate.clear()
            try:
                self.team.pause()
            except RuntimeError:
                logger.debug("team pause requested before initialization", exc_info=True)
            except Exception:
                logger.debug("team pause raised", exc_info=True)
            self._touch_progress("paused")
        else:
            self._resume_gate.set()
            try:
                self.team.resume()
            except RuntimeError:
                logger.debug("team resume requested before initialization", exc_info=True)
            except Exception:
                logger.debug("team resume raised", exc_info=True)
            self._touch_progress("resumed")
            self._last_watchdog_action_ts = 0.0

    async def _wait_until_resumed(self) -> None:
        while self._paused:
            await self._resume_gate.wait()

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
                r"[^。！？!?]*(?:同学|先生)?[，,:：]?\s*你?(?:刚才|前面|上一位)[^。！？!?]*[。！？!?]",
                "",
                text,
            ).strip()
            text = re.sub(
                r"(刚才|前面|上一位|某位同学|有同学).*?(说|提到|讲到)[^。！？!?]*[。！？!?]",
                "",
                text,
            ).strip()
            if not text:
                text = "同学们，我们先一起梳理一下这个问题的背景，再逐一发表观点。"
            return text

        if (source in self.ai_names or source in self.human_names) and is_first_turn:
            # 同学首轮发言去掉"上一位同学"式互引
            text = re.sub(r"(上一位同学|刚才.*同学|某位同学)", "这个问题", text)
            text = re.sub(r"(你说得对|他说得对|她说得对)", "我先说说我的看法", text)
            text = re.sub(r"^(基于|根据).{0,12}(发言|观点)[，,]", "", text)
            return text

        return text

    def _sanitize_reference_attribution(self, source: str, content: str) -> str:
        """修正明显错误的"刚才/上一位"引用归属，并防止自引用。

        规则：
        1. 若文本出现"刚才X/上一位X/前面X"，且 X 不是最近一位实际发言者，
           则改写为最近一位发言者，避免 A/B 错置。
        2. 若角色引用了自己（"我觉得小明说得对"当自己是小明时），
           改写为中性表述"有同学说得对"。
        """
        text = (content or "").strip()
        if not text:
            return text

        current_display = self._agent_to_display_name.get(source, source)
        current_agent_names = {source, current_display}

        # ── 规则 A：禁止自引用 ───────────────────────────────────────────────────
        # 若角色引用自己（"我觉得XX说……"当自己是XX时），改为中性表述
        self_reference_patterns = [
            rf"{re.escape(current_display)}\s*(说|提到|认为|觉得|讲到|说过)",
            rf"{re.escape(source)}\s*(说|提到|认为|觉得|讲到|说过)",
        ]
        for pattern in self_reference_patterns:
            text = re.sub(pattern, r"有同学\1", text)

        # 防止"我（XX）觉得……"这种冗余自我介绍
        text = re.sub(rf"我[（(]{re.escape(current_display)}[）)]", "我", text)

        if not self._recent_display_speakers:
            return text

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
                    rf"{re.escape(name)}(?:同学|先生)?(?:还)?\s*刚才(说|提到|讲到)的",
                    r"有同学刚才\1的",
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?(?:还)?\s*刚才(说|提到|讲到)过的",
                    r"有同学刚才\1过的",
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?[，,:：]?\s*你?(?:刚才|前面|上一轮)[^。！？!?]*[。！？!?]?",
                    "",
                    text,
                )
                text = re.sub(
                    rf"(刚才|前面|上一轮)\s*{re.escape(name)}(?:同学|先生)?",
                    r"\1有同学",
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?(?:还)?\s*(刚才说|刚才提到|说得?对?|提到|认为|觉得|讲到|说过)",
                    r"有同学\1",
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?这个",
                    "这个",
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?的这个",
                    "这个",
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?的(比喻|想法|观点|问题|疑问|说法|例子)",
                    r"这个\1",
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}\s*(说得?对?|提到|认为|觉得|讲到|说过)",
                    r"有同学\1",
                    text,
                )
        text = re.sub(r"\s{2,}", " ", text).strip()
        text = re.sub(r"^[，,:：\s]+", "", text)
        return text

    def _sanitize_all_references(self, source: str, content: str) -> str:
        text = self._sanitize_opening_reference(source, content)
        return self._sanitize_reference_attribution(source, text)

    def _is_non_substantive_turn(self, content: str) -> bool:
        normalized = re.sub(r"\s+", "", (content or "").strip())
        return normalized in {
            "（跳过）",
            "(跳过)",
            "跳过",
            "（旁听）",
            "(旁听)",
            "旁听",
            "（我先听听大家的意见）",
            "(我先听听大家的意见)",
            "我先听听大家的意见",
        }

    def _extract_core_viewpoint(self, content: str) -> str:
        text = re.sub(r"（[^）]{0,24}）", "", content or "")
        text = re.sub(r"\([^)]{0,24}\)", "", text)
        text = re.sub(r"\s+", " ", text).strip(" ，,。！？!?；;:：")
        if not text:
            return ""
        first_sentence = re.split(r"[。！？!?；;]", text, maxsplit=1)[0].strip()
        summary = first_sentence or text
        if len(summary) > 42:
            summary = summary[:42].rstrip("，,；;、 ") + "…"
        return summary

    async def _record_turn_summary(self, source: str, content: str) -> None:
        if self.summary_memory is None or source not in self.all_names:
            return
        text = (content or "").strip()
        if not text or self._is_non_substantive_turn(text):
            return
        display_source = self._agent_to_display_name.get(source, source)
        summary = self._extract_core_viewpoint(text)
        if not summary:
            return
        self._recent_turn_summaries.append((display_source, summary))
        if len(self._recent_turn_summaries) > 3:
            self._recent_turn_summaries = self._recent_turn_summaries[-3:]
        await self.summary_memory.replace_turn_summaries(self._recent_turn_summaries)

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

    async def _put_human_input(self, speaker: str, text: str) -> None:
        await put_human_input(speaker, text, session_scope=self._human_queue_scope)

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
                self._human_turn_idle_notice_sent = False
        else:
            self._human_turn_started_mono = 0.0
            self._human_turn_idle_notice_sent = False
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

            # 暂停期间跳过所有 watchdog 检查
            if self._paused:
                continue

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
                    if self._human_turn_idle_notice_sent:
                        continue
                    self._last_watchdog_action_ts = now
                    self._human_turn_idle_notice_sent = True
                    speaker = self.current_speaker
                    display = self._agent_to_display_name.get(speaker, speaker)
                    logger.warning(
                        "[FloorManager] human turn stalled for %.1fs, manual input still required speaker=%s",
                        idle_sec,
                        speaker,
                    )
                    await self._emit_message(
                        "系统",
                        f"{display} 同学，如果你暂时不想发言，可以手动点“跳过”；系统不会替你跳过。",
                        "system",
                    )
                    self._touch_progress("watchdog_human_wait_notice")
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
        self._resume_gate.set()
        self._recent_turn_summaries.clear()
        if self.summary_memory is not None:
            await self.summary_memory.clear()

        try:
            stream = self.team.run_stream(task=topic)

            async for event in stream:
                if self._paused:
                    await self._wait_until_resumed()
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
            elif "rate" in error_msg.lower() or "429" in error_msg or "quota" in error_msg.lower() or "insufficient" in error_msg.lower() or "余额" in error_msg or "billing" in error_msg.lower() or "balance" in error_msg.lower():
                await self._emit_error(f"API 调用频率受限或 Token 不足: {error_msg}")
                # 需求12：给出非常明确、友好的提示，而不是让讨论静默卡住
                friendly = (
                    "提示：AI 模型账户的 Token 额度或调用频率已用尽，讨论暂时无法继续。\n"
                    "您可以：\n"
                    "1) 在设置页切换到另一个仍有余额的模型（例如 DeepSeek / 豆包 / 通义千问）；\n"
                    "2) 或给当前模型账户充值后点击“继续”重试；\n"
                    "3) 当前内容已保存，随时可以恢复讨论。"
                )
                await self._emit_message("系统", friendly, "system")
                yield {
                    "event_type": "api_error",
                    "data": {
                        "message": friendly,
                        "original_error": error_msg,
                        "recoverable": True,
                        "kind": "token_exhausted",
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
            self._last_human_input_requested_speaker = ""

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
            content = self._sanitize_all_references(source, content)

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
            if not self._is_non_substantive_turn(content):
                self._speaker_message_count[source] = self._speaker_message_count.get(source, 0) + 1
                self._recent_display_speakers.append(display_source)
                if len(self._recent_display_speakers) > 16:
                    self._recent_display_speakers = self._recent_display_speakers[-16:]
            await self._record_turn_summary(source, content)

            return {
                "event_type": "message",
                "data": {"source": source, "content": content},
            }

        # 人类输入请求
        if isinstance(event, UserInputRequestedEvent):
            # 等待人类输入，带超时
            speaker = self.current_speaker or ""
            if not speaker and len(self.human_agents) == 1:
                speaker = self.human_agents[0].name
            if (
                speaker
                and speaker == self._last_human_input_requested_speaker
                and self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
            ):
                logger.info(
                    "[FloorManager] 忽略重复 human_input_requested: speaker=%s state=%s",
                    speaker,
                    self.state,
                )
                return None
            await self._set_state(FloorState.HUMAN_SPEAKING, reason="human_input_requested")
            self._last_human_input_requested_speaker = speaker
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
        self._last_human_input_requested_speaker = ""

        logger.info("[FloorManager] 收到人类输入: name=%s, text_len=%d", normalized_name, len(normalized_text))

        # 空输入直接按跳过处理，保证流程继续。
        if not normalized_text:
            logger.info("[FloorManager] 空输入，自动跳过: %s", normalized_name)
            await self._put_human_input(normalized_name, "（跳过）")
            await self._emit_message("系统", f"{normalized_name or '该同学'}未输入有效内容，已自动跳过本轮。", "system")
            return

        # 跳过指令不需要安全过滤
        if normalized_text in ("（跳过）", "(跳过)", "跳过"):
            logger.info("[FloorManager] 用户主动跳过: %s", normalized_name)
            await self._put_human_input(normalized_name, "（跳过）")
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
            await self._put_human_input(normalized_name, normalized_text)
            logger.info("[FloorManager] 人类输入已提交到队列: %s", normalized_name)
        except Exception as e:
            logger.warning("[FloorManager] 提交人类输入失败，自动跳过。name=%s, err=%s", normalized_name, e)
            await self._put_human_input(normalized_name, "（跳过）")
            await self._emit_message("系统", f"{normalized_name or '该同学'}输入处理异常，系统已自动跳过并继续讨论。", "system")

    async def request_interrupt(self, speaker: str) -> None:
        """处理打断请求。

        当参与者请求打断当前发言者时调用。
        只有人类学生可以举手打断，AI 角色不能举手。
        记录打断者，切换到 INTERRUPTED 状态，
        通知主持人进行下一轮选择。

        Args:
            speaker: 请求打断的参与者名字。
        """
        # 检查打断者是否为人类学生
        human_display_names = {
            self._agent_to_display_name.get(name, name)
            for name in self.human_names
        }
        is_human = speaker in self.human_names or speaker in human_display_names
        if not is_human:
            logger.info("[FloorManager] 非人类参与者 %s 尝试举手打断，忽略", speaker)
            return

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

        # 从"正在讲话"切换回"等待提交文本"，避免状态长期停留 HUMAN_SPEAKING。
        if self.state == FloorState.HUMAN_SPEAKING:
            await self._set_state(
                FloorState.HUMAN_TURN_WAITING,
                reason="ptt_end_waiting_input",
                recovery=True,
            )