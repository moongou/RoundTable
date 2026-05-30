from __future__ import annotations

import asyncio
import contextlib
import re
import time
from types import SimpleNamespace

import pytest
from autogen_core import CancellationToken
from autogen_agentchat.messages import ModelClientStreamingChunkEvent
from autogen_agentchat.messages import SelectSpeakerEvent
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.messages import UserInputRequestedEvent

from app.agents.human_proxy import (
    clear_human_queues,
    create_human_proxy,
    get_human_queue,
    make_human_input_func,
    put_human_input,
)
from app.core.floor_manager import FloorManager
from app.core.floor_manager import FloorState
from app.core.floor_manager import SpeakerUtteranceStatus
from app.core.llm_errors import describe_model_error
from app.core.meeting_history import _build_script_line, _export_line_record
from app.core.rolling_summary_memory import HumanResponseGuidanceMemory, RollingSummaryMemory
from app.core.thinkers import thinker_label
from app.core.turn_scheduler import create_discussion_team
from app.core.turn_scheduler import get_designated_speaker
from app.core.turn_scheduler import is_generic_nomination
from app.core.turn_scheduler import parse_speaker_designation
from app.core.turn_scheduler import set_designated_speaker
from app.api.v1.websocket import _run_discussion


class _ModelContextStub:
    def __init__(self) -> None:
        self.messages = []

    async def add_message(self, message) -> None:
        self.messages.append(message)


class _TeamStub:
    def __init__(self) -> None:
        self.paused = False
        self.resumed = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.resumed = True


class _FailingTeamStub(_TeamStub):
    def __init__(self, exc: BaseException) -> None:
        super().__init__()
        self.exc = exc

    def run_stream(self, task):
        async def _stream():
            if False:
                yield None
            raise self.exc

        return _stream()


class _StreamingTeamStub(_TeamStub):
    def __init__(self, events) -> None:
        super().__init__()
        self._streams = [list(events)]

    def run_stream(self, *_, **__):
        events = self._streams.pop(0) if self._streams else []

        async def _stream():
            for event in events:
                yield event

        return _stream()


class _MultiStreamTeamStub(_TeamStub):
    def __init__(self, streams) -> None:
        super().__init__()
        self._streams = [list(stream) for stream in streams]
        self.run_calls = 0

    def run_stream(self, *_, **__):
        self.run_calls += 1
        events = self._streams.pop(0) if self._streams else []

        async def _stream():
            for event in events:
                yield event

        return _stream()


class _ClosableReentrantMultiStreamTeamStub(_TeamStub):
    def __init__(self, streams) -> None:
        super().__init__()
        self._streams = [list(stream) for stream in streams]
        self.run_calls = 0
        self._running = False

    def run_stream(self, *_, **__):
        if self._running:
            raise ValueError(
                "The team is already running, it cannot run again until it is stopped."
            )

        self.run_calls += 1
        events = self._streams.pop(0) if self._streams else []
        team = self

        async def _stream():
            team._running = True
            try:
                for event in events:
                    yield event
            finally:
                team._running = False

        return _stream()


class _StallingThenContinuingTeamStub(_TeamStub):
    def __init__(self, first_events, second_events) -> None:
        super().__init__()
        self._streams = [list(first_events), list(second_events)]
        self.run_calls = 0

    def run_stream(self, *_, **__):
        self.run_calls += 1
        current_index = self.run_calls - 1

        async def _stream():
            events = self._streams[current_index] if current_index < len(self._streams) else []
            for event in events:
                yield event
            if current_index == 0:
                await asyncio.Future()

        return _stream()


class _AsyncTeamControlStub(_TeamStub):
    async def pause(self) -> None:
        self.paused = True

    async def resume(self) -> None:
        self.resumed = True


class _SlowAsyncTeamControlStub(_TeamStub):
    async def pause(self) -> None:
        await asyncio.sleep(10)
        self.paused = True

    async def resume(self) -> None:
        await asyncio.sleep(10)
        self.resumed = True


class _SlowClosableStreamStub:
    def __init__(self) -> None:
        self.cancelled = False

    async def aclose(self) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.fixture(autouse=True)
def _reset_designated_next_speaker() -> None:
    set_designated_speaker(None)
    yield
    set_designated_speaker(None)


class _SafetyFilterStub:
    async def filter_or_rewrite(self, content: str) -> str:
        return content

    async def check_human_input(self, _content: str) -> tuple[bool, str]:
        return True, ""


class _DiscussionFloorManagerStub:
    def __init__(self, events) -> None:
        self._events = events
        self.submitted_inputs = []

    async def run(self, _topic):
        for event in self._events:
            yield event

    async def submit_human_input(self, name: str, text: str) -> None:
        self.submitted_inputs.append((name, text))


def test_floor_manager_general_stall_timeout_is_below_client_idle_window() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="empath"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager._PENDING_WAIT_CANCEL_TIMEOUT_SEC = 0.5

    assert 0 < floor_manager._general_stall_timeout_sec < 45.0


@pytest.mark.asyncio
async def test_floor_manager_cancel_pending_wait_task_times_out_on_stubborn_stream_wait() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager._PENDING_WAIT_CANCEL_TIMEOUT_SEC = 0.5

    release_cancel = asyncio.Event()

    async def _stubborn_wait() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release_cancel.wait()
            raise

    task = asyncio.create_task(_stubborn_wait())
    await asyncio.sleep(0)

    started_at = time.monotonic()
    await asyncio.wait_for(floor_manager._cancel_pending_wait_task(task), timeout=1.5)
    elapsed = time.monotonic() - started_at

    assert elapsed < 1.2

    release_cancel.set()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_floor_manager_marks_team_rebuild_when_stream_wait_cancel_times_out() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        team_factory=_TeamStub,
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager._PENDING_WAIT_CANCEL_TIMEOUT_SEC = 0.01

    release_cancel = asyncio.Event()

    async def _stubborn_wait() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release_cancel.wait()
            raise

    task = asyncio.create_task(_stubborn_wait())
    await asyncio.sleep(0)

    await floor_manager._cancel_pending_wait_task(task, team_wait=True)

    assert floor_manager._team_rebuild_requested is True
    release_cancel.set()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_request_end_discussion_auto_resumes_when_paused() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._pause_team_for_human_input()
    floor_manager.set_paused(True)

    await floor_manager.request_end_discussion("豆苗", source="button")

    assert floor_manager._paused is False
    assert floor_manager._blocked_for_human_input is False
    assert floor_manager._resume_gate.is_set()
    assert floor_manager.state == FloorState.CLOSING
    assert floor_manager._discussion_end_requested is True


@pytest.mark.asyncio
async def test_rolling_summary_memory_injects_recent_three_turns() -> None:
    memory = RollingSummaryMemory()
    await memory.replace_turn_summaries(
        [
            ("李老师", "先把问题背景说清楚"),
            ("小探", "我更想从好奇心出发看这件事"),
            ("孔子先生", "先立住做人的根，再谈方法"),
            ("豆苗", "我觉得规则也要留一点弹性"),
        ]
    )
    model_context = _ModelContextStub()

    await memory.update_context(model_context)

    assert len(model_context.messages) == 1
    content = model_context.messages[0].content
    assert "最近3轮发言摘要" in content
    assert "李老师" not in content
    assert "小探" in content
    assert "孔子先生" in content
    assert "豆苗" in content


@pytest.mark.asyncio
async def test_floor_manager_updates_recent_turn_memory_and_skips_jump_marker() -> None:
    memory = RollingSummaryMemory()
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
        summary_memory=memory,
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )

    await floor_manager._record_turn_summary("moderator", "同学们，我们先把问题看清楚。")
    await floor_manager._record_turn_summary("豆苗", "（跳过）")
    await floor_manager._record_turn_summary("explorer", "我觉得先试试看，再慢慢改。")

    results = (await memory.query("")).results
    assert len(results) == 2
    assert results[0].content["speaker"] == "李老师"
    assert results[1].content["speaker"] == "小探"


def test_describe_model_error_cloudflare_tunnel_is_actionable_without_traceback() -> None:
    info = describe_model_error(
        "InternalServerError: Error code: 530 - {'error': {'message': "
        "'The host is configured as a Cloudflare Tunnel, but Cloudflare is currently "
        "unable to reach it.', 'type': 'bad_response_status_code'}}\n"
        "Traceback:\n  File '/tmp/site-packages/openai/_base_client.py', line 1"
    )

    assert info.kind == "provider_unreachable"
    assert info.recoverable is True
    assert "Cloudflare Tunnel" in info.message
    assert "Base URL" in info.message
    assert "Traceback" not in info.technical_detail


def test_describe_model_error_token_and_context_limits_are_specific() -> None:
    quota = describe_model_error(
        "RateLimitError: insufficient_quota: You exceeded your current quota"
    )
    context = describe_model_error(
        "BadRequestError: context_length_exceeded: maximum context length is 8192 tokens"
    )

    assert quota.kind == "quota_or_tokens_exhausted"
    assert "额度不足" in quota.message or "Token 已用尽" in quota.message
    assert context.kind == "context_length_exceeded"
    assert "上下文长度不足" in context.message


@pytest.mark.asyncio
async def test_floor_manager_emits_friendly_model_error_without_traceback() -> None:
    floor_manager = FloorManager(
        team=_FailingTeamStub(
            RuntimeError(
                "InternalServerError: Error code: 530 - {'error': {'message': "
                "'The host is configured as a Cloudflare Tunnel, but Cloudflare is "
                "currently unable to reach it.', 'type': 'bad_response_status_code'}}\n"
                "Traceback:\n  File '/tmp/site-packages/openai/_base_client.py', line 1"
            )
        ),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    events = [event async for event in floor_manager.run("测试话题")]
    api_error = next(event for event in events if event["event_type"] == "api_error")

    assert api_error["data"]["kind"] == "provider_unreachable"
    assert "Cloudflare Tunnel" in api_error["data"]["message"]
    assert "Traceback" not in api_error["data"]["message"]
    assert "Traceback" not in api_error["data"].get("technical_detail", "")


@pytest.mark.asyncio
async def test_human_response_guidance_memory_injects_redirect_hint() -> None:
    memory = HumanResponseGuidanceMemory()
    await memory.replace_guidance(
        [
            {
                "speaker": "豆苗",
                "summary": "我昨晚吃了两块披萨，还想养小猫。",
                "assessment": "off_topic",
                "topic_focus": "在家上学",
                "suggested_peer_name": "小探",
                "suggested_peer_summary": "我更关心孩子会不会孤单。",
            }
        ]
    )
    model_context = _ModelContextStub()

    await memory.update_context(model_context)

    assert len(model_context.messages) == 1
    content = model_context.messages[0].content
    assert "真人学生回应提醒" in content
    assert "温和把话题拉回“在家上学”" in content
    assert "小探刚才提到的“我更关心孩子会不会孤单。”" in content


def test_floor_manager_pause_gate_calls_team_pause_and_resume() -> None:
    team = _TeamStub()
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )

    floor_manager.set_paused(True)
    assert team.paused is True
    assert floor_manager._paused is True
    assert floor_manager._resume_gate.is_set() is False

    floor_manager.set_paused(False)
    assert team.resumed is True
    assert floor_manager._paused is False
    assert floor_manager._resume_gate.is_set() is True


def test_floor_manager_human_input_gate_calls_team_pause_and_resume() -> None:
    team = _TeamStub()
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )

    floor_manager._pause_team_for_human_input()
    assert team.paused is True
    assert floor_manager._blocked_for_human_input is True
    assert floor_manager._resume_gate.is_set() is False

    floor_manager._resume_team_after_human_input()
    assert team.resumed is True
    assert floor_manager._blocked_for_human_input is False
    assert floor_manager._resume_gate.is_set() is True


@pytest.mark.asyncio
async def test_floor_manager_executes_async_team_pause_and_resume_controls() -> None:
    team = _AsyncTeamControlStub()
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )

    floor_manager.set_paused(True)
    await asyncio.sleep(0)
    assert team.paused is True

    floor_manager.set_paused(False)
    await asyncio.sleep(0)
    assert team.resumed is True


@pytest.mark.asyncio
async def test_floor_manager_drains_slow_team_control_tasks_without_callback_noise() -> None:
    team = _SlowAsyncTeamControlStub()
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    floor_manager._TEAM_CONTROL_DRAIN_TIMEOUT_SEC = 0.01
    floor_manager._TEAM_CONTROL_CANCEL_GRACE_SEC = 0.01

    floor_manager.set_paused(True)
    await asyncio.sleep(0)

    assert floor_manager._team_control_tasks
    await floor_manager._drain_background_team_tasks()
    await asyncio.sleep(0)
    assert not [task for task in floor_manager._team_control_tasks if not task.done()]


@pytest.mark.asyncio
async def test_floor_manager_designates_peer_followup_after_human_vocative_reply() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "skeptic": "小疑",
            "豆苗": "豆苗",
        }
    )
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._speaker_message_count["moderator"] = 1
    floor_manager._speaker_message_count["explorer"] = 1
    floor_manager._recent_display_speakers = ["老师", "小探"]

    await floor_manager._process_event(
        TextMessage(source="豆苗", content="老师，你说得对，我觉得标准答案有时太死板。")
    )

    assert floor_manager._expected_next_ai_speaker == "skeptic"
    assert floor_manager._pending_continuation_task


@pytest.mark.asyncio
async def test_floor_manager_keeps_explicit_teacher_question_from_peer_followup() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._speaker_message_count["moderator"] = 1
    floor_manager._speaker_message_count["explorer"] = 1
    floor_manager._recent_display_speakers = ["老师", "小探"]

    await floor_manager._process_event(TextMessage(source="豆苗", content="老师，你怎么看这个问题？"))

    assert floor_manager._expected_next_ai_speaker is None
    assert floor_manager._pending_continuation_task is None


@pytest.mark.asyncio
async def test_floor_manager_close_active_stream_cancels_slow_aclose_cleanly() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    floor_manager._TEAM_STREAM_CLOSE_TIMEOUT_SEC = 0.01
    floor_manager._TEAM_STREAM_CLOSE_CANCEL_GRACE_SEC = 0.01
    stream = _SlowClosableStreamStub()

    await floor_manager._close_active_team_stream(stream)

    assert stream.cancelled is True


def test_parse_speaker_designation_supports_ultra_short_invite() -> None:
    assert parse_speaker_designation("豆苗同学，请。", ["老师", "豆苗"]) == "豆苗"
    assert parse_speaker_designation("小探同学，请。", ["老师", "小探", "豆苗"]) == "小探"


def test_floor_manager_pending_human_input_request_snapshot_returns_waiting_request() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )

    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"
    floor_manager._pending_human_input_reason = "interrupt"
    floor_manager._human_input_request_seq = 7
    floor_manager._last_human_input_request_id = "hr-7"

    snapshot = floor_manager.pending_human_input_request_snapshot()

    assert snapshot == {
        "speaker": "豆苗",
        "reason": "interrupt",
        "request_id": "hr-7",
        "state": FloorState.HUMAN_TURN_WAITING.value,
    }


def test_floor_manager_pending_human_input_request_snapshot_returns_none_for_non_waiting_state() -> (
    None
):
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )

    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"

    snapshot = floor_manager.pending_human_input_request_snapshot()

    assert snapshot is None


def test_floor_manager_discussion_metrics_initial_state_is_zero() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    metrics = floor_manager.discussion_metrics()
    assert metrics["total_substantive_turns"] == 0
    assert metrics["moderator_turn_share"] == 0.0
    assert metrics["moderator_nomination_share"] == 0.0
    assert metrics["post_human_moderator_feedback_rate"] == 0.0
    assert metrics["warnings"] == []


def test_floor_manager_discussion_metrics_tracks_shares_and_warnings() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    # 模拟若干轮发言：老师占比超出 0.45 触发警告。
    floor_manager._substantive_turn_count = 10
    floor_manager._moderator_substantive_turn_count = 6
    # 点名：老师 1 次、同学 4 次；新策略允许同伴承担更多邀请。
    floor_manager._moderator_nomination_count = 1
    floor_manager._peer_nomination_count = 4
    # 真人发言后允许完全由同伴/思想家接续。
    floor_manager._human_completed_turn_count = 3
    floor_manager._immediate_post_human_feedback_moderator = 0
    floor_manager._immediate_post_human_feedback_peer = 1
    floor_manager._speaker_message_count.update({"moderator": 6, "explorer": 1, "豆苗": 3})

    metrics = floor_manager.discussion_metrics()
    assert metrics["moderator_turn_share"] == 0.6
    assert metrics["moderator_nomination_share"] == 0.2
    assert metrics["post_human_moderator_feedback_rate"] == 0.0
    warnings_text = "\n".join(metrics["warnings"])
    assert "moderator_turn_share" in warnings_text
    assert "moderator_share_over_35_percent" in warnings_text
    assert "moderator_nomination_share" not in warnings_text
    assert "post_human_moderator_feedback_rate" not in warnings_text


def test_floor_manager_diagnostics_includes_discussion_metrics() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    diag = floor_manager.diagnostics()
    assert "discussion_metrics" in diag
    assert diag["discussion_metrics"]["total_substantive_turns"] == 0


def test_floor_manager_non_human_ai_max_chars_supports_40_second_envelope() -> None:
    # 规则5：非真人单轮上限提升到约 40 秒，对应 ~200 中文字符。
    assert FloorManager._NON_HUMAN_AI_MAX_CHARS >= 180
    assert FloorManager._NON_HUMAN_AI_MAX_CHARS <= 240


def test_floor_manager_watchdog_fallback_prefers_non_human_after_recent_human_turn() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="测试用户999")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "peacemaker": "小和",
            "测试用户999": "测试用户999",
        }
    )
    floor_manager._speaker_message_count.update(
        {
            "moderator": 2,
            "peacemaker": 2,
            "测试用户999": 1,
        }
    )
    floor_manager._recent_display_speakers = ["小和", "老师", "测试用户999", "老师"]

    assert floor_manager._smart_fallback_speaker() == "peacemaker"


def test_floor_manager_allows_moderator_invitation_tail_while_human_waiting() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="测试用户999")],
        safety_filter=_SafetyFilterStub(),
    )

    floor_manager.state = FloorState.HUMAN_TURN_WAITING

    assert floor_manager._should_suppress_ai_while_human_waiting("moderator") is False
    assert floor_manager._should_suppress_ai_while_human_waiting("peacemaker") is True


def test_floor_manager_suppresses_residual_ai_when_human_floor_is_blocked() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="测试用户999")],
        safety_filter=_SafetyFilterStub(),
    )

    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager._blocked_for_human_input = True
    floor_manager.current_speaker = "测试用户999"
    floor_manager._last_human_input_requested_speaker = "测试用户999"

    assert floor_manager._should_suppress_ai_while_human_waiting("moderator") is False
    assert floor_manager._should_suppress_ai_while_human_waiting("peacemaker") is True


def test_floor_manager_sanitizes_moderator_student_role_confusion() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="测试用户999")],
        safety_filter=_SafetyFilterStub(),
    )

    sanitized = floor_manager._sanitize_moderator_role_confusion(
        "（高高举手）李老师，我来说说！我觉得可以先自己写十分钟，再请AI提示。"
    )

    assert "举手" not in sanitized
    assert "李老师，我" not in sanitized
    assert sanitized == "我觉得可以先自己写十分钟，再请AI提示。"

    sanitized = floor_manager._sanitize_moderator_role_confusion(
        "（歪着头思考）李老师，我觉得小探说得有道理，但我还想挑个刺儿。"
    )
    assert "李老师，我" not in sanitized
    assert sanitized == "我觉得小探说得有道理，但我还想挑个刺儿。"

    sanitized = floor_manager._sanitize_moderator_role_confusion(
        "（小疑兴奋地举起手来，声音清脆）老师，我还有一个发现！"
    )
    assert "小疑兴奋" not in sanitized
    assert "老师，我" not in sanitized
    assert sanitized == "我们再追问一个发现！"


def test_floor_manager_sanitizes_real_human_honorifics() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})

    sanitized = floor_manager._sanitize_human_honorifics("豆苗先生，你说得太好了！")

    assert sanitized == "豆苗同学，你说得太好了！"


def test_floor_manager_sanitizes_non_moderator_teacher_closing_role_confusion() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="empath")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "empath": "小爱", "豆苗": "豆苗"})

    sanitized = floor_manager._sanitize_non_moderator_role_confusion(
        "empath",
        "（站在教室门口）同学们，老师真的很开心。下课啦，下周见！",
    )

    assert "老师真的" not in sanitized
    assert "下课" not in sanitized
    assert "下周见" not in sanitized
    assert sanitized


@pytest.mark.asyncio
async def test_floor_manager_returns_to_selecting_after_ai_message_complete() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager.current_speaker = "explorer"

    result = await floor_manager._process_event(
        TextMessage(source="explorer", content="我觉得AI可以帮忙查资料，但不能代替思考。")
    )

    assert result["event_type"] == "message"
    assert floor_manager.state == FloorState.SELECTING_SPEAKER


def test_floor_manager_rewrites_named_quote_when_owner_is_different() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_reference_quotes = [
        ("豆苗", "如果规则是大家一起商量出来的，我会更愿意遵守。"),
        ("小探", "我觉得最有趣的部分是恐龙打架谁会赢，这要自己想象。"),
    ]

    sanitized = floor_manager._sanitize_grounded_quote_attribution(
        "刚才豆苗说到“恐龙打架”，让我想到要自己思考。"
    )

    assert "豆苗说到“恐龙打架”" not in sanitized
    assert "小探说到“恐龙打架”" in sanitized


def _grounding_floor_manager() -> FloorManager:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    return floor_manager


def test_grounding_history_finds_true_owner_without_reference_quotes() -> None:
    """接地校验可在完整发言历史中定位真实出处，即便近期引用缓存为空。"""
    floor_manager = _grounding_floor_manager()
    floor_manager._grounding_history = [
        ("小探", "我觉得最有趣的部分是恐龙打架谁会赢，这要自己想象。"),
        ("豆苗", "如果规则是大家一起商量出来的，我会更愿意遵守。"),
    ]
    # 近期引用缓存为空时，旧的评分逻辑无法定位归属。
    assert floor_manager._guess_reference_owner("恐龙打架") == "小探"


