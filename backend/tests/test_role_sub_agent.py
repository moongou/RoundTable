"""角色子 Agent（RoleSubAgent）测试

验证多角色讨论的"子 agent 工作模式"：
1. 结构化台账归因——每位角色的发言在他人上下文中带明确 [身份] 名字 标签；
2. 上下文隔离——群聊消息绝不以匿名 user 消息混入子 agent 自身模型上下文；
3. 流式保留——ModelClientStreamingChunkEvent 照常产出（前端流畅性）；
4. 自发言去重——回放/重投递不会造成台账重复；
5. 角色数量限制——最多 2 位同学 + 1 位老师（主持人）+ 1 位思想家；
6. SelectorGroupChat 端到端——真实编排下整场讨论归属精准、无异常。
"""

from __future__ import annotations

from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.messages import ModelClientStreamingChunkEvent, TextMessage
from autogen_agentchat.teams import SelectorGroupChat
from autogen_core import CancellationToken
from autogen_ext.models.replay import ReplayChatCompletionClient

from app.agents.character_templates import load_all_templates
from app.agents.moderator import create_moderator
from app.agents.role_sub_agent import (
    ROLE_HUMAN,
    ROLE_MODERATOR,
    ROLE_STUDENT,
    ROLE_THINKER,
    RoleDirectory,
    RoleSubAgent,
)
from app.agents.virtual_character import create_thinker_agent, create_virtual_character
from app.core.role_limits import validate_role_roster
from app.core.rolling_summary_memory import HumanResponseGuidanceMemory
from app.core.thinkers import get_thinker, thinker_label


class RecordingReplayClient(ReplayChatCompletionClient):
    """记录 create_stream 实际收到的消息序列，用于断言子 agent 的隔离上下文。"""

    def __init__(self, chat_completions):
        super().__init__(chat_completions)
        self.stream_calls: list[list] = []

    async def create_stream(self, messages, **kwargs):
        self.stream_calls.append(list(messages))
        async for chunk in super().create_stream(messages, **kwargs):
            yield chunk


def _text(source: str, content: str) -> TextMessage:
    return TextMessage(source=source, content=content)


def _prompt_text(client: RecordingReplayClient, call_index: int = -1) -> str:
    """把某次 LLM 调用的全部消息拼接为纯文本，便于断言。"""
    messages = client.stream_calls[call_index]
    return "\n".join(str(getattr(m, "content", m)) for m in messages)


def _build_directory() -> RoleDirectory:
    directory = RoleDirectory()
    directory.register("moderator", "李老师", ROLE_MODERATOR)
    directory.register("explorer", "小探", ROLE_STUDENT)
    directory.register("skeptic", "小疑", ROLE_STUDENT)
    directory.register("weber", "马克斯·韦伯", ROLE_THINKER)
    directory.register("xiaoming", "小明", ROLE_HUMAN)
    return directory


# --------------------------------------------------------------------- #
# 1. 台账归因 + 上下文隔离
# --------------------------------------------------------------------- #


