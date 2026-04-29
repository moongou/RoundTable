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
from app.core.floor_text_utils import (
    extract_core_viewpoint,
    extract_reference_quote,
    is_non_substantive_turn,
    normalize_reference_match_text,
    score_reference_fragment,
)
from app.core.llm_errors import describe_model_error
from app.core.rolling_summary_memory import HumanResponseGuidanceMemory, RollingSummaryMemory
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

    _STREAM_SENTENCE_ENDINGS = frozenset("。！？!?；;")
    _STREAM_CLOSING_CHARS = frozenset('"\'”’）)]】》」』')
    _META_REASONING_PATTERNS = (
        re.compile(r"</?think>", re.IGNORECASE),
        re.compile(
            r"(用户现在|用户的输入|用户的消息|name\s*=\s*user|系统提示|之前的设定|真人学生发言处理规范|符合要求|复述了用户的关键表述|点评到位|引导其他角色发言|现在需要我扮演|要符合小学生的语气|带趣味性故事|温和质疑|边栏流程指引|系统触发|等待系统触发|按规范|严格基于|自身身份|严格引用|语气感|引导对继续|引导下一位发声|无编造|符合长项要求|首轮已发言|不自我引用|绝不提前|未发生发言|提前引用|/me|静候)"
        ),
        re.compile(
            r"\b(I\s*(?:should|need|want|will|must|have\s*to)|Since\s*it['’]?s|Keeping\s*it|The\s*user|Now\s*I\s*need)",
            re.IGNORECASE,
        ),
        re.compile(r"^(不对|哦，不对|不，看|不，|那我需要|重新理一下|仔细看)"),
        re.compile(r"(然后点评|然后引导|还要注意语言|这样就可以了)"),
    )
    _META_BLOCK_PATTERNS = (
        re.compile(r"\[([^\]\n]{1,220})\]"),
        re.compile(r"\(([^)\n]{1,220})\)"),
        re.compile(r"（([^）\n]{1,220})）"),
    )
    _MODERATOR_REGULAR_SENTENCE_LIMIT = 2
    _MODERATOR_INVITE_SENTENCE_LIMIT = 3
    _MODERATOR_CLOSING_SENTENCE_LIMIT = 3
    _MODERATOR_END_MARKERS = (
        "讨论结束",
        "就到这里",
        "聊到这里",
        "下次见",
        "结束啦",
    )

    def __init__(
        self,
        team: SelectorGroupChat,
        ai_agents: list[AssistantAgent],
        human_agents: list[UserProxyAgent],
        safety_filter: SafetyFilter,
        human_timeout: int = 120,
        designated_speaker_setter: Optional[Callable[[Optional[str]], None]] = None,
        summary_memory: Optional[RollingSummaryMemory] = None,
        human_guidance_memory: Optional[HumanResponseGuidanceMemory] = None,
        human_hand_raise_notifier: Optional[Callable[[str], None]] = None,
        human_queue_scope: str | None = None,
        thinker_agent_names: Optional[list[str]] = None,
    ):
        self.team = team
        self.ai_agents = ai_agents
        self.human_agents = human_agents
        self.safety_filter = safety_filter
        self.human_timeout = human_timeout
        self._set_designated_speaker = designated_speaker_setter or set_designated_speaker
        self.summary_memory = summary_memory
        self.human_guidance_memory = human_guidance_memory
        self._human_hand_raise_notifier = human_hand_raise_notifier
        self._human_queue_scope = human_queue_scope
        self.thinker_names = set(thinker_agent_names or [])

        self.ai_names = {agent.name for agent in ai_agents}
        self.human_names = {agent.name for agent in human_agents}
        self.all_names = self.ai_names | self.human_names

        self.state = FloorState.INIT
        self.current_speaker: Optional[str] = None
        self.session_id = str(uuid.uuid4())
        self._current_topic = ""
        self._pending_human_guidance = False
        self._discussion_end_requested = False

        # 消息回调：外部注册以接收事件
        self._on_message: Optional[Callable] = None
        self._on_turn_change: Optional[Callable] = None
        self._on_state_change: Optional[Callable] = None
        self._on_error: Optional[Callable] = None
        self._on_interrupt: Optional[Callable] = None

        # 打断请求队列
        self._interrupt_queue: list[str] = []

        # 流式文本缓冲
        self._streaming_buffer: dict[str, str] = {}
        self._streaming_emitted_raw_prefix: dict[str, str] = {}
        self._streaming_emitted_segment_keys: dict[str, set[str]] = {}
        self._current_streaming_source: Optional[str] = None
        self._first_human_handoff_streamed = False
        self._moderator_stream_sentence_emitted = 0
        self._dropped_ai_stream_while_human_waiting = 0
        self._dropped_ai_message_while_human_waiting = 0

        # display name → agent name 映射（需求4：用于指定发言者解析）
        self._display_name_to_agent: dict[str, str] = {}
        self._agent_to_display_name: dict[str, str] = {}
        self._thinker_display_names: set[str] = set()
        self._speaker_message_count: dict[str, int] = {name: 0 for name in self.all_names}
        self._recent_display_speakers: list[str] = []
        self._recent_turn_summaries: list[tuple[str, str]] = []
        self._recent_reference_quotes: list[tuple[str, str]] = []
        self._moderator_roleplay_target: Optional[str] = None

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
        self._human_input_request_seq = 0
        self._pending_human_input_reason = "normal"
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

    def diagnostics(self) -> dict[str, int]:
        return {
            "dropped_ai_stream_while_human_waiting": self._dropped_ai_stream_while_human_waiting,
            "dropped_ai_message_while_human_waiting": self._dropped_ai_message_while_human_waiting,
        }

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

    def _get_spoken_display_names(self) -> set[str]:
        """返回整场讨论里实际产生过有效发言的展示名集合。"""
        spoken = set(self._recent_display_speakers)
        for agent_name, count in self._speaker_message_count.items():
            if count > 0:
                spoken.add(self._agent_to_display_name.get(agent_name, agent_name))
        return spoken

    def _get_all_display_names(self) -> list[str]:
        seen: set[str] = set()
        names: list[str] = []
        for agent_name in [*self.ai_names, *self.human_names]:
            display_name = self._agent_to_display_name.get(agent_name, agent_name)
            if display_name and display_name not in seen:
                seen.add(display_name)
                names.append(display_name)
        return names

    def _preferred_human_agent_name(self) -> str:
        for agent in self.human_agents:
            name = getattr(agent, "name", "")
            if name in self.human_names:
                return name
        return sorted(self.human_names)[0] if self.human_names else ""

    def _has_human_spoken(self) -> bool:
        return any(self._speaker_message_count.get(name, 0) > 0 for name in self.human_names)

    def _non_human_turn_count_before_first_human(self) -> int:
        return sum(
            count
            for name, count in self._speaker_message_count.items()
            if name not in self.human_names and name != "moderator" and count > 0
        )

    def _should_force_first_human_invite(self) -> bool:
        return (
            bool(self.human_names)
            and not self._has_human_spoken()
            and self._non_human_turn_count_before_first_human() >= 2
            and self.state not in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
        )

    def _build_first_human_handoff_text(self, original_text: str = "") -> str:
        human_agent = self._preferred_human_agent_name()
        human_display = self._agent_to_display_name.get(human_agent, human_agent)
        human_vocative = self._format_display_vocative(human_display)
        handoff = f"我们先把麦克风交给{human_vocative}。{human_vocative}，你怎么看？"

        lead = ""
        display_names = self._get_all_display_names()
        segments, _ = self._drain_complete_stream_sentences(original_text or "")
        for segment in segments[:2]:
            compact = re.sub(r"\s+", "", segment)
            if not compact or len(compact) > 44:
                continue
            if re.search(r"(请|想问问|问问|想听听|听听|有请|邀请|轮到|下一位|接下来|交给|发言)", segment):
                continue
            if display_names and parse_speaker_designation(segment, display_names):
                continue
            lead = segment.strip()
            break

        return f"{lead}{handoff}" if lead else handoff

    def _force_first_human_invitation(
        self,
        source: str,
        text: str,
        designated: Optional[str],
    ) -> tuple[str, Optional[str], bool]:
        if source != "moderator" or not text or not self._should_force_first_human_invite():
            return text, designated, False

        human_agent = self._preferred_human_agent_name()
        if not human_agent:
            return text, designated, False
        human_display = self._agent_to_display_name.get(human_agent, human_agent)
        forced_text = self._build_first_human_handoff_text(text)
        if forced_text != text:
            logger.info(
                "[FloorManager] 首次真人发言已到期，强制把麦克风交给: %s",
                human_agent,
            )
        return forced_text, human_display, True

    def _force_first_human_stream_segment(self, source: str, segment: str) -> str:
        if source != "moderator" or not segment or not self._should_force_first_human_invite():
            return segment

        display_names = self._get_all_display_names()
        designated = parse_speaker_designation(segment, display_names) if display_names else None
        inviteish = bool(
            re.search(
                r"(请|想问问|问问|想听听|听听|有请|邀请|轮到|下一位|接下来|交给|发言|你怎么看)",
                segment,
            )
        )
        if not designated and not inviteish:
            return segment

        if self._first_human_handoff_streamed:
            return "" if inviteish else segment

        self._first_human_handoff_streamed = True
        return self._build_first_human_handoff_text("")

    def _display_role_suffix(self, display_name: str, fallback: str = "同学") -> str:
        agent_name = self._display_name_to_agent.get(display_name, display_name)
        if agent_name in self.thinker_names or display_name in self._thinker_display_names:
            return "先生"
        if agent_name == "moderator":
            return ""
        return fallback or "同学"

    def _format_display_vocative(self, display_name: str, fallback: str = "同学") -> str:
        suffix = self._display_role_suffix(display_name, fallback=fallback)
        if not suffix or display_name.endswith(suffix):
            return display_name
        return f"{display_name}{suffix}"

    def _sanitize_repeated_self_invitation(self, text: str, *, last_display: str) -> str:
        """避免主持人刚点评完上一位，又点上一位评价自己的发言。"""
        if not last_display:
            return text

        escaped_last = re.escape(last_display)
        last_suffix = self._display_role_suffix(last_display)
        honorific_pattern = r"(?:同学|先生)?"
        target_label = self._format_display_vocative(last_display, fallback=last_suffix or "同学")

        replacements = [
            (
                rf"请\s*{escaped_last}{honorific_pattern}\s*(?:来说|来谈|谈谈|说说|讲讲|分享|回应|补充)[一下吧吗呢]*[，,:：]?\s*你怎么看\s*{escaped_last}{honorific_pattern}",
                f"请其他同学说说，大家怎么看{target_label}",
            ),
            (
                rf"{escaped_last}{honorific_pattern}[，,:：]\s*你怎么看\s*{escaped_last}{honorific_pattern}",
                f"请其他同学说说，大家怎么看{target_label}",
            ),
            (
                rf"请\s*{escaped_last}{honorific_pattern}\s*(?:来说|来谈|谈谈|说说|讲讲|分享|回应|补充)[一下吧吗呢]*",
                "请其他同学说说",
            ),
        ]
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text)
        return text

    def _has_moderator_invitation_intent(self, text: str) -> bool:
        return bool(
            re.search(
                r"(请|想问问|问问|想听听|听听|有请|邀请|轮到|下一位|接下来|交给|发言|你怎么看)",
                text or "",
            )
        )

    def _limit_text_sentence_count(self, text: str, *, max_sentences: int) -> str:
        value = (text or "").strip()
        if not value or max_sentences <= 0:
            return ""

        segments, remainder = self._drain_complete_stream_sentences(value)
        sentence_units = [*segments]
        if remainder:
            sentence_units.append(remainder)
        if len(sentence_units) <= max_sentences:
            return value
        return "".join(sentence_units[:max_sentences]).strip()

    def _enforce_moderator_brevity(self, text: str, *, is_final_closing: bool) -> str:
        value = (text or "").strip()
        if not value:
            return ""
        if is_final_closing:
            limit = self._MODERATOR_CLOSING_SENTENCE_LIMIT
        elif self._has_moderator_invitation_intent(value):
            limit = self._MODERATOR_INVITE_SENTENCE_LIMIT
        else:
            limit = self._MODERATOR_REGULAR_SENTENCE_LIMIT
        return self._limit_text_sentence_count(value, max_sentences=limit)

    def _sanitize_unknown_student_vocatives(self, text: str, *, last_display: str) -> str:
        value = text or ""
        known_names = set(self._agent_to_display_name.values())

        def repl(match: re.Match[str]) -> str:
            name = (match.group('name') or "").strip()
            suffix = (match.group('suffix') or "").strip()
            if name in known_names:
                return match.group(0)
            if last_display:
                fallback = "先生" if suffix == "先生" else "同学"
                return self._format_display_vocative(last_display, fallback=fallback)
            return "有同学"

        return re.sub(
            r"(?P<name>小[\u4e00-\u9fff]{1,2})(?P<suffix>同学|哥哥|姐姐|先生)",
            repl,
            value,
        )

    def _sanitize_ungrounded_neutral_quote_claims(self, text: str) -> str:
        value = text or ""

        def repl(match: re.Match[str]) -> str:
            fragment = (match.group('fragment') or "").strip()
            if not fragment:
                return match.group(0)
            owner = self._guess_reference_owner(fragment)
            if owner:
                return f"{owner}提到“{fragment}”"
            return "这个点"

        return re.sub(
            r"有同学(?:刚才)?(?:提到|说|讲|分享|指出)(?:的)?[“\"「『](?P<fragment>[^”\"」』]{2,80})[”\"」』]",
            repl,
            value,
        )

    def _is_traceable_quote_fragment(self, fragment: str) -> bool:
        normalized = normalize_reference_match_text(fragment)
        if len(normalized) < 2:
            return False

        # 与最近真实发言可对齐，才允许保留引号片段。
        if self._guess_reference_owner(fragment):
            return True

        topic_norm = normalize_reference_match_text(self._current_topic)
        if topic_norm and len(normalized) >= 4 and normalized in topic_norm:
            return True

        return False

    def _sanitize_moderator_quote_whitelist(self, text: str) -> str:
        value = text or ""

        def rewrite_untraceable_quoted_label(match: re.Match[str]) -> str:
            fragment = (match.group('fragment') or "").strip()
            if not fragment:
                return "这个点"
            if self._is_traceable_quote_fragment(fragment):
                return match.group(0)
            return "这个点"

        value = re.sub(
            r"[“\"「『](?P<fragment>[^”\"」』]{2,80})[”\"」』]\s*这个(?:比喻|说法|提法|称呼|词|表达)",
            rewrite_untraceable_quoted_label,
            value,
        )

        def rewrite_teacher_self_quote(match: re.Match[str]) -> str:
            prefix = match.group('prefix') or ''
            fragment = (match.group('fragment') or "").strip()
            if not fragment:
                return f"{prefix}这个点"
            owner = self._guess_reference_owner(fragment)
            if owner:
                return f"{owner}刚才说“{fragment}”"
            return f"{prefix}这个点"

        value = re.sub(
            r"(?P<prefix>老师|我)(?:刚才)?(?:说|提到|讲到|提出|分享)[“\"「『](?P<fragment>[^”\"」』]{2,80})[”\"」』]",
            rewrite_teacher_self_quote,
            value,
        )

        value = re.sub(r"这个点\s*这个点", "这个点", value)
        return value

    def _ensure_moderator_explicit_goodbye(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return value
        compact = re.sub(r"\s+", "", value)
        if "再见" in compact:
            return value
        if not any(marker in compact for marker in self._MODERATOR_END_MARKERS):
            return value
        return f"{value} 再见！"

    def _rewrite_unspoken_named_attribution(
        self,
        text: str,
        *,
        name: str,
        last_display: str,
    ) -> str:
        """把未实际发言者的命名归因改写为最近真实发言者或中性表述。"""
        escaped_name = re.escape(name)
        replacement_prefix = f"刚才{last_display}" if last_display else "刚才有同学"

        def rewrite_named_possessive_quote(match: re.Match[str]) -> str:
            fragment = match.group('fragment')
            noun = match.group('noun')
            return f"{replacement_prefix}提出的“{fragment}”{noun}"

        text = re.sub(
            rf"{escaped_name}(?:同学|先生)?的[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』](?P<noun>玩法|说法|计划|想法|观点|比喻|例子|问题|疑问|思路|表述)",
            rewrite_named_possessive_quote,
            text,
        )

        if last_display:
            escaped_last = re.escape(last_display)
            text = re.sub(
                rf"{escaped_name}(?:同学|先生)?和{escaped_last}(?:同学|先生)?说([得的])",
                rf"{last_display}说\1",
                text,
            )
            text = re.sub(
                rf"{escaped_last}(?:同学|先生)?和{escaped_name}(?:同学|先生)?说([得的])",
                rf"{last_display}说\1",
                text,
            )

        rewrite_specs = [
            (
                rf"{escaped_name}(?:同学|先生)?[，,:：]\s*你(提出的|刚才说的|刚才提到的|说的|说得|提到的|讲到的|分享的|质疑的|追问的)",
                rf"{replacement_prefix}\1",
            ),
            (
                rf"面对\s*{escaped_name}(?:同学|先生)?\s*(提出的|刚才说的|刚才提到的|说的|说得|提到的|讲到的|分享的|质疑的|追问的)",
                rf"面对{replacement_prefix}\1",
            ),
            (
                rf"再到\s*{escaped_name}(?:同学|先生)?\s*(提出的|刚才说的|刚才提到的|说的|说得|提到的|讲到的|分享的|质疑的|追问的)",
                rf"再到{replacement_prefix}\1",
            ),
            (rf"{escaped_name}(?:同学|先生)?\s*对于", f"{replacement_prefix}对于"),
            (
                rf"{escaped_name}(?:同学|先生)?\s*(提出了|提到了|讲到了|分享了|质疑了|追问了)",
                rf"{replacement_prefix}\1",
            ),
            (
                rf"{escaped_name}(?:同学|先生)?\s*(提出的|说的|说得|提到的|讲到的|分享的|质疑的|追问的)",
                rf"{replacement_prefix}\1",
            ),
        ]
        for pattern, replacement in rewrite_specs:
            text = re.sub(pattern, replacement, text)

        if last_display:
            escaped_last = re.escape(last_display)
            text = re.sub(
                rf"({escaped_last}(?:同学|先生)?)[，,:：]\s*刚才{escaped_last}(?:同学|先生)?",
                r"\1，你刚才",
                text,
            )

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
        spoke_set = self._get_spoken_display_names()

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
                    rf"{re.escape(name)}(?:同学|先生)?\s*(提了|问了|说了|想到了|建议了|补充了|指出了)",
                    r"刚才有同学\1",
                    text,
                )
                text = re.sub(
                    rf"(谢谢|感谢|多谢)\s*{re.escape(name)}(?:同学|先生)?",
                    rf"\1{self._format_display_vocative(last_display)}",
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?[，,:：]?\s*你\s*(这个|刚才|说得|讲得|提到|提得|问得|分享)",
                    rf"{self._format_display_vocative(last_display)}，你\1",
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?\s*(?:说得|讲得|问得|提得)(?:太|真|特别|非常|挺|很|也)?(?:好|棒|精彩|到位)",
                    rf"{self._format_display_vocative(last_display)}，你这个点说得很到位",
                    text,
                )
                text = self._rewrite_unspoken_named_attribution(
                    text,
                    name=name,
                    last_display=last_display,
                )
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
                    rf"{re.escape(name)}(?:同学|先生)?这(只|个|件|条|艘|座|种|份)",
                    r"这\1",
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

        # ── 规则 C：呼语式错认（critical） ─────────────────────────────────
        # 老师/同学常见错把上一位发言者的内容夸到另一位身上，例如：
        #   "豆苗同学你讲得太形象了"（实际上一位是小爱）
        #   "豆苗同学问得太好了！'一会儿'到底有多长？"（实际是小疑问的）
        #   "小爱你这段话说得太精彩了！你刚才用'妈妈买牛奶顺便买零食'..."
        # （实际上一位是小和）
        # 这里不依赖名字是否完全没说过，而是只看“最近真正的发言者”。
        # 如果文本里以 X 作为称呼对象做了直接表扬/复述，但 X != last_display
        # 也 != 当前发言者自己，则把 X 改写成 last_display。
        compliment_verbs = (
            r"讲得?(?:太|真|特别|非常|挺|很)"
            r"|说得?(?:太|真|特别|非常|挺|很)"
            r"|问得?(?:太|真|特别|非常|挺|很|好)"
            r"|答得?(?:太|真|特别|非常|挺|很|好)"
            r"|想得?(?:太|真|特别|非常|挺|很|好)"
            r"|提得?(?:太|真|特别|非常|挺|很|好)"
            r"|分析得?(?:太|真|特别|非常|挺|很|好)"
            r"|说得?(?:对|好|妙|棒|精彩)"
            r"|讲得?(?:好|妙|棒|精彩)"
            r"|问得?(?:好|妙|棒|精彩|到点子上)"
            r"|这段话(?:说|讲|问)得"
            r"|这话(?:说|讲|问)得"
            r"|这个问题问得?"
            r"|这个(?:比喻|说法|想法|观点|例子|问题|疑问)(?:真|太|特别)"
            r"|你刚才(?:用|说|讲|提|问)"
            r"|你提出的"
            r"|你这话"
            r"|你这个"
            r"|你帮大家"
            r"|你不仅"
            r"|你点(?:出|到)"
            r"|提醒得?(?:也)?(?:太|真|特别|非常|挺|很|到位)"
        )
        for name in display_names:
            if name == last_display:
                continue
            if name == current_display and source != "moderator":
                continue

            def rewrite_compliment_vocative(
                match: re.Match[str],
                *,
                original_name: str = name,
                target_display: str = last_display,
            ) -> str:
                following = text[match.end() : match.end() + 90]
                quoted = re.match(r"[“\"「『](?P<fragment>[^”\"」』]{2,80})[”\"」』]", following)
                if quoted and self._guess_reference_owner(quoted.group('fragment')) == original_name:
                    return match.group(0)
                verb = match.group('verb')
                pronoun = "" if verb.startswith("你") else "你"
                return (
                    f"{self._format_display_vocative(target_display, fallback=match.group(1) or '同学')}，"
                    f"{pronoun}{verb}"
                )

            text = re.sub(
                rf"{re.escape(name)}(同学|先生)?[，,:：]?\s*(你?)(?P<verb>{compliment_verbs})",
                rewrite_compliment_vocative,
                text,
            )
            # "X你这段话/这话/这次..." 单独再保险一次
            text = re.sub(
                rf"{re.escape(name)}(?:同学|先生)?\s*你这(段话|次|句|个)",
                rf"{self._format_display_vocative(last_display)}，你这\1",
                text,
            )

        # ── 规则 D：句首呼语+赞叹/感叹兜底 ────────────────────────────────
        # 模式："{X}{同学/先生}?[，,]?\s*(?:你这|你的|你刚才的)?(?:这|那)?(问题|说法|比喻|想法|观点|例子|疑问)"
        # 当 X != last_display 且 X != current_display 时，强制改写为 last_display。
        for name in display_names:
            if name == last_display:
                continue
            if name == current_display and source != "moderator":
                continue
            text = re.sub(
                rf"^{re.escape(name)}(同学|先生)?[，,]?\s*(?:你)?(这|那)?(?:个|段|次|句)?\s*(问题|说法|比喻|想法|观点|例子|疑问|答案|思路|表述)",
                lambda m, _ld=last_display: f"{self._format_display_vocative(_ld, fallback=m.group(1) or '同学')}，你这个{m.group(3)}",
                text,
                flags=re.MULTILINE,
            )

        if source == "moderator":
            text = self._sanitize_repeated_self_invitation(text, last_display=last_display)

        text = self._sanitize_unknown_student_vocatives(text, last_display=last_display)
        text = self._sanitize_ungrounded_neutral_quote_claims(text)
        if source == "moderator":
            text = self._sanitize_moderator_quote_whitelist(text)

        text = re.sub(r"\s{2,}", " ", text).strip()
        text = re.sub(r"^[，,:：\s]+", "", text)
        return text

    def _sanitize_moderator_roleplay(self, source: str, content: str) -> str:
        """防止主持人替其他角色直接发言。"""
        text = (content or "").strip()
        if source != "moderator" or not text:
            return text

        display_names = sorted(set(self._agent_to_display_name.values()), key=len, reverse=True)
        for display_name in display_names:
            if not display_name or display_name == self._agent_to_display_name.get("moderator", "moderator"):
                continue
            suffix = self._display_role_suffix(display_name)
            escaped = re.escape(display_name)
            if re.match(rf"^[（(]?\s*{escaped}(?:同学|先生)?[，,:：]?\s*你", text):
                continue
            roleplay_pattern = re.compile(
                rf"^[（(]?\s*{escaped}(?:同学|先生)?[^。！？!?]{{0,40}}"
                rf"(?:思考|想了想|认真地说|说|回答|答道|表示|认为)[^。！？!?]{{0,20}}[）)]?\s*"
                rf"(?:老师[，,:：]\s*)?"
            )
            if roleplay_pattern.search(text):
                self._moderator_roleplay_target = display_name
                return f"请{self._format_display_vocative(display_name, fallback=suffix or '同学')}发言。"

        if self._moderator_roleplay_target:
            target = self._moderator_roleplay_target
            if re.search(rf"请\s*{re.escape(target)}(?:同学|先生)?\s*发言", text):
                return text
            # 角色扮演的后续细节由第一句邀请替代，避免老师继续替学生输出观点。
            return ""

        return text

    def _sanitize_all_references(self, source: str, content: str) -> str:
        text = self._sanitize_moderator_roleplay(source, content)
        if not text:
            return ""
        text = self._sanitize_opening_reference(source, text)
        text = self._sanitize_grounded_quote_attribution(text)
        return self._sanitize_reference_attribution(source, text)

    def _topic_focus_label(self) -> str:
        topic = (self._current_topic or "").strip()
        if not topic:
            return ""
        first_line = next((line.strip() for line in topic.splitlines() if line.strip()), "")
        focus = first_line or topic
        if len(focus) > 32:
            focus = focus[:32].rstrip("，,；;、 ") + "…"
        return focus

    def _pick_recent_peer_focus(self, current_name: str) -> tuple[str, str] | None:
        current_display = self._agent_to_display_name.get(current_name, current_name)
        moderator_display = self._agent_to_display_name.get("moderator", "moderator")
        for speaker, summary in reversed(self._recent_turn_summaries):
            if not speaker or not summary:
                continue
            if speaker in {current_display, moderator_display}:
                continue
            return speaker, summary
        return None

    def _classify_human_input(self, current_name: str, text: str) -> str:
        normalized = normalize_reference_match_text(text)
        if not normalized:
            return "weak"

        weak_markers = (
            "不知道",
            "没想法",
            "随便",
            "都行",
            "还行",
            "就这样",
            "没了",
            "不知道说什么",
            "我不知道",
            "嗯",
            "啊",
        )
        current_display = self._agent_to_display_name.get(current_name, current_name)
        peer_mentions = any(
            normalize_reference_match_text(display_name)
            and normalize_reference_match_text(display_name) in normalized
            for display_name in self._display_name_to_agent.keys()
            if display_name and display_name != current_display
        )

        topical_candidates: list[str] = []
        if self._current_topic:
            topical_candidates.append(self._current_topic)
        topical_candidates.extend(
            summary
            for speaker, summary in self._recent_turn_summaries
            if speaker != current_display and summary
        )
        topical_candidates.extend(
            quote
            for speaker, quote in self._recent_reference_quotes
            if speaker != current_display and quote
        )
        topical_score = max(
            (score_reference_fragment(text, candidate) for candidate in topical_candidates if candidate),
            default=0,
        )

        if len(normalized) <= 6 and topical_score < 6 and not peer_mentions:
            return "weak"
        if any(marker in normalized for marker in weak_markers) and len(normalized) <= 10 and not peer_mentions:
            return "weak"
        if len(normalized) >= 8 and topical_candidates and topical_score < 4 and not peer_mentions:
            return "off_topic"
        return "on_topic"

    def _build_human_guidance(self, current_name: str, text: str) -> dict[str, Any] | None:
        summary = extract_core_viewpoint(text)
        if not summary:
            return None

        assessment = self._classify_human_input(current_name, text)
        if assessment == "on_topic":
            return None

        guidance: dict[str, Any] = {
            "speaker": self._agent_to_display_name.get(current_name, current_name),
            "summary": summary,
            "assessment": assessment,
            "topic_focus": self._topic_focus_label(),
        }
        peer_focus = self._pick_recent_peer_focus(current_name)
        if peer_focus is not None:
            guidance["suggested_peer_name"] = peer_focus[0]
            guidance["suggested_peer_summary"] = peer_focus[1]
        return guidance

    def _guess_reference_owner(self, fragment: str) -> str | None:
        best_name = ""
        best_score = 0
        tied = False
        for speaker, quote in reversed(self._recent_reference_quotes):
            score = score_reference_fragment(fragment, quote)
            if score <= 0:
                continue
            if score > best_score:
                best_name = speaker
                best_score = score
                tied = False
            elif score == best_score and speaker != best_name:
                tied = True

        if best_score < 3 or tied:
            return None
        return best_name

    def _format_reference_name(self, name: str, honorific: str) -> str:
        if not honorific or name.endswith(honorific):
            return name
        return f"{name}{honorific}"

    def _sanitize_grounded_quote_attribution(self, text: str) -> str:
        value = (text or "").strip()
        if not value or not self._recent_reference_quotes:
            return value

        display_names = sorted(set(self._agent_to_display_name.values()), key=len, reverse=True)
        if not display_names:
            return value
        names_pattern = "|".join(re.escape(name) for name in display_names)
        invalidated_names: set[str] = set()

        def rewrite_named_vocative(match: re.Match[str]) -> str:
            name = match.group('name')
            honorific = match.group('honorific') or ''
            verb = match.group('verb')
            fragment = match.group('fragment')
            owner = self._guess_reference_owner(fragment)
            if owner == name:
                return match.group(0)
            if owner:
                invalidated_names.add(name)
                return f"{self._format_reference_name(owner, honorific)}，你{verb}“{fragment}”"
            invalidated_names.add(name)
            return f"有同学{verb}“{fragment}”"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?[，,:：]?\s*你(?P<verb>刚才说的?|刚才提到的?|说的?|提到的?|讲到的?|提出的?|分享的?|质疑的?|追问的?)[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』]",
            rewrite_named_vocative,
            value,
        )
        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?[，,:：]?\s*你(?P<verb>刚才说的?|刚才提(?:到)?的?(?:那个)?|刚才讲到的?|刚才提出的?|刚才分享的?|说的?|提到的?|讲到的?|提出的?|分享的?|质疑的?|追问的?)[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』]",
            rewrite_named_vocative,
            value,
        )

        def rewrite_named_report(match: re.Match[str]) -> str:
            name = match.group('name')
            honorific = match.group('honorific') or ''
            verb = match.group('verb')
            fragment = match.group('fragment')
            owner = self._guess_reference_owner(fragment)
            if owner == name:
                return match.group(0)
            if owner:
                invalidated_names.add(name)
                return f"{self._format_reference_name(owner, honorific)}{verb}“{fragment}”"
            invalidated_names.add(name)
            return f"有同学{verb}“{fragment}”"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?(?:还)?\s*(?P<verb>说的?|提到的?|讲到的?|提出的?|分享的?|质疑的?|追问的?)[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』]",
            rewrite_named_report,
            value,
        )
        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?(?:还)?\s*(?P<verb>刚才说的?|刚才提到的?|刚才讲到的?|刚才提出的?|刚才分享的?|刚才质疑的?|刚才追问的?)[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』]",
            rewrite_named_report,
            value,
        )

        def rewrite_named_object(match: re.Match[str]) -> str:
            name = match.group('name')
            fragment = match.group('fragment')
            owner = self._guess_reference_owner(fragment)
            if owner == name:
                return match.group(0)
            invalidated_names.add(name)
            if owner:
                return f"{owner}提到的“{fragment}”"
            return f"有同学提到的“{fragment}”"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?这个[“\"「『](?P<fragment>[^”\"」』]{{2,40}})[”\"」』]",
            rewrite_named_object,
            value,
        )

        def rewrite_sentence_pronoun(match: re.Match[str]) -> str:
            prefix = match.group('prefix') or ''
            verb = match.group('verb')
            fragment = match.group('fragment')
            owner = self._guess_reference_owner(fragment)
            if owner:
                return f"{prefix}{owner}{verb}“{fragment}”"
            return f"{prefix}有同学{verb}“{fragment}”"

        value = re.sub(
            r"(?P<prefix>^|[。！？!?]\s*)你(?P<verb>刚才说的?|刚才提到的?|说的?|提到的?|讲到的?|提出的?|分享的?|质疑的?|追问的?)[“\"「『](?P<fragment>[^”\"」』]{2,80})[”\"」』]",
            rewrite_sentence_pronoun,
            value,
        )
        value = re.sub(
            r"(?P<prefix>(?:^|[；;:：。！？!?—-]+\s*)(?:还有|而且|另外)?\s*|[，,]\s*(?:还有|而且|另外)\s*)你(?P<verb>刚才说的?|刚才提到的?|刚才讲到的?|刚才提出的?|说的?|提到的?|讲到的?|提出的?|分享的?)[“\"「『](?P<fragment>[^”\"」』]{2,80})[”\"」』]",
            rewrite_sentence_pronoun,
            value,
        )

        for name in invalidated_names:
            value = re.sub(
                rf"{re.escape(name)}(?:同学|先生)?[，,:：]?\s*你这个[^。！？!?]{{0,36}}[。！？!?]",
                "",
                value,
            )
            value = re.sub(
                rf"{re.escape(name)}(?:同学|先生)?这个(比喻|问题|疑惑|想法|说法)",
                r"这个\1",
                value,
            )

        value = re.sub(r"\s{2,}", " ", value).strip()
        value = re.sub(r"^[，,:：\s]+", "", value)
        return value

    async def _record_turn_summary(self, source: str, content: str) -> None:
        if self.summary_memory is None or source not in self.all_names:
            return
        text = (content or "").strip()
        if not text or is_non_substantive_turn(text):
            return
        display_source = self._agent_to_display_name.get(source, source)
        summary = extract_core_viewpoint(text)
        if not summary:
            return
        self._recent_turn_summaries.append((display_source, summary))
        if len(self._recent_turn_summaries) > 3:
            self._recent_turn_summaries = self._recent_turn_summaries[-3:]
        quote = extract_reference_quote(text)
        if quote:
            self._recent_reference_quotes.append((display_source, quote))
            if len(self._recent_reference_quotes) > 5:
                self._recent_reference_quotes = self._recent_reference_quotes[-5:]
        spoken_names = sorted(self._get_spoken_display_names())
        all_display_names = self._get_all_display_names()
        unspoken_names = [name for name in all_display_names if name not in set(spoken_names)]
        await self.summary_memory.replace_turn_summaries(
            self._recent_turn_summaries,
            spoken_names=spoken_names,
            unspoken_names=unspoken_names,
            recent_quotes=self._recent_reference_quotes,
        )

    def _drain_complete_stream_sentences(self, text: str) -> tuple[list[str], str]:
        """从流式文本中提取已完成句子，保留尚未完结的尾段。"""
        value = (text or "").strip()
        if not value:
            return [], ""

        segments: list[str] = []
        start = 0
        for index, char in enumerate(value):
            if char not in self._STREAM_SENTENCE_ENDINGS:
                continue
            end = index + 1
            while end < len(value) and value[end] in self._STREAM_CLOSING_CHARS:
                end += 1
            segment = value[start:end].strip()
            if segment:
                segments.append(segment)
            while end < len(value) and value[end].isspace():
                end += 1
            start = end

        return segments, value[start:].strip()

    def _consume_streaming_sentences(self, source: str, content: str) -> list[str]:
        current = f"{self._streaming_buffer.get(source, '')}{content}"
        segments, remainder = self._drain_complete_stream_sentences(current)
        self._streaming_buffer[source] = remainder
        return segments

    def _mark_streaming_segments_emitted(self, source: str, segments: list[str]) -> None:
        if not segments:
            return
        self._streaming_emitted_raw_prefix[source] = (
            f"{self._streaming_emitted_raw_prefix.get(source, '')}{''.join(segments)}"
        )

    def _looks_like_meta_reasoning_segment(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text or "")
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in self._META_REASONING_PATTERNS)

    def _strip_meta_reasoning_blocks(self, text: str) -> str:
        value = text or ""
        for pattern in self._META_BLOCK_PATTERNS:
            value = pattern.sub(
                lambda match: ""
                if self._looks_like_meta_reasoning_segment(match.group(1))
                else match.group(0),
                value,
            )
        return value

    def _strip_meta_reasoning_text(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return ""

        value = self._strip_meta_reasoning_blocks(value)
        kept_lines: list[str] = []
        for raw_line in re.split(r"(?:\r?\n)+", value):
            line = raw_line.strip()
            if not line:
                continue
            if self._looks_like_meta_reasoning_segment(line):
                continue
            line = re.sub(r"^[>\-*•\s]+", "", line).strip()
            if not line:
                continue

            segments, remainder = self._drain_complete_stream_sentences(line)
            kept_segments = [
                segment.strip()
                for segment in segments
                if segment.strip() and not self._looks_like_meta_reasoning_segment(segment)
            ]
            remainder = remainder.strip()
            if remainder and not self._looks_like_meta_reasoning_segment(remainder):
                kept_segments.append(remainder)

            cleaned_line = "".join(kept_segments).strip()
            cleaned_line = re.sub(r"\s{2,}", " ", cleaned_line).strip()
            cleaned_line = re.sub(r"^[，,；;:：\-*\s]+", "", cleaned_line)
            if cleaned_line:
                kept_lines.append(cleaned_line)

        return "\n".join(kept_lines).strip()

    def _normalize_streaming_segment(self, text: str) -> str:
        value = re.sub(r"\s+", "", text or "")
        return re.sub(r"[。！？!?；;\"'”’）)\]】》」』]+$", "", value)

    def _is_duplicate_streaming_segment(self, source: str, text: str) -> bool:
        key = self._normalize_streaming_segment(text)
        if not key:
            return False
        seen = self._streaming_emitted_segment_keys.setdefault(source, set())
        if key in seen:
            return True
        seen.add(key)
        return False

    def _filter_streamed_tail_duplicates(self, text: str, emitted_keys: set[str]) -> str:
        if not text or not emitted_keys:
            return text

        segments, remainder = self._drain_complete_stream_sentences(text)
        filtered_segments = [
            segment
            for segment in segments
            if self._normalize_streaming_segment(segment) not in emitted_keys
        ]
        filtered_remainder = remainder.strip()
        if filtered_remainder:
            normalized_remainder = self._normalize_streaming_segment(filtered_remainder)
            if normalized_remainder and normalized_remainder in emitted_keys:
                filtered_remainder = ""

        parts = filtered_segments
        if filtered_remainder:
            parts.append(filtered_remainder)
        return "".join(parts).strip()

    def _pop_streaming_message_tail(self, source: str, raw_content: str) -> tuple[bool, str]:
        """在最终消息到达时，返回尚未通过流式播报过的原始尾段。"""
        remainder = self._streaming_buffer.pop(source, "")
        emitted_prefix = self._streaming_emitted_raw_prefix.pop(source, "")
        emitted_segment_keys = self._streaming_emitted_segment_keys.pop(source, set())
        if self._current_streaming_source == source:
            self._current_streaming_source = None

        if not emitted_prefix:
            return False, raw_content
        if raw_content.startswith(emitted_prefix):
            tail = raw_content[len(emitted_prefix):].lstrip()
            return True, self._filter_streamed_tail_duplicates(tail, emitted_segment_keys)

        emitted_segments, _ = self._drain_complete_stream_sentences(emitted_prefix)
        final_segments, final_remainder = self._drain_complete_stream_sentences(raw_content)
        matched_segments = 0
        for emitted_segment, final_segment in zip(emitted_segments, final_segments):
            if self._normalize_streaming_segment(emitted_segment) != self._normalize_streaming_segment(final_segment):
                break
            matched_segments += 1

        if matched_segments > 0:
            unspoken_parts = final_segments[matched_segments:]
            if final_remainder:
                unspoken_parts.append(final_remainder)
            tail = "".join(unspoken_parts).strip()
            return True, self._filter_streamed_tail_duplicates(tail, emitted_segment_keys)

        if remainder:
            tail = remainder.strip()
            return True, self._filter_streamed_tail_duplicates(tail, emitted_segment_keys)

        logger.info(
            "[FloorManager] 流式尾段前缀未对齐，丢弃可能重复的最终 TTS: source=%s emitted_len=%d raw_len=%d",
            source,
            len(emitted_prefix),
            len(raw_content),
        )
        tail = self._filter_streamed_tail_duplicates(raw_content, emitted_segment_keys)
        return True, tail if tail != raw_content else ""

    def _is_repeated_non_moderator_turn(self, source: str) -> bool:
        if source not in self.ai_names or source == "moderator":
            return False
        current_display = self._agent_to_display_name.get(source, source)
        return bool(
            self._recent_display_speakers
            and self._recent_display_speakers[-1] == current_display
        )

    def _should_suppress_ai_while_human_waiting(self, source: str) -> bool:
        return (
            self.state == FloorState.HUMAN_TURN_WAITING
            and source in self.ai_names
        )

    async def _make_human_input_requested_event(
        self,
        speaker: str,
        *,
        reason: str,
        clear_designation: bool = True,
    ) -> Optional[dict]:
        if not speaker or speaker not in self.human_names:
            return None
        if (
            speaker == self._last_human_input_requested_speaker
            and self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
        ):
            logger.info(
                "[FloorManager] 忽略重复 human_input_requested: speaker=%s state=%s",
                speaker,
                self.state,
            )
            return None
        self.current_speaker = speaker
        if clear_designation:
            self._set_designated_speaker(None)
        self._last_human_input_requested_speaker = speaker
        if self.state != FloorState.HUMAN_TURN_WAITING:
            await self._set_state(FloorState.HUMAN_TURN_WAITING, reason=reason)
        else:
            self._touch_progress(reason)
        self._human_input_request_seq += 1
        request_id = f"hr-{self._human_input_request_seq}"
        return {
            "event_type": "human_input_requested",
            "data": {
                "speaker": speaker,
                "reason": self._pending_human_input_reason or "normal",
                "request_id": request_id,
                "state": self.state.value,
            },
        }

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
        self._thinker_display_names = {
            display_name
            for agent_name, display_name in agent_to_display.items()
            if agent_name in self.thinker_names
        }

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
        self._current_topic = (topic or "").strip()
        self._pending_human_guidance = False
        self._discussion_end_requested = False
        self._recent_turn_summaries.clear()
        if self.summary_memory is not None:
            await self.summary_memory.clear()
        if self.human_guidance_memory is not None:
            await self.human_guidance_memory.clear()
        had_error = False

        try:
            stream = self.team.run_stream(task=topic)

            async for event in stream:
                if self._paused:
                    await self._wait_until_resumed()
                result = await self._process_event(event)
                if result:
                    yield result
                    if self._discussion_end_requested:
                        logger.info("[FloorManager] 主持人结束语已发出，停止后续调度")
                        break

        except Exception as e:
            had_error = True
            error_info = describe_model_error(e)
            logger.error(
                "讨论运行异常: %s: %s",
                type(e).__name__,
                error_info.technical_detail or str(e),
                exc_info=True,
            )
            yield {
                "event_type": "api_error" if error_info.is_model_error else "error",
                "data": error_info.to_event_data(),
            }
        finally:
            if (
                not had_error
                and not self._discussion_end_requested
                and "moderator" in self.ai_names
            ):
                forced_goodbye = self._enforce_moderator_brevity(
                    self._ensure_moderator_explicit_goodbye("同学们，今天讨论就到这里。"),
                    is_final_closing=True,
                )
                if forced_goodbye:
                    await self._emit_message("moderator", forced_goodbye, "normal")
                    yield {
                        "event_type": "message",
                        "data": {
                            "source": "moderator",
                            "content": forced_goodbye,
                            "tts_text": forced_goodbye,
                        },
                    }
                    self._discussion_end_requested = True

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
            raw_speaker = event.content if hasattr(event, "content") else str(event)
            if isinstance(raw_speaker, list):
                speaker = next(
                    (str(item).strip() for item in raw_speaker if str(item).strip()),
                    "",
                )
            else:
                speaker = str(raw_speaker).strip()
            if (
                speaker != "moderator"
                and "moderator" in self.ai_names
                and not any(self._speaker_message_count.values())
            ):
                logger.warning(
                    "[FloorManager] 首轮选择异常：%s，强制改回 moderator 开场",
                    speaker,
                )
                speaker = "moderator"
            is_human = speaker in self.human_names

            logger.info("[FloorManager] 选择发言者: %s (is_human=%s)", speaker, is_human)
            if speaker:
                self.current_speaker = speaker

            if is_human:
                await self._set_state(FloorState.HUMAN_TURN_WAITING, reason="speaker_selected_human")
            else:
                await self._set_state(FloorState.AI_SPEAKING, reason="speaker_selected_ai")
            self._last_human_input_requested_speaker = ""
            if speaker == "moderator":
                self._moderator_stream_sentence_emitted = 0

            await self._emit_turn_change(speaker, is_human)
            return {
                "event_type": "turn_change",
                "data": {"speaker": speaker, "is_human": is_human},
            }

        # 流式文本块
        if isinstance(event, ModelClientStreamingChunkEvent):
            source = event.source if hasattr(event, "source") else self.current_speaker
            content = event.content if hasattr(event, "content") else str(event)

            if self._should_suppress_ai_while_human_waiting(source):
                self._dropped_ai_stream_while_human_waiting += 1
                logger.warning(
                    "[FloorManager] 真人等待中丢弃错插 AI 流: source=%s content=%s",
                    source,
                    str(content)[:80],
                )
                return None
            if self._is_repeated_non_moderator_turn(source):
                logger.warning(
                    "[FloorManager] 丢弃连续非主持人流，避免角色自说自答: source=%s content=%s",
                    source,
                    str(content)[:80],
                )
                return None

            payload = {"source": source, "content": content}
            if source in self.ai_names:
                self._current_streaming_source = source
                raw_segments = self._consume_streaming_sentences(source, content)
                consumed_raw_segments: list[str] = []
                tts_segments: list[str] = []
                for raw_segment in raw_segments:
                    cleaned_raw_segment = self._strip_meta_reasoning_text(raw_segment)
                    if not cleaned_raw_segment:
                        logger.info(
                            "[FloorManager] 丢弃推理流片段: source=%s segment=%s",
                            source,
                            raw_segment[:80],
                        )
                        continue
                    segment = self._sanitize_all_references(source, cleaned_raw_segment)
                    segment = self._force_first_human_stream_segment(source, segment)
                    if not segment:
                        consumed_raw_segments.append(raw_segment)
                        continue
                    if source == "moderator":
                        segment = self._enforce_moderator_brevity(segment, is_final_closing=False)
                        if not segment:
                            consumed_raw_segments.append(raw_segment)
                            continue
                        if self._moderator_stream_sentence_emitted >= self._MODERATOR_INVITE_SENTENCE_LIMIT:
                            consumed_raw_segments.append(raw_segment)
                            continue
                    segment = await self.safety_filter.filter_or_rewrite(segment)
                    segment = self._strip_meta_reasoning_text(segment).strip()
                    if segment:
                        consumed_raw_segments.append(raw_segment)
                        if self._is_duplicate_streaming_segment(source, segment):
                            logger.info(
                                "[FloorManager] 丢弃重复流片段: source=%s segment=%s",
                                source,
                                segment[:80],
                            )
                            continue
                        if source == "moderator":
                            self._moderator_stream_sentence_emitted += 1
                        tts_segments.append(segment)
                    else:
                        consumed_raw_segments.append(raw_segment)
                self._mark_streaming_segments_emitted(source, consumed_raw_segments)
                if tts_segments:
                    payload["tts_segments"] = tts_segments
                else:
                    return None

            return {
                "event_type": "stream",
                "data": payload,
            }

        # 完整文本消息
        if isinstance(event, TextMessage):
            source = event.source
            raw_content = event.content
            content = raw_content

            # 过滤 AutoGen 内部任务注入消息（source="user" 是 AutoGen 框架内部产生的）
            if source == "user":
                return None

            if self._should_suppress_ai_while_human_waiting(source):
                self._dropped_ai_message_while_human_waiting += 1
                logger.warning(
                    "[FloorManager] 真人等待中丢弃错插 AI 消息: source=%s content=%s",
                    source,
                    str(raw_content)[:120],
                )
                return None
            if self._is_repeated_non_moderator_turn(source):
                logger.warning(
                    "[FloorManager] 丢弃连续非主持人消息，避免角色自说自答: source=%s content=%s",
                    source,
                    str(raw_content)[:120],
                )
                self._set_designated_speaker("moderator")
                self._pop_streaming_message_tail(source, raw_content)
                return None

            logger.info("[FloorManager] 完整消息: source=%s, content_len=%d", source, len(content))

            content = self._strip_meta_reasoning_text(content)
            if source in self.ai_names and not content:
                logger.info("[FloorManager] 丢弃纯提示词完整消息: source=%s", source)
                return None

            # 首轮发言兜底规整：避免不当引用
            content = self._sanitize_all_references(source, content)
            content = self._strip_meta_reasoning_text(content)
            if source in self.ai_names and not content:
                logger.info("[FloorManager] 丢弃清洗后为空的完整消息: source=%s", source)
                if source == "moderator":
                    self._moderator_roleplay_target = None
                return None

            if source == "moderator":
                content = self._ensure_moderator_explicit_goodbye(content)
            is_final_closing = source == "moderator" and self._is_moderator_final_closing(content)

            # 点名解析应尽量基于原始语义，先于安全改写尝试。
            designated_pre_filter: Optional[str] = None
            display_source = self._agent_to_display_name.get(source, source)
            all_display_names = list(self._display_name_to_agent.keys())
            if all_display_names and (source in self.ai_names or source in self.human_names):
                designated_pre_filter = parse_speaker_designation(content, all_display_names)

            if not is_final_closing:
                content, designated_pre_filter, _ = self._force_first_human_invitation(
                    source,
                    content,
                    designated_pre_filter,
                )

            had_streamed_tts, remaining_tts_raw = self._pop_streaming_message_tail(source, raw_content)

            # 安全过滤 AI 输出
            if source in self.ai_names:
                content = await self.safety_filter.filter_or_rewrite(content)
                content = self._strip_meta_reasoning_text(content)
                if not content:
                    logger.info("[FloorManager] 安全过滤后消息为空，跳过发送: source=%s", source)
                    return None

            if source == "moderator":
                content = self._enforce_moderator_brevity(
                    content,
                    is_final_closing=is_final_closing,
                )
                if not content:
                    logger.info("[FloorManager] 主持人消息被长度约束后为空，跳过发送")
                    return None

            # 如果是老师或用户的发言，检查是否指定了下一位发言者（需求4）
            designated: Optional[str] = designated_pre_filter
            immediate_human_request: Optional[str] = None
            if not is_final_closing and all_display_names and (source in self.ai_names or source in self.human_names):
                designated = designated or parse_speaker_designation(content, all_display_names)
                if designated:
                    agent_name = self._display_name_to_agent.get(designated, designated)
                    logger.info("[FloorManager] %s 指定下一位发言者: %s (agent: %s)", display_source, designated, agent_name)
                    self._set_designated_speaker(agent_name)
                    if source == "moderator" and agent_name in self.human_names:
                        immediate_human_request = agent_name
            elif is_final_closing:
                self._set_designated_speaker(None)
                self._discussion_end_requested = True

            tts_text = ""
            if source in self.ai_names:
                if had_streamed_tts:
                    remaining_tts_raw = self._strip_meta_reasoning_text(remaining_tts_raw).strip()
                    if remaining_tts_raw:
                        tail = self._sanitize_all_references(source, remaining_tts_raw)
                        tts_text = await self.safety_filter.filter_or_rewrite(tail)
                        tts_text = self._strip_meta_reasoning_text(tts_text)
                else:
                    tts_text = content

            await self._emit_message(source, content, "text")
            if not is_non_substantive_turn(content):
                self._speaker_message_count[source] = self._speaker_message_count.get(source, 0) + 1
                self._recent_display_speakers.append(display_source)
                if len(self._recent_display_speakers) > 16:
                    self._recent_display_speakers = self._recent_display_speakers[-16:]
            await self._record_turn_summary(source, content)
            if (
                source == "moderator"
                and self._pending_human_guidance
                and self.human_guidance_memory is not None
            ):
                await self.human_guidance_memory.clear()
                self._pending_human_guidance = False

            if immediate_human_request:
                if source == "moderator":
                    self._moderator_roleplay_target = None
                    self._first_human_handoff_streamed = False
                human_request = await self._make_human_input_requested_event(
                    immediate_human_request,
                    reason="moderator_designated_human",
                    clear_designation=False,
                )
                if human_request is not None:
                    return human_request

            if source == "moderator":
                self._moderator_roleplay_target = None
                self._first_human_handoff_streamed = False
                self._moderator_stream_sentence_emitted = 0
            return {
                "event_type": "message",
                "data": {"source": source, "content": content, "tts_text": tts_text},
            }

        # 人类输入请求
        if isinstance(event, UserInputRequestedEvent):
            # 等待人类输入，带超时
            speaker = self.current_speaker or ""
            if not speaker and len(self.human_agents) == 1:
                speaker = self.human_agents[0].name
            return await self._make_human_input_requested_event(
                speaker,
                reason="human_input_requested_waiting",
                clear_designation=True,
            )

        # 任务完成结果
        if isinstance(event, TaskResult):
            logger.info(f"讨论任务完成: stop_reason={event.stop_reason}")
            return None  # 任务完成后 run() 的 finally 会发送 ended

        # 忽略其他事件类型
        logger.debug(f"忽略未知事件类型: {type(event).__name__}")
        return None

    def _is_moderator_final_closing(self, content: str) -> bool:
        text = re.sub(r"\s+", "", content or "")
        if not text:
            return False
        return "再见" in text

    def _looks_like_reported_designation_mistake(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        patterns = (
            r"(?:老师|你).{0,24}(?:叫|让|说|点名|安排|请).{0,40}(?:搞错|弄错|搞混|错了|错啦)",
            r"(?:老师|你).{0,20}(?:叫我|让我|点名让我|安排我).{0,24}(?:又|却).{0,12}(?:说|请|让|安排)",
            r"(?:刚才|之前|前面).{0,16}(?:叫|让|说|点名|安排|请).{0,30}(?:搞错|弄错|搞混|错了|错啦)",
        )
        return any(re.search(pattern, compact) for pattern in patterns)

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
        self._pending_human_input_reason = "normal"

        logger.info("[FloorManager] 收到人类输入: name=%s, text_len=%d", normalized_name, len(normalized_text))

        # 空输入直接按跳过处理，保证流程继续。
        if not normalized_text:
            logger.info("[FloorManager] 空输入，自动跳过: %s", normalized_name)
            if self.human_guidance_memory is not None:
                await self.human_guidance_memory.clear()
            self._pending_human_guidance = False
            await self._put_human_input(normalized_name, "（跳过）")
            await self._emit_message(
                "系统",
                f"{normalized_name or '该同学'}未输入有效内容，已自动跳过本轮。",
                "system",
            )
            return

        # 跳过指令不需要安全过滤
        if normalized_text in ("（跳过）", "(跳过)", "跳过"):
            logger.info("[FloorManager] 用户主动跳过: %s", normalized_name)
            if self.human_guidance_memory is not None:
                await self.human_guidance_memory.clear()
            self._pending_human_guidance = False
            await self._put_human_input(normalized_name, "（跳过）")
            return

        # 安全过滤人类输入
        is_safe, reason = await self.safety_filter.check_human_input(normalized_text)
        if not is_safe:
            logger.warning(
                "[FloorManager] 人类输入被安全过滤: %s, reason=%s",
                normalized_name,
                reason,
            )
            if self.human_guidance_memory is not None:
                await self.human_guidance_memory.clear()
            self._pending_human_guidance = False
            await self._emit_message(
                "系统",
                "你的发言包含不适当的内容，请换一种方式表达。",
                "system",
            )
            return

        guidance = self._build_human_guidance(normalized_name, normalized_text)
        if self.human_guidance_memory is not None:
            if guidance is not None:
                logger.info(
                    "[FloorManager] 生成真人学生回应指引: speaker=%s assessment=%s",
                    normalized_name,
                    guidance.get("assessment", ""),
                )
                await self.human_guidance_memory.replace_guidance([guidance])
                self._pending_human_guidance = True
            else:
                await self.human_guidance_memory.clear()
                self._pending_human_guidance = False

        # 进入真实提交前先清空陈旧点名，避免上一轮残留目标在本轮提交后再次触发。
        self._set_designated_speaker(None)

        # 检查用户是否指定了下一位发言者（需求4）
        all_participant_names = list(self.all_names)
        # 使用 display name map if available
        participant_labels = (
            list(self._display_name_to_agent.keys())
            if hasattr(self, '_display_name_to_agent')
            else all_participant_names
        )
        designated = parse_speaker_designation(normalized_text, participant_labels)
        if designated:
            if self._looks_like_reported_designation_mistake(normalized_text):
                logger.info(
                    "[FloorManager] 用户 %s 正在反馈点名错误，忽略文本中的历史点名: %s",
                    normalized_name,
                    designated,
                )
                designated = None
        if designated:
            logger.info("[FloorManager] 用户 %s 指定下一位发言者: %s", normalized_name, designated)
            # 转换为 agent name
            agent_name = (
                self._display_name_to_agent.get(designated, designated)
                if hasattr(self, '_display_name_to_agent')
                else designated
            )
            current_agent_name = self._display_name_to_agent.get(normalized_name, normalized_name)
            if agent_name == current_agent_name:
                logger.info(
                    "[FloorManager] 忽略用户 %s 的自指点名: %s",
                    normalized_name,
                    designated,
                )
            else:
                self._set_designated_speaker(agent_name)

        try:
            await self._put_human_input(normalized_name, normalized_text)
            logger.info("[FloorManager] 人类输入已提交到队列: %s", normalized_name)
        except Exception as e:
            logger.warning(
                "[FloorManager] 提交人类输入失败，自动跳过。name=%s, err=%s",
                normalized_name,
                e,
            )
            await self._put_human_input(normalized_name, "（跳过）")
            await self._emit_message(
                "系统",
                f"{normalized_name or '该同学'}输入处理异常，系统已自动跳过并继续讨论。",
                "system",
            )

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
        speaker_agent_name = self._display_name_to_agent.get(speaker, speaker)
        display_speaker = self._agent_to_display_name.get(speaker_agent_name, speaker)
        human_display_names = {
            self._agent_to_display_name.get(name, name)
            for name in self.human_names
        }
        is_human = speaker_agent_name in self.human_names or speaker in human_display_names
        if not is_human:
            logger.info("[FloorManager] 非人类参与者 %s 尝试举手打断，忽略", speaker)
            return

        if (
            self.current_speaker == speaker_agent_name
            and self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
        ):
            logger.info("[FloorManager] %s 已经获得发言权，忽略重复举手", display_speaker)
            return

        logger.info(f"打断请求: {display_speaker} 请求发言 (当前发言者: {self.current_speaker})")
        self._interrupt_queue.append(display_speaker)
        self._pending_human_input_reason = "interrupt"
        if self._human_hand_raise_notifier is not None:
            self._human_hand_raise_notifier(speaker_agent_name)

        await self._set_state(FloorState.INTERRUPTED, reason="interrupt_requested")

        moderator_display = self._agent_to_display_name.get("moderator", "李老师")
        # 让举手者成为下一位优先发言，避免被其他角色插队。
        self._set_designated_speaker(speaker_agent_name)
        # 由老师口吻发布同意插话通知。
        await self._emit_message(
            moderator_display,
            f"{display_speaker} 同学，我同意你先发言，其他同学稍后继续。",
            "interrupt",
        )

        # 通知打断事件
        if self._on_interrupt:
            await self._on_interrupt(display_speaker, self.current_speaker or "", moderator_display)

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