def test_grounding_rejects_fabricated_attribution_even_if_scorer_matches() -> None:
    """即使近期引用评分会命中某人，接地历史中查无此言则拒绝归属，避免张冠李戴。"""
    floor_manager = _grounding_floor_manager()
    # 评分逻辑会把该片段归给豆苗……
    floor_manager._recent_reference_quotes = [("豆苗", "多米诺骨牌效应特别明显")]
    # ……但完整发言历史里豆苗/小探都没真正说过它。
    floor_manager._grounding_history = [
        ("豆苗", "我更在乎规则是不是大家一起商量出来的。"),
        ("小探", "我喜欢自己想象恐龙打架的画面。"),
    ]
    assert floor_manager._guess_reference_owner("多米诺骨牌效应特别明显") is None


def test_grounding_dormant_when_history_empty_preserves_legacy_behavior() -> None:
    """无接地历史时保持旧有评分行为，确保既有用例不被影响。"""
    floor_manager = _grounding_floor_manager()
    floor_manager._recent_reference_quotes = [("豆苗", "多米诺骨牌效应特别明显")]
    floor_manager._grounding_history = []
    assert floor_manager._guess_reference_owner("多米诺骨牌效应特别明显") == "豆苗"


@pytest.mark.asyncio
async def test_floor_manager_recovers_pending_human_message_after_residual_ai_state() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="测试用户999")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "peacemaker": "小和",
            "测试用户999": "测试用户999",
        }
    )
    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager.current_speaker = "测试用户999"
    floor_manager._last_human_input_requested_speaker = "测试用户999"
    floor_manager._blocked_for_human_input = True

    result = await floor_manager._process_event(
        TextMessage(source="测试用户999", content="我觉得这个规则可以先试一周。")
    )

    assert result["event_type"] == "message"
    assert result["data"]["source"] == "测试用户999"
    assert floor_manager.state == FloorState.SELECTING_SPEAKER
    assert floor_manager._human_completed_turn_count == 1


def test_floor_manager_budget_human_invite_skips_recent_human_skip_cooldown() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="测试用户999")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "peacemaker": "小和",
            "测试用户999": "测试用户999",
        }
    )
    floor_manager._speaker_message_count.update(
        {
            "moderator": 2,
            "peacemaker": 2,
            "测试用户999": 0,
        }
    )
    floor_manager._recent_display_speakers = ["小和", "老师"]
    floor_manager._recent_human_skip_pending = True

    assert (
        floor_manager._should_force_budget_human_invitation(
            "moderator",
            "没关系，想到什么随时可以举手说哦。",
            None,
        )
        is False
    )


@pytest.mark.asyncio
async def test_floor_manager_generic_moderator_handoff_designates_non_human_followup() -> None:
    designated_calls: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="测试用户999")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=designated_calls.append,
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "peacemaker": "小和",
            "测试用户999": "测试用户999",
        }
    )
    floor_manager._speaker_message_count.update(
        {
            "moderator": 2,
            "peacemaker": 1,
            "测试用户999": 1,
        }
    )
    floor_manager._recent_display_speakers = ["小和", "老师", "测试用户999"]

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="请其他同学说说。")
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert result["data"]["content"] == "关于这个话题，小和同学，你有什么想法？"
    assert designated_calls[-1] == "peacemaker"
    assert floor_manager._expected_next_ai_speaker == "peacemaker"


@pytest.mark.asyncio
async def test_floor_manager_thinker_cue_prefers_thinker_followup_over_budget_human_invite() -> (
    None
):
    designated_calls: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
            SimpleNamespace(name="confucius"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=designated_calls.append,
        thinker_agent_names=["confucius"],
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "skeptic": "小疑",
            "confucius": "孔子先生",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update(
        {
            "moderator": 2,
            "explorer": 2,
            "skeptic": 2,
            "confucius": 1,
            "豆苗": 1,
        }
    )
    floor_manager._recent_display_speakers = ["豆苗", "小疑", "老师"]

    result = await floor_manager._process_event(
        TextMessage(
            source="moderator",
            content="（眼睛一亮，转头看向思想家）哎，小疑同学这个追问太棒了！",
        )
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert "孔子先生" in result["data"]["content"]
    assert designated_calls[-1] == "confucius"
    assert floor_manager._expected_next_ai_speaker == "confucius"


def test_sanitize_all_references_rewrites_first_turn_and_self_reference() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )

    first_turn = floor_manager._sanitize_all_references(
        "explorer",
        "上一位同学说得对，我也觉得要先试一试。",
    )
    assert "上一位同学" not in first_turn

    floor_manager._recent_display_speakers = ["豆苗"]
    self_reference = floor_manager._sanitize_all_references(
        "explorer",
        "小探说得对，我觉得应该继续。",
    )
    assert "小探说得对" not in self_reference

    explicit_self_reference = floor_manager._sanitize_all_references(
        "explorer",
        "我非常认可自己刚才讲过的观点。",
    )
    assert "自己刚才讲过的观点" not in explicit_self_reference
    assert "我非常认可自己" not in explicit_self_reference


def test_sanitize_all_references_rewrites_third_person_self_reference_variants() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "skeptic": "小疑",
            "豆苗": "豆苗",
        }
    )

    cleaned_possessive = floor_manager._sanitize_all_references(
        "skeptic",
        "嗯，小疑的这个想法这个方法真好！",
    )
    assert "小疑的这个" not in cleaned_possessive

    cleaned_quote = floor_manager._sanitize_all_references(
        "explorer",
        "小探提到“用自己的话讲一遍”的办法太聪明啦！",
    )
    assert "小探提到" not in cleaned_quote
    assert "这个点" in cleaned_quote


def test_sanitize_all_references_removes_unspoken_direct_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )

    floor_manager._recent_display_speakers = ["小探"]
    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "豆苗同学，你刚才说“像用铅笔画身高线”很有意思。小探同学，你怎么看？",
    )

    assert sanitized == "小探同学，你怎么看？"


def test_sanitize_all_references_rewrites_unspoken_past_attribution_clause() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )

    floor_manager._recent_display_speakers = ["李老师", "小探"]
    sanitized = floor_manager._sanitize_all_references(
        "explorer",
        "豆苗刚才讲到的这个角度，也提醒我们先别急着下结论。",
    )

    assert "豆苗" not in sanitized
    assert "有同学刚才讲到的这个角度" in sanitized


def test_sanitize_all_references_neutralizes_unspoken_short_attribution_verb() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="questioner"),
            SimpleNamespace(name="pragmatist"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "questioner": "小思",
            "pragmatist": "小行",
            "豆苗": "豆苗",
        }
    )

    floor_manager._recent_display_speakers = ["李老师", "小行"]
    floor_manager._speaker_message_count.update(
        {
            "moderator": 1,
            "pragmatist": 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "然后小思同学提了个特别重要的问题——安全和堵路。",
    )

    assert "小思同学提了" not in sanitized
    assert "刚才有同学提了个特别重要的问题" in sanitized


def test_sanitize_all_references_rewrites_unspoken_reminder_praise_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="questioner"),
            SimpleNamespace(name="pragmatist"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "questioner": "小思",
            "pragmatist": "小行",
            "豆苗": "豆苗",
        }
    )

    floor_manager._recent_display_speakers = ["李老师", "小思"]
    floor_manager._speaker_message_count.update(
        {
            "moderator": 1,
            "questioner": 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "小行同学提醒得也很到位，安全确实不能忽视。",
    )

    assert "小行同学提醒得" not in sanitized
    assert "小思同学，你提醒得也很到位" in sanitized


def test_sanitize_all_references_blocks_moderator_roleplaying_student() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="pragmatist"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "pragmatist": "小行",
            "豆苗": "豆苗",
        }
    )

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "（小行同学思考片刻，认真地说）老师，我觉得可以这样：让开店的人和摆摊的人一起开会商量！",
    )
    continuation = floor_manager._sanitize_all_references(
        "moderator",
        "比如超市前面200米不能摆摊，菜市场门口可以摆。",
    )

    assert sanitized == "请小行同学发言。"
    assert continuation == ""


def test_sanitize_all_references_rewrites_unspoken_named_challenge_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "skeptic": "小思",
            "豆苗": "豆苗",
        }
    )

    floor_manager._recent_display_speakers = ["李老师", "小探", "小思"]
    floor_manager._speaker_message_count.update(
        {
            "moderator": 1,
            "explorer": 1,
            "skeptic": 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "小思同学，豆苗同学对于“树木是否真的愿意被砍伐”提出了挑战，你怎么看这个观点呢？请小思同学发言。",
    )

    assert "豆苗" not in sanitized
    assert "小思同学，你刚才对于“树木是否真的愿意被砍伐”提出了挑战" in sanitized


def test_sanitize_all_references_drops_unspoken_name_from_joint_attribution() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "skeptic": "小思",
            "豆苗": "豆苗",
        }
    )

    floor_manager._recent_display_speakers = ["李老师", "小探", "小思"]
    floor_manager._speaker_message_count.update(
        {
            "moderator": 1,
            "explorer": 1,
            "skeptic": 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        "explorer",
        "（拍手）豆苗和小思说的太犀利了，这就像是问我“我不爱吃的胡萝卜，是不是宁愿烂在土里也不想变成我的午餐”一样！",
    )

    assert "豆苗" not in sanitized
    assert "小思说的太犀利了" in sanitized


def test_sanitize_all_references_rewrites_unspoken_named_idea_summary_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="dreamer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "dreamer": "小想",
            "豆苗": "豆苗",
        }
    )

    floor_manager._recent_display_speakers = ["李老师", "小想"]
    floor_manager._speaker_message_count.update(
        {
            "moderator": 1,
            "dreamer": 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "豆苗同学，你提出的“让树木成为教室的‘插班生’”这个想法太奇妙了！通过大家今天的讨论，再到豆苗同学提出的这种“森林学校”构想，老师看到了大家非常深刻的思考。",
    )

    assert "豆苗" not in sanitized
    assert "让树木成为教室的" not in sanitized
    assert "这个点很值得继续讨论" in sanitized
    assert "再到刚才小想提出的这种“森林学校”构想" in sanitized


def test_sanitize_all_references_rewrites_named_quote_to_actual_owner() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "skeptic": "小思",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_reference_quotes = [
        ("小探", "如果这种互相猜忌变成了一场丢沙包比赛，这场比赛真的只是为了比谁准吗？"),
        ("小思", "仅仅因为觉得对方有，就能作为动手的证据吗？"),
    ]
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})
    floor_manager._recent_display_speakers = ["李老师", "小思"]

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "豆苗同学，你刚才说“仅仅因为觉得对方有，就能作为动手的证据吗”这个问题，真的是一针见血。",
    )

    assert "豆苗同学" not in sanitized
    assert "小思同学，你刚才说" in sanitized
    assert "仅仅因为觉得对方有，就能作" in sanitized


def test_sanitize_all_references_collapses_duplicate_name_prefix_and_teacher_honorific() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="测试")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "测试": "测试",
        }
    )
    floor_manager._recent_display_speakers = ["测试", "老师"]

    sanitized = floor_manager._sanitize_reference_attribution(
        "moderator",
        "测试刚才测试有同学说得特别有深度，老师先生也想再追问一步。",
    )

    assert "测试刚才测试有同学" not in sanitized
    assert "刚才有同学说得特别有深度" in sanitized
    assert "老师先生" not in sanitized
    assert "老师也想再追问一步" in sanitized


def test_sanitize_all_references_rewrites_teacher_named_direct_quote_to_actual_owner() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="galileo"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="测试用户a")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "galileo": "伽利略",
            "skeptic": "小疑",
            "测试用户a": "测试用户a",
        }
    )
    floor_manager._recent_reference_quotes = [("伽利略", "推钟摆")]
    floor_manager._speaker_message_count.update(
        {
            "galileo": 1,
            "skeptic": 1,
            "测试用户a": 1,
        }
    )

    sanitized = floor_manager._sanitize_all_references(
        "skeptic",
        "我觉得老师先生讲“推钟摆”的故事更能说明问题。",
    )

    assert "老师先生" not in sanitized
    assert "伽利略" in sanitized
    assert "推钟摆" in sanitized


def test_sanitize_all_references_neutralizes_untraceable_named_concept_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"}
    )
    floor_manager._recent_reference_quotes = [("豆苗", "标准答案像一把尺子。")]
    floor_manager._speaker_message_count.update({"explorer": 1, "豆苗": 1})

    sanitized = floor_manager._sanitize_all_references(
        "explorer",
        "小探说有些标准答案是“保命用的”，我觉得这个提醒也有道理。",
    )

    assert "小探说" not in sanitized
    assert "有同学觉得有些标准答案是“保命用的”" in sanitized


def test_sanitize_all_references_rewrites_cross_sentence_quote_followups_to_real_speakers() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "skeptic": "小思",
            "pragmatist": "小行",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_reference_quotes = [
        ("豆苗", "国际政治就是谁的拳头大，谁说了算。"),
        ("小思", "规则到底是靠大家自觉，还是靠某种更厉害的力量在背后盯着才有效呢？"),
        ("小行", "谁违规就扣小红花或者限制课间活动，大家总得掂量掂量吧。"),
    ]
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})
    floor_manager._recent_display_speakers = ["豆苗", "小思", "小行"]

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "豆苗同学，你刚才说“规则到底是靠大家自觉，还是靠某种更厉害的力量在背后盯着”，这个问题问得太深刻了！你提出的“违规扣分”的想法，确实像给国际关系装上了一个“值日轮换表”和“惩罚机制”。",
    )

    assert "豆苗同学" not in sanitized
    assert "小思同学，你刚才说" in sanitized
    assert "规则到底是靠大家自觉，还是" in sanitized
    assert "小行提出的“违规扣分”" in sanitized


def test_sanitize_all_references_moderator_quote_whitelist_drops_untraceable_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_display_speakers = ["李老师", "小探"]
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})
    floor_manager._recent_reference_quotes = [
        ("小探", "演员要先会观察生活，再去尝试不同角色。"),
    ]

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "小探同学，你说的“情绪海绵”这个比喻很有意思。",
    )

    assert "情绪海绵" not in sanitized
    assert "这个点" in sanitized


def test_sanitize_all_references_drops_trailing_untraceable_moderator_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"}
    )
    floor_manager._recent_display_speakers = ["小探", "豆苗"]
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1, "豆苗": 1})
    floor_manager._recent_reference_quotes = [("豆苗", "大人也不是万能的，有时候他们也会搞错")]

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "“药可以治病，但不能乱吃药”——豆苗这个比喻太形象了！",
    )

    assert "药可以治病" not in sanitized
    assert "豆苗这个比喻" not in sanitized
    assert "这个点" in sanitized


def test_sanitize_all_references_drops_named_possessive_untraceable_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"}
    )
    floor_manager._recent_display_speakers = ["豆苗", "小探"]
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1, "豆苗": 1})
    floor_manager._recent_reference_quotes = [("小探", "爷爷养的那盆仙人掌")]

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "小探同学这个“仙人掌汁”的比喻太妙了！",
    )

    assert "仙人掌汁" not in sanitized
    assert "小探同学这个" not in sanitized
    assert "这个点" in sanitized


def test_sanitize_all_references_rewrites_unspoken_named_example_owner_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_display_speakers = ["李老师", "小探"]
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "（笑）豆苗同学举的这个例子太棒了！",
    )

    assert "豆苗同学举的这个例子" not in sanitized
    assert "小探同学，你举的这个例子太棒了" in sanitized


def test_sanitize_all_references_rewrites_unspoken_named_question_owner_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_display_speakers = ["李老师", "小探"]
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "（笑着举手示意）哎呀，豆苗同学问的问题太棒了！",
    )

    assert "豆苗同学问的问题" not in sanitized
    assert "小探同学，你问的问题太棒了" in sanitized


def test_sanitize_all_references_rewrites_unspoken_named_sentence_owner_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_display_speakers = ["李老师", "小探"]
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})
    floor_manager._recent_reference_quotes = [
        ("小探", "追问像是给自己的脑袋装了一个放大镜"),
    ]

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "（惊喜地点头）豆苗同学，你这句话说得太好了——“追问像是给自己的脑袋装了一个放大镜”！",
    )

    assert "豆苗同学，你这句话说得太好了" not in sanitized
    assert "小探同学，你这句话说得太好了" in sanitized


@pytest.mark.asyncio
async def test_floor_manager_interrupt_reason_takes_precedence_over_followup_moderator_designation() -> (
    None
):
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "豆苗": "豆苗",
        }
    )
    floor_manager._pending_human_input_reason = "interrupt"

    event = await floor_manager._make_human_input_requested_event(
        "豆苗",
        reason="moderator_designated_human",
        clear_designation=False,
    )

    assert event is not None
    assert event["event_type"] == "human_input_requested"
    assert event["data"]["reason"] == "interrupt"


def test_sanitize_all_references_moderator_quote_whitelist_keeps_traceable_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_display_speakers = ["李老师", "小探"]
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})
    floor_manager._recent_reference_quotes = [
        ("小探", "先观察再表演。"),
    ]

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "小探同学，你刚才说“先观察再表演”这个观点很扎实，也帮我们把今天的讨论重新拉回到方法上。",
    )

    assert "“先观察再表演”" in sanitized


def test_sanitize_all_references_strips_moderator_surface_noise() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_display_speakers = ["老师", "小探"]
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})
    floor_manager._recent_reference_quotes = [
        ("小探", "先观察再表演。"),
    ]

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "老师**：谢谢老师分享。有有有，你刚才说“先观察再表演”这个观点很扎实。",
    )

    assert sanitized.startswith("谢谢刚才的分享。")
    assert "老师**" not in sanitized
    assert "老师分享" not in sanitized
    assert "有有有" not in sanitized
    assert "“先观察再表演”" in sanitized


@pytest.mark.asyncio
async def test_sanitize_all_references_uses_nominated_only_status_for_unspoken_human() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._pending_human_input_reason = "interrupt"
    floor_manager.state = FloorState.SELECTING_SPEAKER

    event = await floor_manager._make_human_input_requested_event(
        "豆苗",
        reason="interrupt",
    )

    assert event is not None
    assert (
        floor_manager._get_speaker_utterance_status("豆苗") == SpeakerUtteranceStatus.NOMINATED_ONLY
    )

    sanitized = floor_manager._sanitize_all_references(
        "explorer",
        "豆苗同学刚才说“我想试试看”这个点很重要。",
    )

    assert "豆苗" not in sanitized


def test_sanitize_all_references_enforces_quote_length_and_quote_count_caps() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    sanitized = floor_manager._sanitize_all_references(
        "explorer",
        "我想把刚才的想法再整理成一条完整的行动线，先说问题，再说办法，再说为什么这样更稳妥，最后再回到我们现在能做的事。你提到“如果我们把所有想法都装进同一个书包里”，也说“第二句短”，又补了“第三句短”，最后提到“第四句该消失”。",
    )

    assert "“如果我们把所有想法都装进”" in sanitized
    assert "“第二句短”" in sanitized
    assert "“第三句短”" in sanitized
    assert "“第四句该消失”" not in sanitized
    assert sanitized.count("“") == 3
    assert "第四句该消失" in sanitized


def test_sanitize_all_references_enforces_total_quote_share_cap() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    sanitized = floor_manager._sanitize_all_references(
        "explorer",
        "“春天来了”“风也来了”“雨也来了”，我只想补一句。",
    )

    assert sanitized.count("“") == 1
    assert "春天来了" in sanitized
    assert "风也来了" in sanitized
    assert "雨也来了" in sanitized


def test_sanitize_all_references_rewrites_thinker_alias_and_untraceable_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "tagore": "罗宾德拉纳特·泰戈尔",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_display_speakers = ["李老师", "小探"]
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})
    floor_manager._recent_reference_quotes = [
        ("小探", "我更在意学习节奏是不是可持续。"),
    ]

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "泰戈尔先生说得太好了，尤其是“闪电麦昆”这个比喻。",
    )

    assert "泰戈尔先生说得太好了" not in sanitized
    assert "闪电麦昆" not in sanitized
    assert "请泰戈尔" in sanitized


def test_sanitize_all_references_removes_derived_story_quote_attribution() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="empath"),
            SimpleNamespace(name="socrates"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        thinker_agent_names=["socrates"],
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "empath": "小爱",
            "socrates": "苏格拉底",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_reference_quotes = [
        ("苏格拉底", "有人问朋友蚂蚁有几条腿，朋友说六条。"),
        ("小爱", "别人说六条腿就信了，可万一是五条呢？"),
    ]

    sanitized = floor_manager._sanitize_all_references(
        "explorer",
        "最让我忘不掉的是苏格拉底先生讲的那个“少了一条腿的蚂蚁”的故事。",
    )

    assert "“少了一条腿的蚂蚁”" not in sanitized
    assert "讲的那个" not in sanitized
    assert "这个故事" in sanitized


@pytest.mark.asyncio
async def test_skip_turn_is_not_tracked_as_spoken_reference() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "skeptic": "小疑",
            "豆苗": "豆苗",
        }
    )
    floor_manager.state = FloorState.SELECTING_SPEAKER

    await floor_manager._process_event(
        TextMessage(source="explorer", content="我觉得可以先看分数能量出什么。")
    )
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    await floor_manager._process_event(TextMessage(source="豆苗", content="（跳过）"))
    await floor_manager._process_event(
        TextMessage(source="skeptic", content="我更关心分数会不会受状态影响。")
    )

    assert floor_manager._recent_display_speakers == ["小探", "小疑"]
    assert (
        floor_manager._get_speaker_utterance_status("explorer")
        == SpeakerUtteranceStatus.SPOKE_WITH_CONTENT
    )
    assert (
        floor_manager._get_speaker_utterance_status("skeptic")
        == SpeakerUtteranceStatus.SPOKE_WITH_CONTENT
    )
    assert floor_manager._get_speaker_utterance_status("豆苗") == SpeakerUtteranceStatus.SPOKE_EMPTY

    sanitized = floor_manager._sanitize_all_references(
        "moderator",
        "刚才豆苗和小疑都分享了他们的想法。你对这个问题怎么看？",
    )

    assert "豆苗" not in sanitized

    attribution = floor_manager._sanitize_all_references(
        "moderator",
        "你觉得豆苗这个“站歪了”的比喻有道理吗？",
    )

    assert "豆苗这个" not in attribution


