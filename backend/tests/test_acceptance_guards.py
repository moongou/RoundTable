from __future__ import annotations

import asyncio
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
from app.core.llm_errors import describe_model_error
from app.core.meeting_history import _build_script_line, _export_line_record
from app.core.rolling_summary_memory import HumanResponseGuidanceMemory, RollingSummaryMemory
from app.core.thinkers import thinker_label
from app.core.turn_scheduler import create_discussion_team
from app.core.turn_scheduler import parse_speaker_designation
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
        self._events = events

    def run_stream(self, *_, **__):
        async def _stream():
            for event in self._events:
                yield event

        return _stream()


class _SafetyFilterStub:
    async def filter_or_rewrite(self, content: str) -> str:
        return content

    async def check_human_input(self, _content: str) -> tuple[bool, str]:
        return True, ''


class _DiscussionFloorManagerStub:
    def __init__(self, events) -> None:
        self._events = events
        self.submitted_inputs = []

    async def run(self, _topic):
        for event in self._events:
            yield event

    async def submit_human_input(self, name: str, text: str) -> None:
        self.submitted_inputs.append((name, text))


@pytest.mark.asyncio
async def test_rolling_summary_memory_injects_recent_three_turns() -> None:
    memory = RollingSummaryMemory()
    await memory.replace_turn_summaries(
        [
            ('李老师', '先把问题背景说清楚'),
            ('小探', '我更想从好奇心出发看这件事'),
            ('孔子先生', '先立住做人的根，再谈方法'),
            ('豆苗', '我觉得规则也要留一点弹性'),
        ]
    )
    model_context = _ModelContextStub()

    await memory.update_context(model_context)

    assert len(model_context.messages) == 1
    content = model_context.messages[0].content
    assert '最近3轮发言摘要' in content
    assert '李老师' not in content
    assert '小探' in content
    assert '孔子先生' in content
    assert '豆苗' in content


@pytest.mark.asyncio
async def test_floor_manager_updates_recent_turn_memory_and_skips_jump_marker() -> None:
    memory = RollingSummaryMemory()
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=SimpleNamespace(),
        summary_memory=memory,
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            '豆苗': '豆苗',
        }
    )

    await floor_manager._record_turn_summary('moderator', '同学们，我们先把问题看清楚。')
    await floor_manager._record_turn_summary('豆苗', '（跳过）')
    await floor_manager._record_turn_summary('explorer', '我觉得先试试看，再慢慢改。')

    results = (await memory.query('')).results
    assert len(results) == 2
    assert results[0].content['speaker'] == '李老师'
    assert results[1].content['speaker'] == '小探'


def test_describe_model_error_cloudflare_tunnel_is_actionable_without_traceback() -> None:
    info = describe_model_error(
        "InternalServerError: Error code: 530 - {'error': {'message': "
        "'The host is configured as a Cloudflare Tunnel, but Cloudflare is currently "
        "unable to reach it.', 'type': 'bad_response_status_code'}}\n"
        "Traceback:\n  File '/tmp/site-packages/openai/_base_client.py', line 1"
    )

    assert info.kind == 'provider_unreachable'
    assert info.recoverable is True
    assert 'Cloudflare Tunnel' in info.message
    assert 'Base URL' in info.message
    assert 'Traceback' not in info.technical_detail


def test_describe_model_error_token_and_context_limits_are_specific() -> None:
    quota = describe_model_error(
        "RateLimitError: insufficient_quota: You exceeded your current quota"
    )
    context = describe_model_error(
        "BadRequestError: context_length_exceeded: maximum context length is 8192 tokens"
    )

    assert quota.kind == 'quota_or_tokens_exhausted'
    assert '额度不足' in quota.message or 'Token 已用尽' in quota.message
    assert context.kind == 'context_length_exceeded'
    assert '上下文长度不足' in context.message


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
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )

    events = [event async for event in floor_manager.run('测试话题')]
    api_error = next(event for event in events if event['event_type'] == 'api_error')

    assert api_error['data']['kind'] == 'provider_unreachable'
    assert 'Cloudflare Tunnel' in api_error['data']['message']
    assert 'Traceback' not in api_error['data']['message']
    assert 'Traceback' not in api_error['data'].get('technical_detail', '')


@pytest.mark.asyncio
async def test_human_response_guidance_memory_injects_redirect_hint() -> None:
    memory = HumanResponseGuidanceMemory()
    await memory.replace_guidance(
        [
            {
                'speaker': '豆苗',
                'summary': '我昨晚吃了两块披萨，还想养小猫。',
                'assessment': 'off_topic',
                'topic_focus': '在家上学',
                'suggested_peer_name': '小探',
                'suggested_peer_summary': '我更关心孩子会不会孤单。',
            }
        ]
    )
    model_context = _ModelContextStub()

    await memory.update_context(model_context)

    assert len(model_context.messages) == 1
    content = model_context.messages[0].content
    assert '真人学生回应提醒' in content
    assert '温和把话题拉回“在家上学”' in content
    assert '小探刚才提到的“我更关心孩子会不会孤单。”' in content


def test_floor_manager_pause_gate_calls_team_pause_and_resume() -> None:
    team = _TeamStub()
    floor_manager = FloorManager(
        team=team,
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
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


def test_sanitize_all_references_rewrites_first_turn_and_self_reference() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            '豆苗': '豆苗',
        }
    )

    first_turn = floor_manager._sanitize_all_references(
        'explorer',
        '上一位同学说得对，我也觉得要先试一试。',
    )
    assert '上一位同学' not in first_turn

    floor_manager._recent_display_speakers = ['豆苗']
    self_reference = floor_manager._sanitize_all_references(
        'explorer',
        '小探说得对，我觉得应该继续。',
    )
    assert '小探说得对' not in self_reference


def test_sanitize_all_references_removes_unspoken_direct_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=SimpleNamespace(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            '豆苗': '豆苗',
        }
    )

    floor_manager._recent_display_speakers = ['小探']
    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '豆苗同学，你刚才说“像用铅笔画身高线”很有意思。小探同学，你怎么看？',
    )

    assert sanitized == '小探同学，你怎么看？'


def test_sanitize_all_references_rewrites_unspoken_past_attribution_clause() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            '豆苗': '豆苗',
        }
    )

    floor_manager._recent_display_speakers = ['李老师', '小探']
    sanitized = floor_manager._sanitize_all_references(
        'explorer',
        '豆苗刚才讲到的这个角度，也提醒我们先别急着下结论。',
    )

    assert '豆苗' not in sanitized
    assert '有同学刚才讲到的这个角度' in sanitized


def test_sanitize_all_references_neutralizes_unspoken_short_attribution_verb() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name='moderator'),
            SimpleNamespace(name='questioner'),
            SimpleNamespace(name='pragmatist'),
        ],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'questioner': '小思',
            'pragmatist': '小行',
            '豆苗': '豆苗',
        }
    )

    floor_manager._recent_display_speakers = ['李老师', '小行']
    floor_manager._speaker_message_count.update(
        {
            'moderator': 1,
            'pragmatist': 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '然后小思同学提了个特别重要的问题——安全和堵路。',
    )

    assert '小思同学提了' not in sanitized
    assert '刚才有同学提了个特别重要的问题' in sanitized


def test_sanitize_all_references_rewrites_unspoken_reminder_praise_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name='moderator'),
            SimpleNamespace(name='questioner'),
            SimpleNamespace(name='pragmatist'),
        ],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'questioner': '小思',
            'pragmatist': '小行',
            '豆苗': '豆苗',
        }
    )

    floor_manager._recent_display_speakers = ['李老师', '小思']
    floor_manager._speaker_message_count.update(
        {
            'moderator': 1,
            'questioner': 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '小行同学提醒得也很到位，安全确实不能忽视。',
    )

    assert '小行同学提醒得' not in sanitized
    assert '小思同学，你提醒得也很到位' in sanitized


def test_sanitize_all_references_blocks_moderator_roleplaying_student() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name='moderator'),
            SimpleNamespace(name='pragmatist'),
        ],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'pragmatist': '小行',
            '豆苗': '豆苗',
        }
    )

    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '（小行同学思考片刻，认真地说）老师，我觉得可以这样：让开店的人和摆摊的人一起开会商量！',
    )
    continuation = floor_manager._sanitize_all_references(
        'moderator',
        '比如超市前面200米不能摆摊，菜市场门口可以摆。',
    )

    assert sanitized == '请小行同学发言。'
    assert continuation == ''


