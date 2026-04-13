"""讨论引擎 - AutoGen SelectorGroupChat 封装

管理圆桌讨论的轮次调度，使用自定义的 selector_func
实现主持人主导的点名机制。
"""

from __future__ import annotations

import re
from typing import Optional

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.teams import SelectorGroupChat
from autogen_core.models import ChatCompletionClient

from app.config import settings

# 主持人选择器提示词
MODERATOR_SELECTOR_PROMPT = """你是一个讨论主持人，负责从以下参与者中选择下一位发言者。

参与者列表：
{roles}

讨论历史：
{history}

规则：
1. 确保每位参与者发言次数大致均衡
2. 不要让同一个人连续发言两次（除老师外）
3. 优先让还没发言的人先说
4. 只输出参与者名字，不要其他内容
"""

# 结束讨论的关键词
TERMINATION_KEYWORDS = ["讨论结束", "END_DISCUSSION", "今天讨论到这里"]


def parse_moderator_direction(text: str, participant_names: list[str]) -> Optional[str]:
    """从主持人的发言中解析点名信息。

    例如:
    - "请小探发言" → "小探"
    - "小明，你怎么看？" → "小明"
    - "我们来听听小说的想法" → "小说"

    Args:
        text: 主持人的发言文本。
        participant_names: 所有参与者名字列表。

    Returns:
        被点名的参与者名字，如果无法解析则返回 None。
    """
    # 直接检查主持人是否提到了某位参与者的名字
    for name in participant_names:
        # 匹配 "请XXX发言"、"XXX你怎么看"、"听听XXX的想法" 等模式
        if name in text:
            return name
    return None


def _exclude_recent_speakers(
    thread: list,
    all_participant_names: list[str],
    moderator_name: str = "老师",
) -> list[str]:
    """排除最近发言的参与者，避免连续发言。

    主持人不受此限制，随时可以被选中来引导讨论。
    """
    recent_speakers = set()
    for msg in list(reversed(thread))[-3:]:
        source = getattr(msg, "source", None)
        if source:
            recent_speakers.add(source)

    candidates = [
        name for name in all_participant_names if name not in recent_speakers or name == moderator_name
    ]
    return candidates if candidates else list(all_participant_names)


def create_discussion_team(
    moderator: AssistantAgent,
    characters: list[AssistantAgent],
    humans: list[UserProxyAgent],
    selector_client: ChatCompletionClient,
    max_turns: int | None = None,
) -> SelectorGroupChat:
    """创建圆桌讨论团队。

    Args:
        moderator: 主持人 Agent。
        characters: 虚拟角色 Agent 列表。
        humans: 人类参与者 UserProxyAgent 列表。
        selector_client: 用于选择发言者的模型客户端。
        max_turns: 最大讨论轮次。

    Returns:
        配置好的 SelectorGroupChat 团队。
    """
    if max_turns is None:
        max_turns = settings.max_turns

    all_participants = [moderator] + characters + humans
    all_names = [p.name for p in all_participants]

    # 终止条件：达到最大消息数 或 主持人说出结束关键词
    termination = MaxMessageTermination(max_turns) | TextMentionTermination(
        "讨论结束"
    )

    # 创建 selector_func，尝试从主持人发言中解析点名
    def moderator_led_selector(thread: list) -> Optional[str]:
        """自定义发言者选择函数。

        优先使用主持人发言中的点名信息，如果无法解析则交由 LLM 选择。
        """
        for msg in reversed(thread):
            source = getattr(msg, "source", None)
            content = getattr(msg, "content", "") or getattr(msg, "messages", "")
            if source == moderator.name and content:
                next_speaker = parse_moderator_direction(str(content), all_names)
                if next_speaker:
                    return next_speaker
        return None  # 交给 LLM 选择

    team = SelectorGroupChat(
        participants=all_participants,
        model_client=selector_client,
        selector_prompt=MODERATOR_SELECTOR_PROMPT,
        selector_func=moderator_led_selector,
        allow_repeated_speaker=False,
        termination_condition=termination,
        max_turns=max_turns,
    )

    return team