async def test_transcript_attribution_and_isolation():
    """他人发言必须带 [身份] 名字 标签；自身内部上下文保持干净。"""
    directory = _build_directory()
    client = RecordingReplayClient(["我想说说我的看法，关于蚂蚁搬家。"])
    agent = create_virtual_character(
        "explorer",
        model_client=client,
        topic="身边的观察",
        participant_names=["李老师", "小探", "小明"],
        directory=directory,
    )
    assert isinstance(agent, RoleSubAgent)

    reply = await agent.on_messages(
        [
            _text("moderator", "同学们好，我们先请小明说说。"),
            _text("xiaoming", "我昨天去公园看蚂蚁搬家了。"),
        ],
        CancellationToken(),
    )

    # 子 agent 以自己身份返回
    assert reply.chat_message.source == "explorer"
    assert reply.chat_message.content == "我想说说我的看法，关于蚂蚁搬家。"

    prompt = _prompt_text(client)
    # 主持人发言带明确归属
    assert "[老师（主持人）] 李老师：同学们好，我们先请小明说说。" in prompt
    # 真人发言带明确归属
    assert "[真人同学] 小明：我昨天去公园看蚂蚁搬家了。" in prompt
    # 轮次指令明确身份
    assert "现在轮到你" in prompt and "小探" in prompt
    # 台账引用规则存在
    assert "谁说了什么" in prompt
    # 隔离：他人原文只出现在带标签的台账行中（不出现无标签裸消息）
    assert prompt.count("我昨天去公园看蚂蚁搬家了。") == 1
    assert prompt.count("同学们好，我们先请小明说说。") == 1

    # 隔离：自身内部模型上下文没有混入任何群聊消息
    internal = await agent._model_context.get_messages()
    assert len(internal) == 0

    # 自己的发言被登记为 own
    own = [entry for entry in agent.ledger_entries if entry[2]]
    assert any(entry[1] == "我想说说我的看法，关于蚂蚁搬家。" for entry in own)


async def test_ledger_own_reply_no_duplicate_on_echo():
    """自己发言的回放（框架回传）不会重复登记。"""
    directory = _build_directory()
    client = RecordingReplayClient(["我的第一句发言。"])
    agent = create_virtual_character(
        "skeptic",
        model_client=client,
        topic="测试",
        participant_names=["李老师", "小疑"],
        directory=directory,
    )
    await agent.on_messages([_text("moderator", "开场白。")], CancellationToken())

    # 模拟框架回传自己的发言（相同内容、无 id）
    agent._ingest([_text("skeptic", "我的第一句发言。")])
    agent._ingest([_text("skeptic", "我的第一句发言。")])

    own_entries = [entry for entry in agent.ledger_entries if entry[2]]
    assert len(own_entries) == 1


async def test_redelivery_dedup_by_id():
    """同一消息对象重复投递（按 id 去重）不会重复登记。"""
    directory = _build_directory()
    client = RecordingReplayClient(["好的。"])
    agent = create_virtual_character(
        "explorer",
        model_client=client,
        topic="测试",
        participant_names=["李老师"],
        directory=directory,
    )
    msg = _text("moderator", "请小探发言。")
    agent._ingest([msg])
    agent._ingest([msg])
    others = [entry for entry in agent.ledger_entries if not entry[2]]
    assert len(others) == 1


# --------------------------------------------------------------------- #
# 2. 流式输出保留
# --------------------------------------------------------------------- #


async def test_streaming_chunks_preserved():
    """子 agent 必须继续产出 ModelClientStreamingChunkEvent（前端流畅性）。"""
    directory = _build_directory()
    reply_text = "流式回复测试。"
    client = RecordingReplayClient([reply_text])
    agent = create_virtual_character(
        "explorer",
        model_client=client,
        topic="测试",
        participant_names=["李老师", "小探"],
        directory=directory,
    )

    events = []
    async for event in agent.on_messages_stream(
        [_text("moderator", "小探同学，请说说。")], CancellationToken()
    ):
        events.append(event)

    chunks = [e for e in events if isinstance(e, ModelClientStreamingChunkEvent)]
    assert chunks, "未产出流式块事件"
    assert all(chunk.source == "explorer" for chunk in chunks)
    joined = "".join(chunk.content for chunk in chunks)
    assert joined.replace(" ", "") == reply_text.replace(" ", "")


# --------------------------------------------------------------------- #
# 3. 主持人指导记忆仍然生效（隔离上下文中注入）
# --------------------------------------------------------------------- #