def test_sanitize_all_references_rewrites_unspoken_named_challenge_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            'skeptic': '小思',
            '豆苗': '豆苗',
        }
    )

    floor_manager._recent_display_speakers = ['李老师', '小探', '小思']
    floor_manager._speaker_message_count.update(
        {
            'moderator': 1,
            'explorer': 1,
            'skeptic': 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '小思同学，豆苗同学对于“树木是否真的愿意被砍伐”提出了挑战，你怎么看这个观点呢？请小思同学发言。',
    )

    assert '豆苗' not in sanitized
    assert '小思同学，你刚才对于“树木是否真的愿意被砍伐”提出了挑战' in sanitized


def test_sanitize_all_references_drops_unspoken_name_from_joint_attribution() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            'skeptic': '小思',
            '豆苗': '豆苗',
        }
    )

    floor_manager._recent_display_speakers = ['李老师', '小探', '小思']
    floor_manager._speaker_message_count.update(
        {
            'moderator': 1,
            'explorer': 1,
            'skeptic': 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        'explorer',
        '（拍手）豆苗和小思说的太犀利了，这就像是问我“我不爱吃的胡萝卜，是不是宁愿烂在土里也不想变成我的午餐”一样！',
    )

    assert '豆苗' not in sanitized
    assert '小思说的太犀利了' in sanitized


def test_sanitize_all_references_rewrites_unspoken_named_idea_summary_to_last_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='dreamer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'dreamer': '小想',
            '豆苗': '豆苗',
        }
    )

    floor_manager._recent_display_speakers = ['李老师', '小想']
    floor_manager._speaker_message_count.update(
        {
            'moderator': 1,
            'dreamer': 1,
        }
    )
    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '豆苗同学，你提出的“让树木成为教室的‘插班生’”这个想法太奇妙了！通过大家今天的讨论，再到豆苗同学提出的这种“森林学校”构想，老师看到了大家非常深刻的思考。',
    )

    assert '豆苗' not in sanitized
    assert '刚才小想提出的“让树木成为教室的‘插班生’”这个想法太奇妙了' in sanitized
    assert '再到刚才小想提出的这种“森林学校”构想' in sanitized


def test_sanitize_all_references_rewrites_named_quote_to_actual_owner() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            'skeptic': '小思',
            '豆苗': '豆苗',
        }
    )
    floor_manager._recent_reference_quotes = [
        ('小探', '如果这种互相猜忌变成了一场丢沙包比赛，这场比赛真的只是为了比谁准吗？'),
        ('小思', '仅仅因为觉得对方有，就能作为动手的证据吗？'),
    ]
    floor_manager._speaker_message_count['moderator'] = 1
    floor_manager._recent_display_speakers = ['李老师', '小思']

    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '豆苗同学，你刚才说“仅仅因为觉得对方有，就能作为动手的证据吗”这个问题，真的是一针见血。',
    )

    assert '豆苗同学' not in sanitized
    assert '小思同学，你刚才说“仅仅因为觉得对方有，就能作为动手的证据吗”' in sanitized


def test_sanitize_all_references_rewrites_cross_sentence_quote_followups_to_real_speakers() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            'skeptic': '小思',
            'pragmatist': '小行',
            '豆苗': '豆苗',
        }
    )
    floor_manager._recent_reference_quotes = [
        ('豆苗', '国际政治就是谁的拳头大，谁说了算。'),
        ('小思', '规则到底是靠大家自觉，还是靠某种更厉害的力量在背后盯着才有效呢？'),
        ('小行', '谁违规就扣小红花或者限制课间活动，大家总得掂量掂量吧。'),
    ]
    floor_manager._speaker_message_count['moderator'] = 1
    floor_manager._recent_display_speakers = ['豆苗', '小思', '小行']

    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '豆苗同学，你刚才说“规则到底是靠大家自觉，还是靠某种更厉害的力量在背后盯着”，这个问题问得太深刻了！你提出的“违规扣分”的想法，确实像给国际关系装上了一个“值日轮换表”和“惩罚机制”。',
    )

    assert '豆苗同学' not in sanitized
    assert '小思同学，你刚才说“规则到底是靠大家自觉，还是靠某种更厉害的力量在背后盯着”' in sanitized
    assert '小行提出的“违规扣分”' in sanitized


def test_sanitize_all_references_moderator_quote_whitelist_drops_untraceable_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            '豆苗': '豆苗',
        }
    )
    floor_manager._recent_display_speakers = ['李老师', '小探']
    floor_manager._speaker_message_count.update({'moderator': 1, 'explorer': 1})
    floor_manager._recent_reference_quotes = [
        ('小探', '演员要先会观察生活，再去尝试不同角色。'),
    ]

    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '小探同学，你说的“情绪海绵”这个比喻很有意思。',
    )

    assert '情绪海绵' not in sanitized
    assert '这个点' in sanitized


def test_sanitize_all_references_moderator_quote_whitelist_keeps_traceable_quote() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            '豆苗': '豆苗',
        }
    )
    floor_manager._recent_display_speakers = ['李老师', '小探']
    floor_manager._speaker_message_count.update({'moderator': 1, 'explorer': 1})
    floor_manager._recent_reference_quotes = [
        ('小探', '演员要先会观察生活，再去尝试不同角色。'),
    ]

    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '小探同学，你刚才说“演员要先会观察生活，再去尝试不同角色”这个观点很扎实。',
    )

    assert '演员要先会观察生活，再去尝试不同角色' in sanitized


@pytest.mark.asyncio
async def test_skip_turn_is_not_tracked_as_spoken_reference() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name='moderator'),
            SimpleNamespace(name='explorer'),
            SimpleNamespace(name='skeptic'),
        ],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            'skeptic': '小疑',
            '豆苗': '豆苗',
        }
    )

    await floor_manager._process_event(
        TextMessage(source='explorer', content='我觉得可以先看分数能量出什么。')
    )
    await floor_manager._process_event(TextMessage(source='豆苗', content='（跳过）'))
    await floor_manager._process_event(
        TextMessage(source='skeptic', content='我更关心分数会不会受状态影响。')
    )

    assert floor_manager._recent_display_speakers == ['小探', '小疑']

    sanitized = floor_manager._sanitize_all_references(
        'moderator',
        '刚才豆苗和小疑都分享了他们的想法。你对这个问题怎么看？',
    )

    assert '豆苗' not in sanitized

    attribution = floor_manager._sanitize_all_references(
        'moderator',
        '你觉得豆苗这个“站歪了”的比喻有道理吗？',
    )

    assert '豆苗这个' not in attribution


@pytest.mark.asyncio
async def test_human_proxy_queues_are_session_scoped_and_agent_alias_aware() -> None:
    clear_human_queues()
    agent_a = create_human_proxy('豆苗', session_scope='session-a')
    agent_b = create_human_proxy('豆苗', session_scope='session-b')

    queue_a = get_human_queue('豆苗', session_scope='session-a')
    queue_b = get_human_queue('豆苗', session_scope='session-b')
    assert queue_a is not queue_b

    await put_human_input(agent_a.name, '第一条', session_scope='session-a')
    await put_human_input(agent_b.name, '第二条', session_scope='session-b')

    assert await asyncio.wait_for(queue_a.get(), timeout=0.1) == '第一条'
    assert await asyncio.wait_for(queue_b.get(), timeout=0.1) == '第二条'

    clear_human_queues(session_scope='session-a')

    with pytest.raises(KeyError):
        get_human_queue('豆苗', session_scope='session-a')
    assert get_human_queue('豆苗', session_scope='session-b') is queue_b


@pytest.mark.asyncio
async def test_human_proxy_waits_for_real_input_instead_of_auto_observer_fallback() -> None:
    clear_human_queues()
    session_scope = 'manual-human-turn'
    create_human_proxy('豆苗', session_scope=session_scope)
    input_func = make_human_input_func('豆苗', timeout=0.01, session_scope=session_scope)
    token = CancellationToken()

    task = asyncio.create_task(input_func('请发言', token))
    await asyncio.sleep(0.05)

    assert not task.done()

    await put_human_input('豆苗', '我想先从自己的经历说起。', session_scope=session_scope)

    assert await asyncio.wait_for(task, timeout=0.2) == '我想先从自己的经历说起。'


@pytest.mark.asyncio
async def test_watchdog_human_turn_only_reminds_and_does_not_enqueue_skip() -> None:
    clear_human_queues()
    session_scope = 'watchdog-manual-skip'
    create_human_proxy('豆苗', session_scope=session_scope)
    queue = get_human_queue('豆苗', session_scope=session_scope)
    messages: list[tuple[str, str, str]] = []

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=SimpleNamespace(),
        human_queue_scope=session_scope,
    )
    floor_manager.set_display_name_map({'moderator': '李老师', '豆苗': '豆苗'})

    async def _collect_message(source: str, content: str, msg_type: str) -> None:
        messages.append((source, content, msg_type))

    floor_manager.on_message(_collect_message)
    floor_manager.current_speaker = '豆苗'
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
    assert any('系统不会替你跳过' in content for _, content, _ in messages)


def test_floor_manager_stream_sentence_splitter_keeps_quotes_and_tail() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=SimpleNamespace(),
    )

    segments, remainder = floor_manager._drain_complete_stream_sentences('“先想一想。”然后再回答')

    assert segments == ['“先想一想。”']
    assert remainder == '然后再回答'