@pytest.mark.asyncio
async def test_human_proxy_queues_are_session_scoped_and_agent_alias_aware() -> None:
    clear_human_queues()
    agent_a = create_human_proxy("豆苗", session_scope="session-a")
    agent_b = create_human_proxy("豆苗", session_scope="session-b")

    queue_a = get_human_queue("豆苗", session_scope="session-a")
    queue_b = get_human_queue("豆苗", session_scope="session-b")
    assert queue_a is not queue_b

    await put_human_input(agent_a.name, "第一条", session_scope="session-a")
    await put_human_input(agent_b.name, "第二条", session_scope="session-b")

    assert await asyncio.wait_for(queue_a.get(), timeout=0.1) == "第一条"
    assert await asyncio.wait_for(queue_b.get(), timeout=0.1) == "第二条"

    clear_human_queues(session_scope="session-a")

    with pytest.raises(KeyError):
        get_human_queue("豆苗", session_scope="session-a")
    assert get_human_queue("豆苗", session_scope="session-b") is queue_b


@pytest.mark.asyncio
async def test_human_proxy_waits_for_real_input_instead_of_auto_observer_fallback() -> None:
    clear_human_queues()
    session_scope = "manual-human-turn"
    create_human_proxy("豆苗", session_scope=session_scope)
    input_func = make_human_input_func("豆苗", timeout=0.01, session_scope=session_scope)
    token = CancellationToken()

    task = asyncio.create_task(input_func("请发言", token))
    await asyncio.sleep(0.05)

    assert not task.done()

    await put_human_input("豆苗", "我想先从自己的经历说起。", session_scope=session_scope)

    assert await asyncio.wait_for(task, timeout=0.2) == "我想先从自己的经历说起。"


@pytest.mark.asyncio
async def test_watchdog_human_turn_only_reminds_and_does_not_enqueue_skip() -> None:
    clear_human_queues()
    session_scope = "watchdog-manual-skip"
    create_human_proxy("豆苗", session_scope=session_scope)
    queue = get_human_queue("豆苗", session_scope=session_scope)
    messages: list[tuple[str, str, str]] = []

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
        human_queue_scope=session_scope,
    )
    floor_manager.set_display_name_map({"moderator": "李老师", "豆苗": "豆苗"})

    async def _collect_message(source: str, content: str, msg_type: str) -> None:
        messages.append((source, content, msg_type))

    floor_manager.on_message(_collect_message)
    floor_manager.current_speaker = "豆苗"
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager._human_turn_started_mono = time.monotonic() - 10.0
    floor_manager._last_progress_ts = time.monotonic() - 10.0
    floor_manager._human_stall_timeout_sec = 0.01
    floor_manager._min_human_turn_window_sec = 0.0
    floor_manager._stall_check_interval_sec = 0.01

    task = asyncio.create_task(floor_manager._watchdog_loop())
    await asyncio.sleep(0.05)
    floor_manager._watchdog_stop.set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert queue.empty()
    assert any("系统不会替你跳过" in content for _, content, _ in messages)
    assert floor_manager._human_skip_count == 0
    assert floor_manager._human_timeout_count == 1
    assert floor_manager._get_speaker_utterance_status("豆苗") == SpeakerUtteranceStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_watchdog_selecting_speaker_dwell_timeout_designates_fallback_and_restarts() -> None:
    messages: list[tuple[str, str, str]] = []
    designated_calls: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="测试用户999")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=designated_calls.append,
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "peacemaker": "小和",
            "测试用户999": "测试用户999",
        }
    )
    floor_manager._speaker_message_count.update(
        {
            "moderator": 2,
            "peacemaker": 2,
            "测试用户999": 1,
        }
    )
    floor_manager._recent_display_speakers = ["小和", "老师", "测试用户999", "老师"]
    floor_manager.state = FloorState.SELECTING_SPEAKER
    floor_manager._state_entered_mono = time.monotonic() - 10.0
    floor_manager._state_dwell_timeout_sec[FloorState.SELECTING_SPEAKER] = 0.01
    floor_manager._stall_check_interval_sec = 0.01

    async def _collect_message(source: str, content: str, msg_type: str) -> None:
        messages.append((source, content, msg_type))

    floor_manager.on_message(_collect_message)

    task = asyncio.create_task(floor_manager._watchdog_loop())
    await asyncio.sleep(0.05)
    floor_manager._watchdog_stop.set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert floor_manager._stream_restart_requested is True
    assert designated_calls[-1] == "peacemaker"
    assert any("安排下一位发言超过" in content for _, content, _ in messages)


@pytest.mark.asyncio
async def test_selecting_speaker_dwell_timeout_routes_human_fallback_through_moderator() -> None:
    messages: list[tuple[str, str, str]] = []
    human_requests: list[dict[str, object]] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})
    floor_manager._recent_display_speakers = ["老师", "小探"]
    floor_manager.state = FloorState.SELECTING_SPEAKER
    floor_manager._state_entered_mono = time.monotonic() - 20.0
    floor_manager._state_dwell_timeout_sec[FloorState.SELECTING_SPEAKER] = 0.01
    floor_manager._last_watchdog_action_ts = time.monotonic() - 20.0

    async def _collect_message(source: str, content: str, msg_type: str) -> None:
        messages.append((source, content, msg_type))

    async def _collect_human_request(data: dict[str, object]) -> None:
        human_requests.append(dict(data))

    floor_manager.on_message(_collect_message)
    floor_manager.on_human_input_requested(_collect_human_request)

    handled = await floor_manager._handle_state_dwell_timeout(now=time.monotonic())

    assert handled is True
    assert floor_manager.state == FloorState.SELECTING_SPEAKER
    assert floor_manager._stream_restart_requested is True
    assert human_requests == []
    assert floor_manager._expected_next_ai_speaker == "moderator"
    assert floor_manager._pending_continuation_task is not None
    assert "不要直接请求真人学生开麦" in floor_manager._pending_continuation_task
    assert any("安排下一位发言超过" in content for _, content, _ in messages)


@pytest.mark.asyncio
async def test_watchdog_ai_speaking_dwell_timeout_returns_to_selecting() -> None:
    messages: list[tuple[str, str, str]] = []
    designated_calls: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=designated_calls.append,
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "豆苗", "小探"]
    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager.current_speaker = "explorer"
    floor_manager._state_entered_mono = time.monotonic() - 10.0
    floor_manager._state_dwell_timeout_sec[FloorState.AI_SPEAKING] = 0.01
    floor_manager._stall_check_interval_sec = 0.01

    async def _collect_message(source: str, content: str, msg_type: str) -> None:
        messages.append((source, content, msg_type))

    floor_manager.on_message(_collect_message)

    task = asyncio.create_task(floor_manager._watchdog_loop())
    await asyncio.sleep(0.05)
    floor_manager._watchdog_stop.set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert floor_manager.state == FloorState.SELECTING_SPEAKER
    assert floor_manager._stream_restart_requested is True
    assert designated_calls
    assert any("持续超过" in content for _, content, _ in messages)


def test_floor_manager_stream_sentence_splitter_keeps_quotes_and_tail() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )

    segments, remainder = floor_manager._drain_complete_stream_sentences("“先想一想。”然后再回答")

    assert segments == ["“先想一想。”"]
    assert remainder == "然后再回答"


@pytest.mark.asyncio
async def test_floor_manager_streaming_tts_only_leaves_final_tail_for_message() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    stream_event = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="explorer",
            content="先看规则。再想",
        )
    )
    message_event = await floor_manager._process_event(
        TextMessage(source="explorer", content="先看规则。再想一想。")
    )

    assert stream_event is not None
    assert stream_event["event_type"] == "stream"
    assert stream_event["data"]["tts_segments"] == ["先看规则。"]

    assert message_event is not None
    assert message_event["event_type"] == "message"
    assert message_event["data"]["content"] == "先看规则。再想一想。"
    assert message_event["data"]["tts_text"] == "再想一想。"


@pytest.mark.asyncio
async def test_floor_manager_message_callback_receives_streaming_tail_tts() -> None:
    captured: list[tuple[str, str, str, str]] = []

    async def _collect_message(
        source: str,
        content: str,
        msg_type: str,
        tts_text: str,
    ) -> None:
        captured.append((source, content, msg_type, tts_text))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.on_message(_collect_message)

    await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="explorer",
            content="先看规则。再想",
        )
    )
    await floor_manager._process_event(
        TextMessage(source="explorer", content="先看规则。再想一想。")
    )

    assert captured[-1] == (
        "explorer",
        "先看规则。再想一想。",
        "text",
        "再想一想。",
    )


@pytest.mark.asyncio
async def test_floor_manager_drops_meta_reasoning_stream_segments() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    stream_event = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="moderator",
            content="<think>用户现在需要我扮演老师，先复述再点评。</think>。",
        )
    )
    message_event = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，我们先一起梳理一下这个问题。")
    )

    assert stream_event is None
    assert message_event is not None
    assert message_event["event_type"] == "message"
    assert "先说清楚自己的理由和分歧" in message_event["data"]["content"]
    assert "用户现在需要我扮演老师" not in message_event["data"]["content"]
    assert message_event["data"]["tts_text"] == message_event["data"]["content"]


@pytest.mark.asyncio
async def test_floor_manager_meta_stream_does_not_break_clean_stream_tail() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    meta_event = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="explorer",
            content="不对，按照之前的设定，我应该先复述用户输入。",
        )
    )
    clean_stream_event = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="explorer",
            content="先看规则。再想",
        )
    )
    message_event = await floor_manager._process_event(
        TextMessage(source="explorer", content="先看规则。再想一想。")
    )

    assert meta_event is None
    assert clean_stream_event is not None
    assert clean_stream_event["event_type"] == "stream"
    assert clean_stream_event["data"]["tts_segments"] == ["先看规则。"]
    assert message_event is not None
    assert message_event["data"]["tts_text"] == "再想一想。"


def test_floor_manager_streaming_tail_drops_full_repeat_when_prefix_not_exact_match() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    floor_manager._streaming_emitted_raw_prefix["explorer"] = "先看规则。再想一想。"
    floor_manager._streaming_buffer["explorer"] = ""

    had_streamed, tail = floor_manager._pop_streaming_message_tail(
        "explorer",
        "先看规则！再想一想。",
    )

    assert had_streamed is True
    assert tail == ""


@pytest.mark.asyncio
async def test_floor_manager_drops_duplicate_stream_sentences_for_same_turn() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    first_stream = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="explorer",
            content="先看规则。",
        )
    )
    duplicate_stream = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="explorer",
            content="先看规则。再想一",
        )
    )
    message_event = await floor_manager._process_event(
        TextMessage(source="explorer", content="先看规则。再想一想。")
    )

    assert first_stream is not None
    assert first_stream["data"]["tts_segments"] == ["先看规则。"]
    assert duplicate_stream is None
    assert message_event is not None
    assert message_event["data"]["tts_text"] == "再想一想。"


def test_floor_manager_strips_meta_reasoning_from_text_message_content() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )

    cleaned = floor_manager._strip_meta_reasoning_text(
        "嗯，大家好，我是李老师。\n"
        "- **绝不**提前引用、评价或编造任何未发生发言的内容\n"
        "🌟 **等待系统触发**：请静候，让小探同学的声音第一个响起！\n"
        "（/me 等待系统触发“请小疑同学发言”提示，绝不提前编造）"
    )

    assert cleaned == "嗯，大家好，我是李老师。"


def test_floor_manager_strips_english_meta_reasoning_from_stream_text() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="comedian")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    cleaned = floor_manager._strip_meta_reasoning_text(
        "I should provide an answer in the style of 可乐 for the first time!\n"
        "我觉得标准答案像鞋带的第一个结。"
    )

    assert cleaned == "我觉得标准答案像鞋带的第一个结。"


def test_floor_manager_rewrites_moderator_teacher_student_misattribution() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="optimist")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "optimist": "小明",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})
    floor_manager._speaker_message_count["optimist"] = 1
    floor_manager._recent_display_speakers = ["小明"]
    floor_manager._recent_reference_quotes = [
        ("小明", "创新不是故意离开答案很远，而是在站稳以后，勇敢多迈半步"),
    ]

    cleaned = floor_manager._sanitize_all_references(
        "moderator",
        "老师同学，你刚才说的“创新不是故意离开答案很远，而是在站稳以后，勇敢多迈半步”我特别喜欢。",
    )

    assert "老师同学" not in cleaned
    assert cleaned.startswith("小明同学，你刚才说的")


def test_floor_manager_neutralizes_repeated_self_invitation() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="optimist")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "optimist": "小明",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})
    floor_manager._speaker_message_count["optimist"] = 1
    floor_manager._recent_display_speakers = ["小明"]

    cleaned = floor_manager._sanitize_all_references(
        "moderator",
        "小明同学，你刚才说“小鸭船”很有趣；小明同学，你怎么看小明同学说的“小鸭船”和“只描线”？",
    )

    assert "小明同学，你怎么看小明同学" not in cleaned
    assert "请其他同学说说" in cleaned
    assert parse_speaker_designation(cleaned, ["老师", "小明", "豆苗"]) is None


def test_floor_manager_removes_unspoken_name_from_object_reference() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="optimist")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "optimist": "小明",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})
    floor_manager._speaker_message_count["optimist"] = 1
    floor_manager._recent_display_speakers = ["小明"]

    cleaned = floor_manager._sanitize_all_references(
        "moderator",
        "请小明同学说说，你怎么看豆苗同学这只“风筝”呢？",
    )

    assert "豆苗同学这只" not in cleaned
    assert "请小明同学说说" not in cleaned
    assert "这只“风筝”" in cleaned


def test_floor_manager_rewrites_plain_repeat_invitation_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="empath")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "empath": "小爱",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})
    floor_manager._speaker_message_count["empath"] = 1
    floor_manager._recent_display_speakers = ["小爱"]

    cleaned = floor_manager._sanitize_all_references(
        "moderator",
        "请小爱同学发言。",
    )

    assert cleaned == "请其他同学说说。"


def test_floor_manager_rewrites_unspoken_possessive_quote_from_session_six() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "skeptic": "小疑",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})
    floor_manager._recent_display_speakers = ["老师", "小疑"]

    cleaned = floor_manager._sanitize_all_references(
        "moderator",
        "好，那小疑同学，豆苗同学的“15分钟限量版”玩法，你怎么看？",
    )

    assert "豆苗同学的“15分钟限量版”玩法" not in cleaned
    assert "小疑同学，你刚才提出的“15分钟限量版”玩法" in cleaned


def test_meeting_history_script_keeps_agent_debug_fields() -> None:
    message_line = _build_script_line(
        "outbound",
        "message",
        {
            "source": "老师",
            "agent_source": "moderator",
            "content": "豆苗同学，你来说说。",
            "msg_type": "text",
        },
        timestamp="2026-04-28T00:00:00Z",
        event_seq=7,
    )
    assert message_line is not None
    assert message_line["agent_source"] == "moderator"

    request_line = _build_script_line(
        "outbound",
        "human_input_requested",
        {
            "speaker": "豆苗",
            "agent_speaker": "u8c46u82d7",
            "reason": "moderator_designated_human",
            "request_id": "req-1",
            "state": "human_turn_waiting",
        },
        timestamp="2026-04-28T00:00:01Z",
        event_seq=8,
    )
    assert request_line is not None
    assert request_line["speaker"] == "豆苗"
    assert request_line["agent_speaker"] == "u8c46u82d7"
    assert request_line["request_reason"] == "moderator_designated_human"
    assert request_line["request_id"] == "req-1"
    assert request_line["request_state"] == "human_turn_waiting"

    human_input_line = _build_script_line(
        "inbound",
        "human_input",
        {
            "speaker": "豆苗",
            "content": "我来补充一个例子。",
            "request_wait_ms": 932,
            "request_id": "req-1",
        },
        timestamp="2026-04-28T00:00:02Z",
        event_seq=None,
    )
    assert human_input_line is not None
    assert human_input_line["request_wait_ms"] == 932
    assert human_input_line["request_id"] == "req-1"

    client_metric_line = _build_script_line(
        "inbound",
        "client_metric",
        {
            "speaker": "豆苗",
            "name": "tts_first_audio_delay_ms",
            "value_ms": 184,
            "phase": "teacher_feedback",
            "detail": "teacher_first_audio",
            "event_seq": 12,
        },
        timestamp="2026-04-28T00:00:03Z",
        event_seq=None,
    )
    assert client_metric_line is not None
    assert client_metric_line["metric_name"] == "tts_first_audio_delay_ms"
    assert client_metric_line["metric_value_ms"] == 184
    assert client_metric_line["linked_event_seq"] == 12

    end_discussion_line = _build_script_line(
        "inbound",
        "end_discussion",
        {
            "speaker": "豆苗",
            "reason": "button",
        },
        timestamp="2026-04-28T00:00:04Z",
        event_seq=None,
    )
    assert end_discussion_line is not None
    assert end_discussion_line["kind"] == "note"
    assert end_discussion_line["speaker"] == "豆苗"
    assert "请求结束本次讨论" in end_discussion_line["text"]

    exported = _export_line_record(request_line, index=1)
    assert exported["agent_speaker"] == "u8c46u82d7"
    assert exported["request_reason"] == "moderator_designated_human"

    exported_metric = _export_line_record(client_metric_line, index=2)
    assert exported_metric["metric_name"] == "tts_first_audio_delay_ms"
    assert exported_metric["metric_value_ms"] == 184


def test_floor_manager_rewrites_teacher_self_quote_to_real_owner_from_session_seven() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="comedian"),
            SimpleNamespace(name="empath"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "comedian": "可乐",
            "empath": "小爱",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update({"moderator": 2, "comedian": 2, "empath": 1})
    floor_manager._recent_display_speakers = ["可乐", "小爱", "可乐"]
    floor_manager._recent_reference_quotes = [
        (
            "小爱",
            "老人家参与教育最特别的地方，就是他们总能把硬邦邦的知识裹上一层糖衣。这两种爱，其实一个都不能少呢！",
        )
    ]

    cleaned = floor_manager._sanitize_all_references(
        "moderator",
        "哇，可乐同学，你这段话说得太精彩了！老师刚才说“把硬邦邦的知识裹上一层糖衣”，这个比喻我特别喜欢——还有你提到“两种爱一个都不能少”，这个总结特别有温度！",
    )

    assert "老师刚才说" not in cleaned
    assert "还有你提到" not in cleaned
    assert "小爱刚才说“把硬邦邦的知识裹上一层糖衣”" in cleaned
    assert "小爱提到“两种爱一个都不能少”" in cleaned


@pytest.mark.asyncio
async def test_floor_manager_immediately_requests_human_input_when_moderator_invites_human() -> (
    None
):
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "skeptic": "小疑",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})

    result = await floor_manager._process_event(
        TextMessage(
            source="moderator", content="好，我们先请豆苗同学来说说看，豆苗同学，你是什么想法？"
        )
    )

    assert result is not None
    assert result["event_type"] == "human_input_requested"
    assert result["data"]["speaker"] == "豆苗"
    assert result["data"]["reason"] == "moderator_designated_human"
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING
    assert floor_manager.current_speaker == "豆苗"
    assert floor_manager._deferred_human_request_speaker is None
    assert floor_manager._deferred_human_request_reason == ""
    assert designated_updates == ["豆苗"]

    inserted_ai = await floor_manager._process_event(
        TextMessage(source="skeptic", content="那我先替豆苗说一个想法。")
    )

    assert inserted_ai is None


@pytest.mark.asyncio
async def test_floor_manager_suppresses_ai_inserted_while_human_is_waiting() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"

    stream_result = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source="skeptic", content="我先替豆苗说一句。")
    )
    message_result = await floor_manager._process_event(
        TextMessage(source="skeptic", content="我先替豆苗说一句。")
    )

    assert stream_result is None
    assert message_result is None


@pytest.mark.asyncio
async def test_floor_manager_drops_repeated_non_moderator_self_reply() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="tagore")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda _name: None,
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "tagore": "罗宾德拉纳特·泰戈尔",
            "豆苗": "豆苗",
        }
    )
    floor_manager._recent_display_speakers = ["罗宾德拉纳特·泰戈尔"]
    floor_manager._speaker_message_count["tagore"] = 1

    stream_result = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source="tagore", content="哇，泰戈尔爷爷说得真好！")
    )
    message_result = await floor_manager._process_event(
        TextMessage(source="tagore", content="哇，泰戈尔爷爷说得真好！")
    )

    assert stream_result is None
    assert message_result is None


@pytest.mark.asyncio
async def test_floor_manager_routes_participant_question_to_thinker_before_human_budget() -> None:
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="comedian"),
            SimpleNamespace(name="van_gogh"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
        thinker_agent_names=["van_gogh"],
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "comedian": "可乐",
            "van_gogh": "文森特·梵高",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "comedian": 0, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "豆苗"]

    result = await floor_manager._process_event(
        TextMessage(
            source="comedian",
            content="老师老师！我有个问题想问梵高先生！（转向梵高）您画画的时候，星星真的会冲您眨眼睛吗？",
        )
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert designated_updates[-1] == "van_gogh"
    assert floor_manager._deferred_human_request_speaker is None
    assert floor_manager._pending_human_input_reason == "normal"


@pytest.mark.asyncio
async def test_floor_manager_sanitizes_unspoken_thinker_reference_in_stream() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="van_gogh")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        thinker_agent_names=["van_gogh"],
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "van_gogh": "文森特·梵高", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "豆苗"]

    result = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="moderator",
            content="（专注地听完梵高先生的话，频频点头）谢谢豆苗同学带我们回到星光灿烂的夜晚！",
        )
    )

    assert result is not None
    assert result["event_type"] == "stream"
    tts_text = "".join(result["data"].get("tts_segments", []))
    assert "梵高" not in tts_text
    assert "听完豆苗同学刚才的发言" in tts_text


