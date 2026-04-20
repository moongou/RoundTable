"""讨论引擎 - AutoGen SelectorGroupChat 封装

管理圆桌讨论的轮次调度，使用自定义的 selector_func
实现主持人主导的点名机制。

核心规则：
- 老师开场
- 用户发言后，老师优先点评
- 老师/用户可指定下一个发言角色
- 发言队列严格顺序执行
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.teams import SelectorGroupChat
from autogen_core.models import ChatCompletionClient

from app.config import settings

logger = logging.getLogger(__name__)

# 主持人选择器提示词
MODERATOR_SELECTOR_PROMPT = """你是一个讨论主持人，负责从以下参与者中选择下一位发言者。

参与者列表：
{roles}

讨论历史：
{history}

规则：
0. 每个新话题开场必须先由老师发言，介绍背景后再点名
0.1 首轮阶段（老师开场 + 每位同学首次发言）禁止“上一位同学说得对”等互引式表达
1. 人类学生是讨论的焦点人物，讨论应以人类学生为重心展开
2. 人类学生每次发言后，老师应立即点评（总结、补充提问或引导深入）
3. 确保每位参与者发言次数大致均衡
4. 不要让同一个人连续发言两次（除老师外）
5. 优先让还没发言的人先说
6. 不要过于频繁让人类学生发言，通常至少间隔2位非人类发言者
7. 如果有人被指定发言（如"请XX发言"），优先安排该角色
8. 只输出参与者名字，不要其他内容
"""

# 结束讨论的关键词
TERMINATION_KEYWORDS = ["讨论结束", "END_DISCUSSION", "今天讨论到这里"]

# 全局指定发言者请求（由 floor_manager / websocket 设置）
_designated_next_speaker: Optional[str] = None


def set_designated_speaker(name: Optional[str]) -> None:
    """设置被指定的下一个发言者。"""
    global _designated_next_speaker
    _designated_next_speaker = name
    if name:
        logger.info("[TurnScheduler] 指定下一位发言者: %s", name)


def get_designated_speaker() -> Optional[str]:
    """获取并清除被指定的下一个发言者。"""
    global _designated_next_speaker
    name = _designated_next_speaker
    _designated_next_speaker = None
    return name


def parse_speaker_designation(text: str, participant_names: list[str]) -> Optional[str]:
    """从发言内容中解析指定发言者。

    支持模式：
    - "请小探发言" / "请小探来谈谈"
    - "小明，你怎么看？" / "小明你觉得呢"
    - "我们来听听小说的想法"
    - "我想听听小理的观点"

    Args:
        text: 发言文本。
        participant_names: 所有参与者名字列表。

    Returns:
        被指定的参与者名字，如果无法解析则返回 None。
    """
    normalized_text = (text or "").strip()
    if not normalized_text:
        return None

    # 按名字长度降序匹配，避免短名误匹配
    sorted_names = sorted(participant_names, key=len, reverse=True)
    for name in sorted_names:
        # 匹配各种点名模式
        patterns = [
            rf'请\s*{re.escape(name)}\s*(发言|先说|来谈|谈谈|先来)',
            rf'接下来\s*请\s*{re.escape(name)}',
            rf'下一位\s*(请)?\s*{re.escape(name)}',
            rf'轮到\s*{re.escape(name)}',
            rf'请\s*{re.escape(name)}\s*同学',
            rf'{re.escape(name)}[，,]?\s*你(怎么看|觉得|认为|来说|来谈|先说)',
            rf'(我们)?来?听听\s*{re.escape(name)}',
            rf'想听听?\s*{re.escape(name)}',
            rf'由\s*{re.escape(name)}\s*(先)?发言',
        ]
        for pat in patterns:
            if re.search(pat, normalized_text):
                return name
    return None


def _exclude_recent_speakers(
    thread: list,
    all_participant_names: list[str],
    moderator_name: str = "老师",
) -> list[str]:
    """排除最近发言的参与者，避免连续发言。"""
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
    consume_designated_speaker: Optional[Callable[[], Optional[str]]] = None,
    on_designation_lifecycle: Optional[Callable[[str, Optional[str]], None]] = None,
) -> SelectorGroupChat:
    """创建圆桌讨论团队。"""
    if max_turns is None:
        max_turns = settings.max_turns

    all_participants = [moderator] + characters + humans
    all_names = [p.name for p in all_participants]

    termination = MaxMessageTermination(max_turns) | TextMentionTermination(
        "讨论结束"
    )

    human_name_set = {h.name for h in humans}

    def moderator_led_selector(thread: list) -> Optional[str]:
        """自定义发言者选择函数。

        优先级：
        1. 全局指定发言者（老师/用户点名）
        2. 用户刚发言 → 老师点评
        3. 主持人发言中的点名
        4. 人类冷却期控制
        5. 交由 LLM 选择
        """
        participant_msgs = [m for m in thread if getattr(m, "source", None) in all_names]
        if not participant_msgs:
            logger.info("[TurnScheduler] 首轮：选择老师开场")
            return moderator.name

        last_source = getattr(participant_msgs[-1], "source", None) if participant_msgs else None

        # ── 优先级1：检查全局指定发言者 ──
        designated = (
            consume_designated_speaker()
            if consume_designated_speaker is not None
            else get_designated_speaker()
        )
        if designated and on_designation_lifecycle is not None:
            on_designation_lifecycle("consumed", designated)
        if designated and designated in all_names:
            if on_designation_lifecycle is not None:
                on_designation_lifecycle("executed", designated)
            logger.info("[TurnScheduler] 执行指定发言者: %s", designated)
            return designated

        def turns_since_last_human() -> int:
            turns = 0
            for m in reversed(participant_msgs):
                src = getattr(m, "source", None)
                if src in human_name_set:
                    return turns
                turns += 1
            return turns

        human_cooldown = 2
        since_human = turns_since_last_human()

        # ── 优先级2：用户刚发言 → 老师点评 ──
        if last_source in human_name_set:
            logger.info("[TurnScheduler] 用户 %s 刚发言，安排老师点评", last_source)
            return moderator.name

        # ── 优先级3：只解析“最新一条”老师/用户发言中的点名，避免旧消息误触发 ──
        latest_msg = participant_msgs[-1] if participant_msgs else None
        latest_source = getattr(latest_msg, "source", None) if latest_msg else None
        latest_content = str(
            getattr(latest_msg, "content", "") or getattr(latest_msg, "messages", "")
        ) if latest_msg else ""
        if latest_content and (latest_source == moderator.name or latest_source in human_name_set):
            next_speaker = parse_speaker_designation(latest_content, all_names)
            if next_speaker and next_speaker != latest_source:
                if next_speaker in human_name_set and since_human < human_cooldown:
                    logger.info("[TurnScheduler] 点名 %s 但人类冷却中，跳过", next_speaker)
                else:
                    logger.info("[TurnScheduler] 解析点名: %s → %s", latest_source, next_speaker)
                    return next_speaker

        # ── 优先级4：人类发言冷却期 ──
        if since_human < human_cooldown:
            recent_speaker = getattr(thread[-1], "source", None) if thread else None
            non_human_candidates = [
                n for n in all_names if n not in human_name_set and n != recent_speaker
            ]
            if non_human_candidates:
                if moderator.name in non_human_candidates:
                    return moderator.name
                selected = non_human_candidates[0]
                logger.info("[TurnScheduler] 人类冷却中，选择: %s", selected)
                return selected

        # ── 优先级5：交给 LLM 选择 ──
        logger.info("[TurnScheduler] 交由 LLM 选择下一位发言者")
        return None

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