@pytest.mark.asyncio
async def test_floor_manager_streaming_tts_only_leaves_final_tail_for_message() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )

    stream_event = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source='explorer',
            content='先看规则。再想',
        )
    )
    message_event = await floor_manager._process_event(
        TextMessage(source='explorer', content='先看规则。再想一想。')
    )

    assert stream_event is not None
    assert stream_event['event_type'] == 'stream'
    assert stream_event['data']['tts_segments'] == ['先看规则。']

    assert message_event is not None
    assert message_event['event_type'] == 'message'
    assert message_event['data']['content'] == '先看规则。再想一想。'
    assert message_event['data']['tts_text'] == '再想一想。'


@pytest.mark.asyncio
async def test_floor_manager_drops_meta_reasoning_stream_segments() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )

    stream_event = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source='moderator',
            content='<think>用户现在需要我扮演老师，先复述再点评。</think>。',
        )
    )
    message_event = await floor_manager._process_event(
        TextMessage(source='moderator', content='同学们，我们先一起梳理一下这个问题。')
    )

    assert stream_event is None
    assert message_event is not None
    assert message_event['event_type'] == 'message'
    assert message_event['data']['content'] == '同学们，我们先一起梳理一下这个问题。'
    assert message_event['data']['tts_text'] == '同学们，我们先一起梳理一下这个问题。'


@pytest.mark.asyncio
async def test_floor_manager_meta_stream_does_not_break_clean_stream_tail() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )

    meta_event = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source='explorer',
            content='不对，按照之前的设定，我应该先复述用户输入。',
        )
    )
    clean_stream_event = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source='explorer',
            content='先看规则。再想',
        )
    )
    message_event = await floor_manager._process_event(
        TextMessage(source='explorer', content='先看规则。再想一想。')
    )

    assert meta_event is None
    assert clean_stream_event is not None
    assert clean_stream_event['event_type'] == 'stream'
    assert clean_stream_event['data']['tts_segments'] == ['先看规则。']
    assert message_event is not None
    assert message_event['data']['tts_text'] == '再想一想。'


def test_floor_manager_streaming_tail_drops_full_repeat_when_prefix_not_exact_match() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )

    floor_manager._streaming_emitted_raw_prefix['explorer'] = '先看规则。再想一想。'
    floor_manager._streaming_buffer['explorer'] = ''

    had_streamed, tail = floor_manager._pop_streaming_message_tail(
        'explorer',
        '先看规则！再想一想。',
    )

    assert had_streamed is True
    assert tail == ''


@pytest.mark.asyncio
async def test_floor_manager_drops_duplicate_stream_sentences_for_same_turn() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )

    first_stream = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source='moderator',
            content='先看规则。',
        )
    )
    duplicate_stream = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source='moderator',
            content='先看规则。再想一',
        )
    )
    message_event = await floor_manager._process_event(
        TextMessage(source='moderator', content='先看规则。再想一想。')
    )

    assert first_stream is not None
    assert first_stream['data']['tts_segments'] == ['先看规则。']
    assert duplicate_stream is None
    assert message_event is not None
    assert message_event['data']['tts_text'] == '再想一想。'


def test_floor_manager_strips_meta_reasoning_from_text_message_content() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            '豆苗': '豆苗',
        }
    )

    cleaned = floor_manager._strip_meta_reasoning_text(
        '嗯，大家好，我是李老师。\n'
        '- **绝不**提前引用、评价或编造任何未发生发言的内容\n'
        '🌟 **等待系统触发**：请静候，让小探同学的声音第一个响起！\n'
        '（/me 等待系统触发“请小疑同学发言”提示，绝不提前编造）'
    )

    assert cleaned == '嗯，大家好，我是李老师。'


def test_floor_manager_strips_english_meta_reasoning_from_stream_text() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='comedian')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )

    cleaned = floor_manager._strip_meta_reasoning_text(
        'I should provide an answer in the style of 可乐 for the first time!\n'
        '我觉得标准答案像鞋带的第一个结。'
    )

    assert cleaned == '我觉得标准答案像鞋带的第一个结。'


def test_floor_manager_rewrites_moderator_teacher_student_misattribution() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='optimist')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '老师',
            'optimist': '小明',
            '豆苗': '豆苗',
        }
    )
    floor_manager._speaker_message_count['moderator'] = 1
    floor_manager._speaker_message_count['optimist'] = 1
    floor_manager._recent_display_speakers = ['小明']
    floor_manager._recent_reference_quotes = [
        ('小明', '创新不是故意离开答案很远，而是在站稳以后，勇敢多迈半步'),
    ]

    cleaned = floor_manager._sanitize_all_references(
        'moderator',
        '老师同学，你刚才说的“创新不是故意离开答案很远，而是在站稳以后，勇敢多迈半步”我特别喜欢。',
    )

    assert '老师同学' not in cleaned
    assert cleaned.startswith('小明同学，你刚才说的')


def test_floor_manager_neutralizes_repeated_self_invitation() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='optimist')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '老师',
            'optimist': '小明',
            '豆苗': '豆苗',
        }
    )
    floor_manager._speaker_message_count['moderator'] = 1
    floor_manager._speaker_message_count['optimist'] = 1
    floor_manager._recent_display_speakers = ['小明']

    cleaned = floor_manager._sanitize_all_references(
        'moderator',
        '小明同学，你刚才说“小鸭船”很有趣；小明同学，你怎么看小明同学说的“小鸭船”和“只描线”？',
    )

    assert '小明同学，你怎么看小明同学' not in cleaned
    assert '请其他同学说说' in cleaned
    assert parse_speaker_designation(cleaned, ['老师', '小明', '豆苗']) is None


def test_floor_manager_removes_unspoken_name_from_object_reference() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='optimist')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '老师',
            'optimist': '小明',
            '豆苗': '豆苗',
        }
    )
    floor_manager._speaker_message_count['moderator'] = 1
    floor_manager._speaker_message_count['optimist'] = 1
    floor_manager._recent_display_speakers = ['小明']

    cleaned = floor_manager._sanitize_all_references(
        'moderator',
        '请小明同学说说，你怎么看豆苗同学这只“风筝”呢？',
    )

    assert '豆苗同学这只' not in cleaned
    assert '请小明同学说说' not in cleaned
    assert '这只“风筝”' in cleaned


def test_floor_manager_rewrites_unspoken_possessive_quote_from_session_six() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='skeptic')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '老师',
            'skeptic': '小疑',
            '豆苗': '豆苗',
        }
    )
    floor_manager._speaker_message_count.update({'moderator': 1, 'skeptic': 1})
    floor_manager._recent_display_speakers = ['老师', '小疑']

    cleaned = floor_manager._sanitize_all_references(
        'moderator',
        '好，那小疑同学，豆苗同学的“15分钟限量版”玩法，你怎么看？',
    )

    assert '豆苗同学的“15分钟限量版”玩法' not in cleaned
    assert '小疑同学，你刚才提出的“15分钟限量版”玩法' in cleaned


def test_meeting_history_script_keeps_agent_debug_fields() -> None:
    message_line = _build_script_line(
        'outbound',
        'message',
        {
            'source': '老师',
            'agent_source': 'moderator',
            'content': '豆苗同学，你来说说。',
            'msg_type': 'text',
        },
        timestamp='2026-04-28T00:00:00Z',
        event_seq=7,
    )
    assert message_line is not None
    assert message_line['agent_source'] == 'moderator'

    request_line = _build_script_line(
        'outbound',
        'human_input_requested',
        {
            'speaker': '豆苗',
            'agent_speaker': 'u8c46u82d7',
            'reason': 'moderator_designated_human',
            'request_id': 'req-1',
            'state': 'human_turn_waiting',
        },
        timestamp='2026-04-28T00:00:01Z',
        event_seq=8,
    )
    assert request_line is not None
    assert request_line['speaker'] == '豆苗'
    assert request_line['agent_speaker'] == 'u8c46u82d7'
    assert request_line['request_reason'] == 'moderator_designated_human'
    assert request_line['request_id'] == 'req-1'
    assert request_line['request_state'] == 'human_turn_waiting'

    human_input_line = _build_script_line(
        'inbound',
        'human_input',
        {
            'speaker': '豆苗',
            'content': '我来补充一个例子。',
            'request_wait_ms': 932,
            'request_id': 'req-1',
        },
        timestamp='2026-04-28T00:00:02Z',
        event_seq=None,
    )
    assert human_input_line is not None
    assert human_input_line['request_wait_ms'] == 932
    assert human_input_line['request_id'] == 'req-1'

    exported = _export_line_record(request_line, index=1)
    assert exported['agent_speaker'] == 'u8c46u82d7'
    assert exported['request_reason'] == 'moderator_designated_human'