@pytest.mark.asyncio
async def test_floor_manager_strips_system_trigger_prefix_from_stream_segments() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )

    result = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="explorer",
            content="[系统触发：请小探同学发言] （指着窗台的蚂蚁）我悄悄观察过蚂蚁搬家搬家。",
        )
    )

    assert result is not None
    assert "[系统触发：请小探同学发言]" not in result["data"]["content"]
    assert result["data"]["tts_segments"] == ["（指着窗台的蚂蚁）我悄悄观察过蚂蚁搬家搬家。"]


@pytest.mark.asyncio
async def test_floor_manager_caps_non_human_ai_turn_to_roughly_forty_seconds() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    long_content = (
        "第一句我想先讲一个很长很长的生活例子，说明这个问题为什么值得讨论。"
        "第二句我再补充一个观察，让大家看到另一种可能性。"
        "第三句我把自己的理由说清楚。"
        "第四句我还想继续展开更多细节。"
        "第五句这些细节其实已经超过一轮发言该有的长度。"
    )

    result = await floor_manager._process_event(
        TextMessage(source="explorer", content=long_content)
    )

    assert result is not None
    assert result["event_type"] == "message"
    content = result["data"]["content"]
    assert len(content) <= floor_manager._NON_HUMAN_AI_MAX_CHARS + 1
    assert "第四句" not in content


def test_turn_scheduler_prefers_ai_after_moderator_opening() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=4,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "豆苗": "豆苗",
        },
    )

    selector = team._selector_func
    assert selector is not None

    opening_without_designation = [
        SimpleNamespace(source="moderator", content="同学们，我们先把问题想清楚。"),
    ]
    opening_with_ai_designation = [
        SimpleNamespace(source="moderator", content="小探，你先说说你的想法。"),
    ]

    assert selector(opening_without_designation) == "explorer"
    assert selector(opening_with_ai_designation) == "explorer"


@pytest.mark.asyncio
async def test_floor_manager_forces_first_human_invitation_after_first_warmup_turn() -> None:
    emitted_messages: list[tuple[str, str, str]] = []

    async def _on_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager.on_message(_on_message)
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})
    floor_manager._recent_display_speakers = ["老师", "小探"]

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="这个角度很有意思，我们继续往下听。")
    )

    assert result is not None
    assert result["event_type"] == "human_input_requested"
    assert result["data"]["speaker"] == "豆苗"
    assert result["data"]["reason"] == "moderator_designated_human"
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING
    assert any(content == "豆苗同学，你怎么看？" for _source, content, _type in emitted_messages)


@pytest.mark.asyncio
async def test_floor_manager_live_like_moderator_praise_still_triggers_first_human_invite() -> None:
    human_requests: list[dict[str, object]] = []

    async def _on_human_input_requested(data: dict[str, object]) -> None:
        human_requests.append(data)

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="storyteller"),
            SimpleNamespace(name="innovator"),
        ],
        human_agents=[SimpleNamespace(name="u5c0fu8881")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "storyteller": "小说",
            "innovator": "小想",
            "u5c0fu8881": "小袁",
        }
    )
    floor_manager.on_human_input_requested(_on_human_input_requested)
    floor_manager._speaker_message_count.update({"moderator": 1, "storyteller": 1})
    floor_manager._recent_display_speakers = ["老师", "小说"]

    result = await floor_manager._process_event(
        TextMessage(
            source="moderator",
            content="哎呀，这个小故事讲得太生动了！“分了他半个”——这个小小的举动背后可藏着一个大问题呢！",
        )
    )

    assert result is not None
    assert result["event_type"] == "human_input_requested"
    assert result["data"]["speaker"] == "u5c0fu8881"
    assert result["data"]["reason"] == "moderator_designated_human"
    assert human_requests == []


@pytest.mark.asyncio
async def test_floor_manager_rewrites_short_opening_into_topic_context() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager._current_topic = "树木有生命权吗\n为了建学校，要不要砍掉校园旁边的一片老树林？"

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们好！我是李老师。")
    )

    assert result is not None
    assert result["event_type"] == "message"
    content = result["data"]["content"]
    assert "今天我们来聊聊" in content
    assert "老树林" in content
    assert "先说清楚自己的理由" in content
    assert len([part for part in re.split(r"[。！？!?]+", content) if part.strip()]) <= 2


@pytest.mark.asyncio
async def test_floor_manager_blocks_opening_human_handoff_before_warmup() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._current_topic = "树木有生命权吗\n为了建学校，要不要砍掉校园旁边的一片老树林？"

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="豆苗，你怎么看？")
    )

    assert result is not None
    assert result["event_type"] == "message"
    content = result["data"]["content"]
    assert "豆苗" not in content
    assert "今天我们来聊聊" in content
    assert floor_manager._pending_human_input_reason == "normal"
    assert floor_manager._last_human_input_requested_speaker == ""


@pytest.mark.asyncio
async def test_floor_manager_enforces_opening_context_even_with_ai_nomination() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._current_topic = "你会被AI取代吗\nChatGPT能写作业、编程序、画插图。你的未来职业还安全吗？"

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="关于这个话题，小探同学，你有什么想法？")
    )

    assert result is not None
    assert result["event_type"] == "message"
    content = result["data"]["content"]
    assert "今天我们来聊聊" in content
    assert ("背景是：" in content) or ("来自生活里常见的真实讨论" in content)


@pytest.mark.asyncio
async def test_floor_manager_does_not_request_human_turn_for_name_mention_without_invite() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update(
        {
            "moderator": 2,
            "explorer": 2,
            "豆苗": 5,
        }
    )
    floor_manager._recent_display_speakers = ["豆苗", "小探", "老师"]

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="哎呀，豆苗同学想得可真周到！")
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert floor_manager._last_human_input_requested_speaker == ""


@pytest.mark.asyncio
async def test_floor_manager_requires_actual_expected_ai_message_before_releasing_designation() -> (
    None
):
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager._expected_next_ai_speaker = "peacemaker"
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})

    turn_change = await floor_manager._process_event(
        SelectSpeakerEvent(source="system", content=["peacemaker"])
    )
    blocked = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source="moderator", content="我继续补充一句。")
    )
    allowed = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source="peacemaker", content="轮到我了。")
    )

    assert turn_change is not None
    assert turn_change["event_type"] == "turn_change"
    assert blocked is None
    assert designated_updates == []
    assert allowed is not None
    assert allowed["event_type"] == "stream"
    assert floor_manager._expected_next_ai_speaker == "peacemaker"
    completed = await floor_manager._process_event(
        TextMessage(source="peacemaker", content="我完整说完了。")
    )
    assert completed is not None
    assert completed["event_type"] == "message"
    assert floor_manager._expected_next_ai_speaker is None


@pytest.mark.asyncio
async def test_floor_manager_blocks_wrong_ai_selection_before_expected_ai_speaks() -> None:
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="skeptic"),
            SimpleNamespace(name="empath"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager._expected_next_ai_speaker = "skeptic"

    blocked = await floor_manager._process_event(
        SelectSpeakerEvent(source="system", content=["empath"])
    )

    assert blocked is None
    assert designated_updates == ["skeptic"]
    assert floor_manager._expected_next_ai_speaker == "skeptic"
    assert floor_manager.current_speaker is None


@pytest.mark.asyncio
async def test_floor_manager_drops_old_speaker_residue_before_expected_ai_selected() -> None:
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="socrates")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.current_speaker = "moderator"
    floor_manager._expected_next_ai_speaker = "socrates"

    blocked = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source="moderator", content="我再补充一句。")
    )

    assert blocked is None
    assert designated_updates == []
    assert floor_manager._expected_next_ai_speaker == "socrates"


@pytest.mark.asyncio
async def test_floor_manager_drops_moderator_residue_before_expected_ai_selected_without_current_speaker() -> (
    None
):
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager._expected_next_ai_speaker = "skeptic"

    blocked = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source="moderator", content="我再补充一句。")
    )

    assert blocked is None
    assert designated_updates == []
    assert floor_manager._expected_next_ai_speaker == "skeptic"


@pytest.mark.asyncio
async def test_floor_manager_syncs_ai_speaker_from_stream_without_select_event() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "skeptic": "小疑",
            "豆苗": "豆苗",
        }
    )
    floor_manager.current_speaker = "豆苗"
    floor_manager.state = FloorState.SELECTING_SPEAKER

    streamed = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source="小疑同学", content="我补充一下。")
    )

    assert streamed is not None
    assert streamed["event_type"] == "stream"
    assert floor_manager.current_speaker == "skeptic"
    assert floor_manager.state == FloorState.AI_SPEAKING


@pytest.mark.asyncio
async def test_floor_manager_interrupt_uses_synced_ai_speaker_from_display_name_stream() -> None:
    interrupt_current_speakers: list[str] = []

    async def _capture_interrupt(
        _interrupter: str, current_speaker: str, _approved_by: str, _request_id: str
    ) -> None:
        interrupt_current_speakers.append(current_speaker)

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="socrates")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "socrates": "苏格拉底",
            "豆苗": "豆苗",
        }
    )
    floor_manager.current_speaker = "moderator"
    floor_manager.state = FloorState.MODERATOR_OPENING
    floor_manager.on_interrupt(_capture_interrupt)

    streamed = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source="苏格拉底先生", content="我来问个问题。")
    )
    await floor_manager.request_interrupt("豆苗", request_id="int-1")

    assert streamed is not None
    assert floor_manager.current_speaker == "豆苗"
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING
    assert interrupt_current_speakers == ["socrates"]


@pytest.mark.asyncio
async def test_floor_manager_interrupt_emits_human_request_immediately() -> None:
    human_requests: list[dict] = []

    async def _capture_request(data: dict) -> None:
        human_requests.append(dict(data))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.on_human_input_requested(_capture_request)
    floor_manager.current_speaker = "explorer"
    floor_manager.state = FloorState.AI_SPEAKING

    await floor_manager.request_interrupt("豆苗", request_id="int-1")

    assert human_requests == [
        {
            "speaker": "豆苗",
            "reason": "interrupt",
            "request_id": "hr-1",
            "state": FloorState.HUMAN_TURN_WAITING.value,
        }
    ]
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING


def test_turn_scheduler_keeps_human_locked_after_warmup_without_explicit_invite() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("comedian"), _agent("skeptic"), _agent("peacemaker")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            "老师": "moderator",
            "可乐": "comedian",
            "小疑": "skeptic",
            "小和": "peacemaker",
            "豆苗": "豆苗",
        },
    )

    selector = team._selector_func
    assert selector is not None

    thread = [
        SimpleNamespace(source="moderator", content="我们先听听大家的想法。"),
        SimpleNamespace(source="skeptic", content="我觉得要看会不会堵路。"),
        SimpleNamespace(source="comedian", content="我喜欢路边摊，但也怕太乱。"),
        SimpleNamespace(source="moderator", content="大家说得不错，我们继续听另一位同学。"),
    ]

    assert selector(thread) == "peacemaker"


def test_turn_scheduler_keeps_human_locked_late_without_explicit_reinvite() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=24,
        display_name_to_agent={
            "老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
    )

    selector = team._selector_func
    assert selector is not None

    thread = [
        SimpleNamespace(source="moderator", content="我们先把背景理一理。"),
        SimpleNamespace(source="explorer", content="我先想到的是好奇心会不会被标准答案压住。"),
        SimpleNamespace(source="豆苗", content="我觉得有时候是因为怕答错。"),
        SimpleNamespace(
            source="moderator", content="你把害怕答错这个感觉说出来了，我们继续往下听。"
        ),
        SimpleNamespace(source="skeptic", content="可如果完全没有标准，也可能让人更不敢开口。"),
    ]

    assert selector(thread) != "豆苗"


def test_parse_speaker_designation_supports_natural_follow_up_questions() -> None:
    participants = ["李老师", "小探", "豆苗"]

    assert parse_speaker_designation("豆苗你有没有过这种小失误啊？", participants) == "豆苗"
    assert parse_speaker_designation("我也想请小探再补充一下。", participants) == "小探"


def test_generic_nomination_patterns_distinguish_generic_and_explicit_calls() -> None:
    assert is_generic_nomination("大家怎么看？") is True
    assert is_generic_nomination("其他同学呢") is True
    assert is_generic_nomination("还有谁想说？") is True
    assert is_generic_nomination("请张三同学发言。") is False
    assert is_generic_nomination("张三，你怎么看？") is False


def test_set_designated_speaker_latest_assignment_wins() -> None:
    set_designated_speaker("豆苗")
    set_designated_speaker("小探")

    assert get_designated_speaker() == "小探"
    assert get_designated_speaker() is None


def test_parse_speaker_designation_supports_unspoken_student_followup() -> None:
    participants = ["老师", "可乐", "小疑", "小和", "马克斯·韦伯", "豆苗"]

    assert (
        parse_speaker_designation(
            "那最后还有小和同学没发言呢。你自己觉得，大城市该不该在路边摆摊呀？说说你的看法吧。",
            participants,
        )
        == "小和"
    )


def test_parse_speaker_designation_tolerates_suffix_words() -> None:
    """名字后跟"同学"等后缀词时仍能正确解析点名对象。"""
    participants = ["李老师", "小探", "小思", "小和", "豆苗"]

    assert (
        parse_speaker_designation("小思同学，你怎么看豆苗同学的这个想法呢？", participants)
        == "小思"
    )
    assert parse_speaker_designation("那么，小思同学，你觉得呢？", participants) == "小思"
    assert parse_speaker_designation("小和同学，你认为这个方案可行吗？", participants) == "小和"
    assert parse_speaker_designation("豆苗同学你有没有类似的经历？", participants) == "豆苗"
    assert (
        parse_speaker_designation(
            "豆苗同学，你先来开个头吧，你觉得“追求完美”是好事还是坏事呢？", participants
        )
        == "豆苗"
    )


def test_parse_speaker_designation_prefers_invited_name_over_context_mention() -> None:
    participants = ["老师", "小和", "小思", "可乐", "豆苗"]

    assert (
        parse_speaker_designation(
            "好，那我想问问小思同学，听到小和这么说，你是更支持摆摊呢，还是觉得不该让路边摆摊？",
            participants,
        )
        == "小思"
    )


def test_parse_speaker_designation_prefers_target_before_dash_context_mentions() -> None:
    participants = ["老师", "可乐", "小探", "小疑", "小说", "孔子", "豆苗"]

    assert (
        parse_speaker_designation(
            '不过我想问问咱们的真人同学豆苗——可乐说他用冰淇淋当动力，你觉得这和"心里装着更重要的事"是一回事吗？豆苗同学，你怎么看？',
            participants,
        )
        == "豆苗"
    )
    assert (
        parse_speaker_designation(
            '那我想问问小疑同学——你听了小探和可乐的故事，你觉得勇气有没有"大小"之分呀？',
            participants,
        )
        == "小疑"
    )


def test_parse_speaker_designation_strips_markdown_emphasis() -> None:
    participants = ["老师", "小和", "小思", "可乐", "豆苗"]

    assert (
        parse_speaker_designation(
            "好，咱们也听听其他同学的想法。**可乐同学**，你才一年级，你平时看到路边摆摊的小车车是什么感觉呀？",
            participants,
        )
        == "可乐"
    )


def test_parse_speaker_designation_supports_session_five_direct_invites() -> None:
    participants = ["老师", "可乐", "小探", "小疑", "小爱", "小理", "海伦·凯勒", "豆苗"]

    assert (
        parse_speaker_designation("哎，可乐小朋友，你平时最喜欢做手工了对不对？", participants)
        == "可乐"
    )
    assert (
        parse_speaker_designation("来，小理同学，你平时特别喜欢算数学题，对吧？", participants)
        == "小理"
    )
    assert parse_speaker_designation("那海伦·凯勒先生，该您发言啦！", participants) == "海伦·凯勒"


def test_parse_speaker_designation_supports_thinker_alias_titles() -> None:
    participants = ["李老师", "小探", "豆苗", "阿尔弗雷德·阿德勒"]

    assert (
        parse_speaker_designation(
            "那我正式邀请一下阿德勒先生，您先从心理学角度说说看。",
            participants,
        )
        == "阿尔弗雷德·阿德勒"
    )


def test_parse_speaker_designation_supports_direct_question_to_thinker() -> None:
    participants = ["老师", "可乐", "文森特·梵高", "豆苗"]

    assert (
        parse_speaker_designation(
            "老师老师！我有个问题想问梵高先生！（转向梵高）您画画的时候，星星真的会冲您眨眼睛吗？",
            participants,
        )
        == "文森特·梵高"
    )


def test_turn_scheduler_prefers_moderator_when_entering_closing_window() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们从规则和自由开始聊。"),
        SimpleNamespace(source="explorer", content="我觉得规则像扶手。"),
        SimpleNamespace(source="豆苗", content="有时候扶手也会挡住路。"),
        SimpleNamespace(source="moderator", content="你刚才说扶手也会挡住路，这个观察很妙。"),
        SimpleNamespace(source="skeptic", content="那要看扶手是不是太多了。"),
        SimpleNamespace(source="explorer", content="也许可以让扶手更灵活一点。"),
    ]

    assert selector(thread) == "moderator"


def test_turn_scheduler_delays_second_closing_before_human_budget() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们从规则和自由开始聊。"),
        SimpleNamespace(source="explorer", content="我觉得规则像扶手。"),
        SimpleNamespace(source="豆苗", content="有时候扶手也会挡住路。"),
        SimpleNamespace(source="moderator", content="你刚才说扶手也会挡住路，这个观察很妙。"),
        SimpleNamespace(source="skeptic", content="那要看扶手是不是太多了。"),
        SimpleNamespace(
            source="moderator",
            content="收尾前，我想先问问大家，还有没有想补充的观点或想法？豆苗，如果你还有新发现，也可以继续说。",
        ),
        SimpleNamespace(source="豆苗", content="我觉得扶手最好能跟着人一起变。"),
        SimpleNamespace(
            source="moderator", content="你刚才说扶手最好能跟着人一起变，这个想法特别有创造力。"
        ),
        SimpleNamespace(source="explorer", content="那就像会移动的桥。"),
    ]

    assert selector(thread) == "skeptic"


def test_turn_scheduler_honors_moderator_non_human_invite_before_human_budget() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("comedian"), _agent("empath"), _agent("rationalist")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            "老师": "moderator",
            "可乐": "comedian",
            "小爱": "empath",
            "小理": "rationalist",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天聊艺术教育。"),
        SimpleNamespace(source="empath", content="我觉得艺术像阳光。"),
        SimpleNamespace(
            source="moderator",
            content="小爱同学说得真好。哎，可乐小朋友，你平时最喜欢做手工了对不对？",
        ),
    ]

    assert selector(thread) == "comedian"


def test_turn_scheduler_honors_moderator_invite_after_human_spoke() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("rationalist")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            "老师": "moderator",
            "小探": "explorer",
            "小理": "rationalist",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天聊艺术教育。"),
        SimpleNamespace(source="explorer", content="我觉得艺术像游乐场。"),
        SimpleNamespace(source="豆苗", content="我赞同艺术像阳光。"),
        SimpleNamespace(
            source="moderator",
            content="嗯，看来豆苗同学还在思考呢，没关系。来，小理同学，你平时特别喜欢算数学题，对吧？",
        ),
    ]

    assert selector(thread) == "rationalist"


@pytest.mark.asyncio
async def test_floor_manager_stops_after_moderator_goodbye_message() -> None:
    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(
                    source="moderator",
                    content="同学们再见！今天聊得真开心，下次咱们再一起讨论好玩的话题。",
                ),
                UserInputRequestedEvent(request_id="should-not-fire", source="豆苗"),
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager._speaker_message_count["豆苗"] = 5

    events = [event async for event in floor_manager.run("测试话题")]

    assert [event["event_type"] for event in events] == ["message", "ended"]
    assert events[0]["data"]["source"] == "moderator"


@pytest.mark.asyncio
async def test_floor_manager_appends_explicit_goodbye_for_moderator_closing() -> None:
    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source="moderator", content="今天就到这里啦，同学们下次见。"),
                UserInputRequestedEvent(request_id="should-not-fire", source="豆苗"),
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager._speaker_message_count["豆苗"] = 5

    events = [event async for event in floor_manager.run("测试话题")]

    assert [event["event_type"] for event in events] == ["message", "ended"]
    assert "再见" in events[0]["data"]["content"]


@pytest.mark.asyncio
async def test_floor_manager_blocks_moderator_final_closing_before_human_budget() -> None:
    human_requests: list[dict[str, object]] = []

    async def _on_human_input_requested(data: dict[str, object]) -> None:
        human_requests.append(data)

    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source="moderator", content="同学们，今天讨论就到这里。再见！"),
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})
    floor_manager.on_human_input_requested(_on_human_input_requested)
    floor_manager._speaker_message_count["moderator"] = 1
    floor_manager._speaker_message_count["explorer"] = 1
    floor_manager._recent_display_speakers = ["老师", "小探"]

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，今天讨论就到这里。再见！")
    )

    assert result is not None
    assert result["event_type"] == "human_input_requested"
    assert result["data"]["speaker"] == "豆苗"
    assert result["data"]["reason"] == "moderator_designated_human"
    assert human_requests == []
    assert floor_manager._discussion_end_requested is False


@pytest.mark.asyncio
async def test_floor_manager_blocked_final_closing_prefers_unfinished_non_human_continuation() -> (
    None
):
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="innovator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "innovator": "小想", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 3, "豆苗": 3})
    floor_manager._recent_display_speakers = ["老师", "豆苗"]

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，今天讨论就到这里。再见！")
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert "小想同学" in result["data"]["content"]
    assert floor_manager._discussion_end_requested is False


@pytest.mark.asyncio
async def test_floor_manager_blocks_moderator_final_closing_before_ai_coverage() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "skeptic": "小疑", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count.update(
        {
            "moderator": 3,
            "explorer": 2,
            "豆苗": floor_manager._HUMAN_TURN_MIN_TARGET,
        }
    )
    floor_manager._recent_display_speakers = ["老师", "豆苗", "小探"]

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，今天讨论就到这里。再见！")
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert "小疑同学" in result["data"]["content"]
    assert floor_manager._discussion_end_requested is False