async def test_moderator_guidance_memory_injected():
    directory = _build_directory()
    guidance = HumanResponseGuidanceMemory()
    await guidance.replace_guidance(
        [
            {
                "speaker": "小明",
                "summary": "讲了蚂蚁搬家",
                "assessment": "ok",
                "topic_focus": "观察与记录",
            }
        ]
    )
    client = RecordingReplayClient(["小明观察得很仔细，我们请他再展开说说。"])
    moderator = create_moderator(
        model_client=client,
        topic="身边的观察 - 讨论生活中的小发现",
        participant_names=["李老师", "小探", "小明"],
        student_names=["小探"],
        human_names=["小明"],
        memory=[guidance],
        directory=directory,
    )

    await moderator.on_messages(
        [_text("xiaoming", "我昨天去公园看蚂蚁搬家了。")], CancellationToken()
    )

    prompt = _prompt_text(client)
    assert "真人学生回应提醒" in prompt
    assert "小明" in prompt
    assert "[真人同学] 小明：我昨天去公园看蚂蚁搬家了。" in prompt


# --------------------------------------------------------------------- #
# 4. 角色数量限制：2 同学 + 1 老师（固定）+ 1 思想家
# --------------------------------------------------------------------- #


def test_role_limits():
    # 合法阵容
    assert validate_role_roster(["explorer", "skeptic"], ["weber"]) == (True, "")
    assert validate_role_roster(["moderator", "explorer", "skeptic"], []) == (True, "")
    assert validate_role_roster([], ["weber"]) == (True, "")
    # 3 位同学 → 拒绝
    ok, err = validate_role_roster(["explorer", "skeptic", "rationalist"], [])
    assert not ok and "虚拟同学最多 2 位" in err
    # moderator 不计入同学数
    assert validate_role_roster(["moderator", "explorer", "skeptic"], ["weber"]) == (True, "")
    # 2 位思想家 → 拒绝
    ok, err = validate_role_roster(["explorer"], ["weber", "confucius"])
    assert not ok and "思想家最多 1 位" in err