def test_floor_manager_rewrites_teacher_self_quote_to_real_owner_from_session_seven() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name='moderator'),
            SimpleNamespace(name='comedian'),
            SimpleNamespace(name='empath'),
        ],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '老师',
            'comedian': '可乐',
            'empath': '小爱',
            '豆苗': '豆苗',
        }
    )
    floor_manager._speaker_message_count.update(
        {'moderator': 2, 'comedian': 2, 'empath': 1}
    )
    floor_manager._recent_display_speakers = ['可乐', '小爱', '可乐']
    floor_manager._recent_reference_quotes = [
        (
            '小爱',
            '老人家参与教育最特别的地方，就是他们总能把硬邦邦的知识裹上一层糖衣。这两种爱，其实一个都不能少呢！',
        )
    ]

    cleaned = floor_manager._sanitize_all_references(
        'moderator',
        '哇，可乐同学，你这段话说得太精彩了！老师刚才说“把硬邦邦的知识裹上一层糖衣”，这个比喻我特别喜欢——还有你提到“两种爱一个都不能少”，这个总结特别有温度！',
    )

    assert '老师刚才说' not in cleaned
    assert '还有你提到' not in cleaned
    assert '小爱刚才说“把硬邦邦的知识裹上一层糖衣”' in cleaned
    assert '小爱提到“两种爱一个都不能少”' in cleaned


@pytest.mark.asyncio
async def test_floor_manager_requests_human_input_immediately_when_moderator_invites_human() -> None:
    designated_updates: list[str | None] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='skeptic')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '老师',
            'skeptic': '小疑',
            '豆苗': '豆苗',
        }
    )

    result = await floor_manager._process_event(
        TextMessage(source='moderator', content='好，我们先请豆苗同学来说说看，豆苗同学，你是什么想法？')
    )

    assert result is not None
    assert result['event_type'] == 'human_input_requested'
    assert result['data']['speaker'] == '豆苗'
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING
    assert designated_updates == ['豆苗']


@pytest.mark.asyncio
async def test_floor_manager_suppresses_ai_inserted_while_human_is_waiting() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='skeptic')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager.current_speaker = '豆苗'

    stream_result = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source='skeptic', content='我先替豆苗说一句。')
    )
    message_result = await floor_manager._process_event(
        TextMessage(source='skeptic', content='我先替豆苗说一句。')
    )

    assert stream_result is None
    assert message_result is None


@pytest.mark.asyncio
async def test_floor_manager_drops_repeated_non_moderator_self_reply() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='tagore')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda _name: None,
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '老师',
            'tagore': '罗宾德拉纳特·泰戈尔',
            '豆苗': '豆苗',
        }
    )
    floor_manager._recent_display_speakers = ['罗宾德拉纳特·泰戈尔']
    floor_manager._speaker_message_count['tagore'] = 1

    stream_result = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(source='tagore', content='哇，泰戈尔爷爷说得真好！')
    )
    message_result = await floor_manager._process_event(
        TextMessage(source='tagore', content='哇，泰戈尔爷爷说得真好！')
    )

    assert stream_result is None
    assert message_result is None


@pytest.mark.asyncio
async def test_floor_manager_strips_system_trigger_prefix_from_stream_segments() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            '豆苗': '豆苗',
        }
    )

    result = await floor_manager._process_event(
        ModelClientStreamingChunkEvent(
            source='explorer',
            content='[系统触发：请小探同学发言] （指着窗台的蚂蚁）我悄悄观察过蚂蚁搬家搬家。',
        )
    )

    assert result is not None
    assert result['event_type'] == 'stream'
    assert result['data']['tts_segments'] == ['（指着窗台的蚂蚁）我悄悄观察过蚂蚁搬家搬家。']


def test_turn_scheduler_prefers_ai_after_moderator_opening() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=4,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '豆苗': '豆苗',
        },
    )

    selector = team._selector_func
    assert selector is not None

    opening_without_designation = [
        SimpleNamespace(source='moderator', content='同学们，我们先把问题想清楚。'),
    ]
    opening_with_ai_designation = [
        SimpleNamespace(source='moderator', content='小探，你先说说你的想法。'),
    ]

    assert selector(opening_without_designation) == 'explorer'
    assert selector(opening_with_ai_designation) == 'explorer'


def test_turn_scheduler_selects_human_after_warmup_without_explicit_invite() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('comedian'), _agent('skeptic'), _agent('peacemaker')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            '老师': 'moderator',
            '可乐': 'comedian',
            '小疑': 'skeptic',
            '小和': 'peacemaker',
            '豆苗': '豆苗',
        },
    )

    selector = team._selector_func
    assert selector is not None

    thread = [
        SimpleNamespace(source='moderator', content='我们先听听大家的想法。'),
        SimpleNamespace(source='skeptic', content='我觉得要看会不会堵路。'),
        SimpleNamespace(source='comedian', content='我喜欢路边摊，但也怕太乱。'),
        SimpleNamespace(source='moderator', content='大家说得不错，我们继续听另一位同学。'),
    ]

    assert selector(thread) == '豆苗'


def test_parse_speaker_designation_supports_natural_follow_up_questions() -> None:
    participants = ['李老师', '小探', '豆苗']

    assert parse_speaker_designation('豆苗你有没有过这种小失误啊？', participants) == '豆苗'
    assert parse_speaker_designation('我也想请小探再补充一下。', participants) == '小探'


def test_parse_speaker_designation_supports_unspoken_student_followup() -> None:
    participants = ['老师', '可乐', '小疑', '小和', '马克斯·韦伯', '豆苗']

    assert (
        parse_speaker_designation(
            '那最后还有小和同学没发言呢。你自己觉得，大城市该不该在路边摆摊呀？说说你的看法吧。',
            participants,
        )
        == '小和'
    )


def test_parse_speaker_designation_tolerates_suffix_words() -> None:
    """名字后跟"同学"等后缀词时仍能正确解析点名对象。"""
    participants = ['李老师', '小探', '小思', '小和', '豆苗']

    assert (
        parse_speaker_designation('小思同学，你怎么看豆苗同学的这个想法呢？', participants)
        == '小思'
    )
    assert parse_speaker_designation('那么，小思同学，你觉得呢？', participants) == '小思'
    assert parse_speaker_designation('小和同学，你认为这个方案可行吗？', participants) == '小和'
    assert parse_speaker_designation('豆苗同学你有没有类似的经历？', participants) == '豆苗'
    assert (
        parse_speaker_designation(
            '豆苗同学，你先来开个头吧，你觉得“追求完美”是好事还是坏事呢？', participants
        )
        == '豆苗'
    )


def test_parse_speaker_designation_prefers_invited_name_over_context_mention() -> None:
    participants = ['老师', '小和', '小思', '可乐', '豆苗']

    assert (
        parse_speaker_designation(
            '好，那我想问问小思同学，听到小和这么说，你是更支持摆摊呢，还是觉得不该让路边摆摊？',
            participants,
        )
        == '小思'
    )


def test_parse_speaker_designation_prefers_target_before_dash_context_mentions() -> None:
    participants = ['老师', '可乐', '小探', '小疑', '小说', '孔子', '豆苗']

    assert (
        parse_speaker_designation(
            '不过我想问问咱们的真人同学豆苗——可乐说他用冰淇淋当动力，你觉得这和"心里装着更重要的事"是一回事吗？豆苗同学，你怎么看？',
            participants,
        )
        == '豆苗'
    )
    assert (
        parse_speaker_designation(
            '那我想问问小疑同学——你听了小探和可乐的故事，你觉得勇气有没有"大小"之分呀？',
            participants,
        )
        == '小疑'
    )


def test_parse_speaker_designation_strips_markdown_emphasis() -> None:
    participants = ['老师', '小和', '小思', '可乐', '豆苗']

    assert (
        parse_speaker_designation(
            '好，咱们也听听其他同学的想法。**可乐同学**，你才一年级，你平时看到路边摆摊的小车车是什么感觉呀？',
            participants,
        )
        == '可乐'
    )


def test_parse_speaker_designation_supports_session_five_direct_invites() -> None:
    participants = ['老师', '可乐', '小探', '小疑', '小爱', '小理', '海伦·凯勒', '豆苗']

    assert (
        parse_speaker_designation('哎，可乐小朋友，你平时最喜欢做手工了对不对？', participants)
        == '可乐'
    )
    assert (
        parse_speaker_designation('来，小理同学，你平时特别喜欢算数学题，对吧？', participants)
        == '小理'
    )
    assert (
        parse_speaker_designation('那海伦·凯勒先生，该您发言啦！', participants)
        == '海伦·凯勒'
    )


def test_parse_speaker_designation_supports_thinker_alias_titles() -> None:
    participants = ['李老师', '小探', '豆苗', '阿尔弗雷德·阿德勒']

    assert (
        parse_speaker_designation(
            '那我正式邀请一下阿德勒先生，您先从心理学角度说说看。',
            participants,
        )
        == '阿尔弗雷德·阿德勒'
    )


