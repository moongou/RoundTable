from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

MIN_QUOTE_MESSAGE_COUNT = 4
MIN_QUOTE_SPEAKER_COUNT = 3
MAX_TRANSCRIPT_MESSAGES = 24
SUMMARYISH_PREFIXES = (
    '这场讨论',
    '今天的讨论',
    '这告诉我们',
    '这说明',
    '真正的学习',
    '学习不是',
    '我们应该',
    '我们要',
    '所以我们',
)


def collect_quote_material(
    messages: Iterable[Mapping[str, object]],
) -> list[dict[str, str]]:
    material: list[dict[str, str]] = []
    for message in messages:
        msg_type = str(message.get('type', 'text') or 'text').strip()
        source = str(message.get('source', '') or '').strip()
        content = _clean_message_content(str(message.get('content', '') or ''))
        if msg_type == 'system' or source in {'', '系统'} or not content:
            continue
        material.append({'source': source, 'content': content, 'type': msg_type})
    return material


def has_enough_quote_material(messages: Iterable[Mapping[str, object]]) -> bool:
    material = collect_quote_material(messages)
    if len(material) < MIN_QUOTE_MESSAGE_COUNT:
        return False
    speakers = {message['source'] for message in material}
    return len(speakers) >= MIN_QUOTE_SPEAKER_COUNT


def build_golden_quotes_prompt(
    topic_title: str,
    messages: Iterable[Mapping[str, object]],
    *,
    max_quotes: int = 6,
) -> str:
    material = collect_quote_material(messages)[-MAX_TRANSCRIPT_MESSAGES:]
    transcript = '\n'.join(
        f"{index + 1}. {message['source']}：{message['content'][:180]}"
        for index, message in enumerate(material)
    )
    return f"""你是一名儿童圆桌讨论的金句编辑。

请根据下面这段讨论记录，提炼 {max_quotes} 条“今日金句”。

要求：
- 金句必须是你重新提炼和归纳后的表达，不能直接照抄原话。
- 每条 10 到 22 个字，宁可短一点，也不要把道理解释完整。
- 要像“被重新炼过的一句话”，少解释，多判断；少复述，多凝练。
- 优先写成前后两小截互相碰撞的一句短句，让人一眼记住。
- 优先使用对比、转折、意象、节奏感，让孩子一眼记住。
- 如果要用意象，必须紧贴讨论主题或现场已经出现过的比喻，不要凭空引入无关画面。
- 不要写成总结句、道理句、课堂评语或分析句。
- 避免出现“这说明/这告诉我们/我们应该/所以”这类总结腔。
- 不要写说话人名字，不要编号，不要加引号，不要以句号结尾。
- 如果讨论材料还不够形成金句，就返回空数组。

风格示例：
- 较差：分数衡量的是结果，但理解过程也很重要
- 较好：分数会亮灯，理解才会发光
- 较差：真正学会一道题，应该能把为什么讲清楚
- 较好：会做题是起点，会说明白才算真懂
- 较差：学习不只是拿分，更重要的是理解知识
- 较好：分数记结果，脑子记火花
- 较差：拼错火箭的宇航员找到了自己的星空
- 较好：分数记答卷，理解记脑海里的灯

请只返回 JSON 对象，格式如下：
{{"quotes": ["...", "..."]}}

讨论主题：{topic_title.strip() or '未命名话题'}
讨论记录：
{transcript}
"""


def parse_golden_quotes_response(raw: str, *, max_quotes: int = 6) -> list[str]:
    text = (raw or '').strip()
    if not text:
        return []

    candidates: list[str] = []
    code_blocks = re.findall(r'```(?:json)?\s*(.*?)```', text, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(block.strip() for block in code_blocks if block.strip())

    first_object = _slice_balanced_json(text, '{', '}')
    if first_object:
        candidates.append(first_object)
    first_array = _slice_balanced_json(text, '[', ']')
    if first_array:
        candidates.append(first_array)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        normalized = _normalize_quotes(_extract_quote_values(parsed), max_quotes=max_quotes)
        if normalized:
            return normalized

    bullet_lines = [
        re.sub(r'^[\-•*\d\.\)\s]+', '', line).strip()
        for line in text.splitlines()
    ]
    return _normalize_quotes(bullet_lines, max_quotes=max_quotes)


def _extract_quote_values(payload: object) -> list[str]:
    if isinstance(payload, dict):
        values = payload.get('quotes', [])
        if isinstance(values, list):
            return [str(item) for item in values]
        return []
    if isinstance(payload, list):
        return [str(item) for item in payload]
    return []


def _normalize_quotes(quotes: Iterable[str], *, max_quotes: int) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in quotes:
        text = (raw or '').strip()
        text = text.strip('"“”\'')
        text = re.sub(r'^[\-•*\d\.\)\s]+', '', text)
        text = re.sub(r'^[^：:]{1,12}[：:]', '', text)
        text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)
        text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[，。！？；：])', '', text)
        text = re.sub(r'(?<=[，。！？；：])\s+(?=[\u4e00-\u9fff])', '', text)
        text = re.sub(r'\s+', ' ', text).strip(' ，,。；;、')
        if len(text) < 10 or len(text) > 32:
            continue
        if text.startswith(SUMMARYISH_PREFIXES):
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
        if len(normalized) >= max_quotes:
            break
    return normalized


def _clean_message_content(content: str) -> str:
    text = re.sub(r'（[^）]{0,24}）', '', content or '')
    text = re.sub(r'\([^)]{0,24}\)', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def _slice_balanced_json(text: str, open_char: str, close_char: str) -> str | None:
    start = text.find(open_char)
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None