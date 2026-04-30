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
1. **人类学生是讨论的绝对核心**（占讨论重要性30%-50%），所有讨论应以人类学生的观点为中心展开
2. 人类学生发言后，不一定每次都要老师立即点评；可以先让别的同学直接回应、补充、追问，老师主要负责串联、点名和拉回主题
2.1 老师的主持要简短，很多时候一句肯定或一句追问就够了，不要长篇讲评
3. 一场 10 分钟左右的讨论里，老师一般要通过**明确点名**把人类学生安排到5到8次发言，默认目标约6到7次；如果人类学生是通过举手获得发言，也计入这5到8次
3.1 老师开场后，通常先让1到2位非人类参与者铺垫，再开始第一次**明确点名**人类学生发言；真人学生必须在开场后的前3分钟内获得第一次发言机会
3.2 在达到建议上限前，人类学生的频率可以稍高；达到建议上限后，除非老师明确点名、同学明确把话题交给真人、或人类学生主动举手获准，一般不要继续主动安排
3.3 如果人类学生已经多次主动举手，说明其参与意愿很强，此时可以放宽到5到10次甚至更多发言，并相应推迟收尾时机
4. 不要让同一个人连续发言两次（除老师外）
5. 优先让还没发言的人先说
6. 人类学生通常至少间隔2位非人类发言者后可再次安排，但如果讨论需要人类回应则可提前
7. 如果有人被指定发言（如"请XX发言"），必须安排该角色；同一句里出现多个名字时，只按邀请动词后第一个受邀者执行，不要把后文引用到的人当成被点名者
8. 当AI角色发言时，应经常引用或回应人类学生此前的观点，形成以人类为中心的讨论网络
9. 讨论接近收尾时，优先让老师启动收尾流程：第一次请明确用"收尾前，我想先问问大家，还有没有什么想说的或者想要分享的……"征询一次；如果后来又出现新的补充，再让老师明确用"最后我再问一次，还有没有什么想说的或者想要分享的……"进行第二次征询
10. 只输出参与者名字，不要其他内容
"""

FIRST_CLOSING_PROMPT_MARKER = "收尾前，我想先问问大家"
SECOND_CLOSING_PROMPT_MARKER = "最后我再问一次"

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


def _build_participant_alias_map(participant_names: list[str]) -> dict[str, str]:
    """构建参与者别名映射，支持使用简称/姓氏点名。"""

    alias_owner: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    title_pattern = re.compile(r"(?:同学|老师|先生|女士|小朋友)$")

    for raw_name in participant_names:
        canonical = (raw_name or "").strip()
        if not canonical:
            continue

        aliases: set[str] = {canonical}
        stripped = title_pattern.sub("", canonical).strip()
        if stripped:
            aliases.add(stripped)

        # 处理中西文复合姓名，支持使用最后一段进行点名（如“阿德勒先生”）。
        for splitter in ("·", "・", ".", " "):
            if splitter not in stripped:
                continue
            tail = stripped.split(splitter)[-1].strip()
            if len(tail) >= 2:
                aliases.add(tail)

        for alias in aliases:
            normalized_alias = alias.strip()
            if len(normalized_alias) < 2:
                continue
            owner = alias_owner.get(normalized_alias)
            if owner is None:
                alias_owner[normalized_alias] = canonical
            elif owner != canonical:
                ambiguous_aliases.add(normalized_alias)

    for alias in ambiguous_aliases:
        alias_owner.pop(alias, None)

    return alias_owner


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
    normalized_text = re.sub(r"[*_`~]+", "", (text or "").strip())
    if not normalized_text:
        return None

    alias_map = _build_participant_alias_map(participant_names)
    if not alias_map:
        return None

    def first_target_after_invite_verb() -> Optional[str]:
        invite_marker = re.compile(
            r"(?:想问问|问问|想请|也请|不如请|要不请|正式邀请|邀请|想听听|听听|有请|请|轮到|下一位|接下来请|交给)"
        )
        target_tail = (
            r"(?=$|[，,、：:\s。！？!?；;—-]"
            r"|发言|先说|来说|来谈|谈谈|说说|讲讲|分享|回应|补充|的|该|您|你)"
        )
        alias_items = sorted(alias_map.items(), key=lambda item: len(item[0]), reverse=True)
        for marker in invite_marker.finditer(normalized_text):
            segment = normalized_text[marker.end() : marker.end() + 80]
            segment = re.split(r"[。！？!?\n]", segment, maxsplit=1)[0]
            matches: list[tuple[int, int, str]] = []
            for alias, canonical_name in alias_items:
                escaped_alias = re.escape(alias)
                title_suffix = r"(?:同学|老师|先生|女士|小朋友)?"
                match = re.search(rf"{escaped_alias}{title_suffix}{target_tail}", segment)
                if match:
                    matches.append((match.start(), -len(alias), canonical_name))
            if matches:
                matches.sort()
                return matches[0][2]
        return None

    invited_target = first_target_after_invite_verb()
    if invited_target:
        return invited_target

    # 按别名长度降序匹配，优先命中更具体的长名。
    sorted_aliases = sorted(alias_map.items(), key=lambda item: len(item[0]), reverse=True)
    for alias, canonical_name in sorted_aliases:
        escaped_alias = re.escape(alias)
        # 允许"请/想问问/邀请"等动词与名字之间出现最多 12 个非句末标点的修饰字符，
        # 例如"想问问一直没说话的豆苗同学"。注意 [^。！？\n] 排除句末标点防止跨句。
        invite_filler = r"[^。！？，,；;—\-、\n]{0,12}"
        title_suffix = r"(?:同学|老师|先生|女士|小朋友)?"
        patterns = [
            rf'请\s*{escaped_alias}{title_suffix}\s*(发言|先说|来谈|谈谈|先来|先讲|先分享)?',
            rf'请\s*(?:一下)?\s*{invite_filler}{escaped_alias}{title_suffix}\s*(发言|先说|来谈|谈谈|先来|先讲|先分享)',
            rf'接下来\s*请\s*{invite_filler}{escaped_alias}{title_suffix}',
            rf'下一位\s*(请)?\s*{invite_filler}{escaped_alias}{title_suffix}',
            rf'轮到\s*{escaped_alias}{title_suffix}',
            rf'(来|好|那么|那|接下来)[，,、：:\s]*{escaped_alias}{title_suffix}(?:[，,:：]\s*)?(?:该)?(?:您|你)?\s*(?:发言|先说|来说|来谈|谈谈|说说|讲讲|分享|有什么|怎么看|觉得|认为|听了)',
            rf'(?:^|[。！？!?；;，,]\s*)(?:哎|来|好|那么|那|接下来)?[，,、：:\s]*{escaped_alias}{title_suffix}[，,:：]\s*(您|你)[^。！？；;\n]{{0,36}}(?:怎么看|觉得|认为|有什么|有没有|会不会|对不对|对吧|是不是|说说|讲讲|谈谈|平时|看到|什么感觉|感觉|是什么)',
            rf'(想请|也请|不如请|要不请|正式邀请|邀请|想问问|问问|想听听|听听)\s*(?:一下)?\s*{invite_filler}{escaped_alias}{title_suffix}',
            # 名字 + 标点 + 至多 30 个非句末字符 + 第二人称问句（容许"豆苗同学，作为...，你听了..."）
            rf'{escaped_alias}{title_suffix}[，,:：]\s*[^。！？\n]{{0,30}}(您|你)(怎么看|觉得|认为|来说|来谈|先说|先来|听了|听到|看了|看到|想了想|有没有|会不会|能不能|要不要|想不想|愿不愿意|是否|能否|有什么|什么感觉|感觉|是什么|说说|讲讲)',
            rf'{escaped_alias}{title_suffix}[，,:：]?\s*(您|你)(怎么看|觉得|认为|来说|来谈|先说|先来|听了|看到|有没有|会不会|能不能|要不要|想不想|愿不愿意|是否|能否|什么感觉|感觉|是什么)',
            rf'{escaped_alias}\S{{0,3}}[，,]?\s*你(怎么看|觉得|认为|来说|来谈|先说)',
            rf'{escaped_alias}\S{{0,3}}[，,]?\s*你(先来(?:开个头|说说|讲讲|聊聊|谈谈)?|先开个头|先说说|先讲讲|先聊聊|先谈谈)',
            rf'{escaped_alias}\S{{0,3}}[，,]?\s*(你)?(有没有|会不会|能不能|要不要|想不想|愿不愿意|是否|能否)',
            rf'(?:最后)?(?:还有|还剩)\s*{escaped_alias}{title_suffix}\s*(?:还)?没(?:有)?发言[^。！？\n]{{0,24}}[。！？!?]\s*(?:您|你)(?:自己)?[^。！？\n]{{0,36}}(?:怎么看|觉得|认为|说说|讲讲|谈谈|分享|想说)',
            rf'(想请|也请|不如请|要不请)?\s*{escaped_alias}{title_suffix}\s*(也)?(说说|讲讲|谈谈|分享|回应|补充)',
            rf'{escaped_alias}{title_suffix}[，,]?\s*(也)?(说说|讲讲|谈谈|分享|回应|补充)\s*(吧|一下)?',
            rf'(我)?(也)?想请\s*{invite_filler}{escaped_alias}{title_suffix}\s*(再)?(说说|讲讲|谈谈|分享|回应|补充)\s*(一下)?',
            rf'(我们)?来?听听\s*{invite_filler}{escaped_alias}{title_suffix}',
            rf'想听听?\s*{invite_filler}{escaped_alias}{title_suffix}',
            rf'由\s*{escaped_alias}{title_suffix}\s*(先)?发言',
            rf'那(?:咱们|我们)?\s*(?:就|先)?\s*(?:有请|请)\s*{invite_filler}{escaped_alias}{title_suffix}',
        ]
        for pattern in patterns:
            if re.search(pattern, normalized_text):
                return canonical_name
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
        name
        for name in all_participant_names
        if name not in recent_speakers or name == moderator_name
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
    display_name_to_agent: Optional[dict[str, str]] = None,
    get_human_engagement_level: Optional[Callable[[], int]] = None,
    thinker_agent_names: Optional[list[str]] = None,
) -> SelectorGroupChat:
    """创建圆桌讨论团队。"""
    if max_turns is None:
        max_turns = settings.max_turns

    nominal_max_turns = max_turns
    closing_turn_buffer = 4
    engagement_max_turn_extension_by_level = {
        0: 0,
        1: 4,
        2: 6,
    }
    engagement_closing_extension_by_level = {
        0: 0,
        1: 2,
        2: 3,
    }
    engagement_target_extension_by_level = {
        0: 0,
        1: 1,
        2: 2,
    }
    max_engagement_level = max(engagement_max_turn_extension_by_level)
    max_turns = nominal_max_turns + closing_turn_buffer
    if humans:
        max_turns += engagement_max_turn_extension_by_level[max_engagement_level]

    all_participants = [moderator] + characters + humans
    all_names = [p.name for p in all_participants]

    # display_name → agent_name 映射，用于在发言内容中解析 display name 的点名
    _display_name_to_agent = display_name_to_agent or {}
    _display_names = list(_display_name_to_agent.keys())

    termination = MaxMessageTermination(max_turns) | TextMentionTermination("讨论结束")

    first_closing_trigger_turn = max(5, nominal_max_turns - 5)
    second_closing_trigger_turn = max(first_closing_trigger_turn + 1, nominal_max_turns - 2)

    human_name_set = {h.name for h in humans}
    thinker_name_set = set(thinker_agent_names or [])
    preferred_human_turn_target = 0
    human_turn_min_target = 0
    human_turn_soft_cap = 0
    first_human_invite_after_turns = 2
    forced_first_human_after_turns = 3
    human_reinvite_gap = 2
    moderator_soft_cap_ratio = 0.30
    if humans:
        human_turn_min_target = min(5, max(2, round(nominal_max_turns / 5)))
        human_turn_soft_cap = max(human_turn_min_target, min(8, round(nominal_max_turns / 3)))
        preferred_human_turn_target = max(
            human_turn_min_target,
            min(human_turn_soft_cap, round(nominal_max_turns * 0.27)),
        )

    def moderator_led_selector(thread: list) -> Optional[str]:
        """自定义发言者选择函数。

        优先级：
        1. 全局指定发言者（老师/用户点名）
        2. 用户刚发言 → 老师点评
        3. 主持人发言中的点名（使用 display names 解析）
        4. 人类冷却期控制
        5. 交由 LLM 选择
        """
        participant_msgs = [m for m in thread if getattr(m, "source", None) in all_names]
        if not participant_msgs:
            logger.info("[TurnScheduler] 首轮：选择老师开场")
            return moderator.name

        last_source = getattr(participant_msgs[-1], "source", None) if participant_msgs else None
        non_moderator_msgs = [
            m for m in participant_msgs if getattr(m, "source", None) != moderator.name
        ]
        has_human_spoken = any(
            getattr(m, "source", None) in human_name_set for m in participant_msgs
        )
        moderator_turn_count = sum(
            1 for m in participant_msgs if getattr(m, "source", None) == moderator.name
        )
        moderator_ratio = (
            moderator_turn_count / max(1, len(participant_msgs))
        )
        human_turn_count = sum(
            1 for m in participant_msgs if getattr(m, "source", None) in human_name_set
        )
        engagement_level = 0
        if get_human_engagement_level is not None:
            try:
                engagement_level = max(
                    0, min(max_engagement_level, int(get_human_engagement_level()))
                )
            except Exception:
                engagement_level = 0
        is_opening_round = last_source == moderator.name and not non_moderator_msgs

        # 思想家发言计数（嘉宾必须被点名至少一次，否则视为未完成讨论）
        unspoken_thinkers = [
            n for n in thinker_name_set
            if not any(getattr(m, "source", None) == n for m in participant_msgs)
        ]
        spoken_sources = {getattr(m, "source", None) for m in participant_msgs}
        unspoken_non_human = [
            n for n in all_names
            if n not in spoken_sources
            and n != moderator.name
            and n not in human_name_set
        ]

        def turns_since_moderator_marker(marker: str) -> int | None:
            turns = 0
            for msg in reversed(participant_msgs):
                if getattr(msg, "source", None) != moderator.name:
                    turns += 1
                    continue
                content = str(getattr(msg, "content", "") or getattr(msg, "messages", ""))
                if marker in content:
                    return turns
                turns += 1
            return None

        first_closing_seen = turns_since_moderator_marker(FIRST_CLOSING_PROMPT_MARKER)
        second_closing_seen = turns_since_moderator_marker(SECOND_CLOSING_PROMPT_MARKER)
        effective_first_closing_trigger_turn = first_closing_trigger_turn
        effective_second_closing_trigger_turn = second_closing_trigger_turn
        effective_preferred_human_turn_target = preferred_human_turn_target
        effective_human_turn_soft_cap = human_turn_soft_cap
        effective_first_closing_trigger_turn += engagement_closing_extension_by_level[
            engagement_level
        ]
        effective_second_closing_trigger_turn += engagement_closing_extension_by_level[
            engagement_level
        ]
        effective_preferred_human_turn_target += engagement_target_extension_by_level[
            engagement_level
        ]
        effective_human_turn_soft_cap += engagement_target_extension_by_level[engagement_level]

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

        def non_human_continuation_candidates() -> list[str]:
            preferred = [
                n
                for n in _exclude_recent_speakers(thread, all_names, moderator_name=moderator.name)
                if n not in human_name_set and n != moderator.name
            ]
            if preferred:
                return preferred

            fallback = [
                n
                for n in all_names
                if n not in human_name_set and n != moderator.name and n != last_source
            ]
            if fallback:
                return fallback

            return [n for n in all_names if n not in human_name_set and n != moderator.name]

        def preferred_human_name() -> Optional[str]:
            if not human_name_set:
                return None
            return sorted(human_name_set)[0]

        def balancing_candidate() -> Optional[str]:
            if unspoken_thinkers:
                return unspoken_thinkers[0]
            if unspoken_non_human:
                return unspoken_non_human[0]
            non_human = non_human_continuation_candidates()
            if non_human:
                return non_human[0]
            return None

        def peer_response_candidate() -> Optional[str]:
            non_human = non_human_continuation_candidates()
            if non_human:
                return non_human[0]
            return None

        def coverage_gap_candidate() -> Optional[str]:
            if unspoken_thinkers:
                return unspoken_thinkers[0]
            if unspoken_non_human:
                return unspoken_non_human[0]
            return None

        human_cooldown = 2
        since_human = turns_since_last_human()
        human_within_soft_cap = human_turn_count < effective_human_turn_soft_cap
        human_reinvite_due = (
            bool(human_name_set)
            and has_human_spoken
            and human_within_soft_cap
            and human_turn_count < effective_preferred_human_turn_target
            and since_human >= human_reinvite_gap
        )

        # ── 优先级2：用户刚发言后，优先允许同伴自然接话，老师不必每次都立即点评 ──
        if last_source in human_name_set:
            if first_closing_seen is not None and first_closing_seen <= 1:
                logger.info("[TurnScheduler] 收尾征询后的真人补充，回到老师完成串联")
                return moderator.name
            candidate = coverage_gap_candidate()
            if candidate and candidate != moderator.name:
                logger.info(
                    "[TurnScheduler] 用户 %s 刚发言，优先补齐未发言参与者: %s",
                    last_source,
                    candidate,
                )
                return candidate
            candidate = peer_response_candidate()
            if candidate and candidate != moderator.name:
                logger.info(
                    "[TurnScheduler] 用户 %s 刚发言，优先让同伴接着回应: %s",
                    last_source,
                    candidate,
                )
                return candidate
            logger.info("[TurnScheduler] 用户 %s 刚发言，兜底回到老师串联", last_source)
            return moderator.name

        if (
            human_name_set
            and not has_human_spoken
            and len(non_moderator_msgs) >= 2
            and last_source != moderator.name
        ):
            logger.info("[TurnScheduler] 真人学生尚未发言，优先把老师拉回邀请位")
            return moderator.name

        if human_reinvite_due and last_source != moderator.name:
            candidate = coverage_gap_candidate()
            if candidate and candidate != moderator.name:
                logger.info(
                    "[TurnScheduler] 真人预算偏低但仍有未发言参与者，先补齐覆盖: %s",
                    candidate,
                )
                return candidate
            logger.info(
                "[TurnScheduler] 真人学生发言次数仍低于目标，安排老师回到邀请位: %s/%s level=%s",
                human_turn_count,
                effective_preferred_human_turn_target,
                engagement_level,
            )
            return moderator.name

        latest_msg = participant_msgs[-1] if participant_msgs else None
        latest_source = getattr(latest_msg, "source", None) if latest_msg else None
        latest_content = (
            str(getattr(latest_msg, "content", "") or getattr(latest_msg, "messages", ""))
            if latest_msg
            else ""
        )
        explicit_next_speaker: Optional[str] = None
        if latest_content and latest_source == moderator.name:
            next_display = None
            if _display_names:
                next_display = parse_speaker_designation(latest_content, _display_names)
            explicit_next_speaker = next_display or parse_speaker_designation(
                latest_content,
                all_names,
            )
            if explicit_next_speaker in _display_name_to_agent:
                explicit_next_speaker = _display_name_to_agent[explicit_next_speaker]

        # 当老师发言占比过高时，优先让非老师继续，避免主持人垄断话轮。
        if (
            last_source == moderator.name
            and len(non_moderator_msgs) >= 3
            and moderator_ratio >= moderator_soft_cap_ratio
            and not explicit_next_speaker
        ):
            candidate = balancing_candidate()
            if candidate and candidate != moderator.name:
                logger.info(
                    "[TurnScheduler] 老师占比偏高(%.2f)，切换给 %s",
                    moderator_ratio,
                    candidate,
                )
                return candidate

        # ── 优先级2.3：思想家嘉宾必须被点名至少一次 ──
        # 在已经热身（非首轮且至少有 4 条非主持人发言）后，如果思想家还没说话，
        # 把老师拉回到邀请位，由老师显式邀请思想家发言。
        if (
            unspoken_thinkers
            and len(non_moderator_msgs) >= 4
            and last_source != moderator.name
            and last_source not in thinker_name_set
        ):
            logger.info(
                "[TurnScheduler] 思想家尚未发言，安排老师邀请: %s", unspoken_thinkers
            )
            return moderator.name

        # 收尾门槛：在以下条件全部满足之前，绝不允许调度器主动触发收尾
        #   1) 真人学生发言已达到最低预算 (human_turn_min_target)
        #   2) 思想家嘉宾至少发言过 1 次
        #   3) 每位虚拟同学/思想家至少发言过 1 次
        closing_preconditions_ok = (
            (not human_name_set or human_turn_count >= human_turn_min_target)
            and not unspoken_thinkers
            and not unspoken_non_human
        )

        # ── 优先级2.5：接近收尾时，优先把老师拉回到征询/总结位 ──
        if (
            closing_preconditions_ok
            and len(participant_msgs) >= effective_first_closing_trigger_turn
            and first_closing_seen is None
            and last_source != moderator.name
        ):
            logger.info("[TurnScheduler] 接近收尾，安排老师进行第一次收尾征询")
            return moderator.name

        # 第二次收尾必须距第一次收尾至少有 2 条非主持人实质发言，避免「秒收尾」。
        if (
            closing_preconditions_ok
            and len(participant_msgs) >= effective_second_closing_trigger_turn
            and first_closing_seen is not None
            and first_closing_seen >= 2
            and second_closing_seen is None
            and last_source != moderator.name
        ):
            logger.info("[TurnScheduler] 接近收尾，安排老师进行第二次收尾征询")
            return moderator.name

        # 若主持人在收尾门槛未达成时仍发出了收尾标记，需要拉回非主持人继续讨论
        if (
            not closing_preconditions_ok
            and first_closing_seen == 0  # 老师上一条就是第一次收尾
            and last_source == moderator.name
        ):
            # 优先邀请尚未发言的思想家或虚拟同学，其次再回到真人
            for cand in unspoken_thinkers + unspoken_non_human:
                logger.info("[TurnScheduler] 收尾门槛未达成，转向未发言者: %s", cand)
                return cand
            if human_name_set and human_turn_count < human_turn_min_target:
                non_human_candidates = non_human_continuation_candidates()
                if non_human_candidates:
                    selected = non_human_candidates[0]
                    logger.info(
                        "[TurnScheduler] 收尾门槛未达成但老师未明确点名真人，继续非真人讨论: %s (%s/%s)",
                        selected, human_turn_count, human_turn_min_target,
                    )
                    return selected

        # ── 优先级3：只解析“最新一条”老师/用户发言中的点名，避免旧消息误触发 ──
        if latest_content and (latest_source == moderator.name or latest_source in human_name_set):
            # 优先使用 display names 解析点名（moderator 发言中使用的是 display names）
            next_speaker = explicit_next_speaker
            if next_speaker is None:
                next_display = None
                if _display_names:
                    next_display = parse_speaker_designation(latest_content, _display_names)
                # 回退到 agent names（兼容性）
                next_speaker = next_display or parse_speaker_designation(
                    latest_content,
                    all_names,
                )
            if next_speaker and next_speaker != latest_source:
                # 将 display name 转换为 agent name
                if next_speaker in _display_name_to_agent:
                    next_speaker = _display_name_to_agent[next_speaker]
                # 若 moderator 明确点名，无视冷却期，直接执行
                if latest_source == moderator.name:
                    logger.info("[TurnScheduler] moderator 明确点名: %s", next_speaker)
                    return next_speaker
                # 非 moderator 点名时，人类冷却期仍生效
                if next_speaker in human_name_set and since_human < human_cooldown:
                    logger.info("[TurnScheduler] 点名 %s 但人类冷却中，跳过", next_speaker)
                else:
                    logger.info("[TurnScheduler] 解析点名: %s → %s", latest_source, next_speaker)
                    return next_speaker

            # 老师刚完成开场时，若没显式点到真人学生，就不要让系统直接把
            # 首轮交给真人，避免打乱老师先点名虚拟同学/思想家的顺序。
            if latest_source == moderator.name and is_opening_round:
                opening_candidates = [
                    n for n in all_names if n not in human_name_set and n != moderator.name
                ]
                if opening_candidates:
                    selected = opening_candidates[0]
                    logger.info("[TurnScheduler] moderator 开场后默认首轮交给: %s", selected)
                    return selected

            if latest_source == moderator.name and not is_opening_round and not has_human_spoken:
                warmup_candidates = non_human_continuation_candidates()
                if warmup_candidates:
                    selected = warmup_candidates[0]
                    logger.info(
                        "[TurnScheduler] 老师未明确点名真人，继续由非人类参与者铺垫: %s",
                        selected,
                    )
                    return selected

            if latest_source == moderator.name and human_reinvite_due:
                logger.info(
                    "[TurnScheduler] 真人发言预算偏低，但老师未明确点名真人，继续等待明确授权: %s/%s level=%s",
                    human_turn_count,
                    effective_preferred_human_turn_target,
                    engagement_level,
                )
                non_human_candidates = non_human_continuation_candidates()
                if non_human_candidates:
                    return non_human_candidates[0]

        # ── 优先级4：人类发言冷却期 ──
        if since_human < human_cooldown:
            recent_speaker = getattr(thread[-1], "source", None) if thread else None
            non_human_candidates = [
                n for n in all_names if n not in human_name_set and n != recent_speaker
            ]
            if non_human_candidates:
                peer_candidates = [
                    n for n in non_human_candidates if n != moderator.name
                ]
                if peer_candidates:
                    selected = peer_candidates[0]
                    logger.info("[TurnScheduler] 人类冷却中，优先继续非主持人: %s", selected)
                    return selected
                selected = non_human_candidates[0]
                logger.info("[TurnScheduler] 人类冷却中，选择: %s", selected)
                return selected

        if human_name_set:
            candidate = balancing_candidate()
            if candidate and candidate not in human_name_set:
                logger.info("[TurnScheduler] 无显式真人授权，保持真人锁定: %s", candidate)
                return candidate
            if moderator.name != last_source:
                logger.info("[TurnScheduler] 无显式真人授权且无其他候选，回到老师")
                return moderator.name

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