@pytest.mark.asyncio
async def test_floor_manager_blocks_moderator_final_closing_before_minimum_substantive_turns() -> (
    None
):
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
            SimpleNamespace(name="empath"),
            SimpleNamespace(name="confucius"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        thinker_agent_names={"confucius"},
        nominal_max_turns=14,
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "skeptic": "小疑",
            "empath": "小爱",
            "confucius": "孔子",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count.update(
        {
            "moderator": 6,
            "explorer": 2,
            "skeptic": 1,
            "empath": 1,
            "confucius": 1,
            "豆苗": floor_manager._HUMAN_TURN_MIN_TARGET,
        }
    )
    floor_manager._substantive_turn_count = 16
    floor_manager._recent_display_speakers = ["老师", "小探", "豆苗"]

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，今天讨论就到这里。再见！")
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert "再见" not in result["data"]["content"]
    assert floor_manager._discussion_end_requested is False
    gate = floor_manager._closing_gate_status()
    assert gate["remaining_substantive_turns"] > 0


@pytest.mark.asyncio
async def test_floor_manager_skips_forced_goodbye_when_stream_ends_during_human_wait() -> None:
    emitted_messages: list[tuple[str, str, str]] = []

    async def _on_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source="moderator", content="豆苗同学，你来说说看？"),
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager.on_message(_on_message)
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            floor_manager._resume_team_after_human_input()

    assert events[0]["event_type"] == "human_input_requested"
    assert any(event["event_type"] == "human_input_requested" for event in events)
    assert events[-1]["event_type"] == "ended"
    assert any(
        source == "系统" and msg_type == "system" and "按跳过处理" in content
        for source, content, msg_type in emitted_messages
    )
    assert not any(
        source == "豆苗" and msg_type == "text" for source, _content, msg_type in emitted_messages
    )
    assert not any(
        event["event_type"] == "message" and "再见" in event["data"]["content"] for event in events
    )


@pytest.mark.asyncio
async def test_floor_manager_recovers_with_human_request_when_stream_ends_before_human_budget() -> (
    None
):
    emitted_messages: list[tuple[str, str, str]] = []

    async def _on_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    floor_manager = FloorManager(
        team=_MultiStreamTeamStub(
            [
                [TextMessage(source="explorer", content="我想先从体验角度说说。")],
                [TextMessage(source="moderator", content="豆苗同学，你怎么看？")],
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})
    floor_manager.on_message(_on_message)
    floor_manager._speaker_message_count["moderator"] = 1
    floor_manager._recent_display_speakers = ["老师"]

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            break

    assert [event["event_type"] for event in events] == [
        "message",
        "human_input_requested",
    ]
    assert events[0]["data"]["source"] == "explorer"
    assert events[1]["data"]["speaker"] == "豆苗"
    assert any(
        source == "moderator" and content == "豆苗同学，你怎么看？"
        for source, content, _msg_type in emitted_messages
    )
    assert not any(
        event["event_type"] == "message" and "再见" in event["data"]["content"] for event in events
    )


@pytest.mark.asyncio
async def test_floor_manager_routes_expected_ai_stream_recovery_through_moderator() -> None:
    floor_manager = FloorManager(
        team=_MultiStreamTeamStub(
            [
                [
                    TextMessage(
                        source="moderator", content="我们先听听孔子先生的想法。孔子先生，你怎么看？"
                    ),
                    TextMessage(source="confucius", content="学习要自己思考，工具只能辅助。"),
                ],
                [],
            ]
        ),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="confucius"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "confucius": "孔子", "skeptic": "小疑", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count.update(
        {"moderator": 1, "confucius": 0, "skeptic": 0, "豆苗": 1}
    )
    floor_manager._recent_display_speakers = ["老师", "豆苗"]
    floor_manager._general_stall_timeout_sec = 0.01

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            break

    assert any(
        event.get("event_type") == "message" and event["data"]["source"] == "confucius"
        for event in events
    )
    assert not any(event.get("event_type") == "human_input_requested" for event in events)
    assert floor_manager.state != FloorState.HUMAN_TURN_WAITING
    assert floor_manager._expected_next_ai_speaker is None
    assert floor_manager._pending_continuation_task is None


@pytest.mark.asyncio
async def test_floor_manager_preserves_explicit_ai_designation_over_human_budget_recovery() -> None:
    floor_manager = FloorManager(
        team=_MultiStreamTeamStub(
            [
                [TextMessage(source="moderator", content="请小探同学发言。")],
                [TextMessage(source="explorer", content="我来回应一下。")],
            ]
        ),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "skeptic": "小疑", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count.update(
        {"moderator": 2, "explorer": 1, "skeptic": 1, "豆苗": 4}
    )
    floor_manager._recent_display_speakers = ["豆苗", "小疑", "老师"]
    floor_manager._general_stall_timeout_sec = 0.01

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event.get("event_type") == "human_input_requested" or (
            event.get("event_type") == "message"
            and event.get("data", {}).get("source") == "explorer"
        ):
            break

    assert not any(event.get("event_type") == "human_input_requested" for event in events)
    assert any(
        event.get("event_type") == "message" and event.get("data", {}).get("source") == "explorer"
        for event in events
    )


@pytest.mark.asyncio
async def test_floor_manager_completes_pending_human_input_when_stream_ends_during_human_wait() -> (
    None
):
    emitted_messages: list[tuple[str, str, str]] = []

    async def _on_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source="moderator", content="豆苗同学，你来说说看？"),
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager.on_message(_on_message)
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            await floor_manager.submit_human_input("豆苗", "我还想补充一点。")

    assert events[0]["event_type"] == "human_input_requested"
    assert any(event["event_type"] == "human_input_requested" for event in events)
    assert events[-1]["event_type"] == "ended"
    assert any(
        source == "豆苗" and msg_type == "text" and content == "我还想补充一点。"
        for source, content, msg_type in emitted_messages
    )
    assert not any(
        source == "系统" and msg_type == "system" and "按跳过处理" in content
        for source, content, msg_type in emitted_messages
    )


@pytest.mark.asyncio
async def test_floor_manager_restarts_team_stream_after_human_turn_recovery() -> None:
    floor_manager = FloorManager(
        team=_MultiStreamTeamStub(
            [
                [TextMessage(source="moderator", content="豆苗同学，你来说说看？")],
                [TextMessage(source="skeptic", content="我接着回应一下。")],
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "skeptic": "小疑", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            await floor_manager.submit_human_input("豆苗", "我还想补充一点。")

    assert events[0]["event_type"] == "human_input_requested"
    assert any(event["event_type"] == "human_input_requested" for event in events)
    assert any(
        event["event_type"] == "message"
        and event["data"]["source"] == "豆苗"
        and event["data"]["content"] == "我还想补充一点。"
        for event in events
    )
    assert not any(
        event["event_type"] == "message"
        and event["data"]["source"] == "系统"
        and "按跳过处理" in event["data"]["content"]
        for event in events
    )
    assert floor_manager.team.run_calls >= 2


@pytest.mark.asyncio
async def test_floor_manager_restarts_team_stream_after_moderator_designates_ai() -> None:
    floor_manager = FloorManager(
        team=_MultiStreamTeamStub(
            [
                [
                    TextMessage(source="moderator", content="同学们好，今天我们先把背景理清楚。"),
                    TextMessage(source="moderator", content="接下来请小疑发言。"),
                ],
                [TextMessage(source="skeptic", content="我来接着说。")],
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "skeptic": "小疑", "豆苗": "豆苗"})

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if (
            event.get("event_type") == "message"
            and event.get("data", {}).get("source") == "skeptic"
        ):
            break

    assert any(
        event["event_type"] == "message"
        and event["data"]["source"] == "moderator"
        and "小疑" in event["data"]["content"]
        for event in events
    )
    assert any(
        event["event_type"] == "message"
        and event["data"]["source"] == "skeptic"
        and event["data"]["content"] == "我来接着说。"
        for event in events
    )


@pytest.mark.asyncio
async def test_floor_manager_moderator_handoff_survives_brevity_and_keeps_tts_aligned() -> None:
    emitted_messages: list[tuple[str, str, str, str]] = []

    async def _on_message(
        source: str,
        content: str,
        msg_type: str,
        tts_text: str = "",
    ) -> None:
        emitted_messages.append((source, content, msg_type, tts_text))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})
    floor_manager.on_message(_on_message)
    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager.current_speaker = "moderator"
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})

    await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="moderator",
            content="哎呀，小探同学分享的这个例子太生动了！",
        )
    )
    await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source="moderator",
            content="你提到小狗豆豆耷拉耳朵、发出呜呜声，这个观察真的很棒。",
        )
    )

    await floor_manager._process_event(
        TextMessage(
            source="moderator",
            content=(
                "哎呀，小探同学分享的这个例子太生动了！"
                "你提到小狗豆豆耷拉耳朵、发出呜呜声，这个观察真的很棒。"
                "那其他同学怎么看呢？豆苗同学，你今天也来跟大家聊聊吧。"
            ),
        )
    )

    moderator_messages = [
        message
        for message in emitted_messages
        if message[0] == "moderator" and message[2] == "text"
    ]

    assert moderator_messages
    _, content, _, tts_text = moderator_messages[-1]
    assert content == "豆苗同学，你怎么看？"
    assert tts_text == "豆苗同学，你怎么看？"
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING
    assert floor_manager.current_speaker == "豆苗"


@pytest.mark.asyncio
async def test_floor_manager_does_not_restart_while_designated_ai_reply_is_still_in_same_stream() -> (
    None
):
    team = _ClosableReentrantMultiStreamTeamStub(
        [
            [
                TextMessage(source="moderator", content="同学们好，今天我们先把背景理清楚。"),
                TextMessage(source="moderator", content="接下来请小疑发言。"),
                TextMessage(source="skeptic", content="我来接着说。"),
            ],
        ]
    )
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "skeptic": "小疑", "豆苗": "豆苗"})

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if (
            event.get("event_type") == "message"
            and event.get("data", {}).get("source") == "skeptic"
        ):
            break

    assert any(
        event["event_type"] == "message"
        and event["data"]["source"] == "skeptic"
        and event["data"]["content"] == "我来接着说。"
        for event in events
    )
    assert team.run_calls == 1
    assert floor_manager.team.run_calls == 1


@pytest.mark.asyncio
async def test_floor_manager_restarts_team_stream_after_general_stall() -> None:
    emitted_messages: list[tuple[str, str, str]] = []

    async def _on_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    team = _StallingThenContinuingTeamStub(
        [TextMessage(source="skeptic", content="我先说一句。")],
        [TextMessage(source="moderator", content="恢复后继续。")],
    )
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "skeptic": "小疑", "豆苗": "豆苗"})
    floor_manager.on_message(_on_message)
    floor_manager._general_stall_timeout_sec = 0.01

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            await floor_manager.submit_human_input("豆苗", "我先接一句。")

    assert any(
        source == "系统"
        and msg_type == "system"
        and ("自动恢复调度" in content or "系统已指定" in content)
        for source, content, msg_type in emitted_messages
    )
    assert team.run_calls >= 2


@pytest.mark.asyncio
async def test_floor_manager_flushes_stalled_stream_chunks_before_restart() -> None:
    team = _StallingThenContinuingTeamStub(
        [
            TextMessage(source="peacemaker", content="我先说一句。"),
            ModelClientStreamingChunkEvent(source="moderator", content="我来接一句。"),
        ],
        [
            TextMessage(source="moderator", content="接下来请豆苗发言。"),
        ],
    )
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "peacemaker": "小和", "豆苗": "豆苗"})
    floor_manager._general_stall_timeout_sec = 0.01

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            await floor_manager.submit_human_input("豆苗", "我接着说。")

    assert any(event["event_type"] == "human_input_requested" for event in events)
    assert any(
        event["event_type"] == "human_input_requested" and event["data"]["speaker"] == "豆苗"
        for event in events
    )
    assert team.run_calls >= 2


@pytest.mark.asyncio
async def test_floor_manager_suppresses_residual_ai_output_after_moderator_handoff_to_human() -> (
    None
):
    team = _MultiStreamTeamStub(
        [
            [
                TextMessage(source="moderator", content="豆苗同学，你来说说看？"),
                ModelClientStreamingChunkEvent(source="skeptic", content="残留片段。"),
            ],
            [TextMessage(source="skeptic", content="我接着回应一下。")],
        ]
    )
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "skeptic": "小疑", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            await floor_manager.submit_human_input("豆苗", "（跳过）")

    assert events[0]["event_type"] == "human_input_requested"
    assert events[0]["data"]["speaker"] == "豆苗"
    assert not any(
        event["event_type"] == "stream" and event["data"]["source"] == "skeptic" for event in events
    )
    assert any(
        event["event_type"] == "human_input_requested" and event["data"]["speaker"] == "豆苗"
        for event in events
    )
    assert any(
        event["event_type"] == "message"
        and event["data"]["source"] == "豆苗"
        and event["data"]["content"] == "（跳过）"
        for event in events
    )
    assert team.run_calls >= 2


@pytest.mark.asyncio
async def test_floor_manager_manual_skip_sets_deterministic_non_human_followup() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="skeptic"),
            SimpleNamespace(name="innovator"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "skeptic": "小疑",
            "innovator": "小创",
            "豆苗": "豆苗",
        }
    )
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._recent_human_skip_pending = True
    floor_manager._speaker_message_count.update({"moderator": 1, "innovator": 1})
    floor_manager._recent_display_speakers = ["老师", "小创", "豆苗"]

    await floor_manager._process_event(TextMessage(source="豆苗", content="（跳过）"))

    assert get_designated_speaker() == "skeptic"
    assert floor_manager._expected_next_ai_speaker == "skeptic"


@pytest.mark.asyncio
async def test_floor_manager_stall_recovery_designated_human_emits_input_request() -> None:
    emitted_messages: list[tuple[str, str, str]] = []

    async def _on_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    team = _StallingThenContinuingTeamStub(
        [],
        [TextMessage(source="moderator", content="收到，继续讨论。")],
    )
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "skeptic": "小疑", "豆苗": "豆苗"})
    floor_manager.on_message(_on_message)
    floor_manager._general_stall_timeout_sec = 0.01
    # Pre-seed prior turns so smart fallback prioritizes human invitation over moderator opening.
    floor_manager._speaker_message_count["moderator"] = 1
    floor_manager._speaker_message_count["skeptic"] = 1

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            await floor_manager.submit_human_input("豆苗", "我来接着说。")

    assert any(
        source == "系统" and msg_type == "system" and "系统已指定" in content
        for source, content, msg_type in emitted_messages
    )
    assert any(
        event["event_type"] == "human_input_requested"
        and event["data"]["speaker"] == "豆苗"
        and event["data"]["reason"] == "moderator_designated_human"
        for event in events
    )
    assert team.run_calls >= 2


@pytest.mark.asyncio
async def test_floor_manager_request_end_discussion_finishes_human_wait_session() -> None:
    emitted_messages: list[tuple[str, str, str, str]] = []

    async def _on_message(
        source: str,
        content: str,
        msg_type: str,
        tts_text: str = "",
    ) -> None:
        emitted_messages.append((source, content, msg_type, tts_text))

    floor_manager = FloorManager(
        team=_MultiStreamTeamStub(
            [
                [TextMessage(source="moderator", content="豆苗同学，你来说说看？")],
                [],
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager.on_message(_on_message)
    floor_manager._speaker_message_count.update({"moderator": 1, "skeptic": 1})

    events = []
    async for event in floor_manager.run("测试话题"):
        events.append(event)
        if event["event_type"] == "human_input_requested":
            await floor_manager.request_end_discussion("豆苗", source="button")

    assert any(event["event_type"] == "human_input_requested" for event in events)
    assert events[-1]["event_type"] == "ended"
    assert not any(event["event_type"] in {"error", "api_error"} for event in events)
    assert any(
        source == "系统" and msg_type == "system" and "请求结束本次讨论" in content
        for source, content, msg_type, _ in emitted_messages
    )
    assert not any(
        source == "系统" and msg_type == "system" and "检测到流程持续停滞" in content
        for source, content, msg_type, _ in emitted_messages
    )
    assert any(
        source == "moderator"
        and msg_type == "text"
        and ("总结" in content or "点评" in content or "今天" in content)
        for source, content, msg_type, _ in emitted_messages
    )


def test_floor_manager_reasserts_expected_ai_designation_only_once_per_mismatch() -> None:
    designated_calls: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="innovator"),
            SimpleNamespace(name="rationalist"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=designated_calls.append,
    )
    floor_manager.current_speaker = "moderator"
    floor_manager._expected_next_ai_speaker = "innovator"

    assert floor_manager._enforce_expected_ai_speaker("rationalist") is True
    assert floor_manager._enforce_expected_ai_speaker("rationalist") is True

    assert designated_calls == ["innovator"]


@pytest.mark.asyncio
async def test_floor_manager_rewrites_ungrounded_neutral_quote_claims() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager._speaker_message_count["moderator"] = 1

    result = floor_manager._sanitize_all_references(
        "moderator",
        "有同学提到“抖水珠”的比喻太绝了。你愿意展开说说吗？",
    )

    assert "有同学提到“抖水珠”" not in result
    assert "抖水珠" not in result
    assert "展开说说" in result


@pytest.mark.asyncio
async def test_floor_manager_forces_moderator_goodbye_before_end_when_missing() -> None:
    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source="explorer", content="我觉得演员要先会观察生活。"),
            ]
        ),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})

    events = [event async for event in floor_manager.run("测试话题")]

    assert [event["event_type"] for event in events] == ["message", "message", "ended"]
    assert events[1]["data"]["source"] == "moderator"
    assert "再见" in events[1]["data"]["content"]


@pytest.mark.asyncio
async def test_floor_manager_keeps_mixed_script_human_name_stable_in_reference_rewrite() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="user_a")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "user_a": "测试用户a"})
    floor_manager._speaker_message_count["user_a"] = 1
    floor_manager._recent_display_speakers = ["测试用户a"]

    cleaned = floor_manager._sanitize_all_references(
        "moderator",
        "测试用户a，你这个点说得很到位。",
    )

    assert "测试测试用户a" not in cleaned
    assert "测试老师" not in cleaned
    assert "测试用户a" in cleaned


@pytest.mark.asyncio
async def test_floor_manager_drops_ungrounded_named_quote_after_praise() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="user_a")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "user_a": "测试用户a"})
    floor_manager._speaker_message_count["user_a"] = 1
    floor_manager._recent_display_speakers = ["测试用户a"]
    floor_manager._recent_reference_quotes = [
        ("测试用户a", "艺术课陶冶人的精神，提高人的境界。"),
    ]

    cleaned = floor_manager._sanitize_all_references(
        "moderator",
        "测试用户a，你这个点说得很到位——“艺术从来不属于价格标签，它属于每一个愿意去感受、去表达的心灵。”这句话我记在小本本上了！",
    )

    assert "艺术从来不属于价格标签" not in cleaned
    assert "测试用户a" in cleaned
    assert "说得很到位" in cleaned


def test_sanitize_all_references_neutralizes_unspoken_thinker_nickname() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="comedian"),
            SimpleNamespace(name="kant"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        thinker_agent_names=["kant"],
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "comedian": "可乐",
            "kant": "伊曼努尔·康德",
            "豆苗": "豆苗",
        }
    )
    floor_manager._speaker_message_count["moderator"] = 1
    floor_manager._recent_display_speakers = ["老师"]

    cleaned = floor_manager._sanitize_all_references(
        "comedian",
        "康德老爷爷，你说的话好深奥哦！不过我好像明白一点点。",
    )

    assert "康德" not in cleaned
    assert "老爷爷" not in cleaned
    assert "你说的话" not in cleaned
    assert "不过我好像明白一点点" in cleaned


def test_floor_manager_drops_untraceable_moderator_praise_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count["explorer"] = 1
    floor_manager._recent_display_speakers = ["小探"]
    floor_manager._recent_reference_quotes = [("小探", "当然按啊")]

    cleaned = floor_manager._sanitize_all_references(
        "moderator",
        "哎呀，这个比喻太妙了——“吃一整盒糖，甜得都分不出味”！我们继续听听大家。",
    )

    assert "吃一整盒糖" not in cleaned
    assert "甜得都分不出味" not in cleaned
    assert "我们继续听听大家" in cleaned


def test_floor_manager_rewrites_unspoken_question_credit() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="dewey"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "dewey": "约翰·杜威", "skeptic": "小疑", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "dewey": 0, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "豆苗"]

    cleaned = floor_manager._sanitize_all_references(
        "dewey",
        "老师，你说得好，小疑同学也问到了要害上！",
    )

    assert "小疑" not in cleaned
    assert "问到了" not in cleaned
    assert "刚才这个问题" in cleaned


def test_floor_manager_drops_ungrounded_named_labeled_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="dewey")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "dewey": "约翰·杜威", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "dewey": 1, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "豆苗"]
    floor_manager._recent_reference_quotes = [("豆苗", "班级规则应该大家一起定")]

    cleaned = floor_manager._sanitize_all_references(
        "dewey",
        "豆苗刚才那个“连铅笔都用不上”的例子特别好。",
    )

    assert "连铅笔都用不上" not in cleaned
    assert "豆苗刚才" not in cleaned
    assert "这个例子" in cleaned


def test_floor_manager_rewrites_misattributed_named_labeled_quote_to_owner() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="turing")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "turing": "艾伦·图灵", "豆苗": "豆苗", "skeptic": "小疑"}
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "turing": 1, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "小疑", "豆苗"]
    floor_manager._recent_reference_quotes = [("小疑", "我们是不是应该先解决地球问题")]

    cleaned = floor_manager._sanitize_all_references(
        "turing",
        "豆苗提到的“先解决地球问题”，其实指向同一个核心。",
    )

    assert "豆苗提到" not in cleaned
    assert "小疑提到的“先解决地球问题”" in cleaned