def test_turn_scheduler_prefers_moderator_when_entering_closing_window() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('skeptic')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '小疑': 'skeptic',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天我们从规则和自由开始聊。'),
        SimpleNamespace(source='explorer', content='我觉得规则像扶手。'),
        SimpleNamespace(source='豆苗', content='有时候扶手也会挡住路。'),
        SimpleNamespace(source='moderator', content='你刚才说扶手也会挡住路，这个观察很妙。'),
        SimpleNamespace(source='skeptic', content='那要看扶手是不是太多了。'),
        SimpleNamespace(source='explorer', content='也许可以让扶手更灵活一点。'),
    ]

    assert selector(thread) == 'moderator'


def test_turn_scheduler_prefers_moderator_for_second_closing_inquiry() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('skeptic')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '小疑': 'skeptic',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天我们从规则和自由开始聊。'),
        SimpleNamespace(source='explorer', content='我觉得规则像扶手。'),
        SimpleNamespace(source='豆苗', content='有时候扶手也会挡住路。'),
        SimpleNamespace(source='moderator', content='你刚才说扶手也会挡住路，这个观察很妙。'),
        SimpleNamespace(source='skeptic', content='那要看扶手是不是太多了。'),
        SimpleNamespace(
            source='moderator',
            content='收尾前，我想先问问大家，还有没有想补充的观点或想法？豆苗，如果你还有新发现，也可以继续说。',
        ),
        SimpleNamespace(source='豆苗', content='我觉得扶手最好能跟着人一起变。'),
        SimpleNamespace(
            source='moderator', content='你刚才说扶手最好能跟着人一起变，这个想法特别有创造力。'
        ),
        SimpleNamespace(source='explorer', content='那就像会移动的桥。'),
    ]

    assert selector(thread) == 'moderator'


def test_turn_scheduler_honors_moderator_non_human_invite_before_human_budget() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('comedian'), _agent('empath'), _agent('rationalist')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            '老师': 'moderator',
            '可乐': 'comedian',
            '小爱': 'empath',
            '小理': 'rationalist',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天聊艺术教育。'),
        SimpleNamespace(source='empath', content='我觉得艺术像阳光。'),
        SimpleNamespace(source='moderator', content='小爱同学说得真好。哎，可乐小朋友，你平时最喜欢做手工了对不对？'),
    ]

    assert selector(thread) == 'comedian'


def test_turn_scheduler_honors_moderator_invite_after_human_spoke() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('rationalist')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            '老师': 'moderator',
            '小探': 'explorer',
            '小理': 'rationalist',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天聊艺术教育。'),
        SimpleNamespace(source='explorer', content='我觉得艺术像游乐场。'),
        SimpleNamespace(source='豆苗', content='我赞同艺术像阳光。'),
        SimpleNamespace(source='moderator', content='嗯，看来豆苗同学还在思考呢，没关系。来，小理同学，你平时特别喜欢算数学题，对吧？'),
    ]

    assert selector(thread) == 'rationalist'


@pytest.mark.asyncio
async def test_floor_manager_stops_after_moderator_goodbye_message() -> None:
    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source='moderator', content='同学们再见！今天聊得真开心，下次咱们再一起讨论好玩的话题。'),
                UserInputRequestedEvent(request_id='should-not-fire', source='豆苗'),
            ]
        ),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({'moderator': '老师', '豆苗': '豆苗'})

    events = [event async for event in floor_manager.run('测试话题')]

    assert [event['event_type'] for event in events] == ['message', 'ended']
    assert events[0]['data']['source'] == 'moderator'


@pytest.mark.asyncio
async def test_floor_manager_appends_explicit_goodbye_for_moderator_closing() -> None:
    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source='moderator', content='今天就到这里啦，同学们下次见。'),
                UserInputRequestedEvent(request_id='should-not-fire', source='豆苗'),
            ]
        ),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({'moderator': '老师', '豆苗': '豆苗'})

    events = [event async for event in floor_manager.run('测试话题')]

    assert [event['event_type'] for event in events] == ['message', 'ended']
    assert '再见' in events[0]['data']['content']


@pytest.mark.asyncio
async def test_floor_manager_rewrites_ungrounded_neutral_quote_claims() -> None:
    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source='moderator', content='有同学提到“抖水珠”的比喻太绝了。你愿意展开说说吗？'),
            ]
        ),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({'moderator': '老师', '豆苗': '豆苗'})

    events = [event async for event in floor_manager.run('测试话题')]

    assert events[0]['event_type'] == 'message'
    assert '有同学提到“抖水珠”' not in events[0]['data']['content']
    assert '抖水珠' not in events[0]['data']['content']
    assert '展开说说' in events[0]['data']['content']


@pytest.mark.asyncio
async def test_floor_manager_forces_moderator_goodbye_before_end_when_missing() -> None:
    floor_manager = FloorManager(
        team=_StreamingTeamStub(
            [
                TextMessage(source='explorer', content='我觉得演员要先会观察生活。'),
            ]
        ),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({'moderator': '老师', 'explorer': '小探', '豆苗': '豆苗'})

    events = [event async for event in floor_manager.run('测试话题')]

    assert [event['event_type'] for event in events] == ['message', 'message', 'ended']
    assert events[1]['data']['source'] == 'moderator'
    assert '再见' in events[1]['data']['content']


def test_turn_scheduler_pulls_teacher_back_if_human_has_not_spoken_yet() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('pavlov')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '巴甫洛夫': 'pavlov',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天我们来聊聊习惯是怎么形成的。'),
        SimpleNamespace(source='explorer', content='我觉得习惯像一条常走的小路。'),
        SimpleNamespace(source='pavlov', content='我会先从重复和信号之间的关系来看。'),
    ]

    assert selector(thread) == 'moderator'


def test_turn_scheduler_brings_in_human_after_teacher_returns_to_invite_phase() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('pavlov')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '巴甫洛夫': 'pavlov',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天我们来聊聊习惯是怎么形成的。'),
        SimpleNamespace(source='explorer', content='我觉得习惯像一条常走的小路。'),
        SimpleNamespace(source='pavlov', content='我会先从重复和信号之间的关系来看。'),
        SimpleNamespace(
            source='moderator', content='你们都给了一个好起点，我们再把目光转回到同学自己的经验。'
        ),
    ]

    assert selector(thread) == '豆苗'

    explicit_invite_thread = [
        *thread[:-1],
        SimpleNamespace(
            source='moderator', content='你们都给了一个好起点，豆苗同学，你也来说说自己的经验吧。'
        ),
    ]

    assert selector(explicit_invite_thread) == '豆苗'