async def test_session_api_role_limit():
    """REST 会话创建同样执行 2+1+1 限制。"""
    from fastapi import HTTPException
    from app.api.v1.sessions import CreateSessionRequest, create_session

    request = CreateSessionRequest(
        topic_id="",
        free_topic="测试话题",
        character_ids=["explorer", "skeptic", "rationalist"],
        thinker_ids=[],
    )
    try:
        await create_session(request)
        raise AssertionError("应当抛出 HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "虚拟同学最多 2 位" in exc.detail


# --------------------------------------------------------------------- #
# 5. SelectorGroupChat 端到端：整场讨论归属精准
# --------------------------------------------------------------------- #


async def test_group_chat_end_to_end_attribution():
    """4 个子 agent（老师+2 同学+1 思想家）真实编排一场讨论。

    断言：
    - 讨论流畅完成，发言顺序正确；
    - 每个子 agent 的 LLM 提示中都带全场结构化台账（明确归属）；
    - 自己的历史发言标注（就是你）；
    - 所有子 agent 内部上下文始终保持隔离（空）。
    """
    templates = load_all_templates()
    explorer_name = templates["explorer"].name  # 小探
    skeptic_name = templates["skeptic"].name  # 小疑
    weber = get_thinker("weber")
    weber_name = thinker_label("weber", weber)  # 马克斯·韦伯
    moderator_name = "李老师"

    directory = RoleDirectory()
    roster = [moderator_name, explorer_name, skeptic_name, weber_name]

    moderator_opening = (
        "同学们好，今天我们讨论坚持与放弃。首先有请小探同学发言。"
    )
    explorer_reply = "我觉得坚持就像种子发芽，要有耐心。"
    skeptic_reply = "可是种子要是一直不发芽呢？我有点怀疑。"
    weber_reply = "溪水绕过石头，不是放弃，是另一种坚持。"
    moderator_closing = "大家说得都很好，今天讨论结束。"

    mod_client = RecordingReplayClient([moderator_opening, moderator_closing])
    explorer_client = RecordingReplayClient([explorer_reply])
    skeptic_client = RecordingReplayClient([skeptic_reply])
    weber_client = RecordingReplayClient([weber_reply])
    selector_client = ReplayChatCompletionClient(["moderator"])  # selector_func 全程自定义，不需要实际调用

    moderator = create_moderator(
        model_client=mod_client,
        topic="坚持与放弃 - 什么时候该坚持，什么时候可以换个方向",
        participant_names=roster,
        student_names=[explorer_name, skeptic_name],
        thinker_names=[weber_name],
        memory=[],
        directory=directory,
    )
    explorer = create_virtual_character(
        "explorer",
        model_client=explorer_client,
        topic="坚持与放弃",
        participant_names=roster,
        directory=directory,
    )
    skeptic = create_virtual_character(
        "skeptic",
        model_client=skeptic_client,
        topic="坚持与放弃",
        participant_names=roster,
        directory=directory,
    )
    thinker = create_thinker_agent(
        "weber",
        model_client=weber_client,
        topic="坚持与放弃",
        participant_names=roster,
        directory=directory,
    )

    # 注册表登记校验：老师 + 2 同学 + 1 思想家，全部就位
    assert directory.role_type("moderator") == ROLE_MODERATOR
    assert directory.role_type("explorer") == ROLE_STUDENT
    assert directory.role_type("skeptic") == ROLE_STUDENT
    assert directory.role_type("weber") == ROLE_THINKER

    order = ["moderator", "explorer", "skeptic", "weber"]

    def fixed_selector(thread: list) -> str:
        spoken = [m for m in thread if getattr(m, "source", None) in order]
        return order[len(spoken) % len(order)]

    team = SelectorGroupChat(
        participants=[moderator, explorer, skeptic, thinker],
        model_client=selector_client,
        selector_func=fixed_selector,
        allow_repeated_speaker=True,
        termination_condition=MaxMessageTermination(8) | TextMentionTermination("讨论结束"),
    )

    result = await team.run(task="讨论：坚持与放弃")

    # 发言顺序正确（过滤掉任务消息）
    speech_sources = [
        m.source for m in result.messages if isinstance(m, TextMessage) and m.source in order
    ]
    assert speech_sources == ["moderator", "explorer", "skeptic", "weber", "moderator"]

    # 小疑发言时，提示中已有李老师与小探的明确归属
    skeptic_prompt = _prompt_text(skeptic_client)
    assert f"[老师（主持人）] {moderator_name}：{moderator_opening}" in skeptic_prompt
    assert f"[同学] {explorer_name}：{explorer_reply}" in skeptic_prompt
    assert "现在轮到你" in skeptic_prompt

    # 韦伯先生发言时，台账包含前面所有人（含身份标签）
    weber_prompt = _prompt_text(weber_client)
    assert f"[老师（主持人）] {moderator_name}：{moderator_opening}" in weber_prompt
    assert f"[同学] {explorer_name}：{explorer_reply}" in weber_prompt
    assert f"[同学] {skeptic_name}：{skeptic_reply}" in weber_prompt

    # 老师第二次发言时，自己的开场白标注（就是你）
    moderator_second_prompt = _prompt_text(mod_client, call_index=1)
    assert "（就是你）" in moderator_second_prompt
    assert f"[思想家] {weber_name}：{weber_reply}" in moderator_second_prompt

    # 每条他人原文在台账中只出现一次（无匿名重复注入）
    assert weber_prompt.count(explorer_reply) == 1
    assert weber_prompt.count(skeptic_reply) == 1

    # 隔离性：所有子 agent 的内部模型上下文始终为空
    for agent in (moderator, explorer, skeptic, thinker):
        internal = await agent._model_context.get_messages()
        assert len(internal) == 0, f"{agent.name} 内部上下文被污染"

    # 台账登记了全场发言
    assert any(entry[1] == weber_reply for entry in thinker.ledger_entries)
    assert any(entry[1] == skeptic_reply and not entry[2] for entry in thinker.ledger_entries)