def test_floor_manager_rewrites_prefixed_named_quote_to_owner() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="socrates")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        thinker_agent_names=["socrates"],
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "socrates": "苏格拉底", "empath": "小爱", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "socrates": 1, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "豆苗", "小爱"]
    floor_manager._recent_reference_quotes = [("豆苗", "这件事有没有让别人更难受，还是让大家更自由")]

    cleaned = floor_manager._sanitize_all_references(
        "socrates",
        "刚才小爱说的“自由还是难受”让我想起一个故事。",
    )

    assert "小爱说的" not in cleaned
    assert "豆苗的这个想法让我想起" in cleaned


def test_floor_manager_drops_paraphrased_named_quote_when_not_exactly_traceable() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "peacemaker": "小和", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 1, "peacemaker": 1, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "豆苗"]
    floor_manager._recent_reference_quotes = [("豆苗", "这个谎就不是错，而是为了救人")]

    cleaned = floor_manager._sanitize_all_references(
        "peacemaker",
        "豆苗说的“救人更重要”我理解。",
    )

    assert "豆苗说的“救人更重要”" not in cleaned
    assert "豆苗的这个想法我理解" in cleaned


def test_floor_manager_drops_name_from_sentence_quote_when_not_exactly_traceable() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="pragmatist")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "pragmatist": "小行", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 1, "pragmatist": 1, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "豆苗"]
    floor_manager._recent_reference_quotes = [("豆苗", "边界这件事真不是能或者不能这么简单")]

    cleaned = floor_manager._sanitize_all_references(
        "pragmatist",
        "豆苗这句话说得太准了——不是“能不能去”，是“怎么去”！",
    )

    assert "豆苗这句话" not in cleaned
    assert "这句话说得太准了" in cleaned


def test_floor_manager_drops_untraceable_named_story_and_followup() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="kant")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "kant": "康德", "peacemaker": "小和", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 1, "kant": 1, "豆苗": 1})
    floor_manager._recent_display_speakers = ["老师", "小和", "豆苗"]
    floor_manager._recent_reference_quotes = [("小和", "我听了康德先生的回答，觉得又佩服又有点纠结")]

    cleaned = floor_manager._sanitize_all_references(
        "kant",
        "小和同学，你提的这个“姥姥和电视剧”的故事真有意思。让我用你的例子来想想看——你对姥姥说“电视里的小宝宝在找你”，这其实不是谎言。你并没有说假话，只是换了一个更体贴的说法。",
    )

    assert "姥姥和电视剧" not in cleaned
    assert "电视里的小宝宝" not in cleaned
    assert "小和同学，你提" not in cleaned


@pytest.mark.asyncio
async def test_floor_manager_human_message_during_closing_does_not_reopen_selection() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager.state = FloorState.CLOSING
    floor_manager.current_speaker = "豆苗"

    result = await floor_manager._process_event(TextMessage(source="豆苗", content="我补充一句。"))

    assert result is None
    assert floor_manager.state == FloorState.CLOSING


def test_floor_manager_final_praise_does_not_quote_truncated_highlights() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager._recent_human_turn_summaries["豆苗"] = [
        "老师，我觉得标准答案有时候会让我们不敢问\"为什么\"，但我们可以多问几个问题来找到更多答案。",
        "老师，小疑问的\"标准答案是谁定的\"这个问题让我想到，有时候标准答案可能只是其中一种解法。",
    ]
    floor_manager._speaker_message_count["豆苗"] = 2

    closing = floor_manager._build_grounded_moderator_final_closing("今天到这里。")

    assert "提到“" not in closing
    assert "想到“" not in closing
    assert "标准答案是谁定的" in closing


def test_floor_manager_final_praise_skips_meta_complaint_highlights() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager._recent_human_turn_summaries["豆苗"] = [
        "老师，你怎么把麦克风突然就给我了，我还没准备好呢。",
        "树木也会呼吸、会长大，所以我觉得它们也算是有生命的。",
    ]
    floor_manager._speaker_message_count["豆苗"] = 2

    closing = floor_manager._build_grounded_moderator_final_closing("今天到这里。")

    # 元提问/抱怨不应被当成观点夸出来
    assert "麦克风" not in closing
    assert "突然就给我" not in closing
    # 真正的观点应当保留
    assert "树木也会呼吸" in closing


def test_floor_manager_smart_fallback_honors_expected_ai_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="skeptic"),
            SimpleNamespace(name="socrates"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        thinker_agent_names=["socrates"],
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "豆苗": 1})
    floor_manager._expected_next_ai_speaker = "skeptic"

    assert floor_manager._smart_fallback_speaker() == "skeptic"


def test_floor_manager_waiting_human_speaker_prefers_requested_human() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.current_speaker = "moderator"
    floor_manager._last_human_input_requested_speaker = "豆苗"
    floor_manager.state = FloorState.HUMAN_TURN_WAITING

    assert floor_manager._current_waiting_human_speaker() == "豆苗"


def test_floor_manager_routes_budget_recovery_to_moderator_before_human_request() -> None:
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"}
    )
    floor_manager._current_topic = "测试话题"
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1})
    floor_manager._recent_display_speakers = ["老师", "小探"]

    recovered = floor_manager._schedule_recovery_after_premature_stream_end()

    assert recovered is True
    assert designated_updates[-1] == "moderator"
    assert floor_manager._expected_next_ai_speaker == "moderator"
    assert floor_manager._deferred_human_request_speaker is None
    assert floor_manager._pending_human_input_reason == "normal"
    assert floor_manager._pending_continuation_task is not None
    assert "不要直接请求真人学生开麦" in floor_manager._pending_continuation_task


@pytest.mark.asyncio
async def test_floor_manager_rebuilds_handoff_text_after_blocking_repeat_human_invite() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "skeptic": "小疑", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count.update(
        {"moderator": 2, "explorer": 1, "skeptic": 0, "豆苗": 1}
    )
    floor_manager._recent_display_speakers = ["老师", "豆苗"]

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="豆苗同学，你怎么看？")
    )

    assert result is not None
    assert "豆苗同学" not in result["data"]["content"]
    assert "小疑同学" in result["data"]["content"]


def test_floor_manager_budget_human_invite_waits_for_adaptive_gap() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "skeptic": "小疑", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count.update(
        {"moderator": 3, "explorer": 2, "skeptic": 2, "豆苗": 3}
    )
    floor_manager._recent_display_speakers = ["老师", "豆苗", "小探"]

    assert (
        floor_manager._should_force_budget_human_invitation("moderator", "我们继续聊聊。", None)
        is False
    )


@pytest.mark.asyncio
async def test_floor_manager_final_closing_praises_real_human_contribution() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        summary_memory=RollingSummaryMemory(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 2, "豆苗": 5})
    floor_manager._recent_display_speakers = ["老师", "豆苗"]
    floor_manager.state = FloorState.AI_SPEAKING
    await floor_manager._record_turn_summary(
        "豆苗", "我觉得艺术课能陶冶人的精神，也能提高人的境界。"
    )
    await floor_manager._record_turn_summary(
        "豆苗", "而且艺术教育会贯穿人的一生，不是只有会画画才算懂艺术。"
    )

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，今天讨论就到这里。再见！")
    )

    assert result is not None
    assert result["data"]["source"] == "moderator"
    assert result["data"]["tts_text"] == result["data"]["content"]
    assert "豆苗" in result["data"]["content"]
    assert "陶冶人的精神" in result["data"]["content"]
    assert "贯穿人的一生" in result["data"]["content"]
    assert "下次如果" in result["data"]["content"]
    assert "再见" in result["data"]["content"]
    assert floor_manager._discussion_end_requested is True


@pytest.mark.asyncio
async def test_floor_manager_diagnostics_reports_incomplete_ai_coverage() -> None:
    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source="explorer", content="我觉得演员要先会观察生活。"),
            ]
        ),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="pavlov"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "pavlov": "巴甫洛夫", "豆苗": "豆苗"}
    )

    _ = [event async for event in floor_manager.run("测试话题")]
    diagnostics = floor_manager.diagnostics()

    assert diagnostics["coverage_ok"] is False
    assert diagnostics["human_turn_count"] == 0
    assert diagnostics["missing_ai_display_names"] == ["巴甫洛夫"]
    assert diagnostics["spoken_display_names"] == ["小探", "老师"]


def test_floor_manager_closing_gate_does_not_force_ready_when_ai_missing() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "老师",
            "explorer": "小探",
            "skeptic": "小疑",
        }
    )
    floor_manager._speaker_message_count.update({"moderator": 2, "explorer": 1, "skeptic": 0})
    floor_manager._closing_attempt_count = floor_manager._CLOSING_ATTEMPT_LIMIT

    gate = floor_manager._closing_gate_status()

    assert gate["missing_ai_display_names"] == ["小疑"]
    assert gate["forced_ready_due_to_attempt_limit"] is False
    assert gate["ready"] is False


def test_floor_manager_human_skip_ladder_reduces_target_and_allows_exception() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1, "豆苗": 0})

    floor_manager._register_human_skip(reason="manual_skip", is_timeout=False)
    floor_manager._register_human_skip(reason="manual_skip", is_timeout=False)
    gate_after_two = floor_manager._closing_gate_status()

    assert gate_after_two["human_turn_target"] == 4
    assert gate_after_two["human_turn_target_mode"] == "fallback_after_two_skips"

    floor_manager._register_human_skip(reason="timeout", is_timeout=True)
    gate_after_three = floor_manager._closing_gate_status()

    assert gate_after_three["human_turn_target"] == 3
    assert gate_after_three["human_budget_exception_allowed"] is True
    assert "remaining_human_turns=3" not in gate_after_three["blockers"]
    assert gate_after_three["participation_insufficient"] is True


@pytest.mark.asyncio
async def test_floor_manager_timeout_skip_updates_degradation_counters() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"

    result = await floor_manager._recover_pending_human_turn_after_stream_end(
        reason="timeout_test",
        log_message="timeout-test",
        request_stream_restart=False,
    )

    assert result is None
    assert floor_manager._human_skip_count == 1
    assert floor_manager._human_timeout_count == 1
    assert floor_manager._consecutive_human_skips == 1
    assert floor_manager._recent_human_skip_pending is True
    assert floor_manager._get_speaker_utterance_status("豆苗") == SpeakerUtteranceStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_floor_manager_timeout_skip_after_idle_notice_does_not_double_count() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"
    floor_manager._human_turn_idle_notice_sent = True
    floor_manager._human_timeout_count = 1

    result = await floor_manager._recover_pending_human_turn_after_stream_end(
        reason="timeout_test",
        log_message="timeout-test",
        request_stream_restart=False,
    )

    assert result is None
    assert floor_manager._human_skip_count == 1
    assert floor_manager._human_timeout_count == 1
    assert floor_manager._consecutive_human_skips == 1
    assert floor_manager._recent_human_skip_pending is True
    assert floor_manager._get_speaker_utterance_status("豆苗") == SpeakerUtteranceStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_floor_manager_timeout_notice_uses_requested_human_when_current_speaker_polluted() -> None:
    emitted: list[tuple[str, str, str]] = []

    async def capture_message(source: str, content: str, kind: str) -> None:
        emitted.append((source, content, kind))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager.on_message(capture_message)
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "moderator"
    floor_manager._last_human_input_requested_speaker = "豆苗"

    result = await floor_manager._recover_pending_human_turn_after_stream_end(
        reason="timeout_test",
        log_message="timeout-test",
        request_stream_restart=False,
    )

    assert result is None
    assert emitted[-1][1] == "豆苗这一轮还没来得及发言，系统先按跳过处理。"
    assert "老师这一轮" not in emitted[-1][1]


def test_audit_quote_owner_ignores_participant_name_inside_quote_fragment() -> None:
    from scripts.simulate_10_rounds_quality_audit import _mentions_participant_as_quote_owner

    text = "最后夸夸豆苗，后来又想到“我接一下小和的方向，我觉得讨论“说谎永远…””。"
    closing_text = "最后夸夸豆苗，你先提到“老师点名我了，那我就接小和的比喻说两句”，后来又想到“我接一下小和的方向，我觉得讨论“说谎永远…””。"

    assert _mentions_participant_as_quote_owner(text, "小和", "我接一下小和的方向，我觉得讨论“说谎永远…") is False
    assert _mentions_participant_as_quote_owner(closing_text, "小和", "我接一下小和的方向，我觉得讨论“说谎永远…") is False


def test_audit_quote_attribution_ignores_concept_label_quotes() -> None:
    from scripts.simulate_10_rounds_quality_audit import _has_session_quote_attribution

    text = "小思用“捉迷藏指错方向”来比喻，我是说，你把“说谎”这个动作和“为什么要说谎”这个目的分开了。"
    human_concept_text = "我同意豆苗说的！不过我觉得标准答案还容易让我们变成“做题机器”。"

    assert _has_session_quote_attribution(text, "为什么要说谎", ["老师", "小思", "小理"]) is False
    assert _has_session_quote_attribution(human_concept_text, "做题机器", ["老师", "豆苗"]) is False


def test_runner_sanitizes_human_proxy_wrong_topic_title() -> None:
    from scripts.simulate_10_rounds_quality_audit import _sanitize_human_reply

    cleaned = _sanitize_human_reply(
        "数字鸿沟",
        [],
        0,
        "我接一下小理的方向，我觉得讨论“AI 会抢走工作吗”时，不能只看一个标准。",
    )

    assert "小理的方向" not in cleaned
    assert "AI 会抢走工作吗" not in cleaned
    assert "讨论“数字鸿沟”时" in cleaned


@pytest.mark.asyncio
async def test_submit_human_input_requests_owner_loop_recovery_when_no_active_wait_task() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"
    floor_manager._human_turn_idle_notice_sent = True
    floor_manager._human_timeout_count = 1
    floor_manager._blocked_for_human_input = True

    await floor_manager.submit_human_input("豆苗", "我觉得幸福是和家人在一起。")

    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING
    assert floor_manager._human_timeout_count == 1
    assert floor_manager._pending_submitted_human_inputs == {"豆苗": "我觉得幸福是和家人在一起。"}
    assert floor_manager._stream_restart_requested is True


@pytest.mark.asyncio
async def test_owner_loop_recovers_pending_human_input_before_next_team_wait() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"
    floor_manager._human_turn_idle_notice_sent = True
    floor_manager._human_timeout_count = 1
    floor_manager._remember_submitted_human_input("豆苗", "我觉得幸福是和家人在一起。")

    result = await floor_manager._recover_pending_human_input_before_next_team_wait()

    assert result["event_type"] == "message"
    assert result["data"]["source"] == "豆苗"
    assert "和家人在一起" in result["data"]["content"]
    assert floor_manager.state == FloorState.SELECTING_SPEAKER
    assert (
        floor_manager._get_speaker_utterance_status("豆苗")
        == SpeakerUtteranceStatus.SPOKE_WITH_CONTENT
    )
    assert floor_manager._human_completed_turn_count == 1
    assert floor_manager._human_timeout_count == 1
    assert floor_manager._pending_submitted_human_inputs == {}


def test_turn_scheduler_pulls_teacher_back_if_human_has_not_spoken_yet() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("pavlov")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "巴甫洛夫": "pavlov",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们来聊聊习惯是怎么形成的。"),
        SimpleNamespace(source="explorer", content="我觉得习惯像一条常走的小路。"),
        SimpleNamespace(source="pavlov", content="我会先从重复和信号之间的关系来看。"),
    ]

    assert selector(thread) == "moderator"


def test_turn_scheduler_brings_in_human_after_teacher_returns_to_invite_phase() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("pavlov")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "巴甫洛夫": "pavlov",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们来聊聊习惯是怎么形成的。"),
        SimpleNamespace(source="explorer", content="我觉得习惯像一条常走的小路。"),
        SimpleNamespace(source="pavlov", content="我会先从重复和信号之间的关系来看。"),
        SimpleNamespace(
            source="moderator", content="你们都给了一个好起点，我们再把目光转回到同学自己的经验。"
        ),
    ]

    assert selector(thread) == "explorer"

    explicit_invite_thread = [
        *thread[:-1],
        SimpleNamespace(
            source="moderator", content="你们都给了一个好起点，豆苗同学，你也来说说自己的经验吧。"
        ),
    ]

    assert selector(explicit_invite_thread) == "豆苗"


@pytest.mark.asyncio
async def test_floor_manager_blocks_unauthorized_human_input_request_event() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.current_speaker = "豆苗"

    result = await floor_manager._process_event(
        UserInputRequestedEvent(request_id="req-unauthorized", source="豆苗")
    )

    assert result is None
    assert floor_manager.state != FloorState.HUMAN_TURN_WAITING


@pytest.mark.asyncio
async def test_floor_manager_recovers_from_stale_human_input_request_event() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.state = FloorState.SELECTING_SPEAKER
    floor_manager.current_speaker = "豆苗"

    result = await floor_manager._process_event(
        UserInputRequestedEvent(request_id="req-stale", source="豆苗")
    )

    assert result is None
    assert floor_manager.current_speaker == ""
    assert floor_manager._stream_restart_requested is True
    assert floor_manager._last_human_input_requested_speaker == ""


@pytest.mark.asyncio
async def test_floor_manager_marks_moderator_designated_human_request_reason() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "豆苗": "豆苗"})

    request_event = await floor_manager._process_event(
        TextMessage(source="moderator", content="请豆苗同学发言。")
    )

    assert request_event is not None
    assert request_event["data"]["reason"] == "moderator_designated_human"


@pytest.mark.asyncio
async def test_floor_manager_enforces_opening_basic_context_for_moderator() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager._current_topic = "要不要给小学生布置周末作业？"

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们好，我们开始吧。")
    )

    assert result is not None
    assert result["event_type"] == "message"
    content = result["data"]["content"]
    assert "来自生活里常见的真实讨论" in content
    assert "先说清楚自己的理由和分歧" in content
    assert len([part for part in re.split(r"[。！？!?]+", content) if part.strip()]) <= 2


@pytest.mark.asyncio
async def test_floor_manager_forces_budget_human_invite_after_brief_moderator_bridge() -> None:
    emitted_messages: list[tuple[str, str, str]] = []

    async def _on_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="skeptic")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "skeptic": "小疑", "豆苗": "豆苗"})
    floor_manager.on_message(_on_message)
    floor_manager._speaker_message_count["豆苗"] = 2
    floor_manager._speaker_message_count["skeptic"] = 2
    floor_manager._recent_display_speakers = ["豆苗", "小疑"]

    request_event = await floor_manager._process_event(
        TextMessage(source="moderator", content="（点头微笑）小疑同学，你说得对！")
    )

    assert request_event is not None
    assert request_event["event_type"] == "human_input_requested"
    assert request_event["data"]["reason"] == "moderator_designated_human"
    moderator_text_messages = [
        content
        for source, content, msg_type in emitted_messages
        if source == "moderator" and msg_type == "text"
    ]
    assert moderator_text_messages[-1] == "豆苗同学，你怎么看？"
    assert any(
        source == "moderator" and msg_type == "text" and content == "豆苗同学，你怎么看？"
        for source, content, msg_type in emitted_messages
    )


@pytest.mark.asyncio
async def test_floor_manager_routes_regular_participant_handoff_back_to_moderator() -> None:
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})
    floor_manager._recent_display_speakers = ["老师", "小疑"]

    result = await floor_manager._process_event(
        TextMessage(source="explorer", content="豆苗，你怎么看？")
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert designated_updates == ["moderator"]
    assert floor_manager._moderator_roleplay_target == "豆苗"
    assert floor_manager._pending_human_input_reason == "normal"


@pytest.mark.asyncio
async def test_floor_manager_blocks_immediate_participant_handoff_back_to_recent_human() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})
    floor_manager._speaker_message_count["豆苗"] = 1
    floor_manager._recent_display_speakers = ["老师", "豆苗"]

    request_event = await floor_manager._process_event(
        TextMessage(source="explorer", content="请豆苗同学接着说说刚才的想法。")
    )

    assert request_event is not None
    assert request_event["event_type"] == "message"


@pytest.mark.asyncio
async def test_floor_manager_allows_first_human_handoff_from_participant_after_warmup() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "skeptic": "小疑", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count["explorer"] = 1
    floor_manager._speaker_message_count["skeptic"] = 1

    request_event = await floor_manager._process_event(
        TextMessage(source="explorer", content="请豆苗同学也说说你的理由。")
    )

    assert request_event is not None
    assert request_event["event_type"] == "human_input_requested"
    assert request_event["data"]["reason"] == "participant_designated_human"


@pytest.mark.asyncio
async def test_floor_manager_allows_thinker_designated_human_with_explicit_handoff() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="socrates"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        thinker_agent_names=["socrates"],
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "socrates": "苏格拉底", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count["explorer"] = 1
    floor_manager._speaker_message_count["socrates"] = 1

    request_event = await floor_manager._process_event(
        TextMessage(source="socrates", content="请豆苗同学发言。")
    )

    assert request_event is not None
    assert request_event["event_type"] == "human_input_requested"
    assert request_event["data"]["reason"] == "participant_designated_human"


@pytest.mark.asyncio
async def test_floor_manager_peer_can_force_human_budget_invite_without_moderator() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {"moderator": "老师", "explorer": "小探", "empath": "小爱", "豆苗": "豆苗"}
    )
    floor_manager._speaker_message_count["豆苗"] = 1
    floor_manager._speaker_message_count["explorer"] = 1
    floor_manager._speaker_message_count["empath"] = 1
    floor_manager._recent_display_speakers = ["豆苗", "小爱"]
    emitted: list[tuple[str, str, str, str]] = []

    async def _capture(source: str, content: str, msg_type: str, tts_text: str = "") -> None:
        emitted.append((source, content, msg_type, tts_text))

    floor_manager.on_message(_capture)

    request_event = await floor_manager._process_event(
        TextMessage(source="explorer", content="我觉得可以先试一种新办法。")
    )

    assert emitted
    assert emitted[-1][0] == "explorer"
    assert "豆苗同学，你怎么看？" in emitted[-1][1]
    assert request_event is not None
    assert request_event["event_type"] == "human_input_requested"
    assert request_event["data"]["reason"] == "participant_designated_human"


