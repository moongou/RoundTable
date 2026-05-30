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
import inspect
import json
import logging
import math
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

from app.agents.human_proxy import get_human_queue, put_human_input
from app.core.discussion_rules import (
    CitationClaim,
    SpeakerBalanceSnapshot,
    speaker_balance_warnings,
    validate_citation_claim,
)
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
from app.core.turn_scheduler import (
    create_discussion_team,
    set_designated_speaker,
    parse_speaker_designation,
)
from app.core.turn_scheduler import is_generic_nomination

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


class SpeakerUtteranceStatus(str, Enum):
    """参与者最近一次轮次的发言状态。"""

    NOMINATED_ONLY = "nominated_only"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    SPOKE_WITH_CONTENT = "spoke_with_content"
    SPOKE_EMPTY = "spoke_empty"


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
    _STREAM_CLOSING_CHARS = frozenset("\"'”’）)]】》」』")
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
    _MODERATOR_REGULAR_SENTENCE_LIMIT = 1
    _MODERATOR_INVITE_SENTENCE_LIMIT = 1
    _MODERATOR_CLOSING_SENTENCE_LIMIT = 2
    _MODERATOR_OPENING_SENTENCE_LIMIT = 2
    _FIRST_HUMAN_MIN_WARMUP_TURNS = 1
    _FIRST_HUMAN_MAX_WAIT_SEC = 120.0
    _HUMAN_TURN_MIN_TARGET = 5
    _NON_HUMAN_AI_MAX_SENTENCES = 3
    # ~200 中文字 ≈ 35-40 秒 TTS（中文播报约 5-6 字/秒），符合规则五的 40 秒上限。
    _NON_HUMAN_AI_MAX_CHARS = 200
    # 规则 14：老师发言总占比软目标 30-40%，超出 0.45 后告警。
    _MODERATOR_TURN_SHARE_TARGET_MIN = 0.30
    _MODERATOR_TURN_SHARE_TARGET_MAX = 0.40
    _MODERATOR_TURN_SHARE_WARN_OVER = 0.45
    # 规则 11：老师点名占比软目标约 80%。
    _MODERATOR_NOMINATION_SHARE_TARGET = 0.50
    _MODERATOR_NOMINATION_SHARE_WARN_BELOW = 0.0
    # 规则 7：真人发言后保留少量老师兜底，其余交给同学/思想家推进。
    _POST_HUMAN_MODERATOR_FEEDBACK_TARGET = 0.0
    _MODERATOR_END_MARKERS = (
        "讨论结束",
        "就到这里",
        "聊到这里",
        "下次见",
        "结束啦",
    )
    _AUTHORIZED_HUMAN_REQUEST_REASONS = frozenset(
        {
            "moderator_designated_human",
            "participant_designated_human",
            "interrupt",
        }
    )
    _ALLOWED_STATE_TRANSITIONS: dict[FloorState, frozenset[FloorState]] = {
        FloorState.INIT: frozenset(
            {
                FloorState.MODERATOR_OPENING,
                FloorState.SELECTING_SPEAKER,
                FloorState.HUMAN_TURN_WAITING,
                FloorState.INTERRUPTED,
                FloorState.ENDED,
            }
        ),
        FloorState.MODERATOR_OPENING: frozenset(
            {
                FloorState.SELECTING_SPEAKER,
                FloorState.AI_SPEAKING,
                FloorState.CLOSING,
                FloorState.ENDED,
            }
        ),
        FloorState.SELECTING_SPEAKER: frozenset(
            {
                FloorState.AI_SPEAKING,
                FloorState.HUMAN_TURN_WAITING,
                FloorState.INTERRUPTED,
                FloorState.CLOSING,
                FloorState.ENDED,
            }
        ),
        FloorState.AI_SPEAKING: frozenset(
            {
                FloorState.SELECTING_SPEAKER,
                FloorState.INTERRUPTED,
                FloorState.CLOSING,
                FloorState.ENDED,
            }
        ),
        FloorState.HUMAN_TURN_WAITING: frozenset(
            {
                FloorState.HUMAN_SPEAKING,
                FloorState.SELECTING_SPEAKER,
                FloorState.INTERRUPTED,
                FloorState.CLOSING,
                FloorState.ENDED,
            }
        ),
        FloorState.HUMAN_SPEAKING: frozenset(
            {
                FloorState.HUMAN_TURN_WAITING,
                FloorState.SELECTING_SPEAKER,
                FloorState.INTERRUPTED,
                FloorState.CLOSING,
                FloorState.ENDED,
            }
        ),
        FloorState.INTERRUPTED: frozenset(
            {
                FloorState.SELECTING_SPEAKER,
                FloorState.ENDED,
            }
        ),
        FloorState.CLOSING: frozenset({FloorState.ENDED}),
        FloorState.ENDED: frozenset(),
    }
    _STATE_DWELL_TIMEOUT_DEFAULTS: dict[FloorState, float] = {
        FloorState.MODERATOR_OPENING: 60.0,
        FloorState.SELECTING_SPEAKER: 15.0,
        FloorState.AI_SPEAKING: 45.0,
    }
    _HUMAN_TURN_MIN_TARGET_AFTER_TWO_SKIPS = 4
    _HUMAN_TURN_MIN_TARGET_AFTER_THREE_SKIPS = 3
    _CLOSING_ATTEMPT_LIMIT = 3
    _MIN_SUBSTANTIVE_TURNS_BEFORE_CLOSING = 18
    # Absolute maximum wall-clock duration for any session (seconds).
    # After this, the session is force-ended regardless of state.
    _MAX_SESSION_DURATION_SEC = 1800.0
    # Maximum consecutive stall recoveries without real progress before force-ending.
    _MAX_CONSECUTIVE_STALL_RECOVERIES = 8
    # Minimum interval between send_drop history writes of the same reason (seconds).
    _SEND_DROP_HISTORY_THROTTLE_SEC = 30.0
    # Bound how long we wait for a cancelled stream __anext__ task to acknowledge
    # cancellation. Some model streams can ignore cancellation for a while, and
    # owner-loop recovery must not block indefinitely on that wait.
    _PENDING_WAIT_CANCEL_TIMEOUT_SEC = 2.0
    _TEAM_CONTROL_DRAIN_TIMEOUT_SEC = 0.75
    _TEAM_CONTROL_CANCEL_GRACE_SEC = 0.5
    _TEAM_STREAM_CLOSE_TIMEOUT_SEC = 1.2
    _TEAM_STREAM_CLOSE_CANCEL_GRACE_SEC = 0.5

    def __init__(
        self,
        team: SelectorGroupChat,
        ai_agents: list[AssistantAgent],
        human_agents: list[UserProxyAgent],
        safety_filter: SafetyFilter,
        human_timeout: int = 120,
        team_factory: Optional[Callable[[], SelectorGroupChat]] = None,
        designated_speaker_setter: Optional[Callable[[Optional[str]], None]] = None,
        summary_memory: Optional[RollingSummaryMemory] = None,
        human_guidance_memory: Optional[HumanResponseGuidanceMemory] = None,
        human_hand_raise_notifier: Optional[Callable[[str], None]] = None,
        human_queue_scope: str | None = None,
        thinker_agent_names: Optional[list[str]] = None,
        nominal_max_turns: int = 24,
        is_connected: Optional[Callable[[], bool]] = None,
    ):
        self.team = team
        self._team_factory = team_factory
        self.ai_agents = ai_agents
        self.human_agents = human_agents
        self.safety_filter = safety_filter
        self.human_timeout = human_timeout
        self._nominal_max_turns = max(8, nominal_max_turns)
        self._is_connected = is_connected
        self._stream_restart_requested = False
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
        self._state_entered_mono = time.monotonic()
        self._state_dwell_timeout_sec = dict(self._STATE_DWELL_TIMEOUT_DEFAULTS)
        self._state_timeout_counts: dict[str, int] = {
            state.value: 0 for state in self._STATE_DWELL_TIMEOUT_DEFAULTS
        }
        self.current_speaker: Optional[str] = None
        self.session_id = str(uuid.uuid4())
        self._current_topic = ""
        self._pending_human_guidance = False
        self._discussion_end_requested = False
        self._end_requested_by_human = ""
        self._end_request_source = ""
        self._pending_submitted_human_inputs: dict[str, str] = {}
        self._recent_human_skip_pending = False
        self._human_skip_count = 0
        self._human_timeout_count = 0
        self._consecutive_human_skips = 0
        self._human_hand_raise_count = 0
        self._human_turn_target_override = self._HUMAN_TURN_MIN_TARGET
        self._human_participation_insufficient = False
        self._closing_attempt_count = 0
        self._closing_attempt_limit_reached = False

        # 消息回调：外部注册以接收事件
        self._on_message: Optional[Callable] = None
        self._on_turn_change: Optional[Callable] = None
        self._on_state_change: Optional[Callable] = None
        self._on_error: Optional[Callable] = None
        self._on_interrupt: Optional[Callable] = None
        self._on_human_input_requested: Optional[Callable] = None

        # 打断请求队列
        self._interrupt_queue: list[str] = []

        # 流式文本缓冲
        self._streaming_buffer: dict[str, str] = {}
        self._streaming_emitted_raw_prefix: dict[str, str] = {}
        self._streaming_emitted_segment_keys: dict[str, set[str]] = {}
        self._streaming_tts_emitted: set[str] = set()
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
        self._speaker_utterance_status: dict[str, SpeakerUtteranceStatus] = {}
        self._recent_display_speakers: list[str] = []
        self._recent_turn_summaries: list[tuple[str, str]] = []
        self._recent_reference_quotes: list[tuple[str, str]] = []
        # 接地校验历史：保留每位发言者的完整发言内容（display_name, content），
        # 用于核对“被引片段是否真的出现在该角色历史发言中”。仅在实时讨论
        # （summary_memory 启用）时填充；单测默认为空，故不影响既有用例行为。
        self._grounding_history: list[tuple[str, str]] = []
        self._last_completed_message_key: Optional[tuple[str, str]] = None
        self._last_completed_message_ts: float = 0.0
        self._recent_completed_message_ts: dict[tuple[str, str], float] = {}
        self._recent_human_turn_summaries: dict[str, list[str]] = {
            name: [] for name in self.human_names
        }
        self._moderator_roleplay_target: Optional[str] = None
        self._expected_next_ai_speaker: Optional[str] = None
        self._expected_ai_reassertion_key: Optional[tuple[str, str]] = None
        self._discussion_started_mono: float = 0.0
        self._deferred_human_request_speaker: Optional[str] = None
        self._deferred_human_request_reason = ""
        self._pending_continuation_task: Optional[str] = None
        self._pending_moderator_human_invite_target: Optional[str] = None

        # Stalled watchdog: detect no-progress windows and auto-recover human wait stalls.
        self._watchdog_task: Optional[asyncio.Task] = None
        self._watchdog_stop = asyncio.Event()
        self._last_progress_ts = time.monotonic()
        self._last_watchdog_action_ts = 0.0
        self._stall_check_interval_sec = 2.0
        # Keep backend recovery below common WebSocket/client idle waits so the
        # server emits a recovery event before clients conclude the discussion is stuck.
        self._general_stall_timeout_sec = min(42.0, max(24.0, float(human_timeout) / 2.0))
        self._deferred_human_request_timeout_sec = max(4.0, min(8.0, float(human_timeout) / 2.0))
        self._submitted_human_input_recovery_timeout_sec = 2.0
        # 需求11：用户要求 30 秒未发言自动跳过。与前端倒计时保持一致。
        self._human_stall_timeout_sec = 30.0
        self._min_human_turn_window_sec = 8.0
        self._human_turn_started_mono = 0.0
        self._last_human_input_requested_speaker = ""
        self._human_input_request_seq = 0
        self._last_human_input_request_id = ""
        self._pending_human_input_reason = "normal"
        self._human_turn_idle_notice_sent = False

        # 规则 7/11/13/14：发言/点名分布度量。
        self._substantive_turn_count: int = 0
        self._moderator_substantive_turn_count: int = 0
        self._human_completed_turn_count: int = 0
        self._immediate_post_human_feedback_moderator: int = 0
        self._immediate_post_human_feedback_peer: int = 0
        self._post_human_feedback_pending: bool = False
        self._moderator_nomination_count: int = 0
        self._peer_nomination_count: int = 0

        # Selector stall recovery: track consecutive LLM selector failures
        self._consecutive_selector_stalls = 0
        self._max_consecutive_selector_stalls = 1
        self._consecutive_stall_recoveries = 0

        # Throttle send_drop history writes per reason
        self._send_drop_history_last_ts: dict[str, float] = {}

        # 暂停状态
        self._paused = False
        self._resume_gate = asyncio.Event()
        self._resume_gate.set()
        self._stream_restart_gate = asyncio.Event()
        self._blocked_for_human_input = False
        self._active_stream_next_event_task: Optional[asyncio.Task[Any]] = None
        self._team_control_tasks: set[asyncio.Task[Any]] = set()
        self._cancelled_wait_tasks: set[asyncio.Task[Any]] = set()
        self._repeated_non_moderator_drop_log_keys: set[tuple[str, str, str]] = set()
        self._run_loop_active = False
        self._pending_run_events: list[dict] = []
        self._team_rebuild_requested = False

    def set_paused(self, paused: bool) -> None:
        """设置暂停状态。暂停时 watchdog 停止检查，恢复时重置进度时间戳。"""
        self._paused = paused
        if paused:
            self._resume_gate.clear()
            try:
                self._close_unawaited_team_control(self.team.pause())
            except RuntimeError:
                logger.debug("team pause requested before initialization", exc_info=True)
            except Exception:
                logger.debug("team pause raised", exc_info=True)
            self._touch_progress("paused")
        else:
            if not self._blocked_for_human_input:
                self._resume_gate.set()
            try:
                self._close_unawaited_team_control(self.team.resume())
            except RuntimeError:
                logger.debug("team resume requested before initialization", exc_info=True)
            except Exception:
                logger.debug("team resume raised", exc_info=True)
            self._touch_progress("resumed")
            self._last_watchdog_action_ts = 0.0

    def _close_unawaited_team_control(self, result: Any) -> None:
        if inspect.iscoroutine(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                result.close()
                return

            task = loop.create_task(result)
            self._team_control_tasks.add(task)
            task.add_done_callback(self._consume_team_control_task_result)

    def _consume_team_control_task_result(self, done: asyncio.Task[Any]) -> None:
        self._team_control_tasks.discard(done)
        try:
            done.result()
        except (asyncio.CancelledError, GeneratorExit):
            pass
        except Exception:
            logger.debug("team control task raised", exc_info=True)

    def _consume_cancelled_wait_task_result(self, done: asyncio.Task[Any]) -> None:
        self._cancelled_wait_tasks.discard(done)
        try:
            done.result()
        except (asyncio.CancelledError, StopAsyncIteration, GeneratorExit):
            pass
        except Exception:
            logger.debug("cancelled team wait task raised", exc_info=True)

    def _consume_autogen_background_task_result(self, done: asyncio.Task[Any]) -> None:
        try:
            done.result()
        except (asyncio.CancelledError, StopAsyncIteration, GeneratorExit):
            pass
        except ValueError as exc:
            if "task_done() called too many times" in str(exc):
                logger.debug("suppressed AutoGen runtime shutdown queue accounting noise")
            else:
                logger.debug("AutoGen background task raised ValueError", exc_info=True)
        except Exception:
            logger.debug("AutoGen background task raised during shutdown", exc_info=True)

    async def _cancel_autogen_background_tasks(self, runtime: Any) -> None:
        tasks = [
            task
            for task in list(getattr(runtime, "_background_tasks", set()) or set())
            if isinstance(task, asyncio.Task) and not task.done()
        ]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
            task.add_done_callback(self._consume_autogen_background_task_result)
        _done, pending = await asyncio.wait(
            tasks,
            timeout=self._TEAM_CONTROL_CANCEL_GRACE_SEC,
        )
        for task in pending:
            task.add_done_callback(self._consume_autogen_background_task_result)

    async def _force_stop_autogen_runtime(self, team: Any, *, reason: str) -> None:
        runtime = getattr(team, "_runtime", None)
        if runtime is None:
            return
        await self._cancel_autogen_background_tasks(runtime)
        run_context = getattr(runtime, "_run_context", None)
        if run_context is None:
            return
        stop = getattr(runtime, "stop", None)
        if not callable(stop):
            return
        try:
            await stop()
            logger.info("[FloorManager] force-stopped AutoGen runtime after %s", reason)
        except RuntimeError:
            logger.debug("AutoGen runtime already stopped after %s", reason, exc_info=True)
        except ValueError as exc:
            if "task_done() called too many times" in str(exc):
                logger.debug("suppressed AutoGen runtime stop queue accounting noise")
            else:
                logger.debug("AutoGen runtime stop raised ValueError", exc_info=True)
        except Exception:
            logger.debug("AutoGen runtime stop failed after %s", reason, exc_info=True)

    async def _drain_background_team_tasks(self) -> None:
        tasks = [
            task
            for task in (*self._team_control_tasks, *self._cancelled_wait_tasks)
            if not task.done()
        ]
        if not tasks:
            return
        _done, pending = await asyncio.wait(
            tasks,
            timeout=self._TEAM_CONTROL_DRAIN_TIMEOUT_SEC,
        )
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.wait(
                pending,
                timeout=self._TEAM_CONTROL_CANCEL_GRACE_SEC,
            )

    def diagnostics(self) -> dict[str, Any]:
        spoken_display_names = sorted(self._get_spoken_display_names())
        closing_gate = self._closing_gate_status()
        metrics = self.discussion_metrics()
        return {
            "dropped_ai_stream_while_human_waiting": self._dropped_ai_stream_while_human_waiting,
            "dropped_ai_message_while_human_waiting": self._dropped_ai_message_while_human_waiting,
            "human_turn_count": closing_gate["human_turn_count"],
            "spoken_display_names": spoken_display_names,
            "missing_ai_display_names": closing_gate["missing_ai_display_names"],
            "coverage_ok": closing_gate["ready"],
            "closing_gate": closing_gate,
            "discussion_metrics": metrics,
            "speaker_utterance_statuses": self._speaker_utterance_statuses_by_display(),
            "state_timeout_counts": dict(self._state_timeout_counts),
            "human_skip_stats": {
                "skip_count": self._human_skip_count,
                "timeout_count": self._human_timeout_count,
                "consecutive_skip_count": self._consecutive_human_skips,
                "hand_raise_count": self._human_hand_raise_count,
                "participation_insufficient": self._human_participation_insufficient,
            },
        }

    def discussion_metrics(self) -> dict[str, Any]:
        """规则 7/11/13/14：返回老师占比、点名分布、点评率等度量数据。"""
        total_turns = self._substantive_turn_count
        moderator_turns = self._moderator_substantive_turn_count
        moderator_share = (moderator_turns / total_turns) if total_turns > 0 else 0.0

        total_nominations = self._moderator_nomination_count + self._peer_nomination_count
        moderator_nomination_share = (
            (self._moderator_nomination_count / total_nominations) if total_nominations > 0 else 0.0
        )

        total_post_human = self._human_completed_turn_count
        moderator_feedback_rate = (
            (self._immediate_post_human_feedback_moderator / total_post_human)
            if total_post_human > 0
            else 0.0
        )

        warnings: list[str] = []
        if total_turns >= 6 and moderator_share > self._MODERATOR_TURN_SHARE_WARN_OVER:
            warnings.append(f"moderator_turn_share={moderator_share:.2f}超出建议范围(0.30-0.40)")
        if (
            total_nominations >= 4
            and moderator_nomination_share < self._MODERATOR_NOMINATION_SHARE_WARN_BELOW
        ):
            warnings.append(
                f"moderator_nomination_share={moderator_nomination_share:.2f}偏低(目标≈0.50)"
            )
        if (
            total_post_human >= 2
            and moderator_feedback_rate < self._POST_HUMAN_MODERATOR_FEEDBACK_TARGET
        ):
            warnings.append(
                f"post_human_moderator_feedback_rate={moderator_feedback_rate:.2f}偏低(目标≥0.00)"
            )
        missing_count = sum(
            1
            for name in self.ai_names
            if name != "moderator" and self._speaker_message_count.get(name, 0) <= 0
        )
        if total_turns >= 8 and missing_count > 0:
            warnings.append(f"missing_ai_count={missing_count}(规则13要求每位虚拟角色至少1次发言)")

        balance_snapshot = SpeakerBalanceSnapshot(
            counts={
                self._agent_to_display_name.get(name, name): count
                for name, count in self._speaker_message_count.items()
            },
            moderator_name=self._agent_to_display_name.get("moderator", "moderator"),
            human_names={self._agent_to_display_name.get(name, name) for name in self.human_names},
            virtual_names={
                self._agent_to_display_name.get(name, name)
                for name in self.ai_names
                if name != "moderator"
            },
        )
        warnings.extend(speaker_balance_warnings(balance_snapshot))

        return {
            "total_substantive_turns": total_turns,
            "moderator_turn_share": round(moderator_share, 3),
            "moderator_turn_share_target_range": [
                self._MODERATOR_TURN_SHARE_TARGET_MIN,
                self._MODERATOR_TURN_SHARE_TARGET_MAX,
            ],
            "moderator_nomination_count": self._moderator_nomination_count,
            "peer_nomination_count": self._peer_nomination_count,
            "moderator_nomination_share": round(moderator_nomination_share, 3),
            "human_completed_turn_count": total_post_human,
            "post_human_moderator_feedback_count": self._immediate_post_human_feedback_moderator,
            "post_human_peer_feedback_count": self._immediate_post_human_feedback_peer,
            "post_human_moderator_feedback_rate": round(moderator_feedback_rate, 3),
            "warnings": warnings,
        }

    async def _wait_until_resumed(self) -> None:
        while self._paused or self._blocked_for_human_input:
            await self._resume_gate.wait()

    def _pause_team_for_human_input(self) -> None:
        if self._blocked_for_human_input:
            return
        self._blocked_for_human_input = True
        self._resume_gate.clear()
        try:
            self._close_unawaited_team_control(self.team.pause())
        except RuntimeError:
            logger.debug("team pause requested before initialization", exc_info=True)
        except Exception:
            logger.debug("team pause raised", exc_info=True)

    def _resume_team_after_human_input(self) -> None:
        if not self._blocked_for_human_input:
            return
        self._blocked_for_human_input = False
        try:
            self._close_unawaited_team_control(self.team.resume())
        except RuntimeError:
            logger.debug("team resume requested before initialization", exc_info=True)
        except Exception:
            logger.debug("team resume raised", exc_info=True)
        if not self._paused:
            self._resume_gate.set()

    def _moderator_opening_has_context(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text or "")
        if not normalized:
            return False

        # 至少具备“来源/定义/背景争议/问题界定”中的任一要素，才算开场信息充分。
        context_markers = (
            r"来源|来自|生活里|现实中|课堂上|新闻里",
            r"定义|是指|意思是|所谓|我们要讨论的是|核心问题",
            r"背景|争议|分歧|矛盾|基本情况|该不该|要不要|值不值得|为什么|是什么|会不会|能不能",
        )
        return any(re.search(pattern, normalized) for pattern in context_markers)

    def _build_moderator_opening_baseline(self) -> str:
        focus = (self._topic_focus_label() or "").rstrip("。！？!?")
        topic_lines = [
            line.strip() for line in (self._current_topic or "").splitlines() if line.strip()
        ]
        detail = topic_lines[1] if len(topic_lines) >= 2 else ""
        detail = re.sub(r"^(背景|情境|提示|思考题|讨论题)[:：]\s*", "", detail)
        detail = detail.strip().rstrip("。！？!?")
        if len(detail) > 48:
            detail = detail[:48].rstrip("，,；;、 ") + "…"

        opening_parts = []
        if focus:
            opening_parts.append(f"同学们好，我是李老师，今天我们来聊聊“{focus}”。")
        else:
            opening_parts.append("同学们好，我是李老师，今天我们来聊一个生活里常会碰到的问题。")
        if detail:
            opening_parts.append(f"背景是：{detail}，请大家先说清楚自己的理由。")
        else:
            opening_parts.append(
                "这个问题来自生活里常见的真实讨论，请大家先说清楚自己的理由和分歧。"
            )
        return "".join(opening_parts)

    def _sanitize_opening_reference(self, source: str, content: str) -> str:
        """首轮发言兜底规整：避免开场阶段出现不当引用。"""
        text = (content or "").strip()
        if not text:
            return text

        count = self._speaker_message_count.get(source, 0)
        is_moderator = source == "moderator"
        is_first_turn = count == 0
        is_first_discussion_turn = self._is_discussion_opening_message(source)

        if (
            is_moderator
            and self._moderator_roleplay_target
            and re.fullmatch(r"请[^。！？!?]{1,24}(?:同学|先生)?发言[。！？!?]?", text)
        ):
            return text

        if is_moderator and is_first_discussion_turn:
            display_names = self._get_all_display_names()
            if display_names and parse_speaker_designation(text, display_names):
                return text
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
            text = re.sub(
                r"(?:请|想问问|问问|想听听|听听|有请|邀请|轮到|接下来|交给|先请|先让)[^。！？!?]*(?:发言|怎么看|来说|来谈|说说|谈谈|讲讲|分享|回应|补充)[^。！？!?]*[。！？!?]?",
                "",
                text,
            ).strip()
            text = re.sub(
                r"[^。！？!?]*(?:同学|先生)?[，,:：]\s*你怎么看[^。！？!?]*[。！？!?]?",
                "",
                text,
            ).strip()
            # Clean up residual fragments and rejoin
            text = re.sub(r"\s{2,}", " ", text).strip()
            text = re.sub(r"^[，,:：、；;]+\s*", "", text)
            if not text:
                text = self._build_moderator_opening_baseline()
            return text

        if (
            source != "moderator"
            and (source in self.ai_names or source in self.human_names)
            and is_first_turn
        ):
            # 同学首轮发言去掉"上一位同学"式互引
            text = re.sub(r"(上一位同学|刚才.*同学|某位同学)", "这个问题", text)
            text = re.sub(r"(你说得对|他说得对|她说得对)", "我先说说我的看法", text)
            text = re.sub(r"^(基于|根据).{0,12}(发言|观点)[，,]", "", text)
            return text

        return text

    def _sanitize_moderator_role_confusion(self, content: str) -> str:
        """Prevent the moderator from role-playing as a student addressing the teacher."""
        text = (content or "").strip()
        if not text:
            return text
        text = re.sub(
            r"^（[^）]{0,30}(?:举手|举起|拍手|跳起来|坐直|身体[^）]{0,12}|前倾)[^）]{0,30}）\s*",
            "",
            text,
        )
        text = re.sub(
            r"^\([^)]{0,30}(?:举手|举起|拍手|跳起来|坐直|身体[^)]{0,12}|前倾)[^)]{0,30}\)\s*",
            "",
            text,
        )
        text = re.sub(
            r"^[（(][^）)]{0,30}[）)]\s*((?:李老师|老师)[，,、]?\s*我)",
            r"\1",
            text,
        )
        text = re.sub(
            r"^(?:李老师|老师)[，,、]?\s*我(?:来|想)?(?:说说|说一下|发言|回答)[！!。,.，、]?\s*",
            "",
            text,
        )
        text = re.sub(
            r"^(?:李老师|老师)[，,、]?\s*我(?:还)?(?:有|想问|想补充)",
            "我们再追问",
            text,
        )
        text = re.sub(
            r"^(?:李老师|老师)[，,、]?\s*我觉得",
            "我觉得",
            text,
        )
        return text.strip() or (content or "").strip()

    def _sanitize_human_honorifics(self, content: str) -> str:
        """Keep real human participants addressed as students, never as thinkers."""
        text = content or ""
        for human_agent in self.human_names:
            display = self._agent_to_display_name.get(human_agent, human_agent)
            if not display:
                continue
            escaped = re.escape(display)
            text = re.sub(rf"{escaped}\s*先生", f"{display}同学", text)
            text = re.sub(rf"{escaped}\s*老师", f"{display}同学", text)
        return text

    def _sanitize_non_moderator_role_confusion(self, source: str, content: str) -> str:
        """Prevent students/thinkers from taking the teacher's closing role."""
        text = (content or "").strip()
        if source == "moderator" or not text:
            return text
        closing_or_teacher_role = re.compile(
            r"(?:大家再见|同学们再见|下课啦|下周见|今天(?:的)?讨论(?:就)?到这里|"
            r"老师真的|老师很开心|同学们[，,、]?\s*老师)"
        )
        if not closing_or_teacher_role.search(text):
            return text

        parts = [
            part.strip()
            for part in re.split(r"(?<=[。！？!?])", text)
            if part.strip() and not closing_or_teacher_role.search(part)
        ]
        cleaned = "".join(parts).strip()
        if cleaned and len(re.sub(r"\s+", "", cleaned)) >= 12:
            return cleaned
        return "我也很受启发，今天的讨论让我看到，不能只看一个表面的结果，还要看背后的过程和选择。"

    def _is_discussion_opening_message(self, source: str) -> bool:
        if source != "moderator" or self._speaker_message_count.get("moderator", 0) > 0:
            return False
        moderator_display = self._agent_to_display_name.get("moderator", "moderator")
        if any(name != moderator_display for name in self._recent_display_speakers):
            return False
        return not any(
            count > 0 for name, count in self._speaker_message_count.items() if name != "moderator"
        )

    def _looks_like_moderator_opening_candidate(self, content: str) -> bool:
        text = (content or "").strip()
        if not text:
            return False
        if self._has_explicit_moderator_invitation_phrase(text):
            return False
        opening_markers = (
            "同学们",
            "今天",
            "这个问题",
            "这个话题",
            "我们先",
            "先一起",
            "来自",
            "生活里",
        )
        return any(marker in text for marker in opening_markers)

    def _ensure_moderator_opening_context(self, content: str) -> str:
        text = (content or "").strip()
        sentence_count = len([part for part in re.split(r"[。！？!?]+", text) if part.strip()])
        if sentence_count > self._MODERATOR_OPENING_SENTENCE_LIMIT and not self._has_explicit_moderator_invitation_phrase(text):
            return self._build_moderator_opening_baseline()
        if self._validate_moderator_opening(text) and self._moderator_opening_has_context(text):
            return text
        baseline = self._build_moderator_opening_baseline()
        if not text:
            return baseline
        if self._has_moderator_invitation_intent(text):
            if not self._has_explicit_moderator_invitation_phrase(text):
                display_names = self._get_all_display_names()
                if display_names and parse_speaker_designation(text, display_names):
                    return baseline
                if text.endswith(("。", "！", "？", "!", "?")):
                    return f"{baseline}{text}"
                return f"{baseline}{text}。"
            if text.endswith(("。", "！", "？", "!", "?")):
                return f"{baseline}{text}"
            return f"{baseline}{text}。"
        if len(text) < 36 or not self._moderator_opening_has_context(text):
            return baseline
        return text

    def _get_spoken_display_names(self) -> set[str]:
        """返回整场讨论里实际产生过有效发言的展示名集合。"""
        spoken = set(self._recent_display_speakers)
        for agent_name, count in self._speaker_message_count.items():
            if count > 0:
                spoken.add(self._agent_to_display_name.get(agent_name, agent_name))
        return spoken

    def _set_speaker_utterance_status(
        self,
        speaker: str,
        status: SpeakerUtteranceStatus,
    ) -> None:
        agent_name = self._normalize_agent_name(speaker)
        if not agent_name or agent_name not in self.all_names:
            return
        self._speaker_utterance_status[agent_name] = status

    def _get_speaker_utterance_status(self, speaker: str) -> SpeakerUtteranceStatus | None:
        agent_name = self._normalize_agent_name(speaker)
        if not agent_name:
            return None
        return self._speaker_utterance_status.get(agent_name)

    def _has_substantive_utterance(self, speaker: str) -> bool:
        agent_name = self._normalize_agent_name(speaker)
        if not agent_name:
            return False
        if self._speaker_message_count.get(agent_name, 0) > 0:
            return True
        return (
            self._speaker_utterance_status.get(agent_name)
            == SpeakerUtteranceStatus.SPOKE_WITH_CONTENT
        )

    def _reference_eligible_display_names(self) -> set[str]:
        eligible: set[str] = set()
        candidate_names = set(self.all_names)
        candidate_names.update(self._speaker_message_count.keys())
        candidate_names.update(self._speaker_utterance_status.keys())
        candidate_names.update(
            self._display_name_to_agent.get(display_name, display_name)
            for display_name in self._recent_display_speakers
            if display_name
        )
        for agent_name in candidate_names:
            if self._has_substantive_utterance(agent_name):
                eligible.add(self._agent_to_display_name.get(agent_name, agent_name))
        eligible.update(name for name in self._recent_display_speakers if name)
        return eligible

    def _speaker_utterance_statuses_by_display(self) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for agent_name in sorted(self.all_names):
            status = self._speaker_utterance_status.get(agent_name)
            if status is None:
                continue
            display_name = self._agent_to_display_name.get(agent_name, agent_name)
            statuses[display_name] = status.value
        return statuses

    def _get_all_display_names(self) -> list[str]:
        seen: set[str] = set()
        names: list[str] = []
        for agent_name in [*self.ai_names, *self.human_names]:
            display_name = self._agent_to_display_name.get(agent_name, agent_name)
            if display_name and display_name not in seen:
                seen.add(display_name)
                names.append(display_name)
        # display 映射可能先于 agent 列表更新，额外并入映射键避免简称解析遗漏。
        for display_name in self._display_name_to_agent.keys():
            if display_name and display_name not in seen:
                seen.add(display_name)
                names.append(display_name)
        return names

    def _iter_display_name_aliases(self) -> list[tuple[str, str]]:
        """返回 (规范展示名, 可识别别名) 对，覆盖简称/尾名等自然称呼。"""
        alias_pairs: list[tuple[str, str]] = []
        seen: set[str] = set()

        for canonical in self._get_all_display_names():
            candidates = {canonical}
            compact = canonical.replace(" ", "")
            if compact:
                candidates.add(compact)
            for token in re.split(r"[·•・\-—\s]+", canonical):
                token = token.strip()
                if len(token) >= 2:
                    candidates.add(token)
            if re.fullmatch(r"[\u4e00-\u9fff]{2,}", compact):
                if len(compact) >= 3:
                    candidates.add(compact[-3:])
                if len(compact) >= 2:
                    candidates.add(compact[-2:])

            canonical_agent = self._display_name_to_agent.get(canonical, canonical)
            if canonical in self._thinker_display_names or canonical_agent in self.thinker_names:
                suffix_aliases = set(candidates)
                for alias in suffix_aliases:
                    if alias.endswith(("先生", "老师", "爷爷", "老爷爷", "老先生")):
                        continue
                    for suffix in ("先生", "爷爷", "老爷爷", "老先生"):
                        candidates.add(f"{alias}{suffix}")

            for alias in sorted(candidates, key=len, reverse=True):
                alias = alias.strip()
                if len(alias) < 2:
                    continue
                if alias in seen:
                    continue
                seen.add(alias)
                alias_pairs.append((canonical, alias))

        alias_pairs.sort(key=lambda item: len(item[1]), reverse=True)
        return alias_pairs

    def _display_aliases(self) -> list[str]:
        return [alias for _canonical, alias in self._iter_display_name_aliases()]

    def _build_targeted_continuation_task(self, speaker: str) -> Optional[str]:
        if speaker not in self.ai_names:
            return None
        display_name = self._agent_to_display_name.get(speaker, speaker)
        topic_label = self._topic_focus_label() or "当前话题"
        if speaker == "moderator":
            return (
                f"继续当前关于{topic_label}的讨论。"
                f"下一位必须由{display_name}立刻接上，用一到两句话把讨论重新带回主题，"
                "并明确安排下一位发言者，不要沉默，不要只重复系统提示。"
            )
        return (
            f"继续当前关于{topic_label}的讨论。"
            f"下一位必须由{display_name}直接发言，马上回应刚才的讨论推进，"
            "不要再次点名真人，不要先让主持人重复总结。"
        )

    def _build_moderator_human_invite_task(self, target_agent: str) -> str:
        target_display = self._agent_to_display_name.get(target_agent, target_agent)
        moderator_display = self._agent_to_display_name.get("moderator", "李老师")
        topic_label = self._topic_focus_label() or "当前话题"
        target_vocative = self._format_display_vocative(target_display)
        return (
            f"继续当前关于{topic_label}的讨论。"
            f"下一位必须先由{moderator_display}发言，用一句极短的话明确点名{target_vocative}发言，"
            f"例如“{target_vocative}，请。”。"
            "在主持人真实说出点名邀请前，不要直接请求真人学生开麦。"
        )

    def _route_human_recovery_through_moderator(self, target_agent: str, *, reason: str) -> str:
        if "moderator" not in self.ai_names:
            return ""
        self._set_designated_speaker("moderator")
        self._expected_next_ai_speaker = "moderator"
        self._pending_continuation_task = self._build_moderator_human_invite_task(target_agent)
        self._pending_moderator_human_invite_target = target_agent
        self._deferred_human_request_speaker = None
        self._deferred_human_request_reason = ""
        self._pending_human_input_reason = "normal"
        logger.info(
            "[FloorManager] redirect human recovery through moderator: target=%s reason=%s",
            target_agent,
            reason,
        )
        return "moderator"

    def _normalize_agent_name(self, name: Any) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            return ""
        direct = self._display_name_to_agent.get(normalized)
        if direct:
            return direct
        compact = re.sub(r"\s+", "", normalized)
        for canonical, alias in self._iter_display_name_aliases():
            if alias == normalized or alias == compact or alias in compact:
                return self._display_name_to_agent.get(canonical, canonical)
        return normalized

    def _preferred_human_agent_name(self) -> str:
        for agent in self.human_agents:
            name = getattr(agent, "name", "")
            if name in self.human_names:
                return name
        return sorted(self.human_names)[0] if self.human_names else ""

    def _build_initial_task(self, topic: str) -> str:
        """Build a strong initial task string that enforces topic opening.

        Includes explicit opening requirements to ensure the moderator introduces
        the topic properly before any discussion begins.
        """
        topic_text = (topic or "").strip()
        # Extract topic title and story for context
        topic_title = ""
        topic_story = ""
        for line in topic_text.splitlines():
            line = line.strip()
            if not topic_title and line:
                topic_title = line
            if line and len(line) > 30:
                topic_story = line
                break

        parts = [topic_text]

        # Add explicit opening instruction
        opening_instruction = (
            "\n\n⚠️ 【开场指令 - 最高优先级，必须遵守】\n"
            "作为主持人李老师，你的第一轮开场发言必须包含以下三个部分：\n"
            "1. 自我介绍和欢迎（一句话即可）\n"
            "2. 介绍本场讨论话题的来源和背景（这个话题从哪来的、为什么值得讨论）\n"
            "3. 用小学生能理解的语言解释话题中的核心概念（如果有生僻词，用生活例子解释）\n\n"
            "开场示例格式：'同学们好！我是李老师。今天我们来聊聊[话题]。这个话题来自[来源/生活场景]，"
            "核心问题是[问题定义]。简单来说，[概念]就是[生活化解释]。'\n\n"
            "开场必须覆盖话题的核心背景信息。如果话题资料里有故事或数据，必须在开场中引用。"
            "开场结束后再点名第一位同学发言。严禁跳过话题介绍直接点名。"
        )
        parts.append(opening_instruction)
        return "\n".join(parts)

    def _validate_moderator_opening(self, content: str) -> bool:
        """Check if the moderator's opening message contains adequate topic introduction.

        Returns True if the opening is adequate, False if deficient.
        """
        text = (content or "").strip()
        if not text:
            return False

        # Must be more than just greeting + name introduction
        if len(text) < 30:
            return False

        # Check for topic keyword presence
        topic = (self._current_topic or "").strip()
        if topic:
            # Extract key terms from topic (first line usually has the title)
            topic_first_line = topic.splitlines()[0].strip() if topic.splitlines() else topic
            key_terms = re.findall(r"[一-鿿]{2,}", topic_first_line)
            # At least one key term should appear in the opening
            term_match = any(term in text for term in key_terms if len(term) >= 3)
            if not term_match and len(key_terms) > 0:
                logger.warning(
                    "[FloorManager] 主持人开场未提及话题关键词: terms=%s",
                    key_terms[:3],
                )
                return False

        # Check for topic introduction markers
        intro_markers = [
            "讨论",
            "话题",
            "聊聊",
            "谈谈",
            "问题",
            "今天",
            "主题",
            "背景",
            "来自",
            "来源",
        ]
        has_intro_marker = any(marker in text for marker in intro_markers)
        if not has_intro_marker:
            logger.warning("[FloorManager] 主持人开场缺少话题引导标记")
            return False

        return True

    def _build_topic_context_note(self) -> str:
        """Build a system note providing topic context when moderator opening is deficient."""
        topic = (self._current_topic or "").strip()
        if not topic:
            return ""

        # Extract topic title (first non-empty line)
        lines = [l.strip() for l in topic.splitlines() if l.strip()]
        if not lines:
            return ""

        topic_title = lines[0] if len(lines[0]) < 30 else lines[0][:30]

        # Extract story/background (longer lines later in the topic text)
        story_lines = [l for l in lines[1:] if len(l) > 30]
        story_preview = ""
        if story_lines:
            story_preview = story_lines[0][:150]
            if len(story_lines[0]) > 150:
                story_preview += "…"

        parts = [f"📋 本场话题：{topic_title}"]
        if story_preview:
            parts.append(f"📖 背景资料：{story_preview}")

        return "\n".join(parts)

    def _smart_fallback_speaker(self) -> str:
        """Deterministic fallback when LLM selector stalls.

        Returns the best next speaker without calling any LLM.
        Priority: human (if below target) > unspoken > least-recent non-moderator > human > moderator.
        """
        spoken = {name for name, count in self._speaker_message_count.items() if count > 0}
        total_spoken = len(spoken)
        # If nobody has spoken yet, moderator must go first
        if total_spoken == 0:
            return "moderator"

        expected = self._expected_next_ai_speaker
        if expected in self.ai_names and expected != "moderator":
            return expected

        human_agent = self._preferred_human_agent_name()
        human_turn_target = self._current_human_turn_target()
        delay_first_human_handoff = self._should_delay_first_human_handoff()

        # Priority 1: human if below minimum target (5) and not just spoke
        if (
            human_agent
            and self.human_names
            and not self._recent_human_skip_pending
            and not self._human_budget_exception_allowed()
        ):
            if self._human_turn_count() < human_turn_target:
                last_agent = self._last_substantive_agent_speaker()
                if last_agent != human_agent and self._non_human_turns_since_last_human() >= 1:
                    return human_agent

        # Priority 2: unspoken non-moderator participants
        unspoken = [
            n
            for n in self.all_names
            if n != "moderator"
            and n not in spoken
            and not (self._recent_human_skip_pending and n in self.human_names)
            and not (delay_first_human_handoff and n in self.human_names)
        ]
        if unspoken:
            thinkers_unspoken = [n for n in unspoken if n in self.thinker_names]
            if thinkers_unspoken:
                return thinkers_unspoken[0]
            return unspoken[0]

        # Priority 3: least-recent non-human, non-moderator speaker (skip last speaker)
        last_agent = self._last_substantive_agent_speaker()
        for display_name in reversed(self._recent_display_speakers):
            agent_name = self._display_name_to_agent.get(display_name, display_name)
            if (
                agent_name not in self.human_names
                and agent_name != "moderator"
                and agent_name != last_agent
            ):
                return agent_name
        # If only last speaker is available among non-humans, return moderator
        if last_agent and last_agent != "moderator" and last_agent not in self.human_names:
            return "moderator"

        # Priority 4: human (even if above target)
        if human_agent and self.human_names and not self._recent_human_skip_pending:
            last_agent = self._last_substantive_agent_speaker()
            if last_agent != human_agent and not delay_first_human_handoff:
                return human_agent

        # Priority 5: moderator
        return "moderator"

    def _generic_moderator_handoff_fallback(self, text: str) -> Optional[str]:
        """Resolve a deterministic non-human follow-up for generic moderator prompts.

        When the moderator says things like "请其他同学说说" right after a human turn,
        relying purely on downstream selector continuation can leave the team stream
        without a concrete next speaker. In that case, proactively designate the best
        non-human continuation candidate.
        """
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return None
        if self._last_substantive_agent_speaker() not in self.human_names:
            return None
        if not is_generic_nomination(compact):
            return None

        fallback = self._smart_fallback_speaker()
        if not fallback or fallback == "moderator" or fallback in self.human_names:
            return None
        return fallback

    def _human_content_explicitly_invites_moderator(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        return bool(
            re.search(r"(?:请|想请|想问一下|想问一问|想问问|想问|问问|想听听|听听|邀请).{0,16}(?:老师|李老师)", compact)
            or re.search(
                r"(?:老师|李老师)[，,:：]?(?:您|你)(?:怎么看|怎么想|觉得呢|认为呢|能不能|可不可以|是否|能否|说说|讲讲|回答|解释|帮)",
                compact,
            )
        )

    def _designate_peer_followup_after_human_turn(self, content: str) -> None:
        if self._discussion_end_requested or self.state in (FloorState.CLOSING, FloorState.ENDED):
            return
        if self._expected_next_ai_speaker:
            return
        if self._human_content_explicitly_invites_moderator(content):
            return
        fallback = self._smart_fallback_speaker()
        if fallback in self.ai_names and fallback != "moderator":
            self._set_designated_speaker(fallback)
            self._expected_next_ai_speaker = fallback
            self._pending_continuation_task = self._build_targeted_continuation_task(fallback)
            self._deferred_human_request_speaker = None
            self._deferred_human_request_reason = ""
            logger.info("[FloorManager] 真人发言后指定同伴接续: %s", fallback)

    def _select_fallback_and_designate(self) -> Optional[str]:
        """Select smart fallback speaker and set as designated. Returns the chosen speaker."""
        if self._discussion_end_requested or self.state in (FloorState.CLOSING, FloorState.ENDED):
            return None
        speaker = self._smart_fallback_speaker()
        if speaker:
            if speaker in self.human_names:
                routed = self._route_human_recovery_through_moderator(
                    speaker,
                    reason="smart_fallback",
                )
                return routed or None
            self._set_designated_speaker(speaker)
            if speaker in self.ai_names:
                self._expected_next_ai_speaker = speaker
                self._pending_continuation_task = self._build_targeted_continuation_task(speaker)
                self._deferred_human_request_speaker = None
                self._deferred_human_request_reason = ""
            logger.info("[FloorManager] Smart fallback: designated speaker=%s", speaker)
        return speaker

    def _schedule_recovery_after_premature_stream_end(self) -> bool:
        if not self._moderator_has_spoken():
            return False
        if self._discussion_end_requested or not self._should_block_moderator_final_closing():
            return False

        human_turn_target = self._current_human_turn_target()

        fallback = self._smart_fallback_speaker()
        if not fallback or fallback == "moderator":
            logger.info(
                "[FloorManager] stream ended before human budget reached, but no recovery target is available: human_turn_count=%s target=%s",
                self._human_turn_count(),
                human_turn_target,
            )
            return False

        if fallback in self.human_names:
            routed = self._route_human_recovery_through_moderator(
                fallback,
                reason="premature_stream_end_before_human_budget",
            )
            logger.info(
                "[FloorManager] stream ended before human budget reached; ask moderator to invite human: human_turn_count=%s target=%s speaker=%s routed=%s",
                self._human_turn_count(),
                human_turn_target,
                fallback,
                routed,
            )
            return bool(routed)

        if fallback in self.ai_names:
            self._set_designated_speaker(fallback)
            self._expected_next_ai_speaker = fallback
            self._pending_continuation_task = self._build_targeted_continuation_task(fallback)
            self._deferred_human_request_speaker = None
            self._deferred_human_request_reason = ""
            logger.info(
                "[FloorManager] stream ended before human budget reached; recover with AI continuation: human_turn_count=%s target=%s speaker=%s",
                self._human_turn_count(),
                human_turn_target,
                fallback,
            )
            return True

        return False

    def _has_human_spoken(self) -> bool:
        return any(self._speaker_message_count.get(name, 0) > 0 for name in self.human_names)

    def _human_turn_count(self) -> int:
        return sum(self._speaker_message_count.get(name, 0) for name in self.human_names)

    def _current_human_turn_target(self) -> int:
        return max(0, int(self._human_turn_target_override))

    def _human_turn_target_mode(self) -> str:
        if self._human_budget_exception_allowed():
            return "exception_no_handraise_after_three_skips"
        target = self._current_human_turn_target()
        if target <= self._HUMAN_TURN_MIN_TARGET_AFTER_THREE_SKIPS:
            return "fallback_after_three_skips"
        if target <= self._HUMAN_TURN_MIN_TARGET_AFTER_TWO_SKIPS:
            return "fallback_after_two_skips"
        return "base"

    def _human_budget_exception_allowed(self) -> bool:
        return (
            bool(self.human_names)
            and self._human_hand_raise_count <= 0
            and self._human_skip_count >= 3
            and self._human_turn_count() <= 0
        )

    def _moderator_has_spoken(self) -> bool:
        return self._speaker_message_count.get("moderator", 0) > 0

    def _register_human_skip(self, *, reason: str, is_timeout: bool) -> None:
        self._human_skip_count += 1
        self._consecutive_human_skips += 1
        self._recent_human_skip_pending = True
        if is_timeout and not self._human_turn_idle_notice_sent:
            self._human_timeout_count += 1
        if self._consecutive_human_skips >= 3:
            self._human_turn_target_override = min(
                self._human_turn_target_override,
                self._HUMAN_TURN_MIN_TARGET_AFTER_THREE_SKIPS,
            )
            self._human_participation_insufficient = True
        elif self._consecutive_human_skips >= 2:
            self._human_turn_target_override = min(
                self._human_turn_target_override,
                self._HUMAN_TURN_MIN_TARGET_AFTER_TWO_SKIPS,
            )
        if self._human_budget_exception_allowed():
            self._human_participation_insufficient = True
        logger.info(
            "[FloorManager] registered human skip: reason=%s skip_count=%s consecutive=%s target=%s timeout_count=%s",
            reason,
            self._human_skip_count,
            self._consecutive_human_skips,
            self._current_human_turn_target(),
            self._human_timeout_count,
        )

    def _register_human_substantive_turn(self) -> None:
        self._consecutive_human_skips = 0

    async def _handle_state_dwell_timeout(self, *, now: float) -> bool:
        state = self.state
        limit_sec = self._state_dwell_timeout_sec.get(state)
        if limit_sec is None:
            return False
        if (now - self._state_entered_mono) < limit_sec:
            return False
        if now - self._last_watchdog_action_ts < 6.0:
            return False

        self._last_watchdog_action_ts = now
        self._state_timeout_counts[state.value] = self._state_timeout_counts.get(state.value, 0) + 1
        timeout_reason = f"state_dwell_timeout:{state.value}"

        if state in (FloorState.MODERATOR_OPENING, FloorState.AI_SPEAKING):
            await self._enter_selecting_speaker(reason=timeout_reason, recovery=True)

        fallback = self._select_fallback_and_designate()
        if state == FloorState.SELECTING_SPEAKER:
            message = (
                f"安排下一位发言超过 {int(limit_sec)} 秒，系统已改为请{self._agent_to_display_name.get(fallback, fallback)}继续。"
                if fallback
                else f"安排下一位发言超过 {int(limit_sec)} 秒，系统正在重试调度。"
            )
        else:
            message = (
                f"{state.value} 持续超过 {int(limit_sec)} 秒，系统已切回选人并安排{self._agent_to_display_name.get(fallback, fallback)}继续。"
                if fallback
                else f"{state.value} 持续超过 {int(limit_sec)} 秒，系统正在重试调度。"
            )
        await self._emit_message("系统", message, "system")
        if state == FloorState.SELECTING_SPEAKER and fallback in self.human_names:
            human_request = await self._make_human_input_requested_event(
                fallback,
                reason="moderator_designated_human",
                clear_designation=False,
            )
            if human_request is not None and self._on_human_input_requested:
                await self._on_human_input_requested(human_request["data"])
        self._request_stream_restart(timeout_reason)
        self._touch_progress(timeout_reason)
        return True

    def _missing_required_ai_speakers(self) -> list[str]:
        return [
            name
            for name in self.ai_names
            if name != "moderator" and self._speaker_message_count.get(name, 0) <= 0
        ]

    def _closing_gate_status(self) -> dict[str, Any]:
        human_turn_count = self._human_turn_count()
        human_turn_target = self._current_human_turn_target()
        human_budget_exception_allowed = self._human_budget_exception_allowed()
        remaining_human_turns = (
            max(0, human_turn_target - human_turn_count) if self.human_names else 0
        )
        missing_ai_agents = self._missing_required_ai_speakers()
        missing_ai_display_names = sorted(
            self._agent_to_display_name.get(name, name) for name in missing_ai_agents
        )
        min_substantive_turns = (
            self._MIN_SUBSTANTIVE_TURNS_BEFORE_CLOSING if len(self.all_names) >= 6 else 0
        )
        remaining_substantive_turns = max(
            0,
            min_substantive_turns - self._substantive_turn_count,
        )

        blockers: list[str] = []
        if remaining_human_turns > 0 and not human_budget_exception_allowed:
            blockers.append(f"remaining_human_turns={remaining_human_turns}")
        if missing_ai_display_names:
            blockers.append("missing_ai_display_names=" + ",".join(missing_ai_display_names))
        if remaining_substantive_turns > 0:
            blockers.append(f"remaining_substantive_turns={remaining_substantive_turns}")

        forced_ready_due_to_attempt_limit = (
            bool(blockers)
            and not missing_ai_display_names
            and self._closing_attempt_count >= self._CLOSING_ATTEMPT_LIMIT
        )

        return {
            "ready": (not blockers) or forced_ready_due_to_attempt_limit,
            "human_turn_count": human_turn_count,
            "human_turn_target": human_turn_target,
            "human_turn_base_target": self._HUMAN_TURN_MIN_TARGET,
            "human_turn_target_mode": self._human_turn_target_mode(),
            "human_budget_exception_allowed": human_budget_exception_allowed,
            "remaining_human_turns": remaining_human_turns,
            "missing_ai_agents": missing_ai_agents,
            "missing_ai_display_names": missing_ai_display_names,
            "min_substantive_turns": min_substantive_turns,
            "total_substantive_turns": self._substantive_turn_count,
            "remaining_substantive_turns": remaining_substantive_turns,
            "blockers": blockers,
            "closing_attempt_count": self._closing_attempt_count,
            "closing_attempt_limit": self._CLOSING_ATTEMPT_LIMIT,
            "forced_ready_due_to_attempt_limit": forced_ready_due_to_attempt_limit,
            "participation_insufficient": self._human_participation_insufficient,
        }

    def _should_block_moderator_final_closing(self) -> bool:
        gate = self._closing_gate_status()
        return not bool(gate["ready"])

    def _non_human_turn_count_before_first_human(self) -> int:
        return sum(
            count
            for name, count in self._speaker_message_count.items()
            if name not in self.human_names and name != "moderator" and count > 0
        )

    def _non_human_turns_since_last_human(self) -> int:
        turns = 0
        for display_name in reversed(self._recent_display_speakers):
            agent_name = self._display_name_to_agent.get(display_name, display_name)
            if agent_name in self.human_names:
                return turns
            if agent_name != "moderator":
                turns += 1
        return turns

    def _should_force_first_human_invite(self) -> bool:
        if not self.human_names or self._has_human_spoken():
            return False
        target_agent = self._preferred_human_agent_name()
        if target_agent and self._speaker_message_count.get(target_agent, 0) > 0:
            return False
        if self._non_human_turn_count_before_first_human() >= self._FIRST_HUMAN_MIN_WARMUP_TURNS:
            return True
        if self._discussion_started_mono <= 0:
            return False
        return (time.monotonic() - self._discussion_started_mono) >= self._FIRST_HUMAN_MAX_WAIT_SEC

    def _should_delay_first_human_handoff(self) -> bool:
        if not self.human_names or self._has_human_spoken():
            return False
        return self._non_human_turn_count_before_first_human() < self._FIRST_HUMAN_MIN_WARMUP_TURNS

    def _enforce_expected_ai_speaker(self, source: str, *, release_on_match: bool = True) -> bool:
        expected = self._expected_next_ai_speaker
        if not expected or source not in self.ai_names:
            self._expected_ai_reassertion_key = None
            return False
        if source == expected:
            if release_on_match:
                self._expected_next_ai_speaker = None
                self._expected_ai_reassertion_key = None
            return False
        if source == "moderator" and expected != "moderator":
            logger.info(
                "[FloorManager] 点名后丢弃主持人残余流/消息: source=%s expected=%s current=%s",
                source,
                expected,
                self.current_speaker,
            )
            return True
        if source == self.current_speaker and source in self.ai_names:
            logger.info(
                "[FloorManager] 点名后丢弃旧说话人残余流/消息: source=%s expected=%s current=%s",
                source,
                expected,
                self.current_speaker,
            )
            return True
        if self.current_speaker == expected:
            logger.info(
                "[FloorManager] 点名后丢弃陈旧旧流/消息: source=%s expected=%s current=%s",
                source,
                expected,
                self.current_speaker,
            )
            return True
        reassertion_key = (expected, self.current_speaker or "")
        if self._expected_ai_reassertion_key != reassertion_key:
            logger.warning(
                "[FloorManager] 点名后发言者不匹配，拦截 source=%s expected=%s",
                source,
                expected,
            )
            self._expected_ai_reassertion_key = reassertion_key
            self._set_designated_speaker(expected)
            self._request_stream_restart(f"expected_ai_mismatch:{expected}")
            self._touch_progress("expected_ai_mismatch_restart")
        else:
            logger.debug(
                "[FloorManager] 重复点名错位流已丢弃: source=%s expected=%s",
                source,
                expected,
            )
        return True

    def _is_authorized_human_request_reason(self, reason: str) -> bool:
        return (reason or "").strip() in self._AUTHORIZED_HUMAN_REQUEST_REASONS

    def _resolve_human_input_request_reason(self, reason: str) -> str:
        normalized_reason = (reason or "").strip()
        pending_reason = (self._pending_human_input_reason or "").strip()
        if pending_reason == "interrupt":
            return pending_reason
        if self._is_authorized_human_request_reason(normalized_reason):
            return normalized_reason
        if normalized_reason in {
            "speaker_selected_human",
            "human_input_requested_waiting",
        } and self._is_authorized_human_request_reason(pending_reason):
            return pending_reason
        return "normal"

    def _build_direct_named_handoff_text(
        self,
        target_display_name: str,
        *,
        prefer_contextual_human_followup: bool = False,
    ) -> str:
        display_name = (target_display_name or "").strip()
        if not display_name:
            return ""

        agent_name = self._display_name_to_agent.get(display_name, display_name)
        target_vocative = self._format_display_vocative(display_name)

        if agent_name in self.human_names:
            return f"{target_vocative}，你怎么看？"

        if agent_name in self.thinker_names or display_name in self._thinker_display_names:
            return f"{target_vocative}，你怎么看？"

        return f"关于这个话题，{target_vocative}，你有什么想法？"

    def _build_first_human_handoff_text(
        self,
        original_text: str = "",
        target_display_name: str = "",
    ) -> str:
        human_agent = self._preferred_human_agent_name()
        human_display = target_display_name or self._agent_to_display_name.get(
            human_agent,
            human_agent,
        )
        return self._build_direct_named_handoff_text(
            human_display,
            prefer_contextual_human_followup=True,
        )

    def _build_targeted_handoff_text(self, target_display_name: str) -> str:
        return self._build_direct_named_handoff_text(target_display_name)

    def _build_targeted_handoff_with_lead_text(
        self,
        original_text: str,
        target_display_name: str,
    ) -> str:
        return self._build_targeted_handoff_text(target_display_name)

    def _ensure_explicit_moderator_handoff_content(
        self,
        content: str,
        designated_display_name: str,
        designated_agent_name: str,
        *,
        explicit_formal_human_invite: bool,
        forced_human_invite: bool,
        budget_forced_human_invite: bool,
    ) -> str:
        text = (content or "").strip()
        display_name = (designated_display_name or "").strip()
        agent_name = (designated_agent_name or "").strip()
        if not text or not display_name or not agent_name:
            return text

        if agent_name in self.human_names:
            if (
                forced_human_invite
                or budget_forced_human_invite
                or not self._has_explicit_moderator_invitation_phrase(
                    text,
                    designated=display_name,
                )
            ):
                return self._build_first_human_handoff_text(
                    text,
                    target_display_name=display_name,
                )
            return text

        if agent_name in self.ai_names and agent_name != "moderator":
            parsed = parse_speaker_designation(text, self._get_all_display_names())
            if parsed != display_name:
                return self._build_targeted_handoff_with_lead_text(text, display_name)
        return text

    def _choose_thinker_followup_agent(self, text: str) -> str:
        compact = re.sub(r"\s+", "", text or "")
        if not compact or not self.thinker_names:
            return ""
        normalized = normalize_reference_match_text(compact)
        if not normalized:
            return ""

        for thinker in sorted(self.thinker_names):
            display_name = self._agent_to_display_name.get(thinker, thinker)
            display_norm = normalize_reference_match_text(display_name)
            if display_norm and display_norm in normalized:
                return thinker

        if "思想家" not in compact:
            return ""

        last_agent = self._last_substantive_agent_speaker()
        candidates = [thinker for thinker in sorted(self.thinker_names) if thinker != last_agent]
        if not candidates:
            candidates = sorted(self.thinker_names)
        return candidates[0] if candidates else ""

    def _select_human_for_final_praise(self) -> str:
        preferred = self._preferred_human_agent_name()
        if preferred in self.human_names and self._speaker_message_count.get(preferred, 0) > 0:
            return preferred
        for agent in self.human_agents:
            name = getattr(agent, "name", "")
            if name in self.human_names and self._speaker_message_count.get(name, 0) > 0:
                return name
        for name in sorted(self.human_names):
            if self._speaker_message_count.get(name, 0) > 0:
                return name
        return ""

    def _collect_human_final_praise_highlights(self, speaker: str) -> list[str]:
        highlights = self._recent_human_turn_summaries.get(speaker, [])
        if not highlights:
            return []

        filtered: list[str] = []
        seen: set[str] = set()
        weak_markers = (
            "刚刚发言过",
            "我刚刚发言过",
            "我同意",
            "我也同意",
            "我也觉得",
            "老师",
        )
        # 规则 5/8：收尾褒奖只能引用真正的观点，过滤掉对系统/麦克风/流程的元提问、
        # 抱怨或与议题无关的话（例如“你怎么把麦克风突然就给我了”“怎么说两遍”），
        # 避免把这些当成“认真的观察”夸出来。
        meta_markers = (
            "麦克风",
            "话筒",
            "轮到我",
            "突然就给我",
            "突然让我",
            "怎么轮到",
            "说两遍",
            "说了两遍",
            "重复",
            "卡住",
            "卡了",
            "听不清",
            "听不见",
            "没声音",
            "听得到吗",
            "能听到吗",
            "怎么操作",
            "按哪里",
            "这个软件",
            "这个系统",
            "退出",
        )
        for summary in highlights:
            value = (summary or "").strip(" ，,。！？!?；;:：")
            value = re.sub(r"^我接着刚才的讨论[，,]\s*", "", value)
            value = re.sub(r"^我接一下[^，,。！？!?]{1,16}的(?:方向|想法|观点)[，,]\s*", "", value)
            normalized = normalize_reference_match_text(value)
            if not normalized or normalized in seen:
                continue
            if len(normalized) < 6:
                continue
            if any(marker in value for marker in meta_markers):
                continue
            if any(marker in value for marker in weak_markers) and len(normalized) < 14:
                continue
            seen.add(normalized)
            filtered.append(value)
        return filtered[-2:]

    def _shorten_final_praise_highlight(self, text: str, *, max_visible_chars: int = 18) -> str:
        value = (text or "").strip(" ，,。！？!?；;:：")
        if not value:
            return ""
        shortened = self._truncate_quote_fragment(value, max_visible_chars=max_visible_chars)
        if not shortened:
            return ""
        if self._quote_fragment_visible_length(shortened) < self._quote_fragment_visible_length(
            value
        ):
            return f"{shortened}…"
        return shortened

    def _build_grounded_moderator_final_closing(self, original_text: str) -> str:
        human_agent = self._select_human_for_final_praise()
        if not human_agent:
            base = (original_text or "").strip() or "今天的讨论就到这里。再见！"
            return self._ensure_moderator_explicit_goodbye(base)

        human_display = self._agent_to_display_name.get(human_agent, human_agent)
        highlights = [
            shortened
            for shortened in (
                self._shorten_final_praise_highlight(item)
                for item in self._collect_human_final_praise_highlights(human_agent)
            )
            if shortened
        ]
        def praise_highlight_text(value: str) -> str:
            cleaned = re.sub(r"[“”\"「」『』]", "", value or "").strip(" ，,。！？!?；;:：")
            return cleaned or "自己的想法"

        if len(highlights) >= 2:
            first = praise_highlight_text(highlights[0])
            second = praise_highlight_text(highlights[1])
            praise = (
                f"最后还想特别夸夸{human_display}，你先提到{first}，后来又想到{second}，"
                "说明你一直在认真听、认真想，也能把自己的观点往前推进，下次如果把最想说明的一点再讲具体一点，你的表达会更有力量。"
            )
        elif highlights:
            first = praise_highlight_text(highlights[0])
            praise = (
                f"最后还想特别夸夸{human_display}，你提到{first}，这个观察很认真，也让大家把问题想得更深了一点，"
                "下次如果顺着这个点再补一个生活里的例子，你的想法会更清楚。"
            )
        else:
            praise = (
                f"最后还想特别夸夸{human_display}，你今天愿意接住大家的话，把自己的想法认真说出来，这份投入很可贵，"
                "下次也可以试着把最想说明的一点讲得再具体一点。"
            )
        return f"{praise}今天的讨论就到这里，同学们再见！"

    def _looks_like_explicit_human_end_request(self, text: str) -> bool:
        value = (text or "").strip()
        if not value:
            return False
        compact = re.sub(r"\s+", "", value)
        if not compact:
            return False
        if re.search(r"(?:不|别|先别|还别|不能|还不能).{0,4}结束", compact):
            return False
        if re.search(r"(?:怎么|什么时候|能不能|可不可以).{0,6}结束", compact):
            return False
        explicit_patterns = (
            r"(?:我们|咱们|那就|就|先)?结束吧",
            r"(?:先|就)到这里吧",
            r"(?:不聊了|别聊了)",
            r"没有什么要说的了.{0,8}结束吧",
            r"都快\d+分钟了.{0,8}结束吧",
        )
        return any(re.search(pattern, compact) for pattern in explicit_patterns)

    async def _emit_moderator_closing_and_mark_end(
        self,
        *,
        reason: str,
        original_text: str = "今天的讨论就到这里。再见！",
    ) -> None:
        closing = self._enforce_moderator_brevity(
            self._build_grounded_moderator_final_closing(original_text),
            is_final_closing=True,
        )
        if not closing:
            closing = self._ensure_moderator_explicit_goodbye(original_text)
        if not closing:
            return

        if self.state not in (FloorState.CLOSING, FloorState.ENDED):
            allowed = self._ALLOWED_STATE_TRANSITIONS.get(self.state, frozenset())
            if FloorState.CLOSING in allowed:
                await self._set_state(FloorState.CLOSING, reason=reason)

        if not is_non_substantive_turn(closing):
            self._set_speaker_utterance_status(
                "moderator",
                SpeakerUtteranceStatus.SPOKE_WITH_CONTENT,
            )
            self._speaker_message_count["moderator"] = (
                self._speaker_message_count.get("moderator", 0) + 1
            )
            display_source = self._agent_to_display_name.get("moderator", "moderator")
            self._recent_display_speakers.append(display_source)
            if len(self._recent_display_speakers) > 16:
                self._recent_display_speakers = self._recent_display_speakers[-16:]
            self._substantive_turn_count += 1
            self._moderator_substantive_turn_count += 1
            if self._post_human_feedback_pending:
                self._immediate_post_human_feedback_moderator += 1
                self._post_human_feedback_pending = False

        await self._emit_message("moderator", closing, "text", tts_text=closing)
        self._discussion_end_requested = True

    async def request_end_discussion(
        self,
        speaker: str,
        *,
        spoken_text: str = "",
        source: str = "human_request",
    ) -> None:
        speaker_agent = self._normalize_agent_name(speaker)
        display_speaker = self._agent_to_display_name.get(
            speaker_agent,
            speaker_agent or speaker,
        )
        if not speaker_agent or speaker_agent not in self.human_names:
            return
        if self._discussion_end_requested or self.state == FloorState.ENDED:
            return

        if self._paused:
            logger.info(
                "[FloorManager] end discussion requested while paused; auto-resuming to finish closing"
            )
            self.set_paused(False)

        self._end_requested_by_human = speaker_agent
        self._end_request_source = source
        self._pending_human_input_reason = "normal"
        self._set_designated_speaker(None)
        self._expected_next_ai_speaker = None
        self._deferred_human_request_speaker = None
        self._deferred_human_request_reason = ""
        self._pending_continuation_task = None
        self._moderator_roleplay_target = None
        self._last_human_input_requested_speaker = ""
        self._last_human_input_request_id = ""
        self._pending_submitted_human_inputs.pop(speaker_agent, None)
        if self.human_guidance_memory is not None:
            await self.human_guidance_memory.clear()
        self._pending_human_guidance = False

        normalized_spoken_text = (spoken_text or "").strip()
        if normalized_spoken_text:
            await self._process_event(
                TextMessage(source=speaker_agent, content=normalized_spoken_text)
            )
        else:
            await self._emit_message(
                "系统",
                f"{display_speaker or '这位同学'}请求结束本次讨论，李老师正在做最后总结。",
                "system",
            )

        await self._emit_moderator_closing_and_mark_end(
            reason="human_requested_end_discussion",
        )
        self._resume_team_after_human_input()
        self._request_stream_restart("human_requested_end_discussion")

    def _last_substantive_agent_speaker(self) -> str:
        if not self._recent_display_speakers:
            return ""
        last_display = self._recent_display_speakers[-1]
        return self._display_name_to_agent.get(last_display, last_display)

    def _should_allow_participant_human_handoff(self, target_agent: str) -> bool:
        if target_agent not in self.human_names:
            return False
        if self._speaker_message_count.get(target_agent, 0) <= 0:
            return (
                self._non_human_turn_count_before_first_human()
                >= self._FIRST_HUMAN_MIN_WARMUP_TURNS
            )
        if self._non_human_turns_since_last_human() >= 1:
            return True
        return self._last_substantive_agent_speaker() == target_agent

    def _force_first_human_invitation(
        self,
        source: str,
        text: str,
        designated: Optional[str],
    ) -> tuple[str, Optional[str], bool]:
        if source == "moderator" and self._moderator_roleplay_target:
            target_display = self._moderator_roleplay_target
            target_agent = self._display_name_to_agent.get(target_display, target_display)
            if target_agent in self.human_names:
                return (
                    self._build_first_human_handoff_text(
                        text,
                        target_display_name=target_display,
                    ),
                    target_display,
                    True,
                )
        return text, designated, False

    def _should_force_budget_human_invitation(
        self,
        source: str,
        text: str,
        designated: Optional[str],
    ) -> bool:
        if source != "moderator" or designated is not None or self._discussion_end_requested:
            return False
        if self._recent_human_skip_pending:
            return False
        if not self.human_names or self._has_moderator_invitation_intent(text):
            return False
        if self._choose_thinker_followup_agent(text):
            return False
        target_agent = self._preferred_human_agent_name()
        if not target_agent:
            return False
        if self._human_budget_exception_allowed():
            return False

        human_count = self._human_turn_count()
        human_turn_target = self._current_human_turn_target()
        if human_count >= human_turn_target:
            return False

        # First human turn: invite after 2 non-human warm-up turns or at 2 minutes.
        if self._speaker_message_count.get(target_agent, 0) <= 0:
            return self._should_force_first_human_invite()

        # Even distribution: calculate adaptive gap based on remaining budget
        # Remaining human turns needed vs remaining total turns
        total_non_mod = sum(
            1
            for name, count in self._speaker_message_count.items()
            if name != "moderator" and name not in self.human_names and count > 0
        )
        remaining_human = human_turn_target - human_count
        # Estimate remaining turns based on nominal max
        estimated_remaining = max(1, self._nominal_max_turns - total_non_mod - human_count)
        # Desired gap: evenly space remaining human turns across remaining discussion
        desired_gap = max(2, estimated_remaining // max(1, remaining_human + 1))

        current_gap = self._non_human_turns_since_last_human()
        if human_count <= 2:
            return current_gap >= 1
        return current_gap >= desired_gap

    def _should_force_peer_budget_human_invitation(
        self,
        source: str,
        text: str,
        designated: Optional[str],
    ) -> bool:
        if (
            source not in self.ai_names
            or source == "moderator"
            or designated is not None
            or self._discussion_end_requested
        ):
            return False
        if self._recent_human_skip_pending or not self.human_names:
            return False
        if self._has_moderator_invitation_intent(text) or self._choose_thinker_followup_agent(text):
            return False
        target_agent = self._preferred_human_agent_name()
        if not target_agent or self._speaker_message_count.get(target_agent, 0) <= 0:
            return False
        if self._human_budget_exception_allowed():
            return False
        if self._human_turn_count() >= self._current_human_turn_target():
            return False
        return self._non_human_turns_since_last_human() >= 1

    def _append_peer_human_handoff_text(self, text: str, target_display_name: str) -> str:
        value = (text or "").strip()
        vocative = self._format_display_vocative(target_display_name, fallback="同学")
        invite = f"{vocative}，你怎么看？"
        if not value:
            return invite
        if value.endswith(("。", "！", "？", "!", "?")):
            return f"{value}{invite}"
        return f"{value}。{invite}"

    def _force_first_human_stream_segment(self, source: str, segment: str) -> str:
        return segment

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
                rf"请\s*{escaped_last}{honorific_pattern}\s*(?:来说|来谈|谈谈|说说|讲讲|分享|回应|补充|发言)[一下吧吗呢]*[，,:：]?\s*你怎么看\s*{escaped_last}{honorific_pattern}",
                f"请其他同学说说，大家怎么看{target_label}",
            ),
            (
                rf"{escaped_last}{honorific_pattern}[，,:：]\s*你怎么看\s*{escaped_last}{honorific_pattern}",
                f"请其他同学说说，大家怎么看{target_label}",
            ),
            (
                rf"请\s*{escaped_last}{honorific_pattern}\s*(?:来说|来谈|谈谈|说说|讲讲|分享|回应|补充|发言)[一下吧吗呢]*",
                "请其他同学说说",
            ),
        ]
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text)
        return text

    def _has_moderator_invitation_intent(self, text: str) -> bool:
        return bool(
            re.search(
                r"(请|想问问|问问|想听听|听听|有请|邀请|轮到|下一位|接下来|交给|发言|你怎么看|来说说|说说|谈谈|讲讲|分享|回应|补充)",
                text or "",
            )
        )

    def _has_explicit_moderator_invitation_phrase(
        self,
        text: str,
        *,
        designated: str = "",
    ) -> bool:
        def build_target_pattern(value: str) -> str:
            target = (value or "").strip()
            if not target:
                return r"[^。！？!?]{1,24}(?:同学|先生)?"
            aliases = {target}
            stripped = re.sub(r"(?:同学|老师|先生|女士|小朋友)$", "", target).strip()
            if stripped:
                aliases.add(stripped)
                compact = re.sub(r"\s+", "", stripped)
                if compact:
                    aliases.add(compact)
                for splitter in ("·", "・", ".", " "):
                    if splitter not in stripped:
                        continue
                    tail = stripped.split(splitter)[-1].strip()
                    if len(tail) >= 2:
                        aliases.add(tail)
            escaped_aliases = [
                re.escape(alias)
                for alias in sorted((alias for alias in aliases if alias), key=len, reverse=True)
            ]
            return rf"(?:{'|'.join(escaped_aliases)})(?:同学|老师|先生|女士|小朋友)?"

        target_pattern = (
            build_target_pattern(designated) if designated else r"[^。！？!?]{1,24}(?:同学|先生)?"
        )
        value = text or ""
        if re.search(
            rf"(?:把)?(?:话筒|麦克风)?交给\s*{target_pattern}(?:[，,:：。！？!?]|$)",
            value,
        ):
            return True
        return bool(
            re.search(
                rf"(?:请|有请|邀请|轮到|接下来请|交给|先请|先让|我们先听听){target_pattern}[^。！？!?]{{0,8}}(?:发言|来说|来谈|谈谈|说说|讲讲|分享|回应|补充)",
                value,
            )
        )

    def _has_explicit_targeted_moderator_handoff(
        self,
        text: str,
        *,
        designated: str,
    ) -> bool:
        target = (designated or "").strip()
        if not target:
            return False
        value = text or ""
        if self._has_explicit_moderator_invitation_phrase(value, designated=target):
            return True
        aliases = {target}
        stripped = re.sub(r"(?:同学|老师|先生|女士|小朋友)$", "", target).strip()
        if stripped:
            aliases.add(stripped)
            compact = re.sub(r"\s+", "", stripped)
            if compact:
                aliases.add(compact)
            for splitter in ("·", "・", ".", " "):
                if splitter not in stripped:
                    continue
                tail = stripped.split(splitter)[-1].strip()
                if len(tail) >= 2:
                    aliases.add(tail)
        target_pattern = rf"(?:{'|'.join(re.escape(alias) for alias in sorted((alias for alias in aliases if alias), key=len, reverse=True))})(?:同学|老师|先生|女士|小朋友)?"
        return bool(
            re.search(
                rf"(?:想问问|问问|想听听|听听){target_pattern}",
                value,
            )
            or re.search(
                rf"{target_pattern}[，,:：]?\s*(?:您|你)[^。！？!?]{{0,28}}(?:怎么看|觉得|认为|先说|来说|来谈|谈谈|说说|讲讲|聊聊|分享|回应|补充|有没有|会不会|能不能|要不要|想不想|愿不愿意|是否|能否)",
                value,
            )
        )

    def _is_pure_moderator_human_invite(
        self,
        text: str,
        *,
        designated: str = "",
    ) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        target_pattern = (
            rf"{re.escape(designated)}(?:同学|先生)?"
            if designated
            else r"[^。！？!?]{1,24}(?:同学|先生)?"
        )
        return bool(
            re.fullmatch(
                rf"(?:请|有请|邀请|轮到|接下来请|先请|先让){target_pattern}(?:发言|来说|来谈|谈谈|说说|讲讲|分享|回应|补充)[。！？!?]?",
                compact,
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
        elif self._is_discussion_opening_message("moderator"):
            limit = self._MODERATOR_OPENING_SENTENCE_LIMIT
        elif self._has_moderator_invitation_intent(value):
            limit = self._MODERATOR_INVITE_SENTENCE_LIMIT
        else:
            limit = self._MODERATOR_REGULAR_SENTENCE_LIMIT
        return self._limit_text_sentence_count(value, max_sentences=limit)

    def _limit_text_char_count(self, text: str, *, max_chars: int) -> str:
        value = (text or "").strip()
        if not value or len(value) <= max_chars:
            return value

        segments, remainder = self._drain_complete_stream_sentences(value)
        units = [*segments]
        if remainder:
            units.append(remainder)

        kept: list[str] = []
        current_len = 0
        for unit in units:
            next_len = current_len + len(unit)
            if kept and next_len > max_chars:
                break
            if not kept and len(unit) > max_chars:
                trimmed = unit[: max_chars - 1].rstrip("，,；;:：、 ")
                return f"{trimmed}。" if trimmed else ""
            kept.append(unit)
            current_len = next_len
        return "".join(kept).strip()

    def _enforce_non_human_ai_duration(self, source: str, text: str) -> str:
        value = (text or "").strip()
        if not value or source not in self.ai_names or source == "moderator":
            return value
        value = self._limit_text_sentence_count(
            value,
            max_sentences=self._NON_HUMAN_AI_MAX_SENTENCES,
        )
        return self._limit_text_char_count(
            value,
            max_chars=self._NON_HUMAN_AI_MAX_CHARS,
        )

    def _sanitize_unknown_student_vocatives(self, text: str, *, last_display: str) -> str:
        value = text or ""
        known_names = set(self._agent_to_display_name.values())

        def repl(match: re.Match[str]) -> str:
            name = (match.group("name") or "").strip()
            suffix = (match.group("suffix") or "").strip()
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
            fragment = (match.group("fragment") or "").strip()
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

        if self._is_exactly_traceable_quote_fragment(fragment):
            return True

        if len(normalized) <= 10 and self._guess_reference_owner(fragment):
            return True

        return False

    def _is_exactly_traceable_quote_fragment(self, fragment: str) -> bool:
        normalized = normalize_reference_match_text(fragment)
        if len(normalized) < 2:
            return False

        for _speaker, quote in reversed(self._recent_reference_quotes):
            candidate_norm = normalize_reference_match_text(quote)
            if candidate_norm and normalized in candidate_norm:
                return True

        topic_norm = normalize_reference_match_text(self._current_topic)
        return bool(topic_norm and len(normalized) >= 4 and normalized in topic_norm)

    def _sanitize_moderator_quote_whitelist(self, text: str) -> str:
        value = text or ""

        def is_exactly_traceable_fragment(fragment: str) -> bool:
            return self._is_exactly_traceable_quote_fragment(fragment)

        def rewrite_untraceable_praise_quote(match: re.Match[str]) -> str:
            fragment = (match.group("fragment") or "").strip()
            if fragment and self._is_traceable_quote_fragment(fragment):
                return match.group(0)
            subject = (match.group("subject") or "这个点").strip().rstrip("的")
            noun = (match.group("noun") or "点").strip()
            if subject in {"这个", "这"}:
                subject = f"这个{noun}"
            return f"{subject}很值得继续讨论。"

        value = re.sub(
            r"(?P<subject>(?:刚才)?(?:这个|这|[^。！？!?，,；;]{1,8}))(?P<noun>比喻|例子|说法|提法|表达|观点|问题|想法)[^。！？!?“\"「『]{0,18}[—\-–,:：，, ]*[“\"「『](?P<fragment>[^”\"」』]{1,80})[”\"」』][^。！？!?]{0,24}[。！？!?]?",
            rewrite_untraceable_praise_quote,
            value,
        )

        def rewrite_untraceable_quoted_label(match: re.Match[str]) -> str:
            fragment = (match.group("fragment") or "").strip()
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

        def rewrite_trailing_untraceable_quote(match: re.Match[str]) -> str:
            fragment = (match.group("fragment") or "").strip()
            if fragment and is_exactly_traceable_fragment(fragment):
                return match.group(0)
            return "这个点很值得继续讨论。"

        value = re.sub(
            r"[“\"「『](?P<fragment>[^”\"」』]{2,80})[”\"」』]\s*[—\-–,:：，, ]*(?:[^。！？!?，,；;“\"「『]{1,10})?(?:同学|先生)?(?:这个|这)?(?:比喻|说法|提法|观点|想法|例子|问题|表达)[^。！？!?]{0,24}[。！？!?]?",
            rewrite_trailing_untraceable_quote,
            value,
        )

        def rewrite_named_possessive_untraceable_quote(match: re.Match[str]) -> str:
            fragment = (match.group("fragment") or "").strip()
            if fragment and is_exactly_traceable_fragment(fragment):
                return match.group(0)
            noun = match.group("noun") or "点"
            return f"这个{noun}很值得继续讨论。"

        value = re.sub(
            r"[^。！？!?，,；;“\"「『]{1,10}(?:同学|先生)?(?:这个|这)?[“\"「『](?P<fragment>[^”\"」』]{2,80})[”\"」』](?:的)?(?P<noun>比喻|说法|提法|观点|想法|例子|问题|表达)[^。！？!?]{0,24}[。！？!?]?",
            rewrite_named_possessive_untraceable_quote,
            value,
        )
        value = re.sub(
            r"[^。！？!?，,；;“\"「『]{1,10}(?:同学|先生)?(?:这个|这)?(?P<claim>这个(?:点|比喻|说法|提法|观点|想法|例子|问题|表达)很值得继续讨论)",
            r"\g<claim>",
            value,
        )

        def rewrite_teacher_self_quote(match: re.Match[str]) -> str:
            prefix = match.group("prefix") or ""
            fragment = (match.group("fragment") or "").strip()
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

    def _sanitize_moderator_surface_noise(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return ""

        value = re.sub(r"[*_]{1,3}", "", value)

        moderator_display = (self._agent_to_display_name.get("moderator", "") or "").strip()
        labels = {"主持人", "老师"}
        if moderator_display:
            labels.add(moderator_display)
        label_pattern = "|".join(
            re.escape(label)
            for label in sorted((label for label in labels if label), key=len, reverse=True)
        )
        if label_pattern:
            value = re.sub(rf"^(?:{label_pattern})\s*[：:]\s*", "", value)
            value = re.sub(
                rf"(感谢|谢谢)(?:{label_pattern})(?:的)?(分享|发言|提醒|补充)",
                lambda match: f"{match.group(1)}刚才的{match.group(2)}",
                value,
            )

        value = re.sub(r"([这那有真太很])\1{2,}", r"\1", value)
        value = re.sub(r"\s{2,}", " ", value).strip()
        value = re.sub(r"^[，,:：\s]+", "", value)
        return value

    def _quote_fragment_visible_length(self, fragment: str) -> int:
        return len(normalize_reference_match_text(fragment))

    def _truncate_quote_fragment(self, fragment: str, *, max_visible_chars: int) -> str:
        value = (fragment or "").strip()
        if not value:
            return ""
        pieces: list[str] = []
        visible_count = 0
        for char in value:
            normalized = normalize_reference_match_text(char)
            if normalized:
                if visible_count >= max_visible_chars:
                    break
                visible_count += len(normalized)
            pieces.append(char)
        return "".join(pieces).rstrip("，,；;、 ")

    def _enforce_quote_constraints(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return value

        quote_pattern = re.compile(r'[“"「『](?P<fragment>[^”"」』]{1,120})[”"」』]')
        matches = list(quote_pattern.finditer(value))
        if not matches:
            return value

        total_visible_chars = max(1, len(normalize_reference_match_text(value)))
        replacement_chunks: list[tuple[int, int, str]] = []
        kept_quotes: list[dict[str, Any]] = []

        for index, match in enumerate(matches):
            fragment = (match.group("fragment") or "").strip()
            visible_length = self._quote_fragment_visible_length(fragment)
            replacement = f"“{fragment}”"
            plain_replacement = fragment
            kept_visible_length = visible_length
            prefix_context = value[max(0, match.start() - 12) : match.start()]
            suffix_context = value[match.end() : match.end() + 12]
            protected_traceable_quote = bool(
                self._is_traceable_quote_fragment(fragment)
                and re.search(r"(?:刚才说|刚才讲|刚才提到|提到)$", prefix_context)
                and re.match(r"[，,]?\s*这个(?:比喻|说法|提法|表达|总结)", suffix_context)
            )

            if protected_traceable_quote:
                pass
            elif index >= 3:
                replacement = plain_replacement
                kept_visible_length = 0
            elif visible_length > 12:
                shortened = self._truncate_quote_fragment(fragment, max_visible_chars=12)
                if shortened:
                    replacement = f"“{shortened}”"
                    plain_replacement = shortened
                    kept_visible_length = self._quote_fragment_visible_length(shortened)
                else:
                    replacement = "这个点"
                    plain_replacement = "这个点"
                    kept_visible_length = 0

            replacement_chunks.append((match.start(), match.end(), replacement))
            if replacement.startswith("“"):
                kept_quotes.append(
                    {
                        "chunk_index": len(replacement_chunks) - 1,
                        "visible_length": kept_visible_length,
                        "plain_replacement": plain_replacement,
                        "protected": protected_traceable_quote,
                    }
                )

        total_quote_chars = sum(item["visible_length"] for item in kept_quotes)
        max_quote_chars = max(1, math.ceil(total_visible_chars * 0.3))
        while total_quote_chars > max_quote_chars and kept_quotes:
            removable_quotes = [item for item in kept_quotes if not item.get("protected")]
            if not removable_quotes:
                break
            longest = max(removable_quotes, key=lambda item: item["visible_length"])
            chunk_index = longest["chunk_index"]
            start, end, _previous = replacement_chunks[chunk_index]
            replacement_chunks[chunk_index] = (start, end, longest["plain_replacement"])
            total_quote_chars -= longest["visible_length"]
            kept_quotes.remove(longest)

        rebuilt: list[str] = []
        cursor = 0
        for start, end, replacement in replacement_chunks:
            rebuilt.append(value[cursor:start])
            rebuilt.append(replacement)
            cursor = end
        rebuilt.append(value[cursor:])
        normalized = "".join(rebuilt)
        normalized = re.sub(r"这个点\s*这个点", "这个点", normalized)
        normalized = re.sub(r"这个点(这个比喻|这个说法|这个想法|这个观点)", r"\1", normalized)

        def strip_untraceable_praise_quote(match: re.Match[str]) -> str:
            lead = (match.group("lead") or "").strip()
            fragment = (match.group("fragment") or "").strip()
            if self._is_traceable_quote_fragment(fragment):
                return match.group(0)
            lead = re.sub(r"[—\-–,:：，,]+$", "", lead).strip()
            return f"你{lead}。"

        normalized = re.sub(
            r"你(?P<lead>[^“\"「『。！？!?]{0,28}(?:说得|讲得|问得|提得|想得|精彩|到位|太棒|真棒|很棒|真好)[^“\"「『。！？!?]{0,8})[—\-–,:：，, ]*[“\"「『](?P<fragment>[^”\"」』]{1,80})[”\"」』][^。！？!?]{0,36}",
            strip_untraceable_praise_quote,
            normalized,
        )

        canonical_names = sorted(self._get_all_display_names(), key=len, reverse=True)
        if canonical_names:
            canonical_pattern = "|".join(re.escape(name) for name in canonical_names)

            def strip_canonical_named_praise_quote(match: re.Match[str]) -> str:
                lead = (match.group("lead") or "").strip()
                if not re.search(r"(说得|讲得|问得|提得|想得|精彩|到位|太棒|真棒|很棒|真好)", lead):
                    return match.group(0)
                fragment = (match.group("fragment") or "").strip()
                name = match.group("name")
                if self._is_traceable_quote_fragment(fragment):
                    return match.group(0)
                honorific = match.group("honorific") or ""
                lead = re.sub(r"[—\-–,:：，,]+$", "", lead).strip()
                return f"{self._format_reference_name(name, honorific)}，你{lead}。"

            normalized = re.sub(
                rf"(?P<name>{canonical_pattern})(?P<honorific>同学|先生)?[，,:：]?\s*你(?P<lead>[^“\"「『。！？!?]{{0,28}})[“\"「『](?P<fragment>[^”\"」』]{{1,80}})[”\"」』](?:这句话|这个说法|这个比喻)?[^。！？!?]{{0,24}}[。！？!?]?",
                strip_canonical_named_praise_quote,
                normalized,
            )
        alias_pairs = self._iter_display_name_aliases()
        if alias_pairs:
            alias_to_canonical = {alias: canonical for canonical, alias in alias_pairs}
            names_pattern = "|".join(re.escape(alias) for _canonical, alias in alias_pairs)

            def strip_named_praise_quote(match: re.Match[str]) -> str:
                lead = (match.group("lead") or "").strip()
                if not re.search(r"(说得|讲得|问得|提得|想得|精彩|到位|太棒|真棒|很棒|真好)", lead):
                    return match.group(0)
                fragment = (match.group("fragment") or "").strip()
                name_alias = match.group("name")
                name = alias_to_canonical.get(name_alias, name_alias)
                if self._is_traceable_quote_fragment(fragment):
                    return match.group(0)
                honorific = match.group("honorific") or ""
                lead = re.sub(r"[—\-–,:：，,]+$", "", lead).strip()
                return f"{self._format_reference_name(name, honorific)}，你{lead}。"

            normalized = re.sub(
                rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?[，,:：]?\s*你(?P<lead>[^“\"「『。！？!?]{{0,28}})(?:[“\"「『](?P<fragment>[^”\"」』]{{1,80}})[”\"」』])[^。！？!?]{{0,36}}",
                strip_named_praise_quote,
                normalized,
            )
        normalized = re.sub(r"\s{2,}", " ", normalized).strip()
        return normalized

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
        extra_honorific = r"(?:同学|先生|老师|爷爷|奶奶|老爷爷|老奶奶|老先生)?"

        text = re.sub(
            rf"{escaped_name}{extra_honorific}[，,:：]?\s*你(?:说的话|讲的话|刚才说的?|刚才讲的?|前面说的?|前面讲的?)(?:[^。！？!?]{{0,40}})[。！？!?]?",
            "刚才这个问题挺值得继续想。",
            text,
        )

        def rewrite_unspoken_hearing(match: re.Match[str]) -> str:
            verb = match.group("verb")
            if last_display:
                return f"{verb}{self._format_display_vocative(last_display)}刚才的发言"
            return f"{verb}刚才这位同学的发言"

        text = re.sub(
            rf"(?P<verb>听完|听了|听到|看完|看到)\s*{escaped_name}{extra_honorific}(?:的)?(?:话|发言|分享|观点|想法)",
            rewrite_unspoken_hearing,
            text,
        )

        def rewrite_named_possessive_quote(match: re.Match[str]) -> str:
            fragment = match.group("fragment")
            noun = match.group("noun")
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
            (
                rf"{escaped_name}(?:同学|先生)?(?:也)?\s*(问到了?|问到|说到了?|讲到了?|提到了?)([^。！？!?]{{0,16}})",
                r"刚才这个问题\2",
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

        self_rewrites = [
            (
                r"我(?:很|非常)?(?:赞同|认可|同意)自己(?:刚才)?(?:讲|说|提|提出|讲过|说过)?的?(?:话|观点|想法|说法)",
                "我想补充刚才这个观点",
            ),
            (
                r"我(?:很|非常)?(?:赞同|认可|同意)我(?:刚才)?(?:讲|说|提|提出|讲过|说过)?的?(?:话|观点|想法|说法)",
                "我想补充刚才这个观点",
            ),
            (
                r"自己(?:刚才)?(?:讲|说|提出|讲过|说过)的(?:话|观点|想法|说法)",
                "刚才这个观点",
            ),
        ]
        for pattern, replacement in self_rewrites:
            text = re.sub(pattern, replacement, text)

        # 防止角色用第三人称夸自己（如“小说提到……真棒”“小疑的这个想法真好”）。
        if source != "moderator":
            escaped_current_display = re.escape(current_display)
            third_person_self_rewrites = [
                (
                    rf"{escaped_current_display}(?:同学|先生)?的这个",
                    "这个",
                ),
                (
                    rf"{escaped_current_display}(?:同学|先生)?的这(个|种|条|句|段)",
                    r"这\1",
                ),
                (
                    rf"{escaped_current_display}(?:同学|先生)?(?:还)?(?:提到|说到|讲到)(?=[“\"「『])",
                    "",
                ),
                (
                    rf"{escaped_current_display}(?:同学|先生)?(?:还)?(提到|说到|讲到|认为|觉得|指出|提出|分享|强调|质疑|追问)",
                    r"我\1",
                ),
            ]
            for pattern, replacement in third_person_self_rewrites:
                text = re.sub(pattern, replacement, text)

        # 防止"我（XX）觉得……"这种冗余自我介绍
        text = re.sub(rf"我[（(]{re.escape(current_display)}[）)]", "我", text)
        text = re.sub(r"老师(?:同学|先生)", "老师", text)
        text = re.sub(r"([\u4e00-\u9fff]{1,4})刚才\1有同学", "刚才有同学", text)

        last_display = ""
        for name in reversed(self._recent_display_speakers):
            if name != current_display:
                last_display = name
                break

        # 构建"具备可引用内容"的名字集合。只有真正说过实质内容的人才允许被当作引用对象。
        spoke_set = self._reference_eligible_display_names()

        alias_pairs = self._iter_display_name_aliases()
        alias_to_canonical = {alias: canonical for canonical, alias in alias_pairs}
        display_names = [alias for _canonical, alias in alias_pairs]
        for name in display_names:
            canonical_name = alias_to_canonical.get(name, name)
            if canonical_name == current_display:
                continue
            # 修正"刚才/上一位/前面 + 错误名字" → 替换为真正的上一位
            if last_display and canonical_name != last_display:
                text = re.sub(
                    rf"(刚才|上一位|前面)\s*{re.escape(name)}", rf"\1{last_display}", text
                )
            # 检测对从未发言者的引用（"X说/X提到/X认为"） → 替换为中性表述
            if canonical_name not in spoke_set:

                def rewrite_unspoken_direct_quote(match: re.Match[str]) -> str:
                    fragment = (match.group("fragment") or "").strip()
                    owner = self._guess_reference_owner(fragment)
                    if owner and owner != name:
                        return f"{self._format_display_vocative(owner)}，你刚才说“{fragment}”"
                    return ""

                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?[，,:：]?\s*你刚才说[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』][^。！？!?]*[。！？!?]?",
                    rewrite_unspoken_direct_quote,
                    text,
                )
                text = re.sub(
                    rf"{re.escape(name)}(?:同学|先生)?\s*(提了|问了|说了|想到了|建议了|补充了|指出了)",
                    r"刚才有同学\1",
                    text,
                )
                if last_display:
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
                    text = re.sub(
                        rf"{re.escape(name)}(?:同学|先生)?\s*(举|提|问|说|讲)的这个(比喻|例子|问题|想法|观点|说法|疑问)",
                        rf"{self._format_display_vocative(last_display)}，你\1的这个\2",
                        text,
                    )
                    text = re.sub(
                        rf"{re.escape(name)}(?:同学|先生)?\s*问的问题",
                        rf"{self._format_display_vocative(last_display)}，你问的问题",
                        text,
                    )
                    text = re.sub(
                        rf"{re.escape(name)}(?:同学|先生)?[，,:：]?\s*你这句话说得",
                        rf"{self._format_display_vocative(last_display)}，你这句话说得",
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
        if last_display:
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
                canonical_name = alias_to_canonical.get(name, name)
                if canonical_name == last_display:
                    continue
                if canonical_name == current_display and source != "moderator":
                    continue

                def rewrite_compliment_vocative(
                    match: re.Match[str],
                    *,
                    original_name: str = name,
                    original_canonical: str = canonical_name,
                    target_display: str = last_display,
                ) -> str:
                    following = text[match.end() : match.end() + 90]
                    quoted = re.match(
                        r"[“\"「『](?P<fragment>[^”\"」』]{2,80})[”\"」』]", following
                    )
                    if (
                        quoted
                        and self._guess_reference_owner(quoted.group("fragment"))
                        in {original_name, original_canonical}
                    ):
                        return match.group(0)
                    verb = match.group("verb")
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
        if last_display:
            for name in display_names:
                canonical_name = alias_to_canonical.get(name, name)
                if canonical_name == last_display:
                    continue
                if canonical_name == current_display and source != "moderator":
                    continue
                text = re.sub(
                    rf"^{re.escape(name)}(同学|先生)?[，,]?\s*(?:你)?(这|那)?(?:个|段|次|句)?\s*(问题|说法|比喻|想法|观点|例子|疑问|答案|思路|表述)",
                    lambda m, _ld=last_display: (
                        f"{self._format_display_vocative(_ld, fallback=m.group(1) or '同学')}，你这个{m.group(3)}"
                    ),
                    text,
                    flags=re.MULTILINE,
                )

        if source == "moderator" and last_display:
            text = self._sanitize_repeated_self_invitation(text, last_display=last_display)

        if last_display:
            text = self._sanitize_unknown_student_vocatives(text, last_display=last_display)
        text = self._sanitize_ungrounded_neutral_quote_claims(text)
        if source == "moderator":
            text = self._sanitize_moderator_quote_whitelist(text)
            text = self._sanitize_moderator_surface_noise(text)

        text = re.sub(r"\s{2,}", " ", text).strip()
        text = re.sub(r"^[，,:：\s]+", "", text)
        return text

    def _sanitize_moderator_roleplay(self, source: str, content: str) -> str:
        """防止主持人替其他角色直接发言。"""
        text = (content or "").strip()
        if source != "moderator" or not text:
            return text

        display_names = self._display_aliases()
        for display_name in display_names:
            if not display_name or display_name == self._agent_to_display_name.get(
                "moderator", "moderator"
            ):
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
        text = self._sanitize_reference_attribution(source, text)
        if source == "moderator":
            text = self._validate_moderator_references(text)
        if source in self.ai_names:
            text = self._enforce_quote_constraints(text)
        return text

    def _validate_moderator_references(self, content: str) -> str:
        """Validate moderator references against actual speaker history.

        Detects and fixes:
        - Praising/quoting someone who hasn't spoken yet
        - Referencing content that doesn't exist in any speaker's messages
        - Attributing specific quotes or metaphors to the wrong person
        """
        if not content or not self._recent_turn_summaries:
            return content

        text = content
        spoken_displays = self._reference_eligible_display_names()

        # Pattern 1: "X同学，你说得Y" / "X，你讲得Y" — but X hasn't spoken
        praise_patterns = [
            re.compile(
                rf"({re.escape(display)})(?:同学|先生)?[，,：:\s]*(?:你|您)(?:说|讲|问|这个|那段|刚才)(?:得|的|的这段话)\S{{0,40}}(?:真好|太棒|很棒|太好了|很对|很到位|太精彩|真精彩|很深刻|很形象|问得好|问得太好)"
            )
            for display in self._display_name_to_agent.keys()
            if display and display not in spoken_displays
        ]
        for pattern in praise_patterns:
            if pattern.search(text):
                logger.warning(
                    "[FloorManager] 主持人引用了未发言者，替换为通用表达: %s",
                    text[:80],
                )
                text = pattern.sub("刚才有同学提到一个有意思的点", text)
                break

        # Pattern 2: "X用Y比喻/形容" — check if Y exists in X's actual messages
        metaphor_pattern = re.compile(
            r"(?:^|[。！？!?；;])\s*([^\s，,]{2,8})(?:同学|先生)?[用拿][了]?\s*(\S{1,12})\s*(?:比喻|形容|例子|对比|类比|说法)",
        )
        for match in metaphor_pattern.finditer(text):
            ref_name = match.group(1)
            ref_metaphor = match.group(2)
            agent_name = self._display_name_to_agent.get(ref_name, ref_name)
            if (
                agent_name in self.human_names
                and self._speaker_message_count.get(agent_name, 0) <= 0
            ):
                logger.warning(
                    "[FloorManager] 主持人引用未发言真人的比喻，移除: name=%s metaphor=%s",
                    ref_name,
                    ref_metaphor,
                )
                text = text.replace(match.group(0), "")
                continue
            # Check if this metaphor/keyword exists in any recent summary
            found = any(ref_metaphor in summary for speaker, summary in self._recent_turn_summaries)
            if not found:
                logger.warning(
                    "[FloorManager] 主持人引用不存在的内容: name=%s content=%s",
                    ref_name,
                    ref_metaphor,
                )
                text = text.replace(match.group(0), "刚才有同学提到一个有意思的比喻")

        # Pattern 3: "刚才X说Y" / "X提到Y" — check X is the actual last speaker
        last_attribution = re.compile(
            r"(?:刚才|刚刚|前面|上一位)\s*([^\s，,]{2,8})(?:同学|先生)?[说提到讲到指出问道]\S{0,30}",
        )
        match = last_attribution.search(text)
        if match:
            ref_name = match.group(1)
            last_actual_speaker = ""
            if self._recent_display_speakers:
                last_actual_speaker = self._recent_display_speakers[-1]
            if ref_name and last_actual_speaker and ref_name != last_actual_speaker:
                agent_name = self._display_name_to_agent.get(ref_name, ref_name)
                if (
                    agent_name in self.human_names
                    and self._speaker_message_count.get(agent_name, 0) <= 0
                ):
                    logger.warning(
                        "[FloorManager] 张冠李戴：主持人说'%s说Y'但%s未发言，实际发言者是%s",
                        ref_name,
                        ref_name,
                        last_actual_speaker,
                    )
                    text = re.sub(
                        rf"刚才\s*{re.escape(ref_name)}(?:同学|先生)?[说提到讲到指出问道]",
                        f"刚才{last_actual_speaker}同学",
                        text,
                        count=1,
                    )

        return text

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
            (
                score_reference_fragment(text, candidate)
                for candidate in topical_candidates
                if candidate
            ),
            default=0,
        )

        if len(normalized) <= 6 and topical_score < 6 and not peer_mentions:
            return "weak"
        if (
            any(marker in normalized for marker in weak_markers)
            and len(normalized) <= 10
            and not peer_mentions
        ):
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

    def _is_grounded_citation(self, owner: str, fragment: str) -> bool:
        """核对 owner 的历史发言中是否真的出现过 fragment（接地校验）。"""
        if not owner or not fragment or not self._grounding_history:
            return False
        return validate_citation_claim(
            CitationClaim(owner=owner, quote=fragment, history=self._grounding_history)
        )

    def _grounded_citation_owner(self, fragment: str) -> str | None:
        """在完整发言历史中找出真正说过 fragment 的最近发言者；找不到返回 None。"""
        if not fragment or not self._grounding_history:
            return None
        seen: set[str] = set()
        for owner, _content in reversed(self._grounding_history):
            if owner in seen:
                continue
            seen.add(owner)
            if validate_citation_claim(
                CitationClaim(owner=owner, quote=fragment, history=self._grounding_history)
            ):
                return owner
        return None

    def _guess_reference_owner(self, fragment: str) -> str | None:
        # 接地校验优先：若完整发言历史中能确认真实出处，直接采用该归属。
        grounded_owner = self._grounded_citation_owner(fragment)
        if grounded_owner is not None:
            return grounded_owner

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
        # 接地兜底：若已有完整发言历史，却无法确认评分得到的归属真的说过该片段，
        # 说明这很可能是被编造/错配的引用，拒绝归属以避免张冠李戴。
        if self._grounding_history and not self._is_grounded_citation(best_name, fragment):
            return None
        return best_name

    def _format_reference_name(self, name: str, honorific: str) -> str:
        moderator_display = self._agent_to_display_name.get("moderator", "moderator")
        if name == moderator_display:
            return name
        if not honorific or name.endswith(honorific):
            return name
        return f"{name}{honorific}"

    def _sanitize_grounded_quote_attribution(self, text: str) -> str:
        value = (text or "").strip()
        if not value or not self._recent_reference_quotes:
            return value

        alias_pairs = self._iter_display_name_aliases()
        display_names = [alias for _canonical, alias in alias_pairs]
        alias_to_canonical = {alias: canonical for canonical, alias in alias_pairs}
        if not display_names:
            return value
        names_pattern = "|".join(re.escape(name) for name in display_names)
        invalidated_names: set[str] = set()

        def rewrite_named_vocative(match: re.Match[str]) -> str:
            name_alias = match.group("name")
            name = alias_to_canonical.get(name_alias, name_alias)
            honorific = match.group("honorific") or ""
            verb = match.group("verb")
            fragment = match.group("fragment")
            owner = self._guess_reference_owner(fragment)
            if owner == name:
                return match.group(0)
            if owner:
                invalidated_names.add(name_alias)
                return f"{self._format_reference_name(owner, honorific)}，你{verb}“{fragment}”"
            invalidated_names.add(name_alias)
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
            name_alias = match.group("name")
            name = alias_to_canonical.get(name_alias, name_alias)
            honorific = match.group("honorific") or ""
            verb = match.group("verb")
            fragment = match.group("fragment")
            owner = self._guess_reference_owner(fragment)
            def unquoted_reference(owner_name: str | None) -> str:
                reference_name = (
                    self._format_reference_name(owner_name, honorific) if owner_name else "有同学"
                )
                noun = "问题" if re.search(r"问|追问|质疑", verb) else "想法"
                return f"{reference_name}的这个{noun}"

            if owner == name:
                if not self._is_exactly_traceable_quote_fragment(fragment):
                    return unquoted_reference(owner)
                return match.group(0)
            if owner:
                invalidated_names.add(name_alias)
                if not self._is_exactly_traceable_quote_fragment(fragment):
                    return unquoted_reference(owner)
                return f"{self._format_reference_name(owner, honorific)}{verb}“{fragment}”"
            invalidated_names.add(name_alias)
            return unquoted_reference(None)

        value = re.sub(
            rf"(?:刚才|刚刚|前面|之前)?(?P<name>{names_pattern})(?P<honorific>同学|先生)?(?:还)?\s*(?P<verb>说的?|提到的?|讲到的?|提出的?|分享的?|质疑的?|追问的?)[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』]",
            rewrite_named_report,
            value,
        )
        value = re.sub(
            rf"(?:刚才|刚刚|前面|之前)?(?P<name>{names_pattern})(?P<honorific>同学|先生)?(?:还)?\s*(?P<verb>刚才说的?|刚才提到的?|刚才讲到的?|刚才提出的?|刚才分享的?|刚才质疑的?|刚才追问的?)[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』]",
            rewrite_named_report,
            value,
        )

        def rewrite_named_story_report(match: re.Match[str]) -> str:
            name_alias = match.group("name")
            name = alias_to_canonical.get(name_alias, name_alias)
            honorific = match.group("honorific") or ""
            fragment = match.group("fragment")
            noun = match.group("noun") or "例子"
            owner = self._guess_reference_owner(fragment)
            if owner == name and self._is_exactly_traceable_quote_fragment(fragment):
                return match.group(0)
            invalidated_names.add(name_alias)
            reference_name = self._format_reference_name(owner, honorific) if owner else "有同学"
            if owner and self._is_exactly_traceable_quote_fragment(fragment):
                return f"{reference_name}讲的“{fragment}”的{noun}"
            return f"{reference_name}的这个{noun}"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?(?:刚才|刚刚|前面|之前)?(?:讲|讲到|说|说到|提|提到)(?:的)?(?:那个|这个)?[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』](?:的)?(?P<noun>故事|例子|比喻|说法|观点|想法|问题|表达|答案)",
            rewrite_named_story_report,
            value,
        )

        def rewrite_named_claim_quote(match: re.Match[str]) -> str:
            name_alias = match.group("name")
            name = alias_to_canonical.get(name_alias, name_alias)
            honorific = match.group("honorific") or ""
            verb = match.group("verb")
            lead = match.group("lead") or ""
            fragment = match.group("fragment")
            owner = self._guess_reference_owner(fragment)
            if owner == name and self._is_exactly_traceable_quote_fragment(fragment):
                return match.group(0)
            invalidated_names.add(name_alias)
            if owner and self._is_exactly_traceable_quote_fragment(fragment):
                return f"{self._format_reference_name(owner, honorific)}{verb}{lead}“{fragment}”"
            return f"有同学觉得{lead}“{fragment}”"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?\s*(?P<verb>说|说到|认为|觉得|提到|讲到)(?P<lead>[^。！？!?“\"「『]{{0,24}})[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』]",
            rewrite_named_claim_quote,
            value,
        )

        def rewrite_named_object(match: re.Match[str]) -> str:
            name_alias = match.group("name")
            name = alias_to_canonical.get(name_alias, name_alias)
            fragment = match.group("fragment")
            owner = self._guess_reference_owner(fragment)
            if owner == name:
                return match.group(0)
            invalidated_names.add(name_alias)
            if owner:
                return f"{owner}提到的“{fragment}”"
            return f"有同学提到的“{fragment}”"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?这个[“\"「『](?P<fragment>[^”\"」』]{{2,40}})[”\"」』]",
            rewrite_named_object,
            value,
        )

        def rewrite_named_labeled_quote(match: re.Match[str]) -> str:
            name_alias = match.group("name")
            name = alias_to_canonical.get(name_alias, name_alias)
            honorific = match.group("honorific") or ""
            fragment = match.group("fragment")
            noun = match.group("noun") or "点"
            owner = self._guess_reference_owner(fragment)
            if owner == name:
                return match.group(0)
            invalidated_names.add(name_alias)
            if owner:
                return f"{self._format_reference_name(owner, honorific)}提到的“{fragment}”"
            return f"这个{noun}"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?(?:刚才|刚刚|前面)?(?:那个|这个)?[“\"「『](?P<fragment>[^”\"」』]{{2,60}})[”\"」』](?:的)?(?P<noun>比喻|例子|说法|观点|想法|问题|表达|答案)",
            rewrite_named_labeled_quote,
            value,
        )
        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?(?:刚才|刚刚|前面)?(?:提到|说|讲|提出)(?:的)?[“\"「『](?P<fragment>[^”\"」』]{{2,60}})[”\"」』](?:这个|的)?(?P<noun>比喻|例子|说法|观点|想法|问题|表达|答案)?",
            rewrite_named_labeled_quote,
            value,
        )

        def rewrite_named_sentence_quote(match: re.Match[str]) -> str:
            body = match.group("body") or ""
            fragments = re.findall(r"[“\"「『]([^”\"」』]{2,80})[”\"」』]", body)
            if fragments and all(self._is_exactly_traceable_quote_fragment(fragment) for fragment in fragments):
                return match.group(0)
            return f"这句话{body}"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?这句话(?P<body>[^。！？!?]{{0,120}}[“\"「『][^。！？!?]{{0,120}})",
            rewrite_named_sentence_quote,
            value,
        )

        def rewrite_named_story_quote(match: re.Match[str]) -> str:
            name_alias = match.group("name")
            fragment = match.group("fragment")
            noun = match.group("noun") or "例子"
            owner = self._guess_reference_owner(fragment)
            name = alias_to_canonical.get(name_alias, name_alias)
            if owner == name and self._is_exactly_traceable_quote_fragment(fragment):
                return match.group(0)
            invalidated_names.add(name_alias)
            return f"这个{noun}"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?[，,:：]?\s*你(?:刚才)?(?:提|提到|讲|讲到|说|说到)(?:的)?(?:这个|那个)?[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』]的?(?P<noun>故事|例子|想法|问题|说法)",
            rewrite_named_story_quote,
            value,
        )

        def rewrite_sentence_pronoun(match: re.Match[str]) -> str:
            prefix = match.group("prefix") or ""
            verb = match.group("verb")
            fragment = match.group("fragment")
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

        def rewrite_named_direct_quote(match: re.Match[str]) -> str:
            prefix = match.group("prefix") or ""
            name_alias = match.group("name")
            name = alias_to_canonical.get(name_alias, name_alias)
            honorific = match.group("honorific") or ""
            verb = match.group("verb")
            fragment = match.group("fragment")
            suffix = match.group("suffix") or ""
            owner = self._guess_reference_owner(fragment)
            if owner == name:
                return match.group(0)
            invalidated_names.add(name_alias)
            owner_text = self._format_reference_name(owner, honorific) if owner else "有同学"
            return f"{prefix}{owner_text}{verb}“{fragment}”{suffix}"

        value = re.sub(
            rf"(?P<prefix>(?:刚才|前面|之前)?)(?P<name>{names_pattern})(?P<honorific>同学|先生)?\s*(?P<verb>说|说到|讲|讲到|提到|讲过|说过)[“\"「『](?P<fragment>[^”\"」』]{{1,80}})[”\"」』](?P<suffix>[^。！？!?]{{0,16}})",
            rewrite_named_direct_quote,
            value,
        )

        def rewrite_named_praise_quote(match: re.Match[str]) -> str:
            name_alias = match.group("name")
            name = alias_to_canonical.get(name_alias, name_alias)
            honorific = match.group("honorific") or ""
            praise = (match.group("praise") or "").strip()
            fragment = match.group("fragment")
            owner = self._guess_reference_owner(fragment)
            if owner == name:
                return match.group(0)
            invalidated_names.add(name_alias)
            praise = praise.lstrip("你").strip(" ，,：:—-")
            return f"{self._format_reference_name(name, honorific)}，你{praise}。"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?[，,:：]?\s*你(?P<praise>这个点说得[^“\"「『]{0, 12}|这句话说得[^“\"「『]{0, 12}|问得[^“\"「『]{0, 12}|提得[^“\"「『]{0, 12}|讲得[^“\"「『]{0, 12})[—\-–,:：，, ]*[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』][^。！？!?]{{0,24}}",
            rewrite_named_praise_quote,
            value,
        )

        def rewrite_broad_named_praise_quote(match: re.Match[str]) -> str:
            lead = (match.group("lead") or "").strip()
            if not re.search(r"(说得|讲得|问得|提得|想得|精彩|到位|太棒|真棒|很棒|真好)", lead):
                return match.group(0)
            name_alias = match.group("name")
            name = alias_to_canonical.get(name_alias, name_alias)
            honorific = match.group("honorific") or ""
            fragment = match.group("fragment")
            owner = self._guess_reference_owner(fragment)
            if owner == name:
                return match.group(0)
            invalidated_names.add(name_alias)
            lead = re.sub(r"[—\-–,:：，,]+$", "", lead).strip()
            return f"{self._format_reference_name(name, honorific)}，你{lead}。"

        value = re.sub(
            rf"(?P<name>{names_pattern})(?P<honorific>同学|先生)?[，,:：]?\s*你(?P<lead>[^“\"「『。！？!?]{{0,28}})[“\"「『](?P<fragment>[^”\"」』]{{2,80}})[”\"」』][^。！？!?]{{0,36}}[。！？!?]?",
            rewrite_broad_named_praise_quote,
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
            value = re.sub(r"(?:让我用)?你的(?:例子|故事)[^。！？!?]{0,160}[。！？!?]?", "", value)
            value = re.sub(r"你(?:对|并没有|只是)[^。！？!?]{0,160}[。！？!?]?", "", value)

        value = re.sub(r"\s{2,}", " ", value).strip()
        value = re.sub(r"^[，,:：\s]+", "", value)
        return value

    async def _record_turn_summary(self, source: str, content: str) -> None:
        if source not in self.all_names:
            return
        text = (content or "").strip()
        if not text or is_non_substantive_turn(text):
            return
        display_source = self._agent_to_display_name.get(source, source)
        summary = extract_core_viewpoint(text)
        if not summary:
            return
        if source in self.human_names:
            human_summaries = self._recent_human_turn_summaries.setdefault(source, [])
            if not human_summaries or human_summaries[-1] != summary:
                human_summaries.append(summary)
                if len(human_summaries) > 4:
                    self._recent_human_turn_summaries[source] = human_summaries[-4:]
        if self.summary_memory is None:
            return
        self._recent_turn_summaries.append((display_source, summary))
        if len(self._recent_turn_summaries) > 3:
            self._recent_turn_summaries = self._recent_turn_summaries[-3:]
        # 接地校验历史：保留较长的完整发言内容，便于核对被引片段的真实出处。
        self._grounding_history.append((display_source, text))
        if len(self._grounding_history) > 40:
            self._grounding_history = self._grounding_history[-40:]
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
                lambda match: (
                    ""
                    if self._looks_like_meta_reasoning_segment(match.group(1))
                    else match.group(0)
                ),
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
        had_streamed_tts = source in self._streaming_tts_emitted or bool(emitted_prefix)
        self._streaming_tts_emitted.discard(source)

        if not had_streamed_tts:
            return False, raw_content
        if not emitted_prefix:
            return False, raw_content
        if raw_content.startswith(emitted_prefix):
            tail = raw_content[len(emitted_prefix) :].lstrip()
            return True, self._filter_streamed_tail_duplicates(tail, emitted_segment_keys)

        emitted_segments, _ = self._drain_complete_stream_sentences(emitted_prefix)
        final_segments, final_remainder = self._drain_complete_stream_sentences(raw_content)
        matched_segments = 0
        for emitted_segment, final_segment in zip(emitted_segments, final_segments):
            if self._normalize_streaming_segment(
                emitted_segment
            ) != self._normalize_streaming_segment(final_segment):
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

    def _build_stalled_stream_message(self) -> Optional[TextMessage]:
        source = self._current_streaming_source
        if not source or source not in self.ai_names:
            return None

        emitted_prefix = self._streaming_emitted_raw_prefix.get(source, "")
        remainder = self._streaming_buffer.get(source, "")
        raw_content = f"{emitted_prefix}{remainder}".strip()
        if not raw_content:
            return None

        logger.warning(
            "[FloorManager] flushing stalled streaming content as final message: source=%s content_len=%d",
            source,
            len(raw_content),
        )
        return TextMessage(source=source, content=raw_content)

    async def _close_active_team_stream(self, stream: Any) -> None:
        aclose = getattr(stream, "aclose", None)
        if not callable(aclose):
            return
        close_task: asyncio.Task[Any] | None = None
        try:
            close_task = asyncio.ensure_future(aclose())
            await asyncio.wait_for(
                asyncio.shield(close_task),
                timeout=self._TEAM_STREAM_CLOSE_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.debug(
                "[FloorManager] closing abandoned team stream timed out; continuing with restart"
            )
            if close_task is not None:
                close_task.cancel()
                try:
                    await asyncio.wait_for(
                        close_task,
                        timeout=self._TEAM_STREAM_CLOSE_CANCEL_GRACE_SEC,
                    )
                except (asyncio.CancelledError, GeneratorExit):
                    pass
                except asyncio.TimeoutError:
                    self._cancelled_wait_tasks.add(close_task)
                    close_task.add_done_callback(self._consume_cancelled_wait_task_result)
                    if self._team_factory is not None:
                        self._team_rebuild_requested = True
                        await self._force_stop_autogen_runtime(
                            self.team,
                            reason="stalled stream close",
                        )
                except Exception:
                    logger.debug("[FloorManager] cancelled team stream close failed", exc_info=True)
        except (asyncio.CancelledError, GeneratorExit):
            if close_task is not None and not close_task.done():
                close_task.cancel()
            raise
        except Exception:
            logger.debug("[FloorManager] closing abandoned team stream failed", exc_info=True)

    def _is_repeated_non_moderator_turn(self, source: str) -> bool:
        if source not in self.ai_names or source == "moderator":
            return False
        current_display = self._agent_to_display_name.get(source, source)
        return bool(
            self._recent_display_speakers and self._recent_display_speakers[-1] == current_display
        )

    def _should_suppress_ai_while_human_waiting(self, source: str) -> bool:
        pending_human_speaker = self._normalize_agent_name(self._last_human_input_requested_speaker)
        has_pending_human_request = pending_human_speaker in self.human_names
        return (
            (
                self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
                or self._blocked_for_human_input
                or has_pending_human_request
            )
            and source in self.ai_names
            and source != "moderator"
        )

    async def _prepare_pending_human_message_state(self, source: str) -> None:
        """Normalize state before accepting a human message after residual AI output."""
        if source not in self.human_names:
            return
        if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
            return
        pending_human_speaker = self._normalize_agent_name(
            self.current_speaker or self._last_human_input_requested_speaker
        )
        if pending_human_speaker in self.human_names and pending_human_speaker != source:
            return
        allowed = self._ALLOWED_STATE_TRANSITIONS.get(self.state, frozenset())
        if FloorState.HUMAN_TURN_WAITING not in allowed and FloorState.SELECTING_SPEAKER in allowed:
            await self._enter_selecting_speaker(
                reason="prepare_pending_human_message",
                recovery=True,
            )
        if FloorState.HUMAN_TURN_WAITING in self._ALLOWED_STATE_TRANSITIONS.get(
            self.state, frozenset()
        ):
            self.current_speaker = source
            await self._set_state(
                FloorState.HUMAN_TURN_WAITING,
                reason="pending_human_message_recovered",
                recovery=True,
            )

    async def _sync_ai_turn_without_selection_event(self, source: str, *, reason: str) -> None:
        if source not in self.ai_names:
            return
        self.current_speaker = source
        if self.state == FloorState.MODERATOR_OPENING:
            await self._enter_selecting_speaker(reason="implicit_speaker_selection", recovery=True)
        if self.state == FloorState.SELECTING_SPEAKER:
            await self._set_state(FloorState.AI_SPEAKING, reason=reason, recovery=True)

    async def _make_human_input_requested_event(
        self,
        speaker: str,
        *,
        reason: str,
        clear_designation: bool = True,
    ) -> Optional[dict]:
        if not speaker or speaker not in self.human_names:
            return None
        if self._discussion_end_requested or self.state in (FloorState.CLOSING, FloorState.ENDED):
            logger.info(
                "[FloorManager] 忽略 closing/ended 期间的人类请求恢复: speaker=%s state=%s reason=%s",
                speaker,
                self.state,
                reason,
            )
            if speaker == self._deferred_human_request_speaker:
                self._deferred_human_request_speaker = None
                self._deferred_human_request_reason = ""
            if clear_designation:
                self._set_designated_speaker(None)
            return None
        if speaker == self._last_human_input_requested_speaker and self.state in (
            FloorState.HUMAN_TURN_WAITING,
            FloorState.HUMAN_SPEAKING,
        ):
            logger.info(
                "[FloorManager] 忽略重复 human_input_requested: speaker=%s state=%s",
                speaker,
                self.state,
            )
            return None
        request_reason = self._resolve_human_input_request_reason(reason)
        if not self._is_authorized_human_request_reason(request_reason):
            logger.warning(
                "[FloorManager] 拦截未授权 human_input_requested: speaker=%s reason=%s pending_reason=%s",
                speaker,
                reason,
                self._pending_human_input_reason,
            )
            if clear_designation:
                self._set_designated_speaker(None)
            if (reason or "").strip() in {
                "speaker_selected_human",
                "human_input_requested_waiting",
            }:
                await self._recover_from_stale_human_request(
                    speaker,
                    reason="stale_human_input_request",
                )
            return None
        if speaker == self._deferred_human_request_speaker:
            self._deferred_human_request_speaker = None
            self._deferred_human_request_reason = ""
        if not self._has_substantive_utterance(speaker):
            self._set_speaker_utterance_status(speaker, SpeakerUtteranceStatus.NOMINATED_ONLY)
        self.current_speaker = speaker
        if clear_designation:
            self._set_designated_speaker(None)
        self._last_human_input_requested_speaker = speaker
        if self.state != FloorState.HUMAN_TURN_WAITING:
            allowed = self._ALLOWED_STATE_TRANSITIONS.get(self.state, frozenset())
            if (
                FloorState.HUMAN_TURN_WAITING not in allowed
                and FloorState.SELECTING_SPEAKER in allowed
            ):
                await self._enter_selecting_speaker(
                    reason=f"prepare_human_input_request:{request_reason}",
                    recovery=True,
                )
            await self._set_state(FloorState.HUMAN_TURN_WAITING, reason=reason)
        else:
            self._touch_progress(reason)
        self._pause_team_for_human_input()
        self._human_input_request_seq += 1
        request_id = f"hr-{self._human_input_request_seq}"
        self._last_human_input_request_id = request_id
        return {
            "event_type": "human_input_requested",
            "data": {
                "speaker": speaker,
                "reason": request_reason,
                "request_id": request_id,
                "state": self.state.value,
            },
        }

    async def _recover_from_stale_human_request(self, speaker: str, *, reason: str) -> None:
        normalized_speaker = self._normalize_agent_name(speaker)
        logger.warning(
            "[FloorManager] recovering from stale human request: speaker=%s state=%s",
            normalized_speaker or speaker,
            self.state,
        )
        if normalized_speaker and self.current_speaker == normalized_speaker:
            self.current_speaker = ""
        if normalized_speaker and normalized_speaker == self._deferred_human_request_speaker:
            self._deferred_human_request_speaker = None
            self._deferred_human_request_reason = ""
        self._last_human_input_requested_speaker = ""
        self._last_human_input_request_id = ""
        self._pending_human_input_reason = "normal"
        self._resume_team_after_human_input()
        if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
            await self._set_state(
                FloorState.SELECTING_SPEAKER,
                reason=reason,
                recovery=True,
            )
        if self.state != FloorState.INIT:
            self._request_stream_restart(reason)

    async def _recover_pending_human_turn_after_stream_end(
        self,
        *,
        reason: str,
        log_message: str,
        request_stream_restart: bool = True,
    ) -> Optional[dict]:
        skipped_speaker = (
            self._current_waiting_human_speaker()
            or self.current_speaker
            or self._last_human_input_requested_speaker
        )
        pending_human_text = self._pop_submitted_human_input(skipped_speaker)
        display_speaker = self._agent_to_display_name.get(skipped_speaker, skipped_speaker)
        logger.warning(log_message)
        result: Optional[dict] = None
        if pending_human_text:
            human_source = self._normalize_agent_name(skipped_speaker)
            result = await self._process_event(
                TextMessage(source=human_source or skipped_speaker, content=pending_human_text)
            )
        elif display_speaker:
            self._set_speaker_utterance_status(skipped_speaker, SpeakerUtteranceStatus.TIMED_OUT)
            self._register_human_skip(reason=reason, is_timeout=True)
            await self._emit_message(
                "系统",
                f"{display_speaker}这一轮还没来得及发言，系统先按跳过处理。",
                "system",
            )

        self._last_human_input_requested_speaker = ""
        self._last_human_input_request_id = ""
        self._pending_human_input_reason = "normal"
        self._set_designated_speaker(None)
        self._resume_team_after_human_input()
        await self._set_state(
            FloorState.SELECTING_SPEAKER,
            reason=reason,
            recovery=True,
        )
        if self._recent_human_skip_pending:
            fallback = self._smart_fallback_speaker()
            if fallback and fallback not in self.human_names:
                self._set_designated_speaker(fallback)
                if fallback in self.ai_names:
                    self._expected_next_ai_speaker = fallback
                    self._pending_continuation_task = self._build_targeted_continuation_task(
                        fallback
                    )
        self._team_rebuild_requested = True
        if not request_stream_restart:
            self._clear_stream_restart_request()
        if request_stream_restart:
            self._request_stream_restart(reason)
        return result

    def on_message(self, callback: Callable) -> "FloorManager":
        """注册消息回调。callback(source, content, msg_type[, tts_text])"""
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
        """注册打断回调。callback(interrupter, current_speaker, approved_by, request_id)"""
        self._on_interrupt = callback
        return self

    def on_human_input_requested(self, callback: Callable) -> "FloorManager":
        """注册真人输入请求回调。callback(data)"""
        self._on_human_input_requested = callback
        return self

    def pending_human_input_request_snapshot(self) -> Optional[dict[str, str]]:
        """返回当前待处理的人类输入请求，用于暂停恢复后的前端状态重同步。"""
        if self.state not in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
            return None
        speaker = self._normalize_agent_name(
            self.current_speaker or self._last_human_input_requested_speaker
        )
        if not speaker or speaker not in self.human_names:
            return None
        reason = self._resolve_human_input_request_reason("human_input_requested_waiting")
        request_id = (
            self._last_human_input_request_id or f"hr-{max(self._human_input_request_seq, 1)}"
        ).strip()
        return {
            "speaker": speaker,
            "reason": reason,
            "request_id": request_id,
            "state": self.state.value,
        }

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
        agent_speaker = self._display_name_to_agent.get(speaker, speaker)
        await put_human_input(agent_speaker, text, session_scope=self._human_queue_scope)

    def _remember_submitted_human_input(self, speaker: str, text: str) -> None:
        agent_speaker = self._normalize_agent_name(speaker)
        if agent_speaker:
            self._pending_submitted_human_inputs[agent_speaker] = (text or "").strip()

    def _current_waiting_human_speaker(self) -> str:
        current = self._normalize_agent_name(self.current_speaker)
        if current in self.human_names:
            return current
        requested = self._normalize_agent_name(self._last_human_input_requested_speaker)
        if requested in self.human_names:
            return requested
        return current or requested

    def _should_inline_process_submitted_human_input(self, speaker: str) -> bool:
        agent_speaker = self._normalize_agent_name(speaker)
        if not agent_speaker:
            return False
        if self.state not in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
            return False
        waiting_speaker = self._current_waiting_human_speaker()
        if waiting_speaker != agent_speaker:
            return False
        active_wait = self._active_stream_next_event_task
        return active_wait is None or active_wait.done()

    def _peek_submitted_human_input(self, speaker: str) -> str:
        agent_speaker = self._normalize_agent_name(speaker)
        if not agent_speaker:
            return ""
        return (self._pending_submitted_human_inputs.get(agent_speaker, "") or "").strip()

    def _take_queued_human_input(self, agent_speaker: str) -> str:
        try:
            queue = get_human_queue(agent_speaker, session_scope=self._human_queue_scope)
        except KeyError:
            return ""
        try:
            queued_text = queue.get_nowait()
        except asyncio.QueueEmpty:
            return ""
        return (queued_text or "").strip()

    def _pop_submitted_human_input(self, speaker: str) -> str:
        agent_speaker = self._normalize_agent_name(speaker)
        if not agent_speaker:
            return ""
        cached = (self._pending_submitted_human_inputs.pop(agent_speaker, "") or "").strip()
        queued_text = self._take_queued_human_input(agent_speaker)
        if cached:
            return cached
        return queued_text

    async def _recover_pending_human_input_before_next_team_wait(self) -> Optional[dict]:
        waiting_speaker = self._current_waiting_human_speaker()
        pending_submitted_input = (
            self._peek_submitted_human_input(waiting_speaker)
            if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
            else ""
        )
        if not pending_submitted_input:
            return None
        return await self._recover_pending_human_turn_after_stream_end(
            reason="submitted_human_input_owner_recovery",
            log_message="[FloorManager] owner loop recovered pending human input before opening next team wait",
            request_stream_restart=False,
        )

    async def _emit_message(
        self,
        source: str,
        content: str,
        msg_type: str = "text",
        *,
        tts_text: str = "",
    ) -> None:
        """发送消息事件。"""
        self._touch_progress("emit_message")
        if self._on_message:
            callback = self._on_message
            forwarded_tts_text = (tts_text or "").strip()
            if not forwarded_tts_text and msg_type != "system" and source != "系统":
                forwarded_tts_text = (content or "").strip()

            try:
                signature = inspect.signature(callback)
                params = list(signature.parameters.values())
                accepts_tts_text = (
                    any(
                        param.kind
                        in (
                            inspect.Parameter.VAR_POSITIONAL,
                            inspect.Parameter.VAR_KEYWORD,
                        )
                        for param in params
                    )
                    or len(signature.parameters) >= 4
                )
            except (TypeError, ValueError):
                accepts_tts_text = True

            if accepts_tts_text:
                await callback(source, content, msg_type, forwarded_tts_text)
            else:
                await callback(source, content, msg_type)

    async def _emit_turn_change(self, speaker: str, is_human: bool) -> None:
        """发送轮次变更事件。"""
        self.current_speaker = speaker
        self._touch_progress("emit_turn_change")
        if self._on_turn_change:
            await self._on_turn_change(speaker, is_human)

    async def _enter_selecting_speaker(self, *, reason: str, recovery: bool = False) -> None:
        if self.state != FloorState.SELECTING_SPEAKER:
            await self._set_state(FloorState.SELECTING_SPEAKER, reason=reason, recovery=recovery)

    def _queue_run_event(self, event: Optional[dict]) -> None:
        if self._run_loop_active and event is not None:
            self._pending_run_events.append(event)

    def _drain_pending_run_events(self) -> list[dict]:
        if not self._pending_run_events:
            return []
        events = list(self._pending_run_events)
        self._pending_run_events.clear()
        return events

    async def _notify_deferred_human_input_requested(self, speaker: str, *, reason: str) -> None:
        if not self._on_human_input_requested or not speaker:
            return
        await self._on_human_input_requested(
            {
                "speaker": speaker,
                "reason": self._resolve_human_input_request_reason(reason),
                "state": FloorState.HUMAN_TURN_WAITING.value,
            }
        )

    async def _set_state(
        self,
        new_state: FloorState,
        *,
        reason: str = "",
        recovery: bool = False,
    ) -> None:
        """更新状态并发送事件。"""
        old_state = self.state
        if old_state == new_state:
            self._touch_progress("set_state_noop")
            return
        allowed = self._ALLOWED_STATE_TRANSITIONS.get(old_state, frozenset())
        if new_state not in allowed:
            message = (
                f"Illegal floor state transition: {old_state.value} -> {new_state.value}"
                f" (reason={reason or 'n/a'})"
            )
            logger.error("[FloorManager] %s", message)
            raise RuntimeError(message)
        self.state = new_state
        self._state_entered_mono = time.monotonic()
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

    def _request_stream_restart(self, reason: str) -> None:
        self._stream_restart_requested = True
        self._stream_restart_gate.set()
        logger.info("[FloorManager] requested team stream restart: %s", reason)

    def _clear_stream_restart_request(self) -> None:
        self._stream_restart_requested = False
        self._stream_restart_gate.clear()

    async def _cancel_pending_wait_task(
        self,
        task: Optional[asyncio.Task[Any]],
        *,
        team_wait: bool = False,
    ) -> None:
        if task is None or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(
                task,
                timeout=self._PENDING_WAIT_CANCEL_TIMEOUT_SEC,
            )
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        except asyncio.TimeoutError:
            logger.warning(
                "[FloorManager] timed out waiting %.2fs for cancelled team wait task; continuing recovery",
                self._PENDING_WAIT_CANCEL_TIMEOUT_SEC,
            )
            self._cancelled_wait_tasks.add(task)
            task.add_done_callback(self._consume_cancelled_wait_task_result)
            if team_wait and self._team_factory is not None:
                self._team_rebuild_requested = True
                await self._force_stop_autogen_runtime(
                    self.team,
                    reason="stalled stream wait cancellation",
                )
        except Exception:
            logger.debug("[FloorManager] cancel pending wait task failed", exc_info=True)

    def _cancel_active_stream_wait(self, reason: str) -> None:
        task = self._active_stream_next_event_task
        if task is None or task.done():
            return
        task.cancel()
        logger.info("[FloorManager] canceled active team wait task: %s", reason)

    def _log_repeated_non_moderator_drop(self, source: str, content: str, *, kind: str) -> None:
        key = (kind, source, self.current_speaker or "")
        if key in self._repeated_non_moderator_drop_log_keys:
            logger.debug(
                "[FloorManager] 丢弃连续非主持人%s: source=%s content=%s",
                kind,
                source,
                str(content)[:120],
            )
            return
        if len(self._repeated_non_moderator_drop_log_keys) > 32:
            self._repeated_non_moderator_drop_log_keys.clear()
        self._repeated_non_moderator_drop_log_keys.add(key)
        logger.warning(
            "[FloorManager] 丢弃连续非主持人%s，避免残留 TTS 外泄: source=%s content=%s",
            kind,
            source,
            str(content)[:120],
        )

    async def _watchdog_loop(self) -> None:
        """Watchdog loop: auto-recover stuck human-turn windows and emit diagnostics."""
        _connection_lost_at: Optional[float] = None
        while not self._watchdog_stop.is_set():
            await asyncio.sleep(self._stall_check_interval_sec)

            # 暂停期间跳过所有 watchdog 检查
            if self._paused:
                continue

            now = time.monotonic()
            idle_sec = now - self._last_progress_ts

            if self.state in (FloorState.ENDED, FloorState.CLOSING):
                continue

            # Guard 1: Absolute session duration limit (30 min).
            # Only applies when run() has started (discussion_started_mono > 0).
            if self._discussion_started_mono > 0:
                session_duration_sec = now - self._discussion_started_mono
                if session_duration_sec >= self._MAX_SESSION_DURATION_SEC:
                    logger.error(
                        "[FloorManager] session exceeded max duration %.0fs; force-ending session=%s",
                        session_duration_sec,
                        self.session_id,
                    )
                    await self._emit_message(
                        "系统",
                        "讨论已超过最大时长限制（30分钟），系统自动结束。",
                        "system",
                    )
                    self._discussion_end_requested = True
                    self._request_stream_restart("max_session_duration")
                    continue

            # Guard 2: Client connection lost for too long (only during active session)
            if (
                self._discussion_started_mono > 0
                and self._is_connected is not None
                and not self._is_connected()
            ):
                if _connection_lost_at is None:
                    _connection_lost_at = now
                    logger.warning(
                        "[FloorManager] client connection lost session=%s",
                        self.session_id,
                    )
                elif now - _connection_lost_at >= 30.0:
                    logger.error(
                        "[FloorManager] client disconnected for %.0fs; force-ending session=%s",
                        now - _connection_lost_at,
                        self.session_id,
                    )
                    await self._emit_message(
                        "系统",
                        "连接已断开超过30秒，讨论自动结束。",
                        "system",
                    )
                    self._discussion_end_requested = True
                    self._request_stream_restart("client_disconnected")
                    continue
            else:
                _connection_lost_at = None

            # Guard 3: Consecutive stall recoveries without progress (only during active session)
            if (
                self._discussion_started_mono > 0
                and self._consecutive_stall_recoveries >= self._MAX_CONSECUTIVE_STALL_RECOVERIES
            ):
                logger.error(
                    "[FloorManager] %d consecutive stall recoveries without progress; force-ending session=%s",
                    self._consecutive_stall_recoveries,
                    self.session_id,
                )
                await self._emit_message(
                    "系统",
                    "讨论多次停滞无法恢复，系统自动结束。",
                    "system",
                )
                self._discussion_end_requested = True
                self._request_stream_restart("max_stall_recoveries")
                continue

            if await self._handle_state_dwell_timeout(now=now):
                continue

            # Prefer explicit auto-recovery for human wait stalls.
            if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
                waiting_speaker = self._current_waiting_human_speaker()
                pending_submitted_input = (
                    self._pending_submitted_human_inputs.get(
                        self._normalize_agent_name(waiting_speaker),
                        "",
                    )
                    or ""
                ).strip()
                if waiting_speaker and pending_submitted_input and idle_sec >= 15.0:
                    if now - self._last_watchdog_action_ts < 6.0:
                        continue
                    self._last_watchdog_action_ts = now
                    logger.warning(
                        "[FloorManager] human input was submitted but still not consumed after %.1fs; nudging owner loop to recover pending human turn speaker=%s",
                        idle_sec,
                        waiting_speaker,
                    )
                    self._request_stream_restart("watchdog_pending_human_input")
                    self._touch_progress("watchdog_nudge_pending_human_input")
                    continue
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
                    speaker = self._current_waiting_human_speaker() or self.current_speaker
                    display = self._agent_to_display_name.get(speaker, speaker)
                    self._human_timeout_count += 1
                    self._set_speaker_utterance_status(speaker, SpeakerUtteranceStatus.TIMED_OUT)
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

            if (
                self._deferred_human_request_speaker
                and idle_sec >= self._deferred_human_request_timeout_sec
            ):
                if now - self._last_watchdog_action_ts < 12.0:
                    continue
                self._last_watchdog_action_ts = now
                logger.warning(
                    "[FloorManager] deferred human request timed out without stream boundary; requesting human input speaker=%s state=%s",
                    self._deferred_human_request_speaker,
                    self.state,
                )
                human_request = await self._make_human_input_requested_event(
                    self._deferred_human_request_speaker,
                    reason=self._deferred_human_request_reason or "moderator_designated_human",
                    clear_designation=False,
                )
                if human_request is not None and self._on_human_input_requested:
                    await self._on_human_input_requested(human_request["data"])
                self._touch_progress("watchdog_deferred_human_request")
                continue

            # Non-human general stall recovery is handled inside run(), where the
            # active team stream can be restarted safely after timeout.

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
        self._end_requested_by_human = ""
        self._end_request_source = ""
        self._pending_submitted_human_inputs.clear()
        self._discussion_started_mono = time.monotonic()
        self._expected_next_ai_speaker = None
        self._deferred_human_request_speaker = None
        self._deferred_human_request_reason = ""
        self._pending_continuation_task = None
        self._clear_stream_restart_request()
        self._pending_moderator_human_invite_target = None
        self._recent_turn_summaries.clear()
        if self.summary_memory is not None:
            await self.summary_memory.clear()
        if self.human_guidance_memory is not None:
            await self.human_guidance_memory.clear()
        had_error = False

        recovered_human_turn_stream_end = False
        saw_events_after_human_turn_recovery = False
        _local_msg_count = 0
        _local_budget_cap = self._nominal_max_turns * 2 + 8
        self._consecutive_selector_stalls = 0
        self._consecutive_stall_recoveries = 0

        try:
            next_task: Optional[str] = self._build_initial_task(topic)

            while True:
                if next_task is None and self._pending_continuation_task:
                    next_task = self._pending_continuation_task
                    self._pending_continuation_task = None
                stream = self.team.run_stream(task=next_task)
                stream_iter = stream.__aiter__()
                next_task = None
                restart_after_general_stall = False
                stream_exhausted = False
                stream_had_any_event = False
                stream_consumed_expected_ai_turn = False
                stream_closed_for_recovery = False

                while True:
                    if self._paused or self._blocked_for_human_input:
                        await self._wait_until_resumed()
                    result = await self._recover_pending_human_input_before_next_team_wait()
                    if result is not None:
                        await self._close_active_team_stream(stream)
                        stream_closed_for_recovery = True
                        if result is not None:
                            yield result
                        recovered_human_turn_stream_end = True
                        saw_events_after_human_turn_recovery = False
                        restart_after_general_stall = True
                        logger.info(
                            "[FloorManager] restarting team stream after owner-loop pending human recovery"
                        )
                        break
                    next_event_task: Optional[asyncio.Task[Any]] = None
                    restart_task: Optional[asyncio.Task[Any]] = None
                    try:
                        pending_submitted_input = (
                            self._peek_submitted_human_input(
                                self.current_speaker or self._last_human_input_requested_speaker
                            )
                            if self.state
                            in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
                            else ""
                        )
                        stall_timeout_sec = (
                            self._deferred_human_request_timeout_sec
                            if self._deferred_human_request_speaker
                            else self._general_stall_timeout_sec
                        )
                        if pending_submitted_input:
                            stall_timeout_sec = min(
                                stall_timeout_sec,
                                self._submitted_human_input_recovery_timeout_sec,
                            )
                        next_event_task = asyncio.create_task(stream_iter.__anext__())
                        self._active_stream_next_event_task = next_event_task
                        restart_task = asyncio.create_task(self._stream_restart_gate.wait())
                        done, _pending = await asyncio.wait(
                            {next_event_task, restart_task},
                            timeout=stall_timeout_sec,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if restart_task in done and self._stream_restart_requested:
                            await self._cancel_pending_wait_task(next_event_task, team_wait=True)
                            self._clear_stream_restart_request()
                            current_waiting_speaker = self._current_waiting_human_speaker()
                            current_pending_submitted_input = (
                                self._peek_submitted_human_input(current_waiting_speaker)
                                if self.state
                                in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
                                else ""
                            )
                            if current_pending_submitted_input:
                                await self._close_active_team_stream(stream)
                                stream_closed_for_recovery = True
                                result = await self._recover_pending_human_turn_after_stream_end(
                                    reason="submitted_human_input_restart",
                                    log_message="[FloorManager] submitted human input triggered owner-loop recovery",
                                    request_stream_restart=False,
                                )
                                if result is not None:
                                    yield result
                                recovered_human_turn_stream_end = True
                                saw_events_after_human_turn_recovery = False
                            restart_after_general_stall = True
                            logger.info(
                                "[FloorManager] restarting team stream after explicit restart request"
                            )
                            self._touch_progress("run_stream_explicit_restart")
                            break
                        if next_event_task in done:
                            await self._cancel_pending_wait_task(restart_task)
                            current_waiting_speaker = self._current_waiting_human_speaker()
                            current_pending_submitted_input = (
                                self._peek_submitted_human_input(current_waiting_speaker)
                                if self.state
                                in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
                                else ""
                            )
                            if next_event_task.cancelled() and current_pending_submitted_input:
                                await self._close_active_team_stream(stream)
                                stream_closed_for_recovery = True
                                result = await self._recover_pending_human_turn_after_stream_end(
                                    reason="submitted_human_input_restart",
                                    log_message="[FloorManager] canceled owner wait after submitted human input; recovering pending human turn",
                                    request_stream_restart=False,
                                )
                                if result is not None:
                                    yield result
                                recovered_human_turn_stream_end = True
                                saw_events_after_human_turn_recovery = False
                                restart_after_general_stall = True
                                logger.info(
                                    "[FloorManager] recovering pending human input after active wait cancellation"
                                )
                                break
                            event = next_event_task.result()
                            stream_had_any_event = True
                        else:
                            await self._cancel_pending_wait_task(next_event_task, team_wait=True)
                            await self._cancel_pending_wait_task(restart_task)
                            raise asyncio.TimeoutError
                    except asyncio.TimeoutError:
                        if self._paused or self._blocked_for_human_input:
                            continue
                        if self._discussion_end_requested or self.state in (
                            FloorState.CLOSING,
                            FloorState.ENDED,
                        ):
                            logger.info(
                                "[FloorManager] ignoring stall recovery because discussion is closing state=%s",
                                self.state,
                            )
                            break
                        waiting_speaker = self._current_waiting_human_speaker()
                        pending_submitted_input = (
                            self._peek_submitted_human_input(waiting_speaker)
                            if self.state
                            in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
                            else ""
                        )
                        if pending_submitted_input:
                            await self._cancel_pending_wait_task(next_event_task, team_wait=True)
                            await self._close_active_team_stream(stream)
                            stream_closed_for_recovery = True
                            result = await self._recover_pending_human_turn_after_stream_end(
                                reason="watchdog_pending_human_input",
                                log_message="[FloorManager] human input submitted while team remained waiting; finalize pending human input on owner loop",
                                request_stream_restart=False,
                            )
                            if result is not None:
                                yield result
                            recovered_human_turn_stream_end = True
                            saw_events_after_human_turn_recovery = False
                            restart_after_general_stall = True
                            logger.info(
                                "[FloorManager] owner loop recovered pending human input after short timeout"
                            )
                            break
                        if self._deferred_human_request_speaker:
                            logger.warning(
                                "[FloorManager] deferred human request stalled before stream boundary event; requesting human input directly speaker=%s state=%s",
                                self._deferred_human_request_speaker,
                                self.state,
                            )
                            human_request = await self._make_human_input_requested_event(
                                self._deferred_human_request_speaker,
                                reason=self._deferred_human_request_reason
                                or "moderator_designated_human",
                                clear_designation=False,
                            )
                            if human_request is not None:
                                yield human_request
                                self._touch_progress("run_stream_timeout_deferred_human_request")
                                continue
                        stalled_stream_message = self._build_stalled_stream_message()
                        if stalled_stream_message is not None:
                            result = await self._process_event(stalled_stream_message)
                            if result:
                                yield result
                                if result.get("event_type") == "message":
                                    _local_msg_count += 1
                                    if _local_msg_count >= _local_budget_cap:
                                        logger.warning(
                                            "[FloorManager] local message budget exhausted (%d/%d), forcing discussion end",
                                            _local_msg_count,
                                            _local_budget_cap,
                                        )
                                        self._discussion_end_requested = True
                                    _src = (result.get("data") or {}).get("source", "")
                                    if (
                                        _src == "moderator"
                                        and self._expected_next_ai_speaker in self.ai_names
                                        and self._expected_next_ai_speaker != "moderator"
                                    ):
                                        logger.info(
                                            "[FloorManager] moderator designated AI handoff pending after stalled stream flush; restarting continuation target=%s",
                                            self._expected_next_ai_speaker,
                                        )
                                if self._discussion_end_requested:
                                    logger.info("[FloorManager] 主持人结束语已发出，停止后续调度")
                                    break
                            self._touch_progress("run_stream_timeout_flush_stream")
                            restart_after_general_stall = True
                            self._consecutive_stall_recoveries += 1
                            break
                        now = time.monotonic()
                        self._last_watchdog_action_ts = now
                        self._consecutive_selector_stalls += 1
                        self._consecutive_stall_recoveries += 1
                        logger.warning(
                            "[FloorManager] team stream stalled while waiting for next event; restarting continuation state=%s stalls=%d",
                            self.state,
                            self._consecutive_selector_stalls,
                        )
                        # Smart fallback: after consecutive stalls, designate next speaker deterministically
                        if (
                            self._consecutive_selector_stalls
                            >= self._max_consecutive_selector_stalls
                        ):
                            fallback = self._select_fallback_and_designate()
                            msg = (
                                f"检测到流程持续停滞，系统已指定 {self._agent_to_display_name.get(fallback, fallback)} 继续发言。"
                                if fallback
                                else "检测到流程停滞，系统正在自动恢复调度。"
                            )
                            await self._emit_message("系统", msg, "system")
                            if fallback and fallback in self.human_names:
                                human_request = await self._make_human_input_requested_event(
                                    fallback,
                                    reason="moderator_designated_human",
                                    clear_designation=False,
                                )
                                if human_request is not None:
                                    yield human_request
                            self._consecutive_selector_stalls = 0
                        else:
                            await self._emit_message(
                                "系统",
                                "检测到流程停滞，系统正在自动恢复调度。",
                                "system",
                            )
                        self._touch_progress("run_stream_timeout_recovery")
                        restart_after_general_stall = True
                        break
                    except StopAsyncIteration:
                        stream_exhausted = True
                        break
                    finally:
                        await self._cancel_pending_wait_task(restart_task)
                        if self._active_stream_next_event_task is next_event_task:
                            self._active_stream_next_event_task = None

                    if recovered_human_turn_stream_end:
                        saw_events_after_human_turn_recovery = True

                    expected_ai_before_event = self._expected_next_ai_speaker
                    if (
                        expected_ai_before_event in self.ai_names
                        and expected_ai_before_event != "moderator"
                    ):
                        event_source = self._normalize_agent_name(
                            getattr(event, "source", self.current_speaker)
                        )
                        if event_source == expected_ai_before_event:
                            stream_consumed_expected_ai_turn = True

                    result = await self._process_event(event)
                    if result:
                        if result.get("event_type") in {
                            "message",
                            "stream",
                            "human_input_requested",
                        }:
                            self._consecutive_selector_stalls = 0
                            self._consecutive_stall_recoveries = 0
                        yield result
                        if result.get("event_type") == "message":
                            _src = (result.get("data") or {}).get("source", "")
                            _local_msg_count += 1
                            if _local_msg_count >= _local_budget_cap:
                                logger.warning(
                                    "[FloorManager] local message budget exhausted (%d/%d), forcing discussion end",
                                    _local_msg_count,
                                    _local_budget_cap,
                                )
                                self._discussion_end_requested = True
                            if (
                                _src == "moderator"
                                and self._expected_next_ai_speaker in self.ai_names
                                and self._expected_next_ai_speaker != "moderator"
                            ):
                                logger.info(
                                    "[FloorManager] moderator designated AI handoff pending; keep current stream alive until boundary target=%s",
                                    self._expected_next_ai_speaker,
                                )
                        if self._discussion_end_requested:
                            logger.info("[FloorManager] 主持人结束语已发出，停止后续调度")
                            break
                    if self._stream_restart_requested:
                        self._clear_stream_restart_request()
                        restart_after_general_stall = True
                        logger.info(
                            "[FloorManager] restarting team stream after explicit restart request"
                        )
                        break

                if self._discussion_end_requested:
                    break

                expected_ai_pending_at_stream_end = (
                    self._expected_next_ai_speaker in self.ai_names
                    and self._expected_next_ai_speaker != "moderator"
                )
                if (
                    stream_exhausted
                    and not stream_had_any_event
                    and self._expected_next_ai_speaker == "moderator"
                    and self._pending_moderator_human_invite_target
                ):
                    logger.warning(
                        "[FloorManager] moderator human-invite recovery stream ended without events; clearing target=%s",
                        self._pending_moderator_human_invite_target,
                    )
                    self._pending_moderator_human_invite_target = None
                    self._expected_next_ai_speaker = None
                    self._pending_continuation_task = None
                    self._set_designated_speaker(None)
                    break
                if stream_exhausted and (
                    self.state not in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
                    and not expected_ai_pending_at_stream_end
                    and (
                        not stream_consumed_expected_ai_turn
                        or self._should_block_moderator_final_closing()
                    )
                ):
                    if self._schedule_recovery_after_premature_stream_end():
                        restart_after_general_stall = True

                if stream_exhausted and self._deferred_human_request_speaker:
                    human_request = await self._make_human_input_requested_event(
                        self._deferred_human_request_speaker,
                        reason=self._deferred_human_request_reason or "moderator_designated_human",
                        clear_designation=False,
                    )
                    if human_request is not None:
                        yield human_request
                        if self._paused or self._blocked_for_human_input:
                            await self._wait_until_resumed()
                        if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
                            result = await self._recover_pending_human_turn_after_stream_end(
                                reason="deferred_human_request_stream_boundary",
                                log_message="[FloorManager] deferred human request reached stream boundary; finalize pending human input if present",
                                request_stream_restart=False,
                            )
                            if result is not None:
                                yield result
                            recovered_human_turn_stream_end = True
                            saw_events_after_human_turn_recovery = False
                            logger.info(
                                "[FloorManager] deferred human turn recovered at stream boundary; restarting team stream continuation"
                            )
                            continue

                if restart_after_general_stall:
                    abandoned_team = self.team
                    if not stream_closed_for_recovery:
                        await self._close_active_team_stream(stream)
                    if self._team_rebuild_requested and self._team_factory is not None:
                        await self._force_stop_autogen_runtime(
                            abandoned_team,
                            reason="team rebuild after stream recovery",
                        )
                        self.team = self._team_factory()
                        self._team_rebuild_requested = False
                        logger.info(
                            "[FloorManager] rebuilt discussion team after owner-loop human recovery"
                        )
                    logger.info(
                        "[FloorManager] restarting team stream after general stall recovery"
                    )
                    continue

                if (
                    stream_exhausted
                    and self._expected_next_ai_speaker in self.ai_names
                    and self._expected_next_ai_speaker != "moderator"
                ):
                    if not stream_had_any_event:
                        logger.warning(
                            "[FloorManager] designated AI continuation exhausted without events; clearing pending target=%s",
                            self._expected_next_ai_speaker,
                        )
                        self._expected_next_ai_speaker = None
                        self._pending_continuation_task = None
                        self._set_designated_speaker(None)
                        break
                    logger.info(
                        "[FloorManager] designated AI still pending at stream boundary; restarting continuation target=%s",
                        self._expected_next_ai_speaker,
                    )
                    continue

                if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
                    result = await self._recover_pending_human_turn_after_stream_end(
                        reason="team_stream_ended_during_human_turn",
                        log_message="[FloorManager] team stream ended during human turn, finalize pending human input if present",
                        request_stream_restart=False,
                    )
                    if result is not None:
                        yield result

                    recovered_human_turn_stream_end = True
                    saw_events_after_human_turn_recovery = False
                    logger.info(
                        "[FloorManager] human turn recovered; restarting team stream continuation"
                    )
                    continue

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
            skip_forced_goodbye = (
                recovered_human_turn_stream_end and not saw_events_after_human_turn_recovery
            )
            forced_goodbye_blocked = self._should_block_moderator_final_closing()
            if not self._moderator_has_spoken():
                forced_goodbye_blocked = False
            if (
                forced_goodbye_blocked
                and not had_error
                and not self._discussion_end_requested
                and not skip_forced_goodbye
            ):
                logger.info(
                    "[FloorManager] skip forced goodbye because human budget is still missing: human_turn_count=%s target=%s",
                    self._human_turn_count(),
                    self._HUMAN_TURN_MIN_TARGET,
                )
            if (
                not had_error
                and not self._discussion_end_requested
                and not skip_forced_goodbye
                and not forced_goodbye_blocked
                and "moderator" in self.ai_names
                and self.state not in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING)
            ):
                if self.state != FloorState.CLOSING:
                    await self._set_state(FloorState.CLOSING, reason="forced_final_closing")
                forced_goodbye = self._enforce_moderator_brevity(
                    self._ensure_moderator_explicit_goodbye("同学们，今天讨论就到这里。"),
                    is_final_closing=True,
                )
                if forced_goodbye:
                    if not is_non_substantive_turn(forced_goodbye):
                        self._speaker_message_count["moderator"] = (
                            self._speaker_message_count.get("moderator", 0) + 1
                        )
                        display_source = self._agent_to_display_name.get("moderator", "moderator")
                        self._recent_display_speakers.append(display_source)
                        if len(self._recent_display_speakers) > 16:
                            self._recent_display_speakers = self._recent_display_speakers[-16:]
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
            await self._cancel_pending_wait_task(
                self._active_stream_next_event_task,
                team_wait=True,
            )
            self._active_stream_next_event_task = None
            await self._drain_background_team_tasks()
            if self._watchdog_task:
                self._watchdog_task.cancel()
                try:
                    await self._watchdog_task
                except asyncio.CancelledError:
                    pass
                self._watchdog_task = None
            await self._force_stop_autogen_runtime(
                self.team,
                reason="floor manager shutdown",
            )
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
            speaker = self._normalize_agent_name(speaker)
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
            expected_ai = self._expected_next_ai_speaker
            if expected_ai and speaker in self.ai_names and speaker != expected_ai:
                logger.warning(
                    "[FloorManager] 点名后 selector 选择了非预期发言者，已拦截 speaker=%s expected=%s",
                    speaker,
                    expected_ai,
                )
                self._set_designated_speaker(expected_ai)
                return None
            if is_human:
                request_reason = self._resolve_human_input_request_reason("speaker_selected_human")
                if not self._is_authorized_human_request_reason(request_reason):
                    logger.warning(
                        "[FloorManager] 拦截未授权真人回合: speaker=%s state=%s pending_reason=%s",
                        speaker,
                        self.state,
                        self._pending_human_input_reason,
                    )
                    if speaker:
                        self._set_designated_speaker(None)
                    return None
            if speaker:
                self.current_speaker = speaker
                if not self._has_substantive_utterance(speaker):
                    self._set_speaker_utterance_status(
                        speaker, SpeakerUtteranceStatus.NOMINATED_ONLY
                    )

            await self._enter_selecting_speaker(
                reason="speaker_selection_received",
                recovery=True,
            )

            if is_human:
                await self._set_state(
                    FloorState.HUMAN_TURN_WAITING, reason="speaker_selected_human"
                )
            else:
                await self._set_state(FloorState.AI_SPEAKING, reason="speaker_selected_ai")
            if not is_human:
                self._last_human_input_requested_speaker = ""
                self._last_human_input_request_id = ""
            if speaker == "moderator":
                self._moderator_stream_sentence_emitted = 0

            await self._emit_turn_change(speaker, is_human)
            return {
                "event_type": "turn_change",
                "data": {"speaker": speaker, "is_human": is_human},
            }

        # 流式文本块
        if isinstance(event, ModelClientStreamingChunkEvent):
            raw_source = event.source if hasattr(event, "source") else self.current_speaker
            source = self._normalize_agent_name(raw_source)
            content = event.content if hasattr(event, "content") else str(event)

            if self._enforce_expected_ai_speaker(source, release_on_match=False):
                return None

            if self._should_suppress_ai_while_human_waiting(source):
                self._dropped_ai_stream_while_human_waiting += 1
                logger.warning(
                    "[FloorManager] 真人等待中丢弃错插 AI 流: source=%s content=%s",
                    source,
                    str(content)[:80],
                )
                return None

            if self._is_repeated_non_moderator_turn(source):
                self._log_repeated_non_moderator_drop(source, content, kind="流")
                self._set_designated_speaker("moderator")
                self._pop_streaming_message_tail(source, "")
                return None

            await self._sync_ai_turn_without_selection_event(
                source,
                reason="ai_stream_detected",
            )

            payload = {"source": source, "content": content}
            if source in self.ai_names:
                payload["content"] = self._strip_meta_reasoning_text(content)
                self._current_streaming_source = source
                raw_segments = self._consume_streaming_sentences(source, content)
                emitted_raw_segments: list[str] = []
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
                        continue
                    if source == "moderator":
                        segment = self._enforce_moderator_brevity(segment, is_final_closing=False)
                        if not segment:
                            continue
                        if (
                            self._moderator_stream_sentence_emitted
                            >= self._MODERATOR_INVITE_SENTENCE_LIMIT
                        ):
                            # 超出主持人流式限额的句子保留给最终消息，避免 TTS 漏播。
                            continue
                    else:
                        segment = self._enforce_non_human_ai_duration(source, segment)
                        if not segment:
                            continue
                    segment = await self.safety_filter.filter_or_rewrite(segment)
                    segment = self._strip_meta_reasoning_text(segment).strip()
                    if segment:
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
                        emitted_raw_segments.append(raw_segment)
                self._mark_streaming_segments_emitted(source, emitted_raw_segments)
                if tts_segments:
                    self._streaming_tts_emitted.add(source)
                    payload["tts_segments"] = tts_segments
                else:
                    return None

            return {
                "event_type": "stream",
                "data": payload,
            }

        # 完整文本消息
        if isinstance(event, TextMessage):
            raw_source = event.source
            source = self._normalize_agent_name(raw_source)
            raw_content = event.content
            content = raw_content

            # 过滤 AutoGen 内部任务注入消息（source="user" 是 AutoGen 框架内部产生的）
            if source == "user":
                return None

            if source in self.human_names:
                if self._discussion_end_requested or self.state in (
                    FloorState.CLOSING,
                    FloorState.ENDED,
                ):
                    logger.info(
                        "[FloorManager] ignoring late human message during closing: speaker=%s state=%s",
                        source,
                        self.state.value,
                    )
                    return None
                await self._prepare_pending_human_message_state(source)
                self._pending_submitted_human_inputs.pop(source, None)
                self._last_human_input_requested_speaker = ""
                self._last_human_input_request_id = ""
                await self._set_state(FloorState.HUMAN_SPEAKING, reason="human_message_received")

            if self._enforce_expected_ai_speaker(source):
                self._pop_streaming_message_tail(source, raw_content)
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
                self._log_repeated_non_moderator_drop(source, raw_content, kind="消息")
                self._set_designated_speaker("moderator")
                self._pop_streaming_message_tail(source, raw_content)
                return None

            await self._sync_ai_turn_without_selection_event(
                source,
                reason="ai_message_detected",
            )

            logger.info("[FloorManager] 完整消息: source=%s, content_len=%d", source, len(content))

            content = self._strip_meta_reasoning_text(content)
            if source in self.ai_names and not content:
                logger.info("[FloorManager] 丢弃纯提示词完整消息: source=%s", source)
                return None

            # 首轮发言兜底规整：避免不当引用
            content = self._sanitize_all_references(source, content)
            content = self._sanitize_human_honorifics(content)
            content = self._sanitize_non_moderator_role_confusion(source, content)
            content = self._strip_meta_reasoning_text(content)
            if source in self.ai_names and not content:
                logger.info("[FloorManager] 丢弃清洗后为空的完整消息: source=%s", source)
                if source == "moderator":
                    self._moderator_roleplay_target = None
                return None

            if source == "moderator":
                content = self._sanitize_moderator_role_confusion(content)
                content = self._ensure_moderator_explicit_goodbye(content)
            is_final_closing = source == "moderator" and self._is_moderator_final_closing(content)
            normalized_completed_content = re.sub(r"\s+", "", content or "")
            completed_key = (source, normalized_completed_content)
            now_mono = time.monotonic()
            for old_key, old_ts in list(self._recent_completed_message_ts.items()):
                if now_mono - old_ts > 30.0:
                    self._recent_completed_message_ts.pop(old_key, None)
            if (
                not is_final_closing
                and normalized_completed_content
                and (
                    (
                        self._last_completed_message_key == completed_key
                        and (now_mono - self._last_completed_message_ts) <= 30.0
                    )
                    or (
                        now_mono - self._recent_completed_message_ts.get(completed_key, -999.0)
                        <= 30.0
                    )
                )
            ):
                logger.info(
                    "[FloorManager] 丢弃重复完整消息: source=%s content=%s",
                    source,
                    content[:120],
                )
                self._pop_streaming_message_tail(source, raw_content)
                return None
            if is_final_closing and self._should_block_moderator_final_closing():
                self._closing_attempt_count += 1
                if self._closing_attempt_count >= self._CLOSING_ATTEMPT_LIMIT:
                    self._closing_attempt_limit_reached = True
                gate_after_attempt = self._closing_gate_status()
                if gate_after_attempt["forced_ready_due_to_attempt_limit"]:
                    logger.warning(
                        "[FloorManager] closing gate remained blocked after %s attempts; allowing abnormal close blockers=%s",
                        self._closing_attempt_count,
                        gate_after_attempt["blockers"],
                    )
                    self._human_participation_insufficient = True
                else:
                    logger.info(
                        "[FloorManager] blocked closing attempt %s/%s blockers=%s",
                        self._closing_attempt_count,
                        self._CLOSING_ATTEMPT_LIMIT,
                        gate_after_attempt["blockers"],
                    )
            if is_final_closing and self._should_block_moderator_final_closing():
                resume_agent = self._smart_fallback_speaker()
                resume_display = self._agent_to_display_name.get(resume_agent, resume_agent)
                if resume_agent in self.ai_names and resume_agent != "moderator":
                    logger.info(
                        "[FloorManager] 拦截主持人提前收尾，转为继续邀请未完成参与者: human_turn_count=%s target=%s",
                        self._human_turn_count(),
                        resume_agent,
                    )
                    content = self._build_targeted_handoff_text(resume_display)
                else:
                    target_agent = self._preferred_human_agent_name()
                    target_display = self._agent_to_display_name.get(target_agent, target_agent)
                    logger.info(
                        "[FloorManager] 拦截主持人提前收尾，转为继续邀请真人: human_turn_count=%s target=%s",
                        self._human_turn_count(),
                        target_agent,
                    )
                    content = self._build_first_human_handoff_text(
                        target_display_name=target_display,
                    )
                is_final_closing = False
            elif is_final_closing:
                content = self._build_grounded_moderator_final_closing(content)
                await self._set_state(FloorState.CLOSING, reason="moderator_final_closing")

            # Validate moderator opening quality on first substantive message
            if (
                source == "moderator"
                and not is_final_closing
                and self._is_discussion_opening_message(source)
                and not is_non_substantive_turn(content)
            ):
                if not self._validate_moderator_opening(content):
                    logger.warning("[FloorManager] 主持人开场不合格，已强制替换为话题简介")
                content = self._ensure_moderator_opening_context(content)
            designated_pre_filter: Optional[str] = None
            explicit_formal_human_invite = False
            forced_human_invite = False
            budget_forced_human_invite = False
            display_source = self._agent_to_display_name.get(source, source)
            all_display_names = list(self._display_name_to_agent.keys())
            if all_display_names and (source in self.ai_names or source in self.human_names):
                designated_pre_filter = parse_speaker_designation(content, all_display_names)
                if source == "moderator" and designated_pre_filter:
                    designated_agent = self._display_name_to_agent.get(
                        designated_pre_filter,
                        designated_pre_filter,
                    )
                    if not self._has_explicit_targeted_moderator_handoff(
                        content,
                        designated=designated_pre_filter,
                    ):
                        designated_pre_filter = None
                        designated_agent = ""
                    explicit_formal_human_invite = bool(
                        designated_agent in self.human_names
                        and self._has_explicit_moderator_invitation_phrase(
                            content,
                            designated=designated_pre_filter,
                        )
                    )

            if not is_final_closing:
                content, designated_pre_filter, forced_human_invite = (
                    self._force_first_human_invitation(
                        source,
                        content,
                        designated_pre_filter,
                    )
                )
                if self._should_force_budget_human_invitation(
                    source,
                    content,
                    designated_pre_filter,
                ):
                    target_agent = self._preferred_human_agent_name()
                    target_display = self._agent_to_display_name.get(target_agent, target_agent)
                    logger.info(
                        "[FloorManager] 老师短主持后自动补真人邀请: human_turn_count=%s target=%s",
                        self._human_turn_count(),
                        target_agent,
                    )
                    content = self._build_first_human_handoff_text(
                        content,
                        target_display_name=target_display,
                    )
                    designated_pre_filter = target_display
                    budget_forced_human_invite = True

            had_streamed_tts, remaining_tts_raw = self._pop_streaming_message_tail(
                source, raw_content
            )

            # 安全过滤 AI 输出
            if source in self.ai_names:
                content = await self.safety_filter.filter_or_rewrite(content)
                content = self._strip_meta_reasoning_text(content)
                if not content:
                    self._pop_streaming_message_tail(source, "")
                    logger.info("[FloorManager] 安全过滤后消息为空，跳过发送: source=%s", source)
                    return None

            if source == "moderator":
                content = self._enforce_moderator_brevity(
                    content,
                    is_final_closing=is_final_closing,
                )
                if not content:
                    self._pop_streaming_message_tail(source, "")
                    logger.info("[FloorManager] 主持人消息被长度约束后为空，跳过发送")
                    return None
            elif source in self.ai_names:
                content = self._enforce_non_human_ai_duration(source, content)
                if not content:
                    self._pop_streaming_message_tail(source, "")
                    logger.info(
                        "[FloorManager] AI 消息被40秒时长约束后为空，跳过发送: source=%s", source
                    )
                    return None

            if self._should_force_peer_budget_human_invitation(
                source,
                content,
                designated_pre_filter,
            ):
                target_agent = self._preferred_human_agent_name()
                target_display = self._agent_to_display_name.get(target_agent, target_agent)
                content = self._append_peer_human_handoff_text(content, target_display)
                designated_pre_filter = target_display
                logger.info(
                    "[FloorManager] 同伴接续后直接邀请真人: source=%s target=%s",
                    source,
                    target_agent,
                )

            immediate_human_request_speaker = ""
            immediate_human_request_reason = ""
            notify_human_request_speaker = ""
            notify_human_request_reason = ""
            boundary_handoff_target = ""

            def should_restart_for_boundary_handoff(target: str) -> bool:
                if not target or target == source:
                    return False
                # Let same-stream AI replies arrive naturally after a moderator designation.
                if source == "moderator" and target in self.ai_names:
                    return False
                # Deferred moderator-to-human invites should also wait for the current
                # stream boundary so residual AI output can flush before requesting input.
                if (
                    source == "moderator"
                    and target in self.human_names
                    and not immediate_human_request_speaker
                ):
                    return False
                return True

            # 如果是老师或用户的发言，检查是否指定了下一位发言者（需求4）
            designated: Optional[str] = designated_pre_filter
            if (
                not is_final_closing
                and all_display_names
                and (source in self.ai_names or source in self.human_names)
            ):
                designated = designated or parse_speaker_designation(content, all_display_names)
                agent_name = (
                    self._display_name_to_agent.get(designated, designated) if designated else ""
                )
                if (
                    source == "moderator"
                    and designated
                    and designated_pre_filter is None
                    and not self._has_explicit_targeted_moderator_handoff(
                        content,
                        designated=designated,
                    )
                ):
                    designated = None
                    agent_name = ""
                if not designated and source == "moderator":
                    thinker_followup_agent = self._choose_thinker_followup_agent(content)
                    if thinker_followup_agent:
                        designated = self._agent_to_display_name.get(
                            thinker_followup_agent,
                            thinker_followup_agent,
                        )
                        agent_name = thinker_followup_agent
                        content = self._build_targeted_handoff_text(designated)
                        logger.info(
                            "[FloorManager] 老师提到思想家，已补明确交接: %s",
                            thinker_followup_agent,
                        )
                if not designated and source == "moderator":
                    fallback_agent = self._generic_moderator_handoff_fallback(content)
                    if fallback_agent:
                        designated = self._agent_to_display_name.get(fallback_agent, fallback_agent)
                        agent_name = fallback_agent
                        logger.info(
                            "[FloorManager] 老师泛化交接语缺少明确点名，已补指定下一位: %s",
                            fallback_agent,
                        )
                if (
                    not designated
                    and source == "moderator"
                    and not self._recent_human_skip_pending
                    and self._should_force_first_human_invite()
                ):
                    target_agent = self._preferred_human_agent_name()
                    target_display = self._agent_to_display_name.get(target_agent, target_agent)
                    logger.info(
                        "[FloorManager] 老师短主持未显式点名，兜底补首轮真人邀请: target=%s",
                        target_agent,
                    )
                    content = self._build_first_human_handoff_text(
                        content,
                        target_display_name=target_display,
                    )
                    designated = target_display
                    agent_name = target_agent
                if designated:
                    if (
                        source == "moderator"
                        and agent_name in self.human_names
                        and self._should_delay_first_human_handoff()
                    ):
                        if not explicit_formal_human_invite:
                            logger.warning(
                                "[FloorManager] 首轮真人暖场不足，忽略主持人过早点名: target=%s non_human_turns=%s",
                                agent_name,
                                self._non_human_turn_count_before_first_human(),
                            )
                            designated = None
                            agent_name = ""
                            content = self._build_moderator_opening_baseline()
                    if designated:
                        logger.info(
                            "[FloorManager] %s 指定下一位发言者: %s (agent: %s)",
                            display_source,
                            designated,
                            agent_name,
                        )
                        # 规则 11：统计老师 vs 同学点名次数。
                        if source == "moderator":
                            self._moderator_nomination_count += 1
                        elif source in self.ai_names:
                            self._peer_nomination_count += 1
                    # 防止指定刚发过言的人（back-to-back），与现实讨论场景不符且会导致流程停滞
                    last_substantive = self._last_substantive_agent_speaker()
                    original_designated = designated
                    if designated and agent_name in {last_substantive, source}:
                        logger.warning(
                            "[FloorManager] 拦截back-to-back/self指定: %s 不能立即继续接话",
                            agent_name,
                        )
                        agent_name = self._smart_fallback_speaker()
                        if agent_name and agent_name not in {last_substantive, source}:
                            designated = self._agent_to_display_name.get(agent_name, agent_name)
                            logger.info(
                                "[FloorManager] back-to-back已重定向至: %s (display=%s)",
                                agent_name,
                                designated,
                            )
                        else:
                            # 无可选fallback，放弃指定让selector自行决定
                            designated = None
                            agent_name = ""
                    if (
                        source == "moderator"
                        and original_designated
                        and designated != original_designated
                    ):
                        if designated and agent_name in self.human_names:
                            content = self._build_first_human_handoff_text(
                                content,
                                target_display_name=designated,
                            )
                        elif designated:
                            content = self._build_targeted_handoff_text(designated)
                        else:
                            content = "我们先听听其他同学的想法。"
                    if designated and source == "moderator":
                        content = self._ensure_explicit_moderator_handoff_content(
                            content,
                            designated,
                            agent_name,
                            explicit_formal_human_invite=explicit_formal_human_invite,
                            forced_human_invite=forced_human_invite,
                            budget_forced_human_invite=budget_forced_human_invite,
                        )
                    if designated and source == "moderator" and agent_name in self.human_names:
                        self._set_designated_speaker(agent_name)
                        self._deferred_human_request_speaker = agent_name
                        self._deferred_human_request_reason = "moderator_designated_human"
                        self._pending_human_input_reason = self._deferred_human_request_reason
                        self._pending_moderator_human_invite_target = None
                        self._expected_next_ai_speaker = None
                        immediate_human_request_speaker = agent_name
                        immediate_human_request_reason = self._deferred_human_request_reason
                        if (
                            forced_human_invite
                            or (
                                budget_forced_human_invite
                                and self._speaker_message_count.get(agent_name, 0) > 0
                            )
                            or self._is_pure_moderator_human_invite(
                                raw_content,
                                designated=designated,
                            )
                        ):
                            immediate_human_request_speaker = agent_name
                            immediate_human_request_reason = self._deferred_human_request_reason
                        boundary_handoff_target = agent_name
                    elif source in self.ai_names and agent_name in self.human_names:
                        if self._should_allow_participant_human_handoff(agent_name):
                            self._set_designated_speaker(agent_name)
                            self._deferred_human_request_speaker = agent_name
                            self._deferred_human_request_reason = "participant_designated_human"
                            self._pending_human_input_reason = self._deferred_human_request_reason
                            immediate_human_request_speaker = agent_name
                            immediate_human_request_reason = self._deferred_human_request_reason
                            boundary_handoff_target = agent_name
                        else:
                            self._moderator_roleplay_target = self._agent_to_display_name.get(
                                agent_name,
                                designated,
                            )
                            self._set_designated_speaker("moderator")
                            self._pending_human_input_reason = "normal"
                            boundary_handoff_target = "moderator"
                            logger.info(
                                "[FloorManager] %s 点到真人 %s，先交还老师正式邀请",
                                display_source,
                                self._moderator_roleplay_target,
                            )
                        self._expected_next_ai_speaker = None
                    elif (
                        source == "moderator"
                        and agent_name in self.ai_names
                        and agent_name != "moderator"
                    ):
                        self._set_designated_speaker(agent_name)
                        self._expected_next_ai_speaker = agent_name
                        boundary_handoff_target = agent_name
                    else:
                        self._set_designated_speaker(agent_name)
                        boundary_handoff_target = agent_name
            elif is_final_closing:
                self._set_designated_speaker(None)
                self._expected_next_ai_speaker = None
                self._deferred_human_request_speaker = None
                self._deferred_human_request_reason = ""
                self._discussion_end_requested = True

            if (
                not is_final_closing
                and source in self.human_names
                and not designated
                and (content or "").strip() in {"（跳过）", "(跳过)", "跳过"}
            ):
                fallback_agent = self._smart_fallback_speaker()
                if fallback_agent and fallback_agent != source:
                    self._set_designated_speaker(fallback_agent)
                    boundary_handoff_target = fallback_agent
                    if fallback_agent in self.ai_names:
                        self._expected_next_ai_speaker = fallback_agent
                        self._pending_continuation_task = self._build_targeted_continuation_task(
                            fallback_agent
                        )
                    logger.info(
                        "[FloorManager] 真人手动跳过后，已指定确定性续讲者: %s",
                        fallback_agent,
                    )

            tts_text = ""
            if source in self.ai_names:
                if had_streamed_tts:
                    remaining_tts_raw = self._strip_meta_reasoning_text(remaining_tts_raw).strip()
                    if remaining_tts_raw:
                        tail = self._sanitize_all_references(source, remaining_tts_raw)
                        tail = self._sanitize_human_honorifics(tail)
                        tail = self._sanitize_non_moderator_role_confusion(source, tail)
                        tts_text = await self.safety_filter.filter_or_rewrite(tail)
                        tts_text = self._strip_meta_reasoning_text(tts_text)
                else:
                    tts_text = content
                if source == "moderator" and tts_text and tts_text not in content:
                    tts_text = content
                if source == "moderator" and is_final_closing:
                    tts_text = content

            message_event = {
                "event_type": "message",
                "data": {"source": source, "content": content, "tts_text": tts_text},
            }

            await self._emit_message(source, content, "text", tts_text=tts_text)
            self._last_completed_message_key = completed_key
            self._last_completed_message_ts = now_mono
            if normalized_completed_content:
                self._recent_completed_message_ts[completed_key] = now_mono
            if not is_non_substantive_turn(content):
                self._set_speaker_utterance_status(
                    source, SpeakerUtteranceStatus.SPOKE_WITH_CONTENT
                )
                if source != "moderator":
                    self._closing_attempt_count = 0
                if source not in self.human_names:
                    self._recent_human_skip_pending = False
                self._speaker_message_count[source] = self._speaker_message_count.get(source, 0) + 1
                self._recent_display_speakers.append(display_source)
                if len(self._recent_display_speakers) > 16:
                    self._recent_display_speakers = self._recent_display_speakers[-16:]
                # 规则 14: 老师占比；规则 7: 人后馈馈领。
                self._substantive_turn_count += 1
                if source == "moderator":
                    self._moderator_substantive_turn_count += 1
                if (
                    self._post_human_feedback_pending
                    and source not in self.human_names
                    and source != "系统"
                ):
                    if source == "moderator":
                        self._immediate_post_human_feedback_moderator += 1
                    else:
                        self._immediate_post_human_feedback_peer += 1
                    self._post_human_feedback_pending = False
            elif source in self.human_names and self._get_speaker_utterance_status(source) is None:
                self._set_speaker_utterance_status(source, SpeakerUtteranceStatus.SPOKE_EMPTY)
            await self._record_turn_summary(source, content)
            if immediate_human_request_speaker:
                human_request = await self._make_human_input_requested_event(
                    immediate_human_request_speaker,
                    reason=immediate_human_request_reason,
                    clear_designation=False,
                )
                if human_request is not None:
                    self._queue_run_event(message_event)
                    if should_restart_for_boundary_handoff(boundary_handoff_target):
                        self._request_stream_restart(
                            f"designated_speaker_handoff:{boundary_handoff_target}"
                        )
                    if (
                        source == "moderator"
                        and self._pending_human_guidance
                        and self.human_guidance_memory is not None
                    ):
                        await self.human_guidance_memory.clear()
                        self._pending_human_guidance = False

                    if source == "moderator":
                        self._moderator_roleplay_target = None
                        self._first_human_handoff_streamed = False
                        self._moderator_stream_sentence_emitted = 0
                    if source in self.human_names:
                        if not is_non_substantive_turn(content):
                            self._register_human_substantive_turn()
                        self._deferred_human_request_speaker = None
                        self._deferred_human_request_reason = ""
                        self._last_human_input_request_id = ""
                        self._human_completed_turn_count += 1
                        self._post_human_feedback_pending = True
                        self._designate_peer_followup_after_human_turn(content)
                        if not self._discussion_end_requested and self.state not in (
                            FloorState.CLOSING,
                            FloorState.ENDED,
                        ):
                            await self._set_state(
                                FloorState.SELECTING_SPEAKER, reason="human_message_complete"
                            )
                    return human_request
            elif notify_human_request_speaker:
                await self._notify_deferred_human_input_requested(
                    notify_human_request_speaker,
                    reason=notify_human_request_reason,
                )
            if should_restart_for_boundary_handoff(boundary_handoff_target):
                self._request_stream_restart(
                    f"designated_speaker_handoff:{boundary_handoff_target}"
                )
            if (
                source == "moderator"
                and self._pending_human_guidance
                and self.human_guidance_memory is not None
            ):
                await self.human_guidance_memory.clear()
                self._pending_human_guidance = False

            if source == "moderator":
                self._moderator_roleplay_target = None
                self._first_human_handoff_streamed = False
                self._moderator_stream_sentence_emitted = 0
            if source in self.human_names:
                if not is_non_substantive_turn(content):
                    self._register_human_substantive_turn()
                self._deferred_human_request_speaker = None
                self._deferred_human_request_reason = ""
                self._last_human_input_request_id = ""
                # 规则 7：记录人后馈馈领待补。
                self._human_completed_turn_count += 1
                self._post_human_feedback_pending = True
                self._designate_peer_followup_after_human_turn(content)
                if not self._discussion_end_requested and self.state not in (
                    FloorState.CLOSING,
                    FloorState.ENDED,
                ):
                    await self._set_state(
                        FloorState.SELECTING_SPEAKER, reason="human_message_complete"
                    )
            elif source in self.ai_names and self.state == FloorState.AI_SPEAKING:
                await self._enter_selecting_speaker(
                    reason="ai_message_complete",
                    recovery=True,
                )
            return message_event

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
        self._pending_human_input_reason = "normal"

        logger.info(
            "[FloorManager] 收到人类输入: name=%s, text_len=%d",
            normalized_name,
            len(normalized_text),
        )

        # 空输入直接按跳过处理，保证流程继续。
        if not normalized_text:
            logger.info("[FloorManager] 空输入，自动跳过: %s", normalized_name)
            if self.human_guidance_memory is not None:
                await self.human_guidance_memory.clear()
            self._pending_human_guidance = False
            self._register_human_skip(reason="empty_input", is_timeout=False)
            self._set_designated_speaker(None)
            self._expected_next_ai_speaker = None
            self._remember_submitted_human_input(normalized_name, "（跳过）")
            await self._put_human_input(normalized_name, "（跳过）")
            self._resume_team_after_human_input()
            if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
                self._request_stream_restart("submitted_human_input")
            await self._emit_message(
                "系统",
                f"{normalized_name or '该同学'}未输入有效内容，已自动跳过本轮。",
                "system",
            )
            self._set_speaker_utterance_status(normalized_name, SpeakerUtteranceStatus.SPOKE_EMPTY)
            return

        # 跳过指令不需要安全过滤
        if normalized_text in ("（跳过）", "(跳过)", "跳过"):
            logger.info("[FloorManager] 用户主动跳过: %s", normalized_name)
            if self.human_guidance_memory is not None:
                await self.human_guidance_memory.clear()
            self._pending_human_guidance = False
            self._register_human_skip(reason="manual_skip", is_timeout=False)
            self._set_designated_speaker(None)
            self._expected_next_ai_speaker = None
            self._remember_submitted_human_input(normalized_name, "（跳过）")
            await self._put_human_input(normalized_name, "（跳过）")
            self._resume_team_after_human_input()
            if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
                self._request_stream_restart("submitted_human_input")
            self._set_speaker_utterance_status(normalized_name, SpeakerUtteranceStatus.SKIPPED)
            return

        if not normalize_reference_match_text(normalized_text):
            logger.info(
                "[FloorManager] 输入仅包含空白/表情/标点，按空内容处理: %s", normalized_name
            )
            if self.human_guidance_memory is not None:
                await self.human_guidance_memory.clear()
            self._pending_human_guidance = False
            self._register_human_skip(reason="empty_like_input", is_timeout=False)
            self._set_designated_speaker(None)
            self._expected_next_ai_speaker = None
            self._remember_submitted_human_input(normalized_name, "（跳过）")
            await self._put_human_input(normalized_name, "（跳过）")
            self._resume_team_after_human_input()
            if self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
                self._request_stream_restart("submitted_human_input")
            self._set_speaker_utterance_status(normalized_name, SpeakerUtteranceStatus.SPOKE_EMPTY)
            await self._emit_message(
                "系统",
                f"{normalized_name or '该同学'}未输入有效内容，已自动跳过本轮。",
                "system",
            )
            return

        self._recent_human_skip_pending = False

        # 提前记录输入，使看门狗能在安全检查期间检测到待处理的输入，
        # 防止安全检查慢时系统陷入死锁。如安全检查未通过，后续再弹出。
        self._remember_submitted_human_input(normalized_name, normalized_text)

        # 安全过滤人类输入（内置超时，超时后默认放行）
        is_safe, reason = await self.safety_filter.check_human_input(normalized_text)
        if not is_safe:
            logger.warning(
                "[FloorManager] 人类输入被安全过滤: %s, reason=%s",
                normalized_name,
                reason,
            )
            # 安全检查拒绝时，撤回已记录的输入
            self._pending_submitted_human_inputs.pop(normalized_name, None)
            if self.human_guidance_memory is not None:
                await self.human_guidance_memory.clear()
            self._pending_human_guidance = False
            await self._emit_message(
                "系统",
                "你的发言包含不适当的内容，请换一种方式表达。",
                "system",
            )
            return

        if self._looks_like_explicit_human_end_request(normalized_text):
            logger.info(
                "[FloorManager] 真人明确提出结束讨论，直接进入老师收尾: %s",
                normalized_name,
            )
            await self.request_end_discussion(
                normalized_name,
                spoken_text=normalized_text,
                source="human_text",
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
        self._expected_next_ai_speaker = None

        # 检查用户是否指定了下一位发言者（需求4）
        all_participant_names = list(self.all_names)
        # 使用 display name map if available
        participant_labels = (
            list(self._display_name_to_agent.keys())
            if hasattr(self, "_display_name_to_agent")
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
                if hasattr(self, "_display_name_to_agent")
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
                if agent_name in self.ai_names and agent_name != "moderator":
                    self._expected_next_ai_speaker = agent_name

        try:
            # 注意：_remember_submitted_human_input 已在安全检查前调用，此处不再重复。
            await self._put_human_input(normalized_name, normalized_text)
            self._resume_team_after_human_input()
            if self._should_inline_process_submitted_human_input(normalized_name):
                # Let the owner loop consume the pending input at a safe boundary.
                self._request_stream_restart("submitted_human_input_owner_recovery")
            elif self.state in (FloorState.HUMAN_TURN_WAITING, FloorState.HUMAN_SPEAKING):
                self._request_stream_restart("submitted_human_input")
            logger.info("[FloorManager] 人类输入已提交到队列: %s", normalized_name)
        except Exception as e:
            logger.warning(
                "[FloorManager] 提交人类输入失败，自动跳过。name=%s, err=%s",
                normalized_name,
                e,
            )
            # 覆盖为跳过标记，并确保运行循环能继续
            self._remember_submitted_human_input(normalized_name, "（跳过）")
            await self._put_human_input(normalized_name, "（跳过）")
            self._resume_team_after_human_input()
            await self._emit_message(
                "系统",
                f"{normalized_name or '该同学'}输入处理异常，系统已自动跳过并继续讨论。",
                "system",
            )

    async def request_interrupt(self, speaker: str, request_id: str = "") -> None:
        """处理打断请求。

        当参与者请求打断当前发言者时调用。
        只有人类学生可以举手打断，AI 角色不能举手。
        记录打断者，切换到 INTERRUPTED 状态，
        通知主持人进行下一轮选择。

        Args:
            speaker: 请求打断的参与者名字。
        """
        # 检查打断者是否为人类学生
        speaker_agent_name = self._normalize_agent_name(speaker)
        display_speaker = self._agent_to_display_name.get(speaker_agent_name, speaker)
        human_display_names = {
            self._agent_to_display_name.get(name, name) for name in self.human_names
        }
        is_human = speaker_agent_name in self.human_names or speaker in human_display_names
        if not is_human:
            logger.info("[FloorManager] 非人类参与者 %s 尝试举手打断，忽略", speaker)
            return

        if self.current_speaker == speaker_agent_name and self.state in (
            FloorState.HUMAN_TURN_WAITING,
            FloorState.HUMAN_SPEAKING,
        ):
            # 举手者此刻已经持有发言权（例如刚被主持人点名）：不再重复授予发言权，
            # 也不另起一次 human_input_requested，以免产生重复发言。但仍需把这次举手
            # 记为“已受理的插话”，广播插话事件并把当前轮次归因为 interrupt，
            # 这样前端/历史记录能看到举手已被响应，而不是被静默吞掉。
            logger.info(
                "[FloorManager] %s 已持有发言权，将本次举手记为已受理的插话", display_speaker
            )
            self._human_hand_raise_count += 1
            self._pending_human_input_reason = "interrupt"
            if self._human_hand_raise_notifier is not None:
                self._human_hand_raise_notifier(speaker_agent_name)
            if self._on_interrupt:
                moderator_display = self._agent_to_display_name.get("moderator", "李老师")
                await self._on_interrupt(
                    display_speaker,
                    self.current_speaker or "",
                    moderator_display,
                    request_id,
                )
            return

        logger.info(f"打断请求: {display_speaker} 请求发言 (当前发言者: {self.current_speaker})")
        self._interrupt_queue.append(display_speaker)
        self._human_hand_raise_count += 1
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
            f"{self._format_display_vocative(display_speaker)}，请。",
            "interrupt",
        )

        # 通知打断事件
        if self._on_interrupt:
            await self._on_interrupt(
                display_speaker,
                self.current_speaker or "",
                moderator_display,
                request_id,
            )

        await self._enter_selecting_speaker(
            reason="interrupt_granted",
            recovery=True,
        )
        human_request = await self._make_human_input_requested_event(
            speaker_agent_name,
            reason="interrupt",
            clear_designation=True,
        )
        if human_request is not None:
            if self._on_human_input_requested:
                await self._on_human_input_requested(human_request["data"])
            return

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