def test_turn_scheduler_honors_non_human_markdown_invite_before_human_budget() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('peacemaker'), _agent('questioner'), _agent('comedian')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=12,
        display_name_to_agent={
            '老师': 'moderator',
            '小和': 'peacemaker',
            '小思': 'questioner',
            '可乐': 'comedian',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func
    assert selector is not None

    thread = [
        SimpleNamespace(source='moderator', content='我们先请小和同学来说说你的第一印象吧。'),
        SimpleNamespace(source='peacemaker', content='我觉得可以划专门的地方摆摊。'),
        SimpleNamespace(
            source='moderator',
            content='好，咱们也听听其他同学的想法。**可乐同学**，你才一年级，你平时看到路边摆摊的小车车是什么感觉呀？',
        ),
    ]

    assert selector(thread) == 'comedian'


def test_turn_scheduler_prefers_invited_name_over_context_mention() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('peacemaker'), _agent('questioner')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=12,
        display_name_to_agent={
            '老师': 'moderator',
            '小和': 'peacemaker',
            '小思': 'questioner',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func
    assert selector is not None

    thread = [
        SimpleNamespace(source='moderator', content='我们先请小和同学来说说你的第一印象吧。'),
        SimpleNamespace(source='peacemaker', content='我觉得可以划专门的地方摆摊。'),
        SimpleNamespace(
            source='moderator',
            content='好，那我想问问小思同学，听到小和这么说，你是更支持摆摊呢，还是觉得不该让路边摆摊？',
        ),
    ]

    assert selector(thread) == 'questioner'


def test_turn_scheduler_runs_five_simulated_discussions_without_unsolicited_human_turns() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    cases = [
        {
            'topic': '大城市该不该在路边摆摊？',
            'students': [('可乐', 'comedian'), ('小疑', 'skeptic'), ('小和', 'peacemaker')],
            'thinker': ('马克斯·韦伯', 'weber'),
            'unspoken': ('小和', 'peacemaker'),
        },
        {
            'topic': '追求完美是好事还是坏事？',
            'students': [('小探', 'explorer'), ('小思', 'thinker_child'), ('小理', 'logic')],
            'thinker': ('苏格拉底', 'socrates'),
            'unspoken': ('小理', 'logic'),
        },
        {
            'topic': '要不要给作业设置难度等级？',
            'students': [('小爱', 'caring'), ('小辩', 'debater'), ('小安', 'steady'), ('可乐', 'comedian')],
            'thinker': ('孔子', 'confucius'),
            'unspoken': ('小安', 'steady'),
        },
        {
            'topic': '班级规则应该大家一起定吗？',
            'students': [('小光', 'bright'), ('小疑', 'skeptic'), ('小和', 'peacemaker')],
            'thinker': ('约翰·杜威', 'dewey'),
            'unspoken': ('小和', 'peacemaker'),
        },
        {
            'topic': '网络上看到的信息都可信吗？',
            'students': [('小探', 'explorer'), ('小真', 'verifier'), ('小思', 'thinker_child'), ('小合', 'synthesizer')],
            'thinker': ('亚里士多德', 'aristotle'),
            'unspoken': ('小合', 'synthesizer'),
        },
    ]

    for case in cases:
        display_name_to_agent = {'老师': 'moderator', '豆苗': '豆苗'}
        display_name_to_agent.update(dict(case['students']))
        display_name_to_agent[case['thinker'][0]] = case['thinker'][1]
        characters = [_agent(agent) for _display, agent in case['students']]
        characters.append(_agent(case['thinker'][1]))
        team = create_discussion_team(
            moderator=_agent('moderator'),
            characters=characters,
            humans=[_agent('豆苗')],
            selector_client=SimpleNamespace(),
            max_turns=24,
            display_name_to_agent=display_name_to_agent,
            thinker_agent_names=[case['thinker'][1]],
        )
        selector = team._selector_func
        assert selector is not None

        first_student_display, first_student_agent = case['students'][0]
        second_student_display, second_student_agent = case['students'][1]
        unspoken_display, unspoken_agent = case['unspoken']
        thinker_display, thinker_agent = case['thinker']
        thread = []

        assert selector(thread) == 'moderator'
        thread.append(SimpleNamespace(source='moderator', content=f'今天我们讨论：{case["topic"]}'))
        assert selector(thread) == first_student_agent
        thread.append(SimpleNamespace(source=first_student_agent, content=f'{first_student_display}先说一个生活观察。'))

        thread.append(SimpleNamespace(source='moderator', content=f'请{second_student_display}同学说说你的看法。'))
        assert selector(thread) == second_student_agent
        thread.append(SimpleNamespace(source=second_student_agent, content=f'{second_student_display}提出一个不同角度。'))

        thread.append(SimpleNamespace(source='moderator', content=f'我们也请{thinker_display}先生来谈谈他的看法。'))
        assert selector(thread) == thinker_agent
        thread.append(SimpleNamespace(source=thinker_agent, content=f'{thinker_display}从概念上补充。'))

        thread.append(
            SimpleNamespace(
                source='moderator',
                content=f'那最后还有{unspoken_display}同学没发言呢。你自己觉得，{case["topic"]}说说你的看法吧。',
            )
        )
        assert selector(thread) == unspoken_agent
        thread.append(SimpleNamespace(source=unspoken_agent, content=f'{unspoken_display}补上自己的判断。'))

        spoken_student_agents = {first_student_agent, second_student_agent, unspoken_agent}
        for extra_student_display, extra_student_agent in case['students']:
            if extra_student_agent in spoken_student_agents:
                continue
            thread.append(SimpleNamespace(source='moderator', content=f'请{extra_student_display}同学也补充一下。'))
            assert selector(thread) == extra_student_agent
            thread.append(SimpleNamespace(source=extra_student_agent, content=f'{extra_student_display}补充一个具体例子。'))
            spoken_student_agents.add(extra_student_agent)

        thread.append(SimpleNamespace(source='moderator', content='大家说得不错，我们继续回到同学自己的经验。'))
        assert selector(thread) != '豆苗'

        thread.append(SimpleNamespace(source='moderator', content='豆苗同学，你也来说说自己的看法吧。'))
        assert selector(thread) == '豆苗'
        thread.append(SimpleNamespace(source='豆苗', content='我觉得要看会不会影响别人，也要给摊主机会。'))
        assert selector(thread) == 'moderator'

        thread.append(SimpleNamespace(source='moderator', content=f'请{first_student_display}同学回应一下豆苗的观点。'))
        assert selector(thread) == first_student_agent
        thread.append(SimpleNamespace(source=first_student_agent, content='我同意要同时看秩序和生活需要。'))

        thread.append(SimpleNamespace(source='moderator', content='豆苗同学，听了补充以后，你想不想再修正一下？'))
        assert selector(thread) == '豆苗'
        thread.append(SimpleNamespace(source='豆苗', content='我会加上固定区域和时间，这样更公平。'))
        assert selector(thread) == 'moderator'

        speakers = [item.source for item in thread]
        assert '豆苗' in speakers
        assert thinker_agent in speakers
        assert {agent for _display, agent in case['students']}.issubset(set(speakers))


def test_turn_scheduler_waits_for_two_non_human_turns_before_first_human_invite() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('pavlov')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '巴甫洛夫': 'pavlov',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天我们先聊聊为什么有人想在家上学。'),
        SimpleNamespace(source='explorer', content='我先从好奇心和冒险感说起。'),
        SimpleNamespace(source='moderator', content='这个角度很活，我们再多听一位同学铺垫一下。'),
    ]

    assert selector(thread) == 'pavlov'


def test_turn_scheduler_stops_proactively_inviting_human_after_soft_cap() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('pavlov')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '巴甫洛夫': 'pavlov',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='我们先从自由和纪律开始。'),
        SimpleNamespace(source='explorer', content='我觉得自由像打开地图。'),
        SimpleNamespace(source='豆苗', content='但地图也需要方向。'),
        SimpleNamespace(source='moderator', content='你这个提醒很关键。'),
        SimpleNamespace(source='pavlov', content='习惯会决定人怎么使用自由。'),
        SimpleNamespace(source='豆苗', content='所以规则不能完全消失。'),
        SimpleNamespace(source='moderator', content='规则和自由要一起看。'),
        SimpleNamespace(source='explorer', content='我更关心孩子会不会孤单。'),
        SimpleNamespace(source='豆苗', content='是啊，社交真的很重要。'),
        SimpleNamespace(source='moderator', content='你把问题抓到了中心。'),
        SimpleNamespace(source='pavlov', content='重复互动本身就是训练。'),
        SimpleNamespace(source='豆苗', content='所以学校像真实训练场。'),
        SimpleNamespace(source='moderator', content='这个比喻很稳。'),
        SimpleNamespace(source='explorer', content='但也许可以保留一点家庭弹性。'),
        SimpleNamespace(source='豆苗', content='我赞成周末保留弹性。'),
        SimpleNamespace(source='moderator', content='那我们继续往实施层面想。'),
        SimpleNamespace(source='pavlov', content='关键在于边界和节奏。'),
        SimpleNamespace(source='豆苗', content='我觉得五天学校两天家庭挺合适。'),
        SimpleNamespace(source='moderator', content='这个组合方案已经很具体了。'),
        SimpleNamespace(source='explorer', content='那接下来可以比较不同年龄段。'),
    ]

    assert selector(thread) is None


def test_turn_scheduler_stops_reinviting_human_after_compact_target() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('skeptic')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=40,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '小疑': 'skeptic',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='我们先从自由和纪律开始。'),
        SimpleNamespace(source='explorer', content='我觉得自由像打开地图。'),
        SimpleNamespace(source='豆苗', content='但地图也需要方向。'),
        SimpleNamespace(source='moderator', content='你这个提醒很关键。'),
        SimpleNamespace(source='skeptic', content='习惯会决定人怎么使用自由。'),
        SimpleNamespace(source='explorer', content='我更关心孩子会不会孤单。'),
        SimpleNamespace(source='豆苗', content='是啊，社交真的很重要。'),
        SimpleNamespace(source='moderator', content='你把问题抓到了中心。'),
        SimpleNamespace(source='skeptic', content='重复互动本身就是训练。'),
        SimpleNamespace(source='explorer', content='可以保留一点家庭弹性。'),
        SimpleNamespace(source='豆苗', content='我赞成周末保留弹性。'),
        SimpleNamespace(source='moderator', content='那我们继续往实施层面想。'),
        SimpleNamespace(source='skeptic', content='关键在于边界和节奏。'),
        SimpleNamespace(source='explorer', content='不同年龄段可以不同安排。'),
        SimpleNamespace(source='豆苗', content='低年级需要更多陪伴。'),
        SimpleNamespace(source='moderator', content='这个年龄差异很重要。'),
        SimpleNamespace(source='skeptic', content='家长负担也得算进去。'),
        SimpleNamespace(source='explorer', content='还要安排同伴活动。'),
        SimpleNamespace(source='豆苗', content='可以固定每周一起做项目。'),
        SimpleNamespace(source='moderator', content='这已经有方案感了。'),
        SimpleNamespace(source='skeptic', content='项目也需要评价标准。'),
        SimpleNamespace(source='explorer', content='评价最好不只看分数。'),
        SimpleNamespace(source='豆苗', content='可以看作品和过程记录。'),
        SimpleNamespace(source='moderator', content='你把评价方式补上了。'),
        SimpleNamespace(source='skeptic', content='过程记录也可能变成形式主义。'),
        SimpleNamespace(source='explorer', content='那记录应该简单一点。'),
        SimpleNamespace(source='豆苗', content='每周只写三个重点就够。'),
        SimpleNamespace(source='moderator', content='这个约束很实用。'),
        SimpleNamespace(source='skeptic', content='还需要有人定期回看。'),
        SimpleNamespace(source='explorer', content='老师和家长可以轮流看。'),
    ]

    assert selector(thread) is None