def test_turn_scheduler_honors_non_human_markdown_invite_before_human_budget() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("peacemaker"), _agent("questioner"), _agent("comedian")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=12,
        display_name_to_agent={
            "老师": "moderator",
            "小和": "peacemaker",
            "小思": "questioner",
            "可乐": "comedian",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func
    assert selector is not None

    thread = [
        SimpleNamespace(source="moderator", content="我们先请小和同学来说说你的第一印象吧。"),
        SimpleNamespace(source="peacemaker", content="我觉得可以划专门的地方摆摊。"),
        SimpleNamespace(
            source="moderator",
            content="好，咱们也听听其他同学的想法。**可乐同学**，你才一年级，你平时看到路边摆摊的小车车是什么感觉呀？",
        ),
    ]

    assert selector(thread) == "comedian"


def test_turn_scheduler_prefers_invited_name_over_context_mention() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("peacemaker"), _agent("questioner")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=12,
        display_name_to_agent={
            "老师": "moderator",
            "小和": "peacemaker",
            "小思": "questioner",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func
    assert selector is not None

    thread = [
        SimpleNamespace(source="moderator", content="我们先请小和同学来说说你的第一印象吧。"),
        SimpleNamespace(source="peacemaker", content="我觉得可以划专门的地方摆摊。"),
        SimpleNamespace(
            source="moderator",
            content="好，那我想问问小思同学，听到小和这么说，你是更支持摆摊呢，还是觉得不该让路边摆摊？",
        ),
    ]

    assert selector(thread) == "questioner"


def test_turn_scheduler_runs_ten_simulated_discussions_without_unsolicited_human_turns() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    cases = [
        {
            "topic": "大城市该不该在路边摆摊？",
            "students": [("可乐", "comedian"), ("小疑", "skeptic"), ("小和", "peacemaker")],
            "thinker": ("马克斯·韦伯", "weber"),
            "unspoken": ("小和", "peacemaker"),
        },
        {
            "topic": "追求完美是好事还是坏事？",
            "students": [("小探", "explorer"), ("小思", "thinker_child"), ("小理", "logic")],
            "thinker": ("苏格拉底", "socrates"),
            "unspoken": ("小理", "logic"),
        },
        {
            "topic": "要不要给作业设置难度等级？",
            "students": [
                ("小爱", "caring"),
                ("小辩", "debater"),
                ("小安", "steady"),
                ("可乐", "comedian"),
            ],
            "thinker": ("孔子", "confucius"),
            "unspoken": ("小安", "steady"),
        },
        {
            "topic": "班级规则应该大家一起定吗？",
            "students": [("小光", "bright"), ("小疑", "skeptic"), ("小和", "peacemaker")],
            "thinker": ("约翰·杜威", "dewey"),
            "unspoken": ("小和", "peacemaker"),
        },
        {
            "topic": "网络上看到的信息都可信吗？",
            "students": [
                ("小探", "explorer"),
                ("小真", "verifier"),
                ("小思", "thinker_child"),
                ("小合", "synthesizer"),
            ],
            "thinker": ("亚里士多德", "aristotle"),
            "unspoken": ("小合", "synthesizer"),
        },
        {
            "topic": "做错事后应该先道歉还是先解释？",
            "students": [("小诚", "honest"), ("小问", "inquirer"), ("小暖", "warm")],
            "thinker": ("苏格拉底", "socrates"),
            "unspoken": ("小暖", "warm"),
        },
        {
            "topic": "班级奖励应该奖励结果还是努力？",
            "students": [("小勤", "diligent"), ("小衡", "balance"), ("小辩", "debater")],
            "thinker": ("孔子", "confucius"),
            "unspoken": ("小衡", "balance"),
        },
        {
            "topic": "AI写作业是在帮忙还是在偷懒？",
            "students": [("小真", "verifier"), ("小探", "explorer"), ("小理", "logic")],
            "thinker": ("伊曼努尔·康德", "kant"),
            "unspoken": ("小理", "logic"),
        },
        {
            "topic": "好朋友犯错时要不要立刻指出来？",
            "students": [("小和", "peacemaker"), ("小直", "direct"), ("小思", "thinker_child")],
            "thinker": ("约翰·杜威", "dewey"),
            "unspoken": ("小直", "direct"),
        },
        {
            "topic": "学校午餐应该大家投票决定吗？",
            "students": [("小合", "synthesizer"), ("可乐", "comedian"), ("小安", "steady")],
            "thinker": ("亚里士多德", "aristotle"),
            "unspoken": ("小安", "steady"),
        },
    ]

    for case in cases:
        display_name_to_agent = {"老师": "moderator", "豆苗": "豆苗"}
        display_name_to_agent.update(dict(case["students"]))
        display_name_to_agent[case["thinker"][0]] = case["thinker"][1]
        characters = [_agent(agent) for _display, agent in case["students"]]
        characters.append(_agent(case["thinker"][1]))
        team = create_discussion_team(
            moderator=_agent("moderator"),
            characters=characters,
            humans=[_agent("豆苗")],
            selector_client=SimpleNamespace(),
            max_turns=24,
            display_name_to_agent=display_name_to_agent,
            thinker_agent_names=[case["thinker"][1]],
        )
        selector = team._selector_func
        assert selector is not None

        first_student_display, first_student_agent = case["students"][0]
        second_student_display, second_student_agent = case["students"][1]
        unspoken_display, unspoken_agent = case["unspoken"]
        thinker_display, thinker_agent = case["thinker"]
        thread = []

        assert selector(thread) == "moderator"
        thread.append(SimpleNamespace(source="moderator", content=f"今天我们讨论：{case['topic']}"))
        assert selector(thread) == first_student_agent
        thread.append(
            SimpleNamespace(
                source=first_student_agent, content=f"{first_student_display}先说一个生活观察。"
            )
        )

        thread.append(
            SimpleNamespace(
                source="moderator", content=f"请{second_student_display}同学说说你的看法。"
            )
        )
        assert selector(thread) == second_student_agent
        thread.append(
            SimpleNamespace(
                source=second_student_agent, content=f"{second_student_display}提出一个不同角度。"
            )
        )

        thread.append(
            SimpleNamespace(
                source="moderator", content=f"我们也请{thinker_display}先生来谈谈他的看法。"
            )
        )
        assert selector(thread) == thinker_agent
        thread.append(
            SimpleNamespace(source=thinker_agent, content=f"{thinker_display}从概念上补充。")
        )

        thread.append(
            SimpleNamespace(
                source="moderator",
                content=f"那最后还有{unspoken_display}同学没发言呢。你自己觉得，{case['topic']}说说你的看法吧。",
            )
        )
        assert selector(thread) == unspoken_agent
        thread.append(
            SimpleNamespace(source=unspoken_agent, content=f"{unspoken_display}补上自己的判断。")
        )

        spoken_student_agents = {first_student_agent, second_student_agent, unspoken_agent}
        for extra_student_display, extra_student_agent in case["students"]:
            if extra_student_agent in spoken_student_agents:
                continue
            thread.append(
                SimpleNamespace(
                    source="moderator", content=f"请{extra_student_display}同学也补充一下。"
                )
            )
            assert selector(thread) == extra_student_agent
            thread.append(
                SimpleNamespace(
                    source=extra_student_agent, content=f"{extra_student_display}补充一个具体例子。"
                )
            )
            spoken_student_agents.add(extra_student_agent)

        thread.append(
            SimpleNamespace(
                source="moderator", content="大家说得不错，我们继续回到同学自己的经验。"
            )
        )
        assert selector(thread) != "豆苗"

        thread.append(
            SimpleNamespace(source="moderator", content="豆苗同学，你也来说说自己的看法吧。")
        )
        assert selector(thread) == "豆苗"
        thread.append(
            SimpleNamespace(source="豆苗", content="我觉得要看会不会影响别人，也要给摊主机会。")
        )
        next_after_human = selector(thread)
        assert next_after_human not in {"moderator", "豆苗"}
        thread.append(
            SimpleNamespace(
                source=next_after_human, content="豆苗这个平衡点说得清楚，我们接着听听同学回应。"
            )
        )

        thread.append(
            SimpleNamespace(
                source="moderator", content="豆苗同学，听了补充以后，你想不想再修正一下？"
            )
        )
        assert selector(thread) == "豆苗"
        thread.append(
            SimpleNamespace(source="豆苗", content="我会加上固定区域和时间，这样更公平。")
        )
        next_after_second_human = selector(thread)
        assert next_after_second_human not in {"moderator", "豆苗"}

        speakers = [item.source for item in thread]
        assert "豆苗" in speakers
        assert thinker_agent in speakers
        assert {agent for _display, agent in case["students"]}.issubset(set(speakers))


def test_turn_scheduler_returns_teacher_after_first_non_human_warmup() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("pavlov")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "巴甫洛夫": "pavlov",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们先聊聊为什么有人想在家上学。"),
        SimpleNamespace(source="explorer", content="我先从好奇心和冒险感说起。"),
    ]

    assert selector(thread) == "moderator"


def test_turn_scheduler_routes_post_human_turn_to_peer_feedback() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("socrates")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=12,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "苏格拉底": "socrates",
            "豆苗": "豆苗",
        },
        thinker_agent_names=["socrates"],
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们先从自己的观察开始。"),
        SimpleNamespace(source="explorer", content="我先说一个身边的小例子。"),
        SimpleNamespace(source="moderator", content="豆苗同学，你也来说说自己的看法吧。"),
        SimpleNamespace(source="豆苗", content="我觉得先让更多同学开口，会更公平。"),
    ]

    assert selector(thread) in {"explorer", "socrates"}


def test_turn_scheduler_prefers_less_spoken_non_human_candidate_for_balance() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic"), _agent("rationalist")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=16,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "小理": "rationalist",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="我们先把背景理一理。"),
        SimpleNamespace(source="explorer", content="我先说一个观察。"),
        SimpleNamespace(source="skeptic", content="我补一个质疑。"),
        SimpleNamespace(source="explorer", content="我再补充一个例子。"),
        SimpleNamespace(source="rationalist", content="我从逻辑角度看一下。"),
        SimpleNamespace(source="explorer", content="我再回应一下。"),
        SimpleNamespace(source="skeptic", content="我再追问一个细节。"),
        SimpleNamespace(source="moderator", content="豆苗同学，你也说说。"),
        SimpleNamespace(source="豆苗", content="我觉得规则要更清楚。"),
    ]

    assert selector(thread) == "rationalist"


def test_turn_scheduler_does_not_treat_human_teacher_vocative_as_handoff() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=12,
        display_name_to_agent={
            "李老师": "moderator",
            "老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们先从自己的观察开始。"),
        SimpleNamespace(source="explorer", content="我先说一个身边的小例子。"),
        SimpleNamespace(source="moderator", content="豆苗同学，你也来说说自己的看法吧。"),
        SimpleNamespace(source="豆苗", content="老师，你说得对，我觉得标准答案有时太死板。"),
    ]

    assert selector(thread) == "skeptic"


def test_turn_scheduler_allows_explicit_human_question_to_teacher() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=12,
        display_name_to_agent={
            "老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们先从自己的观察开始。"),
        SimpleNamespace(source="explorer", content="我先说一个身边的小例子。"),
        SimpleNamespace(source="moderator", content="豆苗同学，你也来说说自己的看法吧。"),
        SimpleNamespace(source="豆苗", content="老师，你怎么看这个问题？"),
    ]

    assert selector(thread) == "moderator"


def test_turn_scheduler_stops_proactively_inviting_human_after_soft_cap() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("pavlov")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "巴甫洛夫": "pavlov",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="我们先从自由和纪律开始。"),
        SimpleNamespace(source="explorer", content="我觉得自由像打开地图。"),
        SimpleNamespace(source="豆苗", content="但地图也需要方向。"),
        SimpleNamespace(source="moderator", content="你这个提醒很关键。"),
        SimpleNamespace(source="pavlov", content="习惯会决定人怎么使用自由。"),
        SimpleNamespace(source="豆苗", content="所以规则不能完全消失。"),
        SimpleNamespace(source="moderator", content="规则和自由要一起看。"),
        SimpleNamespace(source="explorer", content="我更关心孩子会不会孤单。"),
        SimpleNamespace(source="豆苗", content="是啊，社交真的很重要。"),
        SimpleNamespace(source="moderator", content="你把问题抓到了中心。"),
        SimpleNamespace(source="pavlov", content="重复互动本身就是训练。"),
        SimpleNamespace(source="豆苗", content="所以学校像真实训练场。"),
        SimpleNamespace(source="moderator", content="这个比喻很稳。"),
        SimpleNamespace(source="explorer", content="但也许可以保留一点家庭弹性。"),
        SimpleNamespace(source="豆苗", content="我赞成周末保留弹性。"),
        SimpleNamespace(source="moderator", content="那我们继续往实施层面想。"),
        SimpleNamespace(source="pavlov", content="关键在于边界和节奏。"),
        SimpleNamespace(source="豆苗", content="我觉得五天学校两天家庭挺合适。"),
        SimpleNamespace(source="moderator", content="这个组合方案已经很具体了。"),
        SimpleNamespace(source="explorer", content="那接下来可以比较不同年龄段。"),
    ]

    assert selector(thread) == "pavlov"


def test_turn_scheduler_stops_reinviting_human_after_compact_target() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=40,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="我们先从自由和纪律开始。"),
        SimpleNamespace(source="explorer", content="我觉得自由像打开地图。"),
        SimpleNamespace(source="豆苗", content="但地图也需要方向。"),
        SimpleNamespace(source="moderator", content="你这个提醒很关键。"),
        SimpleNamespace(source="skeptic", content="习惯会决定人怎么使用自由。"),
        SimpleNamespace(source="explorer", content="我更关心孩子会不会孤单。"),
        SimpleNamespace(source="豆苗", content="是啊，社交真的很重要。"),
        SimpleNamespace(source="moderator", content="你把问题抓到了中心。"),
        SimpleNamespace(source="skeptic", content="重复互动本身就是训练。"),
        SimpleNamespace(source="explorer", content="可以保留一点家庭弹性。"),
        SimpleNamespace(source="豆苗", content="我赞成周末保留弹性。"),
        SimpleNamespace(source="moderator", content="那我们继续往实施层面想。"),
        SimpleNamespace(source="skeptic", content="关键在于边界和节奏。"),
        SimpleNamespace(source="explorer", content="不同年龄段可以不同安排。"),
        SimpleNamespace(source="豆苗", content="低年级需要更多陪伴。"),
        SimpleNamespace(source="moderator", content="这个年龄差异很重要。"),
        SimpleNamespace(source="skeptic", content="家长负担也得算进去。"),
        SimpleNamespace(source="explorer", content="还要安排同伴活动。"),
        SimpleNamespace(source="豆苗", content="可以固定每周一起做项目。"),
        SimpleNamespace(source="moderator", content="这已经有方案感了。"),
        SimpleNamespace(source="skeptic", content="项目也需要评价标准。"),
        SimpleNamespace(source="explorer", content="评价最好不只看分数。"),
        SimpleNamespace(source="豆苗", content="可以看作品和过程记录。"),
        SimpleNamespace(source="moderator", content="你把评价方式补上了。"),
        SimpleNamespace(source="skeptic", content="过程记录也可能变成形式主义。"),
        SimpleNamespace(source="explorer", content="那记录应该简单一点。"),
        SimpleNamespace(source="豆苗", content="每周只写三个重点就够。"),
        SimpleNamespace(source="moderator", content="这个约束很实用。"),
        SimpleNamespace(source="skeptic", content="还需要有人定期回看。"),
        SimpleNamespace(source="explorer", content="老师和家长可以轮流看。"),
    ]

    assert selector(thread) == "skeptic"


def test_turn_scheduler_relaxes_human_soft_cap_after_two_hand_raises() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
        get_human_engagement_level=lambda: 1,
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们讨论在家上学。"),
        SimpleNamespace(source="explorer", content="我担心同伴互动变少。"),
        SimpleNamespace(source="豆苗", content="我觉得如果有社团，也许还好。"),
        SimpleNamespace(source="moderator", content="你刚才说社团也许能补上互动，这个角度很好。"),
        SimpleNamespace(source="skeptic", content="可是在家上学不一定有固定伙伴。"),
        SimpleNamespace(source="豆苗", content="那就要设计固定的小组。"),
        SimpleNamespace(source="moderator", content="这个办法已经很像真实方案了。"),
        SimpleNamespace(source="explorer", content="而且小组最好长期稳定。"),
        SimpleNamespace(source="豆苗", content="对，还可以轮流当组长。"),
        SimpleNamespace(source="moderator", content="你把合作细节补得很具体。"),
        SimpleNamespace(source="skeptic", content="但有人可能还是会偷懒。"),
        SimpleNamespace(source="豆苗", content="那就让大家互相打分。"),
        SimpleNamespace(source="moderator", content="这已经进入规则设计了。"),
        SimpleNamespace(source="explorer", content="还可以让老师定期回看记录。"),
        SimpleNamespace(source="豆苗", content="我还想加一个家长反馈表。"),
        SimpleNamespace(source="moderator", content="这让方案更完整了。"),
        SimpleNamespace(source="skeptic", content="不过家长反馈也可能带偏压力。"),
        SimpleNamespace(source="豆苗", content="那反馈表就只写观察，不排名。"),
        SimpleNamespace(source="explorer", content="我赞成，少一点比较会更舒服。"),
        SimpleNamespace(source="skeptic", content="如果真这样，我觉得在家上学也不是完全不行。"),
        SimpleNamespace(source="explorer", content="那下一步就看老师愿不愿意定期看这些记录。"),
    ]

    assert selector(thread) == "moderator"


def test_turn_scheduler_delays_closing_after_two_hand_raises() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=10,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
        get_human_engagement_level=lambda: 1,
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们讨论在家上学。"),
        SimpleNamespace(source="explorer", content="我先想到同伴关系会变化。"),
        SimpleNamespace(source="豆苗", content="我觉得学习自由会更大。"),
        SimpleNamespace(source="skeptic", content="但自由也可能变成拖延。"),
        SimpleNamespace(source="explorer", content="所以要看有没有稳定节奏。"),
    ]

    assert selector(thread) == "moderator"


def test_turn_scheduler_keeps_three_hand_raise_extension_bounded() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    thread = [
        SimpleNamespace(source="moderator", content="今天我们讨论在家上学。"),
        SimpleNamespace(source="explorer", content="我先想到同伴关系会变化。"),
        SimpleNamespace(source="豆苗", content="我觉得学习自由会更大。"),
        SimpleNamespace(source="skeptic", content="但自由也可能变成拖延。"),
        SimpleNamespace(source="explorer", content="所以要看有没有稳定节奏。"),
        SimpleNamespace(source="豆苗", content="所以支持方式也得一起改。"),
        SimpleNamespace(source="skeptic", content="我担心家长会更累。"),
        SimpleNamespace(source="豆苗", content="那就要把家长任务拆小一点。"),
        SimpleNamespace(source="豆苗", content="还可以固定每周做一次回顾。"),
        SimpleNamespace(source="skeptic", content="也可能逼着大家重新设计作息。"),
        SimpleNamespace(source="explorer", content="最好连同伴活动也一起设计。"),
    ]

    level_one_team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=10,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
        get_human_engagement_level=lambda: 1,
    )
    level_two_team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=10,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
        get_human_engagement_level=lambda: 2,
    )

    assert level_one_team._selector_func(thread) == "moderator"
    assert level_two_team._selector_func(thread) == "moderator"


def test_turn_scheduler_prefers_peer_over_moderator_during_human_cooldown() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=12,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们讨论在家上学。"),
        SimpleNamespace(source="explorer", content="我先想到同伴关系会变化。"),
        SimpleNamespace(source="豆苗", content="我觉得学习自由会更大。"),
        SimpleNamespace(source="skeptic", content="但自由也可能变成拖延。"),
    ]

    assert selector(thread) == "explorer"


@pytest.mark.asyncio
async def test_request_interrupt_notifies_human_hand_raise_callback() -> None:
    hand_raises: list[str] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda _name: None,
        human_hand_raise_notifier=lambda name: hand_raises.append(name),
    )
    floor_manager.set_display_name_map({"moderator": "李老师", "豆苗": "豆苗"})
    floor_manager.current_speaker = "moderator"

    await floor_manager.request_interrupt("豆苗")

    assert hand_raises == ["豆苗"]


@pytest.mark.asyncio
async def test_request_interrupt_notifies_callback_with_request_id() -> None:
    callback_args: list[tuple[str, str, str, str]] = []

    async def _on_interrupt(
        interrupter: str,
        current_speaker: str,
        approved_by: str,
        request_id: str,
    ) -> None:
        callback_args.append((interrupter, current_speaker, approved_by, request_id))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda _name: None,
    )
    floor_manager.set_display_name_map({"moderator": "李老师", "豆苗": "豆苗"})
    floor_manager.current_speaker = "moderator"
    floor_manager.on_interrupt(_on_interrupt)

    await floor_manager.request_interrupt("豆苗", request_id="int-1")

    assert callback_args == [("豆苗", "moderator", "李老师", "int-1")]


@pytest.mark.asyncio
async def test_request_interrupt_is_ignored_when_human_turn_is_already_waiting() -> None:
    hand_raises: list[str] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda _name: None,
        human_hand_raise_notifier=lambda name: hand_raises.append(name),
    )
    floor_manager.set_display_name_map({"moderator": "李老师", "豆苗": "豆苗"})
    floor_manager.current_speaker = "豆苗"
    floor_manager.state = FloorState.HUMAN_TURN_WAITING

    await floor_manager.request_interrupt("豆苗")

    assert hand_raises == []
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING


