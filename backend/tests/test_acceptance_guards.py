from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from autogen_agentchat.messages import TextMessage

from app.agents.human_proxy import (
    clear_human_queues,
    create_human_proxy,
    get_human_queue,
    put_human_input,
)
from app.core.floor_manager import FloorManager
from app.core.rolling_summary_memory import RollingSummaryMemory
from app.core.turn_scheduler import create_discussion_team


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
        safety_filter=SimpleNamespace(),
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
        safety_filter=SimpleNamespace(),
    )
    floor_manager.set_display_name_map(
        {
            'moderator': '李老师',
            'explorer': '小探',
            'skeptic': '小疑',
            '豆苗': '豆苗',
        }
    )

    await floor_manager._process_event(TextMessage(source='explorer', content='我觉得可以先看分数能量出什么。'))
    await floor_manager._process_event(TextMessage(source='豆苗', content='（跳过）'))
    await floor_manager._process_event(TextMessage(source='skeptic', content='我更关心分数会不会受状态影响。'))

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