def test_turn_scheduler_relaxes_human_soft_cap_after_two_hand_raises() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('skeptic')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=30,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '小疑': 'skeptic',
            '豆苗': '豆苗',
        },
        get_human_engagement_level=lambda: 1,
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天我们讨论在家上学。'),
        SimpleNamespace(source='explorer', content='我担心同伴互动变少。'),
        SimpleNamespace(source='豆苗', content='我觉得如果有社团，也许还好。'),
        SimpleNamespace(source='moderator', content='你刚才说社团也许能补上互动，这个角度很好。'),
        SimpleNamespace(source='skeptic', content='可是在家上学不一定有固定伙伴。'),
        SimpleNamespace(source='豆苗', content='那就要设计固定的小组。'),
        SimpleNamespace(source='moderator', content='这个办法已经很像真实方案了。'),
        SimpleNamespace(source='explorer', content='而且小组最好长期稳定。'),
        SimpleNamespace(source='豆苗', content='对，还可以轮流当组长。'),
        SimpleNamespace(source='moderator', content='你把合作细节补得很具体。'),
        SimpleNamespace(source='skeptic', content='但有人可能还是会偷懒。'),
        SimpleNamespace(source='豆苗', content='那就让大家互相打分。'),
        SimpleNamespace(source='moderator', content='这已经进入规则设计了。'),
        SimpleNamespace(source='explorer', content='还可以让老师定期回看记录。'),
        SimpleNamespace(source='豆苗', content='我还想加一个家长反馈表。'),
        SimpleNamespace(source='moderator', content='这让方案更完整了。'),
        SimpleNamespace(source='skeptic', content='不过家长反馈也可能带偏压力。'),
        SimpleNamespace(source='豆苗', content='那反馈表就只写观察，不排名。'),
        SimpleNamespace(source='explorer', content='我赞成，少一点比较会更舒服。'),
        SimpleNamespace(source='skeptic', content='如果真这样，我觉得在家上学也不是完全不行。'),
        SimpleNamespace(source='explorer', content='那下一步就看老师愿不愿意定期看这些记录。'),
    ]

    assert selector(thread) == 'moderator'


def test_turn_scheduler_delays_closing_after_two_hand_raises() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('skeptic')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=10,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '小疑': 'skeptic',
            '豆苗': '豆苗',
        },
        get_human_engagement_level=lambda: 1,
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天我们讨论在家上学。'),
        SimpleNamespace(source='explorer', content='我先想到同伴关系会变化。'),
        SimpleNamespace(source='豆苗', content='我觉得学习自由会更大。'),
        SimpleNamespace(source='skeptic', content='但自由也可能变成拖延。'),
        SimpleNamespace(source='explorer', content='所以要看有没有稳定节奏。'),
    ]

    assert selector(thread) is None


def test_turn_scheduler_keeps_three_hand_raise_extension_bounded() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    thread = [
        SimpleNamespace(source='moderator', content='今天我们讨论在家上学。'),
        SimpleNamespace(source='explorer', content='我先想到同伴关系会变化。'),
        SimpleNamespace(source='豆苗', content='我觉得学习自由会更大。'),
        SimpleNamespace(source='skeptic', content='但自由也可能变成拖延。'),
        SimpleNamespace(source='explorer', content='所以要看有没有稳定节奏。'),
        SimpleNamespace(source='豆苗', content='所以支持方式也得一起改。'),
        SimpleNamespace(source='skeptic', content='我担心家长会更累。'),
        SimpleNamespace(source='豆苗', content='那就要把家长任务拆小一点。'),
        SimpleNamespace(source='豆苗', content='还可以固定每周做一次回顾。'),
        SimpleNamespace(source='skeptic', content='也可能逼着大家重新设计作息。'),
        SimpleNamespace(source='explorer', content='最好连同伴活动也一起设计。'),
    ]

    level_one_team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('skeptic')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=10,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '小疑': 'skeptic',
            '豆苗': '豆苗',
        },
        get_human_engagement_level=lambda: 1,
    )
    level_two_team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('skeptic')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=10,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '小疑': 'skeptic',
            '豆苗': '豆苗',
        },
        get_human_engagement_level=lambda: 2,
    )

    assert level_one_team._selector_func(thread) == 'moderator'
    assert level_two_team._selector_func(thread) == 'moderator'


@pytest.mark.asyncio
async def test_request_interrupt_notifies_human_hand_raise_callback() -> None:
    hand_raises: list[str] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda _name: None,
        human_hand_raise_notifier=lambda name: hand_raises.append(name),
    )
    floor_manager.set_display_name_map({'moderator': '李老师', '豆苗': '豆苗'})
    floor_manager.current_speaker = 'moderator'

    await floor_manager.request_interrupt('豆苗')

    assert hand_raises == ['豆苗']


@pytest.mark.asyncio
async def test_request_interrupt_is_ignored_when_human_turn_is_already_waiting() -> None:
    hand_raises: list[str] = []
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda _name: None,
        human_hand_raise_notifier=lambda name: hand_raises.append(name),
    )
    floor_manager.set_display_name_map({'moderator': '李老师', '豆苗': '豆苗'})
    floor_manager.current_speaker = '豆苗'
    floor_manager.state = FloorState.HUMAN_TURN_WAITING

    await floor_manager.request_interrupt('豆苗')

    assert hand_raises == []
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING


def test_turn_scheduler_reasks_closing_question_after_single_new_follow_up() -> None:
    def _agent(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, description=name)

    team = create_discussion_team(
        moderator=_agent('moderator'),
        characters=[_agent('explorer'), _agent('skeptic')],
        humans=[_agent('豆苗')],
        selector_client=SimpleNamespace(),
        max_turns=8,
        display_name_to_agent={
            '李老师': 'moderator',
            '小探': 'explorer',
            '小疑': 'skeptic',
            '豆苗': '豆苗',
        },
    )
    selector = team._selector_func

    thread = [
        SimpleNamespace(source='moderator', content='今天我们从规则和自由开始聊。'),
        SimpleNamespace(source='explorer', content='我觉得规则像扶手。'),
        SimpleNamespace(source='豆苗', content='有时候扶手也会挡住路。'),
        SimpleNamespace(source='moderator', content='你刚才说扶手也会挡住路，这个观察很妙。'),
        SimpleNamespace(source='skeptic', content='那要看扶手是不是太多了。'),
        SimpleNamespace(
            source='moderator',
            content='收尾前，我想先问问大家，还有没有想补充的观点或想法？豆苗，如果你还有新发现，也可以继续说。',
        ),
        SimpleNamespace(source='豆苗', content='我还想补一句，扶手最好还能跟着人慢慢调整。'),
    ]

    assert selector(thread) == 'moderator'


@pytest.mark.asyncio
async def test_floor_manager_ignores_duplicate_human_input_requested_for_same_speaker() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=SimpleNamespace(),
    )
    floor_manager.current_speaker = '豆苗'
    floor_manager.state = FloorState.HUMAN_TURN_WAITING

    first = await floor_manager._process_event(
        UserInputRequestedEvent(request_id='req-1', source='豆苗')
    )
    second = await floor_manager._process_event(
        UserInputRequestedEvent(request_id='req-2', source='豆苗')
    )

    assert first is not None
    assert first['event_type'] == 'human_input_requested'
    assert first['data']['speaker'] == '豆苗'
    assert floor_manager.state == FloorState.HUMAN_TURN_WAITING
    assert second is None


@pytest.mark.asyncio
async def test_floor_manager_clears_stale_designation_on_human_input_requested() -> None:
    designated_updates: list[str | None] = []

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=SimpleNamespace(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.current_speaker = '豆苗'
    floor_manager.state = FloorState.HUMAN_TURN_WAITING
    floor_manager._set_designated_speaker('豆苗')

    result = await floor_manager._process_event(
        UserInputRequestedEvent(request_id='req-clear-designation', source='豆苗')
    )

    assert result is not None
    assert result['event_type'] == 'human_input_requested'
    assert designated_updates[-1] is None


@pytest.mark.asyncio
async def test_floor_manager_marks_interrupt_origin_on_human_input_requested() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
    )
    floor_manager.set_display_name_map({'moderator': '李老师', '豆苗': '豆苗'})
    floor_manager.current_speaker = 'moderator'

    await floor_manager.request_interrupt('豆苗')
    floor_manager.current_speaker = '豆苗'

    result = await floor_manager._process_event(
        UserInputRequestedEvent(request_id='req-interrupt', source='豆苗')
    )

    assert result is not None
    assert result['event_type'] == 'human_input_requested'
    assert result['data']['speaker'] == '豆苗'
    assert result['data']['reason'] == 'interrupt'