def test_turn_scheduler_reasks_closing_question_after_single_new_follow_up() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent("moderator"),
        characters=[_agent("explorer"), _agent("skeptic")],
        humans=[_agent("豆苗")],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            "李老师": "moderator",
            "小探": "explorer",
            "小疑": "skeptic",
            "豆苗": "豆苗",
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source="moderator", content="今天我们从规则和自由开始聊。"),
        SimpleNamespace(source="explorer", content="我觉得规则像扶手。"),
        SimpleNamespace(source="豆苗", content="有时候扶手也会挡住路。"),
        SimpleNamespace(source="moderator", content="你刚才说扶手也会挡住路，这个观察很妙。"),
        SimpleNamespace(source="skeptic", content="那要看扶手是不是太多了。"),
        SimpleNamespace(
            source="moderator",
            content="收尾前，我想先问问大家，还有没有想补充的观点或想法？豆苗，如果你还有新发现，也可以继续说。",
        ),
        SimpleNamespace(source="豆苗", content="我还想补一句，扶手最好还能跟着人慢慢调整。"),
    ]

    assert selector(thread) == "moderator"


@pytest.mark.asyncio
async def test_floor_manager_ignores_duplicate_human_input_requested_for_same_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    floor_manager.current_speaker = "豆苗"
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager._pending_human_input_reason = "interrupt"

    first = await floor_manager._process_event(
        UserInputRequestedEvent(request_id="req-1", source="豆苗")
    )
    second = await floor_manager._process_event(
        UserInputRequestedEvent(request_id="req-2", source="豆苗")
    )

    assert first is not None
    assert first["event_type"] == "human_input_requested"
    assert first["data"]["speaker"] == "豆苗"
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING
    assert second is None


@pytest.mark.asyncio
async def test_floor_manager_ignores_duplicate_waiting_request_without_authorized_reason() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    floor_manager.current_speaker = "豆苗"
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager._pending_human_input_reason = "normal"
    floor_manager._last_human_input_requested_speaker = "豆苗"

    result = await floor_manager._make_human_input_requested_event(
        "豆苗",
        reason="human_input_requested_waiting",
    )

    assert result is None


@pytest.mark.asyncio
async def test_floor_manager_does_not_inherit_pending_authorization_for_non_waiting_reason() -> (
    None
):
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    floor_manager.current_speaker = "豆苗"
    floor_manager._pending_human_input_reason = "participant_designated_human"

    result = await floor_manager._make_human_input_requested_event(
        "豆苗",
        reason="normal",
    )

    assert result is None
    assert floor_manager.state != FloorState.HUMAN_TURN_WAITING


@pytest.mark.asyncio
async def test_floor_manager_ignores_stale_human_request_once_closing_begins() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    floor_manager.state = FloorState.CLOSING
    floor_manager._discussion_end_requested = True
    floor_manager._deferred_human_request_speaker = "豆苗"
    floor_manager._deferred_human_request_reason = "moderator_designated_human"

    result = await floor_manager._make_human_input_requested_event(
        "豆苗",
        reason="moderator_designated_human",
        clear_designation=False,
    )

    assert result is None
    assert floor_manager.state == FloorState.CLOSING
    assert floor_manager._deferred_human_request_speaker is None
    assert floor_manager._deferred_human_request_reason == ""


@pytest.mark.asyncio
async def test_floor_manager_clears_stale_designation_on_human_input_requested() -> None:
    designated_updates: list[str | None] = []

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.current_speaker = "豆苗"
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager._set_designated_speaker("豆苗")
    floor_manager._pending_human_input_reason = "interrupt"

    result = await floor_manager._process_event(
        UserInputRequestedEvent(request_id="req-clear-designation", source="豆苗")
    )

    assert result is not None
    assert result["event_type"] == "human_input_requested"
    assert designated_updates[-1] is None


@pytest.mark.asyncio
async def test_floor_manager_marks_interrupt_origin_on_human_input_requested() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "李老师", "豆苗": "豆苗"})
    floor_manager.current_speaker = "moderator"

    await floor_manager.request_interrupt("豆苗")
    floor_manager.current_speaker = "豆苗"

    duplicate_result = await floor_manager._process_event(
        UserInputRequestedEvent(request_id="req-interrupt", source="豆苗")
    )

    assert duplicate_result is None
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING
    assert floor_manager.current_speaker == "豆苗"


@pytest.mark.asyncio
async def test_submit_human_input_ignores_self_designation_target() -> None:
    clear_human_queues()
    designated_updates: list[str | None] = []

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "豆苗": "豆苗",
        }
    )

    await floor_manager.submit_human_input("豆苗", "我也想请豆苗再补充一下。")

    queue = get_human_queue("豆苗")
    assert await asyncio.wait_for(queue.get(), timeout=0.1) == "我也想请豆苗再补充一下。"
    assert designated_updates == [None]


@pytest.mark.asyncio
async def test_submit_human_input_explicit_end_request_triggers_teacher_closing() -> None:
    clear_human_queues()
    create_human_proxy("豆苗")
    emitted_messages: list[tuple[str, str, str]] = []

    async def capture_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager.on_message(capture_message)
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"
    floor_manager._speaker_message_count.update(
        {
            "moderator": 2,
            "explorer": 1,
            "豆苗": 2,
        }
    )
    floor_manager._recent_human_turn_summaries["豆苗"] = [
        "先比较了在家学习和到校学习的自由度",
        "后来又补充了自律需要家长配合",
    ]

    await floor_manager.submit_human_input("豆苗", "没有什么要说的了，我们结束吧。")

    queue = get_human_queue("豆苗")
    assert queue.empty()
    assert floor_manager._discussion_end_requested is True
    assert any(
        source == "豆苗" and "我们结束吧" in content for source, content, _ in emitted_messages
    )
    assert any(
        source == "moderator" and "豆苗" in content and "再见" in content
        for source, content, _ in emitted_messages
    )


@pytest.mark.asyncio
async def test_submit_human_skip_clears_stale_designation() -> None:
    clear_human_queues()
    designated_updates: list[str | None] = []

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="peacemaker")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager._set_designated_speaker("豆苗")
    floor_manager._expected_next_ai_speaker = "peacemaker"

    await floor_manager.submit_human_input("豆苗", "（跳过）")

    queue = get_human_queue("豆苗")
    assert await asyncio.wait_for(queue.get(), timeout=0.1) == "（跳过）"
    assert designated_updates[-1] is None
    assert floor_manager._expected_next_ai_speaker is None
    assert floor_manager._get_speaker_utterance_status("豆苗") == SpeakerUtteranceStatus.SKIPPED


@pytest.mark.asyncio
async def test_submit_human_input_requests_restart_without_external_wait_cancel() -> None:
    clear_human_queues()

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"

    wait_gate = asyncio.Event()
    pending_wait = asyncio.create_task(wait_gate.wait())
    floor_manager._active_stream_next_event_task = pending_wait

    try:
        await floor_manager.submit_human_input("豆苗", "我觉得幸福是和家人在一起。")

        assert floor_manager._stream_restart_requested is True
        assert floor_manager._active_stream_next_event_task is pending_wait
        assert pending_wait.cancelled() is False
        assert pending_wait.done() is False
    finally:
        pending_wait.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending_wait


@pytest.mark.asyncio
async def test_submit_human_input_marks_emoji_only_as_spoke_empty() -> None:
    clear_human_queues()

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )

    await floor_manager.submit_human_input("豆苗", "🙂🙂")

    queue = get_human_queue("豆苗")
    assert await asyncio.wait_for(queue.get(), timeout=0.1) == "（跳过）"
    assert floor_manager._get_speaker_utterance_status("豆苗") == SpeakerUtteranceStatus.SPOKE_EMPTY


@pytest.mark.asyncio
async def test_submit_human_input_ignores_reported_moderator_designation_mistake() -> None:
    clear_human_queues()
    designated_updates: list[str | None] = []

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="comedian")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "comedian": "可乐",
            "豆苗": "豆苗",
        }
    )

    text = "老师，我是豆苗，你叫我发言，又说请可乐发言，你搞错了。"
    await floor_manager.submit_human_input("豆苗", text)

    queue = get_human_queue("豆苗")
    assert await asyncio.wait_for(queue.get(), timeout=0.1) == text
    assert designated_updates == [None]


@pytest.mark.asyncio
async def test_floor_manager_keeps_explicit_non_human_invite_before_first_human() -> None:
    designated_updates: list[str | None] = []
    emitted_messages: list[tuple[str, str, str]] = []

    async def capture_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="explorer"),
            SimpleNamespace(name="skeptic"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "skeptic": "小疑",
            "豆苗": "豆苗",
        }
    )
    floor_manager.on_message(capture_message)
    floor_manager._speaker_message_count["explorer"] = 1
    floor_manager._speaker_message_count["skeptic"] = 1
    floor_manager._recent_display_speakers.extend(["小探", "小疑"])

    result = await floor_manager._process_event(
        TextMessage(
            source="moderator",
            content="小疑同学刚才的问题很有意思！那我想问问小探同学，你怎么看？",
        )
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert result["data"]["source"] == "moderator"
    assert emitted_messages[-1][0] == "moderator"
    assert "麦克风交给豆苗同学" not in emitted_messages[-1][1]
    assert "小探同学" in emitted_messages[-1][1]
    assert "你有什么想法" in emitted_messages[-1][1]
    assert designated_updates == ["explorer"]


def test_floor_manager_first_human_invite_uses_two_minute_limit_fallback() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager._discussion_started_mono = time.monotonic() - 121.0
    floor_manager.state = FloorState.AI_SPEAKING

    assert floor_manager._should_force_first_human_invite() is True


@pytest.mark.asyncio
async def test_floor_manager_blocks_wrong_ai_before_designated_ai_speaks() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="tagore"),
            SimpleNamespace(name="explorer"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "tagore": "罗宾德拉纳特·泰戈尔",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )

    moderator_turn = await floor_manager._process_event(
        TextMessage(
            source="moderator",
            content="我们请泰戈尔先生先说说他怎么看这个问题。",
        )
    )
    assert moderator_turn is not None

    blocked = await floor_manager._process_event(
        TextMessage(
            source="moderator",
            content="我觉得泰戈尔先生刚才说得特别好。",
        )
    )
    assert blocked is None

    tagore_turn = await floor_manager._process_event(
        TextMessage(
            source="tagore",
            content="自由不是逃离约束，而是在约束中生长。",
        )
    )
    assert tagore_turn is not None
    assert tagore_turn["event_type"] == "message"
    assert tagore_turn["data"]["source"] == "tagore"


@pytest.mark.asyncio
async def test_floor_manager_restarts_stream_on_wrong_expected_ai_message() -> None:
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="skeptic"),
            SimpleNamespace(name="explorer"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager._expected_next_ai_speaker = "skeptic"

    blocked = await floor_manager._process_event(
        TextMessage(source="explorer", content="我抢先说一句。")
    )

    assert blocked is None
    assert designated_updates == ["skeptic"]
    assert floor_manager._expected_next_ai_speaker == "skeptic"
    assert floor_manager._stream_restart_requested is True


@pytest.mark.asyncio
async def test_floor_manager_drops_duplicate_completed_message() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="confucius")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager.current_speaker = "moderator"
    floor_manager._speaker_message_count["moderator"] = 1

    first = await floor_manager._process_event(
        TextMessage(source="moderator", content="我们先听听孔子先生的想法。孔子先生，你怎么看？")
    )
    second = await floor_manager._process_event(
        TextMessage(source="moderator", content="我们先听听孔子先生的想法。孔子先生，你怎么看？")
    )

    assert first is not None
    assert first["event_type"] == "message"
    assert second is None


@pytest.mark.asyncio
async def test_floor_manager_drops_recent_duplicate_completed_message_with_intervening_turn() -> (
    None
):
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="confucius")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager.current_speaker = "moderator"
    floor_manager._speaker_message_count["moderator"] = 1

    first = await floor_manager._process_event(
        TextMessage(source="moderator", content="我们先听听孔子先生的想法。孔子先生，你怎么看？")
    )
    intervening = await floor_manager._process_event(
        TextMessage(source="moderator", content="请其他同学说说。")
    )
    repeated = await floor_manager._process_event(
        TextMessage(source="moderator", content="我们先听听孔子先生的想法。孔子先生，你怎么看？")
    )

    assert first is not None
    assert intervening is not None
    assert repeated is None


@pytest.mark.asyncio
async def test_submit_human_input_builds_redirect_guidance_for_off_topic_user_turn() -> None:
    clear_human_queues()
    guidance_memory = HumanResponseGuidanceMemory()
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        human_guidance_memory=guidance_memory,
    )
    floor_manager.set_display_name_map(
        {
            "moderator": "李老师",
            "explorer": "小探",
            "豆苗": "豆苗",
        }
    )
    floor_manager._current_topic = "在家上学\n\n请大家讨论在家上学和学校教育的差别。"
    floor_manager._recent_turn_summaries = [
        ("小探", "我更关心孩子会不会孤单。"),
    ]

    await floor_manager.submit_human_input("豆苗", "我昨晚吃了两块披萨，还想养小猫。")

    results = (await guidance_memory.query("")).results
    assert len(results) == 1
    payload = results[0].content
    assert payload["assessment"] == "off_topic"
    assert payload["speaker"] == "豆苗"
    assert payload["suggested_peer_name"] == "小探"
    assert payload["suggested_peer_summary"] == "我更关心孩子会不会孤单。"


@pytest.mark.asyncio
async def test_moderator_message_consumes_pending_human_redirect_guidance() -> None:
    guidance_memory = HumanResponseGuidanceMemory()
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
        human_guidance_memory=guidance_memory,
    )
    await guidance_memory.replace_guidance(
        [
            {
                "speaker": "豆苗",
                "summary": "我昨晚吃了两块披萨，还想养小猫。",
                "assessment": "off_topic",
            }
        ]
    )
    floor_manager._pending_human_guidance = True

    result = await floor_manager._process_event(
        TextMessage(source="moderator", content="我们先回到在家上学这个主题，再接着想一想。")
    )

    assert result is not None
    assert (await guidance_memory.query("")).results == []
    assert floor_manager._pending_human_guidance is False


@pytest.mark.asyncio
async def test_floor_manager_sets_current_human_before_waiting_callback() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    observed_speakers: list[str | None] = []

    async def _record_state_change(*_args) -> None:
        observed_speakers.append(floor_manager.current_speaker)

    floor_manager.on_state_change(_record_state_change)
    floor_manager.current_speaker = None
    floor_manager._pending_human_input_reason = "interrupt"

    result = await floor_manager._process_event(
        UserInputRequestedEvent(request_id="req-human", source="豆苗")
    )

    assert result is not None
    assert result["event_type"] == "human_input_requested"
    assert result["data"]["speaker"] == "豆苗"
    assert floor_manager.current_speaker == "豆苗"
    assert observed_speakers == ["豆苗"]


@pytest.mark.asyncio
async def test_floor_manager_rejects_illegal_state_transition() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )

    with pytest.raises(RuntimeError, match="Illegal floor state transition"):
        await floor_manager._set_state(FloorState.AI_SPEAKING, reason="bad_transition")


@pytest.mark.asyncio
async def test_floor_manager_makes_selecting_phase_explicit_before_ai_turn() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )
    observed_states: list[tuple[str, str]] = []

    async def _record_state_change(old_state, new_state, *_args) -> None:
        observed_states.append((old_state.value, new_state.value))

    floor_manager.on_state_change(_record_state_change)
    await floor_manager._set_state(FloorState.MODERATOR_OPENING, reason="discussion_start")

    result = await floor_manager._process_event(
        SelectSpeakerEvent(source="system", content=["moderator"])
    )

    assert result is not None
    assert result["event_type"] == "turn_change"
    assert observed_states == [
        ("init", "moderator_opening"),
        ("moderator_opening", "selecting_speaker"),
        ("selecting_speaker", "ai_speaking"),
    ]


@pytest.mark.asyncio
async def test_floor_manager_allows_third_blocked_closing_with_anomaly_marker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1, "豆苗": 0})
    floor_manager._recent_display_speakers = ["老师", "小探"]
    floor_manager.state = FloorState.AI_SPEAKING

    first = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，今天讨论就到这里，再见。")
    )
    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager.current_speaker = "moderator"
    floor_manager._last_human_input_requested_speaker = ""
    floor_manager._deferred_human_request_speaker = None
    floor_manager._deferred_human_request_reason = ""
    floor_manager._pending_human_input_reason = "normal"
    second = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，今天讨论就到这里，再见。")
    )
    floor_manager.state = FloorState.AI_SPEAKING
    floor_manager.current_speaker = "moderator"
    floor_manager._last_human_input_requested_speaker = ""
    floor_manager._deferred_human_request_speaker = None
    floor_manager._deferred_human_request_reason = ""
    floor_manager._pending_human_input_reason = "normal"
    third = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，今天讨论就到这里，再见。")
    )

    assert first is not None and first["event_type"] == "human_input_requested"
    assert second is not None and second["event_type"] == "human_input_requested"
    assert third is not None and "再见" in third["data"]["content"]
    gate = floor_manager._closing_gate_status()
    assert gate["closing_attempt_count"] == 3
    assert gate["forced_ready_due_to_attempt_limit"] is True
    assert gate["participation_insufficient"] is True


@pytest.mark.asyncio
async def test_human_progress_resets_blocked_closing_attempt_count() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="explorer")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({"moderator": "老师", "explorer": "小探", "豆苗": "豆苗"})
    floor_manager._speaker_message_count.update({"moderator": 1, "explorer": 1, "豆苗": 0})
    floor_manager._recent_display_speakers = ["老师", "小探"]
    floor_manager.state = FloorState.AI_SPEAKING

    _ = await floor_manager._process_event(
        TextMessage(source="moderator", content="同学们，今天讨论就到这里，再见。")
    )
    assert floor_manager._closing_attempt_count == 1

    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"
    floor_manager.state = FloorState.HUMAN_TURN_WAITING

    progress = await floor_manager._process_event(
        TextMessage(source="豆苗", content="我想再补充一个生活里的例子。")
    )

    assert progress is not None
    assert progress["event_type"] == "message"
    assert floor_manager._closing_attempt_count == 0


@pytest.mark.asyncio
async def test_floor_manager_leaves_waiting_state_after_human_message() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = "豆苗"
    floor_manager._last_human_input_requested_speaker = "豆苗"

    result = await floor_manager._process_event(
        TextMessage(source="豆苗", content="我想先回应一下刚才的问题。")
    )

    assert result is not None
    assert result["event_type"] == "message"
    assert floor_manager.state == FloorState.SELECTING_SPEAKER
    assert floor_manager._last_human_input_requested_speaker == ""


@pytest.mark.asyncio
async def test_floor_manager_forces_teacher_to_open_if_selector_returns_wrong_first_speaker() -> (
    None
):
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name="moderator"), SimpleNamespace(name="pavlov")],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=SimpleNamespace(),
    )

    result = await floor_manager._process_event(
        SelectSpeakerEvent(source="system", content=["pavlov"])
    )

    assert result is not None
    assert result["event_type"] == "turn_change"
    assert result["data"]["speaker"] == "moderator"
    assert floor_manager.current_speaker == "moderator"


def test_thinker_label_prefers_stable_name_field() -> None:
    thinker = {
        "name": "巴甫洛夫",
        "display_name": "心理学家巴甫洛夫",
    }

    assert thinker_label("pavlov", thinker) == "巴甫洛夫"


@pytest.mark.asyncio
async def test_run_discussion_maps_stream_source_to_display_name() -> None:
    recorded = []

    async def _send_event(event_type: str, data: dict) -> bool:
        recorded.append((event_type, data))
        return True

    floor_manager = _DiscussionFloorManagerStub(
        [
            {
                "event_type": "stream",
                "data": {
                    "source": "moderator",
                    "content": "今天咱们先把问题看清楚。",
                    "tts_segments": ["今天咱们先把问题看清楚。"],
                },
            },
            {
                "event_type": "stream",
                "data": {
                    "source": "pavlov",
                    "content": "我想从习惯形成的角度看看。",
                },
            },
            {
                "event_type": "ended",
                "data": {"session_id": "session-test"},
            },
        ]
    )

    result = await _run_discussion(
        _send_event,
        floor_manager,
        "测试话题",
        agent_display_map={
            "moderator": "老师",
            "pavlov": "巴甫洛夫",
        },
    )

    assert result == "completed"
    assert recorded[0] == (
        "stream",
        {
            "source": "老师",
            "agent_source": "moderator",
            "content": "今天咱们先把问题看清楚。",
            "tts_segments": ["今天咱们先把问题看清楚。"],
        },
    )
    assert recorded[1] == (
        "stream",
        {
            "source": "巴甫洛夫",
            "agent_source": "pavlov",
            "content": "我想从习惯形成的角度看看。",
        },
    )


@pytest.mark.asyncio
async def test_run_discussion_observer_mode_auto_skips_normal_human_turn() -> None:
    recorded = []

    async def _send_event(event_type: str, data: dict) -> bool:
        recorded.append((event_type, data))
        return True

    floor_manager = _DiscussionFloorManagerStub(
        [
            {
                "event_type": "human_input_requested",
                "data": {
                    "speaker": "豆苗",
                    "reason": "normal",
                },
            },
            {
                "event_type": "ended",
                "data": {"session_id": "session-test"},
            },
        ]
    )

    result = await _run_discussion(
        _send_event,
        floor_manager,
        "测试话题",
        observer_mode=True,
    )

    assert result == "completed"
    assert recorded[0] == (
        "human_input_requested",
        {
            "speaker": "豆苗",
            "reason": "normal",
        },
    )
    assert floor_manager.submitted_inputs == [("豆苗", "（旁听）")]


@pytest.mark.asyncio
async def test_run_discussion_observer_mode_keeps_interrupt_human_turn() -> None:
    recorded = []

    async def _send_event(event_type: str, data: dict) -> bool:
        recorded.append((event_type, data))
        return True

    floor_manager = _DiscussionFloorManagerStub(
        [
            {
                "event_type": "human_input_requested",
                "data": {
                    "speaker": "豆苗",
                    "reason": "interrupt",
                },
            },
            {
                "event_type": "ended",
                "data": {"session_id": "session-test"},
            },
        ]
    )

    result = await _run_discussion(
        _send_event,
        floor_manager,
        "测试话题",
        observer_mode=True,
    )

    assert result == "completed"
    assert recorded[0] == (
        "human_input_requested",
        {
            "speaker": "豆苗",
            "reason": "interrupt",
        },
    )
    assert floor_manager.submitted_inputs == []