@pytest.mark.asyncio
async def test_submit_human_input_ignores_self_designation_target() -> None:
    clear_human_queues()
    designated_updates: list[str | None] = []

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            '豆苗': '豆苗',
        }
    )

    await floor_manager.submit_human_input('豆苗', '我也想请豆苗再补充一下。')

    queue = get_human_queue('豆苗')
    assert await asyncio.wait_for(queue.get(), timeout=0.1) == '我也想请豆苗再补充一下。'
    assert designated_updates == [None]


@pytest.mark.asyncio
async def test_submit_human_input_ignores_reported_moderator_designation_mistake() -> None:
    clear_human_queues()
    designated_updates: list[str | None] = []

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='comedian')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'comedian': '可乐',
            '豆苗': '豆苗',
        }
    )

    text = '老师，我是豆苗，你叫我发言，又说请可乐发言，你搞错了。'
    await floor_manager.submit_human_input('豆苗', text)

    queue = get_human_queue('豆苗')
    assert await asyncio.wait_for(queue.get(), timeout=0.1) == text
    assert designated_updates == [None]


@pytest.mark.asyncio
async def test_floor_manager_forces_first_human_handoff_after_two_ai_turns() -> None:
    designated_updates: list[str | None] = []
    emitted_messages: list[tuple[str, str, str]] = []

    async def capture_message(source: str, content: str, msg_type: str) -> None:
        emitted_messages.append((source, content, msg_type))

    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name='moderator'),
            SimpleNamespace(name='explorer'),
            SimpleNamespace(name='skeptic'),
        ],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
        designated_speaker_setter=lambda name: designated_updates.append(name),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            'skeptic': '小疑',
            '豆苗': '豆苗',
        }
    )
    floor_manager.on_message(capture_message)
    floor_manager._speaker_message_count['explorer'] = 1
    floor_manager._speaker_message_count['skeptic'] = 1
    floor_manager._recent_display_speakers.extend(['小探', '小疑'])

    result = await floor_manager._process_event(
        TextMessage(
            source='moderator',
            content='小疑同学刚才的问题很有意思！那我想问问小探同学，你怎么看？',
        )
    )

    assert result is not None
    assert result['event_type'] == 'human_input_requested'
    assert result['data']['speaker'] == '豆苗'
    assert designated_updates[-1] == '豆苗'
    assert emitted_messages[-1][0] == 'moderator'
    assert '麦克风交给豆苗同学' in emitted_messages[-1][1]
    assert '小探同学，你怎么看' not in emitted_messages[-1][1]


@pytest.mark.asyncio
async def test_submit_human_input_builds_redirect_guidance_for_off_topic_user_turn() -> None:
    clear_human_queues()
    guidance_memory = HumanResponseGuidanceMemory()
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='explorer')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
        human_guidance_memory=guidance_memory,
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            '豆苗': '豆苗',
        }
    )
    floor_manager._current_topic = '在家上学\n\n请大家讨论在家上学和学校教育的差别。'
    floor_manager._recent_turn_summaries = [
        ('小探', '我更关心孩子会不会孤单。'),
    ]

    await floor_manager.submit_human_input('豆苗', '我昨晚吃了两块披萨，还想养小猫。')

    results = (await guidance_memory.query('')).results
    assert len(results) == 1
    payload = results[0].content
    assert payload['assessment'] == 'off_topic'
    assert payload['speaker'] == '豆苗'
    assert payload['suggested_peer_name'] == '小探'
    assert payload['suggested_peer_summary'] == '我更关心孩子会不会孤单。'


@pytest.mark.asyncio
async def test_moderator_message_consumes_pending_human_redirect_guidance() -> None:
    guidance_memory = HumanResponseGuidanceMemory()
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=_SafetyFilterStub(),
        human_guidance_memory=guidance_memory,
    )
    await guidance_memory.replace_guidance(
        [
            {
                'speaker': '豆苗',
                'summary': '我昨晚吃了两块披萨，还想养小猫。',
                'assessment': 'off_topic',
            }
        ]
    )
    floor_manager._pending_human_guidance = True

    result = await floor_manager._process_event(
        TextMessage(source='moderator', content='我们先回到在家上学这个主题，再接着想一想。')
    )

    assert result is not None
    assert (await guidance_memory.query('')).results == []
    assert floor_manager._pending_human_guidance is False


@pytest.mark.asyncio
async def test_floor_manager_sets_current_human_before_waiting_callback() -> None:
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=SimpleNamespace(),
    )
    observed_speakers: list[str | None] = []

    async def _record_state_change(*_args) -> None:
        observed_speakers.append(floor_manager.current_speaker)

    floor_manager.on_state_change(_record_state_change)
    floor_manager.current_speaker = None

    result = await floor_manager._process_event(
        UserInputRequestedEvent(request_id='req-human', source='豆苗')
    )

    assert result is not None
    assert result['event_type'] == 'human_input_requested'
    assert result['data']['speaker'] == '豆苗'
    assert floor_manager.current_speaker == '豆苗'
    assert observed_speakers == ['豆苗']


@pytest.mark.asyncio
async def test_floor_manager_forces_teacher_to_open_if_selector_returns_wrong_first_speaker() -> (
    None
):
    floor_manager = FloorManager(
        team=_TeamStub(),
        ai_agents=[SimpleNamespace(name='moderator'), SimpleNamespace(name='pavlov')],
        human_agents=[SimpleNamespace(name='豆苗')],
        safety_filter=SimpleNamespace(),
    )

    result = await floor_manager._process_event(
        SelectSpeakerEvent(source='system', content=['pavlov'])
    )

    assert result is not None
    assert result['event_type'] == 'turn_change'
    assert result['data']['speaker'] == 'moderator'
    assert floor_manager.current_speaker == 'moderator'


def test_thinker_label_prefers_stable_name_field() -> None:
    thinker = {
        'name': '巴甫洛夫',
        'display_name': '心理学家巴甫洛夫',
    }

    assert thinker_label('pavlov', thinker) == '巴甫洛夫'


@pytest.mark.asyncio
async def test_run_discussion_maps_stream_source_to_display_name() -> None:
    recorded = []

    async def _send_event(event_type: str, data: dict) -> bool:
        recorded.append((event_type, data))
        return True

    floor_manager = _DiscussionFloorManagerStub(
        [
            {
                'event_type': 'stream',
                'data': {
                    'source': 'moderator',
                    'content': '今天咱们先把问题看清楚。',
                    'tts_segments': ['今天咱们先把问题看清楚。'],
                },
            },
            {
                'event_type': 'stream',
                'data': {
                    'source': 'pavlov',
                    'content': '我想从习惯形成的角度看看。',
                },
            },
            {
                'event_type': 'ended',
                'data': {'session_id': 'session-test'},
            },
        ]
    )

    result = await _run_discussion(
        _send_event,
        floor_manager,
        '测试话题',
        agent_display_map={
            'moderator': '老师',
            'pavlov': '巴甫洛夫',
        },
    )

    assert result == 'completed'
    assert recorded[0] == (
        'stream',
        {
            'source': '老师',
            'agent_source': 'moderator',
            'content': '今天咱们先把问题看清楚。',
            'tts_segments': ['今天咱们先把问题看清楚。'],
        },
    )
    assert recorded[1] == (
        'stream',
        {
            'source': '巴甫洛夫',
            'agent_source': 'pavlov',
            'content': '我想从习惯形成的角度看看。',
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
                'event_type': 'human_input_requested',
                'data': {
                    'speaker': '豆苗',
                    'reason': 'normal',
                },
            },
            {
                'event_type': 'ended',
                'data': {'session_id': 'session-test'},
            },
        ]
    )

    result = await _run_discussion(
        _send_event,
        floor_manager,
        '测试话题',
        observer_mode=True,
    )

    assert result == 'completed'
    assert recorded[0] == (
        'human_input_requested',
        {
            'speaker': '豆苗',
            'reason': 'normal',
        },
    )
    assert floor_manager.submitted_inputs == [('豆苗', '（旁听）')]


@pytest.mark.asyncio
async def test_run_discussion_observer_mode_keeps_interrupt_human_turn() -> None:
    recorded = []

    async def _send_event(event_type: str, data: dict) -> bool:
        recorded.append((event_type, data))
        return True

    floor_manager = _DiscussionFloorManagerStub(
        [
            {
                'event_type': 'human_input_requested',
                'data': {
                    'speaker': '豆苗',
                    'reason': 'interrupt',
                },
            },
            {
                'event_type': 'ended',
                'data': {'session_id': 'session-test'},
            },
        ]
    )

    result = await _run_discussion(
        _send_event,
        floor_manager,
        '测试话题',
        observer_mode=True,
    )

    assert result == 'completed'
    assert recorded[0] == (
        'human_input_requested',
        {
            'speaker': '豆苗',
            'reason': 'interrupt',
        },
    )
    assert floor_manager.submitted_inputs == []
